import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:willy_mobile/models/device.dart';
import 'package:willy_mobile/models/server.dart';
import 'package:willy_mobile/screens/server/server_history_section.dart';
import 'package:willy_mobile/screens/server/server_screen.dart';
import 'package:willy_mobile/screens/server/server_system_tab.dart';
import 'package:willy_mobile/screens/server/server_widgets.dart';
import 'package:willy_mobile/services/api_service.dart';
import 'package:willy_mobile/theme.dart';

WillyDevice server({bool online = true}) => WillyDevice.fromJson({
      'device_id': 'server_willy',
      'device_type': 'server',
      'name': 'Willy Server',
      'platform': 'Linux',
      'online': online,
    });

/// What the fake hub answers for each server action.
final _answers = <String, Map<String, dynamic>>{
  'sys_updates': {
    'success': true,
    'packages': [
      for (var i = 0; i < 40; i++)
        {'name': 'package-with-a-long-name-$i.x86_64', 'version': '1.$i.0-1.amzn2023', 'repo': 'amazonlinux'},
    ],
    'security_count': 2,
    'newer_release': '2023.6.20241010',
    'release_note': 'Version 2023.6.20241010 is available.',
    'reboot_needed': true,
    'kernel': '6.1.109-118.189.amzn2023.x86_64',
    'message': '40 package updates available (2 security). A reboot is needed.',
  },
  'storage': {
    'success': true,
    'mounts': [
      {'mount': '/', 'device': '/dev/nvme0n1p1', 'fs': 'xfs', 'used_gb': 20.5, 'total_gb': 30, 'free_gb': 9.5, 'pct': 68.3},
      {'mount': '/mnt/a-rather-long-mount-point-name/data', 'used_gb': 1, 'total_gb': 100, 'free_gb': 99, 'pct': 1},
    ],
    'biggest': [
      {'path': '/home/ec2-user/.cache/some/really/deep/folder/that/is/long', 'gb': 5.2},
      {'path': '/var/log', 'gb': 0.4},
    ],
    'cleanable': {
      'journal': 1.25,
      'docker': 'Images: 1.2GB (40%); Containers: 0B (0%); Local Volumes: 3GB (0%); Build Cache: 300MB',
      'pm2_logs': 0.2,
      'dnf_cache': 0.0,
      'trash': 0.01,
    },
    'cleanable_labels': {
      'journal': 'System logs (journald)',
      'docker': 'Unused Docker images, stopped containers and build cache',
    },
    'swap': {'used_gb': 0.1, 'total_gb': 2, 'pct': 5},
    'message': 'Disk / is 68% full (9.5 GB free).',
  },
  'network': {
    'success': true,
    'interfaces': [
      {'interface': 'ens5', 'state': 'UP', 'addresses': ['172.31.5.9/20', 'fe80::8ff:abcd:ef01:2345/64']},
      {'interface': 'docker0', 'state': 'DOWN', 'addresses': ['172.17.0.1/16']},
    ],
    'listening': [
      {'proto': 'tcp', 'address': '0.0.0.0', 'port': '443', 'public': true, 'process': 'nginx', 'pid': 1},
      {'proto': 'tcp', 'address': '127.0.0.1', 'port': '6379', 'public': false, 'process': 'redis-server', 'pid': 9},
    ],
    'rate': {'down_kbps': 120.5, 'up_kbps': 1830},
    'established': 14,
    'message': 'Open to the internet: 443/tcp (nginx).',
  },
  'security': {
    'success': true,
    'logged_in': ['ec2-user pts/0        2026-10-02 10:00 (203.0.113.7)'],
    'recent_logins': ['ec2-user pts/0        203.0.113.7      Thu Oct  2 10:00:01 2026   still logged in'],
    'failed_ssh_24h': 340,
    'top_attackers': [
      {'ip': '45.1.2.3', 'attempts': 200},
    ],
    'ssh_password_login': 'yes',
    'root_login': 'prohibit-password',
    'fail2ban': 'not installed',
    'message': '340 failed SSH logins in 24 h.',
  },
  'services_list': {
    'success': true,
    'services': [
      {'name': 'sshd', 'load': 'loaded', 'active': 'active', 'sub': 'running', 'description': 'OpenSSH server daemon', 'boot': 'enabled'},
      {
        'name': 'a-very-long-service-name-for-testing-layout@instance',
        'load': 'loaded',
        'active': 'failed',
        'sub': 'failed',
        'description': 'Something that broke while starting up',
        'boot': 'disabled'
      },
      {'name': 'cups', 'load': 'loaded', 'active': 'inactive', 'sub': 'dead', 'description': 'Printing', 'boot': 'static'},
    ],
    'message': '1 services running; failed: a-very-long-service-name-for-testing-layout@instance.',
  },
  'timers': {
    'success': true,
    'cron': ['*/5 * * * * /home/ec2-user/backup.sh >> /home/ec2-user/backup.log 2>&1'],
    'timers': [
      {
        'timer': 'dnf-makecache.timer',
        'line': 'Thu 2026-10-02 11:00:00 UTC 30min left Thu 2026-10-02 10:00:00 UTC 30min ago dnf-makecache.timer'
      },
    ],
  },
  'journal': {
    'success': true,
    'logs': '2026-10-02T10:00:00+0000 ip-172 sshd[123]: error: kex_exchange_identification: closed by remote host\n'
        '2026-10-02T10:00:01+0000 ip-172 kernel: something happened',
    'message': '2 log lines.',
  },
};

MockClient _fakeHub(List<String> calls) => MockClient((req) async {
      final path = req.url.path;
      if (path == '/api/v1/server/history') {
        final pts = [
          for (var i = 0; i < 120; i++)
            {
              't': 1700000000 + i * 60,
              'cpu': i == 50 ? null : 10 + (i % 7) * 5,
              'ram': 55.0,
              'disk': 68.0,
              'swap': 5.0,
              'load': 0.4,
              'rx_kbps': i == 0 ? null : 100.0 + i,
              'tx_kbps': i == 0 ? null : 2000.0,
            }
        ];
        return http.Response(jsonEncode({'success': true, 'hours': 1, 'points': pts}), 200);
      }
      if (path.endsWith('/action')) {
        final body = jsonDecode(req.body) as Map<String, dynamic>;
        final action = body['action'] as String;
        calls.add(action);
        return http.Response(jsonEncode(_answers[action] ?? {'success': false, 'error': 'not faked'}), 200);
      }
      return http.Response('{}', 404);
    });

void main() {
  group('history', () {
    test('parses points oldest first and keeps gaps', () {
      final h = ServerHistory.fromJson({
        'hours': 6,
        'points': [
          {'t': 200.0, 'cpu': 50, 'ram': 60.5, 'disk': 70, 'swap': null, 'load': 0.4, 'rx_kbps': 12.5, 'tx_kbps': 3},
          {'t': 100.0, 'cpu': 10, 'ram': null, 'disk': 70, 'swap': 1, 'load': 0.2, 'rx_kbps': null, 'tx_kbps': null},
          {'cpu': 99}, // no time: dropped
          'junk',
        ],
      });
      expect(h.hours, 6);
      expect(h.times, [100.0, 200.0]);
      expect(h.series((p) => p.ram), [null, 60.5]);
      expect(h.series((p) => p.rxKbps), [null, 12.5]);
      final s = seriesStats(h.series((p) => p.cpu));
      expect(s.avg, 30);
      expect(s.max, 50);
      expect(s.last, 50);
      expect(seriesStats([null, null]).avg, isNull);
      expect(ServerHistory.fromJson({}).points, isEmpty);
    });

    test('axis tops round up to tidy numbers', () {
      expect(niceCeil(0.3), 1);
      expect(niceCeil(1.4), 2);
      expect(niceCeil(2.2), 2.5);
      expect(niceCeil(430), 500);
      expect(niceCeil(1001), 2000);
    });

    test('rates and sizes read well', () {
      expect(formatKbit(null), '—');
      expect(formatKbit(0.2), '0 kbit/s');
      expect(formatKbit(820), '820 kbit/s');
      expect(formatKbit(1500), '1.5 Mbit/s');
      expect(formatKbit(25000), '25 Mbit/s');
      expect(formatGb(null), '—');
      expect(formatGb(0), '0 GB');
      expect(formatGb(0.5), '512 MB');
      expect(formatGb(12.34), '12.3 GB');
      expect(formatGb(250.4), '250 GB');
    });
  });

  group('admin answers', () {
    test('updates', () {
      final u = ServerUpdates.fromJson({
        'packages': [
          {'name': 'openssl.x86_64', 'version': '3.0.8-1', 'repo': 'amazonlinux'},
          {'version': 'no name'},
        ],
        'security_count': 3,
        'newer_release': '2023.6.20241010',
        'release_note': 'A newer release is available.',
        'reboot_needed': true,
        'kernel': '6.1.0',
        'message': '1 package update available.',
      });
      expect(u.packages.single.name, 'openssl.x86_64');
      expect(u.securityCount, 3);
      expect(u.rebootNeeded, isTrue);
      expect(u.newerRelease, '2023.6.20241010');
      expect(u.copyWith(rebootNeeded: false).rebootNeeded, isFalse);
      expect(ServerUpdates.fromJson({}).packages, isEmpty);
    });

    test('storage, with Docker as text and labels from the agent', () {
      final s = ServerStorage.fromJson({
        'mounts': [
          {'mount': '/', 'device': '/dev/nvme0n1p1', 'fs': 'xfs', 'used_gb': 20.5, 'total_gb': 30, 'free_gb': 9.5, 'pct': 68.3},
        ],
        'biggest': [
          {'path': '/var/log', 'gb': 0.4},
          {'path': '/home/ec2-user', 'gb': 5.2},
        ],
        'cleanable': {'trash': 0.0, 'journal': 1.25, 'docker': 'Images: 1.2GB (40%); Build Cache: 300MB', 'extra': 2},
        'cleanable_labels': {'journal': 'System logs (journald)'},
        'swap': {'used_gb': 0.1, 'total_gb': 2, 'pct': 5},
        'message': 'Disk / is 68% full.',
      });
      expect(s.mounts.single.pct, 68.3);
      expect(s.biggest.first.path, '/home/ec2-user', reason: 'biggest first');
      expect(s.cleanable.map((c) => c.key), ['journal', 'docker', 'trash', 'extra']);
      expect(s.cleanable[0].label, 'System logs (journald)');
      expect(s.cleanable[0].sizeText, '1.3 GB');
      expect(s.cleanable[1].text, startsWith('Images'));
      expect(s.cleanable[1].label, contains('Docker'), reason: 'fallback label');
      expect(s.cleanable[2].sizeText, '0 GB');
      expect(s.swapPct, 5);
    });

    test('network sorts public ports first', () {
      final n = ServerNetwork.fromJson({
        'interfaces': [
          {'interface': 'ens5', 'state': 'UP', 'addresses': ['172.31.5.9/20', 'fe80::1/64']},
        ],
        'listening': [
          {'proto': 'tcp', 'address': '127.0.0.1', 'port': '6379', 'public': false, 'process': 'redis', 'pid': 9},
          {'proto': 'tcp', 'address': '0.0.0.0', 'port': '443', 'public': true, 'process': 'nginx', 'pid': 1},
          {'proto': 'tcp', 'address': '0.0.0.0', 'port': '22', 'public': true, 'process': 'sshd', 'pid': 2},
        ],
        'rate': {'down_kbps': 120.5, 'up_kbps': 30},
        'established': 14,
      });
      expect(n.interfaces.single.addresses.first, '172.31.5.9/20');
      expect(n.listening.map((p) => p.port), ['22', '443', '6379']);
      expect(n.downKbps, 120.5);
      expect(n.established, 14);
    });

    test('security rates the SSH settings', () {
      final s = ServerSecurity.fromJson({
        'logged_in': ['ec2-user pts/0 2026-10-02 10:00 (1.2.3.4)'],
        'recent_logins': ['ec2-user pts/0 ...', ''],
        'failed_ssh_24h': 340,
        'top_attackers': [
          {'ip': '45.1.2.3', 'attempts': 200},
          {'attempts': 1},
        ],
        'ssh_password_login': 'yes',
        'root_login': 'prohibit-password',
        'fail2ban': 'not installed',
      });
      expect(s.recentLogins.length, 1);
      expect(s.topAttackers.single.ip, '45.1.2.3');
      expect(s.failedHealth, UnitHealth.bad);
      expect(s.passwordHealth, UnitHealth.bad);
      expect(s.rootHealth, UnitHealth.warn);
      expect(s.fail2banHealth, UnitHealth.warn);
      expect(const ServerSecurity(sshPasswordLogin: 'no', rootLogin: 'no', fail2ban: 'active').rootHealth,
          UnitHealth.good);
    });

    test('services: failed first, filters and the boot switch', () {
      final list = SystemService.listFrom([
        {'name': 'sshd', 'load': 'loaded', 'active': 'active', 'sub': 'running', 'description': 'OpenSSH', 'boot': 'enabled'},
        {'name': 'cups', 'load': 'loaded', 'active': 'inactive', 'sub': 'dead', 'description': 'Printing', 'boot': 'disabled'},
        {'name': 'broken', 'load': 'loaded', 'active': 'failed', 'sub': 'failed', 'description': 'Oops', 'boot': 'static'},
        {'load': 'loaded'},
      ]);
      expect(list.map((s) => s.name), ['broken', 'sshd', 'cups']);
      expect(list[0].failed, isTrue);
      expect(list[0].bootToggleable, isFalse);
      expect(list[1].stateText, 'active (running)');
      expect(list[1].startsAtBoot, isTrue);
      expect(list[2].withBoot('enabled').startsAtBoot, isTrue);
      expect(filterServices(list, '', ServiceFilter.failed).single.name, 'broken');
      expect(filterServices(list, '', ServiceFilter.running).single.name, 'sshd');
      expect(filterServices(list, '', ServiceFilter.stopped).single.name, 'cups');
      expect(filterServices(list, 'print', ServiceFilter.all).single.name, 'cups');
    });

    test('jobs', () {
      final j = ServerJobs.fromJson({
        'cron': ['*/5 * * * * /home/x/backup.sh', ''],
        'timers': [
          {'timer': 'dnf-makecache.timer', 'line': 'Thu 2026-10-02 11:00 ... dnf-makecache.timer'},
          {'line': 'no name'},
        ],
      });
      expect(j.cron.length, 1);
      expect(j.timers.single.timer, 'dnf-makecache.timer');
    });

    test('server errors are worded for the server', () {
      expect(serverErrorText({'error': 'TIMEOUT'}, 'x'), contains('server'));
      expect(serverErrorText({'error': 'DEVICE_OFFLINE'}, 'x'), contains('offline'));
      expect(serverErrorText({'success': false}, 'fallback'), 'fallback');
    });
  });

  testWidgets('history chart draws, reads a moment on tap and fits a phone', (tester) async {
    await tester.binding.setSurfaceSize(const Size(360, 400));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final times = [for (var i = 0; i < 60; i++) 1700000000.0 + i * 60];
    final cpu = [for (var i = 0; i < 60; i++) i == 30 ? null : (i % 10) * 9.0];
    await tester.pumpWidget(MaterialApp(
      theme: buildWillyTheme(),
      home: Scaffold(
        body: Padding(
          padding: const EdgeInsets.all(16),
          child: HistoryChart(
            times: times,
            maxY: 100,
            format: (v) => '${v.round()}%',
            series: [ChartSeries('CPU', WillyColors.cyan, cpu)],
          ),
        ),
      ),
    ));
    expect(find.textContaining('CPU'), findsOneWidget);
    expect(find.textContaining('peak 81%'), findsOneWidget);
    await tester.tapAt(const Offset(300, 60));
    await tester.pump();
    expect(find.textContaining('peak'), findsNothing, reason: 'shows the picked moment instead');
    expect(tester.takeException(), isNull);
  });

  testWidgets('an empty chart says so', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildWillyTheme(),
      home: Scaffold(
        body: HistoryChart(times: const [], format: (v) => '$v', series: const [ChartSeries('In', WillyColors.green, [])]),
      ),
    ));
    expect(tester.takeException(), isNull);
  });

  for (final online in [false, true]) {
    testWidgets('System tab lays out every part on a 360 px phone (${online ? 'online' : 'offline'})', (tester) async {
      await tester.binding.setSurfaceSize(const Size(360, 720));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      await tester.pumpWidget(MaterialApp(
        theme: buildWillyTheme(),
        home: ServerScreen(device: server(online: online), initialTab: ServerTab.system),
      ));
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.text('History'), findsOneWidget);
      for (final s in SystemSection.values) {
        final chip = find.widgetWithText(ChoiceChip, s.label);
        await tester.ensureVisible(chip);
        await tester.pump(const Duration(milliseconds: 100));
        await tester.tap(chip);
        await tester.pump(const Duration(milliseconds: 400));
        expect(tester.takeException(), isNull, reason: s.label);
      }
      expect(find.text('Show logs'), findsOneWidget);
      // Other tabs still work next to it.
      await tester.tap(find.widgetWithText(Tab, 'Overview'));
      await tester.pump(const Duration(milliseconds: 400));
      expect(find.text('Resources'), findsOneWidget);
      // Let the (failing, no hub in tests) requests settle so no timers are left.
      await tester.pump(const Duration(seconds: 45));
      await tester.pumpWidget(const SizedBox.shrink());
    });
  }

  testWidgets('System parts render real answers on a 360 px phone', (tester) async {
    await tester.binding.setSurfaceSize(const Size(360, 740));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final calls = <String>[];
    ApiService.httpClient = _fakeHub(calls);
    addTearDown(() => ApiService.httpClient = http.Client());
    await tester.pumpWidget(MaterialApp(
      theme: buildWillyTheme(),
      home: ServerScreen(device: server(), initialTab: ServerTab.system),
    ));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('CPU & memory'), findsOneWidget);
    expect(find.textContaining('120 samples'), findsOneWidget);
    expect(tester.takeException(), isNull, reason: 'history');

    Future<void> open(SystemSection s) async {
      final chip = find.widgetWithText(ChoiceChip, s.label);
      await tester.ensureVisible(chip);
      await tester.pump(const Duration(milliseconds: 100));
      await tester.tap(chip);
      for (var i = 0; i < 4; i++) {
        await tester.pump(const Duration(milliseconds: 200));
      }
      expect(tester.takeException(), isNull, reason: s.label);
    }

    await open(SystemSection.updates);
    expect(find.text('Reboot needed'), findsOneWidget);
    expect(find.text('2 security'), findsOneWidget);
    await tester.ensureVisible(find.text('Reboot'));
    await tester.pump();
    await tester.tap(find.text('Reboot'));
    await tester.pump(const Duration(milliseconds: 400));
    final confirm = find.widgetWithText(FilledButton, 'Reboot in 1 min');
    expect(confirm, findsOneWidget);
    expect(tester.widget<FilledButton>(confirm).onPressed, isNull, reason: 'needs the tick first');
    await tester.tap(find.byType(Checkbox));
    await tester.pump();
    expect(tester.widget<FilledButton>(confirm).onPressed, isNotNull);
    await tester.tap(find.widgetWithText(TextButton, 'Cancel'));
    await tester.pump(const Duration(milliseconds: 400));
    expect(calls, isNot(contains('reboot')));

    await open(SystemSection.storage);
    expect(find.text('Clean up'), findsOneWidget);
    expect(find.text('/'), findsOneWidget);

    await open(SystemSection.network);
    expect(find.text('443'), findsOneWidget);
    expect(find.text('public'), findsOneWidget);
    expect(find.text('1.8 Mbit/s'), findsOneWidget);

    await open(SystemSection.services);
    expect(find.text('sshd'), findsOneWidget);
    expect(find.text('Failed 1'), findsOneWidget);
    await tester.ensureVisible(find.text('Failed 1'));
    await tester.pump();
    await tester.tap(find.text('Failed 1'));
    await tester.pump();
    expect(find.text('sshd'), findsNothing);
    expect(tester.takeException(), isNull);

    await open(SystemSection.logs);
    expect(find.textContaining('kex_exchange_identification'), findsOneWidget);
    expect(calls, containsAll(['sys_updates', 'storage', 'network', 'security', 'services_list', 'timers', 'journal']));
    expect(calls.where((c) => c == 'sys_updates').length, 1, reason: 'loaded once, not on every visit');

    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('every System part scrolls through its content without overflow', (tester) async {
    await tester.binding.setSurfaceSize(const Size(360, 740));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    ApiService.httpClient = _fakeHub([]);
    addTearDown(() => ApiService.httpClient = http.Client());
    for (final s in SystemSection.values) {
      await tester.pumpWidget(MaterialApp(
        theme: buildWillyTheme(),
        home: Scaffold(
          body: ServerSystemTab(key: ValueKey(s), device: server(), initialSection: s),
        ),
      ));
      for (var i = 0; i < 4; i++) {
        await tester.pump(const Duration(milliseconds: 200));
      }
      // Drag the part's list all the way down, a screen at a time.
      final list = find.byType(ListView).last;
      for (var i = 0; i < 12; i++) {
        await tester.drag(list, const Offset(0, -500));
        await tester.pump(const Duration(milliseconds: 50));
      }
      expect(tester.takeException(), isNull, reason: s.label);
    }
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
