/// Tolerant JSON readers: the hub may send ints, doubles or nulls for any metric.
double? asDouble(dynamic v) {
  if (v is num) return v.toDouble();
  if (v is String) return double.tryParse(v);
  return null;
}

int? asInt(dynamic v) {
  if (v is num) return v.round();
  if (v is String) return int.tryParse(v);
  return null;
}

bool? asBool(dynamic v) => v is bool ? v : null;

String? asString(dynamic v) {
  if (v == null) return null;
  final s = v.toString();
  return s.isEmpty ? null : s;
}

class ProcessInfo {
  final int pid;
  final String name;
  final double cpu;
  final double memMb;
  final int? threads;

  const ProcessInfo({
    required this.pid,
    required this.name,
    required this.cpu,
    required this.memMb,
    this.threads,
  });

  factory ProcessInfo.fromJson(Map<String, dynamic> json) => ProcessInfo(
        pid: asInt(json['pid']) ?? 0,
        name: asString(json['name']) ?? '?',
        cpu: asDouble(json['cpu']) ?? 0,
        memMb: asDouble(json['mem_mb']) ?? 0,
        threads: asInt(json['threads']),
      );
}

/// Recent telemetry samples (~2 s apart) for sparklines.
class TelemetryHistory {
  final List<double?> cpu;
  final List<double?> ram;
  final List<double?> battery;
  final List<double?> netDown;
  final List<double?> netUp;

  const TelemetryHistory({
    this.cpu = const [],
    this.ram = const [],
    this.battery = const [],
    this.netDown = const [],
    this.netUp = const [],
  });

  static List<double?> _series(dynamic raw) =>
      raw is List ? raw.map(asDouble).toList(growable: false) : const [];

  factory TelemetryHistory.fromJson(Map<String, dynamic>? json) {
    if (json == null) return const TelemetryHistory();
    return TelemetryHistory(
      cpu: _series(json['cpu']),
      ram: _series(json['ram']),
      battery: _series(json['battery']),
      netDown: _series(json['net_down']),
      netUp: _series(json['net_up']),
    );
  }
}

class WillyDevice {
  final String id;
  final String type; // 'pc', 'mobile' or 'server' (the Linux machine running the hub)
  final String name;
  final String hostname;
  final String platform;
  final bool online;
  final String connection;
  final double lastSeen;
  final double connectedAt;
  final int commandsHandled;
  final Map<String, dynamic> specs;
  final Map<String, dynamic> telemetry;
  final TelemetryHistory history;

  WillyDevice({
    required this.id,
    required this.type,
    required this.name,
    required this.hostname,
    required this.platform,
    required this.online,
    required this.lastSeen,
    this.connection = 'websocket',
    this.connectedAt = 0,
    this.commandsHandled = 0,
    this.specs = const {},
    this.telemetry = const {},
    this.history = const TelemetryHistory(),
  });

  factory WillyDevice.fromJson(Map<String, dynamic> json) {
    final telemetry = json['telemetry'];
    final specs = json['specs'];
    final history = json['history'];
    return WillyDevice(
      id: asString(json['device_id']) ?? '',
      type: asString(json['device_type']) ?? 'pc',
      name: asString(json['name']) ?? 'Unknown Device',
      hostname: asString(json['hostname']) ?? 'Unknown Host',
      platform: asString(json['platform']) ?? 'Windows',
      online: json['online'] == true || json['status'] == 'online',
      connection: asString(json['connection']) ?? 'websocket',
      lastSeen: asDouble(json['last_seen']) ?? 0,
      connectedAt: asDouble(json['connected_at']) ?? 0,
      commandsHandled: asInt(json['commands_handled']) ?? 0,
      specs: specs is Map ? Map<String, dynamic>.from(specs) : const {},
      telemetry: telemetry is Map ? Map<String, dynamic>.from(telemetry) : const {},
      history: TelemetryHistory.fromJson(history is Map ? Map<String, dynamic>.from(history) : null),
    );
  }

  bool get isPc => type == 'pc';
  bool get isMobile => type == 'mobile';
  bool get isServer => type == 'server';

  /// Order in device lists: PCs, then the server, then phones (and anything new last).
  int get typeRank => switch (type) { 'pc' => 0, 'server' => 1, 'mobile' => 2, _ => 3 };

  // --- Common telemetry ---
  double? get batteryPct => asDouble(telemetry['battery_pct']);
  bool? get isCharging => asBool(telemetry['is_charging']);
  double? get batterySecsLeft => asDouble(telemetry['battery_secs_left']);
  String? get ipAddress => asString(telemetry['ip_address']);
  String? get wifiSsid => asString(telemetry['wifi_ssid']);
  int? get lastPingMs => asInt(telemetry['last_ping_ms']);
  double? get uptimeHours => asDouble(telemetry['uptime_hours']);

  // --- PC telemetry ---
  double? get cpuPct => asDouble(telemetry['cpu_pct']);
  double? get cpuFreqMhz => asDouble(telemetry['cpu_freq_mhz']);
  double? get ramPct => asDouble(telemetry['ram_pct']);
  double? get ramUsedGb => asDouble(telemetry['ram_used_gb']);
  double? get ramTotalGb => asDouble(telemetry['ram_total_gb']);
  double? get diskFreeGb => asDouble(telemetry['disk_free_gb']);
  double? get diskTotalGb => asDouble(telemetry['disk_total_gb']);
  double? get diskPct => asDouble(telemetry['disk_pct']);
  double? get netUpKbps => asDouble(telemetry['net_up_kbps']);
  double? get netDownKbps => asDouble(telemetry['net_down_kbps']);
  String? get activeWindow => asString(telemetry['active_window']);
  String? get activeProcess => asString(telemetry['active_process']);
  double? get idleSec => asDouble(telemetry['idle_sec']);
  double? get volumeLevel => asDouble(telemetry['volume_level']);
  bool? get isMuted => asBool(telemetry['is_muted']);
  double? get brightness => asDouble(telemetry['brightness']);
  int? get processCount => asInt(telemetry['process_count']);

  List<ProcessInfo> get topProcesses {
    final raw = telemetry['top_processes'];
    if (raw is! List) return const [];
    return raw.whereType<Map>().map((m) => ProcessInfo.fromJson(Map<String, dynamic>.from(m))).toList();
  }

  // --- Server telemetry (Linux) ---
  double? get swapPct => asDouble(telemetry['swap_pct']);
  double? get load1 => asDouble(telemetry['load_1']);
  double? get load5 => asDouble(telemetry['load_5']);
  double? get load15 => asDouble(telemetry['load_15']);
  int? get cores => asInt(telemetry['cores']);
  double? get netSentMb => asDouble(telemetry['net_sent_mb']);
  double? get netRecvMb => asDouble(telemetry['net_recv_mb']);

  /// The machine's own hostname (servers report it in telemetry), else the registered one.
  String get machineName => asString(telemetry['hostname']) ?? hostname;

  // --- Mobile telemetry ---
  String? get networkType => asString(telemetry['network_type']);
  bool? get torchOn => asBool(telemetry['torch_on']);
  double? get storageFreeGb => asDouble(telemetry['storage_free_gb']);
  double? get storageTotalGb => asDouble(telemetry['storage_total_gb']);
  int get unreadNotifications => asInt(telemetry['unread_notifications']) ?? 0;
  int get unreadWhatsApp => asInt(telemetry['unread_whatsapp']) ?? 0;
  int get missedCalls => asInt(telemetry['missed_calls']) ?? 0;
  String? get model => asString(telemetry['model']);

  // --- Specs ---
  String? get cpuModel => asString(specs['cpu_model']);
  String? get osName => asString(specs['os']);
  bool get hasBattery => specs['has_battery'] == true || batteryPct != null;
  int? get monitors => asInt(specs['monitors']);
  List<String> get gpus {
    final raw = specs['gpu'];
    return raw is List ? raw.map((e) => e.toString()).toList() : const [];
  }
}
