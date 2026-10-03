import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:willy_mobile/models/call_panel.dart';
import 'package:willy_mobile/models/device.dart';
import 'package:willy_mobile/models/server.dart';
import 'package:willy_mobile/screens/server/server_card.dart';
import 'package:willy_mobile/screens/server/server_screen.dart';
import 'package:willy_mobile/screens/server/server_widgets.dart';
import 'package:willy_mobile/theme.dart';

WillyDevice dev(String id, {String type = 'pc', bool online = true}) =>
    WillyDevice.fromJson({'device_id': id, 'device_type': type, 'name': id, 'online': online});

final serverJson = {
  'device_id': 'server_willy',
  'device_type': 'server',
  'name': 'Willy Server',
  'hostname': 'ip-172-31-5-9',
  'platform': 'Linux (Ubuntu 24.04)',
  'online': true,
  'telemetry': {
    'cpu_pct': 37.5,
    'ram_pct': 61.2,
    'ram_used_gb': 2.45,
    'ram_total_gb': 4.0,
    'swap_pct': 3.0,
    'disk_pct': 71.0,
    'disk_free_gb': 22.4,
    'disk_total_gb': 77.4,
    'load_1': 0.52,
    'load_5': 0.4,
    'load_15': 0.31,
    'cores': 2,
    'uptime_hours': 250.5,
    'process_count': 143,
    'net_sent_mb': 5120,
    'net_recv_mb': 830,
    'hostname': 'vps-1',
    'battery_pct': null,
  },
};

const status = {
  'success': true,
  'summary': 'Your server is having trouble.',
  'problems': [
    {'key': 'site:old.example.com', 'message': 'old.example.com is down (HTTP 502).'},
    {'key': 'cert:shop.example.com', 'message': 'The certificate for shop.example.com expires in 9 days.'},
    {'message': 'no key'},
  ],
  'ignored': ['site:test.example.com'],
  'snapshot': {
    'checked_at': 1700000000.0,
    'sites_checked_at': 1699999000.0,
    'apps': [
      {'name': 'willy-server', 'status': 'online', 'restarts': 3, 'memory_mb': 180},
      {'name': 'worker', 'status': 'errored', 'restarts': 15, 'memory_mb': 0},
    ],
    'services': {'nginx': 'active', 'docker': 'failed'},
    'containers': [
      {'name': 'redis', 'state': 'running', 'status': 'Up 3 days'},
      {'name': 'old', 'state': 'exited'},
    ],
    'sites': [
      {'domain': 'shop.example.com', 'up': true, 'status': 200, 'ms': 120, 'cert_days': 9},
      {'domain': 'old.example.com', 'up': false, 'status': 502, 'ms': 80, 'cert_days': 40},
      {'domain': 'test.example.com', 'up': false, 'status': null, 'error': 'URLError: timed out', 'cert_days': null},
      {'domain': 'blog.example.com', 'up': true, 'status': 200, 'ms': 90, 'cert_days': 60},
    ],
  },
};

void main() {
  group('server device', () {
    test('is its own type, never a PC or a phone', () {
      final s = WillyDevice.fromJson(serverJson);
      expect(s.isServer, isTrue);
      expect(s.isPc, isFalse);
      expect(s.isMobile, isFalse);
      expect(s.typeRank, greaterThan(dev('pc').typeRank));
      expect(s.typeRank, lessThan(dev('p', type: 'mobile').typeRank));
    });

    test('reads Linux telemetry', () {
      final s = WillyDevice.fromJson(serverJson);
      expect(s.load1, 0.52);
      expect(s.load15, 0.31);
      expect(s.cores, 2);
      expect(s.swapPct, 3.0);
      expect(s.netSentMb, 5120);
      expect(s.machineName, 'vps-1');
      expect(s.batteryPct, isNull);
      expect(formatLoad(s.load1, s.load5, s.load15), '0.52 · 0.40 · 0.31');
      expect(formatLoad(null, null, null), '—');
      expect(formatMb(830), '830 MB');
      expect(formatMb(5120), '5.0 GB');
    });

    test('is not picked as "the PC" for panels', () {
      final devices = [dev('server_willy', type: 'server'), dev('pc_a')];
      expect(pickPanelPc(devices, null)!.id, 'pc_a');
      expect(pickPanelPc(devices, 'server_willy')!.id, 'pc_a', reason: 'a screenshot panel needs a PC');
      expect(pickPanelPc(devices, 'server_willy', allowServer: true)!.id, 'server_willy');
      expect(pickPanelPc([dev('server_willy', type: 'server')], null), isNull);
      expect(pickPanelPc([dev('server_willy', type: 'server', online: false)], 'server_willy', allowServer: true),
          isNull);
    });
  });

  group('server files', () {
    test('mount points keep their path and the longest one wins', () {
      const root = PcDrive(name: '/', label: '/dev/xvda1');
      const data = PcDrive(name: '/mnt/data', label: '/dev/xvdb');
      expect(root.letter, '/');
      expect(root.isMount, isTrue);
      expect(data.letter, '/mnt/data');
      expect(driveOf('/home/ubuntu', [root, data]), same(root));
      expect(driveOf('/mnt/data/backups', [root, data]), same(data));
      expect(driveOf('/mnt/data', [root, data]), same(data));
      expect(driveOf('/mnt/database', [root, data]), same(root), reason: 'not inside /mnt/data');
      expect(driveOf('', [root, data]), isNull);
    });

    test('Windows drives still match', () {
      const c = PcDrive(name: r'C:\');
      const d = PcDrive(name: r'D:\');
      expect(c.letter, 'C:');
      expect(c.isMount, isFalse);
      expect(driveOf(r'd:\Work', [c, d]), same(d));
      expect(driveOf(r'C:\', [c, d]), same(c));
    });

    test('top-level server listing', () {
      final dir = DirListing.fromResult({
        'success': true,
        'path': '',
        'parent': null,
        'entries': [
          {'name': '/var/www', 'path': '/var/www', 'folder': true},
          {'name': '/home/ubuntu', 'path': '/home/ubuntu', 'folder': true},
        ],
        'drives': [
          {'name': '/', 'label': '/dev/root', 'free_gb': 22.4, 'total_gb': 77.4},
        ],
      });
      expect(dir.isRoot, isTrue);
      expect(dir.entries.map((e) => e.name), ['/home/ubuntu', '/var/www']);
      expect(dir.drives.single.letter, '/');
      expect(breadcrumbsFor('/var/www/site'), [
        const PathCrumb('/', '/'),
        const PathCrumb('var', '/var'),
        const PathCrumb('www', '/var/www'),
        const PathCrumb('site', '/var/www/site'),
      ]);
    });
  });

  group('server health', () {
    test('parses the hub status', () {
      final h = ServerHealth.fromJson(Map<String, dynamic>.from(status));
      expect(h.summary, 'Your server is having trouble.');
      expect(h.healthy, isFalse);
      expect(h.problems.map((p) => p.key), ['site:old.example.com', 'cert:shop.example.com']);
      expect(h.ignored, ['site:test.example.com']);
      expect(h.apps.map((a) => '${a.name}:${a.health.name}'), ['willy-server:good', 'worker:bad']);
      expect(h.apps.first.restarts, 3);
      expect(h.services.map((s) => '${s.name}:${s.state}'), ['docker:failed', 'nginx:active']);
      expect(h.services.every((s) => s.kind == ServerUnitKind.service), isTrue);
      expect(h.containers.map((c) => '${c.name}:${c.health.name}'), ['redis:good', 'old:idle']);
      expect(h.checkedAt, 1700000000.0);
    });

    test('down sites first, then the soonest certificate', () {
      final h = ServerHealth.fromJson(Map<String, dynamic>.from(status));
      expect(h.sites.map((s) => s.domain),
          ['old.example.com', 'test.example.com', 'shop.example.com', 'blog.example.com']);
      expect(h.sites[1].error, 'URLError: timed out');
    });

    test('empty or partial answers', () {
      final h = ServerHealth.fromJson({'success': true});
      expect(h.healthy, isTrue);
      expect(h.sites, isEmpty);
      expect(ServerUnit.apps('junk'), isEmpty);
      expect(ServerUnit.services([
        {'name': 'cron', 'state': 'active'}
      ]).single.name, 'cron');
    });

    test('unit and certificate states', () {
      expect(unitHealth('online'), UnitHealth.good);
      expect(unitHealth('active'), UnitHealth.good);
      expect(unitHealth('running'), UnitHealth.good);
      expect(unitHealth('errored'), UnitHealth.bad);
      expect(unitHealth('failed'), UnitHealth.bad);
      expect(unitHealth('restarting'), UnitHealth.bad);
      expect(unitHealth('launching'), UnitHealth.warn);
      expect(unitHealth('stopped'), UnitHealth.idle);
      expect(unitHealth('exited'), UnitHealth.idle);
      expect(certHealth(5), UnitHealth.bad);
      expect(certHealth(14), UnitHealth.warn);
      expect(certHealth(60), UnitHealth.good);
      expect(certHealth(null), UnitHealth.idle);
      expect(certText(9), 'cert 9d');
      expect(certText(-2), 'cert expired');
      expect(certText(null), 'no cert info');
      expect(problemLabel('site:a.com'), 'Website a.com');
      expect(problemLabel('disk'), 'Disk almost full');
      expect(problemLabel('weird'), 'weird');
    });

    test('processes', () {
      final list = ServerProcess.listFrom([
        {'pid': 812, 'name': 'node', 'user': 'ubuntu', 'cpu': 2.5, 'memory_mb': 180.4, 'command': 'node app.js'},
        'junk',
      ]);
      expect(list.single.name, 'node');
      expect(list.single.memoryMb, 180.4);
      expect(list.single.user, 'ubuntu');
    });

    test('command history: newest first, no repeats, capped', () {
      var h = <String>[];
      h = pushCommandHistory(h, 'df -h');
      h = pushCommandHistory(h, 'uptime');
      h = pushCommandHistory(h, ' df -h ');
      h = pushCommandHistory(h, '   ');
      expect(h, ['df -h', 'uptime']);
      for (var i = 0; i < 40; i++) {
        h = pushCommandHistory(h, 'echo $i', max: 5);
      }
      expect(h.length, 5);
      expect(h.first, 'echo 39');
    });
  });

  testWidgets('server card shows live stats and its shortcuts', (tester) async {
    await tester.binding.setSurfaceSize(const Size(420, 1000));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(MaterialApp(
      theme: buildWillyTheme(),
      home: Scaffold(
        body: SingleChildScrollView(child: ServerCard(device: WillyDevice.fromJson(serverJson))),
      ),
    ));
    expect(find.text('Willy Server'), findsOneWidget);
    expect(find.text('ONLINE'), findsOneWidget);
    expect(find.text('38%'), findsOneWidget); // CPU 37.5 rounds up
    expect(find.text('2.5 / 4.0 GB'), findsOneWidget);
    expect(find.text('22 GB free of 77 GB'), findsOneWidget);
    expect(find.text('0.52 · 0.40 · 0.31'), findsOneWidget);
    expect(find.text('143'), findsOneWidget);
    for (final t in ['Overview', 'Files', 'Terminal']) {
      expect(find.text(t), findsOneWidget);
    }
    expect(find.text('Processes'), findsNWidgets(2)); // the stat chip and the shortcut
    expect(find.byIcon(Icons.dns_rounded), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('offline server card says so', (tester) async {
    final json = Map<String, dynamic>.from(serverJson)..['online'] = false;
    await tester.pumpWidget(MaterialApp(
      theme: buildWillyTheme(),
      home: Scaffold(body: SingleChildScrollView(child: ServerCard(device: WillyDevice.fromJson(json)))),
    ));
    expect(find.text('OFFLINE'), findsOneWidget);
    expect(find.textContaining('server agent not connected'), findsOneWidget);
  });

  testWidgets('state chips and stat bars render', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildWillyTheme(),
      home: Scaffold(
        body: Column(children: [
          StateChip.of('errored'),
          const StatBar(label: 'Disk', pct: 91, caption: '5 GB free'),
          const StatBar(label: 'Swap', pct: null),
        ]),
      ),
    ));
    expect(find.text('errored'), findsOneWidget);
    expect(find.text('91%'), findsOneWidget);
    expect(find.text('—'), findsOneWidget);
  });

  testWidgets('server screen lays out every tab on a phone', (tester) async {
    await tester.binding.setSurfaceSize(const Size(360, 720));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final json = Map<String, dynamic>.from(serverJson)..['online'] = false; // nothing is sent to a device
    await tester.pumpWidget(MaterialApp(
      theme: buildWillyTheme(),
      home: ServerScreen(device: WillyDevice.fromJson(json)),
    ));
    await tester.pump(const Duration(milliseconds: 200));
    expect(find.text('Resources'), findsOneWidget);
    expect(find.textContaining("agent isn't connected"), findsOneWidget);
    for (final tab in ['Terminal', 'Processes', 'Files', 'Overview']) {
      await tester.tap(find.widgetWithText(Tab, tab));
      await tester.pump(const Duration(milliseconds: 400));
      expect(tester.takeException(), isNull, reason: tab);
    }
    await tester.tap(find.widgetWithText(Tab, 'Terminal'));
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.text('Run a command on the server'), findsOneWidget);
    expect(find.text('df -h'), findsOneWidget);
    // Let the (failing, offline) hub requests settle so no timers are left.
    await tester.pump(const Duration(seconds: 25));
  });
}
