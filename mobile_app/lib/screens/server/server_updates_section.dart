import 'package:flutter/material.dart';

import '../../models/device.dart';
import '../../models/server.dart';
import '../../services/api_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import 'server_widgets.dart';

/// OS updates on the server: what's pending (security count, a newer release, whether a
/// reboot is due), installing them, and rebooting.
class ServerUpdatesSection extends StatefulWidget {
  final WillyDevice device;
  final bool active;

  const ServerUpdatesSection({super.key, required this.device, this.active = true});

  @override
  State<ServerUpdatesSection> createState() => _ServerUpdatesSectionState();
}

class _ServerUpdatesSectionState extends State<ServerUpdatesSection> {
  ServerUpdates? _info;
  String? _error;
  bool _checking = false;
  bool _checked = false;
  DateTime? _at;
  String? _installing; // "security" / "all" while dnf runs
  String? _lastOutput;
  bool _lastOutputOk = true;
  bool _rebootBusy = false;
  DateTime? _rebootAt; // when a reboot we asked for happens
  bool _showAll = false;

  bool get _online => widget.device.online;

  @override
  void initState() {
    super.initState();
    if (widget.active && _online) _check();
  }

  @override
  void didUpdateWidget(ServerUpdatesSection old) {
    super.didUpdateWidget(old);
    if (widget.active && _online && !_checked && !_checking) _check();
  }

  Future<void> _check() async {
    if (_checking || _installing != null) return;
    setState(() {
      _checking = true;
      _error = null;
    });
    final res = await ApiService.serverAction(widget.device.id, 'sys_updates');
    if (!mounted) return;
    setState(() {
      _checking = false;
      _checked = true;
      if (res['success'] == true) {
        _info = ServerUpdates.fromJson(res);
        _at = DateTime.now();
      } else {
        _error = serverErrorText(res, "Couldn't check for updates.");
      }
    });
  }

  Future<void> _install({required bool securityOnly}) async {
    final info = _info;
    final count = securityOnly ? (info?.securityCount ?? 0) : (info?.packages.length ?? 0);
    final ok = await confirmAction(
      context,
      title: securityOnly ? 'Install security updates?' : 'Install all updates?',
      message: '${securityOnly ? 'Installs the $count security update${count == 1 ? '' : 's'}' : 'Installs all $count pending update${count == 1 ? '' : 's'}'} '
          'on ${widget.device.name} with dnf. It can take up to 30 minutes and keeps running on the server even if you '
          'leave this screen. Services may restart briefly; the server does not reboot by itself.',
      confirmLabel: 'Install',
      color: WillyColors.amber,
    );
    if (!ok || !mounted) return;
    setState(() {
      _installing = securityOnly ? 'security' : 'all';
      _lastOutput = null;
    });
    final res = await ApiService.serverAction(widget.device.id, 'apply_updates', {'security_only': securityOnly});
    if (!mounted) return;
    final success = res['success'] == true;
    setState(() {
      _installing = null;
      _lastOutput = [res['output']?.toString() ?? '', if (!success) serverErrorText(res, 'The update failed.')]
          .where((s) => s.trim().isNotEmpty)
          .join('\n\n');
      _lastOutputOk = success;
      if (res['reboot_needed'] == true && _info != null) _info = _info!.copyWith(rebootNeeded: true);
    });
    showWillySnack(
      context,
      success ? (res['message']?.toString() ?? 'Updates installed.') : serverErrorText(res, 'The update failed.'),
      error: !success,
    );
    if (success) {
      _checked = false;
      _check();
    }
  }

  Future<void> _reboot() async {
    final ok = await confirmStrong(
      context,
      title: 'Reboot ${widget.device.name}?',
      message: 'The server restarts 1 minute from now. Every website, app, container and the Willy hub on it '
          'go offline for a few minutes, and anything unsaved on the server is lost. '
          'You can cancel the reboot until then.',
      ackLabel: 'I understand the websites go down while it restarts',
      confirmLabel: 'Reboot in 1 min',
    );
    if (!ok || !mounted) return;
    setState(() => _rebootBusy = true);
    final res = await ApiService.serverAction(widget.device.id, 'reboot');
    if (!mounted) return;
    final success = res['success'] == true;
    setState(() {
      _rebootBusy = false;
      if (success) _rebootAt = DateTime.now().add(const Duration(minutes: 1));
    });
    showWillySnack(
      context,
      success
          ? (res['message']?.toString() ?? 'The server reboots in 1 minute.')
          : serverErrorText(res, "Couldn't schedule the reboot."),
      error: !success,
    );
  }

  Future<void> _cancelReboot() async {
    setState(() => _rebootBusy = true);
    final res = await ApiService.serverAction(widget.device.id, 'cancel_reboot');
    if (!mounted) return;
    final success = res['success'] == true;
    setState(() {
      _rebootBusy = false;
      if (success) _rebootAt = null;
    });
    showWillySnack(
      context,
      success ? (res['message']?.toString() ?? 'Reboot cancelled.') : serverErrorText(res, "Couldn't cancel the reboot."),
      error: !success,
    );
  }

  @override
  Widget build(BuildContext context) {
    final info = _info;
    return RefreshIndicator(
      onRefresh: _check,
      color: WillyColors.cyan,
      backgroundColor: WillyColors.cardAlt,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 28),
        children: [
          _summaryCard(info),
          if (_rebootAt != null) _rebootBanner(),
          _actionsCard(info),
          if (_lastOutput != null) _outputCard(),
          if (info != null) _packagesCard(info),
        ],
      ),
    );
  }

  Widget _summaryCard(ServerUpdates? info) {
    final chips = <Widget>[
      if (info != null) ...[
        StateChip(
          text: info.packages.isEmpty ? 'Up to date' : '${info.packages.length} update${info.packages.length == 1 ? '' : 's'}',
          color: info.packages.isEmpty ? WillyColors.green : WillyColors.sky,
        ),
        StateChip(
          text: '${info.securityCount} security',
          color: info.securityCount > 0 ? WillyColors.red : WillyColors.green,
        ),
        if (info.rebootNeeded) const StateChip(text: 'Reboot needed', color: WillyColors.amber),
        if (info.newerRelease != null) StateChip(text: 'New release ${info.newerRelease}', color: WillyColors.purple),
      ],
    ];
    return ServerSection(
      title: 'Updates',
      icon: Icons.system_update_alt_rounded,
      trailing: _checking
          ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
          : null,
      children: [
        if (!_online && info == null)
          const ServerNote('The server agent is offline, so updates can\'t be checked.', icon: Icons.cloud_off_rounded)
        else if (_checking && info == null)
          const ServerNote('Checking for updates… dnf can take a few minutes.', busy: true)
        else if (_error != null)
          ServerNote.error(_error!),
        if (info != null) ...[
          if (info.message.isNotEmpty)
            Text(info.message, style: const TextStyle(color: WillyColors.textSoft, fontSize: 13.5, height: 1.35)),
          const SizedBox(height: 10),
          Wrap(spacing: 6, runSpacing: 6, children: chips),
          const SizedBox(height: 8),
          Text(
            [
              if (info.kernel != null) 'Kernel ${info.kernel}',
              if (_at != null) 'checked ${TimeOfDay.fromDateTime(_at!).format(context)}',
            ].join(' · '),
            style: const TextStyle(color: WillyColors.faint, fontSize: 11),
          ),
          if (info.newerRelease != null && (info.releaseNote ?? '').isNotEmpty)
            Theme(
              data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
              child: ExpansionTile(
                tilePadding: EdgeInsets.zero,
                childrenPadding: const EdgeInsets.only(bottom: 6),
                title: const Text('About the new release',
                    style: TextStyle(color: WillyColors.textSoft, fontSize: 13, fontWeight: FontWeight.w600)),
                children: [SelectableText(info.releaseNote!, style: monoStyle.copyWith(fontSize: 11))],
              ),
            ),
        ],
      ],
    );
  }

  Widget _rebootBanner() {
    final at = TimeOfDay.fromDateTime(_rebootAt!).format(context);
    return Container(
      margin: const EdgeInsets.only(bottom: 14),
      padding: const EdgeInsets.fromLTRB(14, 10, 6, 10),
      decoration: BoxDecoration(
        color: WillyColors.red.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: WillyColors.red.withValues(alpha: 0.4)),
      ),
      child: Row(
        children: [
          const Icon(Icons.restart_alt_rounded, color: WillyColors.red, size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: Text('Reboot scheduled for $at. Willy reconnects when the server is back.',
                style: const TextStyle(color: Color(0xFFFCA5A5), fontSize: 12.5)),
          ),
          TextButton(
            onPressed: _rebootBusy || !_online ? null : _cancelReboot,
            child: const Text('Cancel it'),
          ),
        ],
      ),
    );
  }

  Widget _actionsCard(ServerUpdates? info) {
    final busy = _checking || _installing != null;
    final online = _online;
    final hasSecurity = (info?.securityCount ?? 0) > 0;
    final hasAny = (info?.packages.isNotEmpty ?? false);
    return ServerSection(
      title: 'Actions',
      icon: Icons.build_circle_outlined,
      children: [
        if (_installing != null)
          ServerNote(
            'Installing ${_installing == 'security' ? 'security' : 'all'} updates… this can take up to 30 minutes '
            'and continues on the server if you leave.',
            busy: true,
            color: WillyColors.amber,
          ),
        Wrap(
          spacing: 4,
          runSpacing: 2,
          children: [
            SmallAction(
              label: 'Check',
              icon: Icons.refresh_rounded,
              color: WillyColors.sky,
              busy: _checking,
              onPressed: online && !busy ? _check : null,
            ),
            SmallAction(
              label: 'Install security',
              icon: Icons.security_rounded,
              color: WillyColors.green,
              busy: _installing == 'security',
              onPressed: online && !busy && hasSecurity ? () => _install(securityOnly: true) : null,
            ),
            SmallAction(
              label: 'Install all',
              icon: Icons.system_update_alt_rounded,
              color: WillyColors.amber,
              busy: _installing == 'all',
              onPressed: online && !busy && hasAny ? () => _install(securityOnly: false) : null,
            ),
            SmallAction(
              label: 'Reboot',
              icon: Icons.restart_alt_rounded,
              color: WillyColors.red,
              busy: _rebootBusy,
              onPressed: online && _installing == null ? _reboot : null,
            ),
            SmallAction(
              label: 'Cancel reboot',
              icon: Icons.cancel_outlined,
              color: WillyColors.textSoft,
              onPressed: online && !_rebootBusy ? _cancelReboot : null,
            ),
          ],
        ),
      ],
    );
  }

  Widget _outputCard() {
    return ServerSection(
      title: _lastOutputOk ? 'Last install' : 'Last install failed',
      icon: _lastOutputOk ? Icons.check_circle_outline_rounded : Icons.error_outline_rounded,
      trailing: TextButton(
        onPressed: () => showOutputSheet(context,
            title: 'dnf output', subtitle: 'Last lines of the update', text: _lastOutput!, error: !_lastOutputOk),
        child: const Text('Open'),
      ),
      children: [
        Text(
          _lastOutput!.trim().isEmpty ? '(no output)' : _lastOutput!,
          maxLines: 8,
          overflow: TextOverflow.ellipsis,
          style: _lastOutputOk ? monoStyle.copyWith(fontSize: 11) : monoStyle.copyWith(fontSize: 11, color: const Color(0xFFFCA5A5)),
        ),
      ],
    );
  }

  Widget _packagesCard(ServerUpdates info) {
    final pkgs = info.packages;
    final shown = _showAll ? pkgs : pkgs.take(30).toList();
    return ServerSection(
      title: 'Packages',
      icon: Icons.inventory_2_outlined,
      trailing: Text('${pkgs.length}', style: const TextStyle(color: WillyColors.faint, fontSize: 12)),
      children: [
        if (pkgs.isEmpty) const Text('Nothing to update.', style: TextStyle(color: WillyColors.faint, fontSize: 13)),
        for (final p in shown)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(p.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: WillyColors.text, fontSize: 13, fontWeight: FontWeight.w600)),
                Text('${p.version}${p.repo.isNotEmpty ? ' · ${p.repo}' : ''}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: monoStyle.copyWith(fontSize: 11, color: WillyColors.faint)),
              ],
            ),
          ),
        if (pkgs.length > shown.length)
          TextButton(
            onPressed: () => setState(() => _showAll = true),
            child: Text('Show all ${pkgs.length}'),
          ),
      ],
    );
  }
}
