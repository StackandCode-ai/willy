import 'dart:async';

import 'package:flutter/material.dart';

import '../models/activity.dart';
import '../services/api_service.dart';
import '../theme.dart';
import '../widgets/common.dart';

/// Live feed of every command the hub handles (from the phone, PC, dashboard or voice).
class ActivityScreen extends StatefulWidget {
  const ActivityScreen({super.key});

  @override
  State<ActivityScreen> createState() => _ActivityScreenState();
}

class _ActivityScreenState extends State<ActivityScreen> {
  List<ActivityEntry> _entries = ApiService.activity;
  StreamSubscription<List<ActivityEntry>>? _sub;
  Timer? _tick;
  String _filter = 'all';

  @override
  void initState() {
    super.initState();
    _sub = ApiService.activityStream.listen((list) {
      if (mounted) setState(() => _entries = list);
    });
    _tick = Timer.periodic(const Duration(seconds: 15), (_) {
      if (mounted) setState(() {});
    });
    _refresh();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _tick?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    await ApiService.refreshActivity();
    await ApiService.refreshStats();
    if (mounted) setState(() => _entries = ApiService.activity);
  }

  static IconData _sourceIcon(String source) => switch (source) {
        'mobile' => Icons.smartphone_rounded,
        'pc' => Icons.laptop_windows_rounded,
        'server' => Icons.dns_rounded,
        'dashboard' => Icons.dashboard_rounded,
        'voice' => Icons.mic_rounded,
        _ => Icons.api_rounded,
      };

  @override
  Widget build(BuildContext context) {
    final sources = {'all', ..._entries.map((e) => e.source)};
    final visible = _filter == 'all' ? _entries : _entries.where((e) => e.source == _filter).toList();
    final stats = ApiService.stats;
    final total = asNum(stats['total']);
    final ok = asNum(stats['succeeded']);
    final successRate = (total != null && total > 0 && ok != null) ? '${(ok / total * 100).round()}%' : '—';

    return RefreshIndicator(
      onRefresh: _refresh,
      color: WillyColors.cyan,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          WillyCard(
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [
                _stat('Commands', '${stats['total'] ?? '—'}', WillyColors.cyan),
                _stat('Success', successRate, WillyColors.green),
                _stat('Median', stats['latency_p50_ms'] != null ? '${stats['latency_p50_ms']} ms' : '—', WillyColors.amber),
                _stat('⚡ Fast', '${stats['fast_path'] ?? '—'}', WillyColors.purple),
              ],
            ),
          ),
          SizedBox(
            height: 38,
            child: ListView(
              scrollDirection: Axis.horizontal,
              children: [
                for (final s in sources)
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: ChoiceChip(
                      label: Text(s == 'all' ? 'All' : s),
                      selected: _filter == s,
                      onSelected: (_) => setState(() => _filter = s),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 10),
          if (visible.isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 48),
              child: Column(
                children: [
                  Icon(Icons.history_rounded, size: 56, color: WillyColors.borderStrong),
                  SizedBox(height: 10),
                  Text('No activity yet', style: TextStyle(color: Colors.white70, fontSize: 16, fontWeight: FontWeight.bold)),
                  SizedBox(height: 4),
                  Text('Commands from any device appear here live.',
                      style: TextStyle(color: WillyColors.faint, fontSize: 13)),
                ],
              ),
            ),
          for (final e in visible) _entryTile(e),
        ],
      ),
    );
  }

  Widget _stat(String label, String value, Color color) => Column(
        children: [
          Text(value, style: TextStyle(color: color, fontSize: 18, fontWeight: FontWeight.w800)),
          const SizedBox(height: 2),
          Text(label, style: const TextStyle(color: WillyColors.muted, fontSize: 11)),
        ],
      );

  Widget _entryTile(ActivityEntry e) {
    final Color statusColor = e.isRunning ? WillyColors.amber : (e.failed ? WillyColors.red : WillyColors.green);
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: WillyColors.cardAlt,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: e.isRunning ? WillyColors.amber.withValues(alpha: 0.4) : WillyColors.border),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 34,
            height: 34,
            decoration: BoxDecoration(color: statusColor.withValues(alpha: 0.14), shape: BoxShape.circle),
            child: e.isRunning
                ? Padding(
                    padding: const EdgeInsets.all(9),
                    child: CircularProgressIndicator(strokeWidth: 2, color: statusColor),
                  )
                : Icon(e.failed ? Icons.close_rounded : Icons.check_rounded, color: statusColor, size: 18),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(e.query,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: WillyColors.text, fontWeight: FontWeight.w600, fontSize: 14)),
                if (e.reply != null && e.reply!.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text(e.reply!,
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: WillyColors.muted, fontSize: 12, height: 1.3)),
                ],
                const SizedBox(height: 8),
                Wrap(
                  spacing: 6,
                  runSpacing: 4,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(_sourceIcon(e.source), size: 12, color: WillyColors.faint),
                        const SizedBox(width: 3),
                        Text(e.source, style: const TextStyle(color: WillyColors.faint, fontSize: 11)),
                      ],
                    ),
                    if (e.fastPath) const StatusPill(text: '⚡', color: WillyColors.amber, dot: false),
                    for (final tool in e.tools.take(3))
                      StatusPill(text: tool.replaceAll('_', ' '), color: WillyColors.sky, dot: false),
                    if (e.latencyMs != null)
                      Text('${e.latencyMs} ms', style: const TextStyle(color: WillyColors.faint, fontSize: 11)),
                    Text(timeAgo(e.ts), style: const TextStyle(color: WillyColors.faint, fontSize: 11)),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

num? asNum(dynamic v) => v is num ? v : null;
