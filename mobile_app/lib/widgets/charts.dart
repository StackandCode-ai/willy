import 'dart:math';

import 'package:flutter/material.dart';

import '../theme.dart';

/// Circular gauge that animates smoothly between telemetry updates.
class RingGauge extends StatelessWidget {
  final double? value; // 0-100, null = unknown
  final String label;
  final String? centerText;
  final String? caption;
  final Color color;
  final double size;
  final IconData? icon;

  const RingGauge({
    super.key,
    required this.value,
    required this.label,
    required this.color,
    this.centerText,
    this.caption,
    this.size = 66,
    this.icon,
  });

  @override
  Widget build(BuildContext context) {
    final target = (value ?? 0).clamp(0, 100).toDouble();
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        TweenAnimationBuilder<double>(
          tween: Tween(end: target),
          duration: const Duration(milliseconds: 700),
          curve: Curves.easeOutCubic,
          builder: (context, animated, _) => SizedBox(
            width: size,
            height: size,
            child: CustomPaint(
              painter: _RingPainter(fraction: value == null ? 0 : animated / 100, color: color),
              child: Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (icon != null) Icon(icon, size: size * 0.2, color: color),
                    Text(
                      centerText ?? (value == null ? '—' : '${animated.round()}%'),
                      style: TextStyle(
                        color: WillyColors.text,
                        fontSize: size * 0.2,
                        fontWeight: FontWeight.w800,
                        fontFeatures: const [FontFeature.tabularFigures()],
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
        const SizedBox(height: 6),
        Text(label, style: const TextStyle(color: WillyColors.muted, fontSize: 11, fontWeight: FontWeight.w600)),
        if (caption != null)
          Padding(
            padding: const EdgeInsets.only(top: 1),
            child: Text(
              caption!,
              style: const TextStyle(color: WillyColors.faint, fontSize: 10),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
      ],
    );
  }
}

class _RingPainter extends CustomPainter {
  final double fraction;
  final Color color;

  _RingPainter({required this.fraction, required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    final stroke = size.width * 0.1;
    final rect = Offset.zero & size;
    final arcRect = rect.deflate(stroke / 2);
    const start = pi * 0.75;
    const sweep = pi * 1.5;

    final track = Paint()
      ..color = WillyColors.border
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke
      ..strokeCap = StrokeCap.round;
    canvas.drawArc(arcRect, start, sweep, false, track);

    if (fraction > 0) {
      final value = Paint()
        ..shader = SweepGradient(
          startAngle: start,
          endAngle: start + sweep,
          colors: [color.withValues(alpha: 0.55), color],
          transform: const GradientRotation(0),
        ).createShader(rect)
        ..style = PaintingStyle.stroke
        ..strokeWidth = stroke
        ..strokeCap = StrokeCap.round;
      canvas.drawArc(arcRect, start, sweep * fraction.clamp(0.0, 1.0), false, value);
    }
  }

  @override
  bool shouldRepaint(_RingPainter old) => old.fraction != fraction || old.color != color;
}

/// Minimal line chart for telemetry history (nulls are skipped).
class Sparkline extends StatelessWidget {
  final List<double?> values;
  final Color color;
  final double height;
  final double? maxValue; // fixed scale (e.g. 100 for percentages); null = auto
  final List<double?>? secondary;
  final Color? secondaryColor;

  const Sparkline({
    super.key,
    required this.values,
    required this.color,
    this.height = 44,
    this.maxValue,
    this.secondary,
    this.secondaryColor,
  });

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: height,
      width: double.infinity,
      child: CustomPaint(
        painter: _SparkPainter(
          values: values,
          color: color,
          maxValue: maxValue,
          secondary: secondary,
          secondaryColor: secondaryColor ?? WillyColors.purple,
        ),
      ),
    );
  }
}

class _SparkPainter extends CustomPainter {
  final List<double?> values;
  final Color color;
  final double? maxValue;
  final List<double?>? secondary;
  final Color secondaryColor;

  _SparkPainter({
    required this.values,
    required this.color,
    required this.maxValue,
    required this.secondary,
    required this.secondaryColor,
  });

  double _scale() {
    if (maxValue != null) return maxValue!;
    final all = [...values, ...?secondary].whereType<double>();
    final peak = all.isEmpty ? 1.0 : all.reduce(max);
    return peak <= 0 ? 1.0 : peak * 1.15;
  }

  void _drawSeries(Canvas canvas, Size size, List<double?> series, Color c, double top, {bool fill = true}) {
    final points = <Offset>[];
    final n = series.length;
    if (n < 2) return;
    for (var i = 0; i < n; i++) {
      final v = series[i];
      if (v == null) continue;
      final x = size.width * i / (n - 1);
      final y = size.height - (v.clamp(0, top) / top) * (size.height - 2) - 1;
      points.add(Offset(x, y));
    }
    if (points.length < 2) return;

    final path = Path()..moveTo(points.first.dx, points.first.dy);
    for (var i = 1; i < points.length; i++) {
      final p0 = points[i - 1];
      final p1 = points[i];
      final mid = Offset((p0.dx + p1.dx) / 2, (p0.dy + p1.dy) / 2);
      path.quadraticBezierTo(p0.dx, p0.dy, mid.dx, mid.dy);
    }
    path.lineTo(points.last.dx, points.last.dy);

    if (fill) {
      final area = Path.from(path)
        ..lineTo(points.last.dx, size.height)
        ..lineTo(points.first.dx, size.height)
        ..close();
      canvas.drawPath(
        area,
        Paint()
          ..shader = LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [c.withValues(alpha: 0.28), c.withValues(alpha: 0.0)],
          ).createShader(Offset.zero & size),
      );
    }
    canvas.drawPath(
      path,
      Paint()
        ..color = c
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round,
    );
    canvas.drawCircle(points.last, 3, Paint()..color = c);
  }

  @override
  void paint(Canvas canvas, Size size) {
    final top = _scale();
    final grid = Paint()
      ..color = WillyColors.border.withValues(alpha: 0.6)
      ..strokeWidth = 1;
    for (final f in [0.25, 0.5, 0.75]) {
      canvas.drawLine(Offset(0, size.height * f), Offset(size.width, size.height * f), grid);
    }
    if (secondary != null) _drawSeries(canvas, size, secondary!, secondaryColor, top, fill: false);
    _drawSeries(canvas, size, values, color, top);
  }

  @override
  bool shouldRepaint(_SparkPainter old) =>
      old.values != values || old.secondary != secondary || old.color != color || old.maxValue != maxValue;
}
