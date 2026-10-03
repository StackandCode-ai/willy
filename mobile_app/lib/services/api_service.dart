import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:web_socket_channel/io.dart';

import '../models/activity.dart';
import '../models/device.dart';
import '../models/presence.dart';
import '../models/push.dart';
import 'file_transfer_service.dart';
import 'settings_store.dart';
import 'telemetry_service.dart';

enum HubState { connecting, online, offline }

class HubStatus {
  final HubState state;
  final int? latencyMs;
  final String? detail;

  const HubStatus(this.state, {this.latencyMs, this.detail});

  bool get isOnline => state == HubState.online;
}

/// Connection to the Willy hub.
///
/// One persistent WebSocket carries everything realtime: live device telemetry,
/// the activity feed, reminders, commands (no HTTP handshake per command) and
/// actions the hub sends to this phone. REST calls share one keep-alive client.
class ApiService {
  static String get baseUrl => WillySettings.baseUrl;
  static set baseUrl(String v) => WillySettings.baseUrl = normalizeBaseUrl(v);
  static String get token => WillySettings.token;
  static set token(String v) => WillySettings.token = v.trim();
  static String get deviceId =>
      WillySettings.deviceId.isNotEmpty ? WillySettings.deviceId : 'mobile_android_phone';
  static String deviceName = 'Android Phone';

  static const _heartbeatEvery = Duration(seconds: 5);
  static const _countsEvery = Duration(seconds: 30);
  static const _maxActivity = 100;

  static http.Client _http = http.Client();

  /// Widget tests answer REST calls with a fake client.
  @visibleForTesting
  static set httpClient(http.Client client) => _http = client;

  static Map<String, String> get _headers => {
        'Authorization': 'Bearer $token',
        'Content-Type': 'application/json',
        'X-Willy-Client': 'mobile',
      };

  // ------------------------------------------------------------- live state

  static final Map<String, WillyDevice> _devices = {};
  static final List<ActivityEntry> _activity = [];
  static Map<String, dynamic> serverInfo = {};
  static Map<String, dynamic> stats = {};
  static HubStatus status = const HubStatus(HubState.connecting);
  static Map<String, dynamic> phoneStatus = {};

  static final _devicesCtl = StreamController<List<WillyDevice>>.broadcast();
  static final _activityCtl = StreamController<List<ActivityEntry>>.broadcast();
  static final _statusCtl = StreamController<HubStatus>.broadcast();
  static final _eventsCtl = StreamController<Map<String, dynamic>>.broadcast();

  static Stream<List<WillyDevice>> get devicesStream => _devicesCtl.stream;
  static Stream<List<ActivityEntry>> get activityStream => _activityCtl.stream;
  static Stream<HubStatus> get statusStream => _statusCtl.stream;

  /// reminder_due, morning_call_due, reminders_changed, phone_ringing, quickdrop_note, presence_alert,
  /// late_reply ({request_id, query, result}: the answer to a command that timed out), ...
  static Stream<Map<String, dynamic>> get eventsStream => _eventsCtl.stream;

  static List<WillyDevice> get devices {
    final list = _devices.values.toList();
    list.sort((a, b) {
      if (a.online != b.online) return a.online ? -1 : 1;
      if (a.typeRank != b.typeRank) return a.typeRank.compareTo(b.typeRank);
      return a.name.toLowerCase().compareTo(b.name.toLowerCase());
    });
    return list;
  }

  static List<ActivityEntry> get activity => List.unmodifiable(_activity);
  static WillyDevice? device(String id) => _devices[id];

  /// Only real PCs: never the Linux server, so PC actions can't land on it.
  static List<WillyDevice> get pcs => devices.where((d) => d.isPc).toList();

  static List<WillyDevice> get servers => devices.where((d) => d.isServer).toList();

  /// The online PC commands go to by default (most recently seen first).
  static WillyDevice? get primaryPc {
    final online = pcs.where((d) => d.online).toList()
      ..sort((a, b) => b.lastSeen.compareTo(a.lastSeen));
    if (online.isNotEmpty) return online.first;
    return pcs.isNotEmpty ? pcs.first : null;
  }

  // ----------------------------------------------------------------- socket

  static IOWebSocketChannel? _channel;
  static StreamSubscription? _sub;
  static Timer? _heartbeatTimer;
  static Timer? _reconnectTimer;
  static Timer? _countsTimer;
  static int _serial = 0;
  static int _attempt = 0;
  static bool _stopped = false;
  static final Map<String, Completer<Map<String, dynamic>>> _pending = {};
  static final Random _rng = Random();

  // Commands the hub keeps working on after the app stopped waiting (timeout or a dropped
  // socket). The hub still sends their command_result later, and re-sends undelivered ones
  // to the new socket after a reconnect; those arrive as a `late_reply` event instead.
  static const _lateReplyWindow = Duration(minutes: 10);
  static const _maxLateReplies = 100;
  static final Map<String, String> _lateQueries = {}; // request_id -> query, while pending
  static final Map<String, _LateRequest> _awaitingLate = {}; // insertion order = age

  static const stillWorkingReply = "Still working on it — I'll post the answer here.";
  static const reconnectingReply = "Lost the hub connection — still working on it, I'll post the answer here.";

  // Presence alerts reach the phone twice: as a `presence_alert` event (in-app banner) and
  // as a `notify` action (system notification). Remember recent ones so that notify
  // doesn't also pop the in-app note dialog for the same news.
  static final RecentAlerts _recentPresence = RecentAlerts();

  static String normalizeBaseUrl(String v) {
    var s = v.trim();
    if (s.isEmpty) return WillySettings.defaultBaseUrl;
    if (!s.startsWith('http://') && !s.startsWith('https://')) s = 'http://$s';
    while (s.endsWith('/')) {
      s = s.substring(0, s.length - 1);
    }
    return s;
  }

  static Uri _api(String path, [Map<String, String>? query]) {
    final uri = Uri.parse('$baseUrl$path');
    return query == null ? uri : uri.replace(queryParameters: query);
  }

  /// Loads this phone's identity, then connects and starts background syncs.
  static Future<void> init() async {
    final st = await MobileTelemetryService.getDeviceStatus();
    if (st.isNotEmpty) phoneStatus = st;
    await WillySettings.ensureDeviceId(st['android_id'] as String?);
    deviceName = WillySettings.deviceName.isNotEmpty
        ? WillySettings.deviceName
        : (st['device_name'] as String?) ?? 'Android Phone';
    connectDeviceSocket();
    MobileTelemetryService.syncTelemetryToServer();
    _countsTimer?.cancel();
    _countsTimer = Timer.periodic(_countsEvery, (_) => MobileTelemetryService.syncTelemetryToServer());
  }

  /// (Re)connects the realtime socket; safe to call repeatedly.
  static Future<void> connectDeviceSocket() async {
    _stopped = false;
    final serial = ++_serial;
    _teardownSocket();
    _setStatus(const HubStatus(HubState.connecting));

    final wsBase = baseUrl.replaceFirst(RegExp(r'^http'), 'ws');
    final version = phoneStatus['android_version'];
    final uri = Uri.parse('$wsBase/ws/devices').replace(queryParameters: {
      'token': token,
      'device_id': deviceId,
      'device_type': 'mobile',
      'name': deviceName,
      'platform': version != null ? 'Android $version' : 'Android',
      'hostname': (phoneStatus['model'] ?? 'Android').toString(),
    });

    try {
      final channel = IOWebSocketChannel.connect(
        uri,
        pingInterval: const Duration(seconds: 15),
        connectTimeout: const Duration(seconds: 10),
      );
      await channel.ready;
      if (serial != _serial) {
        channel.sink.close();
        return;
      }
      _channel = channel;
      _attempt = 0;
      _setStatus(HubStatus(HubState.online, latencyMs: status.latencyMs));
      _sub = channel.stream.listen(
        _onMessage,
        onDone: () => _onSocketClosed(serial),
        onError: (_) => _onSocketClosed(serial),
        cancelOnError: true,
      );
      _sendHeartbeat();
      _heartbeatTimer = Timer.periodic(_heartbeatEvery, (_) => _sendHeartbeat());
      _sendPushToken(); // on every (re)connect: the hub may have restarted or dropped it
    } catch (e) {
      if (serial == _serial) _onSocketClosed(serial, error: _friendlyError(e));
    }
  }

  static String _friendlyError(Object e) {
    final s = e.toString();
    // The hub refuses the WebSocket upgrade (HTTP 403) when the token is wrong.
    if (s.contains('401') || s.contains('403') || s.contains('4001') || s.contains('not upgraded')) {
      return 'Token rejected (check settings)';
    }
    if (s.contains('Connection refused')) return 'Hub not reachable';
    if (s.contains('timed out') || s.contains('TimeoutException')) return 'Connection timed out';
    return 'Connection failed';
  }

  static void _onSocketClosed(int serial, {String? error}) {
    if (serial != _serial) return;
    _teardownSocket();
    _failPending('Disconnected from hub.');
    if (_stopped) return;
    _attempt++;
    final backoffMs = min(15000, 1000 * (1 << min(_attempt - 1, 4)));
    final delay = Duration(milliseconds: backoffMs + _rng.nextInt(500));
    _setStatus(HubStatus(
      HubState.offline,
      detail: '${error ?? 'Disconnected'} · retrying in ${(delay.inMilliseconds / 1000).round()}s',
    ));
    _reconnectTimer = Timer(delay, () {
      if (serial == _serial && !_stopped) connectDeviceSocket();
    });
  }

  static void _teardownSocket() {
    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _sub?.cancel();
    _sub = null;
    try {
      _channel?.sink.close();
    } catch (_) {}
    _channel = null;
  }

  static void _setStatus(HubStatus s) {
    status = s;
    if (!_statusCtl.isClosed) _statusCtl.add(s);
  }

  static bool _send(Map<String, dynamic> data) {
    final ch = _channel;
    if (ch == null) return false;
    try {
      ch.sink.add(jsonEncode(data));
      return true;
    } catch (_) {
      return false;
    }
  }

  static Future<void> _sendHeartbeat() async {
    final st = await MobileTelemetryService.getDeviceStatus();
    if (st.isNotEmpty) phoneStatus = st;
    _send({
      'type': 'heartbeat',
      'ts': DateTime.now().millisecondsSinceEpoch / 1000.0,
      'telemetry': {
        'battery_pct': phoneStatus['battery_pct'],
        'is_charging': phoneStatus['is_charging'],
        'network_type': phoneStatus['network_type'],
        'storage_free_gb': phoneStatus['storage_free_gb'],
        'storage_total_gb': phoneStatus['storage_total_gb'],
        'ram_pct': phoneStatus['ram_pct'],
        'screen_on': phoneStatus['screen_on'],
        'torch_on': phoneStatus['torch_on'],
        'model': phoneStatus['model'],
        'android_version': phoneStatus['android_version'],
        'tz_offset_min': DateTime.now().timeZoneOffset.inMinutes,
        'last_ping_ms': status.latencyMs,
        ...MobileTelemetryService.lastCounts,
      },
    });
  }

  // ---------------------------------------------------------- incoming data

  static void _onMessage(dynamic raw) {
    Map<String, dynamic> data;
    try {
      final decoded = jsonDecode(raw as String);
      if (decoded is! Map) return;
      data = Map<String, dynamic>.from(decoded);
    } catch (_) {
      return;
    }

    final reqId = data['req_id'];
    final action = data['action'];
    if (reqId is String && action is String) {
      final payload = data['payload'];
      _handleIncomingAction(reqId, action, payload is Map ? Map<String, dynamic>.from(payload) : {});
      return;
    }

    switch (data['type']) {
      case 'token_update':
        // The hub retired the token this phone used: keep the new one and reconnect with it.
        final next = (data['token'] ?? '').toString().trim();
        if (next.length >= 20 && next != WillySettings.token) {
          WillySettings.token = next;
          WillySettings.save().then((_) => reconnect());
        }
        return;
      case 'heartbeat_ack':
        final sent = asDouble(data['client_ts']);
        if (sent != null) {
          final ms = (DateTime.now().millisecondsSinceEpoch - sent * 1000).round();
          _setStatus(HubStatus(HubState.online, latencyMs: max(0, ms)));
        }
        break;
      case 'command_result':
        // Hubs older than v3 reply without request_id: match the oldest pending request.
        final rid = data['request_id'];
        final completer = rid != null
            ? _pending.remove(rid)
            : (_pending.isNotEmpty ? _pending.remove(_pending.keys.first) : null);
        final raw = data['result'];
        final result = raw is Map ? Map<String, dynamic>.from(raw) : <String, dynamic>{'success': false};
        if (completer != null && !completer.isCompleted) {
          completer.complete(result);
        } else if (rid != null) {
          _onLateResult(rid.toString(), result);
        }
        break;
      case 'snapshot':
        _replaceDevices(data['devices']);
        _activity
          ..clear()
          ..addAll((data['activity'] as List? ?? [])
              .whereType<Map>()
              .map((m) => ActivityEntry.fromJson(Map<String, dynamic>.from(m))));
        if (data['server'] is Map) serverInfo = Map<String, dynamic>.from(data['server']);
        if (data['stats'] is Map) stats = Map<String, dynamic>.from(data['stats']);
        _emitActivity();
        _eventsCtl.add(data);
        break;
      case 'devices_list':
        _replaceDevices(data['devices']);
        break;
      case 'device_discovered':
      case 'device_update':
      case 'device_offline':
        final dev = data['device'];
        if (dev is Map) {
          final parsed = WillyDevice.fromJson(Map<String, dynamic>.from(dev));
          if (parsed.id.isNotEmpty) _devices[parsed.id] = parsed;
          _emitDevices();
        }
        if (data['type'] != 'device_update') _eventsCtl.add(data);
        break;
      case 'activity':
        final entry = data['entry'];
        if (entry is Map) {
          final parsed = ActivityEntry.fromJson(Map<String, dynamic>.from(entry));
          _activity.removeWhere((e) => e.id == parsed.id);
          _activity.insert(0, parsed);
          if (_activity.length > _maxActivity) _activity.removeRange(_maxActivity, _activity.length);
          _emitActivity();
        }
        break;
      case 'presence_alert':
        _recentPresence.add(asString(data['title']), asString(data['message']));
        _eventsCtl.add(data);
        break;
      default:
        _eventsCtl.add(data);
    }
  }

  /// Shows a hub `notify` in the app too, unless it repeats a presence alert that already
  /// got a banner (that alert can land a moment after the notify, hence the short wait).
  static void _noteInApp(String title, String text) {
    if (_recentPresence.matches(title, text)) return;
    Timer(const Duration(milliseconds: 1200), () {
      if (_recentPresence.matches(title, text) || _eventsCtl.isClosed) return;
      _eventsCtl.add({'type': 'quickdrop_note', 'title': title, 'text': text});
    });
  }

  static void _replaceDevices(dynamic list) {
    _devices.clear();
    for (final d in (list as List? ?? [])) {
      if (d is Map) {
        final dev = WillyDevice.fromJson(Map<String, dynamic>.from(d));
        if (dev.id.isNotEmpty) _devices[dev.id] = dev;
      }
    }
    _emitDevices();
  }

  static void _emitDevices() {
    if (!_devicesCtl.isClosed) _devicesCtl.add(devices);
  }

  static void _emitActivity() {
    if (!_activityCtl.isClosed) _activityCtl.add(activity);
  }

  /// Actions the hub (PC, dashboard or LLM) runs on this phone.
  static Future<void> _handleIncomingAction(String reqId, String action, Map<String, dynamic> payload) async {
    Map<String, dynamic> result;
    try {
      switch (action) {
        case 'ring_device':
        case 'ring_phone':
          final secs = asInt(payload['duration_sec']) ?? 30;
          await MobileTelemetryService.ringPhone(durationSec: secs);
          _eventsCtl.add({'type': 'phone_ringing', 'message': payload['message'] ?? 'Find My Phone', 'duration_sec': secs});
          result = {'success': true, 'message': 'Phone is ringing.'};
          break;
        case 'stop_ring':
          await MobileTelemetryService.stopRingPhone();
          _eventsCtl.add({'type': 'phone_ring_stopped'});
          result = {'success': true, 'message': 'Stopped ringing.'};
          break;
        case 'set_clipboard':
          final text = payload['text']?.toString() ?? '';
          await Clipboard.setData(ClipboardData(text: text));
          _eventsCtl.add({'type': 'clipboard_received', 'text': text});
          result = {'success': true, 'message': 'Phone clipboard updated.'};
          break;
        case 'get_clipboard':
          final clip = await Clipboard.getData(Clipboard.kTextPlain);
          result = {'success': true, 'text': clip?.text ?? ''};
          break;
        case 'quickdrop':
          final url = asString(payload['url']);
          final text = asString(payload['text']);
          final title = asString(payload['title']) ?? 'QuickDrop';
          if (url != null) {
            final ok = await MobileTelemetryService.openUrl(url);
            result = ok
                ? {'success': true, 'message': 'Link opened on phone.'}
                : {'success': false, 'error': 'Could not open the link on the phone.'};
          } else if (text != null) {
            await Clipboard.setData(ClipboardData(text: text));
            await MobileTelemetryService.showNotification(title, text);
            _eventsCtl.add({'type': 'quickdrop_note', 'title': title, 'text': text});
            result = {'success': true, 'message': 'Note shown on phone and copied to its clipboard.'};
          } else {
            result = {'success': false, 'error': 'Nothing to drop.'};
          }
          break;
        case 'vibrate':
          await MobileTelemetryService.vibrate(ms: asInt(payload['ms']) ?? 600);
          result = {'success': true, 'message': 'Vibrated.'};
          break;
        case 'flashlight':
          final on = payload['on'] != false;
          final ok = await MobileTelemetryService.setTorch(on);
          result = ok
              ? {'success': true, 'on': on, 'message': 'Flashlight ${on ? 'on' : 'off'}.'}
              : {'success': false, 'error': 'This phone has no controllable flashlight.'};
          break;
        case 'speak':
          await MobileTelemetryService.speak(payload['text']?.toString() ?? '');
          result = {'success': true, 'message': 'Speaking on phone.'};
          break;
        case 'notify':
          final title = asString(payload['title']) ?? 'Willy';
          final message = asString(payload['message']) ?? asString(payload['text']) ?? '';
          await MobileTelemetryService.showNotification(title, message);
          _noteInApp(title, message);
          result = {'success': true, 'message': 'Notification shown on phone.'};
          break;
        case 'telemetry':
        case 'get_pc_status':
        case 'system_info':
          result = {'success': true, 'telemetry': phoneStatus};
          break;
        // Phone skills (contract v3.1): all run natively through one bridge, which returns
        // {success, message | error, needs_tap?, ...extra}.
        case 'phone_call':
        case 'phone_sms':
        case 'phone_whatsapp':
        case 'phone_open_app':
        case 'phone_alarm':
        case 'phone_timer':
        case 'phone_volume':
        case 'phone_media':
        case 'phone_navigate':
        case 'phone_notifications':
        case 'phone_contacts':
        case 'phone_open_url':
        case 'phone_install_app':
        case 'phone_uninstall_app':
        case 'phone_camera':
          result = await MobileTelemetryService.phoneAction(action, payload);
          break;
        case 'receive_file':
          result = await FileTransferService.receiveFromHub(payload);
          break;
        default:
          result = {'success': false, 'error': 'Action "$action" is not supported on the phone.'};
      }
    } catch (e) {
      result = {'success': false, 'error': e.toString()};
    }
    _send({'req_id': reqId, 'result': result});
  }

  // ------------------------------------------------------ outgoing requests

  /// Settles every waiting request. Commands the hub may still finish ([keepLate]) are
  /// remembered, so their answer shows up later as a `late_reply` event.
  static void _failPending(String reason, {bool keepLate = true}) {
    _pending.forEach((id, c) {
      if (c.isCompleted) return;
      final query = _lateQueries[id];
      if (keepLate && query != null) {
        _rememberLate(id, query);
        c.complete(_stillWorking(id, reconnectingReply));
      } else {
        c.complete({'success': false, 'error': 'DISCONNECTED', 'reply': reason});
      }
    });
    _pending.clear();
    _lateQueries.clear();
  }

  static Map<String, dynamic> _stillWorking(String id, String reply) =>
      {'success': true, 'pending': true, 'request_id': id, 'reply': reply};

  static void _rememberLate(String id, String query) {
    final now = DateTime.now();
    _awaitingLate.removeWhere((_, r) => now.difference(r.at) > _lateReplyWindow);
    _awaitingLate[id] = _LateRequest(query, now);
    while (_awaitingLate.length > _maxLateReplies) {
      _awaitingLate.remove(_awaitingLate.keys.first);
    }
  }

  /// A command_result nobody waits for any more: pass it on once if we promised to.
  static void _onLateResult(String id, Map<String, dynamic> result) {
    final req = _awaitingLate.remove(id); // removing it also drops re-sent duplicates
    if (req == null || DateTime.now().difference(req.at) > _lateReplyWindow) return;
    if (!_eventsCtl.isClosed) {
      _eventsCtl.add({'type': 'late_reply', 'request_id': id, 'query': req.query, 'result': result});
    }
  }

  /// Sends [message] and waits for its command_result. With [lateQuery] set, a timeout or a
  /// dropped socket answers "still working" (`pending: true`) and the real answer arrives
  /// later as a `late_reply` event carrying the same `request_id`.
  static Future<Map<String, dynamic>> _request(
    Map<String, dynamic> message, {
    Duration timeout = const Duration(seconds: 30),
    String? lateQuery,
  }) async {
    final id = '${DateTime.now().microsecondsSinceEpoch}_${_rng.nextInt(1 << 20)}';
    final completer = Completer<Map<String, dynamic>>();
    _pending[id] = completer;
    if (lateQuery != null) _lateQueries[id] = lateQuery;
    message['request_id'] = id;
    if (!_send(message)) {
      _pending.remove(id);
      _lateQueries.remove(id);
      return {'success': false, 'error': 'SEND_FAILED'};
    }
    try {
      return await completer.future.timeout(timeout);
    } on TimeoutException {
      if (lateQuery != null) {
        _rememberLate(id, lateQuery);
        return _stillWorking(id, stillWorkingReply);
      }
      return {'success': false, 'error': 'TIMEOUT', 'reply': 'The hub took too long to answer.'};
    } finally {
      _pending.remove(id);
      _lateQueries.remove(id);
    }
  }

  static Future<Map<String, dynamic>> _getJson(String path,
      {Map<String, String>? query, Duration timeout = const Duration(seconds: 10)}) async {
    try {
      final res = await _http.get(_api(path, query), headers: _headers).timeout(timeout);
      if (res.statusCode == 200) {
        final body = jsonDecode(res.body);
        return body is Map ? Map<String, dynamic>.from(body) : {'success': true, 'data': body};
      }
      return {'success': false, 'error': _httpError(res)};
    } catch (e) {
      return {'success': false, 'error': e.toString()};
    }
  }

  static Future<Map<String, dynamic>> _postJson(String path, Map<String, dynamic>? body,
      {Duration timeout = const Duration(seconds: 15)}) async {
    try {
      final res = await _http
          .post(_api(path), headers: _headers, body: body == null ? null : jsonEncode(body))
          .timeout(timeout);
      if (res.statusCode == 200) {
        final decoded = jsonDecode(res.body);
        return decoded is Map ? Map<String, dynamic>.from(decoded) : {'success': true};
      }
      return {'success': false, 'error': _httpError(res)};
    } catch (e) {
      return {'success': false, 'error': e.toString()};
    }
  }

  static Future<Map<String, dynamic>> _delete(String path) async {
    try {
      final res = await _http.delete(_api(path), headers: _headers).timeout(const Duration(seconds: 10));
      return res.statusCode == 200 ? {'success': true} : {'success': false, 'error': _httpError(res)};
    } catch (e) {
      return {'success': false, 'error': e.toString()};
    }
  }

  static String _httpError(http.Response res) {
    if (res.statusCode == 401) return 'Unauthorized: check the Remote Token in settings.';
    try {
      final body = jsonDecode(res.body);
      if (body is Map && body['detail'] != null) return body['detail'].toString();
    } catch (_) {}
    return 'Server returned ${res.statusCode}';
  }

  // ------------------------------------------------------------ public API

  /// Fetches the latest list of registered devices from the server (pull-to-refresh).
  static Future<List<WillyDevice>> getDevices() async {
    final data = await _getJson('/api/v1/devices', timeout: const Duration(seconds: 6));
    if (data['devices'] is List) {
      _replaceDevices(data['devices']);
    } else {
      debugPrint('[-] Error fetching devices: ${data['error']}');
    }
    return devices;
  }

  /// Natural-language command, over the open socket when connected (falls back to REST).
  static Future<Map<String, dynamic>> sendCommand({
    required String query,
    String? targetDeviceId,
    bool speakOnPc = false,
    bool returnAudio = false,
    String sessionId = 'mobile',
  }) async {
    if (status.isOnline) {
      final res = await _request({
        'type': 'send_to_device',
        'target_device_id': targetDeviceId,
        'payload': {
          'query': query,
          'return_audio': returnAudio,
          'speak_on_pc': speakOnPc,
          'session_id': sessionId,
        },
      }, timeout: const Duration(seconds: 60), lateQuery: query);
      if (res['error'] != 'SEND_FAILED') return res;
    }
    return _postJson('/api/v1/command', {
      'query': query,
      'target_device_id': targetDeviceId,
      'speak_on_pc': speakOnPc,
      'return_audio': returnAudio,
      'session_id': sessionId,
      'source': 'mobile',
    }, timeout: const Duration(seconds: 60));
  }

  /// Structured action on a device without the LLM (instant), e.g. ("pc_x", "power_action", {"action": "lock"}).
  static Future<Map<String, dynamic>> deviceAction(
    String deviceId,
    String action, [
    Map<String, dynamic>? payload,
    Duration timeout = const Duration(seconds: 25),
  ]) async {
    if (status.isOnline) {
      final res = await _request({
        'type': 'send_to_device',
        'target_device_id': deviceId,
        'action': action,
        'payload': payload ?? {},
      }, timeout: timeout);
      if (res['error'] != 'SEND_FAILED') return res;
    }
    return _postJson('/api/v1/devices/$deviceId/action', {
      'action': action,
      'payload': payload ?? {},
      'source': 'mobile',
    }, timeout: timeout);
  }

  /// Sends an audio recording blob to the server voice call endpoint.
  static Future<Map<String, dynamic>> sendVoiceUtterance(List<int> audioBytes, {String sessionId = 'voice_mobile'}) async {
    try {
      final request = http.MultipartRequest('POST', _api('/api/v1/call/interact', {'session_id': sessionId}))
        ..headers['Authorization'] = 'Bearer $token'
        ..headers['X-Willy-Client'] = 'voice'
        ..files.add(http.MultipartFile.fromBytes('file', audioBytes, filename: 'voice_recording.m4a'));
      final streamed = await _http.send(request).timeout(const Duration(seconds: 45));
      final res = await http.Response.fromStream(streamed);
      if (res.statusCode == 200) return Map<String, dynamic>.from(jsonDecode(res.body));
      return {'success': false, 'error': _httpError(res)};
    } catch (e) {
      debugPrint('[-] Voice call error: $e');
      return {'success': false, 'error': 'Failed to process voice.'};
    }
  }

  /// Validates URL + token and measures latency (for the settings screen).
  static Future<Map<String, dynamic>> checkConnection({String? url, String? tokenOverride}) async {
    final base = normalizeBaseUrl(url ?? baseUrl);
    final sw = Stopwatch()..start();
    try {
      final res = await _http.get(Uri.parse('$base/api/v1/auth/check'), headers: {
        'Authorization': 'Bearer ${tokenOverride ?? token}',
      }).timeout(const Duration(seconds: 8));
      sw.stop();
      if (res.statusCode == 200) {
        final body = jsonDecode(res.body);
        return {'ok': true, 'latency_ms': sw.elapsedMilliseconds, 'server': body is Map ? body['server'] : null};
      }
      if (res.statusCode == 404) {
        return {'ok': false, 'error': 'Hub found but it is an older version (update the server).'};
      }
      return {'ok': false, 'error': res.statusCode == 401 ? 'Token rejected by the hub.' : 'Hub returned ${res.statusCode}.'};
    } catch (e) {
      return {'ok': false, 'error': 'Could not reach $base'};
    }
  }

  /// Trades the one-time code from the dashboard's "Add device" for this phone's own key.
  /// Returns {'ok': true, 'email': ...} (the key is saved) or {'ok': false, 'error': ...}.
  static Future<Map<String, dynamic>> redeemPairCode(String code, {String? url, String? name}) async {
    final base = normalizeBaseUrl(url ?? baseUrl);
    try {
      final res = await _http
          .post(Uri.parse('$base/api/v1/pair/redeem'),
              headers: {'Content-Type': 'application/json', 'X-Willy-Client': 'mobile'},
              body: jsonEncode({
                'code': code.trim(),
                'device_id': deviceId,
                'device_type': 'mobile',
                'name': (name != null && name.trim().isNotEmpty) ? name.trim() : deviceName,
              }))
          .timeout(const Duration(seconds: 10));
      final body = jsonDecode(res.body);
      if (res.statusCode == 200 && body is Map && body['device_key'] is String) {
        WillySettings.baseUrl = base;
        WillySettings.token = (body['device_key'] as String).trim();
        await WillySettings.save();
        return {'ok': true, 'email': body['email']};
      }
      final err = body is Map ? (body['error'] ?? body['detail']) : null;
      return {'ok': false, 'error': err?.toString() ?? 'The hub refused that code (HTTP ${res.statusCode}).'};
    } catch (e) {
      return {'ok': false, 'error': 'Could not reach $base'};
    }
  }

  /// Re-creates the HTTP client and socket (after settings change).
  static void reconnect() {
    _http.close();
    _http = http.Client();
    connectDeviceSocket();
  }

  /// Connects now unless the socket is already up or connecting (skips the retry backoff).
  static void ensureConnected() {
    if (_stopped || status.state != HubState.offline) return;
    connectDeviceSocket();
  }

  // --- Push notifications (FCM) ---

  static String? _pushToken;

  /// True once the current push token reached the hub (socket or REST).
  static final ValueNotifier<bool> pushRegistered = ValueNotifier(false);

  /// Hands the hub this phone's push token: over the socket when connected, else by REST.
  /// The socket path repeats on every reconnect. Never logs the token.
  static Future<bool> registerPushToken(String token) async {
    if (token != _pushToken) pushRegistered.value = false;
    _pushToken = token;
    if (_sendPushToken()) return true;
    final res = await _postJson('/api/v1/push/register', pushRegisterBody(deviceId, token));
    final ok = res['success'] != false;
    if (token == _pushToken && ok) pushRegistered.value = true;
    if (!ok) debugPrint('[Push] Token registration failed: ${res['error']}');
    return ok;
  }

  static bool _sendPushToken() {
    final token = _pushToken;
    if (token == null || token.isEmpty) return false;
    final sent = _send(pushTokenMessage(token));
    if (sent) pushRegistered.value = true;
    return sent;
  }

  // --- Activity & stats ---

  static Future<void> refreshActivity({int limit = 60}) async {
    final data = await _getJson('/api/v1/activity', query: {'limit': '$limit'});
    if (data['activity'] is List) {
      _activity
        ..clear()
        ..addAll((data['activity'] as List).whereType<Map>().map((m) => ActivityEntry.fromJson(Map<String, dynamic>.from(m))));
      _emitActivity();
    }
    if (data['stats'] is Map) stats = Map<String, dynamic>.from(data['stats']);
  }

  static Future<Map<String, dynamic>> refreshStats() async {
    final data = await _getJson('/api/v1/stats');
    if (data['stats'] is Map) stats = Map<String, dynamic>.from(data['stats']);
    if (data['server'] is Map) serverInfo = Map<String, dynamic>.from(data['server']);
    return data;
  }

  static Future<Map<String, dynamic>> resetSession(String sessionId) =>
      _postJson('/api/v1/session/reset', {'session_id': sessionId});

  // --- Morning Call & Telemetry APIs ---

  /// Fetches daily morning briefing script and neural audio.
  static Future<Map<String, dynamic>> getMorningBriefing({bool includeAudio = true}) =>
      _getJson('/api/v1/morning/briefing', query: {'include_audio': '$includeAudio'}, timeout: const Duration(seconds: 25));

  /// Triggers a re-sync of tasks from the ChatGPT thread.
  static Future<Map<String, dynamic>> syncMorningTasks() =>
      _postJson('/api/v1/morning/sync', null, timeout: const Duration(seconds: 25));

  /// Gets morning call settings and ChatGPT thread configuration.
  static Future<Map<String, dynamic>> getMorningConfig() => _getJson('/api/v1/morning/config');

  /// Updates morning call settings and ChatGPT thread configuration.
  static Future<Map<String, dynamic>> updateMorningConfig(Map<String, dynamic> config) =>
      _postJson('/api/v1/morning/config', config);

  /// Pushes mobile notifications, missed calls, and WhatsApp counts to server.
  static Future<bool> updateMobileTelemetry(Map<String, dynamic> telemetry) async {
    final res = await _postJson('/api/v1/mobile/telemetry', telemetry);
    return res['success'] == true;
  }

  // --- Reminders & alarms ---

  static Future<List<dynamic>> getReminders() async {
    final data = await _getJson('/api/v1/reminders');
    return (data['reminders'] as List<dynamic>?) ?? [];
  }

  static Future<Map<String, dynamic>> createReminder({required String text, required String time, String? date}) =>
      _postJson('/api/v1/reminders', {'text': text, 'time': time, 'date': date});

  static Future<Map<String, dynamic>> completeReminder(String id, {bool completed = true}) =>
      _postJson('/api/v1/reminders/$id/complete', {'completed': completed});

  static Future<Map<String, dynamic>> deleteReminder(String id) => _delete('/api/v1/reminders/$id');

  static Future<List<dynamic>> getAlarms() async {
    final data = await _getJson('/api/v1/alarms');
    return (data['alarms'] as List<dynamic>?) ?? [];
  }

  static Future<Map<String, dynamic>> createAlarm({required String time, String label = 'Alarm'}) =>
      _postJson('/api/v1/alarms', {'time': time, 'label': label});

  static Future<Map<String, dynamic>> toggleAlarm(String id, bool enabled) =>
      _postJson('/api/v1/alarms/$id/toggle', {'enabled': enabled});

  static Future<Map<String, dynamic>> deleteAlarm(String id) => _delete('/api/v1/alarms/$id');

  // --- Device convenience wrappers (all instant, no LLM) ---

  /// Triggers audible ring/beacon on the device ('Find My Device').
  static Future<Map<String, dynamic>> ringDevice(String deviceId, {String? message}) =>
      deviceAction(deviceId, 'ring_device', {'message': message ?? 'Find My Device', 'duration_sec': 20});

  /// Pushes clipboard text to target device.
  static Future<Map<String, dynamic>> sendClipboard(String deviceId, String text) =>
      deviceAction(deviceId, 'set_clipboard', {'text': text});

  /// Reads current clipboard text from target device.
  static Future<String?> getClipboard(String deviceId) async {
    final res = await deviceAction(deviceId, 'get_clipboard');
    return res['success'] == true ? res['text'] as String? : null;
  }

  /// Pushes a URL or quick text note to open on the device.
  static Future<Map<String, dynamic>> quickDrop(String deviceId, {String? url, String? text, String? title}) =>
      deviceAction(deviceId, 'quickdrop', {'url': url, 'text': text, 'title': title});

  /// Low-latency JPEG snapshot of the PC monitor (lower quality/width = faster frames).
  static Future<Map<String, dynamic>> getScreenSnapshot(
    String deviceId, {
    int quality = 60,
    int maxWidth = 800,
    String monitor = 'active',
  }) =>
      deviceAction(deviceId, 'get_screen_snapshot', {'quality': quality, 'max_width': maxWidth, 'monitor': monitor},
          const Duration(seconds: 12));

  /// Controls volume or media playback on target PC.
  static Future<Map<String, dynamic>> controlMedia(String deviceId, String action, {int? level}) =>
      deviceAction(deviceId, 'media_control', {'action': action, 'level': level});

  /// Slider-driven, so the PC shows no pop-up for every drag.
  static Future<Map<String, dynamic>> setVolume(String deviceId, int level) =>
      deviceAction(deviceId, 'volume_control', {'action': 'set', 'level': level, 'quiet': true});

  static Future<Map<String, dynamic>> listProcesses(String deviceId, {String sortBy = 'cpu', int limit = 25}) =>
      deviceAction(deviceId, 'list_processes', {'sort_by': sortBy, 'limit': limit});

  static Future<Map<String, dynamic>> killProcess(String deviceId, int pid) =>
      deviceAction(deviceId, 'kill_process', {'pid': pid});

  // --- Willy Server (device_type "server") ---

  /// The hub's health check of the server: summary, problems, websites, services...
  /// [refresh] re-checks every website now (slower).
  static Future<Map<String, dynamic>> getServerStatus({bool refresh = false}) => _getJson(
        '/api/v1/server/status',
        query: refresh ? {'refresh': 'true'} : null,
        timeout: Duration(seconds: refresh ? 60 : 20),
      );

  /// Mutes (or unmutes) a server problem, e.g. a site that isn't run any more.
  static Future<Map<String, dynamic>> ignoreServerProblem(String key, {bool ignore = true}) =>
      _postJson('/api/v1/server/ignore', {'key': key, 'ignore': ignore});

  /// CPU / RAM / disk / swap / load / network minute samples over the last [hours] (1, 6, 24, 168).
  static Future<Map<String, dynamic>> getServerHistory({required int hours, int points = 240}) => _getJson(
        '/api/v1/server/history',
        query: {'hours': '$hours', 'points': '$points'},
        timeout: const Duration(seconds: 20),
      );

  /// How long the app waits for each server admin action. The hub gives the server agent a
  /// little less, so its own "took too long" answer arrives first.
  static const serverActionTimeouts = <String, Duration>{
    'sys_updates': Duration(seconds: 260), // dnf check-update --refresh: up to ~4 min
    'apply_updates': Duration(minutes: 32), // dnf upgrade: up to 30 min
    'storage': Duration(seconds: 130),
    'cleanup': Duration(seconds: 330),
    'network': Duration(seconds: 40),
    'security': Duration(seconds: 85),
    'services_list': Duration(seconds: 40),
    'service_boot': Duration(seconds: 40),
    'timers': Duration(seconds: 40),
    'journal': Duration(seconds: 55),
    'reboot': Duration(seconds: 30),
    'cancel_reboot': Duration(seconds: 30),
    'control': Duration(seconds: 125),
  };

  /// A server admin action (`sys_updates`, `storage`, `cleanup`, ...) with its long timeout.
  static Future<Map<String, dynamic>> serverAction(String deviceId, String action, [Map<String, dynamic>? payload]) =>
      deviceAction(deviceId, action, payload ?? {}, serverActionTimeouts[action] ?? const Duration(seconds: 40));

  static void dispose() {
    _stopped = true;
    _serial++;
    _teardownSocket();
    _countsTimer?.cancel();
    _failPending('App closing.', keepLate: false);
  }
}

class _LateRequest {
  final String query;
  final DateTime at;

  const _LateRequest(this.query, this.at);
}
