import 'dart:async';
import 'dart:convert';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/device.dart';
import '../services/api_service.dart';
import '../services/settings_store.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'pc_actions.dart';

class _Turn {
  /// What was asked; null for a late answer, which shows only Willy's bubble.
  final String? query;
  final DateTime at = DateTime.now();
  String? reply;
  bool pending = true;
  bool success = true;
  bool fastPath = false;
  List<String> tools = const [];
  Map<String, dynamic> timings = const {};

  /// The hub is still working on it: the answer arrives later as its own bubble.
  String? awaitingRequestId;

  /// For a late answer: its request and the question it answers.
  String? lateRequestId;
  String? lateFor;

  _Turn(this.query);

  void apply(Map<String, dynamic> res) {
    pending = false;
    success = res['success'] != false;
    reply = (res['reply'] ?? res['error'] ?? res['message'] ?? 'No reply').toString();
    fastPath = res['fast_path'] == true;
    final t = res['tools'];
    tools = t is List ? t.map((x) => (x is Map ? x['name'] : x).toString()).toList() : const [];
    timings = res['timings'] is Map ? Map<String, dynamic>.from(res['timings']) : const {};
    awaitingRequestId = res['pending'] == true ? res['request_id']?.toString() : null;
  }
}

/// The Remote conversation, kept while the app runs (across tab switches) so answers that
/// arrive after the app stopped waiting (`late_reply`) still land in it.
class RemoteChat {
  static final List<_Turn> _turns = [];
  static final ValueNotifier<int> _revision = ValueNotifier(0);
  static StreamSubscription<Map<String, dynamic>>? _sub;

  static Listenable get changes => _revision;

  /// Starts listening for late answers (safe to call repeatedly; main.dart calls it at startup).
  static void init() {
    _sub ??= ApiService.eventsStream.listen((event) {
      if (event['type'] == 'late_reply') _onLateReply(event);
    });
  }

  static void _changed() => _revision.value++;

  static void _onLateReply(Map<String, dynamic> event) {
    final id = event['request_id']?.toString();
    if (id == null || _turns.any((t) => t.lateRequestId == id)) return; // already shown
    final raw = event['result'];
    final res = raw is Map ? Map<String, dynamic>.from(raw) : <String, dynamic>{};
    res.remove('pending');
    for (final t in _turns) {
      if (t.awaitingRequestId == id) t.awaitingRequestId = null;
    }
    final query = event['query']?.toString();
    _turns.add(_Turn(null)
      ..apply(res)
      ..lateRequestId = id
      ..lateFor = (query != null && query.isNotEmpty) ? query : null);
    _changed();
  }
}

class _QuickAction {
  final String label;
  final IconData icon;
  final Color color;
  final String action;
  final Map<String, dynamic> payload;

  const _QuickAction(this.label, this.icon, this.color, this.action, this.payload);
}

/// Handled on the phone (file picker + upload), not sent to the PC as a device action.
const _sendFileAction = 'send_file';

const _quickActions = [
  _QuickAction('Send file', Icons.upload_file_rounded, WillyColors.green, _sendFileAction, {}),
  _QuickAction('Lock',Icons.lock_outline_rounded, WillyColors.red, 'power_action', {'action': 'lock'}),
  _QuickAction('Play/Pause', Icons.play_arrow_rounded, WillyColors.cyan, 'media_control', {'action': 'play_pause'}),
  _QuickAction('Next', Icons.skip_next_rounded, WillyColors.cyan, 'media_control', {'action': 'next'}),
  _QuickAction('Mute', Icons.volume_off_rounded, WillyColors.amber, 'volume_control', {'action': 'mute'}),
  _QuickAction('Vol 50%', Icons.volume_up_rounded, WillyColors.pink, 'volume_control', {'action': 'set', 'level': 50}),
  _QuickAction('Desktop', Icons.desktop_windows_outlined, WillyColors.sky, 'window_action', {'action': 'minimize_all'}),
  _QuickAction('Screenshot', Icons.camera_alt_outlined, WillyColors.purple, 'take_screenshot', {}),
  _QuickAction('Chrome', Icons.public, WillyColors.blue, 'launch_application', {'target': 'chrome'}),
  _QuickAction('VS Code', Icons.code, WillyColors.blue, 'launch_application', {'target': 'code'}),
  _QuickAction('YouTube', Icons.smart_display_outlined, WillyColors.red, 'open_url', {'url': 'https://www.youtube.com'}),
  _QuickAction('Downloads', Icons.download_rounded, WillyColors.green, 'open_folder', {'path': 'downloads'}),
];

const _suggestions = [
  "What's my battery?",
  'Open YouTube and set volume to 30',
  'Remind me to stretch in 30 minutes',
  'What is using my CPU?',
  'Pause the music',
  'Take a screenshot',
];

/// Remote tab: instant one-tap controls plus a conversational assistant.
class RemoteScreen extends StatefulWidget {
  const RemoteScreen({super.key});

  @override
  State<RemoteScreen> createState() => _RemoteScreenState();
}

class _RemoteScreenState extends State<RemoteScreen> {
  List<_Turn> get _turns => RemoteChat._turns; // kept across tab switches
  final TextEditingController _input = TextEditingController();
  final ScrollController _scroll = ScrollController();
  final AudioPlayer _player = AudioPlayer();
  StreamSubscription<List<WillyDevice>>? _sub;
  String? _targetId;
  bool _speakOnPc = false;
  String? _busyAction;

  @override
  void initState() {
    super.initState();
    _sub = ApiService.devicesStream.listen((_) {
      if (mounted) setState(() {});
    });
    RemoteChat.init();
    RemoteChat.changes.addListener(_onChatChanged);
  }

  void _onChatChanged() {
    if (!mounted) return;
    setState(() {});
    _scrollToEnd();
  }

  @override
  void dispose() {
    RemoteChat.changes.removeListener(_onChatChanged);
    _sub?.cancel();
    _input.dispose();
    _scroll.dispose();
    _player.dispose();
    super.dispose();
  }

  WillyDevice? get _target {
    if (_targetId != null) {
      final d = ApiService.device(_targetId!);
      if (d != null) return d;
    }
    return ApiService.primaryPc;
  }

  Future<void> _send(String text) async {
    final query = text.trim();
    if (query.isEmpty) return;
    _input.clear();
    final turn = _Turn(query);
    setState(() => _turns.add(turn));
    _scrollToEnd();

    final speak = WillySettings.speakReplies;
    final res = await ApiService.sendCommand(
      query: query,
      targetDeviceId: _target?.id,
      speakOnPc: _speakOnPc,
      returnAudio: speak && !_speakOnPc,
    );
    // The turn lives in RemoteChat, so it is filled in even if this tab was left meanwhile.
    turn.apply(res);
    if (!mounted) return;
    setState(() {});
    _scrollToEnd();

    final audio = res['audio_base64'];
    if (speak && audio is String && audio.isNotEmpty) {
      try {
        await _player.play(BytesSource(base64Decode(audio)));
      } catch (_) {}
    }
  }

  void _scrollToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(_scroll.position.maxScrollExtent + 80,
            duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
      }
    });
  }

  Future<void> _quick(_QuickAction qa) async {
    final target = _target;
    if (target == null || !target.online) {
      showWillySnack(context, 'No PC online', error: true);
      return;
    }
    HapticFeedback.lightImpact();
    if (qa.action == _sendFileAction) {
      setState(() => _busyAction = qa.label);
      await PcActions.pickAndSendFiles(context, target);
      if (mounted) setState(() => _busyAction = null);
      return;
    }
    setState(() => _busyAction = qa.label);
    final sw = Stopwatch()..start();
    final res = await ApiService.deviceAction(target.id, qa.action, qa.payload);
    sw.stop();
    if (!mounted) return;
    setState(() => _busyAction = null);
    final ok = res['success'] == true;
    showWillySnack(
      context,
      ok ? '${qa.label} · ${sw.elapsedMilliseconds} ms' : (res['reply'] ?? res['error'] ?? 'Failed').toString(),
      error: !ok,
    );
  }

  Future<void> _newConversation() async {
    await ApiService.resetSession('mobile');
    if (mounted) setState(() => _turns.clear());
  }

  @override
  Widget build(BuildContext context) {
    final pcs = ApiService.pcs;
    final target = _target;

    return Column(
      children: [
        // Target + options
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 4, 8, 0),
          child: Row(
            children: [
              Expanded(child: _targetChip(pcs, target)),
              IconButton(
                tooltip: WillySettings.speakReplies ? 'Replies are spoken' : 'Replies are silent',
                icon: Icon(WillySettings.speakReplies ? Icons.record_voice_over : Icons.voice_over_off,
                    color: WillySettings.speakReplies ? WillyColors.cyan : WillyColors.faint),
                onPressed: () {
                  setState(() => WillySettings.speakReplies = !WillySettings.speakReplies);
                  WillySettings.save();
                },
              ),
              IconButton(
                tooltip: _speakOnPc ? 'Speaking on PC speakers' : 'Speak on PC speakers',
                icon: Icon(Icons.speaker_rounded, color: _speakOnPc ? WillyColors.green : WillyColors.faint),
                onPressed: () => setState(() => _speakOnPc = !_speakOnPc),
              ),
              IconButton(
                tooltip: 'New conversation',
                icon: const Icon(Icons.add_comment_outlined, color: WillyColors.muted),
                onPressed: _newConversation,
              ),
            ],
          ),
        ),
        // Instant controls (no LLM)
        SizedBox(
          height: 86,
          child: ListView.separated(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            scrollDirection: Axis.horizontal,
            itemCount: _quickActions.length,
            separatorBuilder: (_, __) => const SizedBox(width: 8),
            itemBuilder: (context, i) {
              final qa = _quickActions[i];
              return SizedBox(
                width: 78,
                child: ActionTile(
                  icon: qa.icon,
                  label: qa.label,
                  color: qa.color,
                  busy: _busyAction == qa.label,
                  onTap: target?.online == true ? () => _quick(qa) : null,
                ),
              );
            },
          ),
        ),
        const Divider(height: 1, color: WillyColors.border),
        // Conversation
        Expanded(
          child: _turns.isEmpty
              ? _emptyConversation()
              : ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
                  itemCount: _turns.length,
                  itemBuilder: (context, i) => _turnView(_turns[i]),
                ),
        ),
        // Suggestions + input
        SizedBox(
          height: 40,
          child: ListView.separated(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            scrollDirection: Axis.horizontal,
            itemCount: _suggestions.length,
            separatorBuilder: (_, __) => const SizedBox(width: 8),
            itemBuilder: (context, i) => ActionChip(
              backgroundColor: WillyColors.card,
              side: const BorderSide(color: WillyColors.border),
              label: Text(_suggestions[i], style: const TextStyle(color: WillyColors.sky, fontSize: 12)),
              onPressed: () => _send(_suggestions[i]),
            ),
          ),
        ),
        SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 10),
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _input,
                    textInputAction: TextInputAction.send,
                    onSubmitted: _send,
                    style: const TextStyle(color: Colors.white),
                    decoration: InputDecoration(
                      hintText: 'Ask Willy or give a PC command…',
                      prefixIcon: const Icon(Icons.terminal_rounded, color: WillyColors.cyan, size: 20),
                      contentPadding: const EdgeInsets.symmetric(vertical: 12),
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(24)),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(24),
                        borderSide: const BorderSide(color: WillyColors.border),
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.filled(
                  style: IconButton.styleFrom(backgroundColor: WillyColors.cyan, foregroundColor: Colors.black),
                  icon: const Icon(Icons.send_rounded),
                  onPressed: () => _send(_input.text),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _targetChip(List<WillyDevice> pcs, WillyDevice? target) {
    final online = target?.online == true;
    final label = target == null ? 'No PC linked yet' : '${target.name} · ${online ? 'online' : 'offline'}';
    final chip = Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: WillyColors.card,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: WillyColors.border),
      ),
      child: Row(
        children: [
          const Icon(Icons.laptop_windows_rounded, color: WillyColors.cyan, size: 18),
          const SizedBox(width: 8),
          Expanded(
            child: Text(label,
                maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: Colors.white, fontSize: 13)),
          ),
          Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(shape: BoxShape.circle, color: online ? WillyColors.green : WillyColors.red),
          ),
          if (pcs.length > 1) const Icon(Icons.arrow_drop_down, color: WillyColors.muted),
        ],
      ),
    );
    if (pcs.length <= 1) return chip;
    return PopupMenuButton<String>(
      color: WillyColors.card,
      onSelected: (id) => setState(() => _targetId = id),
      itemBuilder: (_) => [
        for (final pc in pcs)
          PopupMenuItem(value: pc.id, child: Text('${pc.name}${pc.online ? '' : ' (offline)'}')),
      ],
      child: chip,
    );
  }

  Widget _emptyConversation() {
    return ListView(
      padding: const EdgeInsets.all(24),
      children: const [
        SizedBox(height: 12),
        Icon(Icons.forum_outlined, size: 56, color: WillyColors.borderStrong),
        SizedBox(height: 12),
        Text('Talk to your PC',
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white70, fontSize: 18, fontWeight: FontWeight.bold)),
        SizedBox(height: 6),
        Text(
          'Simple commands like "volume 40" or "lock my pc" run instantly (⚡). '
          'Anything else goes to the AI brain, which can chain several actions.',
          textAlign: TextAlign.center,
          style: TextStyle(color: WillyColors.faint, fontSize: 13),
        ),
      ],
    );
  }

  Widget _turnView(_Turn t) {
    final total = t.timings['total_ms'];
    final breakdown = [
      if (t.timings['llm_ms'] != null) 'AI ${t.timings['llm_ms']} ms',
      if (t.timings['tool_ms'] != null) 'PC ${t.timings['tool_ms']} ms',
      if (t.timings['tts_ms'] != null) 'voice ${t.timings['tts_ms']} ms',
    ].join(' · ');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (t.query == null && t.lateFor != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 4, right: 36),
            child: Row(
              children: [
                const Icon(Icons.subdirectory_arrow_right_rounded, size: 14, color: WillyColors.faint),
                const SizedBox(width: 4),
                Expanded(
                  child: Text('Answer to "${t.lateFor}"',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: WillyColors.faint, fontSize: 11, fontStyle: FontStyle.italic)),
                ),
              ],
            ),
          ),
        if (t.query != null)
        Align(
          alignment: Alignment.centerRight,
          child: Container(
            margin: const EdgeInsets.only(left: 48, bottom: 6),
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: const BoxDecoration(
              color: Color(0xFF1E293B),
              borderRadius: BorderRadius.only(
                topLeft: Radius.circular(16),
                topRight: Radius.circular(16),
                bottomLeft: Radius.circular(16),
                bottomRight: Radius.circular(4),
              ),
            ),
            child: Text(t.query ?? '', style: const TextStyle(color: Colors.white, fontSize: 14)),
          ),
        ),
        Align(
          alignment: Alignment.centerLeft,
          child: Container(
            margin: const EdgeInsets.only(right: 36, bottom: 14),
            padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
            decoration: BoxDecoration(
              color: WillyColors.card,
              border: Border.all(
                color: t.pending
                    ? WillyColors.border
                    : (t.success ? WillyColors.purple.withValues(alpha: 0.35) : WillyColors.red.withValues(alpha: 0.5)),
              ),
              borderRadius: const BorderRadius.only(
                topLeft: Radius.circular(4),
                topRight: Radius.circular(16),
                bottomLeft: Radius.circular(16),
                bottomRight: Radius.circular(16),
              ),
            ),
            child: t.pending
                ? const Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: WillyColors.purple)),
                      SizedBox(width: 10),
                      Text('Willy is working…', style: TextStyle(color: WillyColors.muted, fontSize: 13)),
                    ],
                  )
                : Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SelectableText(t.reply ?? '', style: const TextStyle(color: WillyColors.textSoft, fontSize: 14, height: 1.35)),
                      const SizedBox(height: 8),
                      Wrap(
                        spacing: 6,
                        runSpacing: 4,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          if (t.awaitingRequestId != null)
                            const StatusPill(text: 'STILL WORKING', color: WillyColors.amber, dot: false),
                          if (t.lateRequestId != null) const StatusPill(text: 'LATE REPLY', color: WillyColors.purple, dot: false),
                          if (t.fastPath) const StatusPill(text: '⚡ FAST PATH', color: WillyColors.amber, dot: false),
                          for (final tool in t.tools)
                            StatusPill(text: tool.replaceAll('_', ' '), color: WillyColors.sky, dot: false),
                          if (total != null)
                            Tooltip(
                              message: breakdown.isEmpty ? 'Total time' : breakdown,
                              child: Text('$total ms', style: const TextStyle(color: WillyColors.faint, fontSize: 11)),
                            ),
                        ],
                      ),
                    ],
                  ),
          ),
        ),
      ],
    );
  }
}
