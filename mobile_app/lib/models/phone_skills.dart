/// Phone skills: the hub (or the PC, through the hub) asks this phone to call, text,
/// WhatsApp, open apps, set alarms and timers, change volume, control media, navigate,
/// read recent notifications and look up contacts. The native side does the work
/// (android/.../PhoneActions.kt); this file holds the pure-Dart pieces.
library;

/// Things to try, shown under Settings > Phone skills.
const phoneSkillExamples = <String>[
  'Call Mom',
  "Text Dad I'm on my way",
  'WhatsApp Priya see you at 6',
  'Set a timer for 10 minutes',
  'Open Spotify on my phone',
  'Read my notifications',
];

/// Turns what the platform channel returned into a JSON-safe result that always follows
/// the contract: `success` is a bool and a failure carries a user-facing `error`.
Map<String, dynamic> normalizePhoneResult(Object? raw) {
  if (raw is! Map) return {'success': false, 'error': "The phone didn't answer."};
  final out = _jsonSafe(raw) as Map<String, dynamic>;
  final success = out['success'] == true;
  out['success'] = success;
  if (!success) {
    final error = out['error']?.toString().trim() ?? '';
    out['error'] = error.isEmpty ? "That didn't work on the phone." : error;
  }
  return out;
}

Object? _jsonSafe(Object? value) {
  if (value is Map) {
    return <String, dynamic>{for (final e in value.entries) e.key.toString(): _jsonSafe(e.value)};
  }
  if (value is List) return [for (final v in value) _jsonSafe(v)];
  return value;
}

/// Runtime permissions and special accesses behind the phone skills.
class PhonePermissions {
  static const callPhone = 'call_phone';
  static const sendSms = 'send_sms';
  static const readContacts = 'read_contacts';
  static const notificationAccess = 'notification_access';
  static const overlay = 'overlay'; // "display over other apps" = background actions
  static const postNotifications = 'post_notifications';

  static const keys = [callPhone, sendSms, readContacts, notificationAccess, overlay, postNotifications];

  /// False when the native side couldn't be reached (tests, another platform, an old build).
  final bool available;
  final Map<String, bool> granted;

  /// Runtime permissions Android won't prompt for again: only App info can grant them now.
  final Set<String> blocked;
  final bool hasTelephony;
  final bool whatsappInstalled;
  final bool listenerConnected;

  const PhonePermissions({
    required this.available,
    required this.granted,
    required this.blocked,
    this.hasTelephony = true,
    this.whatsappInstalled = false,
    this.listenerConnected = false,
  });

  static const unavailable = PhonePermissions(available: false, granted: {}, blocked: {});

  factory PhonePermissions.fromMap(Map<dynamic, dynamic>? raw) {
    if (raw == null) return unavailable;
    final blocked = raw['blocked'];
    return PhonePermissions(
      available: true,
      granted: {for (final key in keys) key: raw[key] == true},
      blocked: blocked is List ? blocked.map((e) => e.toString()).toSet() : <String>{},
      hasTelephony: raw['has_telephony'] != false,
      whatsappInstalled: raw['whatsapp_installed'] == true,
      listenerConnected: raw['notification_listener_connected'] == true,
    );
  }

  bool isOn(String key) => granted[key] == true;

  bool isBlocked(String key) => blocked.contains(key);

  int get onCount => keys.where(isOn).length;
}
