import 'package:flutter/material.dart';

import '../../models/device.dart';
import '../../models/server.dart';
import '../../services/api_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import 'server_widgets.dart';

/// Disks, swap, the biggest folders and what can be cleaned (`storage` / `cleanup`).
class ServerStorageSection extends StatefulWidget {
  final WillyDevice device;
  final bool active;

  const ServerStorageSection({super.key, required this.device, this.active = true});

  @override
  State<ServerStorageSection> createState() => _ServerStorageSectionState();
}

/// What each cleanup does, for its confirm dialog.
const _cleanupWhat = {
  'journal': 'Trims the system logs (journald) to the last 14 days and at most 300 MB.',
  'docker': 'Runs "docker system prune": removes stopped containers, unused networks, dangling images and the '
      'build cache. It never removes volumes, so app data stays.',
  'pm2_logs': 'Empties the pm2 app log files ("pm2 flush"). Running apps keep running.',
  'dnf_cache': 'Deletes downloaded package files ("dnf clean packages"). They are fetched again when needed.',
  'trash': "Permanently deletes everything in Willy's trash (~/.willy-trash). This can't be undone.",
};

class _ServerStorageSectionState extends State<ServerStorageSection> {
  ServerStorage? _info;
  String? _error;
  bool _loading = false;
  bool _loaded = false;
  DateTime? _at;
  final Set<String> _cleaning = {};

  bool get _online => widget.device.online;

  @override
  void initState() {
    super.initState();
    if (widget.active && _online) _load();
  }

  @override
  void didUpdateWidget(ServerStorageSection old) {
    super.didUpdateWidget(old);
    if (widget.active && _online && !_loaded && !_loading) _load();
  }

  Future<void> _load() async {
    if (_loading) return;
    if (!_online) {
      setState(() => _error = 'The server agent is offline.');
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    final res = await ApiService.serverAction(widget.device.id, 'storage');
    if (!mounted) return;
    setState(() {
      _loading = false;
      _loaded = true;
      if (res['success'] == true) {
        _info = ServerStorage.fromJson(res);
        _at = DateTime.now();
      } else {
        _error = serverErrorText(res, "Couldn't read the disks.");
      }
    });
  }

  Future<void> _clean(CleanableItem item) async {
    final ok = await confirmAction(
      context,
      title: 'Clean ${item.label.toLowerCase()}?',
      message: '${_cleanupWhat[item.key] ?? 'Clears ${item.label}.'}\n\nNow: ${item.sizeText}.',
      confirmLabel: 'Clean',
      color: item.key == 'trash' ? WillyColors.red : WillyColors.amber,
    );
    if (!ok || !mounted) return;
    setState(() => _cleaning.add(item.key));
    final res = await ApiService.serverAction(widget.device.id, 'cleanup', {'what': item.key});
    if (!mounted) return;
    setState(() => _cleaning.remove(item.key));
    final success = res['success'] == true;
    showWillySnack(
      context,
      success ? (res['message']?.toString() ?? 'Cleaned up.') : serverErrorText(res, 'The cleanup failed.'),
      error: !success,
    );
    final out = res['output']?.toString() ?? '';
    if (!success && out.trim().isNotEmpty) {
      await showOutputSheet(context, title: 'Cleanup · ${item.key}', text: out, error: true);
    }
    if (success) _load();
  }

  @override
  Widget build(BuildContext context) {
    final info = _info;
    return RefreshIndicator(
      onRefresh: _load,
      color: WillyColors.cyan,
      backgroundColor: WillyColors.cardAlt,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 28),
        children: [
          ServerSection(
            title: 'Disks',
            icon: Icons.storage_rounded,
            trailing: _loading
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : IconButton(
                    tooltip: 'Refresh',
                    visualDensity: VisualDensity.compact,
                    onPressed: _online ? _load : null,
                    icon: const Icon(Icons.refresh_rounded, size: 20),
                  ),
            children: [
              if (!_online && info == null)
                const ServerNote('The server agent is offline.', icon: Icons.cloud_off_rounded)
              else if (_loading && info == null)
                const ServerNote('Measuring the disks and the biggest folders… this takes up to a minute.', busy: true),
              if (_error != null && _online) ServerNote.error(_error!),
              if (info != null) ...[
                if (info.message.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: Text(info.message, style: const TextStyle(color: WillyColors.textSoft, fontSize: 13)),
                  ),
                for (final m in info.mounts) ...[
                  StatBar(
                    label: m.mount,
                    pct: m.pct,
                    caption: '${formatGb(m.usedGb)} of ${formatGb(m.totalGb)} · ${formatGb(m.freeGb)} free',
                  ),
                  const SizedBox(height: 10),
                ],
                StatBar(
                  label: 'Swap',
                  pct: info.swapPct,
                  caption: info.swapTotalGb == null || info.swapTotalGb == 0
                      ? 'no swap'
                      : '${formatGb(info.swapUsedGb)} of ${formatGb(info.swapTotalGb)}',
                ),
                if (_at != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text('Measured ${TimeOfDay.fromDateTime(_at!).format(context)}',
                        style: const TextStyle(color: WillyColors.faint, fontSize: 11)),
                  ),
              ],
            ],
          ),
          if (info != null) _cleanCard(info),
          if (info != null) _biggestCard(info),
        ],
      ),
    );
  }

  Widget _cleanCard(ServerStorage info) {
    return ServerSection(
      title: 'Clean up',
      icon: Icons.cleaning_services_outlined,
      children: [
        if (info.cleanable.isEmpty)
          const Text('Nothing to clean.', style: TextStyle(color: WillyColors.faint, fontSize: 13)),
        for (final c in info.cleanable)
          Container(
            margin: const EdgeInsets.only(top: 8),
            padding: const EdgeInsets.fromLTRB(12, 8, 4, 8),
            decoration: BoxDecoration(
              color: WillyColors.card,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: WillyColors.border),
            ),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(c.label, style: const TextStyle(color: WillyColors.text, fontSize: 13, fontWeight: FontWeight.w600)),
                      const SizedBox(height: 2),
                      Text(c.sizeText,
                          maxLines: 3,
                          overflow: TextOverflow.ellipsis,
                          style: monoStyle.copyWith(fontSize: 11, color: WillyColors.muted)),
                    ],
                  ),
                ),
                SmallAction(
                  label: 'Clean',
                  icon: Icons.delete_sweep_outlined,
                  color: c.key == 'trash' ? WillyColors.red : WillyColors.amber,
                  busy: _cleaning.contains(c.key),
                  onPressed: _online && (c.gb == null || c.gb! > 0 || c.text != null) ? () => _clean(c) : null,
                ),
              ],
            ),
          ),
      ],
    );
  }

  Widget _biggestCard(ServerStorage info) {
    final maxGb = info.biggest.fold<double>(0.001, (m, f) => f.gb > m ? f.gb : m);
    return ServerSection(
      title: 'Biggest folders',
      icon: Icons.folder_rounded,
      children: [
        if (info.biggest.isEmpty)
          const Text('No folder sizes reported.', style: TextStyle(color: WillyColors.faint, fontSize: 13)),
        for (final f in info.biggest)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(f.path,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: monoStyle.copyWith(fontSize: 12, color: WillyColors.textSoft)),
                    ),
                    const SizedBox(width: 8),
                    Text(formatGb(f.gb),
                        style: const TextStyle(color: WillyColors.amber, fontSize: 12, fontWeight: FontWeight.w700)),
                  ],
                ),
                const SizedBox(height: 4),
                ClipRRect(
                  borderRadius: BorderRadius.circular(2),
                  child: LinearProgressIndicator(
                    value: (f.gb / maxGb).clamp(0.0, 1.0),
                    minHeight: 3,
                    backgroundColor: WillyColors.border,
                    color: WillyColors.amber,
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}
