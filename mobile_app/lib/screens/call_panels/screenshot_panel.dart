import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';

import '../../models/call_panel.dart';
import '../../models/device.dart';
import '../../services/api_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import 'panel_host.dart';

/// The PC's current screen, zoomable, with Refresh and "Save to phone".
class ScreenshotPanel extends StatefulWidget {
  final WillyDevice pc;
  final CallPanelRequest request;
  final PanelChrome chrome;

  const ScreenshotPanel({super.key, required this.pc, required this.request, required this.chrome});

  @override
  State<ScreenshotPanel> createState() => _ScreenshotPanelState();
}

class _ScreenshotPanelState extends State<ScreenshotPanel> {
  final _zoom = TransformationController();
  Uint8List? _image;
  int _width = 0;
  int _height = 0;
  DateTime? _at;
  bool _loading = false;
  bool _saving = false;
  String? _error;
  int _serial = 0;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void didUpdateWidget(ScreenshotPanel old) {
    super.didUpdateWidget(old);
    if (widget.request.serial != old.request.serial) _refresh();
  }

  @override
  void dispose() {
    _zoom.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    final serial = ++_serial;
    setState(() {
      _loading = true;
      _error = null;
    });
    final res = await ApiService.deviceAction(
      widget.pc.id,
      'get_screen_snapshot',
      {'quality': 70, 'max_width': 1600},
      const Duration(seconds: 20),
    );
    if (!mounted || serial != _serial) return;
    final b64 = res['image_base64'];
    Uint8List? bytes;
    if (res['success'] == true && b64 is String && b64.isNotEmpty) {
      try {
        bytes = base64Decode(b64);
      } catch (_) {}
    }
    setState(() {
      _loading = false;
      if (bytes == null) {
        _error = panelErrorText(res, "Couldn't get the PC's screen.");
      } else {
        _image = bytes;
        _width = asInt(res['width']) ?? 0;
        _height = asInt(res['height']) ?? 0;
        _at = DateTime.now();
        _zoom.value = Matrix4.identity();
      }
    });
  }

  /// The PC saves a full-quality screenshot and sends it to the phone (Download/Willy).
  Future<void> _saveToPhone() async {
    setState(() => _saving = true);
    HapticFeedback.lightImpact();
    final shot = await ApiService.deviceAction(widget.pc.id, 'take_screenshot', {}, const Duration(seconds: 30));
    if (!mounted) return;
    if (shot['success'] != true) {
      setState(() => _saving = false);
      showWillySnack(context, panelErrorText(shot, "The PC couldn't take a screenshot."), error: true);
      return;
    }
    final sent = await ApiService.deviceAction(
      widget.pc.id,
      'send_file_to_phone',
      {'which': 'latest_screenshot'},
      const Duration(seconds: 45),
    );
    if (!mounted) return;
    setState(() => _saving = false);
    final (text, ok) = panelResultText(sent, ok: 'Screenshot on its way to Download/Willy.');
    showWillySnack(context, text, error: !ok);
  }

  @override
  Widget build(BuildContext context) {
    final subtitle = [
      widget.pc.name,
      if (_at != null) 'captured ${DateFormat('HH:mm:ss').format(_at!)}',
      if (_width > 0 && _height > 0) '$_width×$_height',
    ].join(' · ');
    return CustomScrollView(
      controller: widget.chrome.scroll,
      slivers: [
        SliverToBoxAdapter(
          child: PanelHeader(
            chrome: widget.chrome,
            icon: Icons.screenshot_monitor_rounded,
            color: WillyColors.sky,
            title: widget.request.title ?? 'PC screen',
            subtitle: subtitle,
            actions: [
              IconButton(
                tooltip: 'Refresh',
                onPressed: _loading ? null : _refresh,
                icon: _loading
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.refresh_rounded, color: WillyColors.textSoft),
              ),
            ],
          ),
        ),
        SliverFillRemaining(
          child: Column(
            children: [
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(14),
                    child: ColoredBox(color: Colors.black, child: _viewer()),
                  ),
                ),
              ),
              SafeArea(
                top: false,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
                  child: Row(
                    children: [
                      Expanded(
                        child: OutlinedButton.icon(
                          style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(48)),
                          onPressed: _loading ? null : _refresh,
                          icon: const Icon(Icons.refresh_rounded),
                          label: const Text('Refresh'),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: FilledButton.icon(
                          style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)),
                          onPressed: _saving ? null : _saveToPhone,
                          icon: _saving
                              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                              : const Icon(Icons.download_rounded),
                          label: const Text('Save to phone'),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _viewer() {
    final image = _image;
    if (image == null) {
      if (_error != null) {
        return Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.error_outline_rounded, color: WillyColors.red, size: 40),
                const SizedBox(height: 10),
                Text(_error!, textAlign: TextAlign.center, style: const TextStyle(color: WillyColors.muted)),
              ],
            ),
          ),
        );
      }
      return const Center(child: CircularProgressIndicator());
    }
    return Stack(
      fit: StackFit.expand,
      children: [
        GestureDetector(
          onDoubleTap: () => _zoom.value = Matrix4.identity(),
          child: InteractiveViewer(
            transformationController: _zoom,
            minScale: 1,
            maxScale: 8,
            child: Center(child: Image.memory(image, gaplessPlayback: true, fit: BoxFit.contain)),
          ),
        ),
        if (_error != null)
          Positioned(
            left: 8,
            right: 8,
            bottom: 8,
            child: Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(color: Colors.black87, borderRadius: BorderRadius.circular(10)),
              child: Text(_error!, style: const TextStyle(color: WillyColors.amber, fontSize: 12)),
            ),
          ),
        const Positioned(
          top: 8,
          right: 10,
          child: Text('Pinch to zoom · double-tap to reset',
              style: TextStyle(color: Colors.white54, fontSize: 10.5, shadows: [Shadow(blurRadius: 4)])),
        ),
      ],
    );
  }
}
