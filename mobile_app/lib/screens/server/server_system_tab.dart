import 'package:flutter/material.dart';

import '../../models/device.dart';
import '../../theme.dart';
import 'server_history_section.dart';
import 'server_logs_section.dart';
import 'server_network_section.dart';
import 'server_services_section.dart';
import 'server_storage_section.dart';
import 'server_updates_section.dart';

/// The parts of the System tab, picked with a row of chips (fits 360 px phones).
enum SystemSection { history, updates, storage, network, services, logs }

extension SystemSectionX on SystemSection {
  String get label => switch (this) {
        SystemSection.history => 'History',
        SystemSection.updates => 'Updates',
        SystemSection.storage => 'Storage',
        SystemSection.network => 'Network & security',
        SystemSection.services => 'Services & jobs',
        SystemSection.logs => 'Logs',
      };

  IconData get icon => switch (this) {
        SystemSection.history => Icons.show_chart_rounded,
        SystemSection.updates => Icons.system_update_alt_rounded,
        SystemSection.storage => Icons.storage_rounded,
        SystemSection.network => Icons.shield_outlined,
        SystemSection.services => Icons.settings_suggest_rounded,
        SystemSection.logs => Icons.receipt_long_rounded,
      };
}

/// System administration for the Willy server: history charts, OS updates, storage,
/// network and security, services and scheduled jobs, and the system journal. Each part
/// loads the first time it is shown and keeps its state while switching.
class ServerSystemTab extends StatefulWidget {
  final WillyDevice device;
  final bool active;
  final SystemSection initialSection;

  const ServerSystemTab(
      {super.key, required this.device, this.active = true, this.initialSection = SystemSection.history});

  @override
  State<ServerSystemTab> createState() => _ServerSystemTabState();
}

class _ServerSystemTabState extends State<ServerSystemTab> {
  late SystemSection _section = widget.initialSection;
  late final Set<SystemSection> _seen = {widget.initialSection};

  void _select(SystemSection s) => setState(() {
        _section = s;
        _seen.add(s);
      });

  @override
  Widget build(BuildContext context) {
    final dev = widget.device;
    bool on(SystemSection s) => widget.active && _section == s;
    Widget lazy(SystemSection s, Widget Function() build) => _seen.contains(s) ? build() : const SizedBox.shrink();
    return Column(
      children: [
        SizedBox(
          height: 48,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
            children: [
              for (final s in SystemSection.values)
                Padding(
                  padding: const EdgeInsets.only(right: 6),
                  child: ChoiceChip(
                    visualDensity: VisualDensity.compact,
                    avatar: Icon(s.icon, size: 16, color: _section == s ? WillyColors.cyan : WillyColors.muted),
                    label: Text(s.label),
                    labelStyle: TextStyle(
                      fontSize: 12.5,
                      fontWeight: FontWeight.w600,
                      color: _section == s ? WillyColors.text : WillyColors.muted,
                    ),
                    showCheckmark: false,
                    selected: _section == s,
                    onSelected: (_) => _select(s),
                  ),
                ),
            ],
          ),
        ),
        Expanded(
          child: IndexedStack(
            index: _section.index,
            children: [
              lazy(SystemSection.history,
                  () => ServerHistorySection(device: dev, active: on(SystemSection.history))),
              lazy(SystemSection.updates,
                  () => ServerUpdatesSection(device: dev, active: on(SystemSection.updates))),
              lazy(SystemSection.storage,
                  () => ServerStorageSection(device: dev, active: on(SystemSection.storage))),
              lazy(SystemSection.network,
                  () => ServerNetworkSection(device: dev, active: on(SystemSection.network))),
              lazy(SystemSection.services,
                  () => ServerServicesSection(device: dev, active: on(SystemSection.services))),
              lazy(SystemSection.logs, () => ServerLogsSection(device: dev, active: on(SystemSection.logs))),
            ],
          ),
        ),
      ],
    );
  }
}
