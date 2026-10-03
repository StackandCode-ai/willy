import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'api_service.dart';

/// A file on the phone (a content:// URI from the picker or the share sheet).
class PhoneFile {
  final String uri;
  final String name;
  final int size; // -1 when unknown
  final String mime;

  const PhoneFile({required this.uri, required this.name, required this.size, required this.mime});

  static PhoneFile? fromMap(Object? raw) {
    if (raw is! Map) return null;
    final uri = raw['uri']?.toString() ?? '';
    if (uri.isEmpty) return null;
    final size = raw['size'];
    return PhoneFile(
      uri: uri,
      name: (raw['name']?.toString().trim().isNotEmpty ?? false) ? raw['name'].toString().trim() : 'file',
      size: size is num ? size.toInt() : -1,
      mime: raw['mime']?.toString() ?? 'application/octet-stream',
    );
  }
}

/// What another app shared to Willy: files, or just text (a link or a note).
class SharedPayload {
  final List<PhoneFile> files;
  final String? text;

  const SharedPayload(this.files, this.text);

  bool get isEmpty => files.isEmpty && (text == null || text!.trim().isEmpty);
}

/// Bytes moved so far for one upload or download.
class TransferProgress {
  final String id;
  final int done;
  final int total; // -1 when unknown

  const TransferProgress(this.id, this.done, this.total);

  double? get fraction => total > 0 ? (done / total).clamp(0.0, 1.0) : null;
}

/// Files between the phone and the PC, through the hub. The bytes are streamed natively
/// (MainActivity / FileTransfer.kt), so large files never pass through Dart memory.
class FileTransferService {
  static const MethodChannel _channel = MethodChannel('com.example.willy_mobile/telemetry');
  static const _transferTimeout = Duration(minutes: 30);

  static final _progressCtl = StreamController<TransferProgress>.broadcast();
  static final _sharedCtl = StreamController<void>.broadcast();
  static int _serial = 0;

  static Stream<TransferProgress> get progress => _progressCtl.stream;

  /// Fires when another app shared something to Willy while it was running.
  static Stream<void> get sharedArrived => _sharedCtl.stream;

  /// Calls from the native side (routed here by MobileTelemetryService's channel handler).
  static void onNativeCall(String method, Object? args) {
    switch (method) {
      case 'onSharedFiles':
        if (!_sharedCtl.isClosed) _sharedCtl.add(null);
        break;
      case 'onTransferProgress':
        if (args is Map && !_progressCtl.isClosed) {
          final done = args['done'];
          final total = args['total'];
          _progressCtl.add(TransferProgress(
            args['id']?.toString() ?? '',
            done is num ? done.toInt() : 0,
            total is num ? total.toInt() : -1,
          ));
        }
        break;
    }
  }

  static String newTransferId() => 't${DateTime.now().millisecondsSinceEpoch}_${_serial++}';

  /// Android's document picker (no extra permission needed). Empty when cancelled.
  static Future<List<PhoneFile>> pickFiles() async {
    try {
      final res = await _channel.invokeMethod<List<dynamic>>('pickFiles');
      return (res ?? const []).map(PhoneFile.fromMap).whereType<PhoneFile>().toList();
    } catch (e) {
      debugPrint('[Files] pickFiles error: $e');
      return const [];
    }
  }

  /// Takes (and clears) what was shared to Willy from another app.
  static Future<SharedPayload> takeShared() async {
    try {
      final res = await _channel.invokeMethod<Map<dynamic, dynamic>>('takeSharedFiles');
      if (res == null) return const SharedPayload([], null);
      final files = (res['files'] as List? ?? const []).map(PhoneFile.fromMap).whereType<PhoneFile>().toList();
      return SharedPayload(files, res['text']?.toString());
    } catch (e) {
      debugPrint('[Files] takeSharedFiles error: $e');
      return const SharedPayload([], null);
    }
  }

  /// Uploads [file] to the hub for the PC. Returns `{success, reply, delivered?, error?}`.
  static Future<Map<String, dynamic>> uploadToPc(PhoneFile file, {String? transferId}) async {
    final url = ApiService.normalizeBaseUrl(ApiService.baseUrl);
    final query = Uri(queryParameters: {'target': 'pc', 'from': ApiService.deviceId}).query;
    try {
      final res = await _channel.invokeMethod<Map<dynamic, dynamic>>('uploadFile', {
        'uri': file.uri,
        'url': '$url/api/v1/files?$query',
        'token': ApiService.token,
        'name': file.name,
        'mime': file.mime,
        'size': file.size,
        'transfer_id': transferId ?? newTransferId(),
      }).timeout(_transferTimeout);
      if (res == null) return {'success': false, 'error': "The phone couldn't read ${file.name}."};
      final status = res['status'];
      return parseUploadResponse(status is num ? status.toInt() : -1, res['body']?.toString() ?? '',
          name: file.name, error: res['error']?.toString());
    } on TimeoutException {
      return {'success': false, 'error': 'Sending ${file.name} took too long.'};
    } on MissingPluginException {
      return {'success': false, 'error': 'Sending files needs the latest Willy app on the phone.'};
    } catch (e) {
      return {'success': false, 'error': 'Could not send ${file.name} ($e).'};
    }
  }

  /// A file the app itself wrote (e.g. a photo in its cache), as something [uploadToPc] can send.
  static PhoneFile localFile(String path, {required String name, String mime = 'application/octet-stream'}) {
    int size;
    try {
      size = File(path).lengthSync();
    } catch (_) {
      size = -1;
    }
    return PhoneFile(uri: Uri.file(path).toString(), name: name, size: size, mime: mime);
  }

  /// Copies a photo the app took into the phone's gallery (Pictures/Willy).
  /// Returns `{success, message | error}`.
  static Future<Map<String, dynamic>> saveImageToGallery(String path, {required String name, String mime = 'image/jpeg'}) async {
    try {
      final res = await _channel.invokeMethod<Map<dynamic, dynamic>>('saveToGallery', {
        'path': path,
        'name': name,
        'mime': mime,
      }).timeout(const Duration(seconds: 30));
      if (res == null) return {'success': false, 'error': "The phone couldn't save the photo."};
      return Map<String, dynamic>.from(res);
    } on TimeoutException {
      return {'success': false, 'error': 'Saving the photo took too long.'};
    } on MissingPluginException {
      return {'success': false, 'error': 'Saving photos needs the latest Willy app on the phone.'};
    } catch (e) {
      return {'success': false, 'error': "The phone couldn't save the photo ($e)."};
    }
  }

  /// The hub's answer to `POST /api/v1/files`, as `{success, reply, delivered, file?, error?}`.
  static Map<String, dynamic> parseUploadResponse(int status, String body, {String name = 'the file', String? error}) {
    if (status < 0) {
      return {'success': false, 'error': error ?? "Couldn't reach the hub to send $name."};
    }
    Map<String, dynamic> json = const {};
    try {
      final decoded = jsonDecode(body);
      if (decoded is Map) json = Map<String, dynamic>.from(decoded);
    } catch (_) {}
    if (status == 401 || status == 403) {
      return {'success': false, 'error': 'The hub refused the upload: check the Remote token in settings.'};
    }
    if (status == 413) return {'success': false, 'error': '$name is too big for the hub.'};
    if (status < 200 || status >= 300) {
      final detail = json['detail'] ?? json['error'] ?? json['reply'];
      return {'success': false, 'error': detail?.toString() ?? 'The hub returned $status for $name.'};
    }
    final ok = json['success'] != false;
    final delivered = json['delivered'] == true;
    final reply = json['reply']?.toString() ??
        (ok ? (delivered ? 'Sent $name to your PC.' : 'Uploaded $name — the PC will get it when it connects.') : null);
    return {
      'success': ok,
      'delivered': delivered,
      if (json['file'] is Map) 'file': Map<String, dynamic>.from(json['file']),
      if (reply != null) 'reply': reply,
      if (!ok) 'error': (json['error'] ?? json['detail'] ?? reply ?? 'The hub could not take $name.').toString(),
    };
  }

  /// Hub action `receive_file` {id, name, size, mime, url, from}: download into
  /// Downloads/Willy and post a "tap to open" notification.
  static Future<Map<String, dynamic>> receiveFromHub(Map<String, dynamic> payload) async {
    final rawUrl = payload['url']?.toString().trim() ?? '';
    final name = payload['name']?.toString().trim() ?? '';
    if (rawUrl.isEmpty) return {'success': false, 'error': 'No file link to download.'};
    final url = hubFileUrl(rawUrl, ApiService.baseUrl);
    if (url == null) return {'success': false, 'error': "That file link doesn't look right."};
    final size = payload['size'];
    try {
      final res = await _channel.invokeMethod<Map<dynamic, dynamic>>('receiveFile', {
        'url': url,
        // The token only ever goes to the hub itself.
        'token': sameOrigin(url, ApiService.baseUrl) ? ApiService.token : null,
        'name': name.isEmpty ? 'file' : name,
        'mime': payload['mime']?.toString(),
        'size': size is num ? size.toInt() : -1,
        'from': (payload['from']?.toString().trim().isNotEmpty ?? false) ? payload['from'].toString().trim() : 'your PC',
        'transfer_id': payload['id']?.toString() ?? newTransferId(),
      }).timeout(_transferTimeout);
      if (res == null) return {'success': false, 'error': "The phone couldn't save the file."};
      return Map<String, dynamic>.from(res);
    } on TimeoutException {
      return {'success': false, 'error': 'Downloading ${name.isEmpty ? 'the file' : name} took too long.'};
    } on MissingPluginException {
      return {'success': false, 'error': 'Receiving files needs the latest Willy app on the phone.'};
    } on PlatformException catch (e) {
      return {'success': false, 'error': e.message ?? "The phone couldn't save the file."};
    } catch (e) {
      return {'success': false, 'error': "The phone couldn't save the file ($e)."};
    }
  }

  /// Resolves a hub file link: "/api/v1/files/x" is relative to the hub base URL
  /// (which may carry a path prefix); absolute http(s) links are kept.
  static String? hubFileUrl(String url, String baseUrl) {
    final u = url.trim();
    if (u.isEmpty) return null;
    if (RegExp(r'^https?://', caseSensitive: false).hasMatch(u)) return u;
    if (u.contains('://')) return null;
    final base = ApiService.normalizeBaseUrl(baseUrl);
    return u.startsWith('/') ? '$base$u' : '$base/$u';
  }

  static bool sameOrigin(String url, String baseUrl) {
    try {
      final a = Uri.parse(url);
      final b = Uri.parse(ApiService.normalizeBaseUrl(baseUrl));
      return a.scheme == b.scheme && a.host.toLowerCase() == b.host.toLowerCase() && a.port == b.port;
    } catch (_) {
      return false;
    }
  }

  static String formatBytes(int bytes) {
    if (bytes < 0) return '';
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(0)} KB';
    if (bytes < 1024 * 1024 * 1024) return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
    return '${(bytes / (1024 * 1024 * 1024)).toStringAsFixed(2)} GB';
  }
}
