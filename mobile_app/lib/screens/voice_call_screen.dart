import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import '../models/call_panel.dart';
import '../services/api_service.dart';
import '../services/settings_store.dart';
import '../services/telemetry_service.dart';
import '../theme.dart';
import '../widgets/call_widgets.dart';
import '../widgets/common.dart';
import '../widgets/simple_markdown.dart';
import 'call_panels/panel_host.dart';

enum CallState { connecting, idle, listening, thinking, speaking }

class _VoiceTurn {
  final String you;
  final String willy;
  final bool success;
  final bool fastPath;
  final Map<String, dynamic> timings;
  final CallPanelRequest? panel;
  final DateTime at = DateTime.now();

  _VoiceTurn(this.you, this.willy, {this.success = true, this.fastPath = false, this.timings = const {}, this.panel});
}

/// Hands-free voice call with Willy, laid out like a phone call.
/// Tap Willy to talk; recording stops by itself when you pause. Replies can pop open
/// panels (PC files, PC screen, camera) over the call while it keeps listening.
class VoiceCallScreen extends StatefulWidget {
  final bool isMorningCall;

  /// Called when the user hangs up (the Voice tab goes back to the previous tab).
  /// Without it the screen pops its route.
  final VoidCallback? onEndCall;

  const VoiceCallScreen({super.key, this.isMorningCall = false, this.onEndCall});

  @override
  State<VoiceCallScreen> createState() => _VoiceCallScreenState();
}

class _VoiceCallScreenState extends State<VoiceCallScreen> {
  static const _speechDb = -35.0; // louder than this = speech
  static const _silenceHold = Duration(milliseconds: 1100);
  // People pause early in a command ("open … chrome"): wait longer until ~1.2 s of speech.
  static const _silenceHoldEarly = Duration(milliseconds: 1500);
  static const _earlySpeech = Duration(milliseconds: 1200);
  static const _minSpeech = Duration(milliseconds: 400); // less is a cough or a click: don't send
  static const _maxUtterance = Duration(seconds: 15);
  static const _noSpeechTimeout = Duration(seconds: 6);
  static const _peekHeight = 66.0; // collapsed transcript sheet

  CallState _callState = CallState.connecting;
  late final AudioRecorder _audioRecorder;
  late final AudioPlayer _audioPlayer;

  Timer? _callTimer;
  Timer? _connectTimer;
  final ValueNotifier<int> _callSeconds = ValueNotifier(0);
  String _statusText = 'Connecting…';
  String _caption = ''; // Willy's last reply, as plain text
  final List<_VoiceTurn> _turns = [];

  StreamSubscription<Amplitude>? _ampSub;
  bool _heardSpeech = false;
  Duration _speechHeard = Duration.zero; // speech time in the current utterance
  DateTime? _lastAmpAt;
  DateTime? _silenceSince;
  DateTime? _recordStart;
  final ValueNotifier<double> _level = ValueNotifier(0);
  bool _stopping = false;

  bool _muted = false;
  bool _speakerOn = true;
  bool _handsFree = WillySettings.callHandsFree;
  bool _ending = false;

  // Commands the hub answered "still working" to: their answer arrives as a late_reply.
  final Set<String> _awaitingLate = {};
  StreamSubscription<Map<String, dynamic>>? _hubEvents;
  StreamSubscription<HubStatus>? _hubStatus;
  HubStatus _hub = ApiService.status;

  // Panels over the call.
  final ValueNotifier<CallPanelRequest?> _panelReq = ValueNotifier(null);
  final ValueNotifier<CallLine> _callLine = ValueNotifier(const CallLine('Connecting', WillyColors.amber));
  ModalBottomSheetRoute<void>? _panelRoute;

  final DraggableScrollableController _sheet = DraggableScrollableController();

  @override
  void initState() {
    super.initState();
    _audioRecorder = AudioRecorder();
    _audioPlayer = AudioPlayer();
    _audioPlayer.onPlayerComplete.listen((_) => _afterSpeaking());

    // While this screen is open it owns "Hey Willy"; the home handler is restored on exit.
    MobileTelemetryService.setWakeWordOverride(_onWakeWord);
    _hubEvents = ApiService.eventsStream.listen((event) {
      if (event['type'] == 'late_reply') _onLateReply(event);
    });
    _hubStatus = ApiService.statusStream.listen((s) {
      if (mounted) setState(() => _hub = s);
    });

    _callTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && _callState != CallState.connecting) _callSeconds.value++;
    });

    if (widget.isMorningCall) {
      _startMorningBriefingCall();
    } else {
      // A short "ringing out" moment, then the line is open.
      _connectTimer = Timer(const Duration(milliseconds: 1100), _connected);
    }
  }

  @override
  void dispose() {
    MobileTelemetryService.setWakeWordOverride(null);
    MobileTelemetryService.resumeWakeWord();
    _hubEvents?.cancel();
    _hubStatus?.cancel();
    _callTimer?.cancel();
    _connectTimer?.cancel();
    _ampSub?.cancel();
    _closePanel(deferred: true);
    _audioRecorder.dispose();
    if (!_speakerOn) _audioPlayer.setAudioContext(AudioContext()).ignore(); // back to the normal audio mode
    _audioPlayer.dispose();
    _level.dispose();
    _callSeconds.dispose();
    _sheet.dispose();
    super.dispose();
  }

  // ------------------------------------------------------------ call state

  String get _idleHint {
    if (_muted) return "You're muted. Tap the mic to unmute.";
    return WillySettings.wakeWordEnabled ? "Tap Willy or say 'Hey Willy'" : 'Tap Willy to talk';
  }

  (String, Color) _lineFor(CallState s) => switch (s) {
        CallState.connecting => ('Connecting', WillyColors.amber),
        CallState.idle => _muted ? ('Muted', WillyColors.red) : ('Ready', WillyColors.cyan),
        CallState.listening => ('Listening', WillyColors.green),
        CallState.thinking => ('Thinking', WillyColors.purple),
        CallState.speaking => ('Speaking', WillyColors.sky),
      };

  /// Moves the call to [s] with a haptic tick when the state really changes.
  void _setCall(CallState s, String status) {
    if (!mounted) return;
    final changed = s != _callState;
    setState(() {
      _callState = s;
      _statusText = status;
      if (s != CallState.listening) _level.value = 0;
    });
    final (label, color) = _lineFor(s);
    _callLine.value = CallLine(label, color);
    if (!changed) return;
    switch (s) {
      case CallState.listening:
        HapticFeedback.mediumImpact();
        break;
      case CallState.thinking:
        HapticFeedback.selectionClick();
        break;
      case CallState.speaking:
        HapticFeedback.lightImpact();
        break;
      case CallState.idle:
      case CallState.connecting:
        break;
    }
  }

  void _connected() {
    if (!mounted || _callState != CallState.connecting) return;
    HapticFeedback.heavyImpact();
    if (_handsFree && !_muted) {
      _setCall(CallState.idle, 'Connected');
      _startRecording();
    } else {
      _setCall(CallState.idle, _idleHint);
    }
  }

  void _onWakeWord(String command, String trigger, String fullText) {
    if (!mounted || _muted) return;
    if (_callState == CallState.speaking) _audioPlayer.stop();
    if (command.trim().isNotEmpty) {
      _sendText(command.trim());
    } else if (_callState != CallState.listening) {
      _startRecording();
    }
  }

  Future<void> _startMorningBriefingCall() async {
    _setCall(CallState.thinking, 'Loading your morning briefing…');
    final briefing = await ApiService.getMorningBriefing(includeAudio: true);
    if (!mounted) return;
    if (briefing['success'] == true) {
      final script = (briefing['script'] ?? 'Good morning!').toString();
      setState(() {
        _turns.add(_VoiceTurn('☀️ Morning briefing', script));
        _caption = markdownToPlain(script);
      });
      final audio = briefing['audio_base64'] as String?;
      if (audio != null && audio.isNotEmpty) {
        _setCall(CallState.speaking, 'Willy is reading your briefing…');
        try {
          await _audioPlayer.play(BytesSource(Uint8List.fromList(base64Decode(audio))));
        } catch (_) {
          _backToIdle(_idleHint);
        }
      } else {
        _backToIdle(_idleHint);
      }
    } else {
      _backToIdle("Couldn't load the briefing. Ask me anything.");
    }
  }

  // ------------------------------------------------------------- recording

  Future<void> _startRecording() async {
    if (_callState == CallState.listening || _ending) return;
    if (_muted) {
      _setCall(CallState.idle, _idleHint);
      return;
    }
    try {
      if (!await _audioRecorder.hasPermission()) {
        _backToIdle('Willy needs the microphone. You can type instead.');
        _showTextInput();
        return;
      }
      await _audioPlayer.stop();
      // Take the mic from the "Hey Willy" listener while we record (it resumes afterwards).
      await MobileTelemetryService.pauseWakeWord();
      await Future.delayed(const Duration(milliseconds: 150));
      if (!mounted || _muted || _ending) {
        if (!_muted) MobileTelemetryService.resumeWakeWord();
        return;
      }
      final dir = await getTemporaryDirectory();
      final path = '${dir.path}/willy_input_${DateTime.now().millisecondsSinceEpoch}.m4a';
      try {
        // 16 kHz mono keeps uploads small (faster transcription).
        await _audioRecorder.start(
          const RecordConfig(encoder: AudioEncoder.aacLc, sampleRate: 16000, numChannels: 1, bitRate: 48000),
          path: path,
        );
      } catch (_) {
        await _audioRecorder.start(const RecordConfig(encoder: AudioEncoder.aacLc), path: path);
      }
      _heardSpeech = false;
      _speechHeard = Duration.zero;
      _lastAmpAt = null;
      _silenceSince = null;
      _stopping = false;
      _recordStart = DateTime.now();
      _setCall(CallState.listening, 'Go ahead. I stop when you pause.');
      _ampSub?.cancel();
      _ampSub = _audioRecorder.onAmplitudeChanged(const Duration(milliseconds: 120)).listen(_onAmplitude);
    } catch (_) {
      MobileTelemetryService.resumeWakeWord();
      _backToIdle("Couldn't open the microphone. You can type instead.");
    }
  }

  void _onAmplitude(Amplitude amp) {
    if (_callState != CallState.listening || _stopping) return;
    final now = DateTime.now();
    // Samples arrive every ~120 ms; don't let an odd gap count as a long stretch of speech.
    final sinceLast = _lastAmpAt == null ? const Duration(milliseconds: 120) : now.difference(_lastAmpAt!);
    _lastAmpAt = now;
    final db = amp.current;
    _level.value = ((db + 60) / 60).clamp(0.0, 1.0);
    if (db > _speechDb) {
      _heardSpeech = true;
      _silenceSince = null;
      _speechHeard += sinceLast > const Duration(milliseconds: 300) ? const Duration(milliseconds: 120) : sinceLast;
    } else if (_heardSpeech) {
      _silenceSince ??= now;
      final hold = _speechHeard < _earlySpeech ? _silenceHoldEarly : _silenceHold;
      if (now.difference(_silenceSince!) > hold) {
        _stopRecordingAndSend();
        return;
      }
    }
    final elapsed = now.difference(_recordStart ?? now);
    if (elapsed > _maxUtterance) {
      _stopRecordingAndSend();
    } else if (!_heardSpeech && elapsed > _noSpeechTimeout) {
      _cancelRecording("Didn't hear anything. Tap Willy to try again.");
    }
  }

  /// Stops the recorder and throws the half-said utterance away.
  Future<void> _discardRecording() async {
    await _ampSub?.cancel();
    try {
      final path = await _audioRecorder.stop();
      if (path != null) File(path).delete().ignore();
    } catch (_) {}
  }

  Future<void> _cancelRecording(String message) async {
    _stopping = true;
    await _discardRecording();
    if (!_muted) MobileTelemetryService.resumeWakeWord();
    if (!mounted) return;
    _setCall(CallState.idle, message);
  }

  Future<void> _stopRecordingAndSend() async {
    if (_stopping) return;
    _stopping = true;
    await _ampSub?.cancel();
    _setCall(CallState.thinking, 'Willy is thinking…');
    try {
      final path = await _audioRecorder.stop();
      MobileTelemetryService.resumeWakeWord();
      if (_speechHeard < _minSpeech) {
        if (path != null) File(path).delete().ignore();
        _backToIdle("Didn't catch that. Tap Willy to try again.");
        return;
      }
      final file = path == null ? null : File(path);
      if (file == null || !await file.exists()) {
        _backToIdle('Recording failed. Tap Willy to try again.');
        return;
      }
      final bytes = await file.readAsBytes();
      file.delete().ignore();
      if (bytes.length < 400) {
        _backToIdle('That was too short. Tap Willy to try again.');
        return;
      }
      final res = await ApiService.sendVoiceUtterance(bytes);
      if (!mounted || _ending) return;
      if (res['success'] == true || (res['reply'] ?? '').toString().isNotEmpty) {
        await _showReply((res['transcript'] ?? '').toString(), res);
      } else {
        _backToIdle((res['error'] ?? 'Voice processing failed.').toString());
      }
    } catch (e) {
      MobileTelemetryService.resumeWakeWord();
      _backToIdle('Voice error: $e');
    }
  }

  void _backToIdle(String status) => _setCall(CallState.idle, status);

  Future<void> _sendText(String query) async {
    _setCall(CallState.thinking, 'Willy is thinking…');
    final res = await ApiService.sendCommand(query: query, returnAudio: true, sessionId: 'voice_mobile');
    if (!mounted || _ending) return;
    await _showReply(query, res);
  }

  /// A command that timed out finished after all: add the answer and read it out if idle.
  void _onLateReply(Map<String, dynamic> event) {
    final id = event['request_id']?.toString();
    if (!mounted || id == null || !_awaitingLate.remove(id)) return;
    final raw = event['result'];
    final res = raw is Map ? Map<String, dynamic>.from(raw) : <String, dynamic>{};
    final reply = (res['reply'] ?? res['error'] ?? res['message'] ?? 'Done.').toString();
    final query = event['query']?.toString() ?? '';
    final panel = CallPanelRequest.fromReply(res);
    setState(() {
      _turns.add(_VoiceTurn(query.isEmpty ? '↪ earlier question' : '↪ $query', reply,
          success: res['success'] != false, panel: panel));
      _caption = markdownToPlain(reply);
    });
    if (panel != null) _openPanel(panel);
    if (_callState == CallState.idle && !_muted) MobileTelemetryService.speak(reply);
  }

  Future<void> _showReply(String youSaid, Map<String, dynamic> res) async {
    if (res['pending'] == true && res['request_id'] != null) _awaitingLate.add(res['request_id'].toString());
    final reply = (res['reply'] ?? res['error'] ?? 'Done.').toString();
    final panel = CallPanelRequest.fromReply(res);
    setState(() {
      _turns.add(_VoiceTurn(
        youSaid.isEmpty ? '…' : youSaid,
        reply,
        success: res['success'] != false,
        fastPath: res['fast_path'] == true,
        timings: res['timings'] is Map ? Map<String, dynamic>.from(res['timings']) : const {},
        panel: panel,
      ));
      _caption = markdownToPlain(reply);
    });
    if (panel != null) _openPanel(panel);
    final audio = res['audio_base64'];
    if (audio is String && audio.isNotEmpty) {
      try {
        _setCall(CallState.speaking, 'Tap Willy to interrupt');
        await _audioPlayer.play(BytesSource(Uint8List.fromList(base64Decode(audio))));
        return;
      } catch (_) {}
    }
    _afterSpeaking(delay: const Duration(milliseconds: 700));
  }

  /// Willy finished talking: listen again (hands-free) or wait for a tap.
  void _afterSpeaking({Duration delay = const Duration(milliseconds: 250)}) {
    if (!mounted || _ending) return;
    if (_callState == CallState.listening) return;
    if (_handsFree && !_muted) {
      _setCall(CallState.idle, 'Your turn…');
      Future.delayed(delay, () {
        if (mounted && !_ending && _callState == CallState.idle && _handsFree && !_muted) _startRecording();
      });
    } else {
      _backToIdle(_idleHint);
    }
  }

  Future<void> _handleBargeIn() async {
    await _audioPlayer.stop();
    _startRecording();
  }

  void _onAvatarTap() {
    switch (_callState) {
      case CallState.idle:
        if (_muted) {
          _toggleMute();
        } else {
          _startRecording();
        }
        break;
      case CallState.listening:
        _stopRecordingAndSend();
        break;
      case CallState.speaking:
        _handleBargeIn();
        break;
      case CallState.connecting:
        _connectTimer?.cancel();
        _connected();
        break;
      case CallState.thinking:
        break;
    }
  }

  // --------------------------------------------------------------- controls

  Future<void> _toggleMute() async {
    HapticFeedback.selectionClick();
    final muting = !_muted;
    setState(() => _muted = muting);
    if (muting) {
      final wasListening = _callState == CallState.listening;
      if (wasListening) _stopping = true;
      if (wasListening || _callState == CallState.idle) _setCall(CallState.idle, _idleHint);
      if (wasListening) await _discardRecording();
      // Willy can't hear you at all while muted, "Hey Willy" included.
      await MobileTelemetryService.pauseWakeWord();
    } else {
      await MobileTelemetryService.resumeWakeWord();
      if (_callState == CallState.idle) {
        _setCall(CallState.idle, _idleHint);
        if (_handsFree) _startRecording();
      }
    }
  }

  Future<void> _toggleSpeaker() async {
    HapticFeedback.selectionClick();
    setState(() => _speakerOn = !_speakerOn);
    final ctx = _speakerOn
        ? AudioContext()
        : AudioContext(
            android: const AudioContextAndroid(
              isSpeakerphoneOn: false,
              audioMode: AndroidAudioMode.inCommunication,
              contentType: AndroidContentType.speech,
              usageType: AndroidUsageType.voiceCommunication,
              audioFocus: AndroidAudioFocus.gain,
            ),
          );
    final speaking = _callState == CallState.speaking;
    Duration? at;
    try {
      if (speaking) at = await _audioPlayer.getCurrentPosition();
      await _audioPlayer.setAudioContext(ctx); // this stops the current playback
      if (speaking && mounted && _callState == CallState.speaking) {
        if (at != null) await _audioPlayer.seek(at);
        await _audioPlayer.resume();
      }
    } catch (_) {
      if (speaking) _afterSpeaking();
    }
    if (mounted) {
      showWillySnack(context, _speakerOn ? 'Speaker on' : 'Earpiece: hold the phone to your ear');
    }
  }

  void _toggleHandsFree() {
    HapticFeedback.selectionClick();
    setState(() => _handsFree = !_handsFree);
    WillySettings.callHandsFree = _handsFree;
    WillySettings.save();
    showWillySnack(
        context, _handsFree ? 'Auto-listen on: Willy listens after each answer' : 'Auto-listen off: tap Willy to talk');
    if (_handsFree && _callState == CallState.idle && !_muted) _startRecording();
  }

  void _toggleTranscript() {
    HapticFeedback.selectionClick();
    if (!_sheet.isAttached) return;
    final expanded = _sheet.size > 0.3;
    _sheet.animateTo(
      expanded ? _sheetMin : 0.9,
      duration: const Duration(milliseconds: 320),
      curve: Curves.easeOutCubic,
    );
  }

  double _sheetMin = 0.1;

  Future<void> _endCall() async {
    if (_ending) return;
    _ending = true;
    HapticFeedback.heavyImpact();
    _connectTimer?.cancel();
    await _audioPlayer.stop();
    if (_callState == CallState.listening) await _cancelRecording('Call ended');
    _closePanel();
    if (!widget.isMorningCall) ApiService.resetSession('voice_mobile');
    if (!mounted) return;
    if (widget.onEndCall != null) {
      widget.onEndCall!();
      return;
    }
    final nav = Navigator.of(context);
    if (nav.canPop()) {
      nav.pop();
      return;
    }
    // Nowhere to go back to: start a fresh call on the same screen.
    setState(() {
      _turns.clear();
      _caption = '';
      _callSeconds.value = 0;
      _ending = false;
    });
    _setCall(CallState.idle, 'Call ended. Tap Willy to start again.');
  }

  void _showTextInput() {
    final controller = TextEditingController();
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: WillyColors.card,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(22))),
      builder: (ctx) {
        void send() {
          final query = controller.text.trim();
          Navigator.pop(ctx);
          if (query.isNotEmpty) {
            if (_callState == CallState.listening) _cancelRecording('Typing…');
            if (_callState == CallState.speaking) _audioPlayer.stop();
            _sendText(query);
          }
        }

        return Padding(
          padding: EdgeInsets.fromLTRB(16, 14, 8, 12 + MediaQuery.of(ctx).viewInsets.bottom),
          child: SafeArea(
            top: false,
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: controller,
                    autofocus: true,
                    minLines: 1,
                    maxLines: 4,
                    textInputAction: TextInputAction.send,
                    style: const TextStyle(color: Colors.white),
                    decoration: const InputDecoration(hintText: "Type to Willy, e.g. open my Downloads folder"),
                    onSubmitted: (_) => send(),
                  ),
                ),
                const SizedBox(width: 4),
                IconButton.filled(onPressed: send, icon: const Icon(Icons.send_rounded)),
              ],
            ),
          ),
        );
      },
    );
  }

  // ----------------------------------------------------------------- panels

  void _openPanel(CallPanelRequest req) {
    if (!mounted || _ending) return;
    HapticFeedback.mediumImpact();
    _panelReq.value = req;
    if (_panelRoute != null) return; // the open sheet switches to the new panel
    final navigator = Navigator.of(context);
    final route = buildCallPanelRoute(context, request: _panelReq, call: _callLine);
    _panelRoute = route;
    navigator.push(route).whenComplete(() {
      if (identical(_panelRoute, route)) _panelRoute = null;
    });
  }

  /// Closes the panel sheet (when the call ends). [deferred] while this screen is being torn down.
  void _closePanel({bool deferred = false}) {
    final route = _panelRoute;
    _panelRoute = null;
    if (route == null) return;
    void remove() {
      if (route.isActive) route.navigator?.removeRoute(route);
    }

    if (deferred) {
      WidgetsBinding.instance.addPostFrameCallback((_) => remove());
    } else {
      remove();
    }
  }

  Future<void> _choosePanel() async {
    HapticFeedback.selectionClick();
    final kind = await showModalBottomSheet<CallPanelKind>(
      context: context,
      backgroundColor: WillyColors.card,
      showDragHandle: true,
      builder: (ctx) {
        Widget tile(CallPanelKind k, IconData icon, Color color, String title, String sub) => ListTile(
              leading: Container(
                padding: const EdgeInsets.all(9),
                decoration:
                    BoxDecoration(color: color.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(12)),
                child: Icon(icon, color: color),
              ),
              title: Text(title, style: const TextStyle(color: WillyColors.text, fontWeight: FontWeight.w600)),
              subtitle: Text(sub, style: const TextStyle(color: WillyColors.muted, fontSize: 12.5)),
              onTap: () => Navigator.pop(ctx, k),
            );
        return SafeArea(
          child: Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                tile(CallPanelKind.files, Icons.folder_rounded, WillyColors.amber, 'Files on your PC',
                    'Browse, open, move, send to phone'),
                tile(CallPanelKind.screenshot, Icons.screenshot_monitor_rounded, WillyColors.sky, "PC screen",
                    'See what is on the screen right now'),
                tile(CallPanelKind.camera, Icons.photo_camera_rounded, WillyColors.pink, 'Camera',
                    'Take a photo and send it to your PC'),
              ],
            ),
          ),
        );
      },
    );
    if (kind != null && mounted) _openPanel(CallPanelRequest(kind));
  }

  // --------------------------------------------------------------------- UI

  String _formatTimer(int totalSeconds) {
    final h = totalSeconds ~/ 3600;
    final mins = ((totalSeconds % 3600) ~/ 60).toString().padLeft(2, '0');
    final secs = (totalSeconds % 60).toString().padLeft(2, '0');
    return h > 0 ? '$h:$mins:$secs' : '$mins:$secs';
  }

  AvatarMode get _avatarMode => switch (_callState) {
        CallState.connecting => AvatarMode.connecting,
        CallState.listening => AvatarMode.listening,
        CallState.thinking => AvatarMode.thinking,
        CallState.speaking => AvatarMode.speaking,
        CallState.idle => _muted ? AvatarMode.muted : AvatarMode.idle,
      };

  @override
  Widget build(BuildContext context) {
    final (_, color) = _lineFor(_callState);
    return PopScope(
      // In the Voice tab, Back hangs up and returns to the previous tab.
      canPop: widget.onEndCall == null,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _endCall();
      },
      child: AnnotatedRegion<SystemUiOverlayStyle>(
        value: SystemUiOverlayStyle.light.copyWith(
          statusBarColor: Colors.transparent,
          systemNavigationBarColor: WillyColors.card,
        ),
        child: Scaffold(
          backgroundColor: const Color(0xFF03050C),
          resizeToAvoidBottomInset: false,
          body: LayoutBuilder(
            builder: (context, box) {
              final bottomInset = MediaQuery.of(context).padding.bottom;
              final peek = _peekHeight + bottomInset;
              _sheetMin = (peek / box.maxHeight).clamp(0.05, 0.5);
              return Stack(
                children: [
                  // Background glow tinted by the call state.
                  Positioned.fill(
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 700),
                      curve: Curves.easeOut,
                      decoration: BoxDecoration(
                        gradient: RadialGradient(
                          center: const Alignment(0, -0.35),
                          radius: 1.1,
                          colors: [color.withValues(alpha: 0.20), const Color(0xFF060913), const Color(0xFF03050C)],
                          stops: const [0, 0.55, 1],
                        ),
                      ),
                    ),
                  ),
                  SafeArea(
                    bottom: false,
                    child: Padding(
                      padding: EdgeInsets.only(bottom: peek),
                      child: _callBody(box.maxHeight - peek, color),
                    ),
                  ),
                  _transcriptSheet(),
                ],
              );
            },
          ),
        ),
      ),
    );
  }

  Widget _callBody(double height, Color color) {
    final compact = height < 600;
    final buttonSize = compact ? 54.0 : 62.0;
    return Column(
      children: [
        _topBar(),
        SizedBox(height: compact ? 2 : 6),
        Text(
          widget.isMorningCall ? 'Willy · Morning call' : 'Willy',
          style: TextStyle(
              color: WillyColors.text, fontSize: compact ? 24 : 28, fontWeight: FontWeight.w700, letterSpacing: 0.3),
        ),
        const SizedBox(height: 4),
        AnimatedSwitcher(
          duration: const Duration(milliseconds: 250),
          child: ValueListenableBuilder<int>(
            key: ValueKey(_callState == CallState.connecting),
            valueListenable: _callSeconds,
            builder: (context, secs, _) => Text(
              _callState == CallState.connecting ? 'Calling…' : _formatTimer(secs),
              style: const TextStyle(
                color: WillyColors.muted,
                fontSize: 15,
                fontFeatures: [FontFeature.tabularFigures()],
                letterSpacing: 0.5,
              ),
            ),
          ),
        ),
        Expanded(
          child: LayoutBuilder(
            builder: (context, box) {
              final size = math.min(math.min(box.maxHeight, box.maxWidth * 0.86), 320.0);
              if (size < 90) return const SizedBox.shrink();
              return Center(
                child: WillyAvatar(
                  mode: _avatarMode,
                  color: color,
                  level: _level,
                  size: size,
                  onTap: _onAvatarTap,
                  onLongPress: _showTextInput,
                ),
              );
            },
          ),
        ),
        _stateLine(color),
        if (!compact) _captionView(),
        SizedBox(height: compact ? 8 : 14),
        _controls(buttonSize),
        SizedBox(height: compact ? 10 : 18),
        EndCallButton(onPressed: _endCall, size: compact ? 62 : 70),
        SizedBox(height: compact ? 8 : 14),
      ],
    );
  }

  Widget _topBar() {
    final offline = _hub.state != HubState.online;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 6, 8, 0),
      child: Row(
        children: [
          Icon(Icons.lock_rounded, size: 13, color: WillyColors.faint.withValues(alpha: 0.9)),
          const SizedBox(width: 5),
          Flexible(
            child: Text(
              offline
                  ? (_hub.state == HubState.connecting ? 'Reaching the hub…' : 'Hub offline · replies may fail')
                  : 'Willy voice${_hub.latencyMs != null ? ' · ${_hub.latencyMs} ms' : ''}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(color: offline ? WillyColors.amber : WillyColors.faint, fontSize: 12),
            ),
          ),
          const Spacer(),
          if (!widget.isMorningCall)
            IconButton(
              tooltip: 'New conversation',
              visualDensity: VisualDensity.compact,
              onPressed: () {
                HapticFeedback.selectionClick();
                ApiService.resetSession('voice_mobile');
                setState(() {
                  _turns.clear();
                  _caption = '';
                });
                showWillySnack(context, 'Started a new conversation');
              },
              icon: const Icon(Icons.restart_alt_rounded, color: WillyColors.muted),
            ),
        ],
      ),
    );
  }

  Widget _stateLine(Color color) {
    final (label, _) = _lineFor(_callState);
    final Widget indicator = switch (_callState) {
      CallState.listening => _LevelBars(level: _level, color: color),
      CallState.thinking || CallState.connecting => TypingDots(color: color),
      CallState.speaking => Icon(Icons.graphic_eq_rounded, color: color, size: 18),
      CallState.idle => Icon(_muted ? Icons.mic_off_rounded : Icons.touch_app_rounded, color: color, size: 16),
    };
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 24),
      child: Column(
        children: [
          AnimatedSwitcher(
            duration: const Duration(milliseconds: 260),
            transitionBuilder: (child, anim) => FadeTransition(
              opacity: anim,
              child: SlideTransition(
                position: Tween(begin: const Offset(0, 0.3), end: Offset.zero).animate(anim),
                child: child,
              ),
            ),
            child: Row(
              key: ValueKey(label),
              mainAxisSize: MainAxisSize.min,
              children: [
                SizedBox(height: 22, child: Center(child: indicator)),
                const SizedBox(width: 10),
                Text(label,
                    style: TextStyle(color: color, fontSize: 19, fontWeight: FontWeight.w700, letterSpacing: 0.3)),
              ],
            ),
          ),
          const SizedBox(height: 4),
          AnimatedSwitcher(
            duration: const Duration(milliseconds: 200),
            child: Text(
              _statusText,
              key: ValueKey(_statusText),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: const TextStyle(color: WillyColors.muted, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }

  Widget _captionView() {
    final show = _caption.isNotEmpty && (_callState == CallState.speaking || _callState == CallState.idle);
    return AnimatedOpacity(
      duration: const Duration(milliseconds: 300),
      opacity: show ? 1 : 0,
      child: GestureDetector(
        onTap: show ? _toggleTranscript : null,
        child: Container(
          height: 44,
          margin: const EdgeInsets.fromLTRB(32, 8, 32, 0),
          alignment: Alignment.center,
          child: Text(
            _caption.replaceAll('\n', '  '),
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            textAlign: TextAlign.center,
            style: const TextStyle(color: WillyColors.textSoft, fontSize: 13.5, height: 1.3),
          ),
        ),
      ),
    );
  }

  Widget _controls(double size) {
    Widget cell(Widget child) => Expanded(child: Center(child: child));
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 22),
      child: Column(
        children: [
          Row(
            children: [
              cell(CallControlButton(
                icon: _muted ? Icons.mic_off_rounded : Icons.mic_none_rounded,
                label: _muted ? 'Unmute' : 'Mute',
                active: _muted,
                size: size,
                onPressed: _toggleMute,
              )),
              cell(CallControlButton(
                icon: _speakerOn ? Icons.volume_up_rounded : Icons.phone_in_talk_rounded,
                label: _speakerOn ? 'Speaker' : 'Earpiece',
                active: _speakerOn,
                size: size,
                onPressed: _toggleSpeaker,
              )),
              cell(CallControlButton(
                icon: Icons.keyboard_rounded,
                label: 'Keyboard',
                size: size,
                onPressed: _showTextInput,
              )),
            ],
          ),
          const SizedBox(height: 14),
          Row(
            children: [
              cell(CallControlButton(
                icon: Icons.subject_rounded,
                label: 'Transcript',
                size: size,
                badge: _turns.length,
                onPressed: _toggleTranscript,
              )),
              cell(CallControlButton(
                icon: Icons.dashboard_customize_rounded,
                label: 'Panels',
                size: size,
                onPressed: _choosePanel,
              )),
              cell(CallControlButton(
                icon: Icons.all_inclusive_rounded,
                label: 'Auto-listen',
                active: _handsFree,
                activeColor: WillyColors.green,
                size: size,
                onPressed: _toggleHandsFree,
              )),
            ],
          ),
        ],
      ),
    );
  }

  Widget _transcriptSheet() {
    return DraggableScrollableSheet(
      controller: _sheet,
      initialChildSize: _sheetMin,
      minChildSize: _sheetMin,
      maxChildSize: 0.9,
      snap: true,
      builder: (context, scroll) {
        final bottomInset = MediaQuery.of(context).padding.bottom;
        final latest = _turns.isEmpty ? null : _turns.last;
        return Container(
          decoration: BoxDecoration(
            color: WillyColors.card.withValues(alpha: 0.97),
            borderRadius: const BorderRadius.vertical(top: Radius.circular(22)),
            border: const Border(top: BorderSide(color: WillyColors.borderStrong)),
            boxShadow: const [BoxShadow(color: Color(0x88000000), blurRadius: 24, offset: Offset(0, -4))],
          ),
          child: CustomScrollView(
            controller: scroll,
            slivers: [
              SliverToBoxAdapter(
                child: GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: _toggleTranscript,
                  child: SizedBox(
                    height: _peekHeight,
                    child: Column(
                      children: [
                        const SizedBox(height: 8),
                        Container(
                          width: 38,
                          height: 4,
                          decoration:
                              BoxDecoration(color: WillyColors.borderStrong, borderRadius: BorderRadius.circular(2)),
                        ),
                        const SizedBox(height: 10),
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 18),
                          child: Row(
                            children: [
                              const Icon(Icons.subject_rounded, size: 18, color: WillyColors.cyan),
                              const SizedBox(width: 8),
                              Text(
                                _turns.isEmpty ? 'Transcript' : 'Transcript · ${_turns.length}',
                                style:
                                    const TextStyle(color: WillyColors.text, fontWeight: FontWeight.w700, fontSize: 14),
                              ),
                              const SizedBox(width: 10),
                              Expanded(
                                child: Text(
                                  latest == null
                                      ? 'Swipe up to see the conversation'
                                      : markdownToPlain(latest.willy).replaceAll('\n', ' '),
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(color: WillyColors.faint, fontSize: 12.5),
                                ),
                              ),
                              const Icon(Icons.keyboard_arrow_up_rounded, color: WillyColors.faint),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
              if (_turns.isEmpty)
                const SliverToBoxAdapter(
                  child: Padding(
                    padding: EdgeInsets.fromLTRB(24, 24, 24, 24),
                    child: Text(
                      'Try: "What\'s my laptop battery?", "Show me my Downloads folder" or "Take a screenshot of my PC".\n'
                      'Long-press Willy to type instead.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: WillyColors.faint, fontSize: 13, height: 1.4),
                    ),
                  ),
                )
              else
                // Newest first, so swiping up shows the latest answer straight away.
                SliverPadding(
                  padding: const EdgeInsets.fromLTRB(14, 4, 14, 12),
                  sliver: SliverList.builder(
                    itemCount: _turns.length,
                    itemBuilder: (context, i) => _turnView(_turns[_turns.length - 1 - i]),
                  ),
                ),
              SliverToBoxAdapter(child: SizedBox(height: bottomInset + 12)),
            ],
          ),
        );
      },
    );
  }

  Widget _turnView(_VoiceTurn t) {
    final timings = t.timings;
    final parts = [
      if (timings['stt_ms'] != null) 'hear ${timings['stt_ms']}',
      if (timings['llm_ms'] != null) 'think ${timings['llm_ms']}',
      if (timings['tool_ms'] != null) 'PC ${timings['tool_ms']}',
      if (timings['tts_ms'] != null) 'voice ${timings['tts_ms']}',
    ];
    final panel = t.panel;
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Align(
            alignment: Alignment.centerRight,
            child: Container(
              constraints: const BoxConstraints(maxWidth: 300),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              decoration: BoxDecoration(
                color: WillyColors.cyan.withValues(alpha: 0.12),
                borderRadius: const BorderRadius.only(
                  topLeft: Radius.circular(14),
                  topRight: Radius.circular(14),
                  bottomLeft: Radius.circular(14),
                  bottomRight: Radius.circular(4),
                ),
              ),
              child: Text(t.you, style: const TextStyle(color: WillyColors.text, fontSize: 14)),
            ),
          ),
          const SizedBox(height: 8),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 26,
                height: 26,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: LinearGradient(
                    colors: t.success
                        ? const [WillyColors.cyan, WillyColors.violet]
                        : const [WillyColors.red, WillyColors.pink],
                  ),
                ),
                child:
                    const Text('W', style: TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w900)),
              ),
              const SizedBox(width: 10),
              Expanded(child: SimpleMarkdown(t.willy)),
            ],
          ),
          if (panel != null || parts.isNotEmpty || t.fastPath)
            Padding(
              padding: const EdgeInsets.only(left: 36, top: 6),
              child: Wrap(
                spacing: 8,
                runSpacing: 6,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  if (panel != null)
                    ActionChip(
                      visualDensity: VisualDensity.compact,
                      avatar: Icon(_panelIcon(panel.kind), size: 16, color: WillyColors.cyan),
                      label: Text(_panelLabel(panel), style: const TextStyle(fontSize: 12)),
                      backgroundColor: WillyColors.bgElevated,
                      side: const BorderSide(color: WillyColors.border),
                      onPressed: () => _openPanel(
                          CallPanelRequest(panel.kind, deviceId: panel.deviceId, path: panel.path, title: panel.title)),
                    ),
                  if (t.fastPath) const StatusPill(text: '⚡', color: WillyColors.amber, dot: false),
                  if (parts.isNotEmpty)
                    Text('${parts.join(' · ')} ms', style: const TextStyle(color: WillyColors.faint, fontSize: 11)),
                ],
              ),
            ),
        ],
      ),
    );
  }

  IconData _panelIcon(CallPanelKind k) => switch (k) {
        CallPanelKind.files => Icons.folder_rounded,
        CallPanelKind.screenshot => Icons.screenshot_monitor_rounded,
        CallPanelKind.camera => Icons.photo_camera_rounded,
      };

  String _panelLabel(CallPanelRequest p) {
    if (p.title != null) return p.title!;
    return switch (p.kind) {
      CallPanelKind.files => p.path != null ? 'Open ${baseName(p.path!)}' : 'Open files',
      CallPanelKind.screenshot => 'Show PC screen',
      CallPanelKind.camera => 'Open camera',
    };
  }
}

/// Five little bars that follow the microphone level ("Listening").
class _LevelBars extends StatelessWidget {
  final ValueListenable<double> level;
  final Color color;

  const _LevelBars({required this.level, required this.color});

  static const _shape = [0.55, 0.85, 1.0, 0.8, 0.5];

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<double>(
      valueListenable: level,
      builder: (context, v, _) => Row(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          for (final s in _shape)
            AnimatedContainer(
              duration: const Duration(milliseconds: 110),
              margin: const EdgeInsets.symmetric(horizontal: 1.5),
              width: 3.5,
              height: 4 + 16 * s * v,
              decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(2)),
            ),
        ],
      ),
    );
  }
}
