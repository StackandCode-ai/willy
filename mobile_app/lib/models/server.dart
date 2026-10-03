import 'device.dart';

/// Data for the Willy Server screen: the Linux machine (device_type "server") that runs
/// the hub and the user's websites. Everything parses tolerantly: any field may be missing.

/// One row of the server's `list_processes` answer.
class ServerProcess {
  final int pid;
  final String name;
  final String? user;
  final double cpu;
  final double memoryMb;
  final String command;

  const ServerProcess({
    required this.pid,
    required this.name,
    this.user,
    this.cpu = 0,
    this.memoryMb = 0,
    this.command = '',
  });

  static ServerProcess? fromJson(Object? raw) {
    if (raw is! Map) return null;
    return ServerProcess(
      pid: asInt(raw['pid']) ?? 0,
      name: asString(raw['name']) ?? '?',
      user: asString(raw['user']),
      cpu: asDouble(raw['cpu']) ?? 0,
      memoryMb: asDouble(raw['memory_mb']) ?? asDouble(raw['mem_mb']) ?? 0,
      command: asString(raw['command']) ?? '',
    );
  }

  static List<ServerProcess> listFrom(Object? raw) =>
      raw is List ? raw.map(ServerProcess.fromJson).whereType<ServerProcess>().toList() : const [];
}

/// What a server unit can be: a pm2 app, a systemd service or a Docker container.
enum ServerUnitKind { app, service, container }

extension ServerUnitKindX on ServerUnitKind {
  /// The `kind` the server's `control` action takes.
  String get wire => switch (this) {
        ServerUnitKind.app => 'app',
        ServerUnitKind.service => 'service',
        ServerUnitKind.container => 'container',
      };

  String get label => switch (this) {
        ServerUnitKind.app => 'app',
        ServerUnitKind.service => 'service',
        ServerUnitKind.container => 'container',
      };
}

/// How healthy a unit's state looks, for its chip colour.
enum UnitHealth { good, warn, bad, idle }

/// A pm2 app, systemd service or Docker container with its state.
class ServerUnit {
  final ServerUnitKind kind;
  final String name;
  final String state; // "online", "active", "running", "errored", "exited", ...
  final int? restarts;
  final double? memoryMb;

  const ServerUnit({required this.kind, required this.name, required this.state, this.restarts, this.memoryMb});

  UnitHealth get health => unitHealth(state);

  static List<ServerUnit> apps(Object? raw) => _list(raw, ServerUnitKind.app, 'status');
  static List<ServerUnit> containers(Object? raw) => _list(raw, ServerUnitKind.container, 'state');

  static List<ServerUnit> _list(Object? raw, ServerUnitKind kind, String stateKey) {
    if (raw is! List) return const [];
    final out = <ServerUnit>[];
    for (final e in raw) {
      if (e is! Map) continue;
      final name = asString(e['name']);
      if (name == null) continue;
      out.add(ServerUnit(
        kind: kind,
        name: name,
        state: asString(e[stateKey]) ?? asString(e['status']) ?? asString(e['state']) ?? 'unknown',
        restarts: asInt(e['restarts']),
        memoryMb: asDouble(e['memory_mb']),
      ));
    }
    return out;
  }

  /// `{"nginx": "active", "docker": "failed"}` (or a list of `{name, state}`).
  static List<ServerUnit> services(Object? raw) {
    if (raw is Map) {
      final list = raw.entries
          .map((e) => ServerUnit(
                kind: ServerUnitKind.service,
                name: e.key.toString(),
                state: e.value is Map
                    ? (asString((e.value as Map)['state']) ?? asString((e.value as Map)['status']) ?? 'unknown')
                    : (asString(e.value) ?? 'unknown'),
              ))
          .toList();
      list.sort((a, b) => a.name.toLowerCase().compareTo(b.name.toLowerCase()));
      return list;
    }
    return _list(raw, ServerUnitKind.service, 'state');
  }
}

UnitHealth unitHealth(String state) {
  final s = state.toLowerCase();
  if (s.startsWith('online') || s == 'active' || s.startsWith('running') || s.startsWith('up')) {
    return UnitHealth.good;
  }
  if (s.contains('error') || s.contains('fail') || s == 'dead' || s.contains('restarting') || s.contains('crash')) {
    return UnitHealth.bad;
  }
  if (s.contains('launch') || s.contains('start') || s.contains('activating') || s.contains('reload') ||
      s.contains('paused')) {
    return UnitHealth.warn;
  }
  return UnitHealth.idle; // stopped, exited, inactive, created, unknown
}

/// A website the hub watches.
class ServerSite {
  final String domain;
  final bool up;
  final int? status;
  final double? ms;
  final int? certDays;
  final String? error;

  const ServerSite({required this.domain, required this.up, this.status, this.ms, this.certDays, this.error});

  static ServerSite? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final domain = asString(raw['domain']);
    if (domain == null) return null;
    return ServerSite(
      domain: domain,
      up: raw['up'] == true,
      status: asInt(raw['status']),
      ms: asDouble(raw['ms']),
      certDays: asInt(raw['cert_days']),
      error: asString(raw['error']),
    );
  }

  /// Down sites first, then the soonest certificate expiry, then by name.
  static List<ServerSite> sorted(Object? raw) {
    final list = raw is List ? raw.map(ServerSite.fromJson).whereType<ServerSite>().toList() : <ServerSite>[];
    list.sort((a, b) {
      if (a.up != b.up) return a.up ? 1 : -1;
      final ca = a.certDays ?? 1 << 20, cb = b.certDays ?? 1 << 20;
      if (ca != cb) return ca.compareTo(cb);
      return a.domain.toLowerCase().compareTo(b.domain.toLowerCase());
    });
    return list;
  }
}

/// How urgent a certificate's remaining days are.
UnitHealth certHealth(int? days) {
  if (days == null) return UnitHealth.idle;
  if (days <= 7) return UnitHealth.bad;
  if (days <= 21) return UnitHealth.warn;
  return UnitHealth.good;
}

String certText(int? days) {
  if (days == null) return 'no cert info';
  if (days < 0) return 'cert expired';
  if (days == 0) return 'cert expires today';
  return 'cert ${days}d';
}

/// A problem the hub's monitor raised, e.g. `{"key": "site:x.com", "message": "x.com is down"}`.
class ServerProblem {
  final String key;
  final String message;

  const ServerProblem(this.key, this.message);

  static List<ServerProblem> listFrom(Object? raw) {
    if (raw is! List) return const [];
    final out = <ServerProblem>[];
    for (final e in raw) {
      if (e is! Map) continue;
      final key = asString(e['key']);
      if (key == null) continue;
      out.add(ServerProblem(key, asString(e['message']) ?? problemLabel(key)));
    }
    return out;
  }
}

/// "site:shop.example.com" -> "Website shop.example.com", "cpu" -> "High CPU use".
String problemLabel(String key) {
  final i = key.indexOf(':');
  final kind = i < 0 ? key : key.substring(0, i);
  final what = i < 0 ? '' : key.substring(i + 1);
  return switch (kind) {
    'site' => 'Website $what',
    'cert' => 'Certificate for $what',
    'app' => 'App $what',
    'service' => 'Service $what',
    'container' => 'Container $what',
    'cpu' => 'High CPU use',
    'ram' => 'High memory use',
    'disk' => 'Disk almost full',
    'swap' => 'Heavy swap use',
    'load' => 'High load',
    _ => key,
  };
}

/// GET /api/v1/server/status: the hub's health check of the server.
class ServerHealth {
  final String summary;
  final List<ServerProblem> problems;
  final List<String> ignored;
  final List<ServerSite> sites;
  final List<ServerUnit> apps;
  final List<ServerUnit> services;
  final List<ServerUnit> containers;
  final double? checkedAt;
  final double? sitesCheckedAt;

  const ServerHealth({
    this.summary = '',
    this.problems = const [],
    this.ignored = const [],
    this.sites = const [],
    this.apps = const [],
    this.services = const [],
    this.containers = const [],
    this.checkedAt,
    this.sitesCheckedAt,
  });

  factory ServerHealth.fromJson(Map<String, dynamic> json) {
    final snap = json['snapshot'] is Map ? Map<String, dynamic>.from(json['snapshot']) : const <String, dynamic>{};
    return ServerHealth(
      summary: asString(json['summary']) ?? '',
      problems: ServerProblem.listFrom(json['problems']),
      ignored: json['ignored'] is List ? (json['ignored'] as List).map((e) => e.toString()).toList() : const [],
      sites: ServerSite.sorted(snap['sites']),
      apps: ServerUnit.apps(snap['apps']),
      services: ServerUnit.services(snap['services']),
      containers: ServerUnit.containers(snap['containers']),
      checkedAt: asDouble(snap['checked_at']),
      sitesCheckedAt: asDouble(snap['sites_checked_at']),
    );
  }

  bool get healthy => problems.isEmpty;
}

/// Keeps the newest command first, without repeats, at most [max] long.
List<String> pushCommandHistory(List<String> history, String command, {int max = 30}) {
  final c = command.trim();
  if (c.isEmpty) return history;
  final out = [c, ...history.where((h) => h != c)];
  return out.length > max ? out.sublist(0, max) : out;
}

// ------------------------------------------------------------------ System tab

/// One minute-sample (or bucket average) of GET /api/v1/server/history.
class HistoryPoint {
  final double t; // epoch seconds
  final double? cpu, ram, disk, swap, load;
  final double? rxKbps, txKbps; // kilobits per second

  const HistoryPoint(this.t, {this.cpu, this.ram, this.disk, this.swap, this.load, this.rxKbps, this.txKbps});

  static HistoryPoint? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final t = asDouble(raw['t']);
    if (t == null) return null;
    return HistoryPoint(
      t,
      cpu: asDouble(raw['cpu']),
      ram: asDouble(raw['ram']),
      disk: asDouble(raw['disk']),
      swap: asDouble(raw['swap']),
      load: asDouble(raw['load']),
      rxKbps: asDouble(raw['rx_kbps']),
      txKbps: asDouble(raw['tx_kbps']),
    );
  }
}

class ServerHistory {
  final double hours;
  final List<HistoryPoint> points; // oldest first

  const ServerHistory({this.hours = 1, this.points = const []});

  factory ServerHistory.fromJson(Map<String, dynamic> json) {
    final raw = json['points'];
    final list = raw is List ? raw.map(HistoryPoint.fromJson).whereType<HistoryPoint>().toList() : <HistoryPoint>[];
    list.sort((a, b) => a.t.compareTo(b.t));
    return ServerHistory(hours: asDouble(json['hours']) ?? 1, points: list);
  }

  List<double> get times => [for (final p in points) p.t];
  List<double?> series(double? Function(HistoryPoint p) pick) => [for (final p in points) pick(p)];
}

/// Average, peak and latest value of a series, ignoring gaps.
({double? avg, double? max, double? last}) seriesStats(List<double?> values) {
  final v = values.whereType<double>().toList();
  if (v.isEmpty) return (avg: null, max: null, last: null);
  return (avg: v.reduce((a, b) => a + b) / v.length, max: v.reduce((a, b) => a > b ? a : b), last: v.last);
}

class ServerPackage {
  final String name;
  final String version;
  final String repo;

  const ServerPackage(this.name, this.version, this.repo);
}

/// The `sys_updates` answer.
class ServerUpdates {
  final List<ServerPackage> packages;
  final int securityCount;
  final String? newerRelease;
  final String? releaseNote;
  final bool rebootNeeded;
  final String? kernel;
  final String message;

  const ServerUpdates({
    this.packages = const [],
    this.securityCount = 0,
    this.newerRelease,
    this.releaseNote,
    this.rebootNeeded = false,
    this.kernel,
    this.message = '',
  });

  factory ServerUpdates.fromJson(Map<String, dynamic> json) {
    final raw = json['packages'];
    final pkgs = <ServerPackage>[];
    if (raw is List) {
      for (final p in raw) {
        if (p is! Map) continue;
        final name = asString(p['name']);
        if (name == null) continue;
        pkgs.add(ServerPackage(name, asString(p['version']) ?? '', asString(p['repo']) ?? ''));
      }
    }
    return ServerUpdates(
      packages: pkgs,
      securityCount: asInt(json['security_count']) ?? 0,
      newerRelease: asString(json['newer_release']),
      releaseNote: asString(json['release_note']),
      rebootNeeded: json['reboot_needed'] == true,
      kernel: asString(json['kernel']),
      message: asString(json['message']) ?? '',
    );
  }

  ServerUpdates copyWith({bool? rebootNeeded}) => ServerUpdates(
        packages: packages,
        securityCount: securityCount,
        newerRelease: newerRelease,
        releaseNote: releaseNote,
        rebootNeeded: rebootNeeded ?? this.rebootNeeded,
        kernel: kernel,
        message: message,
      );
}

class ServerMount {
  final String mount;
  final String device;
  final String fs;
  final double? usedGb, totalGb, freeGb, pct;

  const ServerMount(this.mount, {this.device = '', this.fs = '', this.usedGb, this.totalGb, this.freeGb, this.pct});
}

class FolderSize {
  final String path;
  final double gb;

  const FolderSize(this.path, this.gb);
}

/// Something the `cleanup` action can clear: a size in GB, or Docker's own text.
class CleanableItem {
  final String key;
  final String label;
  final double? gb;
  final String? text;

  const CleanableItem(this.key, this.label, {this.gb, this.text});

  String get sizeText {
    if (text != null) return text!.isEmpty ? 'nothing reported' : text!;
    return formatGb(gb);
  }
}

const cleanableOrder = ['journal', 'docker', 'pm2_logs', 'dnf_cache', 'trash'];
const _cleanableFallback = {
  'journal': 'System logs (journald)',
  'docker': 'Unused Docker images, stopped containers and build cache',
  'pm2_logs': 'Old pm2 app logs',
  'dnf_cache': 'Package download cache',
  'trash': "Willy's trash (~/.willy-trash)",
};

/// The `storage` answer.
class ServerStorage {
  final List<ServerMount> mounts;
  final List<FolderSize> biggest;
  final List<CleanableItem> cleanable;
  final double? swapUsedGb, swapTotalGb, swapPct;
  final String message;

  const ServerStorage({
    this.mounts = const [],
    this.biggest = const [],
    this.cleanable = const [],
    this.swapUsedGb,
    this.swapTotalGb,
    this.swapPct,
    this.message = '',
  });

  factory ServerStorage.fromJson(Map<String, dynamic> json) {
    final mounts = <ServerMount>[];
    if (json['mounts'] is List) {
      for (final m in json['mounts'] as List) {
        if (m is! Map) continue;
        final mount = asString(m['mount']);
        if (mount == null) continue;
        mounts.add(ServerMount(mount,
            device: asString(m['device']) ?? '',
            fs: asString(m['fs']) ?? '',
            usedGb: asDouble(m['used_gb']),
            totalGb: asDouble(m['total_gb']),
            freeGb: asDouble(m['free_gb']),
            pct: asDouble(m['pct'])));
      }
    }
    final biggest = <FolderSize>[];
    if (json['biggest'] is List) {
      for (final b in json['biggest'] as List) {
        if (b is! Map) continue;
        final path = asString(b['path']);
        if (path == null) continue;
        biggest.add(FolderSize(path, asDouble(b['gb']) ?? 0));
      }
      biggest.sort((a, b) => b.gb.compareTo(a.gb));
    }
    final labels = json['cleanable_labels'] is Map ? json['cleanable_labels'] as Map : const {};
    final raw = json['cleanable'] is Map ? json['cleanable'] as Map : const {};
    final keys = [
      ...cleanableOrder.where(raw.containsKey),
      ...raw.keys.map((k) => k.toString()).where((k) => !cleanableOrder.contains(k)),
    ];
    final cleanable = <CleanableItem>[];
    for (final k in keys) {
      final v = raw[k];
      final label = asString(labels[k]) ?? _cleanableFallback[k] ?? k;
      if (v is String && double.tryParse(v) == null) {
        cleanable.add(CleanableItem(k, label, text: v.trim()));
      } else {
        cleanable.add(CleanableItem(k, label, gb: asDouble(v)));
      }
    }
    final swap = json['swap'] is Map ? json['swap'] as Map : const {};
    return ServerStorage(
      mounts: mounts,
      biggest: biggest,
      cleanable: cleanable,
      swapUsedGb: asDouble(swap['used_gb']),
      swapTotalGb: asDouble(swap['total_gb']),
      swapPct: asDouble(swap['pct']),
      message: asString(json['message']) ?? '',
    );
  }
}

/// "12.4 GB", "820 MB", "0 GB".
String formatGb(double? gb) {
  if (gb == null) return '—';
  if (gb <= 0) return '0 GB';
  if (gb < 1) return '${(gb * 1024).round()} MB';
  if (gb < 100) return '${gb.toStringAsFixed(1)} GB';
  return '${gb.round()} GB';
}

/// Network rates arrive in kilobits per second: "820 kbit/s", "12.4 Mbit/s".
String formatKbit(double? kbps) {
  if (kbps == null) return '—';
  if (kbps < 1) return '0 kbit/s';
  if (kbps < 1000) return '${kbps.round()} kbit/s';
  if (kbps < 1000000) return '${(kbps / 1000).toStringAsFixed(kbps < 10000 ? 1 : 0)} Mbit/s';
  return '${(kbps / 1000000).toStringAsFixed(1)} Gbit/s';
}

class ServerInterface {
  final String name;
  final String state;
  final List<String> addresses;

  const ServerInterface(this.name, this.state, this.addresses);
}

class ListeningPort {
  final String proto;
  final String address;
  final String port;
  final bool public;
  final String process;
  final int? pid;

  const ListeningPort(
      {required this.proto, required this.address, required this.port, this.public = false, this.process = '', this.pid});
}

/// The `network` answer.
class ServerNetwork {
  final List<ServerInterface> interfaces;
  final List<ListeningPort> listening; // public first
  final double? downKbps, upKbps;
  final int? established;
  final String message;

  const ServerNetwork(
      {this.interfaces = const [],
      this.listening = const [],
      this.downKbps,
      this.upKbps,
      this.established,
      this.message = ''});

  factory ServerNetwork.fromJson(Map<String, dynamic> json) {
    final ifaces = <ServerInterface>[];
    if (json['interfaces'] is List) {
      for (final i in json['interfaces'] as List) {
        if (i is! Map) continue;
        final name = asString(i['interface']);
        if (name == null) continue;
        final addrs = i['addresses'] is List
            ? (i['addresses'] as List).map((e) => e.toString()).where((e) => e.isNotEmpty).toList()
            : <String>[];
        ifaces.add(ServerInterface(name, asString(i['state']) ?? '', addrs));
      }
    }
    final ports = <ListeningPort>[];
    if (json['listening'] is List) {
      for (final l in json['listening'] as List) {
        if (l is! Map) continue;
        ports.add(ListeningPort(
          proto: asString(l['proto']) ?? '',
          address: asString(l['address']) ?? '',
          port: asString(l['port']) ?? '?',
          public: l['public'] == true,
          process: asString(l['process']) ?? '',
          pid: asInt(l['pid']),
        ));
      }
    }
    ports.sort((a, b) {
      if (a.public != b.public) return a.public ? -1 : 1;
      return (int.tryParse(a.port) ?? 0).compareTo(int.tryParse(b.port) ?? 0);
    });
    final rate = json['rate'] is Map ? json['rate'] as Map : const {};
    return ServerNetwork(
      interfaces: ifaces,
      listening: ports,
      downKbps: asDouble(rate['down_kbps']),
      upKbps: asDouble(rate['up_kbps']),
      established: asInt(json['established']),
      message: asString(json['message']) ?? '',
    );
  }
}

/// The `security` answer.
class ServerSecurity {
  final List<String> loggedIn;
  final List<String> recentLogins;
  final int failedSsh24h;
  final List<({String ip, int attempts})> topAttackers;
  final String sshPasswordLogin; // "yes" / "no" / "unknown"
  final String rootLogin; // "no", "prohibit-password", "yes", ...
  final String fail2ban; // "active", "inactive", "not installed"
  final String message;

  const ServerSecurity({
    this.loggedIn = const [],
    this.recentLogins = const [],
    this.failedSsh24h = 0,
    this.topAttackers = const [],
    this.sshPasswordLogin = 'unknown',
    this.rootLogin = 'unknown',
    this.fail2ban = 'unknown',
    this.message = '',
  });

  factory ServerSecurity.fromJson(Map<String, dynamic> json) {
    List<String> lines(Object? raw) =>
        raw is List ? raw.map((e) => e.toString()).where((e) => e.trim().isNotEmpty).toList() : const [];
    final top = <({String ip, int attempts})>[];
    if (json['top_attackers'] is List) {
      for (final a in json['top_attackers'] as List) {
        if (a is! Map) continue;
        final ip = asString(a['ip']);
        if (ip != null) top.add((ip: ip, attempts: asInt(a['attempts']) ?? 0));
      }
    }
    return ServerSecurity(
      loggedIn: lines(json['logged_in']),
      recentLogins: lines(json['recent_logins']),
      failedSsh24h: asInt(json['failed_ssh_24h']) ?? 0,
      topAttackers: top,
      sshPasswordLogin: asString(json['ssh_password_login']) ?? 'unknown',
      rootLogin: asString(json['root_login']) ?? 'unknown',
      fail2ban: asString(json['fail2ban']) ?? 'unknown',
      message: asString(json['message']) ?? '',
    );
  }

  /// Password logins over SSH invite brute force: on is bad.
  UnitHealth get passwordHealth => switch (sshPasswordLogin.toLowerCase()) {
        'yes' => UnitHealth.bad,
        'no' => UnitHealth.good,
        _ => UnitHealth.idle,
      };

  UnitHealth get rootHealth => switch (rootLogin.toLowerCase()) {
        'no' => UnitHealth.good,
        'yes' => UnitHealth.bad,
        'unknown' => UnitHealth.idle,
        _ => UnitHealth.warn, // prohibit-password / without-password / forced-commands-only
      };

  UnitHealth get fail2banHealth => fail2ban.toLowerCase() == 'active' ? UnitHealth.good : UnitHealth.warn;

  UnitHealth get failedHealth =>
      failedSsh24h == 0 ? UnitHealth.good : (failedSsh24h > 100 ? UnitHealth.bad : UnitHealth.warn);
}

/// One systemd service from `services_list`.
class SystemService {
  final String name;
  final String load;
  final String active; // active, inactive, failed, activating
  final String sub; // running, exited, dead, failed
  final String description;
  final String boot; // enabled, disabled, static, masked, ""

  const SystemService(
      {required this.name, this.load = '', this.active = '', this.sub = '', this.description = '', this.boot = ''});

  bool get failed => active == 'failed' || sub == 'failed';
  bool get running => active == 'active' || active == 'activating' || active == 'reloading';

  /// Only enabled / disabled units can be switched; static and masked ones can't.
  bool get bootToggleable => boot == 'enabled' || boot == 'disabled';
  bool get startsAtBoot => boot == 'enabled' || boot == 'enabled-runtime' || boot == 'alias';

  /// "active (running)", "failed", "inactive (dead)".
  String get stateText => sub.isEmpty || sub == active ? (active.isEmpty ? 'unknown' : active) : '$active ($sub)';

  UnitHealth get health => failed ? UnitHealth.bad : unitHealth(active);

  SystemService withBoot(String b) =>
      SystemService(name: name, load: load, active: active, sub: sub, description: description, boot: b);

  static List<SystemService> listFrom(Object? raw) {
    if (raw is! List) return const [];
    final out = <SystemService>[];
    for (final s in raw) {
      if (s is! Map) continue;
      final name = asString(s['name']);
      if (name == null) continue;
      out.add(SystemService(
        name: name,
        load: asString(s['load']) ?? '',
        active: asString(s['active']) ?? '',
        sub: asString(s['sub']) ?? '',
        description: asString(s['description']) ?? '',
        boot: asString(s['boot']) ?? '',
      ));
    }
    return sortServices(out);
  }
}

/// Failed first, then running, then the rest; by name within each.
List<SystemService> sortServices(List<SystemService> list) {
  int rank(SystemService s) => s.failed ? 0 : (s.running ? 1 : 2);
  final out = [...list];
  out.sort((a, b) {
    final r = rank(a).compareTo(rank(b));
    return r != 0 ? r : a.name.toLowerCase().compareTo(b.name.toLowerCase());
  });
  return out;
}

enum ServiceFilter { all, running, failed, stopped }

List<SystemService> filterServices(List<SystemService> list, String query, ServiceFilter filter) {
  final q = query.trim().toLowerCase();
  return list.where((s) {
    final ok = switch (filter) {
      ServiceFilter.all => true,
      ServiceFilter.running => s.running,
      ServiceFilter.failed => s.failed,
      ServiceFilter.stopped => !s.running && !s.failed,
    };
    if (!ok) return false;
    return q.isEmpty || s.name.toLowerCase().contains(q) || s.description.toLowerCase().contains(q);
  }).toList();
}

/// The `timers` answer: the user's crontab and systemd timers.
class ServerJobs {
  final List<String> cron;
  final List<({String timer, String line})> timers;

  const ServerJobs({this.cron = const [], this.timers = const []});

  factory ServerJobs.fromJson(Map<String, dynamic> json) {
    final cron = json['cron'] is List
        ? (json['cron'] as List).map((e) => e.toString()).where((e) => e.trim().isNotEmpty).toList()
        : <String>[];
    final timers = <({String timer, String line})>[];
    if (json['timers'] is List) {
      for (final t in json['timers'] as List) {
        if (t is! Map) continue;
        final name = asString(t['timer']);
        if (name != null) timers.add((timer: name, line: asString(t['line']) ?? ''));
      }
    }
    return ServerJobs(cron: cron, timers: timers);
  }
}

/// journalctl priorities the `journal` action accepts, with friendly names.
const journalPriorities = <(String, String)>[
  ('', 'Any level'),
  ('err', 'Errors and worse'),
  ('warning', 'Warnings and worse'),
  ('notice', 'Notices and worse'),
  ('info', 'Info and worse'),
  ('crit', 'Critical and worse'),
  ('debug', 'Everything (debug)'),
];

/// `since` values for the `journal` action (journalctl syntax).
const journalSince = <(String, String)>[
  ('', 'Any time'),
  ('15 min ago', 'Last 15 min'),
  ('1 hour ago', 'Last hour'),
  ('6 hours ago', 'Last 6 hours'),
  ('today', 'Today'),
  ('yesterday', 'Since yesterday'),
  ('7 days ago', 'Last 7 days'),
];
