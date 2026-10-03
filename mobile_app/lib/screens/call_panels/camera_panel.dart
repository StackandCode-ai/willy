import 'dart:io';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';

import '../../models/call_panel.dart';
import '../../services/file_transfer_service.dart';
import '../../services/telemetry_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import 'panel_host.dart';

/// In-call camera: live preview, front/back switch and a shutter the user presses.
/// Nothing is captured in the background; the camera is released when the panel closes
/// (or the app leaves the screen).
class CameraPanel extends StatefulWidget {
  final CallPanelRequest request;
  final PanelChrome chrome;

  const CameraPanel({super.key, required this.request, required this.chrome});

  @override
  State<CameraPanel> createState() => _CameraPanelState();
}

class _CameraPanelState extends State<CameraPanel> with WidgetsBindingObserver {
  List<CameraDescription> _cameras = const [];
  CameraDescription? _current;
  CameraController? _controller;
  bool _starting = false;
  int _startSerial = 0;
  bool _capturing = false;
  bool _permissionDenied = false;
  String? _error;

  XFile? _shot;
  String _shotName = '';
  bool _sending = false;
  bool _saving = false;
  String? _sendReply;
  bool _sendOk = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _open();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _controller?.dispose();
    _controller = null;
    _discardShot();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.inactive || state == AppLifecycleState.paused) {
      // Never hold the camera while Willy isn't on screen.
      _release();
    } else if (state == AppLifecycleState.resumed &&
        !_starting &&
        _controller == null &&
        _shot == null &&
        _error == null &&
        _current != null) {
      _start(_current!);
    }
  }

  Future<void> _open() async {
    setState(() {
      _starting = true;
      _error = null;
      _permissionDenied = false;
    });
    try {
      _cameras = await availableCameras();
    } on CameraException catch (e) {
      _fail(e);
      return;
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _starting = false;
        _error = "Couldn't reach the camera ($e).";
      });
      return;
    }
    if (!mounted) return;
    if (_cameras.isEmpty) {
      setState(() {
        _starting = false;
        _error = 'This phone has no camera Willy can use.';
      });
      return;
    }
    final back = _cameras.where((c) => c.lensDirection == CameraLensDirection.back);
    await _start(back.isNotEmpty ? back.first : _cameras.first);
  }

  Future<void> _start(CameraDescription description) async {
    final serial = ++_startSerial;
    _current = description;
    final old = _controller;
    setState(() {
      _controller = null;
      _starting = true;
      _error = null;
    });
    await old?.dispose();
    // No audio: this camera only takes photos, and the call keeps the microphone.
    final controller = CameraController(
      description,
      ResolutionPreset.high,
      enableAudio: false,
      imageFormatGroup: ImageFormatGroup.jpeg,
    );
    try {
      await controller.initialize(); // Android asks for the CAMERA permission here the first time
    } on CameraException catch (e) {
      await controller.dispose();
      if (serial == _startSerial) _fail(e);
      return;
    }
    if (!mounted || serial != _startSerial || _shot != null) {
      await controller.dispose();
      return;
    }
    setState(() {
      _controller = controller;
      _starting = false;
    });
  }

  void _fail(CameraException e) {
    if (!mounted) return;
    final denied = e.code.toLowerCase().contains('denied') ||
        e.code.toLowerCase().contains('permission') ||
        e.code == 'CameraAccessRestricted';
    setState(() {
      _starting = false;
      _permissionDenied = denied;
      _error =
          denied ? 'Willy needs camera access to take a photo.' : (e.description ?? 'The camera failed (${e.code}).');
    });
  }

  Future<void> _release() async {
    final c = _controller;
    if (c == null) return;
    if (mounted) setState(() => _controller = null);
    await c.dispose();
  }

  Future<void> _switchCamera() async {
    if (_cameras.length < 2 || _current == null) return;
    HapticFeedback.selectionClick();
    final wanted =
        _current!.lensDirection == CameraLensDirection.back ? CameraLensDirection.front : CameraLensDirection.back;
    final other = _cameras.where((c) => c.lensDirection == wanted);
    final next = other.isNotEmpty ? other.first : _cameras[(_cameras.indexOf(_current!) + 1) % _cameras.length];
    await _start(next);
  }

  Future<void> _takePhoto() async {
    final c = _controller;
    if (c == null || !c.value.isInitialized || c.value.isTakingPicture || _capturing) return;
    setState(() => _capturing = true);
    HapticFeedback.mediumImpact();
    try {
      final file = await c.takePicture();
      if (!mounted) {
        File(file.path).delete().ignore();
        return;
      }
      setState(() {
        _shot = file;
        _shotName = 'Willy_photo_${DateFormat('yyyyMMdd_HHmmss').format(DateTime.now())}.jpg';
        _sendReply = null;
        _capturing = false;
      });
      // The photo is on screen now: let go of the camera until "Retake".
      _release();
    } on CameraException catch (e) {
      if (!mounted) return;
      setState(() => _capturing = false);
      showWillySnack(context, e.description ?? "Couldn't take the photo.", error: true);
    }
  }

  void _discardShot() {
    final shot = _shot;
    _shot = null;
    if (shot != null) File(shot.path).delete().ignore();
  }

  void _retake() {
    setState(() {
      _discardShot();
      _sendReply = null;
    });
    if (_current != null) {
      _start(_current!);
    } else {
      _open();
    }
  }

  Future<void> _sendToPc() async {
    final shot = _shot;
    if (shot == null) return;
    setState(() {
      _sending = true;
      _sendReply = null;
    });
    final res = await FileTransferService.uploadToPc(
      FileTransferService.localFile(shot.path, name: _shotName, mime: 'image/jpeg'),
    );
    if (!mounted) return;
    final ok = res['success'] == true;
    HapticFeedback.lightImpact();
    setState(() {
      _sending = false;
      _sendOk = ok;
      _sendReply = (ok ? res['reply'] : (res['error'] ?? res['reply']))?.toString() ??
          (ok ? 'Sent to your PC.' : "Couldn't send the photo.");
    });
  }

  Future<void> _saveToPhone() async {
    final shot = _shot;
    if (shot == null) return;
    setState(() => _saving = true);
    final res = await FileTransferService.saveImageToGallery(shot.path, name: _shotName);
    if (!mounted) return;
    setState(() => _saving = false);
    final (text, ok) = panelResultText(res, ok: 'Saved to your gallery.', failed: "Couldn't save the photo.");
    showWillySnack(context, text, error: !ok);
  }

  // ------------------------------------------------------------------ UI

  @override
  Widget build(BuildContext context) {
    final front = _current?.lensDirection == CameraLensDirection.front;
    return CustomScrollView(
      controller: widget.chrome.scroll,
      slivers: [
        SliverToBoxAdapter(
          child: PanelHeader(
            chrome: widget.chrome,
            icon: Icons.photo_camera_rounded,
            color: WillyColors.pink,
            title: widget.request.title ?? 'Camera',
            subtitle: _shot != null ? _shotName : (front ? 'Front camera' : 'Back camera'),
          ),
        ),
        SliverFillRemaining(
          child: Column(
            children: [
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(18),
                    child: ColoredBox(color: Colors.black, child: _shot != null ? _photo() : _preview()),
                  ),
                ),
              ),
              SafeArea(top: false, child: _shot != null ? _photoActions() : _shutterRow()),
            ],
          ),
        ),
      ],
    );
  }

  Widget _preview() {
    final c = _controller;
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(_permissionDenied ? Icons.no_photography_rounded : Icons.error_outline_rounded,
                  color: _permissionDenied ? WillyColors.amber : WillyColors.red, size: 44),
              const SizedBox(height: 10),
              Text(_error!, textAlign: TextAlign.center, style: const TextStyle(color: WillyColors.textSoft)),
              const SizedBox(height: 14),
              Wrap(
                spacing: 10,
                runSpacing: 8,
                alignment: WrapAlignment.center,
                children: [
                  FilledButton.icon(
                      onPressed: _open, icon: const Icon(Icons.refresh_rounded), label: const Text('Try again')),
                  if (_permissionDenied)
                    OutlinedButton.icon(
                      onPressed: MobileTelemetryService.openAppSettings,
                      icon: const Icon(Icons.settings_rounded),
                      label: const Text('App settings'),
                    ),
                ],
              ),
            ],
          ),
        ),
      );
    }
    if (c == null || !c.value.isInitialized || _starting) {
      return const Center(child: CircularProgressIndicator(color: WillyColors.pink));
    }
    return Stack(
      fit: StackFit.expand,
      children: [
        Center(child: CameraPreview(c)),
        if (_capturing) const ColoredBox(color: Color(0x55FFFFFF)),
      ],
    );
  }

  Widget _photo() {
    return Stack(
      fit: StackFit.expand,
      children: [
        InteractiveViewer(
          minScale: 1,
          maxScale: 5,
          child: Center(child: Image.file(File(_shot!.path), fit: BoxFit.contain)),
        ),
        if (_sendReply != null)
          Positioned(
            left: 10,
            right: 10,
            bottom: 10,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(
                color: Colors.black.withValues(alpha: 0.75),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: (_sendOk ? WillyColors.green : WillyColors.red).withValues(alpha: 0.6)),
              ),
              child: Row(
                children: [
                  Icon(_sendOk ? Icons.check_circle_rounded : Icons.error_outline_rounded,
                      color: _sendOk ? WillyColors.green : WillyColors.red, size: 20),
                  const SizedBox(width: 10),
                  Expanded(child: Text(_sendReply!, style: const TextStyle(color: WillyColors.text, fontSize: 13))),
                ],
              ),
            ),
          ),
      ],
    );
  }

  Widget _shutterRow() {
    final ready = _controller?.value.isInitialized == true && !_starting && !_capturing;
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 14, 24, 16),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          const SizedBox(width: 52),
          Semantics(
            button: true,
            label: 'Take photo',
            child: GestureDetector(
              onTap: ready ? _takePhoto : null,
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 150),
                width: 76,
                height: 76,
                padding: const EdgeInsets.all(5),
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(color: ready ? Colors.white : WillyColors.faint, width: 4),
                ),
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 120),
                  margin: EdgeInsets.all(_capturing ? 6 : 0),
                  decoration: BoxDecoration(shape: BoxShape.circle, color: ready ? Colors.white : WillyColors.faint),
                ),
              ),
            ),
          ),
          SizedBox(
            width: 52,
            height: 52,
            child: IconButton.filledTonal(
              tooltip: 'Switch camera',
              onPressed: _cameras.length > 1 && !_starting && !_capturing ? _switchCamera : null,
              icon: const Icon(Icons.cameraswitch_rounded),
            ),
          ),
        ],
      ),
    );
  }

  Widget _photoActions() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 14),
      child: Row(
        children: [
          Expanded(
            child: OutlinedButton.icon(
              style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(48)),
              onPressed: _sending || _saving ? null : _retake,
              icon: const Icon(Icons.replay_rounded),
              label: const Text('Retake'),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            flex: 2,
            child: FilledButton.icon(
              style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)),
              onPressed: _sending ? null : _sendToPc,
              icon: _sending
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.send_rounded),
              label: const Text('Send to PC'),
            ),
          ),
          const SizedBox(width: 8),
          SizedBox(
            height: 48,
            width: 52,
            child: IconButton.filledTonal(
              tooltip: 'Save to phone gallery',
              onPressed: _saving ? null : _saveToPhone,
              icon: _saving
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.save_alt_rounded),
            ),
          ),
        ],
      ),
    );
  }
}
