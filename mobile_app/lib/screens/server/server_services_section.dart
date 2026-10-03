import 'package:flutter/material.dart';

import '../../models/device.dart';
import '../../models/server.dart';
import '../../services/api_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import 'server_widgets.dart';

/// Every systemd service (failed first) with start / stop / restart / logs and the
/// start-at-boot switch, plus the crontab and systemd timers.
class ServerServicesSection extends StatefulWidget {
  final WillyDevice device;
  final bool active;

  const ServerServicesSection({super.key, required this.device, this.active = true});

  @override
  State<ServerServicesSection> createState() => _ServerServicesSectionState();
}

class _ServerServicesSectionState extends State<ServerServicesSection> {
  List<SystemService> _services = const [];
  ServerJobs? _jobs;
  String? _error;
  String? _jobsError;
  String _message = '';
  bool _loading = false;
  bool _jobsLoading = false;
  bool _loaded = false;
  ServiceFilter _filter = ServiceFilter.all;
  final _query = TextEditingController();
  final Set<String> _busy = {};
  int _limit = 60;

  bool get _online => widget.device.online;

  @override
  void initState() {
    super.initState();
    if (widget.active && _online) _loadAll();
  }

  @override
  void didUpdateWidget(ServerServicesSection old) {
    super.didUpdateWidget(old);
    if (widget.active && _online && !_loaded && !_loading) _loadAll();
  }

  @override
  void dispose() {
    _query.dispose();
    super.dispose();
  }

  Future<void> _loadAll() async {
    _loaded = true;
    await Future.wait([_loadServices(), _loadJobs()]);
  }

  Future<void> _loadServices() async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    final res = await ApiService.serverAction(widget.device.id, 'services_list');
    if (!mounted) return;
    setState(() {
      _loading = false;
      if (res['success'] == true) {
        _services = SystemService.listFrom(res['services']);
        _message = res['message']?.toString() ?? '';
      } else {
        _error = serverErrorText(res, "Couldn't list the services.");
      }
    });
  }

  Future<void> _loadJobs() async {
    if (_jobsLoading) return;
    setState(() {
      _jobsLoading = true;
      _jobsError = null;
    });
    final res = await ApiService.serverAction(widget.device.id, 'timers');
    if (!mounted) return;
    setState(() {
      _jobsLoading = false;
      if (res['success'] == true) {
        _jobs = ServerJobs.fromJson(res);
      } else {
        _jobsError = serverErrorText(res, "Couldn't read the scheduled jobs.");
      }
    });
  }

  Future<void> _logs(SystemService s) async {
    setState(() => _busy.add(s.name));
    final res = await ApiService.deviceAction(widget.device.id, 'control',
        {'kind': 'service', 'name': s.name, 'action': 'logs', 'lines': 150}, const Duration(seconds: 45));
    if (!mounted) return;
    setState(() => _busy.remove(s.name));
    final ok = res['success'] == true;
    final text = ok
        ? (res['stdout']?.toString() ?? '')
        : [serverErrorText(res, "Couldn't read the logs."), res['stderr']?.toString() ?? '']
            .where((t) => t.trim().isNotEmpty)
            .join('\n\n');
    await showOutputSheet(context, title: 'Logs · ${s.name}', subtitle: 'Last 150 lines of the service', text: text, error: !ok);
  }

  Future<void> _control(SystemService s, String action) async {
    final (verb, done) = switch (action) {
      'restart' => ('Restart', 'Restarted'),
      'stop' => ('Stop', 'Stopped'),
      _ => ('Start', 'Started'),
    };
    final ok = await confirmAction(
      context,
      title: '$verb ${s.name}?',
      message: switch (action) {
        'restart' => 'Restarts the service "${s.name}" on ${widget.device.name}. It is briefly unavailable.',
        'stop' => 'Stops the service "${s.name}" on ${widget.device.name}. Anything it serves goes down until it is '
            'started again.',
        _ => 'Starts the service "${s.name}" on ${widget.device.name}.',
      },
      confirmLabel: verb,
      color: action == 'stop' ? WillyColors.red : (action == 'restart' ? WillyColors.amber : WillyColors.green),
    );
    if (!ok || !mounted) return;
    setState(() => _busy.add(s.name));
    final res = await ApiService.serverAction(
        widget.device.id, 'control', {'kind': 'service', 'name': s.name, 'action': action});
    if (!mounted) return;
    setState(() => _busy.remove(s.name));
    final success = res['success'] == true;
    showWillySnack(
      context,
      success ? (res['message']?.toString() ?? '$done ${s.name}.') : '$verb ${s.name} failed: ${serverErrorText(res, 'no answer')}',
      error: !success,
    );
    if (success) _loadServices();
  }

  Future<void> _boot(SystemService s, bool enable) async {
    final ok = await confirmAction(
      context,
      title: enable ? 'Start ${s.name} at boot?' : 'Stop starting ${s.name} at boot?',
      message: enable
          ? 'The service "${s.name}" will start by itself whenever ${widget.device.name} boots. Nothing changes right now.'
          : 'The service "${s.name}" will no longer start when ${widget.device.name} boots. It keeps running now; '
              'after the next reboot it stays off until started by hand.',
      confirmLabel: enable ? 'Enable' : 'Disable',
      color: enable ? WillyColors.green : WillyColors.amber,
    );
    if (!ok || !mounted) return;
    setState(() => _busy.add(s.name));
    final res = await ApiService.serverAction(widget.device.id, 'service_boot', {'name': s.name, 'enable': enable});
    if (!mounted) return;
    final success = res['success'] == true;
    setState(() {
      _busy.remove(s.name);
      if (success) {
        _services = [for (final x in _services) x.name == s.name ? x.withBoot(enable ? 'enabled' : 'disabled') : x];
      }
    });
    showWillySnack(
      context,
      success ? (res['message']?.toString() ?? 'Changed.') : serverErrorText(res, "Couldn't change that."),
      error: !success,
    );
  }

  @override
  Widget build(BuildContext context) {
    final list = filterServices(_services, _query.text, _filter);
    final failed = _services.where((s) => s.failed).length;
    final shown = list.take(_limit).toList();
    return RefreshIndicator(
      onRefresh: _loadAll,
      color: WillyColors.cyan,
      backgroundColor: WillyColors.cardAlt,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 28),
        children: [
          ServerSection(
            title: 'Services',
            icon: Icons.settings_suggest_rounded,
            trailing: _loading
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : IconButton(
                    tooltip: 'Refresh',
                    visualDensity: VisualDensity.compact,
                    onPressed: _online ? _loadServices : null,
                    icon: const Icon(Icons.refresh_rounded, size: 20),
                  ),
            children: [
              if (!_online && _services.isEmpty)
                const ServerNote('The server agent is offline.', icon: Icons.cloud_off_rounded),
              if (_error != null) ServerNote.error(_error!),
              if (_loading && _services.isEmpty) const ServerNote('Listing services…', busy: true),
              if (_message.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text(_message,
                      style: TextStyle(color: failed > 0 ? const Color(0xFFFCA5A5) : WillyColors.textSoft, fontSize: 13)),
                ),
              TextField(
                controller: _query,
                onChanged: (_) => setState(() => _limit = 60),
                style: const TextStyle(fontSize: 14),
                decoration: InputDecoration(
                  isDense: true,
                  hintText: 'Filter by name or description',
                  prefixIcon: const Icon(Icons.search_rounded, size: 20),
                  suffixIcon: _query.text.isEmpty
                      ? null
                      : IconButton(
                          icon: const Icon(Icons.close_rounded, size: 18),
                          onPressed: () => setState(() => _query.clear()),
                        ),
                ),
              ),
              const SizedBox(height: 8),
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: [
                    for (final f in ServiceFilter.values)
                      Padding(
                        padding: const EdgeInsets.only(right: 6),
                        child: ChoiceChip(
                          visualDensity: VisualDensity.compact,
                          label: Text(switch (f) {
                            ServiceFilter.all => 'All ${_services.length}',
                            ServiceFilter.running => 'Running',
                            ServiceFilter.failed => 'Failed $failed',
                            ServiceFilter.stopped => 'Stopped',
                          }),
                          selected: _filter == f,
                          onSelected: (_) => setState(() {
                            _filter = f;
                            _limit = 60;
                          }),
                        ),
                      ),
                  ],
                ),
              ),
              if (_services.isNotEmpty && list.isEmpty)
                const Padding(
                  padding: EdgeInsets.only(top: 10),
                  child: Text('No services match.', style: TextStyle(color: WillyColors.faint, fontSize: 13)),
                ),
              for (final s in shown) _serviceTile(s),
              if (list.length > shown.length)
                TextButton(
                  onPressed: () => setState(() => _limit += 100),
                  child: Text('Show more (${list.length - shown.length} left)'),
                ),
            ],
          ),
          _jobsCard(),
        ],
      ),
    );
  }

  Widget _serviceTile(SystemService s) {
    final busy = _busy.contains(s.name);
    final online = _online;
    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.fromLTRB(12, 8, 0, 8),
      decoration: BoxDecoration(
        color: WillyColors.card,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: s.failed ? WillyColors.red.withValues(alpha: 0.4) : WillyColors.border),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(s.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: WillyColors.text, fontWeight: FontWeight.w600, fontSize: 13.5)),
                if (s.description.isNotEmpty)
                  Text(s.description,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: WillyColors.faint, fontSize: 11.5)),
                const SizedBox(height: 5),
                Wrap(
                  spacing: 6,
                  runSpacing: 4,
                  children: [
                    StateChip(text: s.stateText, color: healthColor(s.health)),
                    if (s.boot.isNotEmpty)
                      StateChip(
                        text: s.startsAtBoot ? 'starts at boot' : (s.boot == 'disabled' ? 'not at boot' : s.boot),
                        color: s.startsAtBoot ? WillyColors.sky : WillyColors.faint,
                      ),
                  ],
                ),
              ],
            ),
          ),
          busy
              ? const Padding(
                  padding: EdgeInsets.all(14),
                  child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
                )
              : PopupMenuButton<String>(
                  tooltip: 'Actions for ${s.name}',
                  enabled: online,
                  icon: const Icon(Icons.more_vert_rounded, color: WillyColors.textSoft),
                  color: WillyColors.cardAlt,
                  onSelected: (v) => switch (v) {
                    'logs' => _logs(s),
                    'boot_on' => _boot(s, true),
                    'boot_off' => _boot(s, false),
                    _ => _control(s, v),
                  },
                  itemBuilder: (ctx) => [
                    _item('logs', 'Logs', Icons.subject_rounded, WillyColors.sky),
                    _item('restart', 'Restart', Icons.restart_alt_rounded, WillyColors.amber),
                    if (s.running)
                      _item('stop', 'Stop', Icons.stop_circle_outlined, WillyColors.red)
                    else
                      _item('start', 'Start', Icons.play_circle_outline_rounded, WillyColors.green),
                    if (s.bootToggleable)
                      s.boot == 'enabled'
                          ? _item('boot_off', "Don't start at boot", Icons.toggle_off_outlined, WillyColors.amber)
                          : _item('boot_on', 'Start at boot', Icons.toggle_on_outlined, WillyColors.green),
                  ],
                ),
        ],
      ),
    );
  }

  PopupMenuItem<String> _item(String value, String label, IconData icon, Color color) => PopupMenuItem(
        value: value,
        child: Row(
          children: [
            Icon(icon, size: 18, color: color),
            const SizedBox(width: 10),
            Text(label, style: const TextStyle(color: WillyColors.textSoft, fontSize: 14)),
          ],
        ),
      );

  Widget _jobsCard() {
    final j = _jobs;
    return ServerSection(
      title: 'Scheduled jobs',
      icon: Icons.schedule_rounded,
      trailing: _jobsLoading
          ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
          : IconButton(
              tooltip: 'Refresh',
              visualDensity: VisualDensity.compact,
              onPressed: _online ? _loadJobs : null,
              icon: const Icon(Icons.refresh_rounded, size: 20),
            ),
      children: [
        if (_jobsError != null) ServerNote.error(_jobsError!),
        if (j == null && _jobsLoading) const ServerNote('Reading cron and timers…', busy: true),
        if (j != null) ...[
          Text('Cron (${j.cron.length})',
              style: const TextStyle(color: WillyColors.muted, fontSize: 12, fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          if (j.cron.isEmpty)
            const Text('No cron jobs for the server user.', style: TextStyle(color: WillyColors.faint, fontSize: 12.5))
          else
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: SelectableText(j.cron.join('\n'), style: monoStyle.copyWith(fontSize: 11.5)),
            ),
          const SizedBox(height: 12),
          Text('Systemd timers (${j.timers.length})',
              style: const TextStyle(color: WillyColors.muted, fontSize: 12, fontWeight: FontWeight.w600)),
          for (final t in j.timers)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(t.timer,
                      style: const TextStyle(color: WillyColors.textSoft, fontSize: 12.5, fontWeight: FontWeight.w600)),
                  Text(t.line,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: monoStyle.copyWith(fontSize: 10.5, color: WillyColors.faint)),
                ],
              ),
            ),
          if (j.timers.isEmpty)
            const Text('No timers.', style: TextStyle(color: WillyColors.faint, fontSize: 12.5)),
        ],
      ],
    );
  }
}
