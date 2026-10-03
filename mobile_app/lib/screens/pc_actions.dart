import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/device.dart';
import '../services/api_service.dart';
import '../services/file_transfer_service.dart';
import '../theme.dart';
import '../widgets/common.dart';

/// Dialogs and sheets for acting on a PC (shared by the Devices and Remote tabs).
class PcActions {
  /// Runs a direct action and reports the result in a snackbar.
  static Future<Map<String, dynamic>> run(
    BuildContext context,
    WillyDevice dev,
    String action, [
    Map<String, dynamic>? payload,
    String? successText,
  ]) async {
    final res = await ApiService.deviceAction(dev.id, action, payload);
    if (context.mounted) {
      final ok = res['success'] == true;
      showWillySnack(
        context,
        ok
            ? (successText ?? res['message']?.toString() ?? 'Done')
            : (res['reply'] ?? res['error'] ?? 'Failed').toString(),
        error: !ok,
      );
    }
    return res;
  }

  // --- Find My Laptop ---
  static Future<void> locate(BuildContext context, WillyDevice dev) async {
    final res = await ApiService.ringDevice(dev.id, message: 'Find My ${dev.isPc ? 'Laptop' : 'Phone'}');
    if (!context.mounted) return;
    final ok = res['success'] == true;
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Row(
          children: [
            const Icon(Icons.radar, color: WillyColors.cyan),
            const SizedBox(width: 10),
            Expanded(child: Text('Locating ${dev.name}', style: const TextStyle(color: Colors.white, fontSize: 18))),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 70,
              height: 70,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: (ok ? WillyColors.cyan : WillyColors.red).withValues(alpha: 0.15),
                border: Border.all(color: ok ? WillyColors.cyan : WillyColors.red),
              ),
              child: Icon(ok ? Icons.notifications_active : Icons.notifications_off,
                  color: ok ? WillyColors.cyan : WillyColors.red, size: 36),
            ),
            const SizedBox(height: 16),
            Text(
              ok
                  ? 'Audible beacon triggered on ${dev.name}!'
                  : 'Could not reach ${dev.name}: ${res['reply'] ?? res['error'] ?? 'unknown error'}',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.white70, fontSize: 14),
            ),
            if (res['round_trip_ms'] != null) ...[
              const SizedBox(height: 8),
              Text('Delivered in ${res['round_trip_ms']} ms',
                  style: const TextStyle(color: WillyColors.green, fontSize: 12)),
            ],
          ],
        ),
        actions: [
          if (ok && dev.isMobile)
            TextButton(
              onPressed: () => ApiService.deviceAction(dev.id, 'stop_ring'),
              child: const Text('Stop ringing'),
            ),
          FilledButton(onPressed: () => Navigator.pop(ctx), child: const Text('Dismiss')),
        ],
      ),
    );
  }

  // --- Universal clipboard ---
  static Future<void> clipboard(BuildContext context, WillyDevice dev) async {
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (_) => const Center(child: CircularProgressIndicator(color: WillyColors.cyan)),
    );
    final remote = await ApiService.getClipboard(dev.id);
    if (!context.mounted) return;
    Navigator.pop(context);

    final controller = TextEditingController();
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Row(
          children: [
            const Icon(Icons.content_paste_go, color: WillyColors.cyan),
            const SizedBox(width: 10),
            Expanded(child: Text('Clipboard · ${dev.name}', style: const TextStyle(color: Colors.white, fontSize: 16))),
          ],
        ),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('ON THE PC', style: TextStyle(color: WillyColors.faint, fontSize: 11, fontWeight: FontWeight.bold)),
              const SizedBox(height: 6),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: WillyColors.border,
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Text(
                  (remote != null && remote.isNotEmpty) ? remote : '(PC clipboard is empty)',
                  style: const TextStyle(color: Colors.white, fontSize: 13),
                  maxLines: 5,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (remote != null && remote.isNotEmpty)
                Align(
                  alignment: Alignment.centerRight,
                  child: TextButton.icon(
                    icon: const Icon(Icons.copy, size: 16),
                    label: const Text('Copy to phone'),
                    onPressed: () {
                      Clipboard.setData(ClipboardData(text: remote));
                      Navigator.pop(ctx);
                      showWillySnack(context, 'Copied PC clipboard to phone');
                    },
                  ),
                ),
              const Divider(color: WillyColors.borderStrong, height: 22),
              const Text('SEND TO THE PC', style: TextStyle(color: WillyColors.faint, fontSize: 11, fontWeight: FontWeight.bold)),
              const SizedBox(height: 6),
              TextField(
                controller: controller,
                maxLines: 3,
                minLines: 2,
                style: const TextStyle(color: Colors.white, fontSize: 13),
                decoration: const InputDecoration(hintText: 'Type or paste text to push to the PC…'),
              ),
              TextButton.icon(
                icon: const Icon(Icons.paste, size: 16, color: WillyColors.muted),
                label: const Text('Paste phone clipboard', style: TextStyle(color: WillyColors.muted, fontSize: 12)),
                onPressed: () async {
                  final data = await Clipboard.getData(Clipboard.kTextPlain);
                  if (data?.text != null) controller.text = data!.text!;
                },
              ),
            ],
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Close')),
          FilledButton(
            onPressed: () async {
              final txt = controller.text.trim();
              if (txt.isEmpty) return;
              Navigator.pop(ctx);
              final res = await ApiService.sendClipboard(dev.id, txt);
              if (context.mounted) {
                showWillySnack(context, res['success'] == true ? 'Sent to PC clipboard' : 'Failed to sync',
                    error: res['success'] != true);
              }
            },
            child: const Text('Push to PC'),
          ),
        ],
      ),
    );
  }

  // --- QuickDrop ---
  static void quickDrop(BuildContext context, WillyDevice dev) {
    final controller = TextEditingController();
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Row(
          children: [
            const Icon(Icons.send_to_mobile, color: WillyColors.cyan),
            const SizedBox(width: 10),
            Expanded(child: Text('QuickDrop to ${dev.name}', style: const TextStyle(color: Colors.white, fontSize: 16))),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              dev.isPc
                  ? 'A link opens in the PC browser; a note pops up and lands on its clipboard.'
                  : 'A link opens on the phone; a note pops up and lands on its clipboard.',
              style: const TextStyle(color: WillyColors.muted, fontSize: 13),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: controller,
              minLines: 1,
              maxLines: 4,
              style: const TextStyle(color: Colors.white, fontSize: 14),
              decoration: const InputDecoration(hintText: 'https://… or a quick note'),
            ),
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 6,
              children: [
                for (final entry in const {
                  'YouTube': 'https://youtube.com',
                  'GitHub': 'https://github.com',
                  'ChatGPT': 'https://chatgpt.com',
                  'Gmail': 'https://mail.google.com',
                }.entries)
                  ActionChip(
                    backgroundColor: WillyColors.border,
                    label: Text(entry.key, style: const TextStyle(color: WillyColors.cyan, fontSize: 11)),
                    onPressed: () => controller.text = entry.value,
                  ),
              ],
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
            onPressed: () async {
              final text = controller.text.trim();
              if (text.isEmpty) return;
              Navigator.pop(ctx);
              final isUrl = RegExp(r'^(https?://|www\.)', caseSensitive: false).hasMatch(text);
              final res = await ApiService.quickDrop(
                dev.id,
                url: isUrl ? (text.startsWith('www.') ? 'https://$text' : text) : null,
                text: isUrl ? null : text,
                title: isUrl ? 'Opened Web Link' : 'Quick Note',
              );
              if (context.mounted) {
                showWillySnack(context, res['success'] == true ? 'Delivered to ${dev.name}' : 'Delivery failed',
                    error: res['success'] != true);
              }
            },
            child: const Text('Send'),
          ),
        ],
      ),
    );
  }

  // --- Send files to the PC ---

  /// Android's file picker, then [sendFiles].
  static Future<void> pickAndSendFiles(BuildContext context, [WillyDevice? dev]) async {
    final files = await FileTransferService.pickFiles();
    if (files.isEmpty || !context.mounted) return;
    await sendFiles(context, files, pcName: dev?.name);
  }

  /// Uploads [files] to the hub for the PC with a progress dialog, then shows the hub's reply.
  static Future<void> sendFiles(BuildContext context, List<PhoneFile> files, {String? pcName}) async {
    if (files.isEmpty) return;
    final results = await showDialog<List<Map<String, dynamic>>>(
      context: context,
      barrierDismissible: false,
      builder: (_) => _UploadDialog(files: files, pcName: pcName ?? ApiService.primaryPc?.name ?? 'your PC'),
    );
    if (!context.mounted || results == null || results.isEmpty) return;
    final failed = results.where((r) => r['success'] != true).toList();
    final String text;
    if (failed.isEmpty) {
      text = results.length == 1
          ? (results.first['reply'] ?? 'Sent ${files.first.name} to your PC.').toString()
          : 'Sent ${results.length} files to your PC. ${results.last['reply'] ?? ''}'.trim();
    } else if (failed.length == results.length && results.length == 1) {
      text = (failed.first['error'] ?? failed.first['reply'] ?? 'Could not send the file.').toString();
    } else {
      text = '${results.length - failed.length} of ${results.length} files sent. '
          '${failed.first['error'] ?? failed.first['reply'] ?? ''}'.trim();
    }
    final messenger = ScaffoldMessenger.maybeOf(context);
    messenger
      ?..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(text),
        backgroundColor: failed.isEmpty ? WillyColors.green : WillyColors.red,
        duration: const Duration(seconds: 5),
      ));
  }

  // --- Media remote ---
  static void media(BuildContext context, WillyDevice dev) {
    Widget roundButton(IconData icon, VoidCallback onTap, {bool primary = false}) => Material(
          color: primary ? WillyColors.cyan : WillyColors.border,
          shape: const CircleBorder(),
          child: InkWell(
            customBorder: const CircleBorder(),
            onTap: () {
              HapticFeedback.selectionClick();
              onTap();
            },
            child: Padding(
              padding: EdgeInsets.all(primary ? 18 : 14),
              child: Icon(icon, size: primary ? 34 : 28, color: primary ? Colors.black : Colors.white),
            ),
          ),
        );

    showModalBottomSheet(
      context: context,
      backgroundColor: WillyColors.card,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 16, 24, 24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  const Icon(Icons.music_note, color: WillyColors.cyan),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text('Media · ${dev.name}',
                        style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
                  ),
                  IconButton(icon: const Icon(Icons.close, color: Colors.grey), onPressed: () => Navigator.pop(ctx)),
                ],
              ),
              const SizedBox(height: 16),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: [
                  roundButton(Icons.skip_previous_rounded, () => ApiService.controlMedia(dev.id, 'prev')),
                  roundButton(Icons.play_arrow_rounded, () => ApiService.controlMedia(dev.id, 'play_pause'), primary: true),
                  roundButton(Icons.skip_next_rounded, () => ApiService.controlMedia(dev.id, 'next')),
                ],
              ),
              const SizedBox(height: 22),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: [
                  _chipButton(Icons.volume_off, 'Mute', () => ApiService.deviceAction(dev.id, 'volume_control', {'action': 'mute'})),
                  _chipButton(Icons.volume_down, 'Vol −', () => ApiService.deviceAction(dev.id, 'volume_control', {'action': 'down'})),
                  _chipButton(Icons.volume_up, 'Vol +', () => ApiService.deviceAction(dev.id, 'volume_control', {'action': 'up'})),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  static Widget _chipButton(IconData icon, String label, VoidCallback onTap) => FilledButton.tonalIcon(
        style: FilledButton.styleFrom(
          backgroundColor: WillyColors.border,
          foregroundColor: Colors.white,
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        ),
        icon: Icon(icon, size: 18, color: WillyColors.cyan),
        label: Text(label),
        onPressed: () {
          HapticFeedback.selectionClick();
          onTap();
        },
      );

  // --- Hardware specs ---
  static void specs(BuildContext context, WillyDevice dev) {
    final s = dev.specs;
    final rows = <MapEntry<String, String>>[
      if (s['os'] != null) MapEntry('Operating system', '${s['os']} (${s['os_version'] ?? ''})'),
      if (s['cpu_model'] != null) MapEntry('Processor', '${s['cpu_model']}'),
      if (s['cpu_cores'] != null) MapEntry('Cores / threads', '${s['cpu_cores']} / ${s['cpu_threads']}'),
      if (s['ram_total_gb'] != null) MapEntry('Memory', '${s['ram_total_gb']} GB'),
      if (dev.gpus.isNotEmpty) MapEntry('Graphics', dev.gpus.join('\n')),
      if (s['primary_resolution'] != null)
        MapEntry('Display', '${s['primary_resolution']} · ${s['monitors'] ?? 1} monitor(s)'),
      if (dev.diskTotalGb != null) MapEntry('System disk', '${dev.diskFreeGb} GB free of ${dev.diskTotalGb} GB'),
      if (s['user'] != null) MapEntry('Signed-in user', '${s['user']}'),
      if (s['hostname'] != null) MapEntry('Hostname', '${s['hostname']}'),
      MapEntry('Commands handled', '${dev.commandsHandled}'),
      if (s['client_version'] != null) MapEntry('Willy client', 'v${s['client_version']} · heartbeat ${s['heartbeat_sec']}s'),
    ];
    showModalBottomSheet(
      context: context,
      backgroundColor: WillyColors.card,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Icon(Icons.memory, color: WillyColors.purple),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text('${dev.name} · specs',
                        style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
                  ),
                  IconButton(icon: const Icon(Icons.close, color: Colors.grey), onPressed: () => Navigator.pop(ctx)),
                ],
              ),
              if (rows.length <= 1)
                const Padding(
                  padding: EdgeInsets.all(12),
                  child: Text('Specs appear once the PC client v3 connects.', style: TextStyle(color: WillyColors.muted)),
                ),
              for (final row in rows)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 7),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SizedBox(
                        width: 130,
                        child: Text(row.key, style: const TextStyle(color: WillyColors.faint, fontSize: 12)),
                      ),
                      Expanded(child: Text(row.value, style: const TextStyle(color: WillyColors.textSoft, fontSize: 13))),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  // --- Less frequent actions ---
  static void more(BuildContext context, WillyDevice dev) {
    Future<void> act(String action, Map<String, dynamic> payload, String done) async {
      Navigator.pop(context);
      await run(context, dev, action, payload, done);
    }

    showModalBottomSheet(
      context: context,
      backgroundColor: WillyColors.card,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      builder: (ctx) {
        double brightness = (dev.brightness ?? 70).clamp(0, 100).toDouble();
        return StatefulBuilder(
          builder: (ctx, setSheet) => SafeArea(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  GridView.count(
                    crossAxisCount: 4,
                    shrinkWrap: true,
                    physics: const NeverScrollableScrollPhysics(),
                    mainAxisSpacing: 10,
                    crossAxisSpacing: 10,
                    childAspectRatio: 1.05,
                    children: [
                      ActionTile(
                        icon: Icons.upload_file_rounded,
                        label: 'Send file',
                        color: WillyColors.cyan,
                        onTap: () {
                          Navigator.pop(ctx);
                          pickAndSendFiles(context, dev);
                        },
                      ),
                      ActionTile(
                        icon: Icons.desktop_windows_outlined,
                        label: 'Desktop',
                        color: WillyColors.sky,
                        onTap: () => act('window_action', {'action': 'minimize_all'}, 'Showing desktop'),
                      ),
                      ActionTile(
                        icon: Icons.photo_camera_outlined,
                        label: 'Screenshot',
                        color: WillyColors.purple,
                        onTap: () => act('take_screenshot', {}, 'Screenshot saved on the PC'),
                      ),
                      ActionTile(
                        icon: Icons.download_rounded,
                        label: 'Downloads',
                        color: WillyColors.green,
                        onTap: () => act('open_folder', {'path': 'downloads'}, 'Opened Downloads'),
                      ),
                      ActionTile(
                        icon: Icons.bedtime_outlined,
                        label: 'Sleep',
                        color: WillyColors.amber,
                        onTap: () async {
                          Navigator.pop(ctx);
                          if (await confirmAction(context,
                              title: 'Put ${dev.name} to sleep?',
                              message: 'The PC will disconnect until it wakes up.',
                              confirmLabel: 'Sleep',
                              color: WillyColors.amber)) {
                            if (context.mounted) await run(context, dev, 'power_action', {'action': 'sleep'}, 'Sleeping…');
                          }
                        },
                      ),
                    ],
                  ),
                  if (dev.hasBattery) ...[
                    const SizedBox(height: 14),
                    Row(
                      children: [
                        const Icon(Icons.brightness_6_outlined, color: WillyColors.amber, size: 20),
                        Expanded(
                          child: Slider(
                            value: brightness,
                            min: 0,
                            max: 100,
                            divisions: 20,
                            label: '${brightness.round()}%',
                            onChanged: (v) => setSheet(() => brightness = v),
                            onChangeEnd: (v) =>
                                ApiService.deviceAction(dev.id, 'set_brightness', {'level': v.round(), 'quiet': true}),
                          ),
                        ),
                        SizedBox(
                          width: 44,
                          child: Text('${brightness.round()}%',
                              style: const TextStyle(color: WillyColors.muted, fontSize: 12)),
                        ),
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

/// Uploads files one by one and pops with each file's result.
class _UploadDialog extends StatefulWidget {
  final List<PhoneFile> files;
  final String pcName;

  const _UploadDialog({required this.files, required this.pcName});

  @override
  State<_UploadDialog> createState() => _UploadDialogState();
}

class _UploadDialogState extends State<_UploadDialog> {
  final List<Map<String, dynamic>> _results = [];
  StreamSubscription<TransferProgress>? _progressSub;
  int _index = 0;
  String? _transferId;
  TransferProgress? _progress;

  @override
  void initState() {
    super.initState();
    _progressSub = FileTransferService.progress.listen((p) {
      if (p.id == _transferId && mounted) setState(() => _progress = p);
    });
    _run();
  }

  @override
  void dispose() {
    _progressSub?.cancel();
    super.dispose();
  }

  Future<void> _run() async {
    for (var i = 0; i < widget.files.length; i++) {
      final id = FileTransferService.newTransferId();
      if (mounted) {
        setState(() {
          _index = i;
          _transferId = id;
          _progress = null;
        });
      }
      _results.add(await FileTransferService.uploadToPc(widget.files[i], transferId: id));
    }
    if (mounted) Navigator.pop(context, _results);
  }

  @override
  Widget build(BuildContext context) {
    final file = widget.files[_index];
    final p = _progress;
    final total = p != null && p.total > 0 ? p.total : file.size;
    final sizeText = [
      if (p != null) FileTransferService.formatBytes(p.done),
      if (total > 0) FileTransferService.formatBytes(total),
    ].join(' of ');
    return PopScope(
      canPop: false,
      child: AlertDialog(
        title: Row(
          children: [
            const Icon(Icons.upload_file_rounded, color: WillyColors.cyan),
            const SizedBox(width: 10),
            Expanded(
              child: Text('Sending to ${widget.pcName}', style: const TextStyle(color: Colors.white, fontSize: 16)),
            ),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(file.name,
                maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(color: WillyColors.textSoft)),
            const SizedBox(height: 12),
            LinearProgressIndicator(value: p?.fraction, color: WillyColors.cyan, backgroundColor: WillyColors.border),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: Text(sizeText.isEmpty ? 'Starting…' : sizeText,
                      style: const TextStyle(color: WillyColors.muted, fontSize: 12)),
                ),
                if (widget.files.length > 1)
                  Text('${_index + 1} of ${widget.files.length}',
                      style: const TextStyle(color: WillyColors.muted, fontSize: 12)),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
