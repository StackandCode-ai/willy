import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:willy_mobile/models/phone_skills.dart';
import 'package:willy_mobile/models/presence.dart';
import 'package:willy_mobile/theme.dart';
import 'package:willy_mobile/widgets/phone_skills.dart';

const _channel = MethodChannel('com.example.willy_mobile/telemetry');

Map<String, Object?> _status({bool sms = false}) => {
      'call_phone': true,
      'send_sms': sms,
      'read_contacts': true,
      'notification_access': false,
      'overlay': false,
      'post_notifications': true,
      'blocked': <String>[],
      'has_telephony': true,
      'whatsapp_installed': true,
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('normalizePhoneResult', () {
    test('turns nested platform maps into a JSON-safe contract result', () {
      final res = normalizePhoneResult(<Object?, Object?>{
        'success': true,
        'message': 'You have 1 recent notification on your phone.',
        'access': true,
        'notifications': [
          <Object?, Object?>{
            'app': 'WhatsApp',
            'package': 'com.whatsapp',
            'title': 'Mom',
            'text': 'Call me',
            'time': 1790000000000,
          },
        ],
      });
      expect(res['success'], isTrue);
      final first = (res['notifications'] as List).single;
      expect(first, isA<Map<String, dynamic>>());
      expect(first['title'], 'Mom');
      expect(jsonDecode(jsonEncode(res))['notifications'][0]['package'], 'com.whatsapp');
    });

    test('keeps needs_tap and extra fields', () {
      final res = normalizePhoneResult(<Object?, Object?>{
        'success': true,
        'needs_tap': true,
        'message': 'WhatsApp is open with your message to Mom — tap send.',
        'name': 'Mom',
        'number': '+61412345678',
      });
      expect(res['needs_tap'], isTrue);
      expect(res['number'], '+61412345678');
    });

    test('failures always carry a user-facing error', () {
      expect(normalizePhoneResult(null), {'success': false, 'error': "The phone didn't answer."});
      final missing = normalizePhoneResult({'message': 'hm'});
      expect(missing['success'], isFalse);
      expect(missing['error'], isNotEmpty);
      expect(
        normalizePhoneResult({'success': false, 'error': "No contact named 'Bob' on your phone."})['error'],
        "No contact named 'Bob' on your phone.",
      );
    });
  });

  test('PhonePermissions.fromMap reads flags and blocked permissions', () {
    final p = PhonePermissions.fromMap({..._status(), 'blocked': ['send_sms']});
    expect(p.available, isTrue);
    expect(p.isOn(PhonePermissions.callPhone), isTrue);
    expect(p.isOn(PhonePermissions.sendSms), isFalse);
    expect(p.isBlocked(PhonePermissions.sendSms), isTrue);
    expect(p.onCount, 3);
    expect(p.whatsappInstalled, isTrue);
    expect(PhonePermissions.fromMap(null).available, isFalse);
    expect(PhonePermissions.fromMap({}).onCount, 0);
    expect(PhonePermissions.fromMap({}).hasTelephony, isTrue);
  });

  group('PresenceAlert', () {
    test('prefers the hub title and message', () {
      final a = PresenceAlert.fromEvent({
        'type': 'presence_alert',
        'device_id': 'pc_harig',
        'device_name': 'HariG',
        'device_type': 'pc',
        'event': 'offline',
        'reason': 'sleep',
        'reason_text': 'The PC went to sleep.',
        'title': 'HariG is asleep',
        'message': 'HariG went to sleep at 11:02 PM.',
        'offline_for_sec': null,
        'reply': null,
        'timestamp': 1790000000,
      });
      expect(a.isOnline, isFalse);
      expect(a.reason, 'sleep');
      expect(a.headline, 'HariG is asleep');
      expect(a.detail, 'HariG went to sleep at 11:02 PM.');
    });

    test('builds a sentence when the hub sends no title', () {
      final back = PresenceAlert.fromEvent({
        'device_id': 'pc_harig',
        'device_name': 'HariG',
        'event': 'online',
        'reason': 'restarted',
        'reason_text': 'Back after a restart.',
        'offline_for_sec': 95,
      });
      expect(back.isOnline, isTrue);
      expect(back.headline, 'HariG is back online');
      expect(back.detail, 'Back after a restart.');
      expect(back.offlineForSec, 95.0);
      expect(PresenceAlert.fromEvent({'device_name': 'Laptop'}).headline, 'Laptop went offline');
    });
  });

  test('RecentAlerts matches the same news only within its window', () {
    var now = DateTime(2026, 9, 28, 12);
    final recent = RecentAlerts(window: const Duration(seconds: 20), clock: () => now);
    recent.add('HariG is asleep', 'HariG went to sleep at 11:02 PM.');
    expect(recent.matches('harig is  asleep', null), isTrue);
    expect(recent.matches('Willy', 'HariG went to sleep at 11:02 PM.'), isTrue);
    expect(recent.matches('Reminder', 'Stand up'), isFalse);
    expect(recent.matches(null, ''), isFalse);
    now = now.add(const Duration(seconds: 30));
    expect(recent.matches('HariG is asleep', null), isFalse);
  });

  group('PhoneSkillsPanel', () {
    late List<MethodCall> calls;

    setUp(() {
      calls = [];
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(_channel, (call) async {
        calls.add(call);
        switch (call.method) {
          case 'getPhonePermissions':
            return _status();
          case 'requestPhonePermissions':
            return _status(sms: true);
          case 'openNotificationAccessSettings':
          case 'openOverlaySettings':
            return true;
        }
        return null;
      });
    });

    tearDown(() {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(_channel, null);
    });

    Finder buttonInRow(String title, String label) => find.descendant(
          of: find.ancestor(of: find.text(title), matching: find.byType(Row)).first,
          matching: find.text(label),
        );

    testWidgets('lists every skill, grants runtime permissions and opens special access', (tester) async {
      tester.view.physicalSize = const Size(1200, 2400);
      tester.view.devicePixelRatio = 2.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(MaterialApp(
        theme: buildWillyTheme(),
        home: const Scaffold(body: SingleChildScrollView(child: PhoneSkillsPanel())),
      ));
      await tester.pumpAndSettle();

      for (final title in ['Calls', 'SMS', 'Contacts', 'Notification access', 'Background actions', 'Notifications']) {
        expect(find.text(title), findsOneWidget);
      }
      expect(find.text('3/6 ON'), findsOneWidget);
      expect(find.text('“Call Mom”'), findsOneWidget);
      expect(find.text('“Read my notifications”'), findsOneWidget);

      // SMS is off: "Allow" asks Android for SEND_SMS, then the row turns on.
      await tester.tap(buttonInRow('SMS', 'Allow'));
      await tester.pumpAndSettle();
      final request = calls.lastWhere((c) => c.method == 'requestPhonePermissions');
      expect(request.arguments, {
        'permissions': ['send_sms'],
      });
      expect(find.text('4/6 ON'), findsOneWidget);

      // Special accesses open Android's settings screens.
      await tester.tap(buttonInRow('Notification access', 'Settings'));
      await tester.pumpAndSettle();
      expect(calls.any((c) => c.method == 'openNotificationAccessSettings'), isTrue);
      await tester.tap(buttonInRow('Background actions', 'Settings'));
      await tester.pumpAndSettle();
      expect(calls.any((c) => c.method == 'openOverlaySettings'), isTrue);
    });

    testWidgets('explains when the native side is missing', (tester) async {
      // A null reply is how a missing native handler looks (MissingPluginException).
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(_channel, (call) async => null);
      await tester.pumpWidget(MaterialApp(
        theme: buildWillyTheme(),
        home: const Scaffold(body: SingleChildScrollView(child: PhoneSkillsPanel())),
      ));
      await tester.pumpAndSettle();
      expect(find.text('Phone skills need the Willy Android app.'), findsOneWidget);
    });
  });
}
