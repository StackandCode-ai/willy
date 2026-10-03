import 'dart:async';

import 'package:flutter/material.dart';

import '../models/device.dart';
import '../services/api_service.dart';
import '../theme.dart';
import '../widgets/common.dart';

/// Live task manager for a PC: top processes refresh every 3 s; End task with confirmation.
class ProcessesScreen extends StatefulWidget {
  final WillyDevice device;

  const ProcessesScreen({super.key, required this.device});

  @override
  State<ProcessesScreen> createState() => _ProcessesScreenState();
}

class _ProcessesScreenState extends State<ProcessesScreen> {
  List<ProcessInfo> _processes = [];
  String _sortBy = 'cpu';
  String _filter = '';
  bool _loading = true;
  String? _error;
  Timer? _timer;
  final Set<int> _killing = {};

  @override
  void initState() {
    super.initState();
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) => _refresh());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    final res = await ApiService.listProcesses(widget.device.id, sortBy: _sortBy, limit: 40);
    if (!mounted) return;
    setState(() {
      _loading = false;
      if (res['success'] == true && res['processes'] is List) {
        _processes = (res['processes'] as List)
            .whereType<Map>()
            .map((m) => ProcessInfo.fromJson(Map<String, dynamic>.from(m)))
            .toList();
        _error = null;
      } else {
        _error = (res['reply'] ?? res['error'] ?? 'Could not load processes').toString();
      }
    });
  }

  Future<void> _kill(ProcessInfo p) async {
    final ok = await confirmAction(
      context,
      title: 'End ${p.name}?',
      message: 'PID ${p.pid} will be closed on ${widget.device.name}. Unsaved work in it will be lost.',
      confirmLabel: 'End task',
    );
    if (!ok || !mounted) return;
    setState(() => _killing.add(p.pid));
    final res = await ApiService.killProcess(widget.device.id, p.pid);
    if (!mounted) return;
    setState(() => _killing.remove(p.pid));
    showWillySnack(
      context,
      res['success'] == true ? 'Ended ${p.name}' : (res['error'] ?? 'Could not end ${p.name}').toString(),
      error: res['success'] != true,
    );
    _refresh();
  }

  @override
  Widget build(BuildContext context) {
    final visible = _filter.isEmpty
        ? _processes
        : _processes.where((p) => p.name.toLowerCase().contains(_filter.toLowerCase())).toList();
    final maxMem = _processes.fold<double>(1, (m, p) => p.memMb > m ? p.memMb : m);

    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Processes', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 17)),
            Text(widget.device.name, style: const TextStyle(fontSize: 11, color: WillyColors.muted)),
          ],
        ),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'cpu', label: Text('CPU')),
                ButtonSegment(value: 'memory', label: Text('RAM')),
              ],
              selected: {_sortBy},
              showSelectedIcon: false,
              style: const ButtonStyle(visualDensity: VisualDensity.compact),
              onSelectionChanged: (s) {
                setState(() => _sortBy = s.first);
                _refresh();
              },
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
            child: TextField(
              onChanged: (v) => setState(() => _filter = v.trim()),
              style: const TextStyle(color: Colors.white),
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search, color: WillyColors.faint),
                hintText: 'Filter by name…',
                isDense: true,
              ),
            ),
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
              child: Text(_error!, style: const TextStyle(color: WillyColors.red, fontSize: 12)),
            ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator(color: WillyColors.cyan))
                : RefreshIndicator(
                    onRefresh: _refresh,
                    color: WillyColors.cyan,
                    child: ListView.separated(
                      padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
                      itemCount: visible.length,
                      separatorBuilder: (_, __) => const SizedBox(height: 8),
                      itemBuilder: (context, i) {
                        final p = visible[i];
                        final cpuColor = WillyColors.load(p.cpu * 2);
                        return Container(
                          padding: const EdgeInsets.fromLTRB(14, 10, 6, 10),
                          decoration: BoxDecoration(
                            color: WillyColors.cardAlt,
                            borderRadius: BorderRadius.circular(14),
                            border: Border.all(color: WillyColors.border),
                          ),
                          child: Row(
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(p.name,
                                        maxLines: 1,
                                        overflow: TextOverflow.ellipsis,
                                        style: const TextStyle(color: WillyColors.text, fontWeight: FontWeight.w600)),
                                    const SizedBox(height: 4),
                                    Row(
                                      children: [
                                        Text('PID ${p.pid}', style: const TextStyle(color: WillyColors.faint, fontSize: 11)),
                                        if (p.threads != null)
                                          Text(' · ${p.threads} threads',
                                              style: const TextStyle(color: WillyColors.faint, fontSize: 11)),
                                      ],
                                    ),
                                    const SizedBox(height: 6),
                                    ClipRRect(
                                      borderRadius: BorderRadius.circular(3),
                                      child: LinearProgressIndicator(
                                        value: (p.memMb / maxMem).clamp(0.0, 1.0),
                                        minHeight: 4,
                                        backgroundColor: WillyColors.border,
                                        color: WillyColors.purple,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                              const SizedBox(width: 12),
                              Column(
                                crossAxisAlignment: CrossAxisAlignment.end,
                                children: [
                                  Text('${p.cpu.toStringAsFixed(1)}%',
                                      style: TextStyle(color: cpuColor, fontWeight: FontWeight.bold)),
                                  Text(
                                    p.memMb >= 1024 ? '${(p.memMb / 1024).toStringAsFixed(1)} GB' : '${p.memMb.round()} MB',
                                    style: const TextStyle(color: WillyColors.muted, fontSize: 12),
                                  ),
                                ],
                              ),
                              IconButton(
                                tooltip: 'End task',
                                icon: _killing.contains(p.pid)
                                    ? const SizedBox(
                                        width: 18,
                                        height: 18,
                                        child: CircularProgressIndicator(strokeWidth: 2, color: WillyColors.red),
                                      )
                                    : const Icon(Icons.close_rounded, color: WillyColors.red),
                                onPressed: _killing.contains(p.pid) ? null : () => _kill(p),
                              ),
                            ],
                          ),
                        );
                      },
                    ),
                  ),
          ),
        ],
      ),
    );
  }
}
