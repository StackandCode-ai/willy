import 'package:flutter_test/flutter_test.dart';
import 'package:willy_mobile/models/push.dart';

void main() {
  group('pushKindOf', () {
    test('parses every known kind', () {
      expect(pushKindOf({'kind': 'reminder'}), PushKind.reminder);
      expect(pushKindOf({'kind': 'alarm'}), PushKind.alarm);
      expect(pushKindOf({'kind': 'alert'}), PushKind.alert);
      expect(pushKindOf({'kind': 'file'}), PushKind.file);
      expect(pushKindOf({'kind': 'test'}), PushKind.test);
      expect(pushKindOf({'kind': 'wake', 'at': '1700000000'}), PushKind.wake);
    });

    test('is lenient about case and whitespace', () {
      expect(pushKindOf({'kind': ' Wake '}), PushKind.wake);
    });

    test('missing or unknown kind is unknown', () {
      expect(pushKindOf({}), PushKind.unknown);
      expect(pushKindOf({'kind': null}), PushKind.unknown);
      expect(pushKindOf({'kind': 'unknown'}), PushKind.unknown);
      expect(pushKindOf({'kind': 'something_new'}), PushKind.unknown);
    });
  });

  group('push token messages', () {
    test('socket message shape', () {
      expect(pushTokenMessage('tok'), {'type': 'push_token', 'token': 'tok', 'platform': 'android'});
    });

    test('REST register body shape', () {
      expect(pushRegisterBody('mobile_abc', 'tok'), {'device_id': 'mobile_abc', 'token': 'tok', 'platform': 'android'});
    });
  });

  group('alertIdOf', () {
    test('push data id', () => expect(alertIdOf({'kind': 'reminder', 'id': 'r1'}), 'r1'));
    test('hub reminder_due event', () => expect(alertIdOf({'type': 'reminder_due', 'reminder': {'id': 42}}), '42'));
    test('hub alarm event', () => expect(alertIdOf({'alarm': {'id': 'a7'}}), 'a7'));
    test('empty id counts as none', () => expect(alertIdOf({'id': '', 'reminder': {'id': ''}}), isNull));
    test('no id', () => expect(alertIdOf({'kind': 'test'}), isNull));
  });

  group('RecentIds', () {
    test('same id is shown once', () {
      final ids = RecentIds();
      expect(ids.add('r1'), isTrue);
      expect(ids.add('r1'), isFalse);
      expect(ids.contains('r1'), isTrue);
      expect(ids.add('r2'), isTrue);
    });

    test('alerts without an id are always shown', () {
      final ids = RecentIds();
      expect(ids.add(null), isTrue);
      expect(ids.add(null), isTrue);
      expect(ids.add(''), isTrue);
      expect(ids.length, 0);
    });

    test('forgets ids after the window', () {
      var now = DateTime(2026, 10, 2, 9);
      final ids = RecentIds(window: const Duration(minutes: 30), now: () => now);
      expect(ids.add('r1'), isTrue);
      now = now.add(const Duration(minutes: 10));
      expect(ids.add('r1'), isFalse);
      now = now.add(const Duration(minutes: 31));
      expect(ids.add('r1'), isTrue);
    });

    test('keeps only the newest ids', () {
      final ids = RecentIds(capacity: 3);
      for (final id in ['a', 'b', 'c', 'd']) {
        ids.add(id);
      }
      expect(ids.length, 3);
      expect(ids.contains('a'), isFalse);
      expect(ids.contains('d'), isTrue);
    });
  });

  group('tabForPushScreen', () {
    test('maps the hub screens to home tabs', () {
      expect(tabForPushScreen('devices'), 0);
      expect(tabForPushScreen('remote'), 1);
      expect(tabForPushScreen('activity'), 3);
      expect(tabForPushScreen('reminders'), 4);
    });

    test('unknown or missing screen opens nothing special', () {
      expect(tabForPushScreen(null), isNull);
      expect(tabForPushScreen('settings'), isNull);
    });
  });
}
