import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:willy_mobile/models/activity.dart';
import 'package:willy_mobile/models/device.dart';
import 'package:willy_mobile/theme.dart';
import 'package:willy_mobile/widgets/charts.dart';
import 'package:willy_mobile/widgets/common.dart';

/// A device payload exactly as the v3 hub sends it (ints, doubles and nulls mixed).
Map<String, dynamic> hubPc() => {
      'device_id': 'pc_harig',
      'device_type': 'pc',
      'name': 'HariG',
      'hostname': 'HariG',
      'platform': 'Windows 11',
      'status': 'online',
      'online': true,
      'connection': 'websocket',
      'last_seen': 1790000000.5,
      'commands_handled': 7,
      'specs': {
        'cpu_model': '11th Gen Intel(R) Core(TM) i5-11260H',
        'gpu': ['Intel(R) UHD Graphics', 'NVIDIA GeForce RTX 2050'],
        'monitors': 2,
        'has_battery': true,
      },
      'telemetry': {
        'battery_pct': 90,
        'is_charging': true,
        'cpu_pct': 55.6,
        'ram_pct': 87,
        'ram_used_gb': 13.0,
        'ram_total_gb': 15.7,
        'disk_free_gb': 45,
        'net_down_kbps': 59.1,
        'volume_level': 100,
        'is_muted': false,
        'last_ping_ms': 1,
        'wifi_ssid': null,
        'top_processes': [
          {'pid': 30504, 'name': 'chrome.exe', 'cpu': 16.7, 'mem_mb': 56.1, 'threads': 49},
        ],
      },
      'history': {
        'cpu': [10.0, null, 30.5],
        'ram': [80, 81, 82],
      },
    };

void main() {
  group('WillyDevice.fromJson', () {
    test('parses mixed numeric types and nulls', () {
      final d = WillyDevice.fromJson(hubPc());
      expect(d.isPc, isTrue);
      expect(d.online, isTrue);
      expect(d.batteryPct, 90.0);
      expect(d.cpuPct, closeTo(55.6, 0.001));
      expect(d.ramPct, 87.0);
      expect(d.diskFreeGb, 45.0);
      expect(d.wifiSsid, isNull);
      expect(d.lastPingMs, 1);
      expect(d.isMuted, isFalse);
      expect(d.monitors, 2);
      expect(d.gpus, hasLength(2));
      expect(d.hasBattery, isTrue);
      expect(d.topProcesses.single.name, 'chrome.exe');
      expect(d.history.cpu, [10.0, null, 30.5]);
      expect(d.history.ram, [80.0, 81.0, 82.0]);
    });

    test('tolerates a minimal legacy payload', () {
      final d = WillyDevice.fromJson({'device_id': 'mobile_android_phone', 'device_type': 'mobile', 'status': 'online'});
      expect(d.isMobile, isTrue);
      expect(d.online, isTrue);
      expect(d.batteryPct, isNull);
      expect(d.topProcesses, isEmpty);
      expect(d.history.cpu, isEmpty);
      expect(d.unreadNotifications, 0);
    });
  });

  test('ActivityEntry.fromJson reads running and finished entries', () {
    final running = ActivityEntry.fromJson({'id': 'a1', 'ts': 1.0, 'source': 'mobile', 'query': 'lock', 'status': 'running'});
    expect(running.isRunning, isTrue);
    final done = ActivityEntry.fromJson({
      'id': 'a1',
      'ts': 1,
      'source': 'dashboard',
      'query': 'volume 40',
      'status': 'done',
      'success': true,
      'fast_path': true,
      'tools': ['volume_control'],
      'latency_ms': 12,
      'timings': {'total_ms': 12},
    });
    expect(done.fastPath, isTrue);
    expect(done.failed, isFalse);
    expect(done.tools, ['volume_control']);
    expect(done.latencyMs, 12);
  });

  test('formatters', () {
    expect(formatRate(null), '—');
    expect(formatRate(5.25), '5.3 KB/s');
    expect(formatRate(340), '340 KB/s');
    expect(formatRate(2048), '2.0 MB/s');
    expect(formatDuration(90), '1m 30s');
    expect(formatDuration(5400), '1h 30m');
    expect(formatHours(0.5), '30m 0s');
    final now = DateTime.now().millisecondsSinceEpoch / 1000;
    expect(timeAgo(now), 'just now');
    expect(timeAgo(now - 125), '2m ago');
    expect(timeAgo(null), 'never');
  });

  testWidgets('RingGauge and Sparkline render live values', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildWillyTheme(),
      home: const Scaffold(
        body: Column(
          children: [
            RingGauge(value: 42, label: 'CPU', color: WillyColors.cyan),
            RingGauge(value: null, label: 'Battery', color: WillyColors.green, centerText: 'AC'),
            SizedBox(width: 200, child: Sparkline(values: [1, null, 3, 2], color: WillyColors.cyan)),
            StatusPill(text: 'ONLINE', color: WillyColors.green),
          ],
        ),
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('42%'), findsOneWidget);
    expect(find.text('AC'), findsOneWidget);
    expect(find.text('CPU'), findsOneWidget);
    expect(find.text('ONLINE'), findsOneWidget);
  });
}
