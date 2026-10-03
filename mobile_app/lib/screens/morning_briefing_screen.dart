import 'dart:async';

import 'package:flutter/material.dart';

import '../services/api_service.dart';
import '../services/telemetry_service.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'incoming_call_screen.dart';

class MorningBriefingScreen extends StatefulWidget {
  const MorningBriefingScreen({super.key});

  @override
  State<MorningBriefingScreen> createState() => _MorningBriefingScreenState();
}

class _MorningBriefingScreenState extends State<MorningBriefingScreen> {
  bool _isLoading = true;
  bool _callEnabled = true;
  String _callTime = "07:00";
  String _chatgptThreadId = "";
  List<String> _tasks = [];
  Map<String, dynamic> _telemetry = {};
  List<dynamic> _reminders = [];
  List<dynamic> _alarms = [];
  StreamSubscription<Map<String, dynamic>>? _events;

  @override
  void initState() {
    super.initState();
    _loadData();
    _events = ApiService.eventsStream.listen((event) {
      if (!mounted) return;
      if (event['type'] == 'reminders_changed') {
        setState(() {
          if (event['reminders'] is List) _reminders = List<dynamic>.from(event['reminders']);
          if (event['alarms'] is List) _alarms = List<dynamic>.from(event['alarms']);
        });
      } else if (event['type'] == 'reminder_due') {
        _loadReminders();
      }
    });
  }

  @override
  void dispose() {
    _events?.cancel();
    super.dispose();
  }

  Future<void> _loadReminders() async {
    final results = await Future.wait([ApiService.getReminders(), ApiService.getAlarms()]);
    if (!mounted) return;
    setState(() {
      _reminders = results[0];
      _alarms = results[1];
    });
  }

  Future<void> _loadData() async {
    setState(() => _isLoading = true);

    // Sync telemetry from phone (real counts only; nothing is sent if unavailable)
    await MobileTelemetryService.syncTelemetryToServer();

    final results = await Future.wait([
      ApiService.getMorningConfig(),
      ApiService.getMorningBriefing(includeAudio: false),
      ApiService.getReminders(),
      ApiService.getAlarms(),
    ]);
    if (!mounted) return;
    final configRes = results[0] as Map<String, dynamic>;
    final briefingRes = results[1] as Map<String, dynamic>;

    setState(() {
      if (configRes['success'] == true) {
        final cfg = configRes['config'] ?? {};
        _callTime = cfg['call_time'] ?? "07:00";
        _callEnabled = cfg['call_enabled'] ?? true;
        _chatgptThreadId = cfg['chatgpt_thread_id'] ?? "";
        _tasks = List<String>.from(cfg['cached_tasks'] ?? []);
      }
      if (briefingRes['success'] == true) {
        _telemetry = Map<String, dynamic>.from(briefingRes['telemetry'] ?? {});
        if (_tasks.isEmpty) _tasks = List<String>.from(briefingRes['tasks'] ?? []);
      }
      _reminders = results[2] as List<dynamic>;
      _alarms = results[3] as List<dynamic>;
      _isLoading = false;
    });
  }

  Future<void> _syncChatGPTTasks() async {
    showWillySnack(context, "Syncing tasks from ChatGPT thread…");
    final res = await ApiService.syncMorningTasks();
    if (!mounted) return;
    if (res['success'] == true) {
      setState(() => _tasks = List<String>.from(res['tasks'] ?? []));
      showWillySnack(context, "Synced ${_tasks.length} tasks from ChatGPT!");
    }
  }

  Future<void> _saveConfig() => ApiService.updateMorningConfig({
        "call_time": _callTime,
        "call_enabled": _callEnabled,
        "chatgpt_thread_id": _chatgptThreadId,
      });

  Future<void> _pickTime() async {
    final parts = _callTime.split(":");
    final picked = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(
        hour: int.tryParse(parts[0]) ?? 7,
        minute: parts.length > 1 ? (int.tryParse(parts[1]) ?? 0) : 0,
      ),
    );
    if (picked != null) {
      setState(() => _callTime = "${picked.hour.toString().padLeft(2, '0')}:${picked.minute.toString().padLeft(2, '0')}");
      await _saveConfig();
    }
  }

  String _formatTimeOfDay(TimeOfDay t) {
    final h12 = t.hourOfPeriod == 0 ? 12 : t.hourOfPeriod;
    return "${h12.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')} ${t.period == DayPeriod.am ? 'AM' : 'PM'}";
  }

  void _showAddReminderDialog() {
    final textController = TextEditingController();
    TimeOfDay time = TimeOfDay.fromDateTime(DateTime.now().add(const Duration(hours: 1)));
    String day = 'today';

    showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialog) => AlertDialog(
          title: const Text("New reminder", style: TextStyle(color: Colors.white)),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              TextField(
                controller: textController,
                autofocus: true,
                style: const TextStyle(color: Colors.white),
                decoration: const InputDecoration(hintText: "e.g. Review the pull request"),
              ),
              const SizedBox(height: 14),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      icon: const Icon(Icons.schedule, size: 18),
                      label: Text(_formatTimeOfDay(time)),
                      onPressed: () async {
                        final picked = await showTimePicker(context: ctx, initialTime: time);
                        if (picked != null) setDialog(() => time = picked);
                      },
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(value: 'today', label: Text('Today')),
                  ButtonSegment(value: 'tomorrow', label: Text('Tomorrow')),
                ],
                selected: {day},
                showSelectedIcon: false,
                onSelectionChanged: (s) => setDialog(() => day = s.first),
              ),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text("Cancel")),
            FilledButton(
              onPressed: () async {
                final text = textController.text.trim();
                if (text.isEmpty) return;
                Navigator.pop(ctx);
                await ApiService.createReminder(text: text, time: _formatTimeOfDay(time), date: day);
                _loadReminders();
              },
              child: const Text("Save"),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _showAddAlarmDialog() async {
    final picked = await showTimePicker(context: context, initialTime: const TimeOfDay(hour: 7, minute: 0));
    if (picked == null || !mounted) return;
    final labelController = TextEditingController(text: 'Wake up');
    final label = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Alarm at ${_formatTimeOfDay(picked)}', style: const TextStyle(color: Colors.white)),
        content: TextField(
          controller: labelController,
          autofocus: true,
          style: const TextStyle(color: Colors.white),
          decoration: const InputDecoration(hintText: 'Label'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, labelController.text.trim()), child: const Text('Save')),
        ],
      ),
    );
    if (label == null) return;
    await ApiService.createAlarm(time: _formatTimeOfDay(picked), label: label.isEmpty ? 'Alarm' : label);
    _loadReminders();
  }

  void _showChatGPTConfigDialog() {
    final threadController = TextEditingController(text: _chatgptThreadId);
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text("ChatGPT Thread Settings", style: TextStyle(color: Colors.white)),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              "Enter your ChatGPT Thread ID or conversation topic where tasks are tracked:",
              style: TextStyle(color: WillyColors.muted, fontSize: 13),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: threadController,
              style: const TextStyle(color: Colors.white),
              decoration: const InputDecoration(hintText: "e.g. thread_abc123 or project-work"),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text("Cancel")),
          FilledButton(
            onPressed: () async {
              final newId = threadController.text.trim();
              Navigator.pop(ctx);
              setState(() => _chatgptThreadId = newId);
              await _saveConfig();
              _syncChatGPTTasks();
            },
            child: const Text("Save & Sync"),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_isLoading) {
      return const Center(child: CircularProgressIndicator(color: WillyColors.cyan));
    }

    final unreadEmails = _telemetry['unread_emails_count'] ?? 0;
    final emailSenders = List<String>.from(_telemetry['email_senders'] ?? []);
    final missedCalls = _telemetry['missed_calls_count'] ?? 0;
    final missedCallList = List<dynamic>.from(_telemetry['missed_calls'] ?? []);
    final whatsappCount = _telemetry['whatsapp_unread_count'] ?? 0;
    final whatsappSenders = List<dynamic>.from(_telemetry['whatsapp_senders'] ?? []);
    final totalNotifs = _telemetry['total_notifications_count'] ?? 0;
    final pending = _reminders.where((r) => r is Map && r['completed'] != true).toList();
    final done = _reminders.where((r) => r is Map && r['completed'] == true).toList();

    return RefreshIndicator(
      onRefresh: _loadData,
      color: WillyColors.cyan,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          // 1. Scheduled call
          WillyCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.alarm, color: WillyColors.cyan, size: 20),
                    const SizedBox(width: 8),
                    const Expanded(
                      child: Text("Daily Wake-Up Call",
                          style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
                    ),
                    Switch(
                      value: _callEnabled,
                      onChanged: (val) async {
                        setState(() => _callEnabled = val);
                        await _saveConfig();
                      },
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    InkWell(
                      onTap: _pickTime,
                      borderRadius: BorderRadius.circular(10),
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                        decoration: BoxDecoration(
                          color: WillyColors.border,
                          borderRadius: BorderRadius.circular(10),
                          border: Border.all(color: WillyColors.cyan.withValues(alpha: 0.5)),
                        ),
                        child: Row(
                          children: [
                            Text(_callTime,
                                style: const TextStyle(color: WillyColors.cyan, fontSize: 24, fontWeight: FontWeight.bold)),
                            const SizedBox(width: 8),
                            const Icon(Icons.edit, color: Colors.grey, size: 16),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: FilledButton.icon(
                        style: FilledButton.styleFrom(
                          backgroundColor: WillyColors.green,
                          foregroundColor: Colors.white,
                          padding: const EdgeInsets.symmetric(vertical: 14),
                        ),
                        icon: const Icon(Icons.ring_volume, size: 20),
                        label: const Text("Test call"),
                        onPressed: () => Navigator.push(
                          context,
                          MaterialPageRoute(builder: (_) => const IncomingCallScreen()),
                        ),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),

          // 2. Phone status
          WillyCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SectionTitle(
                  'Mobile status & messages',
                  icon: Icons.smartphone,
                  trailing: TextButton.icon(
                    icon: const Icon(Icons.sync, size: 16),
                    label: const Text("Sync now"),
                    onPressed: () async {
                      final res = await MobileTelemetryService.syncTelemetryToServer();
                      if (res.isEmpty && context.mounted) {
                        showWillySnack(context, 'Allow notification access for Willy to read counts.', error: true);
                      }
                      _loadData();
                    },
                  ),
                ),
                Row(
                  children: [
                    _buildMetricTile("Mails", unreadEmails.toString(), Icons.email, WillyColors.blue),
                    const SizedBox(width: 8),
                    _buildMetricTile("Calls", missedCalls.toString(), Icons.phone_missed, WillyColors.red),
                    const SizedBox(width: 8),
                    _buildMetricTile("WhatsApp", whatsappCount.toString(), Icons.chat, WillyColors.green),
                    const SizedBox(width: 8),
                    _buildMetricTile("Notifs", totalNotifs.toString(), Icons.notifications, const Color(0xFFFBBF24)),
                  ],
                ),
                if (missedCalls > 0 && missedCallList.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  Text(
                    "Missed calls: ${missedCallList.map((c) => c is Map ? c['name'] : c.toString()).take(2).join(', ')}",
                    style: const TextStyle(color: WillyColors.red, fontSize: 13),
                  ),
                ],
                if (whatsappCount > 0 && whatsappSenders.isNotEmpty) ...[
                  const SizedBox(height: 6),
                  Text(
                    "WhatsApp from: ${whatsappSenders.map((w) => w is Map ? w['name'] : w.toString()).take(2).join(', ')}",
                    style: const TextStyle(color: WillyColors.green, fontSize: 13),
                  ),
                ],
                if (unreadEmails > 0 && emailSenders.isNotEmpty) ...[
                  const SizedBox(height: 6),
                  Text("Emails from: ${emailSenders.take(2).join(', ')}",
                      style: const TextStyle(color: WillyColors.blue, fontSize: 13)),
                ],
              ],
            ),
          ),

          // 3. ChatGPT tasks
          WillyCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SectionTitle(
                  'ChatGPT thread tasks',
                  icon: Icons.checklist,
                  trailing: IconButton(
                    icon: const Icon(Icons.settings, color: Colors.grey, size: 18),
                    onPressed: _showChatGPTConfigDialog,
                  ),
                ),
                if (_tasks.isEmpty)
                  const Text("No tasks extracted yet. Tap settings to configure your ChatGPT thread.",
                      style: TextStyle(color: WillyColors.muted, fontSize: 13))
                else
                  ..._tasks.map(
                    (task) => Padding(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Icon(Icons.check_circle_outline, color: WillyColors.green, size: 18),
                          const SizedBox(width: 8),
                          Expanded(child: Text(task, style: const TextStyle(color: Colors.white, fontSize: 14))),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
          ),

          // 4. Reminders
          WillyCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SectionTitle(
                  'Reminders',
                  icon: Icons.schedule,
                  trailing: IconButton(
                    icon: const Icon(Icons.add_circle, color: WillyColors.cyan, size: 24),
                    onPressed: _showAddReminderDialog,
                  ),
                ),
                if (_reminders.isEmpty)
                  const Text("No reminders. Say \"remind me to …\" or tap + to add one. They pop up on your PC and phone when due.",
                      style: TextStyle(color: WillyColors.muted, fontSize: 13)),
                for (final rem in [...pending, ...done]) _reminderRow(rem as Map),
              ],
            ),
          ),

          // 5. Alarms
          WillyCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SectionTitle(
                  'Alarms',
                  icon: Icons.alarm_on_rounded,
                  trailing: IconButton(
                    icon: const Icon(Icons.add_alarm_rounded, color: WillyColors.cyan, size: 24),
                    onPressed: _showAddAlarmDialog,
                  ),
                ),
                if (_alarms.isEmpty)
                  const Text("No alarms set. Daily alarms ring on every connected device.",
                      style: TextStyle(color: WillyColors.muted, fontSize: 13)),
                for (final alarm in _alarms) _alarmRow(alarm as Map),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _reminderRow(Map rem) {
    final completed = rem['completed'] == true;
    final missed = rem['missed'] == true;
    final fired = rem['fired_at'] != null;
    final today = DateTime.now().toIso8601String().substring(0, 10);
    final date = rem['date']?.toString();
    final dateLabel = (date == null || date == today) ? '' : ' · $date';
    return Dismissible(
      key: ValueKey(rem['id']),
      direction: DismissDirection.endToStart,
      background: Container(
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.only(right: 16),
        color: WillyColors.red.withValues(alpha: 0.2),
        child: const Icon(Icons.delete_outline, color: WillyColors.red),
      ),
      onDismissed: (_) async {
        setState(() => _reminders.removeWhere((r) => r is Map && r['id'] == rem['id']));
        await ApiService.deleteReminder(rem['id'].toString());
      },
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Row(
          children: [
            Checkbox(
              value: completed,
              activeColor: WillyColors.green,
              onChanged: (v) async {
                setState(() => rem['completed'] = v == true);
                await ApiService.completeReminder(rem['id'].toString(), completed: v == true);
              },
            ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    rem['text']?.toString() ?? '',
                    style: TextStyle(
                      color: completed ? WillyColors.faint : Colors.white,
                      fontSize: 14,
                      decoration: completed ? TextDecoration.lineThrough : null,
                    ),
                  ),
                  Text(
                    '${rem['time'] ?? ''}$dateLabel${missed ? ' · missed' : (fired && !completed ? ' · rang' : '')}',
                    style: TextStyle(color: missed ? WillyColors.red : WillyColors.cyan, fontSize: 12),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _alarmRow(Map alarm) {
    final enabled = alarm['enabled'] != false;
    return Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(alarm['time']?.toString() ?? '',
                  style: TextStyle(
                      color: enabled ? Colors.white : WillyColors.faint, fontSize: 20, fontWeight: FontWeight.w700)),
              Text(alarm['label']?.toString() ?? '', style: const TextStyle(color: WillyColors.muted, fontSize: 12)),
            ],
          ),
        ),
        Switch(
          value: enabled,
          onChanged: (v) async {
            setState(() => alarm['enabled'] = v);
            await ApiService.toggleAlarm(alarm['id'].toString(), v);
          },
        ),
        IconButton(
          icon: const Icon(Icons.delete_outline, color: WillyColors.faint),
          onPressed: () async {
            setState(() => _alarms.removeWhere((a) => a is Map && a['id'] == alarm['id']));
            await ApiService.deleteAlarm(alarm['id'].toString());
          },
        ),
      ],
    );
  }

  Widget _buildMetricTile(String label, String value, IconData icon, Color color) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 12),
        decoration: BoxDecoration(
          color: WillyColors.border,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: color.withValues(alpha: 0.3)),
        ),
        child: Column(
          children: [
            Icon(icon, color: color, size: 20),
            const SizedBox(height: 6),
            Text(value, style: TextStyle(color: color, fontSize: 18, fontWeight: FontWeight.bold)),
            const SizedBox(height: 2),
            Text(label, style: const TextStyle(color: WillyColors.muted, fontSize: 11)),
          ],
        ),
      ),
    );
  }
}
