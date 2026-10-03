import 'package:flutter/material.dart';

import '../../models/device.dart';
import '../../models/server.dart';
import '../../services/api_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import '../call_panels/panel_host.dart' show panelErrorText;
import 'server_widgets.dart';

/// Server health at a glance: live stats, the hub's problem list (mutable), pm2 apps,
/// systemd services and Docker containers with Logs / Restart / Stop / Start, and websites.
class ServerOverviewTab extends StatefulWidget {
  final WillyDevice device;
  final bool active;

  const ServerOverviewTab({super.key, required this.device, this.active = true});

  @override
  State<ServerOverviewTab> createState() => _ServerOverviewTabState();
}

class _ServerOverviewTabState extends State<ServerOverviewTab> {
  ServerHealth? _health;
  String? _healthError;
  List<ServerUnit>? _apps; // live from the server agent (server_status)
  List<ServerUnit>? _containers;
  String? _agentError;
  bool _loading = false;
  bool _rechecking = false;
  bool _loadedOnce = false;
  bool _showIgnored = false;
  final Set<String> _busy = {}; // "kind:name" of units with an action running, or "mute:key"

  @override
  void initState() {
    super.initState();
    if (widget.active) _refresh();
  }

  @override
  void didUpdateWidget(ServerOverviewTab old) {
    super.didUpdateWidget(old);
    if (widget.active && !_loadedOnce && !_loading) _refresh();
  }

  /// The hub's health check (no device round trip). [live] also asks the server agent for its
  /// pm2 apps and containers; that call shows in the activity feed, so only on demand.
  Future<void> _refresh({bool recheck = false, bool live = false}) async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _rechecking = recheck;
    });
    final results = await Future.wait([
      ApiService.getServerStatus(refresh: recheck),
      live && widget.device.online
          ? ApiService.deviceAction(widget.device.id, 'server_status', {}, const Duration(seconds: 30))
          : Future.value(<String, dynamic>{'success': false, 'error': live ? 'DEVICE_OFFLINE' : 'SKIPPED'}),
    ]);
    if (!mounted) return;
    final health = results[0];
    final agent = results[1];
    setState(() {
      _loading = false;
      _rechecking = false;
      _loadedOnce = true;
      if (health['success'] != false) {
        _health = ServerHealth.fromJson(health);
        _healthError = null;
      } else {
        _healthError = panelErrorText(health, "Couldn't load the server's health.");
      }
      if (agent['success'] == true) {
        _apps = ServerUnit.apps(agent['apps']);
        _containers = ServerUnit.containers(agent['containers']);
        _agentError = null;
      } else if (agent['error'] == 'SKIPPED') {
        // Hub data only this time: keep the last live lists.
      } else {
        _agentError = agent['error'] == 'DEVICE_OFFLINE'
            ? 'The server agent is offline: showing the hub\'s last check.'
            : panelErrorText(agent, "The server didn't send its app list.");
      }
    });
  }

  // ------------------------------------------------------------- actions

  Future<void> _logs(ServerUnit u) async {
    final key = '${u.kind.wire}:${u.name}';
    setState(() => _busy.add(key));
    final res = await ApiService.deviceAction(widget.device.id, 'control',
        {'kind': u.kind.wire, 'name': u.name, 'action': 'logs', 'lines': 150}, const Duration(seconds: 40));
    if (!mounted) return;
    setState(() => _busy.remove(key));
    final ok = res['success'] == true;
    final text = ok
        ? (res['stdout']?.toString() ?? '')
        : [panelErrorText(res, "Couldn't read the logs."), res['stderr']?.toString() ?? '']
            .where((s) => s.trim().isNotEmpty)
            .join('\n\n');
    await showOutputSheet(context,
        title: 'Logs · ${u.name}', subtitle: 'Last 150 lines of the ${u.kind.label}', text: text, error: !ok);
  }

  Future<void> _control(ServerUnit u, String action) async {
    final (verb, done) = switch (action) {
      'restart' => ('Restart', 'Restarted'),
      'stop' => ('Stop', 'Stopped'),
      _ => ('Start', 'Started'),
    };
    final what = '${u.kind.label} "${u.name}"';
    final ok = await confirmAction(
      context,
      title: '$verb ${u.name}?',
      message: switch (action) {
        'restart' => 'Restarts the $what on ${widget.device.name}. It is briefly unavailable.',
        'stop' => 'Stops the $what on ${widget.device.name}. Anything it serves goes down until it is started again.',
        _ => 'Starts the $what on ${widget.device.name}.',
      },
      confirmLabel: verb,
      color: action == 'stop' ? WillyColors.red : (action == 'restart' ? WillyColors.amber : WillyColors.green),
    );
    if (!ok || !mounted) return;
    final key = '${u.kind.wire}:${u.name}';
    setState(() => _busy.add(key));
    final res = await ApiService.deviceAction(widget.device.id, 'control',
        {'kind': u.kind.wire, 'name': u.name, 'action': action}, const Duration(seconds: 100));
    if (!mounted) return;
    setState(() => _busy.remove(key));
    final success = res['success'] == true;
    showWillySnack(
      context,
      success
          ? (res['message']?.toString() ?? '$done ${u.name}.')
          : '$verb ${u.name} failed: ${panelErrorText(res, 'no answer')}',
      error: !success,
    );
    if (success) {
      await Future.delayed(const Duration(milliseconds: 1200));
      if (mounted) _refresh(live: true);
    }
  }

  Future<void> _mute(String key, bool ignore) async {
    setState(() => _busy.add('mute:$key'));
    final res = await ApiService.ignoreServerProblem(key, ignore: ignore);
    if (!mounted) return;
    setState(() => _busy.remove('mute:$key'));
    if (res['success'] == false) {
      showWillySnack(context, panelErrorText(res, "Couldn't change that."), error: true);
      return;
    }
    showWillySnack(context, ignore ? 'Muted: no more alerts for it.' : 'Unmuted.');
    _refresh();
  }

  // ------------------------------------------------------------------ UI

  @override
  Widget build(BuildContext context) {
    final h = _health;
    final apps = _apps ?? h?.apps ?? const [];
    final containers = _containers ?? h?.containers ?? const [];
    final services = h?.services ?? const <ServerUnit>[];
    return RefreshIndicator(
      onRefresh: () => _refresh(live: true),
      color: WillyColors.cyan,
      backgroundColor: WillyColors.cardAlt,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
        children: [
          if (_loading && !_loadedOnce) const LinearProgressIndicator(minHeight: 2, color: WillyColors.cyan),
          _healthCard(h),
          _statsCard(widget.device),
          if (_agentError != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 10, left: 4, right: 4),
              child: Text(_agentError!, style: const TextStyle(color: WillyColors.amber, fontSize: 12)),
            ),
          _unitsSection('Apps (pm2)', Icons.apps_rounded, apps, 'No pm2 apps found.'),
          _unitsSection('Services', Icons.settings_suggest_rounded, services,
              h == null ? 'Waiting for the hub\'s check…' : 'No services watched.'),
          _unitsSection('Containers', Icons.inventory_2_outlined, containers, 'No Docker containers.'),
          _sitesSection(h),
        ],
      ),
    );
  }

  Widget _healthCard(ServerHealth? h) {
    if (h == null) {
      return ServerSection(
        title: 'Health',
        icon: Icons.monitor_heart_outlined,
        children: [
          Text(_healthError ?? (_loading ? 'Checking…' : 'Pull down to check the server.'),
              style: TextStyle(color: _healthError != null ? WillyColors.red : WillyColors.muted, fontSize: 13)),
        ],
      );
    }
    final n = h.problems.length;
    final color = n == 0 ? WillyColors.green : (h.problems.any((p) => p.key.startsWith('site:')) ? WillyColors.red : WillyColors.amber);
    return ServerSection(
      title: 'Health',
      icon: Icons.monitor_heart_outlined,
      trailing: StateChip(text: n == 0 ? 'Healthy' : (n == 1 ? '1 problem' : '$n problems'), color: color),
      children: [
        if (h.summary.isNotEmpty)
          Text(h.summary, style: const TextStyle(color: WillyColors.textSoft, fontSize: 13.5)),
        if (h.checkedAt != null)
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text('Checked ${timeAgo(h.checkedAt)}',
                style: const TextStyle(color: WillyColors.faint, fontSize: 11)),
          ),
        for (final p in h.problems) _problemRow(p, muted: false),
        if (h.ignored.isNotEmpty) ...[
          const SizedBox(height: 6),
          InkWell(
            onTap: () => setState(() => _showIgnored = !_showIgnored),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Row(
                children: [
                  Icon(_showIgnored ? Icons.expand_less_rounded : Icons.expand_more_rounded,
                      size: 18, color: WillyColors.muted),
                  const SizedBox(width: 4),
                  Text('Muted (${h.ignored.length})',
                      style: const TextStyle(color: WillyColors.muted, fontSize: 12.5, fontWeight: FontWeight.w600)),
                ],
              ),
            ),
          ),
          if (_showIgnored)
            for (final key in h.ignored) _problemRow(ServerProblem(key, problemLabel(key)), muted: true),
        ],
      ],
    );
  }

  Widget _problemRow(ServerProblem p, {required bool muted}) {
    final busy = _busy.contains('mute:${p.key}');
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Row(
        children: [
          Icon(muted ? Icons.notifications_off_outlined : Icons.error_outline_rounded,
              size: 18, color: muted ? WillyColors.faint : WillyColors.amber),
          const SizedBox(width: 8),
          Expanded(
            child: Text(p.message,
                style: TextStyle(color: muted ? WillyColors.faint : WillyColors.textSoft, fontSize: 13)),
          ),
          busy
              ? const Padding(
                  padding: EdgeInsets.all(10),
                  child: SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                )
              : TextButton(
                  style: TextButton.styleFrom(visualDensity: VisualDensity.compact),
                  onPressed: () => _mute(p.key, !muted),
                  child: Text(muted ? 'Unmute' : 'Mute'),
                ),
        ],
      ),
    );
  }

  Widget _statsCard(WillyDevice d) {
    final ram = d.ramUsedGb != null && d.ramTotalGb != null
        ? '${d.ramUsedGb!.toStringAsFixed(1)} / ${d.ramTotalGb!.toStringAsFixed(1)} GB'
        : null;
    final disk = d.diskFreeGb != null
        ? '${d.diskFreeGb!.round()} GB free${d.diskTotalGb != null ? ' of ${d.diskTotalGb!.round()} GB' : ''}'
        : null;
    final chips = [
      InfoChip(icon: Icons.speed_rounded, label: 'Load 1 · 5 · 15', value: formatLoad(d.load1, d.load5, d.load15)),
      InfoChip(icon: Icons.timer_outlined, label: 'Uptime', value: formatHours(d.uptimeHours), color: WillyColors.sky),
      InfoChip(
          icon: Icons.memory_rounded, label: 'Cores', value: d.cores?.toString() ?? '—', color: WillyColors.purple),
      InfoChip(
          icon: Icons.apps_rounded,
          label: 'Processes',
          value: d.processCount?.toString() ?? '—',
          color: WillyColors.pink),
      InfoChip(
          icon: Icons.north_rounded,
          label: 'Sent since boot',
          value: d.netSentMb == null ? '—' : formatMb(d.netSentMb),
          color: WillyColors.amber),
      InfoChip(
          icon: Icons.south_rounded,
          label: 'Received since boot',
          value: d.netRecvMb == null ? '—' : formatMb(d.netRecvMb),
          color: WillyColors.green),
    ];
    return ServerSection(
      title: 'Resources',
      icon: Icons.analytics_outlined,
      trailing: Text(d.online ? 'live' : 'last known',
          style: TextStyle(color: d.online ? WillyColors.green : WillyColors.faint, fontSize: 11)),
      children: [
        StatBar(label: 'CPU', pct: d.cpuPct, caption: d.cores != null ? '${d.cores} cores' : null),
        const SizedBox(height: 10),
        StatBar(label: 'RAM', pct: d.ramPct, caption: ram),
        const SizedBox(height: 10),
        StatBar(label: 'Disk', pct: d.diskPct, caption: disk),
        const SizedBox(height: 10),
        StatBar(label: 'Swap', pct: d.swapPct),
        const SizedBox(height: 14),
        LayoutBuilder(builder: (context, c) {
          final w = (c.maxWidth - 8) / 2;
          return Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [for (final chip in chips) SizedBox(width: w, child: chip)],
          );
        }),
      ],
    );
  }

  Widget _unitsSection(String title, IconData icon, List<ServerUnit> units, String empty) {
    final bad = units.where((u) => u.health == UnitHealth.bad).length;
    return ServerSection(
      title: title,
      icon: icon,
      trailing: units.isEmpty
          ? null
          : Text(bad > 0 ? '$bad of ${units.length} failing' : '${units.length}',
              style: TextStyle(color: bad > 0 ? WillyColors.red : WillyColors.faint, fontSize: 12)),
      children: [
        if (units.isEmpty)
          Text(_loading && !_loadedOnce ? 'Loading…' : empty,
              style: const TextStyle(color: WillyColors.faint, fontSize: 13)),
        for (final u in units) _unitTile(u),
      ],
    );
  }

  Widget _unitTile(ServerUnit u) {
    final busy = _busy.contains('${u.kind.wire}:${u.name}');
    final online = widget.device.online;
    final running = u.health == UnitHealth.good || u.health == UnitHealth.warn;
    final details = [
      if (u.restarts != null) '${u.restarts} restarts',
      if (u.memoryMb != null && u.memoryMb! > 0) formatMb(u.memoryMb),
    ].join(' · ');
    Widget btn(String label, IconData icon, VoidCallback? onTap, {Color color = WillyColors.textSoft}) => TextButton.icon(
          style: TextButton.styleFrom(
            foregroundColor: color,
            visualDensity: VisualDensity.compact,
            padding: const EdgeInsets.symmetric(horizontal: 8),
            minimumSize: const Size(0, 32),
          ),
          onPressed: online && !busy ? onTap : null,
          icon: Icon(icon, size: 16),
          label: Text(label, style: const TextStyle(fontSize: 12.5)),
        );
    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.fromLTRB(12, 10, 6, 4),
      decoration: BoxDecoration(
        color: WillyColors.card,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: u.health == UnitHealth.bad ? WillyColors.red.withValues(alpha: 0.35) : WillyColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(u.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: WillyColors.text, fontWeight: FontWeight.w600, fontSize: 14)),
              ),
              if (busy)
                const Padding(
                  padding: EdgeInsets.only(right: 8),
                  child: SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
                ),
              Padding(padding: const EdgeInsets.only(right: 6), child: StateChip.of(u.state)),
            ],
          ),
          if (details.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text(details, style: const TextStyle(color: WillyColors.faint, fontSize: 11.5)),
            ),
          Wrap(
            spacing: 2,
            children: [
              btn('Logs', Icons.subject_rounded, () => _logs(u), color: WillyColors.sky),
              btn('Restart', Icons.restart_alt_rounded, () => _control(u, 'restart'), color: WillyColors.amber),
              btn('Stop', Icons.stop_circle_outlined, running ? () => _control(u, 'stop') : null, color: WillyColors.red),
              btn('Start', Icons.play_circle_outline_rounded, running ? null : () => _control(u, 'start'),
                  color: WillyColors.green),
            ],
          ),
        ],
      ),
    );
  }

  Widget _sitesSection(ServerHealth? h) {
    final sites = h?.sites ?? const <ServerSite>[];
    final down = sites.where((s) => !s.up).length;
    return ServerSection(
      title: 'Websites',
      icon: Icons.language_rounded,
      trailing: _rechecking
          ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
          : TextButton.icon(
              style: TextButton.styleFrom(visualDensity: VisualDensity.compact),
              onPressed: _loading ? null : () => _refresh(recheck: true),
              icon: const Icon(Icons.refresh_rounded, size: 16),
              label: const Text('Re-check'),
            ),
      children: [
        if (sites.isEmpty)
          Text(h == null ? 'Waiting for the hub\'s check…' : 'No websites watched.',
              style: const TextStyle(color: WillyColors.faint, fontSize: 13))
        else ...[
          Text(
            [
              down == 0 ? 'All ${sites.length} up' : '$down of ${sites.length} down',
              if (h?.sitesCheckedAt != null) 'checked ${timeAgo(h!.sitesCheckedAt)}',
            ].join(' · '),
            style: TextStyle(color: down == 0 ? WillyColors.green : WillyColors.red, fontSize: 12),
          ),
          for (final s in sites) _siteRow(s),
        ],
      ],
    );
  }

  Widget _siteRow(ServerSite s) {
    final cert = certHealth(s.certDays);
    final detail = s.up
        ? [if (s.status != null) 'HTTP ${s.status}', if (s.ms != null) '${s.ms!.round()} ms'].join(' · ')
        : (s.error ?? (s.status != null ? 'HTTP ${s.status}' : 'Not answering'));
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: Row(
        children: [
          Container(
            width: 9,
            height: 9,
            decoration: BoxDecoration(color: s.up ? WillyColors.green : WillyColors.red, shape: BoxShape.circle),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(s.domain,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: WillyColors.text, fontSize: 13.5, fontWeight: FontWeight.w600)),
                Text(detail.isEmpty ? (s.up ? 'Up' : 'Down') : detail,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(color: s.up ? WillyColors.faint : const Color(0xFFFCA5A5), fontSize: 11.5)),
              ],
            ),
          ),
          const SizedBox(width: 8),
          StateChip(text: certText(s.certDays), color: healthColor(cert)),
        ],
      ),
    );
  }
}
