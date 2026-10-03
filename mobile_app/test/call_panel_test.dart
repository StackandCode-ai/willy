import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:willy_mobile/models/call_panel.dart';
import 'package:willy_mobile/models/device.dart';
import 'package:willy_mobile/screens/call_panels/panel_host.dart';
import 'package:willy_mobile/theme.dart';
import 'package:willy_mobile/widgets/call_widgets.dart';
import 'package:willy_mobile/widgets/simple_markdown.dart';

WillyDevice dev(String id, {String type = 'pc', bool online = true}) =>
    WillyDevice.fromJson({'device_id': id, 'device_type': type, 'name': id, 'online': online});

void main() {
  group('CallPanelRequest.fromReply', () {
    test('reads a files panel with a folder', () {
      final r = CallPanelRequest.fromReply({
        'reply': 'Here is your Downloads folder.',
        'ui': {'panel': 'files', 'device_id': 'pc_harig', 'path': r'C:\Users\hari\Downloads', 'title': 'Downloads'},
      })!;
      expect(r.kind, CallPanelKind.files);
      expect(r.deviceId, 'pc_harig');
      expect(r.path, r'C:\Users\hari\Downloads');
      expect(r.title, 'Downloads');
    });

    test('null / empty fields and other panels', () {
      final r = CallPanelRequest.fromReply({
        'ui': {'panel': 'screenshot', 'device_id': null, 'path': '', 'title': 'null'},
      })!;
      expect(r.kind, CallPanelKind.screenshot);
      expect(r.deviceId, isNull);
      expect(r.path, isNull);
      expect(r.title, isNull);
      expect(
          CallPanelRequest.fromReply({
            'ui': {'panel': 'Camera'}
          })!
              .kind,
          CallPanelKind.camera);
    });

    test('no ui, or an unknown panel, opens nothing', () {
      expect(CallPanelRequest.fromReply({'reply': 'hi'}), isNull);
      expect(CallPanelRequest.fromReply({'ui': 'files'}), isNull);
      expect(
          CallPanelRequest.fromReply({
            'ui': {'panel': 'map'}
          }),
          isNull);
      expect(CallPanelRequest.fromReply(null), isNull);
    });

    test('every request gets a new serial', () {
      final a = CallPanelRequest(CallPanelKind.files);
      final b = CallPanelRequest(CallPanelKind.files);
      expect(b.serial, greaterThan(a.serial));
    });
  });

  group('pickPanelPc', () {
    final devices = [dev('phone', type: 'mobile'), dev('pc_off', online: false), dev('pc_a'), dev('pc_b')];

    test('uses the named PC when it is online', () => expect(pickPanelPc(devices, 'pc_b')!.id, 'pc_b'));
    test('named PC offline -> none', () => expect(pickPanelPc(devices, 'pc_off'), isNull));
    test('no id -> first online PC', () => expect(pickPanelPc(devices, null)!.id, 'pc_a'));
    test('unknown id -> first online PC', () => expect(pickPanelPc(devices, 'gone')!.id, 'pc_a'));
    test('no PC online -> none', () {
      expect(pickPanelPc([dev('phone', type: 'mobile'), dev('pc_off', online: false)], null), isNull);
      expect(pickPanelPc(const [], null), isNull);
    });
  });

  group('list_dir parsing', () {
    test('folders first, names sorted, millisecond dates fixed', () {
      final dir = DirListing.fromResult({
        'success': true,
        'path': r'D:\Work',
        'parent': r'D:\',
        'entries': [
          {'name': 'b.txt', 'path': r'D:\Work\b.txt', 'folder': false, 'size': 12, 'modified': 1790000000},
          {'name': 'Zeta', 'path': r'D:\Work\Zeta', 'folder': true, 'size': null, 'modified': 1790000000000},
          {'name': 'A.pdf', 'path': r'D:\Work\A.pdf', 'folder': false, 'size': 2048, 'modified': 1790000000.5},
          {'name': 'alpha', 'path': r'D:\Work\alpha', 'folder': true},
          {'name': 'broken'},
        ],
        'drives': [
          {'name': r'C:\', 'label': 'OS', 'free_gb': 45.2, 'total_gb': 475},
        ],
      });
      expect(dir.path, r'D:\Work');
      expect(dir.parent, r'D:\');
      expect(dir.isRoot, isFalse);
      expect(dir.entries.map((e) => e.name), ['alpha', 'Zeta', 'A.pdf', 'b.txt']);
      expect(dir.entries[1].modified, closeTo(1790000000, 0.001));
      expect(dir.entries[2].extension, 'pdf');
      expect(dir.drives.single.letter, 'C:');
      expect(dir.drives.single.freeGb, 45.2);
    });

    test('top level has an empty path and no parent', () {
      final dir = DirListing.fromResult({'success': true, 'path': '', 'parent': null, 'entries': []});
      expect(dir.isRoot, isTrue);
      expect(dir.parent, isNull);
    });
  });

  group('paths', () {
    test('joinPcPath', () {
      expect(joinPcPath(r'D:\Work', 'New'), r'D:\Work\New');
      expect(joinPcPath(r'D:\', 'New'), r'D:\New');
      expect(joinPcPath('/home/hari', 'x'), '/home/hari/x');
    });

    test('baseName', () {
      expect(baseName(r'D:\Work\report.docx'), 'report.docx');
      expect(baseName(r'D:\Work\'), 'Work');
      expect(baseName(r'C:\'), r'C:\');
    });

    test('breadcrumbs', () {
      expect(breadcrumbsFor(''), isEmpty);
      expect(breadcrumbsFor(r'D:\'), [const PathCrumb('D:', r'D:\')]);
      expect(breadcrumbsFor(r'D:\Work\Sub'), [
        const PathCrumb('D:', r'D:\'),
        const PathCrumb('Work', r'D:\Work'),
        const PathCrumb('Sub', r'D:\Work\Sub'),
      ]);
      expect(breadcrumbsFor(r'\\nas\share\photos'), [
        const PathCrumb(r'\\nas\share', r'\\nas\share'),
        const PathCrumb('photos', r'\\nas\share\photos'),
      ]);
    });

    test('valid Windows names', () {
      expect(validPcFileName('  Report 2026.docx '), 'Report 2026.docx');
      expect(validPcFileName('a/b'), isNull);
      expect(validPcFileName('what?'), isNull);
      expect(validPcFileName('..'), isNull);
      expect(validPcFileName('trailing.'), isNull);
      expect(validPcFileName(''), isNull);
    });

    test('file types', () {
      PcFileType t(String name, {bool folder = false}) =>
          fileTypeOf(PcEntry(name: name, path: 'x\\$name', folder: folder));
      expect(t('Photos', folder: true), PcFileType.folder);
      expect(t('IMG_1.JPG'), PcFileType.image);
      expect(t('talk.mp4'), PcFileType.video);
      expect(t('notes.md'), PcFileType.text);
      expect(t('setup.exe'), PcFileType.app);
      expect(t('Makefile'), PcFileType.other);
    });
  });

  group('markdown', () {
    test('blocks', () {
      final blocks = parseMarkdownBlocks(
          '# Title\nFirst line\nsecond line\n\n- one\n  - nested\n2. two\n```\ncode here\n```\nend');
      expect(blocks.map((b) => b.type), [
        MdBlockType.heading,
        MdBlockType.paragraph,
        MdBlockType.bullet,
        MdBlockType.bullet,
        MdBlockType.numbered,
        MdBlockType.code,
        MdBlockType.paragraph,
      ]);
      expect(blocks[1].text, 'First line\nsecond line');
      expect(blocks[3].level, 1);
      expect(blocks[4].marker, '2.');
      expect(blocks[5].text, 'code here');
    });

    test('inline styles, and snake_case stays plain', () {
      final spans = parseInlineMarkdown('Run **fast** with `willy --go` and *care* in my_file_name [docs](http://x.y)');
      expect(spans.where((s) => s.bold).single.text, 'fast');
      expect(spans.where((s) => s.code).single.text, 'willy --go');
      expect(spans.where((s) => s.italic).single.text, 'care');
      expect(spans.where((s) => s.link != null).single.text, 'docs');
      expect(spans.map((s) => s.text).join(), 'Run fast with willy --go and care in my_file_name docs');
    });

    test('plain text for captions', () {
      expect(markdownToPlain('**Battery** is at 80%\n- charging\n- `fast`'), 'Battery is at 80%\n• charging\n• fast');
    });
  });

  testWidgets('SimpleMarkdown and call controls render', (tester) async {
    var taps = 0;
    await tester.pumpWidget(MaterialApp(
      theme: buildWillyTheme(),
      home: Scaffold(
        body: Column(
          children: [
            const SimpleMarkdown('## Today\n- **Standup** at 10\n- Ship `v2`'),
            CallControlButton(icon: Icons.mic_off_rounded, label: 'Mute', active: true, onPressed: () => taps++),
          ],
        ),
      ),
    ));
    expect(find.text('Today'), findsOneWidget);
    expect(find.text('•'), findsNWidgets(2));
    expect(find.textContaining('Standup', findRichText: true), findsOneWidget);
    await tester.tap(find.byIcon(Icons.mic_off_rounded));
    expect(taps, 1);
  });

  testWidgets('a files panel with no PC online says so', (tester) async {
    final request = ValueNotifier<CallPanelRequest?>(CallPanelRequest(CallPanelKind.files));
    final call = ValueNotifier(const CallLine('Listening', WillyColors.green));
    await tester.pumpWidget(MaterialApp(
      theme: buildWillyTheme(),
      home: Builder(
        builder: (context) => Scaffold(
          body: Center(
            child: TextButton(
              onPressed: () => Navigator.of(context).push(buildCallPanelRoute(context, request: request, call: call)),
              child: const Text('open'),
            ),
          ),
        ),
      ),
    ));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.text('Your PC is offline'), findsOneWidget);
    expect(find.text('Listening'), findsOneWidget);
    await tester.tap(find.byTooltip('Close'));
    await tester.pumpAndSettle();
    expect(find.text('Your PC is offline'), findsNothing);
  });
}
