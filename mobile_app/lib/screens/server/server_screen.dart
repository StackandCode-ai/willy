import 'dart:async';

import 'package:flutter/material.dart';

import '../../models/call_panel.dart';
import '../../models/device.dart';
import '../../services/api_service.dart';
import '../../theme.dart';
import '../call_panels/files_panel.dart';
import 'server_overview_tab.dart';
import 'server_processes_tab.dart';
import 'server_system_tab.dart';
import 'server_terminal_tab.dart';

enum ServerTab { overview, files, terminal, processes, system }

extension ServerTabX on ServerTab {
  String get label => switch (this) {
        ServerTab.overview => 'Overview',
        ServerTab.files => 'Files',
        ServerTab.terminal => 'Terminal',
        ServerTab.processes => 'Processes',
        ServerTab.system => 'System',
      };

  IconData get icon => switch (this) {
        ServerTab.overview => Icons.monitor_heart_outlined,
        ServerTab.files => Icons.folder_rounded,
        ServerTab.terminal => Icons.terminal_rounded,
        ServerTab.processes => Icons.memory_rounded,
        ServerTab.system => Icons.tune_rounded,
      };

  Color get color => switch (this) {
        ServerTab.overview => WillyColors.green,
        ServerTab.files => WillyColors.amber,
        ServerTab.terminal => WillyColors.cyan,
        ServerTab.processes => WillyColors.pink,
        ServerTab.system => WillyColors.purple,
      };
}

/// The Willy server (device_type "server"): health and websites, files, a terminal, the
/// busiest processes and System (history, updates, storage, network and security, services
/// and jobs, logs). Every action goes through the same device-action path as the PC.
class ServerScreen extends StatefulWidget {
  final WillyDevice device;
  final ServerTab initialTab;

  const ServerScreen({super.key, required this.device, this.initialTab = ServerTab.overview});

  @override
  State<ServerScreen> createState() => _ServerScreenState();
}

class _ServerScreenState extends State<ServerScreen> with SingleTickerProviderStateMixin {
  late WillyDevice _dev = widget.device;
  late final TabController _tabs =
      TabController(length: ServerTab.values.length, vsync: this, initialIndex: widget.initialTab.index);
  late final CallPanelRequest _filesRequest =
      CallPanelRequest(CallPanelKind.files, deviceId: widget.device.id, title: 'Files');
  StreamSubscription<List<WillyDevice>>? _sub;

  /// Tabs that have been shown once (they load their data on first view).
  late final Set<int> _seen = {widget.initialTab.index};

  @override
  void initState() {
    super.initState();
    _sub = ApiService.devicesStream.listen((_) {
      final d = ApiService.device(widget.device.id);
      if (d != null && mounted) setState(() => _dev = d);
    });
    _tabs.addListener(() {
      if (!mounted) return;
      setState(() => _seen.add(_tabs.index));
    });
  }

  @override
  void dispose() {
    _sub?.cancel();
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final dev = _dev;
    final index = _tabs.index;
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 0,
        title: Row(
          children: [
            const Icon(Icons.dns_rounded, color: WillyColors.green, size: 22),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(dev.name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 17)),
                  Text(
                    '${dev.online ? 'Online' : 'Offline · last seen ${timeAgo(dev.lastSeen)}'} · ${dev.platform}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 11, color: dev.online ? WillyColors.green : WillyColors.red),
                  ),
                ],
              ),
            ),
          ],
        ),
        bottom: TabBar(
          controller: _tabs,
          labelPadding: const EdgeInsets.symmetric(horizontal: 4),
          labelStyle: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700),
          // Five tabs share a 360 px phone: labels shrink rather than wrap or clip.
          tabs: [
            for (final t in ServerTab.values)
              Tab(
                icon: Icon(t.icon, size: 20),
                height: 56,
                child: FittedBox(fit: BoxFit.scaleDown, child: Text(t.label, maxLines: 1)),
              ),
          ],
        ),
      ),
      body: Column(
        children: [
          if (!dev.online)
            Container(
              width: double.infinity,
              color: WillyColors.red.withValues(alpha: 0.12),
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: const Text(
                "The server's Willy agent isn't connected, so actions on it fail until it reconnects. "
                'Website checks still come from the hub.',
                style: TextStyle(color: Color(0xFFFCA5A5), fontSize: 12),
              ),
            ),
          // An IndexedStack keeps every tab's state (folder, terminal output) while switching.
          // Only the visible files tab may turn Back into "up a folder".
          Expanded(
            child: IndexedStack(
              index: index,
              children: [
                ServerOverviewTab(device: dev, active: index == ServerTab.overview.index),
                _seen.contains(ServerTab.files.index)
                    ? FilesPanel(
                        key: ValueKey('files_${dev.id}'),
                        pc: dev,
                        request: _filesRequest,
                        handlesBack: index == ServerTab.files.index,
                      )
                    : const SizedBox.shrink(),
                ServerTerminalTab(device: dev, active: index == ServerTab.terminal.index),
                ServerProcessesTab(device: dev, active: index == ServerTab.processes.index),
                _seen.contains(ServerTab.system.index)
                    ? ServerSystemTab(device: dev, active: index == ServerTab.system.index)
                    : const SizedBox.shrink(),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
