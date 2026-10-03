import 'dart:async';
import 'dart:convert';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'models/presence.dart';
import 'models/push.dart';
import 'screens/activity_screen.dart';
import 'screens/devices_screen.dart';
import 'screens/incoming_call_screen.dart';
import 'screens/morning_briefing_screen.dart';
import 'screens/pc_actions.dart';
import 'screens/remote_screen.dart';
import 'screens/voice_call_screen.dart';
import 'services/api_service.dart';
import 'services/file_transfer_service.dart';
import 'services/push_service.dart';
import 'services/settings_store.dart';
import 'services/telemetry_service.dart';
import 'theme.dart';
import 'widgets/common.dart';
import 'widgets/phone_skills.dart';

final GlobalKey<NavigatorState> navigatorKey = GlobalKey<NavigatorState>();

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
    ),
  );
  await WillySettings.load();
  await PushService.initFirebase();
  runApp(const WillyMobileApp());
}

class WillyMobileApp extends StatelessWidget {
  const WillyMobileApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Willy',
      debugShowCheckedModeBanner: false,
      navigatorKey: navigatorKey,
      theme: buildWillyTheme(),
      home: const MainNavigationScreen(),
    );
  }
}

class _Tab {
  final String title;
  final String label;
  final IconData icon;

  const _Tab(this.title, this.label, this.icon);
}

const _tabs = [
  _Tab('Devices', 'Devices', Icons.devices_rounded),
  _Tab('Remote', 'Remote', Icons.touch_app_rounded),
  _Tab('Voice', 'Voice', Icons.graphic_eq_rounded),
  _Tab('Activity', 'Activity', Icons.history_rounded),
  _Tab('Morning briefing', 'Morning', Icons.wb_sunny_rounded),
];

class MainNavigationScreen extends StatefulWidget {
  const MainNavigationScreen({super.key});

  @override
  State<MainNavigationScreen> createState() => _MainNavigationScreenState();
}

class _MainNavigationScreenState extends State<MainNavigationScreen> {
  int _currentIndex = 0;
  int _tabBeforeCall = 0; // where hanging up the voice call returns to
  bool _isWakeWordListening = WillySettings.wakeWordEnabled;
  bool _ringingDialogOpen = false;
  bool _morningCallOpen = false;
  final AudioPlayer _hudAudioPlayer = AudioPlayer();
  StreamSubscription<Map<String, dynamic>>? _events;
  StreamSubscription<void>? _shared;
  bool _sendingShared = false;

  // The "Hey Willy" sheet, while it waits for an answer that may arrive late.
  String? _hudRequestId;
  ValueNotifier<String?>? _hudReply;
  bool _hudOpen = false;
  final Map<String, DateTime> _presenceShownAt = {}; // device_id -> last presence banner

  @override
  void initState() {
    super.initState();
    // Push needs the phone's device id, which ApiService.init settles first.
    ApiService.init().whenComplete(PushService.start);
    PushService.openScreen.addListener(_onPushOpenScreen);
    RemoteChat.init(); // late answers land in the Remote chat even before that tab is opened
    _events = ApiService.eventsStream.listen(_onHubEvent);
    _shared = FileTransferService.sharedArrived.listen((_) => _sendShared());
    WidgetsBinding.instance.addPostFrameCallback((_) => _sendShared()); // shared while Willy was closed

    MobileTelemetryService.setWakeWordHandler(_onGlobalWakeWord);
    if (_isWakeWordListening) MobileTelemetryService.startWakeWordDetection();
    MobileTelemetryService.requestNotificationPermission();
  }

  @override
  void dispose() {
    _events?.cancel();
    _shared?.cancel();
    PushService.openScreen.removeListener(_onPushOpenScreen);
    _hudAudioPlayer.dispose();
    ApiService.dispose();
    super.dispose();
  }

  // ------------------------------------------------------------ hub events

  void _onHubEvent(Map<String, dynamic> event) {
    if (!mounted) return;
    switch (event['type']) {
      case 'reminder_due':
        _onReminderDue(event);
        break;
      case 'morning_call_due':
        _openMorningCall();
        break;
      case 'phone_ringing':
        _showRingingDialog(event['message']?.toString() ?? 'Find My Phone');
        break;
      case 'phone_ring_stopped':
        if (_ringingDialogOpen) Navigator.of(context, rootNavigator: true).maybePop();
        break;
      case 'quickdrop_note':
        _showNote(event['title']?.toString() ?? 'Note', event['text']?.toString() ?? '');
        break;
      case 'clipboard_received':
        showWillySnack(context, 'Clipboard updated from your PC');
        break;
      case 'presence_alert':
        _showPresenceBanner(PresenceAlert.fromEvent(event));
        break;
      case 'late_reply':
        _onLateReply(event);
        break;
      case 'device_discovered':
      case 'device_offline':
        final dev = event['device'];
        if (dev is Map && dev['device_id'] != ApiService.deviceId && dev['device_type'] == 'pc') {
          final online = event['type'] == 'device_discovered';
          final id = dev['device_id']?.toString() ?? '';
          // Newer hubs also send a presence_alert that says why; give it a moment and let it win.
          Future.delayed(const Duration(milliseconds: 1500), () {
            final shownAt = _presenceShownAt[id];
            if (!mounted || (shownAt != null && DateTime.now().difference(shownAt).inSeconds < 10)) return;
            showWillySnack(context, '${dev['name']} ${online ? 'connected' : 'went offline'}', error: !online);
          });
        }
        break;
    }
  }

  /// A tapped push notification named a screen: switch to its tab (not in the middle of a call).
  void _onPushOpenScreen() {
    final screen = PushService.openScreen.value;
    if (screen == null || !mounted) return;
    PushService.openScreen.value = null;
    final tab = tabForPushScreen(screen);
    if (tab == null || _currentIndex == 2) return;
    _selectTab(tab);
  }

  /// A command that timed out finished after all. The Remote chat (RemoteChat) always gets it;
  /// here it updates the "Hey Willy" sheet that asked, or tells the user where to find it.
  void _onLateReply(Map<String, dynamic> event) {
    final id = event['request_id']?.toString();
    final raw = event['result'];
    final res = raw is Map ? raw : const {};
    final reply = (res['reply'] ?? res['error'] ?? res['message'] ?? 'Done.').toString();
    if (id != null && id == _hudRequestId && _hudOpen) {
      _hudReply?.value = reply;
      _hudRequestId = null;
      return;
    }
    if (_currentIndex == 1) return; // the Remote chat is on screen and shows it
    if (_currentIndex == 2) return; // the voice call adds its own late answers to its transcript
    final messenger = ScaffoldMessenger.maybeOf(context);
    messenger
      ?..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        backgroundColor: WillyColors.cardAlt,
        duration: const Duration(seconds: 8),
        content: Text('Willy: $reply',
            maxLines: 4, overflow: TextOverflow.ellipsis, style: const TextStyle(color: WillyColors.text)),
        action: SnackBarAction(label: 'Chat', onPressed: () => setState(() => _currentIndex = 1)),
      ));
  }

  // ------------------------------------------------------------ share sheet

  /// Sends what another app shared to Willy: files go to the PC through the hub; a link or
  /// a note is QuickDropped to the PC.
  Future<void> _sendShared() async {
    if (_sendingShared || !mounted) return;
    _sendingShared = true;
    try {
      final shared = await FileTransferService.takeShared();
      if (!mounted || shared.isEmpty) return;
      if (shared.files.isNotEmpty) {
        await PcActions.sendFiles(context, shared.files);
        return;
      }
      final text = shared.text!.trim();
      var pc = ApiService.primaryPc;
      if (pc == null) {
        await ApiService.getDevices();
        pc = ApiService.primaryPc;
      }
      if (!mounted) return;
      if (pc == null) {
        showWillySnack(context, 'No PC linked to send that to', error: true);
        return;
      }
      final url = RegExp(r'https?://\S+', caseSensitive: false).firstMatch(text)?.group(0);
      final isLink = url != null && url.length >= text.length - 1;
      final res = await ApiService.quickDrop(
        pc.id,
        url: isLink ? url : null,
        text: isLink ? null : text,
        title: isLink ? 'Opened Web Link' : 'From your phone',
      );
      if (!mounted) return;
      final ok = res['success'] == true;
      showWillySnack(
        context,
        ok ? (isLink ? 'Opened the link on ${pc.name}' : 'Sent to ${pc.name}') : (res['reply'] ?? res['error'] ?? 'Delivery failed').toString(),
        error: !ok,
      );
    } finally {
      _sendingShared = false;
    }
  }

  /// In-app banner only: the hub sends a separate `notify` action for the system notification.
  void _showPresenceBanner(PresenceAlert alert) {
    if (alert.deviceId == ApiService.deviceId) return;
    _presenceShownAt[alert.deviceId] = DateTime.now();
    final messenger = ScaffoldMessenger.maybeOf(context);
    if (messenger == null) return;
    final color = alert.isOnline ? WillyColors.green : WillyColors.amber;
    final detail = alert.detail;
    messenger
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        backgroundColor: WillyColors.cardAlt,
        duration: const Duration(seconds: 5),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: BorderSide(color: color.withValues(alpha: 0.55)),
        ),
        content: Row(
          children: [
            Icon(_presenceIcon(alert), color: color, size: 22),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(alert.headline, style: const TextStyle(color: WillyColors.text, fontWeight: FontWeight.w700)),
                  if (detail.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(detail, style: const TextStyle(color: WillyColors.muted, fontSize: 12)),
                  ],
                ],
              ),
            ),
          ],
        ),
      ));
  }

  IconData _presenceIcon(PresenceAlert alert) {
    if (alert.isOnline) return Icons.check_circle_rounded;
    return switch (alert.reason) {
      'sleep' => Icons.bedtime_rounded,
      'shutdown' => Icons.power_settings_new_rounded,
      'restart' => Icons.restart_alt_rounded,
      'logoff' => Icons.logout_rounded,
      'app_closed' => Icons.cancel_rounded,
      'connection_lost' || 'no_heartbeat' => Icons.wifi_off_rounded,
      _ => Icons.power_off_rounded,
    };
  }

  void _onReminderDue(Map<String, dynamic> event) {
    final title = event['title']?.toString() ?? 'Reminder';
    final message = event['message']?.toString() ?? '';
    MobileTelemetryService.vibrate(ms: 700);
    // Skip the system notification if a push for the same reminder already showed one.
    if (PushService.recentAlerts.add(alertIdOf(event))) {
      MobileTelemetryService.showNotification('⏰ $title', message);
    }
    // The PC reads reminders aloud; speak on the phone only when no PC is around.
    if (ApiService.primaryPc?.online != true) MobileTelemetryService.speak('$title: $message');
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Row(
          children: [
            const Icon(Icons.alarm_on_rounded, color: WillyColors.amber),
            const SizedBox(width: 10),
            Text(title, style: const TextStyle(color: Colors.white)),
          ],
        ),
        content: Text(message, style: const TextStyle(color: WillyColors.textSoft, fontSize: 16)),
        actions: [
          if (event['reminder'] is Map)
            TextButton(
              onPressed: () {
                ApiService.completeReminder(event['reminder']['id'].toString());
                Navigator.pop(ctx);
              },
              child: const Text('Mark done'),
            ),
          FilledButton(onPressed: () => Navigator.pop(ctx), child: const Text('OK')),
        ],
      ),
    );
  }

  Future<void> _openMorningCall() async {
    if (_morningCallOpen) return;
    _morningCallOpen = true;
    await navigatorKey.currentState?.push(MaterialPageRoute(builder: (_) => const IncomingCallScreen()));
    _morningCallOpen = false;
  }

  Future<void> _showRingingDialog(String message) async {
    if (_ringingDialogOpen) return;
    _ringingDialogOpen = true;
    await showDialog(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.ring_volume_rounded, color: WillyColors.green),
            SizedBox(width: 10),
            Text('Your phone is ringing', style: TextStyle(color: Colors.white, fontSize: 18)),
          ],
        ),
        content: Text('$message — triggered from Willy.', style: const TextStyle(color: WillyColors.muted)),
        actions: [
          FilledButton.icon(
            style: FilledButton.styleFrom(backgroundColor: WillyColors.red, foregroundColor: Colors.white),
            icon: const Icon(Icons.stop_circle_outlined),
            label: const Text('Stop ringing'),
            onPressed: () {
              MobileTelemetryService.stopRingPhone();
              Navigator.pop(ctx);
            },
          ),
        ],
      ),
    );
    _ringingDialogOpen = false;
  }

  void _showNote(String title, String text) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title, style: const TextStyle(color: Colors.white)),
        content: SelectableText(text, style: const TextStyle(color: WillyColors.textSoft)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Close')),
          const Padding(
            padding: EdgeInsets.only(right: 8, bottom: 12),
            child: Text('Copied to clipboard', style: TextStyle(color: WillyColors.green, fontSize: 12)),
          ),
        ],
      ),
    );
  }

  // ------------------------------------------------------------- wake word

  void _toggleWakeWord() {
    setState(() => _isWakeWordListening = !_isWakeWordListening);
    WillySettings.wakeWordEnabled = _isWakeWordListening;
    WillySettings.save();
    if (_isWakeWordListening) {
      MobileTelemetryService.startWakeWordDetection();
      showWillySnack(context, "'Hey Willy' is on. Say it anytime!");
    } else {
      MobileTelemetryService.stopWakeWordDetection();
      showWillySnack(context, "'Hey Willy' paused.");
    }
  }

  void _onGlobalWakeWord(String command, String trigger, String fullText) {
    if (!mounted) return;
    final query = command.trim();
    if (query.isEmpty) {
      // "Hey Willy" alone: open the voice tab, which takes over the wake word.
      _selectTab(2);
      return;
    }
    _showWakeWordCommandHud(query);
  }

  Future<void> _showWakeWordCommandHud(String query) async {
    final reply = ValueNotifier<String?>(null);
    final ok = ValueNotifier<bool>(true);
    _hudReply = reply;
    _hudRequestId = null;
    _hudOpen = true;

    showModalBottomSheet(
      context: context,
      backgroundColor: WillyColors.card,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
        side: BorderSide(color: WillyColors.border),
      ),
      builder: (ctx) => Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(color: WillyColors.green.withValues(alpha: 0.15), shape: BoxShape.circle),
                  child: const Icon(Icons.mic_rounded, color: WillyColors.green, size: 20),
                ),
                const SizedBox(width: 12),
                const Expanded(
                  child: Text("Hey Willy", style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 16)),
                ),
                IconButton(icon: const Icon(Icons.close, color: Colors.grey, size: 20), onPressed: () => Navigator.pop(ctx)),
              ],
            ),
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: WillyColors.bg,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: WillyColors.border),
              ),
              child: Text("\"$query\"",
                  style: const TextStyle(color: WillyColors.cyan, fontSize: 15, fontStyle: FontStyle.italic)),
            ),
            const SizedBox(height: 16),
            ValueListenableBuilder<String?>(
              valueListenable: reply,
              builder: (context, text, _) => Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (text == null)
                    const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2, color: WillyColors.purple),
                    )
                  else
                    Icon(ok.value ? Icons.check_circle_rounded : Icons.error_rounded,
                        color: ok.value ? WillyColors.green : WillyColors.red, size: 18),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(text ?? 'Working on it…',
                        style: const TextStyle(color: Colors.white, fontSize: 13, height: 1.4)),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
          ],
        ),
      ),
    ).whenComplete(() {
      if (identical(_hudReply, reply)) _hudOpen = false;
    });

    final res = await ApiService.sendCommand(query: query, returnAudio: true);
    if (identical(_hudReply, reply)) _hudRequestId = res['pending'] == true ? res['request_id']?.toString() : null;
    ok.value = res['success'] != false;
    reply.value = (res['reply'] ?? res['error'] ?? 'Done.').toString();
    final audio = res['audio_base64'];
    if (audio is String && audio.isNotEmpty) {
      try {
        await _hudAudioPlayer.play(BytesSource(Uint8List.fromList(base64Decode(audio))));
      } catch (_) {}
    }
  }

  // --------------------------------------------------------------- settings

  void _showSettingsSheet() {
    final serverController = TextEditingController(text: ApiService.baseUrl);
    final tokenController = TextEditingController(text: ApiService.token);
    final nameController = TextEditingController(text: WillySettings.deviceName);
    final codeController = TextEditingController();
    bool redeeming = false;
    String? testResult;
    bool testOk = false;
    bool testing = false;
    bool showToken = false;
    PushService.refreshStatus(); // notification permission may have changed in Android settings

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: WillyColors.card,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) => Padding(
          padding: EdgeInsets.fromLTRB(20, 16, 20, MediaQuery.of(ctx).viewInsets.bottom + 20),
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Row(
                  children: [
                    Icon(Icons.settings, color: WillyColors.cyan, size: 20),
                    SizedBox(width: 8),
                    Text("Hub settings", style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.bold)),
                  ],
                ),
                const SizedBox(height: 16),
                const Text("Hub URL", style: TextStyle(color: WillyColors.muted, fontSize: 12)),
                const SizedBox(height: 4),
                TextField(
                  controller: serverController,
                  keyboardType: TextInputType.url,
                  style: const TextStyle(color: Colors.white, fontSize: 14),
                  decoration: const InputDecoration(hintText: "https://your-server or http://192.168.1.50:8000"),
                ),
                const SizedBox(height: 14),
                const Text("Sign in with a code", style: TextStyle(color: WillyColors.muted, fontSize: 12)),
                const SizedBox(height: 4),
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: codeController,
                        textCapitalization: TextCapitalization.characters,
                        style: const TextStyle(color: Colors.white, fontSize: 14, letterSpacing: 2),
                        decoration: const InputDecoration(hintText: "ABCD-EFGH (dashboard > Add device)"),
                      ),
                    ),
                    const SizedBox(width: 8),
                    FilledButton(
                      onPressed: redeeming
                          ? null
                          : () async {
                              if (codeController.text.trim().isEmpty) return;
                              setSheet(() => redeeming = true);
                              final res = await ApiService.redeemPairCode(codeController.text,
                                  url: serverController.text, name: nameController.text);
                              if (!ctx.mounted) return;
                              if (res['ok'] == true) {
                                if (nameController.text.trim().isNotEmpty) {
                                  WillySettings.deviceName = nameController.text.trim();
                                  ApiService.deviceName = WillySettings.deviceName;
                                  await WillySettings.save();
                                }
                                Navigator.pop(ctx);
                                ApiService.reconnect();
                                if (mounted) showWillySnack(context, "Signed in as ${res['email'] ?? 'your account'}. Connecting…");
                              } else {
                                setSheet(() {
                                  redeeming = false;
                                  testOk = false;
                                  testResult = res['error']?.toString();
                                });
                              }
                            },
                      child: redeeming
                          ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                          : const Text("Sign in"),
                    ),
                  ],
                ),
                const SizedBox(height: 14),
                const Text("Remote token (advanced)", style: TextStyle(color: WillyColors.muted, fontSize: 12)),
                const SizedBox(height: 4),
                TextField(
                  controller: tokenController,
                  obscureText: !showToken,
                  style: const TextStyle(color: Colors.white, fontSize: 14),
                  decoration: InputDecoration(
                    hintText: "WILLY_REMOTE_TOKEN",
                    suffixIcon: IconButton(
                      icon: Icon(showToken ? Icons.visibility_off : Icons.visibility, color: WillyColors.faint),
                      onPressed: () => setSheet(() => showToken = !showToken),
                    ),
                  ),
                ),
                if (tokenController.text.trim().length < 20)
                  const Padding(
                    padding: EdgeInsets.only(top: 6),
                    child: Text(
                      "Not signed in yet: use a code from the dashboard (Add device), or paste your hub token.",
                      style: TextStyle(color: WillyColors.amber, fontSize: 11),
                    ),
                  ),
                const SizedBox(height: 14),
                const Text("Phone name (shown on the PC and dashboard)", style: TextStyle(color: WillyColors.muted, fontSize: 12)),
                const SizedBox(height: 4),
                TextField(
                  controller: nameController,
                  style: const TextStyle(color: Colors.white, fontSize: 14),
                  decoration: InputDecoration(hintText: ApiService.deviceName),
                ),
                const SizedBox(height: 14),
                if (testResult != null)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: Row(
                      children: [
                        Icon(testOk ? Icons.check_circle : Icons.error, color: testOk ? WillyColors.green : WillyColors.red, size: 18),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(testResult!,
                              style: TextStyle(color: testOk ? WillyColors.green : WillyColors.red, fontSize: 13)),
                        ),
                      ],
                    ),
                  ),
                Row(
                  children: [
                    OutlinedButton.icon(
                      icon: testing
                          ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                          : const Icon(Icons.network_check, size: 18),
                      label: const Text("Test"),
                      onPressed: testing
                          ? null
                          : () async {
                              setSheet(() => testing = true);
                              final res = await ApiService.checkConnection(
                                url: serverController.text,
                                tokenOverride: tokenController.text.trim(),
                              );
                              setSheet(() {
                                testing = false;
                                testOk = res['ok'] == true;
                                final server = res['server'];
                                testResult = testOk
                                    ? 'Connected in ${res['latency_ms']} ms'
                                        '${server is Map ? ' · hub v${server['version']} · ${server['pcs_online']} PC online' : ''}'
                                    : res['error']?.toString();
                              });
                            },
                    ),
                    const Spacer(),
                    TextButton(onPressed: () => Navigator.pop(ctx), child: const Text("Cancel")),
                    const SizedBox(width: 8),
                    FilledButton(
                      onPressed: () async {
                        ApiService.baseUrl = serverController.text;
                        ApiService.token = tokenController.text;
                        WillySettings.deviceName = nameController.text.trim();
                        if (WillySettings.deviceName.isNotEmpty) ApiService.deviceName = WillySettings.deviceName;
                        await WillySettings.save();
                        if (ctx.mounted) Navigator.pop(ctx);
                        ApiService.reconnect();
                        if (mounted) showWillySnack(context, "Saved. Reconnecting to the hub…");
                      },
                      child: const Text("Save"),
                    ),
                  ],
                ),
                const SizedBox(height: 14),
                const _PushStatusRow(),
                const SizedBox(height: 14),
                const Divider(height: 1, color: WillyColors.border),
                const SizedBox(height: 14),
                // Re-reads the phone's permissions each time the sheet opens.
                const PhoneSkillsPanel(),
              ],
            ),
          ),
        ),
      ),
    );
  }

  // --------------------------------------------------------------------- UI

  Widget _screenFor(int index) {
    switch (index) {
      case 1:
        return const RemoteScreen();
      case 3:
        return const ActivityScreen();
      case 4:
        return const MorningBriefingScreen();
      default:
        return const DevicesScreen();
    }
  }

  void _selectTab(int index) {
    if (index == _currentIndex) return;
    setState(() {
      if (index == 2) _tabBeforeCall = _currentIndex;
      _currentIndex = index;
    });
  }

  @override
  Widget build(BuildContext context) {
    // The voice call is a full-screen call: no app bar or tabs until you hang up.
    if (_currentIndex == 2) {
      return VoiceCallScreen(onEndCall: () => _selectTab(_tabBeforeCall == 2 ? 0 : _tabBeforeCall));
    }
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 16,
        title: Row(
          children: [
            const Text("WILLY", style: TextStyle(letterSpacing: 1.5, fontWeight: FontWeight.w900, fontSize: 17, color: WillyColors.cyan)),
            const SizedBox(width: 10),
            Flexible(
              child: Text(_tabs[_currentIndex].title,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 15, color: WillyColors.muted, fontWeight: FontWeight.w500)),
            ),
          ],
        ),
        actions: [
          const Padding(padding: EdgeInsets.only(right: 4), child: HubStatusBadge(compact: true)),
          IconButton(
            tooltip: _isWakeWordListening ? "'Hey Willy' is on" : "'Hey Willy' is off",
            icon: Icon(_isWakeWordListening ? Icons.mic_rounded : Icons.mic_off_rounded,
                color: _isWakeWordListening ? WillyColors.green : WillyColors.faint),
            onPressed: _toggleWakeWord,
          ),
          IconButton(
            icon: const Icon(Icons.settings_outlined, color: WillyColors.muted),
            tooltip: "Hub settings",
            onPressed: _showSettingsSheet,
          ),
        ],
      ),
      body: _screenFor(_currentIndex),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _currentIndex,
        backgroundColor: WillyColors.card,
        indicatorColor: WillyColors.cyan.withValues(alpha: 0.18),
        height: 66,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
        onDestinationSelected: _selectTab,
        destinations: [
          for (final tab in _tabs)
            NavigationDestination(
              icon: Icon(tab.icon, color: WillyColors.faint),
              selectedIcon: Icon(tab.icon, color: WillyColors.cyan),
              label: tab.label,
            ),
        ],
      ),
    );
  }
}

/// "Push notifications: on/off" in the settings sheet, plus whether the hub has this phone's
/// push token (the token itself is never shown).
class _PushStatusRow extends StatelessWidget {
  const _PushStatusRow();

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<PushStatus>(
      valueListenable: PushService.status,
      builder: (context, st, _) {
        final on = st.isOn;
        final detail = !st.available
            ? 'Not set up on this phone yet'
            : !st.permitted
                ? 'Notifications are blocked for Willy in Android settings'
                : st.registered
                    ? 'Token registered with the hub'
                    : 'Token not registered yet (hub offline?)';
        final color = on && st.registered ? WillyColors.green : (on ? WillyColors.amber : WillyColors.faint);
        return Row(
          children: [
            Icon(on ? Icons.notifications_active_rounded : Icons.notifications_off_rounded, color: color, size: 20),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Push notifications: ${on ? 'on' : 'off'}',
                      style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w600)),
                  const SizedBox(height: 2),
                  Text(detail, style: const TextStyle(color: WillyColors.muted, fontSize: 12)),
                ],
              ),
            ),
          ],
        );
      },
    );
  }
}
