import 'package:flutter/material.dart';

import '../../models/device.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import 'server_screen.dart';
import 'server_widgets.dart';

/// Devices tab card for the Willy server (device_type "server"): live CPU / RAM / disk,
/// load and uptime. Tapping it (or a shortcut) opens the Server screen.
class ServerCard extends StatelessWidget {
  final WillyDevice device;

  const ServerCard({super.key, required this.device});

  void _open(BuildContext context, ServerTab tab) {
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => ServerScreen(device: device, initialTab: tab)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final dev = device;
    final online = dev.online;
    final subtitle = [
      dev.platform,
      if (dev.machineName.isNotEmpty && dev.machineName != dev.name) dev.machineName,
    ].join(' · ');
    final ramCaption = dev.ramUsedGb != null && dev.ramTotalGb != null
        ? '${dev.ramUsedGb!.toStringAsFixed(1)} / ${dev.ramTotalGb!.toStringAsFixed(1)} GB'
        : null;
    final diskCaption = dev.diskFreeGb != null
        ? '${dev.diskFreeGb!.round()} GB free${dev.diskTotalGb != null ? ' of ${dev.diskTotalGb!.round()} GB' : ''}'
        : null;

    return Opacity(
      opacity: online ? 1 : 0.6,
      child: WillyCard(
        padding: EdgeInsets.zero,
        borderColor: online ? WillyColors.border : WillyColors.red.withValues(alpha: 0.3),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            borderRadius: BorderRadius.circular(20),
            onTap: () => _open(context, ServerTab.overview),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Container(
                        width: 46,
                        height: 46,
                        decoration: BoxDecoration(
                          color: WillyColors.green.withValues(alpha: 0.12),
                          borderRadius: BorderRadius.circular(14),
                          border: Border.all(color: WillyColors.green.withValues(alpha: 0.4)),
                        ),
                        child: const Icon(Icons.dns_rounded, color: WillyColors.green, size: 26),
                      ),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(dev.name,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 17, color: Colors.white)),
                            const SizedBox(height: 2),
                            Text(subtitle,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(fontSize: 12, color: WillyColors.muted)),
                          ],
                        ),
                      ),
                      StatusPill(
                        text: online ? 'ONLINE' : 'OFFLINE',
                        color: online ? WillyColors.green : WillyColors.red,
                      ),
                    ],
                  ),
                  if (!online) ...[
                    const SizedBox(height: 10),
                    Text('Last seen ${timeAgo(dev.lastSeen)} · server agent not connected',
                        style: const TextStyle(color: WillyColors.red, fontSize: 12)),
                  ],
                  const SizedBox(height: 16),
                  StatBar(label: 'CPU', pct: dev.cpuPct, caption: dev.cores != null ? '${dev.cores} cores' : null),
                  const SizedBox(height: 10),
                  StatBar(label: 'RAM', pct: dev.ramPct, caption: ramCaption),
                  const SizedBox(height: 10),
                  StatBar(label: 'Disk', pct: dev.diskPct, caption: diskCaption),
                  const SizedBox(height: 14),
                  LayoutBuilder(builder: (context, c) {
                    final w = (c.maxWidth - 8) / 2;
                    final chips = [
                      InfoChip(
                        icon: Icons.speed_rounded,
                        label: 'Load 1 · 5 · 15 min',
                        value: formatLoad(dev.load1, dev.load5, dev.load15),
                        color: _loadColor(dev),
                      ),
                      InfoChip(
                        icon: Icons.timer_outlined,
                        label: 'Uptime',
                        value: formatHours(dev.uptimeHours),
                        color: WillyColors.sky,
                      ),
                      InfoChip(
                        icon: Icons.apps_rounded,
                        label: 'Processes',
                        value: dev.processCount?.toString() ?? '—',
                        color: WillyColors.purple,
                      ),
                      InfoChip(
                        icon: Icons.swap_vert_rounded,
                        label: 'Swap',
                        value: dev.swapPct == null ? '—' : '${dev.swapPct!.round()}%',
                        color: WillyColors.load(dev.swapPct),
                      ),
                    ];
                    return Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [for (final chip in chips) SizedBox(width: w, child: chip)],
                    );
                  }),
                  const SizedBox(height: 12),
                  Row(
                    children: [
                      for (final (i, t) in ServerTab.values.where((t) => t != ServerTab.system).indexed) ...[
                        if (i > 0) const SizedBox(width: 8),
                        Expanded(
                          child: ActionTile(
                            icon: t.icon,
                            label: t.label,
                            color: t.color,
                            onTap: () => _open(context, t),
                          ),
                        ),
                      ],
                    ],
                  ),
                  const SizedBox(height: 8),
                  // System has many parts, so it gets a full-width shortcut under the four tiles.
                  Material(
                    color: ServerTab.system.color.withValues(alpha: 0.1),
                    borderRadius: BorderRadius.circular(14),
                    child: InkWell(
                      borderRadius: BorderRadius.circular(14),
                      onTap: () => _open(context, ServerTab.system),
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                        decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(14),
                          border: Border.all(color: ServerTab.system.color.withValues(alpha: 0.35)),
                        ),
                        child: Row(
                          children: [
                            Icon(ServerTab.system.icon, size: 18, color: ServerTab.system.color),
                            const SizedBox(width: 10),
                            const Text('System',
                                style: TextStyle(color: WillyColors.textSoft, fontSize: 12.5, fontWeight: FontWeight.w700)),
                            const SizedBox(width: 8),
                            const Expanded(
                              child: Text('history · updates · storage · network · services · logs',
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: TextStyle(color: WillyColors.faint, fontSize: 11)),
                            ),
                            const Icon(Icons.chevron_right_rounded, size: 18, color: WillyColors.faint),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  /// Load per core: green under 0.7, amber under 1, red above.
  static Color _loadColor(WillyDevice dev) {
    final l = dev.load5 ?? dev.load1;
    final cores = dev.cores;
    if (l == null) return WillyColors.faint;
    final perCore = cores != null && cores > 0 ? l / cores : l;
    if (perCore < 0.7) return WillyColors.green;
    if (perCore < 1.0) return WillyColors.amber;
    return WillyColors.red;
  }
}
