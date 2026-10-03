import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/device.dart';
import '../../models/server.dart';
import '../../services/api_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import 'server_widgets.dart';

/// The system journal (`journal`), filtered by unit, level, time and a search word.
class ServerLogsSection extends StatefulWidget {
  final WillyDevice device;
  final bool active;

  const ServerLogsSection({super.key, required this.device, this.active = true});

  @override
  State<ServerLogsSection> createState() => _ServerLogsSectionState();
}

class _ServerLogsSectionState extends State<ServerLogsSection> {
  static const _lineChoices = [100, 200, 500];
  static final _unitPattern = RegExp(r'^[A-Za-z0-9_.@-]*$');

  final _unit = TextEditingController();
  final _grep = TextEditingController();
  String _priority = 'warning';
  String _since = '1 hour ago';
  int _lines = 200;
  String? _logs;
  String? _error;
  String _summary = '';
  bool _loading = false;
  bool _loaded = false;
  int _serial = 0;

  bool get _online => widget.device.online;

  @override
  void initState() {
    super.initState();
    if (widget.active && _online) _load();
  }

  @override
  void didUpdateWidget(ServerLogsSection old) {
    super.didUpdateWidget(old);
    if (widget.active && _online && !_loaded && !_loading) _load();
  }

  @override
  void dispose() {
    _unit.dispose();
    _grep.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final unit = _unit.text.trim();
    if (!_unitPattern.hasMatch(unit)) {
      setState(() => _error = 'A unit name has only letters, digits and . _ @ -');
      return;
    }
    FocusManager.instance.primaryFocus?.unfocus();
    final serial = ++_serial;
    setState(() {
      _loading = true;
      _loaded = true;
      _error = null;
    });
    final res = await ApiService.serverAction(widget.device.id, 'journal', {
      if (unit.isNotEmpty) 'unit': unit,
      if (_priority.isNotEmpty) 'priority': _priority,
      if (_since.isNotEmpty) 'since': _since,
      if (_grep.text.trim().isNotEmpty) 'grep': _grep.text.trim(),
      'lines': _lines,
    });
    if (!mounted || serial != _serial) return;
    setState(() {
      _loading = false;
      if (res['success'] == true) {
        _logs = res['logs']?.toString() ?? '';
        _summary = res['message']?.toString() ?? '';
      } else {
        _error = serverErrorText(res, "Couldn't read the logs.");
      }
    });
  }

  Widget _dropdown<T>(String label, T value, List<(T, String)> items, ValueChanged<T> onChanged) {
    return DropdownButtonFormField<T>(
      initialValue: value,
      isExpanded: true,
      isDense: true,
      decoration: InputDecoration(labelText: label, isDense: true),
      dropdownColor: WillyColors.cardAlt,
      items: [
        for (final (v, l) in items)
          DropdownMenuItem(value: v, child: Text(l, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13.5))),
      ],
      onChanged: (v) {
        if (v != null) onChanged(v);
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final logs = _logs;
    final lineCount = logs == null || logs.trim().isEmpty ? 0 : '\n'.allMatches(logs.trimRight()).length + 1;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 28),
      children: [
        ServerSection(
          title: 'System logs',
          icon: Icons.receipt_long_rounded,
          children: [
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _unit,
                    autocorrect: false,
                    textInputAction: TextInputAction.search,
                    onSubmitted: (_) => _load(),
                    style: const TextStyle(fontSize: 14),
                    decoration: const InputDecoration(isDense: true, labelText: 'Unit', hintText: 'all, or e.g. nginx'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: TextField(
                    controller: _grep,
                    autocorrect: false,
                    textInputAction: TextInputAction.search,
                    onSubmitted: (_) => _load(),
                    style: const TextStyle(fontSize: 14),
                    decoration: const InputDecoration(isDense: true, labelText: 'Search', hintText: 'word or regex'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: _dropdown<String>('Level', _priority, journalPriorities, (v) => setState(() => _priority = v)),
                ),
                const SizedBox(width: 8),
                Expanded(child: _dropdown<String>('Since', _since, journalSince, (v) => setState(() => _since = v))),
              ],
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: _dropdown<int>('Lines', _lines, [for (final n in _lineChoices) (n, 'Last $n')],
                      (v) => setState(() => _lines = v)),
                ),
                const SizedBox(width: 12),
                Flexible(
                  child: FilledButton.icon(
                    onPressed: _online && !_loading ? _load : null,
                    icon: _loading
                        ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.search_rounded, size: 18),
                    label: const Text('Show logs', maxLines: 1, overflow: TextOverflow.ellipsis),
                  ),
                ),
              ],
            ),
            if (!_online) const ServerNote('The server agent is offline.', icon: Icons.cloud_off_rounded),
            if (_error != null) ServerNote.error(_error!),
          ],
        ),
        if (logs != null)
          ServerSection(
            title: lineCount == 0 ? 'No matching lines' : '$lineCount lines',
            icon: Icons.notes_rounded,
            trailing: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                IconButton(
                  tooltip: 'Copy',
                  visualDensity: VisualDensity.compact,
                  onPressed: logs.isEmpty
                      ? null
                      : () {
                          Clipboard.setData(ClipboardData(text: logs));
                          showWillySnack(context, 'Copied');
                        },
                  icon: const Icon(Icons.copy_rounded, size: 18),
                ),
                IconButton(
                  tooltip: 'Full screen',
                  visualDensity: VisualDensity.compact,
                  onPressed: () => showOutputSheet(context,
                      title: _unit.text.trim().isEmpty ? 'System logs' : 'Logs · ${_unit.text.trim()}',
                      subtitle: _summary.isEmpty ? null : _summary,
                      text: logs),
                  icon: const Icon(Icons.open_in_full_rounded, size: 18),
                ),
              ],
            ),
            children: [
              if (lineCount == 0)
                const Text('Nothing logged for these filters. Try a lower level or a longer time.',
                    style: TextStyle(color: WillyColors.faint, fontSize: 12.5))
              else
                Container(
                  constraints: const BoxConstraints(maxHeight: 460),
                  decoration: BoxDecoration(
                    color: const Color(0xFF03050B),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: WillyColors.border),
                  ),
                  child: Scrollbar(
                    child: SingleChildScrollView(
                      padding: const EdgeInsets.all(10),
                      child: SelectableText(logs, style: monoStyle.copyWith(fontSize: 10.5)),
                    ),
                  ),
                ),
            ],
          ),
      ],
    );
  }
}
