import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/server.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import '../call_panels/panel_host.dart' show panelErrorText;

/// Monospace text style for command output and logs.
const monoStyle = TextStyle(fontFamily: 'monospace', fontSize: 12, height: 1.35, color: WillyColors.textSoft);

Color healthColor(UnitHealth h) => switch (h) {
      UnitHealth.good => WillyColors.green,
      UnitHealth.warn => WillyColors.amber,
      UnitHealth.bad => WillyColors.red,
      UnitHealth.idle => WillyColors.faint,
    };

/// "0.52 · 0.40 · 0.31"
String formatLoad(double? l1, double? l5, double? l15) {
  String f(double? v) => v == null ? '—' : v.toStringAsFixed(2);
  if (l1 == null && l5 == null && l15 == null) return '—';
  return '${f(l1)} · ${f(l5)} · ${f(l15)}';
}

/// "820 MB", "12.4 GB".
String formatMb(double? mb) {
  if (mb == null) return '—';
  if (mb < 1024) return '${mb.round()} MB';
  return '${(mb / 1024).toStringAsFixed(1)} GB';
}

/// Small coloured state label ("online", "failed", "running").
class StateChip extends StatelessWidget {
  final String text;
  final Color color;

  const StateChip({super.key, required this.text, required this.color});

  factory StateChip.of(String state) => StateChip(text: state, color: healthColor(unitHealth(state)));

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.13),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withValues(alpha: 0.4)),
      ),
      child: Text(text,
          style: TextStyle(color: color, fontSize: 11, fontWeight: FontWeight.w700, letterSpacing: 0.2)),
    );
  }
}

/// Labelled usage bar: "CPU ······ 42%  4 cores".
class StatBar extends StatelessWidget {
  final String label;
  final double? pct;
  final String? caption;

  const StatBar({super.key, required this.label, required this.pct, this.caption});

  @override
  Widget build(BuildContext context) {
    final v = pct;
    final color = WillyColors.load(v);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 170),
              child: Text(label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: WillyColors.muted, fontSize: 12, fontWeight: FontWeight.w600)),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                caption ?? '',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(color: WillyColors.faint, fontSize: 11),
              ),
            ),
            Text(v == null ? '—' : '${v.round()}%',
                style: TextStyle(color: v == null ? WillyColors.faint : color, fontSize: 12.5, fontWeight: FontWeight.w700)),
          ],
        ),
        const SizedBox(height: 5),
        ClipRRect(
          borderRadius: BorderRadius.circular(4),
          child: LinearProgressIndicator(
            value: v == null ? 0 : (v / 100).clamp(0.0, 1.0),
            minHeight: 7,
            backgroundColor: WillyColors.border,
            color: color,
          ),
        ),
      ],
    );
  }
}

/// A titled block inside the Server screen.
class ServerSection extends StatelessWidget {
  final String title;
  final IconData icon;
  final Widget? trailing;
  final List<Widget> children;

  const ServerSection({super.key, required this.title, required this.icon, this.trailing, required this.children});

  @override
  Widget build(BuildContext context) {
    return WillyCard(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [SectionTitle(title, icon: icon, trailing: trailing), ...children],
      ),
    );
  }
}

/// The error of a server action result, worded for the server.
String serverErrorText(Map<String, dynamic> res, String fallback) {
  return switch (res['error']?.toString()) {
    'TIMEOUT' => "The server didn't answer in time.",
    'DEVICE_OFFLINE' => 'The server agent is offline.',
    'SEND_FAILED' || 'DISCONNECTED' => "Couldn't reach the hub.",
    _ => panelErrorText(res, fallback),
  };
}

/// A one-line note inside a section: a spinner while working, else an icon.
class ServerNote extends StatelessWidget {
  final String text;
  final Color color;
  final IconData? icon;
  final bool busy;

  const ServerNote(this.text, {super.key, this.color = WillyColors.muted, this.icon, this.busy = false});

  const ServerNote.error(this.text, {super.key})
      : color = const Color(0xFFFCA5A5),
        icon = Icons.error_outline_rounded,
        busy = false;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (busy)
            const Padding(
              padding: EdgeInsets.only(top: 2, right: 10),
              child: SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
            )
          else if (icon != null)
            Padding(padding: const EdgeInsets.only(top: 1, right: 8), child: Icon(icon, size: 16, color: color)),
          Expanded(child: Text(text, style: TextStyle(color: color, fontSize: 12.5, height: 1.35))),
        ],
      ),
    );
  }
}

/// Compact text button used in rows of server actions.
class SmallAction extends StatelessWidget {
  final String label;
  final IconData icon;
  final VoidCallback? onPressed;
  final Color color;
  final bool busy;

  const SmallAction(
      {super.key, required this.label, required this.icon, this.onPressed, this.color = WillyColors.textSoft, this.busy = false});

  @override
  Widget build(BuildContext context) {
    return TextButton.icon(
      style: TextButton.styleFrom(
        foregroundColor: color,
        visualDensity: VisualDensity.compact,
        padding: const EdgeInsets.symmetric(horizontal: 8),
        minimumSize: const Size(0, 34),
      ),
      onPressed: busy ? null : onPressed,
      icon: busy
          ? SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: color))
          : Icon(icon, size: 16),
      label: Text(label, style: const TextStyle(fontSize: 12.5)),
    );
  }
}

/// "Label ........ value" row.
class KeyValueRow extends StatelessWidget {
  final String label;
  final Widget value;

  const KeyValueRow(this.label, this.value, {super.key});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Expanded(child: Text(label, style: const TextStyle(color: WillyColors.muted, fontSize: 13))),
          const SizedBox(width: 8),
          Flexible(child: Align(alignment: Alignment.centerRight, child: value)),
        ],
      ),
    );
  }
}

/// A confirm dialog that only enables its button once the user ticks [ackLabel]: for
/// changes that take the server (and everything on it) down.
Future<bool> confirmStrong(
  BuildContext context, {
  required String title,
  required String message,
  required String ackLabel,
  required String confirmLabel,
}) async {
  var ack = false;
  final result = await showDialog<bool>(
    context: context,
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setLocal) => AlertDialog(
        title: Row(
          children: [
            const Icon(Icons.warning_amber_rounded, color: WillyColors.red),
            const SizedBox(width: 8),
            Expanded(child: Text(title, style: const TextStyle(color: WillyColors.text, fontSize: 18))),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(message, style: const TextStyle(color: WillyColors.muted)),
            const SizedBox(height: 10),
            CheckboxListTile(
              value: ack,
              onChanged: (v) => setLocal(() => ack = v == true),
              contentPadding: EdgeInsets.zero,
              dense: true,
              controlAffinity: ListTileControlAffinity.leading,
              activeColor: WillyColors.red,
              title: Text(ackLabel, style: const TextStyle(color: WillyColors.textSoft, fontSize: 13)),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: WillyColors.red, foregroundColor: Colors.white),
            onPressed: ack ? () => Navigator.pop(ctx, true) : null,
            child: Text(confirmLabel),
          ),
        ],
      ),
    ),
  );
  return result == true;
}

/// Scrollable monospace output (logs, command output) in a bottom sheet, with copy.
Future<void> showOutputSheet(
  BuildContext context, {
  required String title,
  required String text,
  String? subtitle,
  bool error = false,
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    backgroundColor: WillyColors.bgElevated,
    showDragHandle: true,
    builder: (ctx) => DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.75,
      minChildSize: 0.35,
      maxChildSize: 0.95,
      builder: (ctx, scroll) => Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 8, 6),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(color: WillyColors.text, fontSize: 16, fontWeight: FontWeight.w700)),
                      if (subtitle != null)
                        Text(subtitle, style: const TextStyle(color: WillyColors.muted, fontSize: 12)),
                    ],
                  ),
                ),
                IconButton(
                  tooltip: 'Copy',
                  icon: const Icon(Icons.copy_rounded, color: WillyColors.textSoft),
                  onPressed: () {
                    Clipboard.setData(ClipboardData(text: text));
                    showWillySnack(ctx, 'Copied');
                  },
                ),
              ],
            ),
          ),
          const Divider(height: 1, color: WillyColors.border),
          Expanded(
            child: SingleChildScrollView(
              controller: scroll,
              padding: const EdgeInsets.all(14),
              child: SelectableText(
                text.trim().isEmpty ? '(no output)' : text,
                style: error ? monoStyle.copyWith(color: const Color(0xFFFCA5A5)) : monoStyle,
              ),
            ),
          ),
        ],
      ),
    ),
  );
}
