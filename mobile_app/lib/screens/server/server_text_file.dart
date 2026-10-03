import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/device.dart';
import '../../services/api_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import '../call_panels/panel_host.dart' show panelErrorText, panelResultText;

/// Shows a text file on the Willy server (`manage_file` read) and lets the user edit and
/// save it (`manage_file` write with overwrite). Pops `true` after a save.
class ServerTextFile extends StatefulWidget {
  final WillyDevice device;
  final String path;
  final String name;

  const ServerTextFile({super.key, required this.device, required this.path, required this.name});

  @override
  State<ServerTextFile> createState() => _ServerTextFileState();
}

class _ServerTextFileState extends State<ServerTextFile> {
  final _text = TextEditingController();
  String _original = '';
  bool _loading = true;
  bool _truncated = false;
  bool _editing = false;
  bool _saving = false;
  bool _saved = false;
  String? _error;

  bool get _dirty => _editing && _text.text != _original;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final res = await ApiService.deviceAction(
        widget.device.id, 'manage_file', {'op': 'read', 'path': widget.path}, const Duration(seconds: 30));
    if (!mounted) return;
    setState(() {
      _loading = false;
      if (res['success'] == true && res['content'] is String) {
        _original = res['content'] as String;
        _text.text = _original;
        _truncated = res['truncated'] == true;
      } else {
        _error = panelErrorText(res, "Couldn't read ${widget.name}.");
      }
    });
  }

  Future<void> _save() async {
    final ok = await confirmAction(
      context,
      title: 'Save ${widget.name}?',
      message: 'This replaces ${widget.path} on ${widget.device.name}.',
      confirmLabel: 'Save',
      color: WillyColors.blue,
    );
    if (!ok || !mounted) return;
    setState(() => _saving = true);
    final res = await ApiService.deviceAction(
      widget.device.id,
      'manage_file',
      {'op': 'write', 'path': widget.path, 'content': _text.text, 'overwrite': true},
      const Duration(seconds: 30),
    );
    if (!mounted) return;
    final (msg, success) = panelResultText(res, ok: 'Saved ${widget.name}.');
    setState(() {
      _saving = false;
      if (success) {
        _original = _text.text;
        _editing = false;
        _saved = true;
      }
    });
    showWillySnack(context, msg, error: !success);
  }

  Future<bool> _confirmDiscard() async {
    if (!_dirty) return true;
    return confirmAction(
      context,
      title: 'Discard your changes?',
      message: "${widget.name} hasn't been saved.",
      confirmLabel: 'Discard',
    );
  }

  @override
  Widget build(BuildContext context) {
    // Always pops through here, so the caller learns about a save (and edits aren't lost by accident).
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) async {
        if (didPop) return;
        final nav = Navigator.of(context);
        if (await _confirmDiscard()) nav.pop(_saved);
      },
      child: Scaffold(
        appBar: AppBar(
          title: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(widget.name, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 16)),
              Text(widget.path,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 11, color: WillyColors.muted)),
            ],
          ),
          actions: [
            if (!_loading && _error == null && !_editing) ...[
              IconButton(
                tooltip: 'Copy',
                icon: const Icon(Icons.copy_rounded),
                onPressed: () {
                  Clipboard.setData(ClipboardData(text: _text.text));
                  showWillySnack(context, 'Copied');
                },
              ),
              IconButton(
                tooltip: _truncated ? 'Too big to edit here' : 'Edit',
                icon: const Icon(Icons.edit_rounded),
                onPressed: _truncated ? null : () => setState(() => _editing = true),
              ),
            ],
            if (_editing)
              _saving
                  ? const Padding(
                      padding: EdgeInsets.all(14),
                      child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)),
                    )
                  : IconButton(tooltip: 'Save', icon: const Icon(Icons.save_rounded), onPressed: _save),
          ],
        ),
        body: _body(),
      ),
    );
  }

  Widget _body() {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.error_outline_rounded, size: 44, color: WillyColors.red),
              const SizedBox(height: 10),
              Text(_error!, textAlign: TextAlign.center, style: const TextStyle(color: WillyColors.muted)),
              const SizedBox(height: 10),
              TextButton.icon(onPressed: _load, icon: const Icon(Icons.refresh_rounded), label: const Text('Try again')),
            ],
          ),
        ),
      );
    }
    const mono = TextStyle(fontFamily: 'monospace', fontSize: 12.5, color: WillyColors.textSoft, height: 1.4);
    return Column(
      children: [
        if (_truncated)
          Container(
            width: double.infinity,
            color: WillyColors.amber.withValues(alpha: 0.12),
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: const Text('Only the start of this file is shown, so it can\'t be edited here.',
                style: TextStyle(color: WillyColors.amber, fontSize: 12)),
          ),
        Expanded(
          child: _editing
              ? TextField(
                  controller: _text,
                  maxLines: null,
                  expands: true,
                  autofocus: true,
                  keyboardType: TextInputType.multiline,
                  textAlignVertical: TextAlignVertical.top,
                  style: mono,
                  onChanged: (_) => setState(() {}),
                  decoration: const InputDecoration(
                    border: InputBorder.none,
                    contentPadding: EdgeInsets.all(14),
                  ),
                )
              : SingleChildScrollView(
                  padding: const EdgeInsets.all(14),
                  child: SelectableText(_text.text.isEmpty ? '(empty file)' : _text.text, style: mono),
                ),
        ),
      ],
    );
  }
}
