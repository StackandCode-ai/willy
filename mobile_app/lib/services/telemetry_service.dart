import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import '../models/phone_skills.dart';
import 'api_service.dart';
import 'file_transfer_service.dart';

typedef WakeWordHandler = void Function(String command, String trigger, String fullText);

/// Bridge to the native Android side (MainActivity): device status, notification
/// counts, ring / torch / vibrate / speech, and the "Hey Willy" wake-word service.
class MobileTelemetryService {
  static const MethodChannel _channel = MethodChannel('com.example.willy_mobile/telemetry');

  /// Last notification / missed-call counts, reused by the realtime heartbeat.
  static Map<String, dynamic> lastCounts = {};

  /// Queries native Android Notification & Call Log services and syncs to Willy Server.
  /// Returns an empty map (and sends nothing) when the native data isn't available,
  /// so the briefing never shows invented numbers.
  static Future<Map<String, dynamic>> syncTelemetryToServer() async {
    Map<String, dynamic> telemetry = {};
    try {
      final res = await _channel.invokeMethod<Map<dynamic, dynamic>>('getMobileTelemetry');
      if (res != null) telemetry = Map<String, dynamic>.from(res);
    } on PlatformException catch (e) {
      debugPrint('[Telemetry] Native telemetry unavailable: ${e.message}');
    } catch (e) {
      debugPrint('[Telemetry] Error: $e');
    }
    if (telemetry.isEmpty) return telemetry;

    lastCounts = {
      'unread_notifications': telemetry['total_notifications_count'] ?? 0,
      'unread_whatsapp': telemetry['whatsapp_unread_count'] ?? 0,
      'missed_calls': telemetry['missed_calls_count'] ?? 0,
    };

    try {
      final status = await getDeviceStatus();
      await ApiService.updateMobileTelemetry({
        ...telemetry,
        'device_id': ApiService.deviceId,
        'device_name': ApiService.deviceName,
        'model': status['model'],
        'platform': status['android_version'] != null ? 'Android ${status['android_version']}' : null,
        'battery_pct': status['battery_pct'],
        'is_charging': status['is_charging'],
      });
    } catch (e) {
      debugPrint('[Telemetry] Failed to push telemetry to server: $e');
    }
    return telemetry;
  }

  /// Live battery, charging, network, storage, RAM plus device identity.
  static Future<Map<String, dynamic>> getDeviceStatus() async {
    try {
      final res = await _channel.invokeMethod<Map<dynamic, dynamic>>('getDeviceStatus');
      if (res != null) return Map<String, dynamic>.from(res);
    } catch (e) {
      debugPrint('[Telemetry] getDeviceStatus error: $e');
    }
    return {};
  }

  static Future<bool> hasNotificationPermission() async {
    try {
      final res = await _channel.invokeMethod<bool>('hasNotificationPermission');
      return res ?? false;
    } catch (_) {
      return false;
    }
  }

  static Future<void> requestNotificationPermission() async {
    try {
      await _channel.invokeMethod('requestNotificationPermission');
    } catch (_) {}
  }

  static Future<void> ringPhone({int durationSec = 30}) async {
    try {
      await _channel.invokeMethod('ringPhone', {'durationSec': durationSec});
    } catch (e) {
      debugPrint('[Telemetry] ringPhone error: $e');
    }
  }

  static Future<void> stopRingPhone() async {
    try {
      await _channel.invokeMethod('stopRingPhone');
    } catch (_) {}
  }

  static Future<bool> openUrl(String url) async {
    try {
      return await _channel.invokeMethod<bool>('openUrl', {'url': url}) ?? false;
    } catch (_) {
      return false;
    }
  }

  static Future<bool> setTorch(bool on) async {
    try {
      return await _channel.invokeMethod<bool>('setTorch', {'on': on}) ?? false;
    } catch (_) {
      return false;
    }
  }

  static Future<void> vibrate({int ms = 400}) async {
    try {
      await _channel.invokeMethod('vibrate', {'ms': ms});
    } catch (_) {}
  }

  static Future<void> speak(String text) async {
    if (text.trim().isEmpty) return;
    try {
      await _channel.invokeMethod('speak', {'text': text});
    } catch (_) {}
  }

  static Future<void> stopSpeaking() async {
    try {
      await _channel.invokeMethod('stopSpeaking');
    } catch (_) {}
  }

  static Future<void> showNotification(String title, String text) async {
    try {
      await _channel.invokeMethod('showNotification', {'title': title, 'text': text});
    } catch (_) {}
  }

  // --- Phone skills ------------------------------------------------------------

  /// Runs a hub phone action (phone_call, phone_sms, phone_whatsapp, …) natively. Never
  /// throws: always returns the contract result `{success, message | error, needs_tap?, …}`.
  static Future<Map<String, dynamic>> phoneAction(String action, Map<String, dynamic> payload) async {
    try {
      final res = await _channel
          .invokeMethod<Object?>('phoneAction', {'action': action, 'payload': payload})
          // Below the hub's 20 s action timeout, so the hub gets a clear answer.
          .timeout(const Duration(seconds: 18));
      return normalizePhoneResult(res);
    } on TimeoutException {
      return {'success': false, 'error': 'The phone took too long to do that.'};
    } on MissingPluginException {
      return {'success': false, 'error': 'Phone skills need the latest Willy app on the phone.'};
    } on PlatformException catch (e) {
      return {'success': false, 'error': e.message ?? "That didn't work on the phone."};
    } catch (_) {
      return {'success': false, 'error': "That didn't work on the phone."};
    }
  }

  /// Which phone-skill permissions / special accesses are granted.
  static Future<PhonePermissions> getPhonePermissions() async {
    try {
      return PhonePermissions.fromMap(await _channel.invokeMethod<Map<dynamic, dynamic>>('getPhonePermissions'));
    } catch (_) {
      return PhonePermissions.unavailable;
    }
  }

  /// Prompts for runtime permissions ('call_phone', 'send_sms', 'read_contacts',
  /// 'post_notifications') and resolves with the new status once the user answers.
  /// Android opens App info instead when it won't show the prompt again.
  static Future<PhonePermissions> requestPhonePermissions(List<String> keys) async {
    try {
      return PhonePermissions.fromMap(
          await _channel.invokeMethod<Map<dynamic, dynamic>>('requestPhonePermissions', {'permissions': keys}));
    } catch (_) {
      return PhonePermissions.unavailable;
    }
  }

  /// Android's "Notification access" screen (lets Willy read recent notifications).
  static Future<bool> openNotificationAccessSettings() => _invokeBool('openNotificationAccessSettings');

  /// "Display over other apps": lets Willy open apps while it is in the background.
  static Future<bool> openOverlaySettings() => _invokeBool('openOverlaySettings');

  /// Willy's App info screen (for permissions Android no longer prompts for).
  static Future<bool> openAppSettings() => _invokeBool('openAppSettings');

  static Future<bool> _invokeBool(String method) async {
    try {
      return await _channel.invokeMethod<bool>(method) ?? false;
    } catch (_) {
      return false;
    }
  }

  // --- "Hey Willy" wake word -------------------------------------------------
  // The home shell installs the default handler; the voice-call screen installs a
  // temporary override and clears it on exit, so the global handler is restored.

  static WakeWordHandler? _defaultHandler;
  static WakeWordHandler? _overrideHandler;
  static bool _channelBound = false;

  static void initializeWakeWordChannel() {
    if (_channelBound) return;
    _channelBound = true;
    _channel.setMethodCallHandler((call) async {
      if (call.method == 'onWakeWordDetected') {
        final args = Map<String, dynamic>.from(call.arguments ?? {});
        final trigger = args['trigger']?.toString() ?? 'Hey Willy';
        final command = args['command']?.toString() ?? '';
        final fullText = args['full_text']?.toString() ?? '';
        debugPrint("[WakeWord] Triggered: trigger='$trigger', command='$command', full='$fullText'");
        (_overrideHandler ?? _defaultHandler)?.call(command, trigger, fullText);
      } else {
        // File transfers share this channel: shared-files notices and progress updates.
        FileTransferService.onNativeCall(call.method, call.arguments);
      }
    });
  }

  static void setWakeWordHandler(WakeWordHandler callback) {
    _defaultHandler = callback;
    initializeWakeWordChannel();
  }

  static void setWakeWordOverride(WakeWordHandler? callback) {
    _overrideHandler = callback;
    initializeWakeWordChannel();
  }

  static Future<bool> startWakeWordDetection() async {
    try {
      initializeWakeWordChannel();
      final res = await _channel.invokeMethod<bool>('startWakeWordDetection');
      return res ?? false;
    } catch (e) {
      debugPrint('[WakeWord] startWakeWordDetection error: $e');
      return false;
    }
  }

  static Future<bool> stopWakeWordDetection() async {
    try {
      final res = await _channel.invokeMethod<bool>('stopWakeWordDetection');
      return res ?? false;
    } catch (_) {
      return false;
    }
  }

  /// Releases the microphone from "Hey Willy" while the app records a voice command.
  static Future<void> pauseWakeWord() async {
    try {
      await _channel.invokeMethod('pauseWakeWord');
    } catch (_) {}
  }

  static Future<void> resumeWakeWord() async {
    try {
      await _channel.invokeMethod('resumeWakeWord');
    } catch (_) {}
  }

  static Future<bool> isWakeWordSupported() async {
    try {
      final res = await _channel.invokeMethod<bool>('isWakeWordSupported');
      return res ?? false;
    } catch (_) {
      return false;
    }
  }
}
