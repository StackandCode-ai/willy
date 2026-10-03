package com.example.willy_mobile

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log
import androidx.core.app.NotificationCompat
import java.util.Locale
import kotlin.math.max
import kotlin.math.sqrt

/**
 * "Hey Willy" listener.
 *
 * Android's SpeechRecognizer is a one-shot recognizer: restarting it in a loop every few
 * seconds made Google's speech service grab exclusive audio focus (pausing music), play its
 * listening chime and drain the battery all day. Instead this service runs a local voice
 * gate: it reads microphone *levels* on-device (no network, no audio focus, no chime) and
 * only hands the mic to SpeechRecognizer when someone is actually speaking.
 */
class WillyWakeWordService : Service() {
    private val TAG = "WillyWakeService"
    private val CHANNEL_ID = "willy_wake_service_channel"
    private val NOTIF_ID = 9021

    private val sampleRate = 16000
    private val frameMs = 100
    private val minSpeechRms = 700.0      // absolute floor for "someone is talking" (16-bit PCM)
    private val speechOverNoise = 3.0     // ...and at least this many times the ambient level
    private val speechFramesToTrigger = 2 // ~200 ms of speech-level sound starts recognition

    private var speechRecognizer: SpeechRecognizer? = null
    @Volatile private var isListening = false
    @Volatile private var gateRunning = false
    @Volatile private var recognizerActive = false
    private var gateThread: Thread? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private var wakeLock: PowerManager.WakeLock? = null
    private var lastTriggerTime = 0L
    private var noiseFloor = 250.0

    private val wakeWordRegex = Regex("(?i)\\b(hey\\s*willy|hi\\s*willy|hello\\s*willy|ok\\s*willy|willy|villy|wili|willie|hey\\s*villy|a\\s*willy|ey\\s*willy)\\b")

    companion object {
        var onWakeWordCallback: ((String, String, String) -> Unit)? = null

        /** True while the app records audio itself (voice tab), so the mic is released. */
        @Volatile var paused: Boolean = false
            private set
        @Volatile private var instance: WillyWakeWordService? = null

        fun pauseListening(value: Boolean) {
            paused = value
            val service = instance ?: return
            if (value) service.mainHandler.post { service.cancelRecognizer() }
        }
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        createNotificationChannel()
        acquireWakeLock()
    }

    /** Frees the mic right away when the app needs it; the gate idles until resumed. */
    private fun cancelRecognizer() {
        if (!recognizerActive) return
        try { speechRecognizer?.cancel() } catch (_: Exception) {}
        endRecognizerSession(300L)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val notification = createNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID, notification, android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(NOTIF_ID, notification)
        }
        isListening = true
        startVoiceGate()
        return START_STICKY
    }

    private fun acquireWakeLock() {
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "willy:wakeword_service_lock")
            wakeLock?.acquire(2 * 60 * 60 * 1000L) // 2 hours
        } catch (e: Exception) {
            Log.e(TAG, "WakeLock error: ${e.message}")
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Willy Wake Word Listener",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Keeps Willy voice assistant active and listening for 'Hey Willy'"
                setShowBadge(false)
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }
    }

    private fun createNotification(): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Willy Voice Assistant Active")
            .setContentText("Listening for 'Hey Willy'...")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    // ------------------------------------------------------------------ voice gate

    private fun startVoiceGate() {
        if (gateRunning || !isListening) return
        gateRunning = true
        gateThread = Thread({ runVoiceGate() }, "willy-voice-gate").also { it.start() }
    }

    /** Blocks until speech is heard (then starts recognition) or the service stops. */
    private fun runVoiceGate() {
        val audioManager = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val frame = ShortArray(sampleRate * frameMs / 1000)
        var record: AudioRecord? = null
        var loudFrames = 0
        var heardSpeech = false
        try {
            while (isListening && !heardSpeech) {
                // Release the mic while the app records or media plays (don't fight either).
                if (paused || audioManager.isMusicActive) {
                    record?.let { releaseQuietly(it) }
                    record = null
                    loudFrames = 0
                    Thread.sleep(400)
                    continue
                }
                val rec = record ?: openRecorder()
                if (rec == null) {
                    Log.w(TAG, "Mic unavailable for the voice gate; retrying shortly")
                    Thread.sleep(2000)
                    continue
                }
                record = rec
                val n = rec.read(frame, 0, frame.size)
                if (n <= 0) continue
                var sum = 0.0
                for (i in 0 until n) {
                    val s = frame[i].toDouble()
                    sum += s * s
                }
                val rms = sqrt(sum / n)
                val threshold = max(minSpeechRms, noiseFloor * speechOverNoise)
                if (rms > threshold) {
                    loudFrames++
                    if (loudFrames >= speechFramesToTrigger) heardSpeech = true
                } else {
                    loudFrames = 0
                    // Track the room's ambient level so steady noise (fans, traffic) doesn't trigger.
                    noiseFloor = noiseFloor * 0.97 + rms * 0.03
                }
            }
        } catch (_: InterruptedException) {
        } catch (e: Exception) {
            Log.e(TAG, "Voice gate error: ${e.message}")
        } finally {
            record?.let { releaseQuietly(it) }
            gateRunning = false
        }
        if (heardSpeech && isListening && !paused) {
            Log.d(TAG, "Voice gate: speech detected (noise floor ${noiseFloor.toInt()}), starting recognizer")
            mainHandler.post { startRecognizerSession() }
        } else if (isListening) {
            mainHandler.postDelayed({ startVoiceGate() }, 1000)
        }
    }

    private fun openRecorder(): AudioRecord? {
        return try {
            val minBuf = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            val rec = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                max(minBuf, sampleRate) // bytes: ~0.5 s of 16-bit mono
            )
            if (rec.state != AudioRecord.STATE_INITIALIZED) {
                rec.release()
                null
            } else {
                rec.startRecording()
                rec
            }
        } catch (e: SecurityException) {
            Log.w(TAG, "RECORD_AUDIO not granted: ${e.message}")
            null
        } catch (e: Exception) {
            Log.w(TAG, "AudioRecord init failed: ${e.message}")
            null
        }
    }

    private fun releaseQuietly(rec: AudioRecord) {
        try { rec.stop() } catch (_: Exception) {}
        try { rec.release() } catch (_: Exception) {}
    }

    // ------------------------------------------------------------- speech recognizer

    private var recognitionIntent: Intent? = null

    private fun getSpeechIntent(): Intent {
        if (recognitionIntent == null) {
            recognitionIntent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag())
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, Locale.getDefault().toLanguageTag())
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5)
                // Let a whole "Hey Willy, open YouTube" through before it ends the session.
                putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1200L)
                putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 1000L)
            }
        }
        return recognitionIntent!!
    }

    // Noise without words can keep a session (and its audio focus) open for 10+ s;
    // a full "Hey Willy, open YouTube" needs ~5 s, so cap sessions.
    private val sessionWatchdog = Runnable {
        if (recognizerActive) {
            Log.d(TAG, "Service: recognizer session capped")
            try { speechRecognizer?.cancel() } catch (_: Exception) {}
            endRecognizerSession(250L)
        }
    }

    /** One recognition pass for the speech the gate just heard; then back to the gate. */
    private fun startRecognizerSession() {
        if (!isListening || paused) {
            startVoiceGate()
            return
        }
        try {
            if (speechRecognizer == null) {
                if (!SpeechRecognizer.isRecognitionAvailable(this)) {
                    Log.w(TAG, "SpeechRecognizer not available")
                    startVoiceGate()
                    return
                }
                speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this)
                speechRecognizer?.setRecognitionListener(createListener())
            }
            recognizerActive = true
            speechRecognizer?.cancel()
            speechRecognizer?.startListening(getSpeechIntent())
            mainHandler.removeCallbacks(sessionWatchdog)
            mainHandler.postDelayed(sessionWatchdog, 8000L)
            Log.d(TAG, "Service: recognizer session started")
        } catch (e: Exception) {
            Log.w(TAG, "Recognizer start failed, recreating: ${e.message}")
            try { speechRecognizer?.destroy() } catch (_: Exception) {}
            speechRecognizer = null
            recognizerActive = false
            mainHandler.postDelayed({ startVoiceGate() }, 800)
        }
    }

    private fun endRecognizerSession(delayMs: Long) {
        mainHandler.removeCallbacks(sessionWatchdog)
        recognizerActive = false
        if (isListening) mainHandler.postDelayed({ startVoiceGate() }, delayMs)
    }

    private fun createListener() = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {
            Log.d(TAG, "Service: onReadyForSpeech")
        }
        override fun onBeginningOfSpeech() {
            Log.d(TAG, "Service: onBeginningOfSpeech")
        }
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEndOfSpeech() {
            Log.d(TAG, "Service: onEndOfSpeech")
        }
        override fun onError(error: Int) {
            Log.d(TAG, "Service: recognizer ended (code $error)")
            val busy = error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY || error == SpeechRecognizer.ERROR_CLIENT
            endRecognizerSession(if (busy) 800L else 250L)
        }
        override fun onResults(results: Bundle?) {
            handleResults(results, isFinal = true)
            endRecognizerSession(250L)
        }
        override fun onPartialResults(partialResults: Bundle?) {
            handleResults(partialResults, isFinal = false)
        }
        override fun onEvent(eventType: Int, params: Bundle?) {}
    }

    private fun handleResults(bundle: Bundle?, isFinal: Boolean) {
        // Only final transcripts: a partial "Hey Willy open" would send a cut-off command.
        if (!isFinal) return
        val matches = bundle?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION) ?: return
        val now = System.currentTimeMillis()

        for (candidate in matches) {
            val trimmed = candidate.trim()
            val match = wakeWordRegex.find(trimmed)
            if (match != null) {
                val command = trimmed.substring(match.range.last + 1).trim()
                if (now - lastTriggerTime < 2500) return
                lastTriggerTime = now

                Log.i(TAG, "Service: WAKE WORD DETECTED: '${match.value}' in '$trimmed'")

                // 1. Wake the screen!
                wakeScreen()

                // 2. Bring Willy app to front via Full Screen Intent & Activity start!
                notifyWakeUpFullScreen()
                bringAppToFront()

                // 3. Haptic feedback
                vibratePhone()

                onWakeWordCallback?.invoke(command, match.value, trimmed)
                break
            }
        }
    }

    private fun wakeScreen() {
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            @Suppress("DEPRECATION")
            val wl = pm.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP or PowerManager.ON_AFTER_RELEASE,
                "willy:service_screen_wake"
            )
            wl.acquire(6000)
        } catch (e: Exception) {
            Log.e(TAG, "wakeScreen error: ${e.message}")
        }
    }

    private fun notifyWakeUpFullScreen() {
        try {
            val wakeChannelId = "willy_wake_alert_channel"
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val channel = NotificationChannel(
                    wakeChannelId,
                    "Willy Wake Up Alert",
                    NotificationManager.IMPORTANCE_HIGH
                ).apply {
                    description = "Alerts and wakes up phone when 'Hey Willy' is spoken"
                    lockscreenVisibility = Notification.VISIBILITY_PUBLIC
                    enableVibration(true)
                }
                nm.createNotificationChannel(channel)
            }

            val fullScreenIntent = Intent(applicationContext, MainActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            }
            val fullScreenPendingIntent = PendingIntent.getActivity(
                applicationContext,
                101,
                fullScreenIntent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            )

            val wakeNotification = NotificationCompat.Builder(this, wakeChannelId)
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setContentTitle("Willy: I'm listening!")
                .setContentText("Voice command active")
                .setPriority(NotificationCompat.PRIORITY_MAX)
                .setCategory(NotificationCompat.CATEGORY_ALARM)
                .setFullScreenIntent(fullScreenPendingIntent, true)
                .setAutoCancel(true)
                .build()

            nm.notify(9022, wakeNotification)
            mainHandler.postDelayed({ nm.cancel(9022) }, 3000)
        } catch (e: Exception) {
            Log.e(TAG, "notifyWakeUpFullScreen error: ${e.message}")
        }
    }

    private fun bringAppToFront() {
        try {
            val intent = Intent(applicationContext, MainActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            }
            startActivity(intent)
        } catch (e: Exception) {
            Log.e(TAG, "bringAppToFront error: ${e.message}")
        }
    }

    private fun vibratePhone() {
        try {
            val vibrator = getSystemService(Context.VIBRATOR_SERVICE) as? android.os.Vibrator
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vibrator?.vibrate(android.os.VibrationEffect.createOneShot(180, android.os.VibrationEffect.DEFAULT_AMPLITUDE))
            } else {
                @Suppress("DEPRECATION")
                vibrator?.vibrate(180)
            }
        } catch (_: Exception) {}
    }

    override fun onDestroy() {
        isListening = false
        if (instance === this) instance = null
        gateThread?.interrupt()
        mainHandler.removeCallbacksAndMessages(null)
        try {
            speechRecognizer?.stopListening()
            speechRecognizer?.cancel()
            speechRecognizer?.destroy()
            speechRecognizer = null
        } catch (_: Exception) {}
        try {
            if (wakeLock?.isHeld == true) wakeLock?.release()
        } catch (_: Exception) {}
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
