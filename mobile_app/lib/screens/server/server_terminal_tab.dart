import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/device.dart';
import '../../models/server.dart';
import '../../services/api_service.dart';
import '../../services/settings_store.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import '../call_panels/panel_host.dart' show panelErrorText;
import 'server_widgets.dart';

/// One command run on the server and what it printed.
class _Run {
  final String command;
  final String? cwd;
  bool running = true;
  int? exitCode;
  double? seconds;
  String stdout = '';
  String stderr = '';
  String? error;

  _Run(this.command, this.cwd);
}

/// A small terminal for the Willy server (`run_shell`). The user typed the command, so
/// pressing Run is the confirmation. Output is monospace and selectable; recent commands
/// are kept in the settings file.
class ServerTerminalTab extends StatefulWidget {
  final WillyDevice device;
  final bool active;

  const ServerTerminalTab({super.key, required this.device, this.active = true});

  @override
  State<ServerTerminalTab> createState() => _ServerTerminalTabState();
}

class _ServerTerminalTabState extends State<ServerTerminalTab> {
  static const _quick = ['df -h', 'free -m', 'uptime', 'pm2 ls', 'docker ps', 'systemctl --failed', 'ls -la'];
  static const _timeoutSec = 60;

  final _input = TextEditingController();
  final _scroll = ScrollController();
  final List<_Run> _runs = [];
  String? _cwd; // null = the server user's home folder

  bool get _running => _runs.isNotEmpty && _runs.last.running;

  @override
  void dispose() {
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _toEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(_scroll.position.maxScrollExtent,
            duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
      }
    });
  }

  Future<void> _run() async {
    final command = _input.text.trim();
    if (command.isEmpty || _running) return;
    HapticFeedback.selectionClick();
    final run = _Run(command, _cwd);
    setState(() {
      _runs.add(run);
      if (_runs.length > 40) _runs.removeAt(0);
      _input.clear();
      WillySettings.serverShellHistory = pushCommandHistory(WillySettings.serverShellHistory, command);
    });
    WillySettings.save();
    _toEnd();
    final res = await ApiService.deviceAction(
      widget.device.id,
      'run_shell',
      {'command': command, if (run.cwd != null) 'cwd': run.cwd, 'timeout': _timeoutSec},
      const Duration(seconds: _timeoutSec + 20),
    );
    if (!mounted) return;
    setState(() {
      run.running = false;
      run.exitCode = asInt(res['exit_code']);
      run.seconds = asDouble(res['duration_sec']);
      run.stdout = res['stdout']?.toString() ?? '';
      run.stderr = res['stderr']?.toString() ?? '';
      if (run.exitCode == null && res['success'] != true) {
        run.error = panelErrorText(res, "The command didn't run.");
      }
    });
    _toEnd();
  }

  Future<void> _pickCwd() async {
    final controller = TextEditingController(text: _cwd ?? '');
    final value = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Working folder', style: TextStyle(color: WillyColors.text, fontSize: 18)),
        content: TextField(
          controller: controller,
          autofocus: true,
          style: monoStyle.copyWith(fontSize: 14),
          decoration: const InputDecoration(hintText: '/var/www  (empty = home folder)'),
          onSubmitted: (v) => Navigator.pop(ctx, v),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, controller.text), child: const Text('Use')),
        ],
      ),
    );
    controller.dispose();
    if (value == null || !mounted) return;
    setState(() => _cwd = value.trim().isEmpty ? null : value.trim());
  }

  Future<void> _history() async {
    final history = WillySettings.serverShellHistory;
    if (history.isEmpty) {
      showWillySnack(context, 'No commands yet.');
      return;
    }
    final picked = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: WillyColors.card,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 8, 4),
              child: Row(
                children: [
                  const Expanded(
                    child: Text('Recent commands',
                        style: TextStyle(color: WillyColors.text, fontSize: 16, fontWeight: FontWeight.w700)),
                  ),
                  TextButton(
                    onPressed: () {
                      WillySettings.serverShellHistory = [];
                      WillySettings.save();
                      Navigator.pop(ctx);
                    },
                    child: const Text('Clear'),
                  ),
                ],
              ),
            ),
            Flexible(
              child: ListView(
                shrinkWrap: true,
                children: [
                  for (final c in history)
                    ListTile(
                      dense: true,
                      leading: const Icon(Icons.history_rounded, size: 18, color: WillyColors.muted),
                      title: Text(c, maxLines: 2, overflow: TextOverflow.ellipsis, style: monoStyle.copyWith(fontSize: 13)),
                      onTap: () => Navigator.pop(ctx, c),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
    if (picked == null || !mounted) return;
    _setInput(picked);
  }

  void _setInput(String text) {
    _input.text = text;
    _input.selection = TextSelection.collapsed(offset: text.length);
  }

  @override
  Widget build(BuildContext context) {
    final online = widget.device.online;
    return Column(
      children: [
        Expanded(
          child: _runs.isEmpty
              ? _empty()
              : ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
                  itemCount: _runs.length,
                  itemBuilder: (context, i) => _runTile(_runs[i]),
                ),
        ),
        Container(
          decoration: const BoxDecoration(
            color: WillyColors.bgElevated,
            border: Border(top: BorderSide(color: WillyColors.border)),
          ),
          child: SafeArea(
            top: false,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                SizedBox(
                  height: 42,
                  child: ListView(
                    scrollDirection: Axis.horizontal,
                    padding: const EdgeInsets.fromLTRB(10, 6, 10, 0),
                    children: [
                      ActionChip(
                        visualDensity: VisualDensity.compact,
                        avatar: const Icon(Icons.folder_open_rounded, size: 15, color: WillyColors.amber),
                        label: Text(_cwd ?? '~', style: monoStyle.copyWith(fontSize: 12)),
                        tooltip: 'Working folder',
                        backgroundColor: WillyColors.card,
                        side: const BorderSide(color: WillyColors.border),
                        onPressed: _pickCwd,
                      ),
                      for (final q in _quick) ...[
                        const SizedBox(width: 6),
                        ActionChip(
                          visualDensity: VisualDensity.compact,
                          label: Text(q, style: monoStyle.copyWith(fontSize: 12)),
                          backgroundColor: WillyColors.card,
                          side: const BorderSide(color: WillyColors.border),
                          onPressed: () => _setInput(q),
                        ),
                      ],
                    ],
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(8, 6, 8, 8),
                  child: Row(
                    children: [
                      IconButton(
                        tooltip: 'Recent commands',
                        onPressed: _history,
                        icon: const Icon(Icons.history_rounded, color: WillyColors.textSoft),
                      ),
                      Expanded(
                        child: TextField(
                          controller: _input,
                          enabled: online,
                          autocorrect: false,
                          enableSuggestions: false,
                          textCapitalization: TextCapitalization.none,
                          keyboardType: TextInputType.visiblePassword, // no autocorrect / capitals
                          textInputAction: TextInputAction.go,
                          style: monoStyle.copyWith(fontSize: 14, color: WillyColors.text),
                          onSubmitted: (_) => _run(),
                          decoration: InputDecoration(
                            isDense: true,
                            prefixText: r'$ ',
                            prefixStyle: monoStyle.copyWith(fontSize: 14, color: WillyColors.green),
                            hintText: online ? 'Command, e.g. df -h' : 'Server offline',
                            contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      ValueListenableBuilder<TextEditingValue>(
                        valueListenable: _input,
                        builder: (context, value, _) => FilledButton.icon(
                          onPressed: online && !_running && value.text.trim().isNotEmpty ? _run : null,
                          icon: _running
                              ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                              : const Icon(Icons.play_arrow_rounded),
                          label: const Text('Run'),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _empty() => const Center(
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.terminal_rounded, size: 52, color: WillyColors.borderStrong),
              SizedBox(height: 12),
              Text('Run a command on the server',
                  style: TextStyle(color: WillyColors.text, fontSize: 16, fontWeight: FontWeight.w700)),
              SizedBox(height: 6),
              Text(
                'It runs in a shell as the server user, from the home folder unless you pick another. '
                'Commands stop after 60 seconds.',
                textAlign: TextAlign.center,
                style: TextStyle(color: WillyColors.muted, fontSize: 12.5),
              ),
            ],
          ),
        ),
      );

  Widget _runTile(_Run r) {
    final ok = r.exitCode == 0;
    final Color chipColor = r.running ? WillyColors.cyan : (ok ? WillyColors.green : WillyColors.red);
    final chipText = r.running
        ? 'running'
        : r.exitCode != null
            ? 'exit ${r.exitCode}'
            : 'failed';
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
        color: const Color(0xFF05070D),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: WillyColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 4, 6),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text.rich(
                        TextSpan(children: [
                          TextSpan(text: r'$ ', style: monoStyle.copyWith(color: WillyColors.green)),
                          TextSpan(text: r.command, style: monoStyle.copyWith(color: WillyColors.text)),
                        ]),
                      ),
                      if (r.cwd != null) Text('in ${r.cwd}', style: monoStyle.copyWith(fontSize: 10.5, color: WillyColors.faint)),
                    ],
                  ),
                ),
                StateChip(text: chipText, color: chipColor),
                if (r.seconds != null)
                  Padding(
                    padding: const EdgeInsets.only(left: 6),
                    child: Text('${r.seconds!.toStringAsFixed(r.seconds! < 10 ? 2 : 1)}s',
                        style: const TextStyle(color: WillyColors.faint, fontSize: 11)),
                  ),
                IconButton(
                  tooltip: 'Copy output',
                  visualDensity: VisualDensity.compact,
                  icon: const Icon(Icons.copy_rounded, size: 17, color: WillyColors.muted),
                  onPressed: r.running
                      ? null
                      : () {
                          Clipboard.setData(ClipboardData(
                              text: [r.stdout, r.stderr, r.error ?? ''].where((s) => s.isNotEmpty).join('\n')));
                          showWillySnack(context, 'Copied');
                        },
                ),
                IconButton(
                  tooltip: 'Edit and run again',
                  visualDensity: VisualDensity.compact,
                  icon: const Icon(Icons.replay_rounded, size: 17, color: WillyColors.muted),
                  onPressed: () => _setInput(r.command),
                ),
              ],
            ),
          ),
          if (r.running)
            const LinearProgressIndicator(minHeight: 2, color: WillyColors.cyan)
          else
            ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 360),
              child: SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(12, 0, 12, 10),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (r.stdout.isNotEmpty) SelectableText(r.stdout.trimRight(), style: monoStyle),
                    if (r.stderr.isNotEmpty)
                      SelectableText(r.stderr.trimRight(), style: monoStyle.copyWith(color: const Color(0xFFFCA5A5))),
                    if (r.error != null)
                      SelectableText(r.error!, style: monoStyle.copyWith(color: const Color(0xFFFCA5A5))),
                    if (r.stdout.isEmpty && r.stderr.isEmpty && r.error == null)
                      Text('(no output)', style: monoStyle.copyWith(color: WillyColors.faint)),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}
