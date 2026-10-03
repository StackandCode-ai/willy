import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/device.dart';
import '../services/api_service.dart';
import '../services/telemetry_service.dart';
import '../theme.dart';
import '../widgets/charts.dart';
import '../widgets/common.dart';
import 'live_screen_screen.dart';
import 'pc_actions.dart';
import 'processes_screen.dart';
import 'server/server_card.dart';

/// Home tab: every linked device with live telemetry pushed by the hub (no polling).
class DevicesScreen extends StatefulWidget {
  const DevicesScreen({super.key});

  @override
  State<DevicesScreen> createState() => _DevicesScreenState();
}

class _DevicesScreenState extends State<DevicesScreen> {
  List<WillyDevice> _devices = ApiService.devices;
  StreamSubscription<List<WillyDevice>>? _sub;
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    _sub = ApiService.devicesStream.listen((devices) {
      if (mounted) setState(() => _devices = devices);
    });
    // Keeps "last seen" labels fresh between updates.
    _tick = Timer.periodic(const Duration(seconds: 5), (_) {
      if (mounted) setState(() {});
    });
    if (_devices.isEmpty) ApiService.getDevices();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _tick?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: () async {
        await ApiService.getDevices();
        await ApiService.refreshStats();
      },
      color: WillyColors.cyan,
      backgroundColor: WillyColors.cardAlt,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          _HubSummary(devices: _devices),
          if (_devices.isEmpty)
            const _EmptyState()
          else
            for (final d in _devices) _cardFor(d),
        ],
      ),
    );
  }
}

/// The card for a device: PC, the Linux server, or a phone (also the fallback for new types).
Widget _cardFor(WillyDevice d) {
  if (d.isPc) return PcCard(key: ValueKey(d.id), device: d);
  if (d.isServer) return ServerCard(key: ValueKey(d.id), device: d);
  return PhoneCard(key: ValueKey(d.id), device: d);
}

// ---------------------------------------------------------------- hub summary

class _HubSummary extends StatefulWidget {
  final List<WillyDevice> devices;

  const _HubSummary({required this.devices});

  @override
  State<_HubSummary> createState() => _HubSummaryState();
}

class _HubSummaryState extends State<_HubSummary> with SingleTickerProviderStateMixin {
  late final AnimationController _pulse =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 1800))..repeat();

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final pcs = widget.devices.where((d) => d.isPc).toList();
    final phones = widget.devices.where((d) => d.isMobile).toList();
    final servers = widget.devices.where((d) => d.isServer).toList();
    final stats = ApiService.stats;
    final host = Uri.tryParse(ApiService.baseUrl)?.authority ?? ApiService.baseUrl;

    return StreamBuilder<HubStatus>(
      stream: ApiService.statusStream,
      initialData: ApiService.status,
      builder: (context, snap) {
        final s = snap.data ?? ApiService.status;
        final color = switch (s.state) {
          HubState.online => WillyColors.cyan,
          HubState.connecting => WillyColors.amber,
          HubState.offline => WillyColors.red,
        };
        final title = switch (s.state) {
          HubState.online => 'Hub live${s.latencyMs != null ? ' · ${s.latencyMs} ms' : ''}',
          HubState.connecting => 'Connecting to hub…',
          HubState.offline => 'Hub offline',
        };
        return WillyCard(
          gradient: const LinearGradient(
            colors: [Color(0xFF0F172A), Color(0xFF1E1B4B)],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
          borderColor: const Color(0x803730A3),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  SizedBox(
                    width: 52,
                    height: 52,
                    child: AnimatedBuilder(
                      animation: _pulse,
                      builder: (context, _) => Stack(
                        alignment: Alignment.center,
                        children: [
                          if (s.isOnline)
                            Container(
                              width: 30 + 22 * _pulse.value,
                              height: 30 + 22 * _pulse.value,
                              decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                border: Border.all(color: color.withValues(alpha: 1 - _pulse.value), width: 2),
                              ),
                            ),
                          Container(
                            width: 40,
                            height: 40,
                            decoration: BoxDecoration(
                              color: color.withValues(alpha: 0.15),
                              shape: BoxShape.circle,
                              border: Border.all(color: color),
                            ),
                            child: Icon(Icons.radar, color: color, size: 22),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(title,
                            style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 16)),
                        const SizedBox(height: 3),
                        Text(
                          s.state == HubState.offline && s.detail != null ? s.detail! : host,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(color: WillyColors.muted, fontSize: 12),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 14),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  _miniStat(Icons.laptop_windows_rounded, '${pcs.where((d) => d.online).length}/${pcs.length} PC',
                      WillyColors.sky),
                  _miniStat(Icons.smartphone_rounded, '${phones.where((d) => d.online).length}/${phones.length} phone',
                      WillyColors.purple),
                  if (servers.isNotEmpty)
                    _miniStat(Icons.dns_rounded, '${servers.where((d) => d.online).length}/${servers.length} server',
                        WillyColors.green),
                  if (stats['total'] != null)
                    _miniStat(Icons.bolt_rounded, '${stats['total']} commands', WillyColors.amber),
                  if (stats['latency_p50_ms'] != null)
                    _miniStat(Icons.speed_rounded, 'p50 ${stats['latency_p50_ms']} ms', WillyColors.green),
                ],
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _miniStat(IconData icon, String text, Color color) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: Colors.black.withValues(alpha: 0.25),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: color.withValues(alpha: 0.3)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 14, color: color),
            const SizedBox(width: 6),
            Text(text, style: const TextStyle(color: WillyColors.textSoft, fontSize: 12, fontWeight: FontWeight.w600)),
          ],
        ),
      );
}

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 40),
      child: Column(
        children: [
          Icon(Icons.devices_other, size: 72, color: WillyColors.borderStrong),
          SizedBox(height: 16),
          Text('Searching for devices…',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white70)),
          SizedBox(height: 8),
          Padding(
            padding: EdgeInsets.symmetric(horizontal: 32),
            child: Text(
              'Start the Willy PC client on your laptop. It appears here instantly with live stats.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.white38, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}

// ------------------------------------------------------------------- PC card

class PcCard extends StatefulWidget {
  final WillyDevice device;

  const PcCard({super.key, required this.device});

  @override
  State<PcCard> createState() => _PcCardState();
}

class _PcCardState extends State<PcCard> {
  double? _volumeDraft; // shown while dragging / until the PC confirms
  Timer? _volumeDebounce;
  DateTime _volumeSentAt = DateTime.fromMillisecondsSinceEpoch(0);
  bool _locking = false;

  @override
  void dispose() {
    _volumeDebounce?.cancel();
    super.dispose();
  }

  void _onVolumeChanged(double v) {
    setState(() => _volumeDraft = v);
    _volumeDebounce?.cancel();
    _volumeDebounce = Timer(const Duration(milliseconds: 250), () async {
      _volumeSentAt = DateTime.now();
      await ApiService.setVolume(widget.device.id, v.round());
    });
  }

  double? get _shownVolume {
    // Prefer the draft for a few seconds after a change so the slider doesn't jump back.
    if (_volumeDraft != null && DateTime.now().difference(_volumeSentAt).inSeconds < 4) return _volumeDraft;
    if (_volumeDraft != null && _volumeDebounce?.isActive == true) return _volumeDraft;
    return widget.device.volumeLevel ?? _volumeDraft;
  }

  Future<void> _lock() async {
    setState(() => _locking = true);
    await PcActions.run(context, widget.device, 'power_action', {'action': 'lock'}, 'Locked ${widget.device.name}');
    if (mounted) setState(() => _locking = false);
  }

  @override
  Widget build(BuildContext context) {
    final dev = widget.device;
    final online = dev.online;
    final h = dev.history;

    return Opacity(
      opacity: online ? 1 : 0.6,
      child: WillyCard(
        borderColor: online ? WillyColors.border : WillyColors.red.withValues(alpha: 0.3),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _header(dev),
            if (!online) ...[
              const SizedBox(height: 10),
              Text('Last seen ${timeAgo(dev.lastSeen)} · actions unavailable',
                  style: const TextStyle(color: WillyColors.red, fontSize: 12)),
            ],
            const SizedBox(height: 16),
            _gauges(dev),
            const SizedBox(height: 16),
            _historyPanel(h, dev),
            const SizedBox(height: 12),
            _infoGrid(dev),
            if (dev.activeWindow != null) ...[
              const SizedBox(height: 10),
              _activeWindow(dev),
            ],
            if (online && dev.volumeLevel != null) ...[
              const SizedBox(height: 6),
              _volumeRow(dev),
            ],
            const SizedBox(height: 10),
            _actions(dev, online),
          ],
        ),
      ),
    );
  }

  Widget _header(WillyDevice dev) {
    final subtitle = [
      dev.osName ?? dev.platform,
      if (dev.ipAddress != null) dev.ipAddress!,
    ].join(' · ');
    return Row(
      children: [
        Container(
          width: 46,
          height: 46,
          decoration: BoxDecoration(
            color: WillyColors.sky.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: WillyColors.sky.withValues(alpha: 0.4)),
          ),
          child: const Icon(Icons.laptop_windows, color: WillyColors.sky, size: 26),
        ),
        const SizedBox(width: 14),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(dev.name, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 17, color: Colors.white)),
              const SizedBox(height: 2),
              Text(subtitle,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 12, color: WillyColors.muted)),
            ],
          ),
        ),
        IconButton(
          tooltip: 'Hardware specs',
          icon: const Icon(Icons.info_outline_rounded, color: WillyColors.muted, size: 20),
          onPressed: () => PcActions.specs(context, dev),
        ),
        StatusPill(
          text: dev.online ? 'ONLINE' : 'OFFLINE',
          color: dev.online ? WillyColors.green : WillyColors.red,
        ),
      ],
    );
  }

  Widget _gauges(WillyDevice dev) {
    final charging = dev.isCharging == true;
    String? batteryCaption;
    if (dev.batteryPct == null) {
      batteryCaption = 'AC power';
    } else if (charging) {
      batteryCaption = 'Charging';
    } else if (dev.batterySecsLeft != null) {
      batteryCaption = '${formatDuration(dev.batterySecsLeft)} left';
    } else {
      batteryCaption = 'On battery';
    }
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceAround,
      children: [
        RingGauge(
          value: dev.cpuPct,
          label: 'CPU',
          color: WillyColors.load(dev.cpuPct),
          caption: dev.cpuFreqMhz != null ? '${(dev.cpuFreqMhz! / 1000).toStringAsFixed(1)} GHz' : null,
        ),
        RingGauge(
          value: dev.ramPct,
          label: 'Memory',
          color: WillyColors.load(dev.ramPct),
          caption: dev.ramUsedGb != null ? '${dev.ramUsedGb}/${dev.ramTotalGb} GB' : null,
        ),
        RingGauge(
          value: dev.diskPct,
          label: 'Disk',
          color: WillyColors.load(dev.diskPct),
          caption: dev.diskFreeGb != null ? '${dev.diskFreeGb!.round()} GB free' : null,
        ),
        RingGauge(
          value: dev.batteryPct,
          label: 'Battery',
          color: WillyColors.battery(dev.batteryPct, charging: charging),
          icon: charging ? Icons.bolt_rounded : null,
          centerText: dev.batteryPct == null ? 'AC' : null,
          caption: batteryCaption,
        ),
      ],
    );
  }

  Widget _historyPanel(TelemetryHistory h, WillyDevice dev) {
    final seconds = (h.cpu.length * 2).clamp(0, 999);
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      decoration: BoxDecoration(
        color: WillyColors.card,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: WillyColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _legend(WillyColors.cyan, 'CPU'),
              const SizedBox(width: 12),
              _legend(WillyColors.purple, 'RAM'),
              const Spacer(),
              Text('last ${seconds}s', style: const TextStyle(color: WillyColors.faint, fontSize: 10)),
            ],
          ),
          const SizedBox(height: 6),
          Sparkline(values: h.cpu, secondary: h.ram, color: WillyColors.cyan, maxValue: 100, height: 52),
          const SizedBox(height: 10),
          Row(
            children: [
              const Icon(Icons.south_rounded, size: 14, color: WillyColors.green),
              Text(' ${formatRate(dev.netDownKbps)}',
                  style: const TextStyle(color: WillyColors.textSoft, fontSize: 12, fontWeight: FontWeight.w600)),
              const SizedBox(width: 14),
              const Icon(Icons.north_rounded, size: 14, color: WillyColors.amber),
              Text(' ${formatRate(dev.netUpKbps)}',
                  style: const TextStyle(color: WillyColors.textSoft, fontSize: 12, fontWeight: FontWeight.w600)),
              const Spacer(),
              if (dev.wifiSsid != null)
                Flexible(
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.wifi_rounded, size: 14, color: WillyColors.sky),
                      const SizedBox(width: 4),
                      Flexible(
                        child: Text(dev.wifiSsid!,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(color: WillyColors.muted, fontSize: 12)),
                      ),
                    ],
                  ),
                ),
            ],
          ),
          const SizedBox(height: 4),
          Sparkline(
            values: h.netDown,
            secondary: h.netUp,
            color: WillyColors.green,
            secondaryColor: WillyColors.amber,
            height: 30,
          ),
        ],
      ),
    );
  }

  Widget _legend(Color color, String label) => Row(
        children: [
          Container(width: 8, height: 8, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
          const SizedBox(width: 5),
          Text(label, style: const TextStyle(color: WillyColors.muted, fontSize: 11, fontWeight: FontWeight.w600)),
        ],
      );

  Widget _infoGrid(WillyDevice dev) {
    final idle = dev.idleSec;
    final activity = idle == null ? '—' : (idle < 60 ? 'Active now' : 'Idle ${formatDuration(idle)}');
    final chips = [
      InfoChip(icon: Icons.timer_outlined, label: 'Uptime', value: formatHours(dev.uptimeHours), color: WillyColors.sky),
      InfoChip(
        icon: Icons.touch_app_outlined,
        label: 'User',
        value: activity,
        color: idle != null && idle < 60 ? WillyColors.green : WillyColors.amber,
      ),
      InfoChip(
        icon: Icons.apps_rounded,
        label: 'Processes',
        value: dev.processCount?.toString() ?? '—',
        color: WillyColors.purple,
      ),
      InfoChip(
        icon: Icons.network_ping_rounded,
        label: 'Hub ping',
        value: dev.lastPingMs != null ? '${dev.lastPingMs} ms' : '—',
        color: WillyColors.green,
      ),
    ];
    return LayoutBuilder(
      builder: (context, c) {
        final w = (c.maxWidth - 8) / 2;
        return Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [for (final chip in chips) SizedBox(width: w, child: chip)],
        );
      },
    );
  }

  Widget _activeWindow(WillyDevice dev) {
    final process = dev.activeProcess;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      decoration: BoxDecoration(
        color: WillyColors.card,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: WillyColors.border),
      ),
      child: Row(
        children: [
          const Icon(Icons.window, size: 15, color: WillyColors.muted),
          const SizedBox(width: 8),
          Expanded(
            child: Text.rich(
              TextSpan(children: [
                TextSpan(text: dev.activeWindow, style: const TextStyle(color: WillyColors.textSoft)),
                if (process != null)
                  TextSpan(text: '  $process', style: const TextStyle(color: WillyColors.faint, fontSize: 11)),
              ]),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12),
            ),
          ),
        ],
      ),
    );
  }

  Widget _volumeRow(WillyDevice dev) {
    final muted = dev.isMuted == true;
    final value = (_shownVolume ?? 0).clamp(0, 100).toDouble();
    return Row(
      children: [
        IconButton(
          tooltip: muted ? 'Unmute' : 'Mute',
          icon: Icon(muted ? Icons.volume_off_rounded : Icons.volume_up_rounded,
              color: muted ? WillyColors.red : WillyColors.cyan),
          onPressed: () {
            HapticFeedback.selectionClick();
            ApiService.deviceAction(dev.id, 'volume_control', {'action': muted ? 'unmute' : 'mute'});
          },
        ),
        Expanded(
          child: Slider(value: value, min: 0, max: 100, onChanged: _onVolumeChanged),
        ),
        SizedBox(
          width: 40,
          child: Text('${value.round()}%',
              textAlign: TextAlign.right, style: const TextStyle(color: WillyColors.muted, fontSize: 12)),
        ),
      ],
    );
  }

  Widget _actions(WillyDevice dev, bool online) {
    VoidCallback? on(VoidCallback f) => online ? f : null;
    final tiles = [
      ActionTile(icon: Icons.notifications_active_outlined, label: 'Locate', color: WillyColors.green,
          onTap: on(() => PcActions.locate(context, dev))),
      ActionTile(icon: Icons.content_paste_rounded, label: 'Clipboard', color: WillyColors.blue,
          onTap: on(() => PcActions.clipboard(context, dev))),
      ActionTile(icon: Icons.screenshot_monitor_rounded, label: 'Live screen', color: WillyColors.purple,
          onTap: on(() => Navigator.push(
              context, MaterialPageRoute(builder: (_) => LiveScreenScreen(device: dev))))),
      ActionTile(icon: Icons.link_rounded, label: 'QuickDrop', color: WillyColors.sky,
          onTap: on(() => PcActions.quickDrop(context, dev))),
      ActionTile(icon: Icons.music_note_rounded, label: 'Media', color: WillyColors.amber,
          onTap: on(() => PcActions.media(context, dev))),
      ActionTile(icon: Icons.lock_outline_rounded, label: 'Lock', color: WillyColors.red, busy: _locking,
          onTap: on(_lock)),
      ActionTile(icon: Icons.memory_rounded, label: 'Processes', color: WillyColors.pink,
          onTap: on(() => Navigator.push(
              context, MaterialPageRoute(builder: (_) => ProcessesScreen(device: dev))))),
      ActionTile(icon: Icons.more_horiz_rounded, label: 'More', color: WillyColors.muted,
          onTap: on(() => PcActions.more(context, dev))),
    ];
    return GridView.count(
      crossAxisCount: 4,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      mainAxisSpacing: 8,
      crossAxisSpacing: 8,
      childAspectRatio: 1.05,
      children: tiles,
    );
  }
}

// ---------------------------------------------------------------- phone card

class PhoneCard extends StatefulWidget {
  final WillyDevice device;

  const PhoneCard({super.key, required this.device});

  @override
  State<PhoneCard> createState() => _PhoneCardState();
}

class _PhoneCardState extends State<PhoneCard> {
  bool? _torchOverride; // set right after a toggle, until the phone reports it
  DateTime _torchSetAt = DateTime.fromMillisecondsSinceEpoch(0);

  bool get _isThisPhone => widget.device.id == ApiService.deviceId;

  bool get _torch {
    final recent = DateTime.now().difference(_torchSetAt).inSeconds < 8;
    if (_torchOverride != null && recent) return _torchOverride!;
    return widget.device.torchOn ?? _torchOverride ?? false;
  }

  Future<void> _toggleTorch() async {
    final next = !_torch;
    bool ok;
    if (_isThisPhone) {
      ok = await MobileTelemetryService.setTorch(next);
    } else {
      final res = await ApiService.deviceAction(widget.device.id, 'flashlight', {'on': next});
      ok = res['success'] == true;
    }
    if (!mounted) return;
    if (ok) {
      setState(() {
        _torchOverride = next;
        _torchSetAt = DateTime.now();
      });
    } else {
      showWillySnack(context, 'Flashlight not available', error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final dev = widget.device;
    final online = dev.online;
    final charging = dev.isCharging == true;
    final network = switch (dev.networkType) {
      'wifi' => 'Wi-Fi',
      'cellular' => 'Mobile data',
      'ethernet' => 'Ethernet',
      'vpn' => 'VPN',
      'none' => 'No network',
      _ => dev.connection == 'http' ? 'HTTP sync' : 'Online',
    };

    return Opacity(
      opacity: online ? 1 : 0.6,
      child: WillyCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 46,
                  height: 46,
                  decoration: BoxDecoration(
                    color: WillyColors.purple.withValues(alpha: 0.14),
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: WillyColors.purple.withValues(alpha: 0.4)),
                  ),
                  child: const Icon(Icons.smartphone, color: WillyColors.purple, size: 26),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Flexible(
                            child: Text(dev.name,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: Colors.white)),
                          ),
                          if (_isThisPhone) ...[
                            const SizedBox(width: 6),
                            const StatusPill(text: 'THIS PHONE', color: WillyColors.cyan, dot: false),
                          ],
                        ],
                      ),
                      const SizedBox(height: 2),
                      Text('${dev.platform} · $network',
                          style: const TextStyle(fontSize: 12, color: WillyColors.muted)),
                    ],
                  ),
                ),
                StatusPill(text: online ? 'ONLINE' : 'OFFLINE', color: online ? WillyColors.green : WillyColors.red),
              ],
            ),
            const SizedBox(height: 14),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [
                RingGauge(
                  value: dev.batteryPct,
                  label: 'Battery',
                  size: 58,
                  color: WillyColors.battery(dev.batteryPct, charging: charging),
                  icon: charging ? Icons.bolt_rounded : null,
                  caption: charging ? 'Charging' : null,
                ),
                RingGauge(
                  value: asDouble(dev.telemetry['ram_pct']),
                  label: 'Memory',
                  size: 58,
                  color: WillyColors.load(asDouble(dev.telemetry['ram_pct'])),
                ),
                RingGauge(
                  value: (dev.storageFreeGb != null && (dev.storageTotalGb ?? 0) > 0)
                      ? 100 - dev.storageFreeGb! / dev.storageTotalGb! * 100
                      : null,
                  label: 'Storage',
                  size: 58,
                  color: WillyColors.sky,
                  caption: dev.storageFreeGb != null ? '${dev.storageFreeGb!.round()} GB free' : null,
                ),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                _count(Icons.notifications_rounded, dev.unreadNotifications, 'alerts', WillyColors.amber),
                const SizedBox(width: 8),
                _count(Icons.phone_missed_rounded, dev.missedCalls, 'missed', WillyColors.red),
                const SizedBox(width: 8),
                _count(Icons.chat_rounded, dev.unreadWhatsApp, 'WhatsApp', WillyColors.green),
              ],
            ),
            if (online) ...[
              const SizedBox(height: 12),
              Row(
                children: [
                  if (!_isThisPhone) ...[
                    Expanded(
                      child: ActionTile(
                        icon: Icons.ring_volume_rounded,
                        label: 'Ring',
                        color: WillyColors.green,
                        onTap: () => PcActions.locate(context, dev),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: ActionTile(
                        icon: Icons.link_rounded,
                        label: 'QuickDrop',
                        color: WillyColors.sky,
                        onTap: () => PcActions.quickDrop(context, dev),
                      ),
                    ),
                    const SizedBox(width: 8),
                  ],
                  Expanded(
                    child: ActionTile(
                      icon: _torch ? Icons.flashlight_off_rounded : Icons.flashlight_on_rounded,
                      label: _torch ? 'Torch off' : 'Torch',
                      color: WillyColors.amber,
                      onTap: _toggleTorch,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: ActionTile(
                      icon: Icons.sync_rounded,
                      label: 'Sync',
                      color: WillyColors.blue,
                      onTap: () async {
                        await MobileTelemetryService.syncTelemetryToServer();
                        if (context.mounted) showWillySnack(context, 'Phone stats synced');
                      },
                    ),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _count(IconData icon, int count, String label, Color color) => Expanded(
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 8),
          decoration: BoxDecoration(
            color: WillyColors.card,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: count > 0 ? color.withValues(alpha: 0.4) : WillyColors.border),
          ),
          child: Column(
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(icon, size: 14, color: color),
                  const SizedBox(width: 4),
                  Text('$count', style: TextStyle(color: color, fontWeight: FontWeight.bold)),
                ],
              ),
              Text(label, style: const TextStyle(color: WillyColors.faint, fontSize: 10)),
            ],
          ),
        ),
      );
}
