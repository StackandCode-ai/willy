import 'device.dart';

/// One command handled by the hub (from any client), updated live running -> done.
class ActivityEntry {
  final String id;
  final double ts;
  final String source;
  final String query;
  final String? deviceId;
  final String status; // running | done | error
  final String? reply;
  final List<String> tools;
  final bool? success;
  final bool fastPath;
  final int? latencyMs;
  final Map<String, dynamic> timings;

  const ActivityEntry({
    required this.id,
    required this.ts,
    required this.source,
    required this.query,
    required this.status,
    this.deviceId,
    this.reply,
    this.tools = const [],
    this.success,
    this.fastPath = false,
    this.latencyMs,
    this.timings = const {},
  });

  bool get isRunning => status == 'running';
  bool get failed => status == 'error' || success == false;

  factory ActivityEntry.fromJson(Map<String, dynamic> json) {
    final tools = json['tools'];
    final timings = json['timings'];
    return ActivityEntry(
      id: asString(json['id']) ?? '',
      ts: asDouble(json['ts']) ?? 0,
      source: asString(json['source']) ?? 'api',
      query: asString(json['query']) ?? '',
      deviceId: asString(json['device_id']),
      status: asString(json['status']) ?? 'done',
      reply: asString(json['reply']),
      tools: tools is List ? tools.map((t) => t.toString()).toList() : const [],
      success: asBool(json['success']),
      fastPath: json['fast_path'] == true,
      latencyMs: asInt(json['latency_ms']),
      timings: timings is Map ? Map<String, dynamic>.from(timings) : const {},
    );
  }
}
