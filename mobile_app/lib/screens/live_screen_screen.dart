import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../models/device.dart';
import '../services/api_service.dart';
import '../services/settings_store.dart';
import '../theme.dart';

/// Live mirror of a PC monitor: frames are requested back-to-back over the hub socket
/// (the next request starts as soon as the previous frame lands).
class LiveScreenScreen extends StatefulWidget {
  final WillyDevice device;

  const LiveScreenScreen({super.key, required this.device});

  @override
  State<LiveScreenScreen> createState() => _LiveScreenScreenState();
}

class _Quality {
  final String key;
  final String label;
  final int jpeg;
  final int width;

  const _Quality(this.key, this.label, this.jpeg, this.width);
}

const _qualities = [
  _Quality('low', 'Fast', 30, 640),
  _Quality('medium', 'Balanced', 45, 960),
  _Quality('high', 'Sharp', 65, 1280),
];

class _LiveScreenScreenState extends State<LiveScreenScreen> {
  Uint8List? _frame;
  String _label = '';
  int _width = 0;
  int _height = 0;
  bool _live = true;
  bool _running = false;
  String? _error;
  int _frameMs = 0;
  double _fps = 0;
  String _monitor = 'active';
  late _Quality _quality;
  final List<DateTime> _frameTimes = [];

  @override
  void initState() {
    super.initState();
    _quality = _qualities.firstWhere(
      (q) => q.key == WillySettings.liveScreenQuality,
      orElse: () => _qualities[1],
    );
    _loop();
  }

  @override
  void dispose() {
    _live = false;
    super.dispose();
  }

  Future<void> _loop() async {
    if (_running) return;
    _running = true;
    while (mounted && _live) {
      final sw = Stopwatch()..start();
      final res = await ApiService.getScreenSnapshot(
        widget.device.id,
        quality: _quality.jpeg,
        maxWidth: _quality.width,
        monitor: _monitor,
      );
      sw.stop();
      if (!mounted) break;
      final b64 = res['image_base64'];
      if (res['success'] == true && b64 is String) {
        final now = DateTime.now();
        _frameTimes.add(now);
        _frameTimes.removeWhere((t) => now.difference(t).inMilliseconds > 3000);
        setState(() {
          _frame = base64Decode(b64);
          _label = res['label']?.toString() ?? '';
          _width = asInt(res['width']) ?? 0;
          _height = asInt(res['height']) ?? 0;
          _frameMs = sw.elapsedMilliseconds;
          _fps = _frameTimes.length / 3.0;
          _error = null;
        });
      } else {
        setState(() => _error = (res['reply'] ?? res['error'] ?? 'Capture failed').toString());
        await Future.delayed(const Duration(seconds: 2));
      }
    }
    _running = false;
  }

  void _toggleLive() {
    setState(() => _live = !_live);
    if (_live) _loop();
  }

  void _setQuality(_Quality q) {
    setState(() => _quality = q);
    WillySettings.liveScreenQuality = q.key;
    WillySettings.save();
  }

  @override
  Widget build(BuildContext context) {
    final monitors = widget.device.monitors ?? 1;
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: WillyColors.bg,
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(widget.device.name, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
            Text(
              _frame == null
                  ? 'Connecting…'
                  : '$_label · ${_width}x$_height · ${_fps.toStringAsFixed(1)} fps · $_frameMs ms',
              style: const TextStyle(fontSize: 11, color: WillyColors.muted),
            ),
          ],
        ),
        actions: [
          IconButton(
            tooltip: _live ? 'Pause' : 'Resume',
            icon: Icon(_live ? Icons.pause_circle_filled : Icons.play_circle_fill,
                color: _live ? WillyColors.amber : WillyColors.green),
            onPressed: _toggleLive,
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: Stack(
              children: [
                Positioned.fill(
                  child: _frame == null
                      ? Center(
                          child: _error == null
                              ? const CircularProgressIndicator(color: WillyColors.cyan)
                              : Padding(
                                  padding: const EdgeInsets.all(24),
                                  child: Text(_error!, textAlign: TextAlign.center, style: const TextStyle(color: WillyColors.red)),
                                ),
                        )
                      : InteractiveViewer(
                          maxScale: 5,
                          child: Center(child: Image.memory(_frame!, gaplessPlayback: true, fit: BoxFit.contain)),
                        ),
                ),
                if (_live && _frame != null)
                  Positioned(
                    top: 10,
                    left: 10,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                      decoration: BoxDecoration(
                        color: WillyColors.red.withValues(alpha: 0.85),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: const Text('● LIVE', style: TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold)),
                    ),
                  ),
                if (_error != null && _frame != null)
                  Positioned(
                    bottom: 10,
                    left: 10,
                    right: 10,
                    child: Text(_error!, style: const TextStyle(color: WillyColors.red, fontSize: 12)),
                  ),
              ],
            ),
          ),
          Container(
            color: WillyColors.bg,
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 14),
            child: SafeArea(
              top: false,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  SegmentedButton<String>(
                    segments: [
                      for (final q in _qualities) ButtonSegment(value: q.key, label: Text(q.label)),
                    ],
                    selected: {_quality.key},
                    onSelectionChanged: (sel) => _setQuality(_qualities.firstWhere((q) => q.key == sel.first)),
                    showSelectedIcon: false,
                  ),
                  if (monitors > 1) ...[
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      children: [
                        for (final m in ['active', for (var i = 1; i <= monitors; i++) '$i', 'all'])
                          ChoiceChip(
                            label: Text(m == 'active' ? 'Active' : (m == 'all' ? 'All screens' : 'Screen $m')),
                            selected: _monitor == m,
                            onSelected: (_) => setState(() => _monitor = m),
                          ),
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
