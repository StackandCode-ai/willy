/// Push notifications from the hub (Firebase Cloud Messaging): the message contract.
///
/// Notification messages carry `{"notification": {title, body}, "data": {kind, id?, screen?}}`;
/// a data-only `{"data": {"kind": "wake"}}` asks the app to reconnect to the hub.
library;

enum PushKind { reminder, alarm, alert, file, test, wake, unknown }

PushKind pushKindOf(Map<String, dynamic> data) {
  final kind = data['kind']?.toString().trim().toLowerCase();
  for (final k in PushKind.values) {
    if (k != PushKind.unknown && k.name == kind) return k;
  }
  return PushKind.unknown;
}

/// Sent over the device socket after getting a token, on every refresh and after each reconnect.
Map<String, dynamic> pushTokenMessage(String token) =>
    {'type': 'push_token', 'token': token, 'platform': 'android'};

/// Body for `POST /api/v1/push/register` while the socket is down.
Map<String, dynamic> pushRegisterBody(String deviceId, String token) =>
    {'device_id': deviceId, 'token': token, 'platform': 'android'};

/// `data["screen"]` -> the home tab it opens (indexes follow `_tabs` in main.dart; reminders
/// live on the Morning tab). Unknown screens just open the app.
const pushScreenTabs = {'devices': 0, 'remote': 1, 'activity': 3, 'reminders': 4};

int? tabForPushScreen(String? screen) =>
    screen == null ? null : pushScreenTabs[screen.trim().toLowerCase()];

/// The id a hub event or push names its reminder / alarm / alert by, if any.
String? alertIdOf(Map<String, dynamic> data) {
  String? clean(Object? v) {
    final s = v?.toString().trim();
    return (s == null || s.isEmpty) ? null : s;
  }

  final direct = clean(data['id']);
  if (direct != null) return direct;
  for (final key in const ['reminder', 'alarm']) {
    final item = data[key];
    if (item is Map) {
      final id = clean(item['id']);
      if (id != null) return id;
    }
  }
  return null;
}

/// Ids of alerts shown recently, so one reminder that reaches the phone both over the hub
/// socket and as a push only notifies once. Small and time-boxed.
class RecentIds {
  final int capacity;
  final Duration window;
  final DateTime Function() _now;
  final Map<String, DateTime> _seen = {}; // insertion order = age

  RecentIds({this.capacity = 50, this.window = const Duration(minutes: 30), DateTime Function()? now})
      : _now = now ?? DateTime.now;

  void _prune() {
    final now = _now();
    _seen.removeWhere((_, at) => now.difference(at) > window);
    while (_seen.length > capacity) {
      _seen.remove(_seen.keys.first);
    }
  }

  bool contains(String? id) {
    if (id == null || id.isEmpty) return false;
    _prune();
    return _seen.containsKey(id);
  }

  /// Records [id]; true when it is new (show it), false when it was already shown.
  /// Alerts without an id are always new.
  bool add(String? id) {
    if (id == null || id.isEmpty) return true;
    _prune();
    if (_seen.containsKey(id)) return false;
    _seen[id] = _now();
    _prune();
    return true;
  }

  int get length => _seen.length;
}
