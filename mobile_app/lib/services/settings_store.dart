import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:path_provider/path_provider.dart';

/// App settings persisted as JSON in the app's private documents folder,
/// so the hub URL / token survive restarts (they used to reset every launch).
class WillySettings {
  static const defaultBaseUrl = 'http://127.0.0.1:8000';
  static const defaultToken = '';  // set in Settings (or by the hub's token update)

  static String baseUrl = defaultBaseUrl;
  static String token = defaultToken;
  static String deviceId = '';
  static String deviceName = '';
  static bool wakeWordEnabled = true;
  static bool speakReplies = true;
  /// Voice call: start listening again by itself after Willy answers.
  static bool callHandsFree = true;
  static String liveScreenQuality = 'medium';
  /// Recent commands run in the Server screen's terminal, newest first.
  static List<String> serverShellHistory = [];

  static File? _file;
  static bool loaded = false;

  static Future<File> _settingsFile() async {
    if (_file != null) return _file!;
    final dir = await getApplicationDocumentsDirectory();
    _file = File('${dir.path}/willy_settings.json');
    return _file!;
  }

  static Future<void> load() async {
    try {
      final file = await _settingsFile();
      if (await file.exists()) {
        final data = jsonDecode(await file.readAsString());
        if (data is Map) {
          baseUrl = (data['base_url'] as String?)?.trim().isNotEmpty == true ? data['base_url'] : baseUrl;
          token = (data['token'] as String?)?.trim().isNotEmpty == true ? data['token'] : token;
          deviceId = data['device_id'] as String? ?? deviceId;
          deviceName = data['device_name'] as String? ?? deviceName;
          wakeWordEnabled = data['wake_word_enabled'] as bool? ?? wakeWordEnabled;
          speakReplies = data['speak_replies'] as bool? ?? speakReplies;
          callHandsFree = data['call_hands_free'] as bool? ?? callHandsFree;
          liveScreenQuality = data['live_screen_quality'] as String? ?? liveScreenQuality;
          final shell = data['server_shell_history'];
          if (shell is List) serverShellHistory = shell.whereType<String>().take(30).toList();
        }
      }
    } catch (_) {
      // Corrupt or unreadable settings: keep defaults.
    }
    loaded = true;
  }

  static Future<void> save() async {
    try {
      final file = await _settingsFile();
      await file.writeAsString(jsonEncode({
        'base_url': baseUrl,
        'token': token,
        'device_id': deviceId,
        'device_name': deviceName,
        'wake_word_enabled': wakeWordEnabled,
        'speak_replies': speakReplies,
        'call_hands_free': callHandsFree,
        'live_screen_quality': liveScreenQuality,
        'server_shell_history': serverShellHistory,
      }));
    } catch (_) {}
  }

  /// Stable id for this phone: derived from ANDROID_ID when available, else random.
  static Future<String> ensureDeviceId(String? androidId) async {
    if (deviceId.isNotEmpty) return deviceId;
    final id = (androidId != null && androidId.isNotEmpty)
        ? androidId.toLowerCase()
        : List.generate(12, (_) => Random.secure().nextInt(16).toRadixString(16)).join();
    deviceId = 'mobile_$id';
    await save();
    return deviceId;
  }

  static bool get usingDefaultToken => token.trim().length < 20;
}
