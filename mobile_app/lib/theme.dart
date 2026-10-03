import 'package:flutter/material.dart';

/// Shared palette so every screen matches the web dashboard and the PC app.
class WillyColors {
  static const bg = Color(0xFF060913);
  static const bgElevated = Color(0xFF0B0F19);
  static const card = Color(0xFF0F172A);
  static const cardAlt = Color(0xFF111827);
  static const border = Color(0xFF1E293B);
  static const borderStrong = Color(0xFF334155);
  static const cyan = Color(0xFF00F2FE);
  static const sky = Color(0xFF38BDF8);
  static const blue = Color(0xFF3B82F6);
  static const purple = Color(0xFFA855F7);
  static const violet = Color(0xFF7C3AED);
  static const green = Color(0xFF10B981);
  static const red = Color(0xFFEF4444);
  static const amber = Color(0xFFF59E0B);
  static const pink = Color(0xFFEC4899);
  static const text = Color(0xFFF8FAFC);
  static const textSoft = Color(0xFFE2E8F0);
  static const muted = Color(0xFF94A3B8);
  static const faint = Color(0xFF64748B);

  /// Green below 60 %, amber below 85 %, red above: used by load gauges.
  static Color load(double? pct) {
    if (pct == null) return faint;
    if (pct < 60) return green;
    if (pct < 85) return amber;
    return red;
  }

  /// Battery colour: red when low, amber when medium.
  static Color battery(double? pct, {bool charging = false}) {
    if (pct == null) return faint;
    if (charging) return green;
    if (pct <= 15) return red;
    if (pct <= 35) return amber;
    return green;
  }
}

ThemeData buildWillyTheme() {
  return ThemeData(
    brightness: Brightness.dark,
    useMaterial3: true,
    scaffoldBackgroundColor: WillyColors.bg,
    primaryColor: WillyColors.cyan,
    colorScheme: const ColorScheme.dark(
      primary: WillyColors.cyan,
      secondary: WillyColors.purple,
      surface: WillyColors.card,
      error: WillyColors.red,
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: WillyColors.bg,
      elevation: 0,
      scrolledUnderElevation: 0,
      centerTitle: false,
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: WillyColors.card,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(20),
        side: const BorderSide(color: WillyColors.border),
      ),
    ),
    snackBarTheme: const SnackBarThemeData(behavior: SnackBarBehavior.floating),
    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.selected) ? Colors.white : WillyColors.muted),
      trackColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.selected) ? WillyColors.cyan : WillyColors.border),
      trackOutlineColor: WidgetStateProperty.all(Colors.transparent),
    ),
    sliderTheme: const SliderThemeData(
      activeTrackColor: WillyColors.cyan,
      inactiveTrackColor: WillyColors.border,
      thumbColor: WillyColors.cyan,
      overlayColor: Color(0x2200F2FE),
      trackHeight: 4,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: WillyColors.bgElevated,
      hintStyle: const TextStyle(color: WillyColors.faint),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: WillyColors.border),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: WillyColors.border),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: WillyColors.cyan),
      ),
    ),
  );
}

/// "340 KB/s", "1.2 MB/s".
String formatRate(double? kbps) {
  if (kbps == null) return '—';
  if (kbps >= 1024) return '${(kbps / 1024).toStringAsFixed(1)} MB/s';
  if (kbps >= 10) return '${kbps.round()} KB/s';
  return '${kbps.toStringAsFixed(1)} KB/s';
}

/// "just now", "12s ago", "5m ago", "3h ago", "2d ago".
String timeAgo(double? epochSeconds) {
  if (epochSeconds == null || epochSeconds <= 0) return 'never';
  final diff = DateTime.now().millisecondsSinceEpoch / 1000 - epochSeconds;
  if (diff < 3) return 'just now';
  if (diff < 60) return '${diff.round()}s ago';
  if (diff < 3600) return '${(diff / 60).floor()}m ago';
  if (diff < 86400) return '${(diff / 3600).floor()}h ago';
  return '${(diff / 86400).floor()}d ago';
}

/// 5400 -> "1h 30m", 90 -> "1m 30s".
String formatDuration(num? seconds) {
  if (seconds == null || seconds < 0) return '—';
  final s = seconds.round();
  if (s < 60) return '${s}s';
  if (s < 3600) return '${s ~/ 60}m ${s % 60}s';
  if (s < 86400) return '${s ~/ 3600}h ${(s % 3600) ~/ 60}m';
  return '${s ~/ 86400}d ${(s % 86400) ~/ 3600}h';
}

String formatHours(double? hours) {
  if (hours == null) return '—';
  return formatDuration(hours * 3600);
}
