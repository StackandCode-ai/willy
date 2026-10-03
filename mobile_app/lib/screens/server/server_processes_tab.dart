import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/device.dart';
import '../../models/server.dart';
import '../../services/api_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import '../call_panels/panel_host.dart' show panelErrorText;
import 'server_widgets.dart';

/// The server's 15 busiest processes, by memory or CPU (`list_processes`). Read-only.
class ServerProcessesTab extends StatefulWidget {
  final WillyDevice device;
  final bool active;

  const ServerProcessesTab({super.key, required this.device, this.active = true});

  @override
  State<ServerProcessesTab> createState() => _ServerProcessesTabState();
}

class _ServerProcessesTabState extends State<ServerProcessesTab> {
  List<ServerProcess> _procs = const [];
  String _sort = 'memory';
  bool _loading = false;
  bool _loaded = false;
  String? _error;
  int _serial = 0;
  DateTime? _at;

  @override
  void initState() {
    super.initState();
    if (widget.active) _refresh();
  }

  @override
  void didUpdateWidget(ServerProcessesTab old) {
    super.didUpdateWidget(old);
    if (widget.active && !_loaded && !_loading) _refresh();
  }

  Future<void> _refresh() async {
    final serial = ++_serial;
    setState(() {
      _loading = true;
      _error = null;
    });
    final res = await ApiService.deviceAction(
        widget.device.id, 'list_processes', {'limit': 15, 'sort': _sort}, const Duration(seconds: 25));
    if (!mounted || serial != _serial) return;
    setState(() {
      _loading = false;
      _loaded = true;
      if (res['success'] == true && res['processes'] is List) {
        _procs = ServerProcess.listFrom(res['processes']);
        _at = DateTime.now();
      } else {
        _error = panelErrorText(res, "Couldn't list the processes.");
      }
    });
  }

  void _details(ServerProcess p) {
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(p.name, style: const TextStyle(color: WillyColors.text, fontSize: 18)),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('PID ${p.pid}${p.user != null ? ' · ${p.user}' : ''}',
                style: const TextStyle(color: WillyColors.muted, fontSize: 13)),
            Text('CPU ${p.cpu.toStringAsFixed(1)}% · ${formatMb(p.memoryMb)}',
                style: const TextStyle(color: WillyColors.muted, fontSize: 13)),
            const SizedBox(height: 10),
            SelectableText(p.command.isEmpty ? '(no command line)' : p.command, style: monoStyle),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () {
              Clipboard.setData(ClipboardData(text: p.command.isEmpty ? p.name : p.command));
              Navigator.pop(ctx);
              showWillySnack(context, 'Copied');
            },
            child: const Text('Copy command'),
          ),
          FilledButton(onPressed: () => Navigator.pop(ctx), child: const Text('Close')),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final byMem = _sort == 'memory';
    final maxVal = _procs.fold<double>(0.0001, (m, p) {
      final v = byMem ? p.memoryMb : p.cpu;
      return v > m ? v : m;
    });
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 8, 6),
          child: Row(
            children: [
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(value: 'memory', label: Text('Memory')),
                  ButtonSegment(value: 'cpu', label: Text('CPU')),
                ],
                selected: {_sort},
                showSelectedIcon: false,
                style: const ButtonStyle(visualDensity: VisualDensity.compact),
                onSelectionChanged: (s) {
                  setState(() => _sort = s.first);
                  _refresh();
                },
              ),
              const Spacer(),
              if (_at != null)
                Text(TimeOfDay.fromDateTime(_at!).format(context),
                    style: const TextStyle(color: WillyColors.faint, fontSize: 11)),
              IconButton(
                tooltip: 'Refresh',
                onPressed: _loading ? null : _refresh,
                icon: _loading
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.refresh_rounded),
              ),
            ],
          ),
        ),
        Expanded(
          child: RefreshIndicator(
            onRefresh: _refresh,
            color: WillyColors.cyan,
            backgroundColor: WillyColors.cardAlt,
            child: _list(byMem, maxVal),
          ),
        ),
      ],
    );
  }

  Widget _list(bool byMem, double maxVal) {
    if (_error != null && _procs.isEmpty) {
      return ListView(children: [
        Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            children: [
              const Icon(Icons.error_outline_rounded, size: 40, color: WillyColors.red),
              const SizedBox(height: 10),
              Text(_error!, textAlign: TextAlign.center, style: const TextStyle(color: WillyColors.muted)),
            ],
          ),
        ),
      ]);
    }
    if (_procs.isEmpty) {
      return ListView(children: [
        Padding(
          padding: const EdgeInsets.all(32),
          child: Center(
            child: _loading
                ? const CircularProgressIndicator()
                : const Text('No processes yet.', style: TextStyle(color: WillyColors.muted)),
          ),
        ),
      ]);
    }
    return ListView.separated(
      padding: const EdgeInsets.fromLTRB(12, 0, 12, 24),
      itemCount: _procs.length + (_error != null ? 1 : 0),
      separatorBuilder: (context, i) => const SizedBox(height: 6),
      itemBuilder: (context, i) {
        if (i == _procs.length) {
          return Text(_error!, style: const TextStyle(color: WillyColors.red, fontSize: 12));
        }
        final p = _procs[i];
        final share = ((byMem ? p.memoryMb : p.cpu) / maxVal).clamp(0.0, 1.0);
        return Material(
          color: WillyColors.card,
          borderRadius: BorderRadius.circular(12),
          child: InkWell(
            borderRadius: BorderRadius.circular(12),
            onTap: () => _details(p),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 9, 12, 9),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(p.name,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(color: WillyColors.text, fontWeight: FontWeight.w600)),
                      ),
                      Text('${p.cpu.toStringAsFixed(1)}%',
                          style: TextStyle(
                              color: byMem ? WillyColors.muted : WillyColors.cyan,
                              fontSize: 12.5,
                              fontWeight: FontWeight.w700)),
                      const SizedBox(width: 12),
                      SizedBox(
                        width: 66,
                        child: Text(formatMb(p.memoryMb),
                            textAlign: TextAlign.right,
                            style: TextStyle(
                                color: byMem ? WillyColors.purple : WillyColors.muted,
                                fontSize: 12.5,
                                fontWeight: FontWeight.w700)),
                      ),
                    ],
                  ),
                  const SizedBox(height: 2),
                  Text(
                    ['PID ${p.pid}', if (p.user != null) p.user!, if (p.command.isNotEmpty) p.command].join(' · '),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: monoStyle.copyWith(fontSize: 11, color: WillyColors.faint),
                  ),
                  const SizedBox(height: 6),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(2),
                    child: LinearProgressIndicator(
                      value: share,
                      minHeight: 3,
                      backgroundColor: WillyColors.border,
                      color: byMem ? WillyColors.purple : WillyColors.cyan,
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}
