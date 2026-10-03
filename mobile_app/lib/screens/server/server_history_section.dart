import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:intl/intl.dart' show DateFormat;

import '../../models/device.dart';
import '../../models/server.dart';
import '../../services/api_service.dart';
import '../../theme.dart';
import 'server_widgets.dart';

/// One line on a [HistoryChart].
class ChartSeries {
  final String label;
  final Color color;
  final List<double?> values; // aligned with the chart's times; null = gap

  const ChartSeries(this.label, this.color, this.values);
}

/// A small line chart drawn with a CustomPainter: gaps where values are missing, a fixed
/// or automatic top, and touch / drag to read the values at a moment.
class HistoryChart extends StatefulWidget {
  final List<double> times; // epoch seconds, oldest first
  final List<ChartSeries> series;
  final double? maxY; // null = scale to the data
  final String Function(double v) format;
  final String Function(double v)? axisFormat; // shorter labels for the y axis
  final double height;

  const HistoryChart({
    super.key,
    required this.times,
    required this.series,
    required this.format,
    this.axisFormat,
    this.maxY,
    this.height = 150,
  });

  @override
  State<HistoryChart> createState() => _HistoryChartState();
}

class _HistoryChartState extends State<HistoryChart> {
  int? _sel;

  @override
  void didUpdateWidget(HistoryChart old) {
    super.didUpdateWidget(old);
    if (_sel != null && _sel! >= widget.times.length) _sel = null;
  }

  double get _top {
    if (widget.maxY != null) return widget.maxY!;
    var m = 0.0;
    for (final s in widget.series) {
      for (final v in s.values) {
        if (v != null && v > m) m = v;
      }
    }
    return niceCeil(m * 1.1);
  }

  void _pick(Offset local, double width) {
    final t = widget.times;
    if (t.isEmpty) return;
    final plotW = width - HistoryChartPainter.left - HistoryChartPainter.right;
    final frac = ((local.dx - HistoryChartPainter.left) / plotW).clamp(0.0, 1.0);
    final t0 = t.first, t1 = t.last;
    final target = t0 + (t1 - t0) * frac;
    var best = 0;
    var bestD = double.infinity;
    for (var i = 0; i < t.length; i++) {
      final d = (t[i] - target).abs();
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
    if (best != _sel) setState(() => _sel = best);
  }

  @override
  Widget build(BuildContext context) {
    final t = widget.times;
    final top = _top;
    final sel = _sel;
    final span = t.length > 1 ? t.last - t.first : 0.0;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        LayoutBuilder(builder: (context, c) {
          return GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTapDown: (d) => _pick(d.localPosition, c.maxWidth),
            onHorizontalDragStart: (d) => _pick(d.localPosition, c.maxWidth),
            onHorizontalDragUpdate: (d) => _pick(d.localPosition, c.maxWidth),
            child: CustomPaint(
              size: Size(c.maxWidth, widget.height),
              painter: HistoryChartPainter(
                times: t,
                series: widget.series,
                top: top,
                format: widget.axisFormat ?? widget.format,
                selected: sel,
                timeLabel: (s) => _timeLabel(s, span),
              ),
            ),
          );
        }),
        const SizedBox(height: 6),
        Wrap(
          spacing: 14,
          runSpacing: 4,
          children: [
            if (sel != null && sel < t.length)
              Text(_fullTime(t[sel]), style: const TextStyle(color: WillyColors.textSoft, fontSize: 11.5)),
            for (final s in widget.series) _legend(s, sel),
          ],
        ),
      ],
    );
  }

  Widget _legend(ChartSeries s, int? sel) {
    final stats = seriesStats(s.values);
    final String text;
    if (sel != null && sel < s.values.length) {
      final v = s.values[sel];
      text = '${s.label} ${v == null ? '—' : widget.format(v)}';
    } else {
      text = '${s.label} ${stats.last == null ? '—' : widget.format(stats.last!)}'
          '${stats.avg == null ? '' : ' · avg ${widget.format(stats.avg!)} · peak ${widget.format(stats.max!)}'}';
    }
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(width: 10, height: 3, decoration: BoxDecoration(color: s.color, borderRadius: BorderRadius.circular(2))),
        const SizedBox(width: 6),
        Flexible(child: Text(text, style: const TextStyle(color: WillyColors.muted, fontSize: 11.5))),
      ],
    );
  }

  static String _timeLabel(double epoch, double span) {
    final d = DateTime.fromMillisecondsSinceEpoch((epoch * 1000).round());
    if (span > 2 * 86400) return DateFormat('EEE d').format(d);
    return DateFormat('HH:mm').format(d);
  }

  static String _fullTime(double epoch) =>
      DateFormat('EEE HH:mm').format(DateTime.fromMillisecondsSinceEpoch((epoch * 1000).round()));
}

/// Axis labels for kbit/s: "800k", "12M".
String _shortKbit(double v) {
  if (v < 1000) return '${v.round()}k';
  if (v < 1000000) return '${(v / 1000).toStringAsFixed(v < 10000 && v % 1000 != 0 ? 1 : 0)}M';
  return '${(v / 1000000).toStringAsFixed(1)}G';
}

/// Rounds up to 1, 2, 2.5 or 5 times a power of ten (at least 1).
double niceCeil(double v) {
  if (v <= 1) return 1;
  final p = math.pow(10, (math.log(v) / math.ln10).floor()).toDouble();
  for (final m in const [1.0, 2.0, 2.5, 5.0, 10.0]) {
    if (v <= m * p) return m * p;
  }
  return 10 * p;
}

class HistoryChartPainter extends CustomPainter {
  static const left = 44.0, right = 6.0, topPad = 6.0, bottom = 18.0;

  final List<double> times;
  final List<ChartSeries> series;
  final double top;
  final String Function(double v) format;
  final String Function(double epoch) timeLabel;
  final int? selected;

  HistoryChartPainter({
    required this.times,
    required this.series,
    required this.top,
    required this.format,
    required this.timeLabel,
    this.selected,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final plot = Rect.fromLTRB(left, topPad, size.width - right, size.height - bottom);
    final grid = Paint()
      ..color = WillyColors.border
      ..strokeWidth = 1;
    const labelStyle = TextStyle(color: WillyColors.faint, fontSize: 9.5);

    // Horizontal grid with value labels.
    for (var i = 0; i <= 4; i++) {
      final y = plot.bottom - plot.height * i / 4;
      canvas.drawLine(Offset(plot.left, y), Offset(plot.right, y), grid);
      _text(canvas, format(top * i / 4), Offset(plot.left - 4, y), labelStyle, alignRight: true);
    }

    if (times.length < 2) {
      _text(canvas, times.isEmpty ? 'No samples yet' : 'Only one sample so far',
          Offset(plot.center.dx, plot.center.dy), const TextStyle(color: WillyColors.faint, fontSize: 12),
          center: true);
      return;
    }

    final t0 = times.first, t1 = times.last;
    final spanT = (t1 - t0) == 0 ? 1.0 : (t1 - t0);
    double xOf(int i) => plot.left + plot.width * (times[i] - t0) / spanT;
    double yOf(double v) => plot.bottom - plot.height * (v / top).clamp(0.0, 1.0);

    // Time labels: start, middle, end.
    for (final (frac, align) in const [(0.0, -1), (0.5, 0), (1.0, 1)]) {
      final x = plot.left + plot.width * frac;
      _text(canvas, timeLabel(t0 + spanT * frac), Offset(x, plot.bottom + 4), labelStyle,
          center: align == 0, alignRight: align == 1, top: true);
    }

    // A gap longer than 5 typical steps breaks the line (the hub or agent was down).
    final step = spanT / math.max(1, times.length - 1);
    final maxGap = step * 5;

    canvas.save();
    canvas.clipRect(plot.inflate(2));
    for (final s in series) {
      final line = Paint()
        ..color = s.color
        ..strokeWidth = 1.8
        ..style = PaintingStyle.stroke
        ..strokeJoin = StrokeJoin.round
        ..strokeCap = StrokeCap.round;
      final fill = Paint()
        ..style = PaintingStyle.fill
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [s.color.withValues(alpha: 0.18), s.color.withValues(alpha: 0.0)],
        ).createShader(plot);
      final path = Path();
      Path? area;
      var open = false;
      double? lastX;
      double? prevT;
      void closeArea() {
        final a = area, x = lastX;
        if (a != null && x != null) {
          a
            ..lineTo(x, plot.bottom)
            ..close();
          canvas.drawPath(a, fill);
        }
        area = null;
      }

      for (var i = 0; i < s.values.length && i < times.length; i++) {
        final v = s.values[i];
        final gap = prevT != null && times[i] - prevT > maxGap;
        if (v == null || gap) {
          if (open) closeArea();
          open = false;
          if (v == null) {
            prevT = times[i];
            continue;
          }
        }
        final x = xOf(i), y = yOf(v);
        if (!open) {
          path.moveTo(x, y);
          area = Path()
            ..moveTo(x, plot.bottom)
            ..lineTo(x, y);
          open = true;
        } else {
          path.lineTo(x, y);
          area!.lineTo(x, y);
        }
        lastX = x;
        prevT = times[i];
      }
      if (open) closeArea();
      canvas.drawPath(path, line);
    }
    canvas.restore();

    final sel = selected;
    if (sel != null && sel < times.length) {
      final x = xOf(sel);
      canvas.drawLine(Offset(x, plot.top), Offset(x, plot.bottom),
          Paint()
            ..color = WillyColors.textSoft.withValues(alpha: 0.5)
            ..strokeWidth = 1);
      for (final s in series) {
        if (sel >= s.values.length) continue;
        final v = s.values[sel];
        if (v == null) continue;
        canvas.drawCircle(Offset(x, yOf(v)), 3.5, Paint()..color = s.color);
        canvas.drawCircle(
            Offset(x, yOf(v)),
            3.5,
            Paint()
              ..color = WillyColors.bg
              ..style = PaintingStyle.stroke
              ..strokeWidth = 1.2);
      }
    }
  }

  void _text(Canvas canvas, String text, Offset at, TextStyle style,
      {bool alignRight = false, bool center = false, bool top = false}) {
    final tp = TextPainter(text: TextSpan(text: text, style: style), textDirection: TextDirection.ltr)..layout();
    var dx = at.dx;
    if (alignRight) dx -= tp.width;
    if (center) dx -= tp.width / 2;
    final dy = top ? at.dy : at.dy - tp.height / 2;
    tp.paint(canvas, Offset(dx, dy));
  }

  @override
  bool shouldRepaint(HistoryChartPainter old) =>
      old.times != times || old.series != series || old.top != top || old.selected != selected;
}

/// History: CPU / RAM, disk / swap and network over 1 h, 6 h, 24 h or 7 days, from the hub
/// (works while the server agent is offline too).
class ServerHistorySection extends StatefulWidget {
  final WillyDevice device;
  final bool active;

  const ServerHistorySection({super.key, required this.device, this.active = true});

  @override
  State<ServerHistorySection> createState() => _ServerHistorySectionState();
}

class _ServerHistorySectionState extends State<ServerHistorySection> {
  static const _ranges = [(1, '1h'), (6, '6h'), (24, '24h'), (168, '7d')];

  int _hours = 1;
  ServerHistory? _history;
  String? _error;
  bool _loading = false;
  bool _loaded = false;
  int _serial = 0;
  DateTime? _at;
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    if (widget.active) _load();
    // Keep the chart fresh while it is on screen (one cheap hub call a minute).
    _poll = Timer.periodic(const Duration(seconds: 60), (_) {
      if (mounted && widget.active && !_loading) _load();
    });
  }

  @override
  void didUpdateWidget(ServerHistorySection old) {
    super.didUpdateWidget(old);
    if (widget.active && !_loaded && !_loading) _load();
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    final serial = ++_serial;
    setState(() {
      _loading = true;
      _error = null;
    });
    final res = await ApiService.getServerHistory(hours: _hours, points: 240);
    if (!mounted || serial != _serial) return;
    setState(() {
      _loading = false;
      _loaded = true;
      if (res['success'] != false && res['points'] is List) {
        _history = ServerHistory.fromJson(res);
        _at = DateTime.now();
      } else {
        _error = serverErrorText(res, "Couldn't load the history.");
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final h = _history;
    final times = h?.times ?? const <double>[];
    String pct(double v) => '${v.round()}%';
    return RefreshIndicator(
      onRefresh: _load,
      color: WillyColors.cyan,
      backgroundColor: WillyColors.cardAlt,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 28),
        children: [
          Row(
            children: [
              Expanded(
                child: SegmentedButton<int>(
                  segments: [for (final (v, l) in _ranges) ButtonSegment(value: v, label: Text(l))],
                  selected: {_hours},
                  showSelectedIcon: false,
                  style: const ButtonStyle(visualDensity: VisualDensity.compact),
                  onSelectionChanged: (s) {
                    setState(() => _hours = s.first);
                    _load();
                  },
                ),
              ),
              IconButton(
                tooltip: 'Refresh',
                onPressed: _loading ? null : _load,
                icon: _loading
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.refresh_rounded),
              ),
            ],
          ),
          if (_error != null) ServerNote.error(_error!),
          if (_at != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 6, left: 2),
              child: Text(
                '${times.length} samples · updated ${TimeOfDay.fromDateTime(_at!).format(context)} · tap a chart to read a moment',
                style: const TextStyle(color: WillyColors.faint, fontSize: 11),
              ),
            ),
          if (h == null && _error == null) const ServerNote('Loading the history…', busy: true),
          if (h != null) ...[
            ServerSection(
              title: 'CPU & memory',
              icon: Icons.memory_rounded,
              children: [
                HistoryChart(
                  times: times,
                  maxY: 100,
                  format: pct,
                  series: [
                    ChartSeries('CPU', WillyColors.cyan, h.series((p) => p.cpu)),
                    ChartSeries('RAM', WillyColors.purple, h.series((p) => p.ram)),
                  ],
                ),
              ],
            ),
            ServerSection(
              title: 'Disk & swap',
              icon: Icons.storage_rounded,
              children: [
                HistoryChart(
                  times: times,
                  maxY: 100,
                  format: pct,
                  series: [
                    ChartSeries('Disk', WillyColors.amber, h.series((p) => p.disk)),
                    ChartSeries('Swap', WillyColors.pink, h.series((p) => p.swap)),
                  ],
                ),
              ],
            ),
            ServerSection(
              title: 'Network',
              icon: Icons.swap_vert_rounded,
              children: [
                HistoryChart(
                  times: times,
                  format: formatKbit,
                  axisFormat: _shortKbit,
                  series: [
                    ChartSeries('In', WillyColors.green, h.series((p) => p.rxKbps)),
                    ChartSeries('Out', WillyColors.sky, h.series((p) => p.txKbps)),
                  ],
                ),
              ],
            ),
            ServerSection(
              title: 'Load average',
              icon: Icons.speed_rounded,
              children: [
                HistoryChart(
                  times: times,
                  height: 110,
                  format: (v) => v < 10 ? v.toStringAsFixed(1) : v.round().toString(),
                  series: [ChartSeries('Load', WillyColors.amber, h.series((p) => p.load))],
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}
