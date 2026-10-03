import 'device.dart';

/// A hub `presence_alert`: a device went offline (sleep, shutdown, app closed, lost
/// connection…) or came back online.
class PresenceAlert {
  final String deviceId;
  final String deviceName;
  final String deviceType;
  final String event; // 'online' | 'offline'
  final String? reason;
  final String? reasonText;
  final String? title;
  final String? message;
  final String? reply;
  final double? offlineForSec;

  const PresenceAlert({
    required this.deviceId,
    required this.deviceName,
    required this.deviceType,
    required this.event,
    this.reason,
    this.reasonText,
    this.title,
    this.message,
    this.reply,
    this.offlineForSec,
  });

  factory PresenceAlert.fromEvent(Map<String, dynamic> e) => PresenceAlert(
        deviceId: asString(e['device_id']) ?? '',
        deviceName: asString(e['device_name']) ?? 'A device',
        deviceType: asString(e['device_type']) ?? 'pc',
        event: asString(e['event']) ?? 'offline',
        reason: asString(e['reason']),
        reasonText: asString(e['reason_text']),
        title: asString(e['title']),
        message: asString(e['message']),
        reply: asString(e['reply']),
        offlineForSec: asDouble(e['offline_for_sec']),
      );

  bool get isOnline => event == 'online';

  /// The hub's title, else a sentence built from the event.
  String get headline {
    final t = title?.trim() ?? '';
    if (t.isNotEmpty) return t;
    return isOnline ? '$deviceName is back online' : '$deviceName went offline';
  }

  /// Second line: the hub's message, else the reason ("Went to sleep").
  String get detail {
    for (final s in [message, reasonText, reply]) {
      final v = s?.trim() ?? '';
      if (v.isNotEmpty && v != headline) return v;
    }
    return '';
  }
}

/// Remembers alert titles/messages for a short window, so the same news arriving by two
/// routes (a hub event and a `notify` action) is shown in the app only once.
class RecentAlerts {
  RecentAlerts({this.window = const Duration(seconds: 20), DateTime Function()? clock})
      : _clock = clock ?? DateTime.now;

  final Duration window;
  final DateTime Function() _clock;
  final List<_SeenAlert> _seen = [];

  static String _norm(String? s) => (s ?? '').trim().toLowerCase().replaceAll(RegExp(r'\s+'), ' ');

  void add(String? title, String? message) {
    _prune();
    _seen.add(_SeenAlert(_norm(title), _norm(message), _clock()));
  }

  /// True when a remembered alert has the same (non-empty) title or message.
  bool matches(String? title, String? message) {
    _prune();
    final t = _norm(title);
    final m = _norm(message);
    return _seen.any((s) => (t.isNotEmpty && s.title == t) || (m.isNotEmpty && s.message == m));
  }

  void _prune() {
    final cutoff = _clock().subtract(window);
    _seen.removeWhere((s) => s.at.isBefore(cutoff));
  }
}

class _SeenAlert {
  final String title;
  final String message;
  final DateTime at;

  const _SeenAlert(this.title, this.message, this.at);
}
