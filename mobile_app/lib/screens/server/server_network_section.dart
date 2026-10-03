import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/device.dart';
import '../../models/server.dart';
import '../../services/api_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import 'server_widgets.dart';

/// Network (IPs, live rate, listening ports) and security (failed SSH logins, who is
/// logged in, SSH settings). Read-only.
class ServerNetworkSection extends StatefulWidget {
  final WillyDevice device;
  final bool active;

  const ServerNetworkSection({super.key, required this.device, this.active = true});

  @override
  State<ServerNetworkSection> createState() => _ServerNetworkSectionState();
}

class _ServerNetworkSectionState extends State<ServerNetworkSection> {
  ServerNetwork? _net;
  ServerSecurity? _sec;
  String? _netError;
  String? _secError;
  bool _netLoading = false;
  bool _secLoading = false;
  bool _loaded = false;
  bool _showLocal = false;
  DateTime? _rateAt;
  Timer? _poll;

  bool get _online => widget.device.online;

  @override
  void initState() {
    super.initState();
    if (widget.active && _online) _loadAll();
    // The live rate refreshes every 20 s while this section is on screen.
    _poll = Timer.periodic(const Duration(seconds: 20), (_) {
      if (mounted && widget.active && _online && _loaded && !_netLoading) _loadNetwork();
    });
  }

  @override
  void didUpdateWidget(ServerNetworkSection old) {
    super.didUpdateWidget(old);
    if (widget.active && _online && !_loaded && !_netLoading && !_secLoading) _loadAll();
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _loadAll() async {
    _loaded = true;
    await Future.wait([_loadNetwork(), _loadSecurity()]);
  }

  Future<void> _loadNetwork() async {
    if (_netLoading) return;
    setState(() {
      _netLoading = true;
      _netError = null;
    });
    final res = await ApiService.serverAction(widget.device.id, 'network');
    if (!mounted) return;
    setState(() {
      _netLoading = false;
      if (res['success'] == true) {
        _net = ServerNetwork.fromJson(res);
        _rateAt = DateTime.now();
      } else {
        _netError = serverErrorText(res, "Couldn't read the network.");
      }
    });
  }

  Future<void> _loadSecurity() async {
    if (_secLoading) return;
    setState(() {
      _secLoading = true;
      _secError = null;
    });
    final res = await ApiService.serverAction(widget.device.id, 'security');
    if (!mounted) return;
    setState(() {
      _secLoading = false;
      if (res['success'] == true) {
        _sec = ServerSecurity.fromJson(res);
      } else {
        _secError = serverErrorText(res, "Couldn't read the security status.");
      }
    });
  }

  void _copy(String text) {
    Clipboard.setData(ClipboardData(text: text));
    showWillySnack(context, 'Copied $text');
  }

  Widget _spinnerOr(bool loading, VoidCallback? onTap) => loading
      ? const Padding(
          padding: EdgeInsets.all(8),
          child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
        )
      : IconButton(
          tooltip: 'Refresh',
          visualDensity: VisualDensity.compact,
          onPressed: _online ? onTap : null,
          icon: const Icon(Icons.refresh_rounded, size: 20),
        );

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: _loadAll,
      color: WillyColors.cyan,
      backgroundColor: WillyColors.cardAlt,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 28),
        children: [
          if (!_online && _net == null && _sec == null)
            const ServerNote('The server agent is offline, so the network and security checks are unavailable.',
                icon: Icons.cloud_off_rounded),
          _networkCard(),
          _portsCard(),
          _securityCard(),
          _sessionsCard(),
        ],
      ),
    );
  }

  Widget _networkCard() {
    final n = _net;
    return ServerSection(
      title: 'Network',
      icon: Icons.lan_outlined,
      trailing: _spinnerOr(_netLoading, _loadNetwork),
      children: [
        if (_netError != null) ServerNote.error(_netError!),
        if (n == null && _netLoading) const ServerNote('Reading interfaces and ports…', busy: true),
        if (n != null) ...[
          Row(
            children: [
              Expanded(child: _rateTile(Icons.south_rounded, 'Down', formatKbit(n.downKbps), WillyColors.green)),
              const SizedBox(width: 8),
              Expanded(child: _rateTile(Icons.north_rounded, 'Up', formatKbit(n.upKbps), WillyColors.sky)),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            [
              if (n.established != null) '${n.established} active connections',
              if (_rateAt != null) 'rate at ${TimeOfDay.fromDateTime(_rateAt!).format(context)}, refreshes every 20 s',
            ].join(' · '),
            style: const TextStyle(color: WillyColors.faint, fontSize: 11),
          ),
          const SizedBox(height: 8),
          for (final i in n.interfaces)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 5),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 74,
                    child: Text(i.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: WillyColors.text, fontSize: 13, fontWeight: FontWeight.w600)),
                  ),
                  Expanded(
                    child: Wrap(
                      spacing: 6,
                      runSpacing: 4,
                      children: [
                        if (i.state.isNotEmpty) StateChip(text: i.state, color: i.state == 'UP' ? WillyColors.green : WillyColors.faint),
                        for (final a in i.addresses)
                          InkWell(
                            onTap: () => _copy(a.split('/').first),
                            child: Text(a, style: monoStyle.copyWith(fontSize: 11.5, color: WillyColors.textSoft)),
                          ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          if (n.interfaces.isEmpty)
            const Text('No interfaces reported.', style: TextStyle(color: WillyColors.faint, fontSize: 13)),
        ],
      ],
    );
  }

  Widget _rateTile(IconData icon, String label, String value, Color color) => InfoChip(
        icon: icon,
        label: label,
        value: value,
        color: color,
      );

  Widget _portsCard() {
    final n = _net;
    if (n == null) return const SizedBox.shrink();
    final public = n.listening.where((p) => p.public).toList();
    final local = n.listening.where((p) => !p.public).toList();
    return ServerSection(
      title: 'Listening ports',
      icon: Icons.settings_ethernet_rounded,
      trailing: Text('${public.length} public',
          style: TextStyle(color: public.isEmpty ? WillyColors.faint : WillyColors.amber, fontSize: 12)),
      children: [
        const Text('Public ports answer from the internet (unless a firewall blocks them).',
            style: TextStyle(color: WillyColors.faint, fontSize: 11.5)),
        const SizedBox(height: 4),
        for (final p in public) _portRow(p),
        if (local.isNotEmpty)
          TextButton.icon(
            style: TextButton.styleFrom(visualDensity: VisualDensity.compact, padding: EdgeInsets.zero),
            onPressed: () => setState(() => _showLocal = !_showLocal),
            icon: Icon(_showLocal ? Icons.expand_less_rounded : Icons.expand_more_rounded, size: 18),
            label: Text('${_showLocal ? 'Hide' : 'Show'} ${local.length} local-only'),
          ),
        if (_showLocal) for (final p in local) _portRow(p),
      ],
    );
  }

  Widget _portRow(ListeningPort p) {
    final color = p.public ? WillyColors.amber : WillyColors.faint;
    return Container(
      margin: const EdgeInsets.only(top: 6),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      decoration: BoxDecoration(
        color: p.public ? WillyColors.amber.withValues(alpha: 0.07) : WillyColors.card,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: p.public ? WillyColors.amber.withValues(alpha: 0.35) : WillyColors.border),
      ),
      child: Row(
        children: [
          SizedBox(
            width: 58,
            child: Text(p.port,
                style: TextStyle(color: p.public ? WillyColors.text : WillyColors.textSoft, fontSize: 14, fontWeight: FontWeight.w700)),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(p.process.isEmpty ? 'unknown process' : '${p.process}${p.pid != null ? ' · ${p.pid}' : ''}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: WillyColors.textSoft, fontSize: 12.5)),
                Text('${p.proto} · ${p.address.isEmpty ? '*' : p.address}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: monoStyle.copyWith(fontSize: 11, color: WillyColors.faint)),
              ],
            ),
          ),
          StateChip(text: p.public ? 'public' : 'local', color: color),
        ],
      ),
    );
  }

  Widget _securityCard() {
    final s = _sec;
    return ServerSection(
      title: 'Security',
      icon: Icons.shield_outlined,
      trailing: _spinnerOr(_secLoading, _loadSecurity),
      children: [
        if (_secError != null) ServerNote.error(_secError!),
        if (s == null && _secLoading) const ServerNote('Reading SSH logs and settings…', busy: true),
        if (s != null) ...[
          if (s.message.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(s.message, style: const TextStyle(color: WillyColors.textSoft, fontSize: 13)),
            ),
          KeyValueRow('Failed SSH logins (24 h)',
              StateChip(text: '${s.failedSsh24h}', color: healthColor(s.failedHealth))),
          KeyValueRow('SSH password login',
              StateChip(text: s.sshPasswordLogin == 'yes' ? 'ON' : s.sshPasswordLogin, color: healthColor(s.passwordHealth))),
          KeyValueRow('Root login', StateChip(text: s.rootLogin, color: healthColor(s.rootHealth))),
          KeyValueRow('fail2ban', StateChip(text: s.fail2ban, color: healthColor(s.fail2banHealth))),
          if (s.topAttackers.isNotEmpty) ...[
            const SizedBox(height: 8),
            const Text('Most failed attempts from',
                style: TextStyle(color: WillyColors.muted, fontSize: 12, fontWeight: FontWeight.w600)),
            for (final a in s.topAttackers)
              InkWell(
                onTap: () => _copy(a.ip),
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  child: Row(
                    children: [
                      Expanded(child: Text(a.ip, style: monoStyle.copyWith(fontSize: 12.5, color: WillyColors.textSoft))),
                      Text('${a.attempts}×',
                          style: const TextStyle(color: WillyColors.red, fontSize: 12.5, fontWeight: FontWeight.w700)),
                    ],
                  ),
                ),
              ),
          ],
        ],
      ],
    );
  }

  Widget _sessionsCard() {
    final s = _sec;
    if (s == null) return const SizedBox.shrink();
    Widget lines(List<String> l, String empty) => l.isEmpty
        ? Text(empty, style: const TextStyle(color: WillyColors.faint, fontSize: 12.5))
        : SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: SelectableText(l.join('\n'), style: monoStyle.copyWith(fontSize: 11)),
          );
    return ServerSection(
      title: 'Sessions',
      icon: Icons.people_outline_rounded,
      trailing: Text('${s.loggedIn.length} now', style: const TextStyle(color: WillyColors.faint, fontSize: 12)),
      children: [
        const Text('Logged in now', style: TextStyle(color: WillyColors.muted, fontSize: 12, fontWeight: FontWeight.w600)),
        const SizedBox(height: 4),
        lines(s.loggedIn, 'Nobody is logged in.'),
        const SizedBox(height: 12),
        const Text('Recent logins', style: TextStyle(color: WillyColors.muted, fontSize: 12, fontWeight: FontWeight.w600)),
        const SizedBox(height: 4),
        lines(s.recentLogins, 'No recent logins recorded.'),
      ],
    );
  }
}
