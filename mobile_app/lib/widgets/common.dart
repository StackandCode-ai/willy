import 'package:flutter/material.dart';

import '../services/api_service.dart';
import '../theme.dart';

/// Rounded dark card used across screens.
class WillyCard extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  final EdgeInsetsGeometry margin;
  final Color? borderColor;
  final Gradient? gradient;

  const WillyCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(16),
    this.margin = const EdgeInsets.only(bottom: 14),
    this.borderColor,
    this.gradient,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: margin,
      padding: padding,
      decoration: BoxDecoration(
        color: gradient == null ? WillyColors.cardAlt : null,
        gradient: gradient,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: borderColor ?? WillyColors.border),
        boxShadow: const [BoxShadow(color: Color(0x55000000), blurRadius: 16, offset: Offset(0, 6))],
      ),
      child: child,
    );
  }
}

/// Small coloured status badge ("● ONLINE").
class StatusPill extends StatelessWidget {
  final String text;
  final Color color;
  final bool dot;

  const StatusPill({super.key, required this.text, required this.color, this.dot = true});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        border: Border.all(color: color.withValues(alpha: 0.45)),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (dot) ...[
            Container(width: 6, height: 6, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
            const SizedBox(width: 6),
          ],
          Text(text, style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: color, letterSpacing: 0.3)),
        ],
      ),
    );
  }
}

/// Live hub connection indicator driven by ApiService.statusStream.
class HubStatusBadge extends StatelessWidget {
  final bool compact;

  const HubStatusBadge({super.key, this.compact = false});

  @override
  Widget build(BuildContext context) {
    return StreamBuilder<HubStatus>(
      stream: ApiService.statusStream,
      initialData: ApiService.status,
      builder: (context, snap) {
        final s = snap.data ?? ApiService.status;
        final (Color color, String text) = switch (s.state) {
          HubState.online => (
              WillyColors.green,
              s.latencyMs != null ? (compact ? '${s.latencyMs} ms' : 'LIVE · ${s.latencyMs} ms') : 'LIVE'
            ),
          HubState.connecting => (WillyColors.amber, compact ? '…' : 'CONNECTING'),
          HubState.offline => (WillyColors.red, 'OFFLINE'),
        };
        return Tooltip(
          message: s.detail ?? ApiService.baseUrl,
          child: StatusPill(text: text, color: color),
        );
      },
    );
  }
}

/// Section heading with optional trailing action.
class SectionTitle extends StatelessWidget {
  final String title;
  final IconData? icon;
  final Widget? trailing;

  const SectionTitle(this.title, {super.key, this.icon, this.trailing});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10, top: 4),
      child: Row(
        children: [
          if (icon != null) ...[Icon(icon, size: 18, color: WillyColors.cyan), const SizedBox(width: 8)],
          Expanded(
            child: Text(title,
                style: const TextStyle(color: WillyColors.text, fontSize: 16, fontWeight: FontWeight.w700)),
          ),
          if (trailing != null) trailing!,
        ],
      ),
    );
  }
}

/// Square-ish action tile used in quick-action grids.
class ActionTile extends StatelessWidget {
  final IconData icon;
  final String label;
  final Color color;
  final VoidCallback? onTap;
  final bool busy;

  const ActionTile({
    super.key,
    required this.icon,
    required this.label,
    required this.color,
    this.onTap,
    this.busy = false,
  });

  @override
  Widget build(BuildContext context) {
    final enabled = onTap != null && !busy;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: enabled ? onTap : null,
        borderRadius: BorderRadius.circular(14),
        child: Ink(
          padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 6),
          decoration: BoxDecoration(
            color: color.withValues(alpha: enabled ? 0.12 : 0.05),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: color.withValues(alpha: enabled ? 0.35 : 0.12)),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              busy
                  ? SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: color))
                  : Icon(icon, color: enabled ? color : WillyColors.faint, size: 20),
              const SizedBox(height: 6),
              Text(
                label,
                textAlign: TextAlign.center,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: enabled ? WillyColors.textSoft : WillyColors.faint,
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Compact label/value row for detail grids.
class InfoChip extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  final Color color;

  const InfoChip({super.key, required this.icon, required this.label, required this.value, this.color = WillyColors.sky});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: WillyColors.card,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: WillyColors.border),
      ),
      child: Row(
        children: [
          Icon(icon, size: 15, color: color),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label, style: const TextStyle(color: WillyColors.faint, fontSize: 10)),
                Text(
                  value,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: WillyColors.textSoft, fontSize: 12, fontWeight: FontWeight.w600),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

void showWillySnack(BuildContext context, String message, {bool error = false}) {
  final messenger = ScaffoldMessenger.maybeOf(context);
  if (messenger == null) return;
  messenger.hideCurrentSnackBar();
  messenger.showSnackBar(
    SnackBar(
      content: Text(message),
      backgroundColor: error ? WillyColors.red : WillyColors.green,
      duration: const Duration(seconds: 2),
    ),
  );
}

/// Shared confirm dialog; resolves true when confirmed.
Future<bool> confirmAction(
  BuildContext context, {
  required String title,
  required String message,
  String confirmLabel = 'Confirm',
  Color color = WillyColors.red,
}) async {
  final result = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: Text(title, style: const TextStyle(color: WillyColors.text, fontSize: 18)),
      content: Text(message, style: const TextStyle(color: WillyColors.muted)),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
        FilledButton(
          style: FilledButton.styleFrom(backgroundColor: color, foregroundColor: Colors.white),
          onPressed: () => Navigator.pop(ctx, true),
          child: Text(confirmLabel),
        ),
      ],
    ),
  );
  return result == true;
}
