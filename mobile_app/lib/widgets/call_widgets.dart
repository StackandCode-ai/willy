import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../theme.dart';

enum AvatarMode { connecting, idle, listening, thinking, speaking, muted }

/// Willy's round avatar with a ring of bars that follows the microphone level while
/// listening, "talks" while Willy speaks, sweeps while thinking and breathes when idle.
class WillyAvatar extends StatefulWidget {
  final AvatarMode mode;
  final Color color;
  final ValueListenable<double> level; // 0..1 microphone level
  final double size;
  final VoidCallback? onTap;
  final VoidCallback? onLongPress;

  const WillyAvatar({
    super.key,
    required this.mode,
    required this.color,
    required this.level,
    this.size = 260,
    this.onTap,
    this.onLongPress,
  });

  @override
  State<WillyAvatar> createState() => _WillyAvatarState();
}

class _AvatarAnim {
  double shown = 0; // smoothed level 0..1
  double t = 0; // seconds
}

class _WillyAvatarState extends State<WillyAvatar> with SingleTickerProviderStateMixin {
  late final AnimationController _ticker;
  final _anim = _AvatarAnim();
  final _clock = Stopwatch()..start();

  @override
  void initState() {
    super.initState();
    _ticker = AnimationController(vsync: this, duration: const Duration(seconds: 1))
      ..addListener(_tick)
      ..repeat();
  }

  void _tick() {
    final t = _clock.elapsedMilliseconds / 1000.0;
    _anim.t = t;
    final target = switch (widget.mode) {
      AvatarMode.listening => widget.level.value,
      AvatarMode.speaking =>
        (0.42 + 0.28 * math.sin(t * 9.1) * math.sin(t * 3.7) + 0.14 * math.sin(t * 17.3)).clamp(0.0, 1.0),
      AvatarMode.thinking => 0.16,
      AvatarMode.connecting => 0.1 + 0.08 * math.sin(t * 4),
      AvatarMode.idle => 0.06 + 0.04 * math.sin(t * 1.6),
      AvatarMode.muted => 0.02,
    };
    final k = target > _anim.shown ? 0.35 : 0.12;
    _anim.shown += (target - _anim.shown) * k;
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final size = widget.size;
    final core = size * 0.46;
    return Semantics(
      button: true,
      label: 'Willy. Tap to talk.',
      child: GestureDetector(
        onTap: widget.onTap,
        onLongPress: widget.onLongPress,
        behavior: HitTestBehavior.opaque,
        child: SizedBox(
          width: size,
          height: size,
          child: Stack(
            alignment: Alignment.center,
            children: [
              Positioned.fill(
                child: RepaintBoundary(
                  child: CustomPaint(painter: _RingPainter(_anim, widget.mode, widget.color, _ticker)),
                ),
              ),
              AnimatedBuilder(
                animation: _ticker,
                builder: (context, child) => Transform.scale(scale: 1 + _anim.shown * 0.07, child: child),
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 450),
                  curve: Curves.easeOut,
                  width: core,
                  height: core,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: widget.mode == AvatarMode.muted
                          ? const [WillyColors.borderStrong, WillyColors.card]
                          : [WillyColors.cyan, Color.lerp(WillyColors.violet, widget.color, 0.35)!],
                    ),
                    boxShadow: [
                      BoxShadow(color: widget.color.withValues(alpha: 0.45), blurRadius: 36, spreadRadius: 2),
                    ],
                  ),
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      // Soft highlight, like light on a glass ball.
                      Positioned(
                        top: core * 0.12,
                        left: core * 0.2,
                        child: Container(
                          width: core * 0.36,
                          height: core * 0.2,
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(core),
                            gradient: LinearGradient(
                              begin: Alignment.topCenter,
                              end: Alignment.bottomCenter,
                              colors: [Colors.white.withValues(alpha: 0.35), Colors.white.withValues(alpha: 0)],
                            ),
                          ),
                        ),
                      ),
                      Text(
                        'W',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: core * 0.42,
                          fontWeight: FontWeight.w900,
                          letterSpacing: -1,
                          shadows: const [Shadow(color: Color(0x66000000), blurRadius: 12)],
                        ),
                      ),
                      if (widget.mode == AvatarMode.muted)
                        Positioned(
                          bottom: core * 0.06,
                          right: core * 0.06,
                          child: Container(
                            padding: const EdgeInsets.all(5),
                            decoration: const BoxDecoration(color: WillyColors.red, shape: BoxShape.circle),
                            child: const Icon(Icons.mic_off_rounded, size: 16, color: Colors.white),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _RingPainter extends CustomPainter {
  final _AvatarAnim anim;
  final AvatarMode mode;
  final Color color;

  _RingPainter(this.anim, this.mode, this.color, Listenable repaint) : super(repaint: repaint);

  static const _bars = 72;

  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    final r0 = size.shortestSide * 0.23; // avatar radius
    final level = anim.shown;
    final t = anim.t;

    // Glow behind the avatar that swells with the voice.
    final glowR = r0 * (1.45 + level * 0.55);
    canvas.drawCircle(
      c,
      glowR,
      Paint()
        ..shader = RadialGradient(colors: [
          color.withValues(alpha: 0.28 + level * 0.2),
          color.withValues(alpha: 0),
        ]).createShader(Rect.fromCircle(center: c, radius: glowR)),
    );

    // Ripples moving outwards (a call "ringing out" / someone talking).
    if (mode == AvatarMode.connecting || mode == AvatarMode.listening || mode == AvatarMode.speaking) {
      final speed = mode == AvatarMode.connecting ? 0.55 : 0.4;
      for (var k = 0; k < 3; k++) {
        final p = ((t * speed) + k / 3) % 1.0;
        final alpha = (1 - p) * (mode == AvatarMode.connecting ? 0.35 : 0.12 + level * 0.3);
        canvas.drawCircle(
          c,
          r0 * (1.15 + p * 0.95),
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1.6
            ..color = color.withValues(alpha: alpha.clamp(0.0, 1.0)),
        );
      }
    }

    // Bars around the avatar.
    final barPaint = Paint()
      ..strokeCap = StrokeCap.round
      ..strokeWidth = math.max(2.0, r0 * 0.035);
    final inner = r0 * 1.12;
    for (var i = 0; i < _bars; i++) {
      final a = (i / _bars) * math.pi * 2 - math.pi / 2;
      final wobble = 0.5 + 0.5 * math.sin(i * 0.9 + t * 6) * math.cos(i * 0.37 - t * 3.1);
      final len = r0 * (0.03 + level * 0.5 * (0.25 + 0.75 * wobble));
      final dir = Offset(math.cos(a), math.sin(a));
      barPaint.color = color.withValues(alpha: (0.35 + level * 0.65).clamp(0.0, 1.0));
      canvas.drawLine(c + dir * inner, c + dir * (inner + len), barPaint);
    }

    // Thinking: a comet sweeping round the ring.
    if (mode == AvatarMode.thinking) {
      final rect = Rect.fromCircle(center: c, radius: r0 * 1.08);
      final start = t * math.pi * 2 * 0.75;
      canvas.drawArc(
        rect,
        start,
        1.6,
        false,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = r0 * 0.07
          ..strokeCap = StrokeCap.round
          ..shader = SweepGradient(
            startAngle: 0,
            endAngle: 1.6,
            colors: [color.withValues(alpha: 0), color],
            transform: GradientRotation(start),
          ).createShader(rect),
      );
    }
  }

  @override
  bool shouldRepaint(covariant _RingPainter old) => old.mode != mode || old.color != color;
}

/// Round call control with a label ("mute", "speaker", ...). [active] shows it lit.
class CallControlButton extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool active;
  final VoidCallback? onPressed;
  final Color activeColor;
  final double size;
  final int? badge;

  const CallControlButton({
    super.key,
    required this.icon,
    required this.label,
    this.active = false,
    this.onPressed,
    this.activeColor = Colors.white,
    this.size = 62,
    this.badge,
  });

  @override
  Widget build(BuildContext context) {
    final enabled = onPressed != null;
    final bg = active ? activeColor : Colors.white.withValues(alpha: enabled ? 0.10 : 0.04);
    final fg = active ? WillyColors.bg : (enabled ? Colors.white : WillyColors.faint);
    return Semantics(
      button: true,
      toggled: active,
      label: label,
      // The label is part of the target too (the circle keeps its ripple).
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: onPressed,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Stack(
              clipBehavior: Clip.none,
              children: [
                AnimatedContainer(
                  duration: const Duration(milliseconds: 220),
                  curve: Curves.easeOut,
                  width: size,
                  height: size,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: bg,
                    border: Border.all(color: Colors.white.withValues(alpha: active ? 0 : 0.08)),
                  ),
                  child: Material(
                    type: MaterialType.transparency,
                    child: InkWell(
                      customBorder: const CircleBorder(),
                      onTap: onPressed,
                      child: Icon(icon, color: fg, size: size * 0.42),
                    ),
                  ),
                ),
                if (badge != null && badge! > 0)
                  Positioned(
                    top: -2,
                    right: -2,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                      decoration: BoxDecoration(color: WillyColors.cyan, borderRadius: BorderRadius.circular(10)),
                      child: Text('${badge! > 99 ? '99+' : badge}',
                          style: const TextStyle(color: WillyColors.bg, fontSize: 10, fontWeight: FontWeight.w800)),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                  color: enabled ? WillyColors.textSoft : WillyColors.faint,
                  fontSize: 11.5,
                  fontWeight: FontWeight.w500),
            ),
          ],
        ),
      ),
    );
  }
}

/// The big red hang-up button.
class EndCallButton extends StatelessWidget {
  final VoidCallback onPressed;
  final double size;
  final String label;

  const EndCallButton({super.key, required this.onPressed, this.size = 70, this.label = 'End'});

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: 'End call',
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: WillyColors.red,
          boxShadow: [BoxShadow(color: WillyColors.red.withValues(alpha: 0.45), blurRadius: 22, spreadRadius: 1)],
        ),
        child: Material(
          type: MaterialType.transparency,
          child: InkWell(
            customBorder: const CircleBorder(),
            onTap: onPressed,
            child: Icon(Icons.call_end_rounded, color: Colors.white, size: size * 0.44),
          ),
        ),
      ),
    );
  }
}

/// Three dots that bounce in turn: "Thinking…".
class TypingDots extends StatefulWidget {
  final Color color;
  final double size;

  const TypingDots({super.key, required this.color, this.size = 5});

  @override
  State<TypingDots> createState() => _TypingDotsState();
}

class _TypingDotsState extends State<TypingDots> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: const Duration(milliseconds: 1100))
    ..repeat();

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _c,
      builder: (context, _) => Row(
        mainAxisSize: MainAxisSize.min,
        children: List.generate(3, (i) {
          final p = ((_c.value - i * 0.18) % 1.0);
          final lift = p < 0.4 ? math.sin(p / 0.4 * math.pi) : 0.0;
          return Container(
            margin: EdgeInsets.symmetric(horizontal: widget.size * 0.35),
            width: widget.size,
            height: widget.size,
            transform: Matrix4.translationValues(0, -lift * widget.size * 0.9, 0),
            decoration: BoxDecoration(
              color: widget.color.withValues(alpha: 0.5 + lift * 0.5),
              shape: BoxShape.circle,
            ),
          );
        }),
      ),
    );
  }
}
