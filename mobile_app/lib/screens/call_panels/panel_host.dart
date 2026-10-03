import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../../models/call_panel.dart';
import '../../models/device.dart';
import '../../services/api_service.dart';
import '../../theme.dart';
import 'camera_panel.dart';
import 'files_panel.dart';
import 'screenshot_panel.dart';

/// What the call is doing, shown in every panel's header so it's clear the call goes on.
class CallLine {
  final String label;
  final Color color;

  const CallLine(this.label, this.color);
}

/// Shared by the panels: the sheet's scroll controller (drags the sheet), the call's
/// live state and a way to close the sheet.
class PanelChrome {
  final ScrollController scroll;
  final ValueListenable<CallLine> call;
  final VoidCallback close;

  const PanelChrome({required this.scroll, required this.call, required this.close});
}

/// A draggable bottom sheet over the call. It follows [request]: when the hub asks for
/// another panel while it is open, the sheet switches to it.
ModalBottomSheetRoute<void> buildCallPanelRoute(
  BuildContext context, {
  required ValueListenable<CallPanelRequest?> request,
  required ValueListenable<CallLine> call,
}) {
  final navigator = Navigator.of(context);
  return ModalBottomSheetRoute<void>(
    builder: (_) => CallPanelSheet(request: request, call: call),
    capturedThemes: InheritedTheme.capture(from: context, to: navigator.context),
    barrierLabel: MaterialLocalizations.of(context).scrimLabel,
    isScrollControlled: true,
    useSafeArea: true,
    backgroundColor: Colors.transparent,
    elevation: 0,
    modalBarrierColor: Colors.black.withValues(alpha: 0.35),
  );
}

class CallPanelSheet extends StatelessWidget {
  final ValueListenable<CallPanelRequest?> request;
  final ValueListenable<CallLine> call;

  const CallPanelSheet({super.key, required this.request, required this.call});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<CallPanelRequest?>(
      valueListenable: request,
      builder: (context, req, _) {
        if (req == null) return const SizedBox.shrink();
        return DraggableScrollableSheet(
          expand: false,
          initialChildSize: 0.82,
          minChildSize: 0.3,
          maxChildSize: 0.97,
          snap: true,
          snapSizes: const [0.55, 0.82],
          builder: (context, scroll) {
            final chrome = PanelChrome(scroll: scroll, call: call, close: () => Navigator.of(context).maybePop());
            return ClipRRect(
              borderRadius: const BorderRadius.vertical(top: Radius.circular(26)),
              child: DecoratedBox(
                decoration: const BoxDecoration(
                  color: WillyColors.bgElevated,
                  border: Border(top: BorderSide(color: WillyColors.borderStrong)),
                ),
                // Own messenger, so snackbars show on the sheet instead of under it.
                child: ScaffoldMessenger(
                  child: Scaffold(
                    backgroundColor: Colors.transparent,
                    // One scrollable at a time: the sheet's controller can't drive two.
                    body: _panelFor(req, chrome),
                  ),
                ),
              ),
            );
          },
        );
      },
    );
  }

  Widget _panelFor(CallPanelRequest req, PanelChrome chrome) {
    if (req.kind == CallPanelKind.camera) {
      // Photos go to the hub, which hands them to the PC when it's around: no PC needed here.
      return CameraPanel(key: const ValueKey('camera'), request: req, chrome: chrome);
    }
    return StreamBuilder<List<WillyDevice>>(
      key: ValueKey(req.kind),
      stream: ApiService.devicesStream,
      initialData: ApiService.devices,
      builder: (context, snap) {
        final pc = pickPanelPc(snap.data ?? ApiService.devices, req.deviceId,
            allowServer: req.kind == CallPanelKind.files);
        if (pc == null) return PcOfflinePanel(request: req, chrome: chrome);
        return switch (req.kind) {
          CallPanelKind.files => FilesPanel(key: ValueKey('files_${pc.id}'), pc: pc, request: req, chrome: chrome),
          _ => ScreenshotPanel(key: ValueKey('screen_${pc.id}'), pc: pc, request: req, chrome: chrome),
        };
      },
    );
  }
}

/// Title row of a panel, with the drag handle and the call's live state.
class PanelHeader extends StatelessWidget {
  final PanelChrome chrome;
  final IconData icon;
  final Color color;
  final String title;
  final String? subtitle;
  final List<Widget> actions;

  const PanelHeader({
    super.key,
    required this.chrome,
    required this.icon,
    required this.title,
    this.color = WillyColors.cyan,
    this.subtitle,
    this.actions = const [],
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 8, 6),
      child: Column(
        children: [
          Row(
            children: [
              ValueListenableBuilder<CallLine>(
                valueListenable: chrome.call,
                builder: (context, line, _) => AnimatedContainer(
                  duration: const Duration(milliseconds: 250),
                  padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                  decoration: BoxDecoration(
                    color: line.color.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: line.color.withValues(alpha: 0.35)),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.call_rounded, size: 11, color: line.color),
                      const SizedBox(width: 5),
                      Text(line.label,
                          style: TextStyle(
                              color: line.color, fontSize: 10.5, fontWeight: FontWeight.w700, letterSpacing: 0.2)),
                    ],
                  ),
                ),
              ),
              const Expanded(
                child: Center(
                  child: SizedBox(
                    width: 40,
                    height: 4,
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        color: WillyColors.borderStrong,
                        borderRadius: BorderRadius.all(Radius.circular(2)),
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 76),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.14),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Icon(icon, color: color, size: 20),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: WillyColors.text, fontSize: 16.5, fontWeight: FontWeight.w700)),
                    if (subtitle != null && subtitle!.isNotEmpty)
                      Text(subtitle!,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(color: WillyColors.muted, fontSize: 12)),
                  ],
                ),
              ),
              ...actions,
              IconButton(
                tooltip: 'Close',
                onPressed: chrome.close,
                icon: const Icon(Icons.close_rounded, color: WillyColors.muted),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// Shown when the panel needs a PC and none is online.
class PcOfflinePanel extends StatefulWidget {
  final CallPanelRequest request;
  final PanelChrome chrome;

  const PcOfflinePanel({super.key, required this.request, required this.chrome});

  @override
  State<PcOfflinePanel> createState() => _PcOfflinePanelState();
}

class _PcOfflinePanelState extends State<PcOfflinePanel> {
  bool _checking = false;

  Future<void> _retry() async {
    setState(() => _checking = true);
    await ApiService.getDevices();
    if (mounted) setState(() => _checking = false);
  }

  @override
  Widget build(BuildContext context) {
    final isFiles = widget.request.kind == CallPanelKind.files;
    final id = widget.request.deviceId;
    final found = id == null ? null : ApiService.device(id);
    final named = isFiles && found != null && found.isServer ? found : null;
    final server = named != null;
    return CustomScrollView(
      controller: widget.chrome.scroll,
      slivers: [
        SliverToBoxAdapter(
          child: PanelHeader(
            chrome: widget.chrome,
            icon: isFiles ? Icons.folder_rounded : Icons.desktop_windows_rounded,
            color: WillyColors.amber,
            title: widget.request.title ?? (isFiles ? 'Files' : 'PC screen'),
          ),
        ),
        SliverFillRemaining(
          hasScrollBody: false,
          child: Padding(
            padding: const EdgeInsets.all(28),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(server ? Icons.cloud_off_rounded : Icons.desktop_access_disabled_rounded,
                    size: 56, color: WillyColors.faint),
                const SizedBox(height: 14),
                Text(named != null ? '${named.name} is offline' : 'Your PC is offline',
                    style: const TextStyle(color: WillyColors.text, fontSize: 18, fontWeight: FontWeight.w700)),
                const SizedBox(height: 6),
                Text(
                  server
                      ? "Its Willy agent isn't connected. This opens by itself when it connects."
                      : 'Wake it up or start Willy on it. This opens by itself when it connects.',
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: WillyColors.muted, fontSize: 13),
                ),
                const SizedBox(height: 18),
                OutlinedButton.icon(
                  onPressed: _checking ? null : _retry,
                  icon: _checking
                      ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.refresh_rounded),
                  label: const Text('Check again'),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

/// "Couldn't …" text for a failed device action.
String panelErrorText(Map<String, dynamic> res, String fallback) {
  final err = res['error']?.toString();
  switch (err) {
    case 'TIMEOUT':
      return "The PC didn't answer in time.";
    case 'SEND_FAILED':
    case 'DISCONNECTED':
      return "Couldn't reach the hub.";
  }
  final text = err ?? res['reply']?.toString() ?? res['message']?.toString();
  return (text == null || text.trim().isEmpty) ? fallback : text;
}

/// The message of a device action result, for a snackbar.
(String, bool) panelResultText(Map<String, dynamic> res, {String ok = 'Done.', String failed = "That didn't work."}) {
  final success = res['success'] == true;
  if (success) {
    final msg = res['message'] ?? res['reply'];
    return ((msg == null || msg.toString().trim().isEmpty) ? ok : msg.toString(), true);
  }
  return (panelErrorText(res, failed), false);
}
