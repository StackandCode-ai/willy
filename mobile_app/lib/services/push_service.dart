import 'dart:async';
import 'dart:isolate';
import 'dart:ui';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';

import '../models/push.dart';
import 'api_service.dart';
import 'telemetry_service.dart';

/// The main isolate listens here so a "wake" push handled in the background isolate can
/// reach the live app (process-wide name, see [IsolateNameServer]).
const _wakePortName = 'willy_push_wake';

/// Runs in a background isolate for pushes that arrive while Willy isn't in the foreground.
/// Android shows notification messages by itself; this only handles the data-only "wake".
@pragma('vm:entry-point')
Future<void> willyPushBackgroundHandler(RemoteMessage message) async {
  if (pushKindOf(message.data) != PushKind.wake) return;
  final port = IsolateNameServer.lookupPortByName(_wakePortName);
  if (port != null) {
    port.send('wake'); // the app is alive in the background: it reconnects to the hub
  } else {
    // No native keep-alive service holds the hub connection; Willy reconnects when opened.
    debugPrint('[Push] Wake push while Willy is closed; it reconnects when opened.');
  }
}

class PushStatus {
  /// Firebase is set up and this phone has a push token.
  final bool available;

  /// Android lets Willy show notifications.
  final bool permitted;

  /// The current token reached the hub.
  final bool registered;

  const PushStatus({this.available = false, this.permitted = false, this.registered = false});

  bool get isOn => available && permitted;
}

/// Firebase Cloud Messaging: registers this phone's push token with the hub and handles pushes
/// (foreground notifications, "wake" requests, taps that open a screen).
class PushService {
  static bool _firebaseReady = false;
  static bool _started = false;
  static bool _hasToken = false;
  static int _tokenAttempts = 0;
  static ReceivePort? _wakePort;
  static final List<StreamSubscription> _subs = [];

  /// Alerts already shown (by id), shared by the hub socket and foreground pushes.
  static final RecentIds recentAlerts = RecentIds();

  static final ValueNotifier<PushStatus> status = ValueNotifier(const PushStatus());

  /// A screen a tapped notification asked for ("reminders", "devices", ...); the home shell
  /// switches tabs and clears it.
  static final ValueNotifier<String?> openScreen = ValueNotifier(null);

  /// Before runApp: Firebase itself and the background handler. Never throws.
  static Future<void> initFirebase() async {
    try {
      await Firebase.initializeApp(); // options come from android/app/google-services.json
      FirebaseMessaging.onBackgroundMessage(willyPushBackgroundHandler);
      _firebaseReady = true;
    } catch (e) {
      debugPrint('[Push] Firebase unavailable: $e');
    }
  }

  /// After the hub connection is set up (device id known): token, listeners, opened-from-push.
  static Future<void> start() async {
    if (_started || !_firebaseReady) return;
    _started = true;
    final messaging = FirebaseMessaging.instance;

    _wakePort = ReceivePort();
    IsolateNameServer.removePortNameMapping(_wakePortName);
    IsolateNameServer.registerPortWithName(_wakePort!.sendPort, _wakePortName);
    _subs.add(_wakePort!.listen((_) => ApiService.ensureConnected()));

    ApiService.pushRegistered.addListener(_publish);
    _subs.add(FirebaseMessaging.onMessage.listen(_onForegroundMessage));
    _subs.add(FirebaseMessaging.onMessageOpenedApp.listen(_onOpened));
    _subs.add(messaging.onTokenRefresh.listen(_register, onError: (_) {}));

    try {
      final initial = await messaging.getInitialMessage();
      if (initial != null) _onOpened(initial);
    } catch (_) {}

    await _fetchToken();
  }

  static Future<void> _fetchToken() async {
    try {
      final token = await FirebaseMessaging.instance.getToken();
      if (token != null && token.isNotEmpty) {
        await _register(token);
        return;
      }
    } catch (e) {
      debugPrint('[Push] Could not get a push token: ${e.runtimeType}');
    }
    // No network / Play services yet: try again a few times.
    if (++_tokenAttempts < 4) Timer(Duration(seconds: 30 * _tokenAttempts), _fetchToken);
  }

  static Future<void> _register(String token) async {
    _hasToken = true;
    await refreshStatus();
    await ApiService.registerPushToken(token);
  }

  /// Re-reads whether notifications are allowed (the settings sheet calls it on open).
  static Future<PushStatus> refreshStatus() async {
    if (_firebaseReady) {
      try {
        final settings = await FirebaseMessaging.instance.getNotificationSettings();
        _permitted = settings.authorizationStatus == AuthorizationStatus.authorized ||
            settings.authorizationStatus == AuthorizationStatus.provisional;
      } catch (_) {}
    }
    _publish();
    return status.value;
  }

  static bool _permitted = false;

  static void _publish() {
    status.value = PushStatus(
      available: _firebaseReady && _hasToken,
      permitted: _permitted,
      registered: ApiService.pushRegistered.value,
    );
  }

  static void _onForegroundMessage(RemoteMessage message) {
    final data = message.data;
    if (pushKindOf(data) == PushKind.wake) {
      ApiService.ensureConnected();
      return;
    }
    final n = message.notification;
    final title = n?.title ?? data['title']?.toString();
    final body = n?.body ?? data['body']?.toString() ?? '';
    if (title == null && body.isEmpty) return;
    // Already shown from the hub socket (or an earlier copy of this push).
    if (!recentAlerts.add(alertIdOf(data))) return;
    MobileTelemetryService.showNotification(title ?? 'Willy', body);
  }

  static void _onOpened(RemoteMessage message) {
    final screen = message.data['screen']?.toString();
    if (screen != null && screen.isNotEmpty) openScreen.value = screen;
  }
}
