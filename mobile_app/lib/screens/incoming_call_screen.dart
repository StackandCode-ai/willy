import 'dart:async';
import 'package:flutter/material.dart';
import '../services/telemetry_service.dart';
import 'voice_call_screen.dart';

class IncomingCallScreen extends StatefulWidget {
  const IncomingCallScreen({super.key});

  @override
  State<IncomingCallScreen> createState() => _IncomingCallScreenState();
}

class _IncomingCallScreenState extends State<IncomingCallScreen>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulseController;
  Timer? _autoDismissTimer;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);

    // Ring + vibrate like a real incoming call (the native side stops by itself after 60 s).
    MobileTelemetryService.ringPhone(durationSec: 60);

    // Auto-dismiss after 60 seconds if not answered
    _autoDismissTimer = Timer(const Duration(seconds: 60), () {
      if (mounted) {
        Navigator.pop(context);
      }
    });
  }

  @override
  void dispose() {
    _autoDismissTimer?.cancel();
    MobileTelemetryService.stopRingPhone();
    _pulseController.dispose();
    super.dispose();
  }

  void _answerCall() {
    _autoDismissTimer?.cancel();
    MobileTelemetryService.stopRingPhone();
    Navigator.pushReplacement(
      context,
      MaterialPageRoute(
        builder: (_) => const VoiceCallScreen(isMorningCall: true),
      ),
    );
  }

  void _declineCall() {
    _autoDismissTimer?.cancel();
    MobileTelemetryService.stopRingPhone();
    Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF060913),
      body: SafeArea(
        child: Container(
          decoration: BoxDecoration(
            gradient: RadialGradient(
              center: Alignment.topCenter,
              radius: 1.2,
              colors: [
                const Color(0xFF00F2FE).withValues(alpha: 0.15),
                const Color(0xFF060913),
              ],
            ),
          ),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              // Header
              Padding(
                padding: const EdgeInsets.only(top: 40),
                child: Column(
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
                      decoration: BoxDecoration(
                        color: const Color(0xFF00F2FE).withValues(alpha: 0.12),
                        borderRadius: BorderRadius.circular(20),
                        border: Border.all(color: const Color(0xFF00F2FE).withValues(alpha: 0.3)),
                      ),
                      child: const Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(Icons.alarm_on, color: Color(0xFF00F2FE), size: 16),
                          SizedBox(width: 6),
                          Text(
                            "SCHEDULED MORNING CALL",
                            style: TextStyle(
                              color: Color(0xFF00F2FE),
                              fontSize: 11,
                              fontWeight: FontWeight.bold,
                              letterSpacing: 1.2,
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 24),
                    const Text(
                      "Willy AI",
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 32,
                        fontWeight: FontWeight.bold,
                        letterSpacing: 0.5,
                      ),
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      "Daily Morning Wake-Up & Task Briefing",
                      style: TextStyle(
                        color: Color(0xFF94A3B8),
                        fontSize: 15,
                      ),
                    ),
                  ],
                ),
              ),

              // Glowing Animated Avatar
              AnimatedBuilder(
                animation: _pulseController,
                builder: (context, child) {
                  final scale = 1.0 + (_pulseController.value * 0.12);
                  final glow = _pulseController.value * 28;
                  return Stack(
                    alignment: Alignment.center,
                    children: [
                      // Outer Ripple Wave
                      Container(
                        width: 170 * scale,
                        height: 170 * scale,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: const Color(0xFF00F2FE).withValues(alpha: 0.08),
                          boxShadow: [
                            BoxShadow(
                              color: const Color(0xFF00F2FE).withValues(alpha: 0.3),
                              blurRadius: glow,
                              spreadRadius: glow * 0.5,
                            ),
                          ],
                        ),
                      ),
                      // Core Avatar Circle
                      Container(
                        width: 130,
                        height: 130,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          gradient: const LinearGradient(
                            colors: [Color(0xFF00F2FE), Color(0xFFA855F7)],
                            begin: Alignment.topLeft,
                            end: Alignment.bottomRight,
                          ),
                          boxShadow: [
                            BoxShadow(
                              color: const Color(0xFF00F2FE).withValues(alpha: 0.5),
                              blurRadius: 20,
                            ),
                          ],
                        ),
                        child: const Center(
                          child: Icon(
                            Icons.phone_in_talk,
                            color: Colors.white,
                            size: 60,
                          ),
                        ),
                      ),
                    ],
                  );
                },
              ),

              // Incoming Actions (Decline & Answer)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 40, vertical: 48),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceAround,
                  children: [
                    // Decline Button
                    Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        GestureDetector(
                          onTap: _declineCall,
                          child: Container(
                            width: 72,
                            height: 72,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: const Color(0xFFEF4444),
                              boxShadow: [
                                BoxShadow(
                                  color: const Color(0xFFEF4444).withValues(alpha: 0.4),
                                  blurRadius: 16,
                                  spreadRadius: 2,
                                ),
                              ],
                            ),
                            child: const Icon(
                              Icons.call_end,
                              color: Colors.white,
                              size: 32,
                            ),
                          ),
                        ),
                        const SizedBox(height: 12),
                        const Text(
                          "Decline",
                          style: TextStyle(
                            color: Color(0xFF94A3B8),
                            fontSize: 14,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                      ],
                    ),

                    // Answer Button
                    Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        GestureDetector(
                          onTap: _answerCall,
                          child: Container(
                            width: 76,
                            height: 76,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              gradient: const LinearGradient(
                                colors: [Color(0xFF10B981), Color(0xFF059669)],
                                begin: Alignment.topLeft,
                                end: Alignment.bottomRight,
                              ),
                              boxShadow: [
                                BoxShadow(
                                  color: const Color(0xFF10B981).withValues(alpha: 0.5),
                                  blurRadius: 20,
                                  spreadRadius: 4,
                                ),
                              ],
                            ),
                            child: const Icon(
                              Icons.call,
                              color: Colors.white,
                              size: 36,
                            ),
                          ),
                        ),
                        const SizedBox(height: 12),
                        const Text(
                          "Answer",
                          style: TextStyle(
                            color: Color(0xFF10B981),
                            fontSize: 14,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
