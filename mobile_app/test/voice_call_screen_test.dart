import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:willy_mobile/screens/voice_call_screen.dart';
import 'package:willy_mobile/theme.dart';

/// Plugins (audio player, recorder, path provider, the app's native channel) answer
/// "nothing" in tests; the framework's own channels keep their normal behaviour.
void _quietPlugins() {
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.allMessagesHandler = (channel, handler, message) {
    if (channel.startsWith('flutter/')) return handler?.call(message);
    const codec = StandardMethodCodec();
    Object? result;
    try {
      final call = codec.decodeMethodCall(message);
      if (call.method == 'hasPermission') result = true; // the recorder may use the mic
      if (call.method == 'getTemporaryDirectory') result = '/tmp';
    } catch (_) {}
    return Future.value(codec.encodeSuccessEnvelope(result));
  };
}

Future<void> _pumpCall(WidgetTester tester, Size size) async {
  tester.view.physicalSize = size * 3;
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  var ended = 0;
  await tester.pumpWidget(MaterialApp(
    theme: buildWillyTheme(),
    home: VoiceCallScreen(onEndCall: () => ended++),
  ));
  await tester.pump(const Duration(milliseconds: 1500)); // past "Connecting"
}

void main() {
  setUp(_quietPlugins);
  tearDown(() => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.allMessagesHandler = null);

  // First: the recorder plugin keeps state between tests.
  testWidgets('mute, panels picker and the offline files panel', (tester) async {
    await _pumpCall(tester, const Size(393, 852));
    // Auto-listen opens the line by itself once connected
    // (the recorder touches the real file system, so give real time a moment too).
    for (var i = 0; i < 10 && find.text('Listening').evaluate().isEmpty; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 100)));
      await tester.pump(const Duration(milliseconds: 200));
    }
    expect(find.text('Listening'), findsOneWidget);

    await tester.tap(find.text('Mute'));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('Unmute'), findsOneWidget);
    expect(find.text('Muted'), findsOneWidget);

    await tester.tap(find.text('Panels'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));
    expect(find.text('Files on your PC'), findsOneWidget);
    await tester.tap(find.text('Files on your PC'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 800));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 800));
    expect(find.text('Your PC is offline'), findsOneWidget);

    await tester.tap(find.byTooltip('Close'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 800));
    expect(find.text('Your PC is offline'), findsNothing);

    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 1));
  });

  for (final size in const [Size(360, 640), Size(393, 852), Size(412, 915)]) {
    testWidgets('call screen lays out at ${size.width.toInt()}x${size.height.toInt()}', (tester) async {
      await _pumpCall(tester, size);
      expect(find.text('Willy'), findsOneWidget);
      expect(find.text('Mute'), findsOneWidget);
      expect(find.text('Panels'), findsOneWidget);
      expect(find.text('Transcript'), findsWidgets);
      expect(find.byTooltip('New conversation'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
      await tester.pump(const Duration(seconds: 1));
    });
  }
}
