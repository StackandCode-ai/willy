package com.example.willy_mobile

import android.Manifest
import android.app.ActivityManager
import android.app.KeyguardManager
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.database.Cursor
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.StatFs
import android.provider.CallLog
import android.provider.Settings
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.util.Log
import android.view.WindowManager
import androidx.annotation.NonNull
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.util.*
import java.util.concurrent.Executors

class MainActivity : FlutterActivity() {
    companion object {
        /** True while Willy is resumed on screen: Android then lets it start other apps directly. */
        @Volatile var inFront: Boolean = false
            private set

        // Phone skills can block (contacts query, waiting for the SMS status), so they run off the UI thread.
        private val phoneExecutor = Executors.newCachedThreadPool()
        private const val PHONE_PERMISSIONS_REQUEST = 4310
        private const val PHONE_PERMISSIONS_PREFS = "willy_phone_skills"
        private const val PICK_FILES_REQUEST = 4320
    }

    private val TAG = "WillyWake"
    private val CHANNEL = "com.example.willy_mobile/telemetry"
    private var methodChannel: MethodChannel? = null
    private val phoneActions by lazy { PhoneActions(this) { inFront } }
    private var pendingPermissionResult: MethodChannel.Result? = null

    // Files between the phone and the PC. Progress goes to Dart on the UI thread.
    private val fileTransfer by lazy {
        FileTransfer(this) { id, done, total ->
            mainHandler.post {
                methodChannel?.invokeMethod("onTransferProgress", mapOf("id" to id, "done" to done, "total" to total))
            }
        }
    }
    private var pendingPickResult: MethodChannel.Result? = null
    private val sharedFiles = mutableListOf<Map<String, Any?>>() // shared to Willy, not yet taken by Dart
    private var sharedText: String? = null

    private var speechRecognizer: SpeechRecognizer? = null
    private var isWakeWordActive = false
    private val mainHandler = Handler(Looper.getMainLooper())
    private var lastWakeTriggerTime = 0L
    private var cpuWakeLock: PowerManager.WakeLock? = null

    private val wakeWordRegex = Regex("(?i)\\b(hey\\s*willy|hi\\s*willy|hello\\s*willy|ok\\s*willy|willy|villy|wili|willie|wake\\s*up|hey\\s*villy|a\\s*willy|ey\\s*willy)\\b")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        configureScreenWakeFlags()
        WillyAlerts.ensureChannel(this) // push notifications in the background use it too
        if (savedInstanceState == null) takeShareIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        takeShareIntent(intent)
    }

    /** Files (or text) handed over by [ShareReceiverActivity]; Dart takes them with "takeSharedFiles". */
    private fun takeShareIntent(source: Intent?) {
        if (source?.action != ShareReceiverActivity.ACTION_SHARE_TO_PC) return
        try {
            @Suppress("DEPRECATION")
            val uris: List<Uri> = if (Build.VERSION.SDK_INT >= 33) {
                source.getParcelableArrayListExtra(ShareReceiverActivity.EXTRA_URIS, Uri::class.java)
            } else {
                source.getParcelableArrayListExtra(ShareReceiverActivity.EXTRA_URIS)
            } ?: emptyList()
            uris.forEach { sharedFiles.add(fileTransfer.describe(it)) }
            source.getStringExtra(ShareReceiverActivity.EXTRA_TEXT)?.let { sharedText = it }
        } catch (e: Exception) {
            Log.w(TAG, "shared files unreadable: ${e.message}")
        }
        // Handled: a later recreation of this screen must not send them again.
        setIntent(Intent(this, MainActivity::class.java).setAction(Intent.ACTION_MAIN))
        methodChannel?.invokeMethod("onSharedFiles", null)
    }

    private fun pickFiles(result: MethodChannel.Result) {
        pendingPickResult?.success(emptyList<Any>())
        pendingPickResult = result
        try {
            val pick = Intent(Intent.ACTION_OPEN_DOCUMENT)
                .addCategory(Intent.CATEGORY_OPENABLE)
                .setType("*/*")
                .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
            @Suppress("DEPRECATION")
            startActivityForResult(pick, PICK_FILES_REQUEST)
        } catch (e: Exception) {
            pendingPickResult = null
            result.error("PICK_ERROR", "There's no file picker on this phone.", null)
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != PICK_FILES_REQUEST) return
        val pending = pendingPickResult ?: return
        pendingPickResult = null
        val uris = mutableListOf<Uri>()
        if (resultCode == RESULT_OK && data != null) {
            val clip = data.clipData
            if (clip != null) {
                for (i in 0 until clip.itemCount) clip.getItemAt(i).uri?.let { uris.add(it) }
            } else {
                data.data?.let { uris.add(it) }
            }
        }
        val files = uris.distinct()
        phoneExecutor.execute {
            val described = files.map { fileTransfer.describe(it) }
            mainHandler.post {
                try {
                    pending.success(described)
                } catch (e: Exception) {
                    Log.w(TAG, "pick reply failed: ${e.message}")
                }
            }
        }
    }

    /** Runs [work] off the UI thread and replies with its map (file transfers block). */
    private fun replyInBackground(result: MethodChannel.Result, label: String, work: () -> Map<String, Any?>) {
        phoneExecutor.execute {
            val res: Map<String, Any?> = try {
                work()
            } catch (t: Throwable) {
                Log.e(TAG, "$label crashed", t)
                mapOf("success" to false, "status" to -1, "error" to "That didn't work on the phone.")
            }
            mainHandler.post {
                try {
                    result.success(res)
                } catch (e: Exception) {
                    Log.w(TAG, "$label reply failed: ${e.message}")
                }
            }
        }
    }

    private fun MethodCall.longArg(key: String): Long = (argument<Any?>(key) as? Number)?.toLong() ?: -1L

    override fun onResume() {
        super.onResume()
        inFront = true
        // Access granted but the listener isn't bound (e.g. after an app update): ask Android to bind it.
        if (!WillyNotificationListenerService.connected && PhoneActions.notificationAccessGranted(this)) {
            PhoneActions.requestListenerRebind(this)
        }
    }

    override fun onPause() {
        inFront = false
        super.onPause()
    }

    private fun configureScreenWakeFlags() {
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD or
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
            )
        }
    }

    override fun configureFlutterEngine(@NonNull flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        methodChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
        methodChannel?.setMethodCallHandler { call, result ->
            when (call.method) {
                "getMobileTelemetry" -> {
                    try {
                        val missedCalls = getRecentMissedCalls()
                        val telemetry = mapOf(
                            "unread_emails_count" to WillyNotificationListenerService.unreadEmailsCount,
                            "email_senders" to WillyNotificationListenerService.emailSenders,
                            "missed_calls_count" to missedCalls.size,
                            "missed_calls" to missedCalls,
                            "whatsapp_unread_count" to WillyNotificationListenerService.whatsappUnreadCount,
                            "whatsapp_senders" to WillyNotificationListenerService.whatsappSenders.map { mapOf("name" to it) },
                            "total_notifications_count" to WillyNotificationListenerService.totalNotificationsCount,
                            "other_notifications" to WillyNotificationListenerService.otherNotifications
                        )
                        result.success(telemetry)
                    } catch (e: Exception) {
                        result.error("TELEMETRY_ERROR", e.message, null)
                    }
                }
                "hasNotificationPermission" -> {
                    val enabled = isNotificationServiceEnabled()
                    result.success(enabled)
                }
                "ringPhone" -> {
                    try {
                        val secs = (call.argument<Int>("durationSec") ?: 30).coerceIn(3, 120)
                        startRingingAlarm()
                        // Never ring forever if nobody taps Stop.
                        mainHandler.removeCallbacks(stopRingRunnable)
                        mainHandler.postDelayed(stopRingRunnable, secs * 1000L)
                        result.success(true)
                    } catch (e: Exception) {
                        result.error("RING_ERROR", e.message, null)
                    }
                }
                "stopRingPhone" -> {
                    mainHandler.removeCallbacks(stopRingRunnable)
                    stopRingingAlarm()
                    result.success(true)
                }
                "isRinging" -> result.success(currentRingtone?.isPlaying == true)
                "getDeviceStatus" -> {
                    try {
                        result.success(getDeviceStatus())
                    } catch (e: Exception) {
                        result.error("STATUS_ERROR", e.message, null)
                    }
                }
                "openUrl" -> result.success(openUrl(call.argument<String>("url") ?: ""))
                "setTorch" -> result.success(setTorch(call.argument<Boolean>("on") ?: false))
                "vibrate" -> {
                    vibrateOnce((call.argument<Int>("ms") ?: 400).toLong())
                    result.success(true)
                }
                "speak" -> {
                    speak(call.argument<String>("text") ?: "")
                    result.success(true)
                }
                "stopSpeaking" -> {
                    tts?.stop()
                    result.success(true)
                }
                "showNotification" -> {
                    showAlertNotification(
                        call.argument<String>("title") ?: "Willy",
                        call.argument<String>("text") ?: ""
                    )
                    result.success(true)
                }
                "requestNotificationPermission" -> {
                    requestNotificationPermission()
                    result.success(true)
                }
                "startWakeWordDetection" -> {
                    try {
                        startWakeWordListening()
                        result.success(true)
                    } catch (e: Exception) {
                        result.error("WAKE_WORD_ERROR", e.message, null)
                    }
                }
                "stopWakeWordDetection" -> {
                    stopWakeWordListening()
                    result.success(true)
                }
                "pauseWakeWord" -> {
                    WillyWakeWordService.pauseListening(true)
                    result.success(true)
                }
                "resumeWakeWord" -> {
                    WillyWakeWordService.pauseListening(false)
                    result.success(true)
                }
                "isWakeWordSupported" -> {
                    val available = SpeechRecognizer.isRecognitionAvailable(this)
                    Log.i(TAG, "isRecognitionAvailable: $available")
                    result.success(available)
                }
                "phoneAction" -> {
                    val action = call.argument<String>("action") ?: ""
                    val payload: Map<String, Any?> = try {
                        call.argument<Map<String, Any?>>("payload") ?: emptyMap()
                    } catch (_: Exception) {
                        emptyMap()
                    }
                    val actions = phoneActions
                    phoneExecutor.execute {
                        val res: Map<String, Any?> = try {
                            actions.handle(action, payload)
                        } catch (t: Throwable) {
                            Log.e(TAG, "phoneAction $action crashed", t)
                            mapOf("success" to false, "error" to "That didn't work on the phone.")
                        }
                        mainHandler.post {
                            try {
                                result.success(res)
                            } catch (e: Exception) {
                                Log.w(TAG, "phoneAction reply failed: ${e.message}")
                            }
                        }
                    }
                }
                "getPhonePermissions" -> {
                    try {
                        result.success(phonePermissionStatus())
                    } catch (e: Exception) {
                        result.error("PERMISSION_ERROR", e.message, null)
                    }
                }
                "requestPhonePermissions" ->
                    requestPhonePermissions(call.argument<List<String>>("permissions") ?: emptyList(), result)
                "openNotificationAccessSettings" -> result.success(openNotificationAccessSettings())
                "openOverlaySettings" -> result.success(openOverlaySettings())
                "openAppSettings" -> result.success(openAppDetailsSettings())
                "pickFiles" -> pickFiles(result)
                "takeSharedFiles" -> {
                    result.success(mapOf("files" to sharedFiles.toList(), "text" to sharedText))
                    sharedFiles.clear()
                    sharedText = null
                }
                "uploadFile" -> {
                    val transfer = fileTransfer
                    val uri = call.argument<String>("uri") ?: ""
                    val url = call.argument<String>("url") ?: ""
                    val token = call.argument<String>("token")
                    val name = call.argument<String>("name") ?: "file"
                    val mime = call.argument<String>("mime")
                    val size = call.longArg("size")
                    val id = call.argument<String>("transfer_id") ?: ""
                    replyInBackground(result, "uploadFile") { transfer.upload(uri, url, token, name, mime, size, id) }
                }
                "receiveFile" -> {
                    val transfer = fileTransfer
                    val url = call.argument<String>("url") ?: ""
                    val token = call.argument<String>("token")
                    val name = call.argument<String>("name") ?: "file"
                    val mime = call.argument<String>("mime")
                    val size = call.longArg("size")
                    val from = call.argument<String>("from") ?: "your PC"
                    val id = call.argument<String>("transfer_id") ?: ""
                    replyInBackground(result, "receiveFile") { transfer.receive(url, token, name, mime, size, from, id) }
                }
                "saveToGallery" -> {
                    val transfer = fileTransfer
                    val path = call.argument<String>("path") ?: ""
                    val name = call.argument<String>("name") ?: "photo.jpg"
                    val mime = call.argument<String>("mime")
                    replyInBackground(result, "saveToGallery") { transfer.saveImageToGallery(path, name, mime) }
                }
                else -> {
                    result.notImplemented()
                }
            }
        }
    }

    private fun startWakeWordListening() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            Log.w(TAG, "RECORD_AUDIO permission not granted!")
            return
        }
        isWakeWordActive = true

        // Acquire CPU wake lock to prevent deep sleep while listening
        acquireCpuWakeLock()

        // Start Foreground Service so microphone stays open even when screen is locked/sleeping
        try {
            WillyWakeWordService.onWakeWordCallback = { command, trigger, fullText ->
                runOnUiThread {
                    wakePhoneScreen()
                    methodChannel?.invokeMethod("onWakeWordDetected", mapOf(
                        "trigger" to trigger,
                        "command" to command,
                        "full_text" to fullText,
                        "is_final" to true
                    ))
                }
            }
            val serviceIntent = Intent(this, WillyWakeWordService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(serviceIntent)
            } else {
                startService(serviceIntent)
            }
            Log.i(TAG, "Started WillyWakeWordService successfully")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start WillyWakeWordService: ${e.message}")
        }
    }

    private fun stopWakeWordListening() {
        isWakeWordActive = false
        releaseCpuWakeLock()

        try {
            stopService(Intent(this, WillyWakeWordService::class.java))
            Log.i(TAG, "Stopped WillyWakeWordService")
        } catch (e: Exception) {
            Log.e(TAG, "Error stopping WillyWakeWordService: ${e.message}")
        }
    }

    private fun acquireCpuWakeLock() {
        try {
            if (cpuWakeLock == null) {
                val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
                cpuWakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "willy:wakeword_cpu_lock")
                cpuWakeLock?.setReferenceCounted(false)
            }
            if (cpuWakeLock?.isHeld == false) {
                cpuWakeLock?.acquire(30 * 60 * 1000L) // 30 min max safety
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to acquire CPU wake lock: ${e.message}")
        }
    }

    private fun releaseCpuWakeLock() {
        try {
            if (cpuWakeLock?.isHeld == true) {
                cpuWakeLock?.release()
            }
        } catch (_: Exception) {}
    }

    private fun wakePhoneScreen() {
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            @Suppress("DEPRECATION")
            val screenLock = pm.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP or PowerManager.ON_AFTER_RELEASE,
                "willy:screen_wakeup"
            )
            screenLock.acquire(6000)

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
                setShowWhenLocked(true)
                setTurnScreenOn(true)
                val km = getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager
                km?.requestDismissKeyguard(this, null)
            }
            Log.i(TAG, "Screen awakened successfully.")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to wake screen: ${e.message}")
        }
    }

    private fun initAndStartRecognizer() {
        if (!isWakeWordActive) return
        try {
            speechRecognizer?.destroy()
            speechRecognizer = null

            val available = SpeechRecognizer.isRecognitionAvailable(this)
            Log.i(TAG, "Init SpeechRecognizer. isRecognitionAvailable=$available")

            speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this)
            speechRecognizer?.setRecognitionListener(object : RecognitionListener {
                override fun onReadyForSpeech(params: Bundle?) {
                    Log.d(TAG, "onReadyForSpeech: Listening...")
                }

                override fun onBeginningOfSpeech() {
                    Log.d(TAG, "onBeginningOfSpeech: User started speaking")
                }

                override fun onRmsChanged(rmsdB: Float) {}
                override fun onBufferReceived(buffer: ByteArray?) {}
                override fun onEndOfSpeech() {
                    Log.d(TAG, "onEndOfSpeech")
                }

                override fun onError(error: Int) {
                    val errorName = when (error) {
                        SpeechRecognizer.ERROR_AUDIO -> "ERROR_AUDIO"
                        SpeechRecognizer.ERROR_CLIENT -> "ERROR_CLIENT"
                        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "ERROR_PERMISSIONS"
                        SpeechRecognizer.ERROR_NETWORK -> "ERROR_NETWORK"
                        SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "ERROR_NETWORK_TIMEOUT"
                        SpeechRecognizer.ERROR_NO_MATCH -> "ERROR_NO_MATCH"
                        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "ERROR_BUSY"
                        SpeechRecognizer.ERROR_SERVER -> "ERROR_SERVER"
                        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "ERROR_TIMEOUT"
                        else -> "ERROR_$error"
                    }
                    Log.w(TAG, "Speech recognition error: $errorName ($error)")

                    if (isWakeWordActive) {
                        // Restart recognition after brief cooldown
                        mainHandler.postDelayed({
                            initAndStartRecognizer()
                        }, 400)
                    }
                }

                override fun onResults(results: Bundle?) {
                    handleSpeechResults(results, isFinal = true)
                    if (isWakeWordActive) {
                        mainHandler.postDelayed({
                            initAndStartRecognizer()
                        }, 300)
                    }
                }

                override fun onPartialResults(partialResults: Bundle?) {
                    handleSpeechResults(partialResults, isFinal = false)
                }

                override fun onEvent(eventType: Int, params: Bundle?) {}
            })

            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag())
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, Locale.getDefault().toLanguageTag())
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5)
            }
            speechRecognizer?.startListening(intent)
            Log.i(TAG, "SpeechRecognizer started listening with language: ${Locale.getDefault().toLanguageTag()}")
        } catch (e: Exception) {
            Log.e(TAG, "Exception in initAndStartRecognizer: ${e.message}")
            if (isWakeWordActive) {
                mainHandler.postDelayed({ initAndStartRecognizer() }, 1000)
            }
        }
    }

    private fun handleSpeechResults(bundle: Bundle?, isFinal: Boolean) {
        val matches = bundle?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION) ?: return
        Log.d(TAG, "Speech candidates (isFinal=$isFinal): $matches")
        val now = System.currentTimeMillis()

        for (candidate in matches) {
            val trimmed = candidate.trim()
            Log.d(TAG, "Heard candidate: '$trimmed' (isFinal=$isFinal)")

            val match = wakeWordRegex.find(trimmed)
            if (match != null) {
                if (now - lastWakeTriggerTime < 2500) {
                    return
                }
                lastWakeTriggerTime = now

                Log.i(TAG, "WAKE WORD TRIGGERED! '${match.value}' in '$trimmed'")

                // Turn on display and dismiss keyguard
                wakePhoneScreen()

                // Haptic feedback
                try {
                    val vibrator = getSystemService(Context.VIBRATOR_SERVICE) as? android.os.Vibrator
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        vibrator?.vibrate(android.os.VibrationEffect.createOneShot(150, android.os.VibrationEffect.DEFAULT_AMPLITUDE))
                    } else {
                        @Suppress("DEPRECATION")
                        vibrator?.vibrate(150)
                    }
                } catch (_: Exception) {}

                val triggerWord = match.value
                val command = trimmed.substring(match.range.last + 1).trim()

                runOnUiThread {
                    methodChannel?.invokeMethod("onWakeWordDetected", mapOf(
                        "trigger" to triggerWord,
                        "command" to command,
                        "full_text" to trimmed,
                        "is_final" to isFinal
                    ))
                }
                break
            }
        }
    }

    private var currentRingtone: android.media.Ringtone? = null
    private var currentVibrator: android.os.Vibrator? = null

    private fun startRingingAlarm() {
        stopRingingAlarm()
        val alarmUri = android.media.RingtoneManager.getDefaultUri(android.media.RingtoneManager.TYPE_ALARM)
            ?: android.media.RingtoneManager.getDefaultUri(android.media.RingtoneManager.TYPE_RINGTONE)
        currentRingtone = android.media.RingtoneManager.getRingtone(applicationContext, alarmUri)
        currentRingtone?.play()

        currentVibrator = getSystemService(Context.VIBRATOR_SERVICE) as? android.os.Vibrator
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            currentVibrator?.vibrate(android.os.VibrationEffect.createWaveform(longArrayOf(0, 500, 200, 500), 0))
        } else {
            @Suppress("DEPRECATION")
            currentVibrator?.vibrate(longArrayOf(0, 500, 200, 500), 0)
        }
    }

    private fun stopRingingAlarm() {
        try {
            currentRingtone?.stop()
            currentVibrator?.cancel()
        } catch (_: Exception) {}
    }

    private fun isNotificationServiceEnabled(): Boolean {
        val pkgName = packageName
        val flat = android.provider.Settings.Secure.getString(
            contentResolver,
            "enabled_notification_listeners"
        )
        return flat != null && flat.contains(pkgName)
    }

    private fun getRecentMissedCalls(): List<Map<String, String>> {
        val list = mutableListOf<Map<String, String>>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.READ_CALL_LOG) != PackageManager.PERMISSION_GRANTED) {
            return list
        }

        val since = System.currentTimeMillis() - (24 * 60 * 60 * 1000)
        val selection = "${CallLog.Calls.TYPE} = ? AND ${CallLog.Calls.DATE} >= ?"
        val selectionArgs = arrayOf(CallLog.Calls.MISSED_TYPE.toString(), since.toString())

        var cursor: Cursor? = null
        try {
            cursor = contentResolver.query(
                CallLog.Calls.CONTENT_URI,
                arrayOf(CallLog.Calls.CACHED_NAME, CallLog.Calls.NUMBER, CallLog.Calls.DATE),
                selection,
                selectionArgs,
                "${CallLog.Calls.DATE} DESC"
            )

            cursor?.let {
                val nameIdx = it.getColumnIndex(CallLog.Calls.CACHED_NAME)
                val numIdx = it.getColumnIndex(CallLog.Calls.NUMBER)
                val dateIdx = it.getColumnIndex(CallLog.Calls.DATE)

                while (it.moveToNext()) {
                    val name = if (nameIdx != -1) it.getString(nameIdx) else null
                    val number = if (numIdx != -1) it.getString(numIdx) else ""
                    val dateMs = if (dateIdx != -1) it.getLong(dateIdx) else 0L

                    val displayName = name ?: (if (number.isNotEmpty()) number else "Unknown Caller")
                    list.add(mapOf("name" to displayName, "number" to number))
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
        } finally {
            cursor?.close()
        }
        return list
    }

    // --- Live device status, remote actions and alerts -------------------------------------

    private val stopRingRunnable = Runnable { stopRingingAlarm() }
    private var torchOn = false
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var pendingSpeech: String? = null

    private fun round1(value: Double): Double = Math.round(value * 10.0) / 10.0

    private fun friendlyDeviceName(): String {
        val userName = try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N_MR1) {
                Settings.Global.getString(contentResolver, Settings.Global.DEVICE_NAME)
            } else null
        } catch (_: Exception) { null }
        if (!userName.isNullOrBlank()) return userName
        val maker = Build.MANUFACTURER.replaceFirstChar { it.uppercase() }
        return if (Build.MODEL.startsWith(maker, ignoreCase = true)) Build.MODEL else "$maker ${Build.MODEL}"
    }

    private fun getDeviceStatus(): Map<String, Any?> {
        val bm = getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        val battery = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        val charging = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) bm.isCharging else null

        var networkType = "none"
        try {
            val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val caps = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) cm.getNetworkCapabilities(cm.activeNetwork) else null
            networkType = when {
                caps == null -> "none"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> "vpn"
                else -> "other"
            }
        } catch (_: Exception) {}

        val stat = StatFs(Environment.getDataDirectory().path)
        val am = getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val mem = ActivityManager.MemoryInfo().also { am.getMemoryInfo(it) }
        val ramPct = if (mem.totalMem > 0) ((mem.totalMem - mem.availMem) * 100 / mem.totalMem).toInt() else null
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager

        return mapOf(
            "battery_pct" to if (battery in 0..100) battery else null,
            "is_charging" to charging,
            "network_type" to networkType,
            "storage_free_gb" to round1(stat.availableBytes / 1e9),
            "storage_total_gb" to round1(stat.totalBytes / 1e9),
            "ram_pct" to ramPct,
            "screen_on" to pm.isInteractive,
            "torch_on" to torchOn,
            "device_name" to friendlyDeviceName(),
            "model" to Build.MODEL,
            "manufacturer" to Build.MANUFACTURER,
            "android_version" to Build.VERSION.RELEASE,
            "sdk_int" to Build.VERSION.SDK_INT,
            "android_id" to (Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID) ?: "")
        )
    }

    private fun openUrl(url: String): Boolean {
        if (url.isBlank()) return false
        return try {
            val target = if (url.contains("://")) url else "https://$url"
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(target)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            true
        } catch (e: Exception) {
            Log.e(TAG, "openUrl failed: ${e.message}")
            false
        }
    }

    private fun setTorch(on: Boolean): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return false
        return try {
            val cm = getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val id = cm.cameraIdList.firstOrNull {
                cm.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
            } ?: return false
            cm.setTorchMode(id, on)
            torchOn = on
            true
        } catch (e: Exception) {
            Log.e(TAG, "setTorch failed: ${e.message}")
            false
        }
    }

    private fun vibrateOnce(ms: Long) {
        try {
            val vibrator = getSystemService(Context.VIBRATOR_SERVICE) as? android.os.Vibrator
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vibrator?.vibrate(android.os.VibrationEffect.createOneShot(ms.coerceIn(50, 5000), android.os.VibrationEffect.DEFAULT_AMPLITUDE))
            } else {
                @Suppress("DEPRECATION")
                vibrator?.vibrate(ms.coerceIn(50, 5000))
            }
        } catch (_: Exception) {}
    }

    private fun speak(text: String) {
        if (text.isBlank()) return
        val engine = tts
        if (engine == null) {
            pendingSpeech = text
            tts = TextToSpeech(applicationContext) { status ->
                ttsReady = status == TextToSpeech.SUCCESS
                if (ttsReady) {
                    tts?.language = Locale.getDefault()
                    pendingSpeech?.let { tts?.speak(it, TextToSpeech.QUEUE_FLUSH, null, "willy") }
                }
                pendingSpeech = null
            }
        } else if (ttsReady) {
            engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, "willy")
        } else {
            pendingSpeech = text
        }
    }

    private fun showAlertNotification(title: String, text: String) {
        try {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val channelId = WillyAlerts.CHANNEL_ID
            WillyAlerts.ensureChannel(this)
            val open = PendingIntent.getActivity(
                this, 7,
                Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            )
            val notification = NotificationCompat.Builder(this, channelId)
                .setSmallIcon(R.drawable.ic_stat_willy)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setAutoCancel(true)
                .setContentIntent(open)
                .build()
            nm.notify((System.currentTimeMillis() % 100000).toInt(), notification)
        } catch (e: Exception) {
            Log.e(TAG, "showAlertNotification failed: ${e.message}")
        }
    }

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            markAsked(listOf(Manifest.permission.POST_NOTIFICATIONS))
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 4201)
        }
    }

    // --- Phone skills: permissions and settings shortcuts ------------------------------------

    private fun runtimePermissionFor(key: String): String? = when (key) {
        "call_phone" -> Manifest.permission.CALL_PHONE
        "send_sms" -> Manifest.permission.SEND_SMS
        "read_contacts" -> Manifest.permission.READ_CONTACTS
        "post_notifications" -> if (Build.VERSION.SDK_INT >= 33) Manifest.permission.POST_NOTIFICATIONS else null
        else -> null
    }

    private fun isGranted(permission: String) =
        ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

    private fun askedKey(permission: String) = "asked:$permission"

    private fun markAsked(permissions: Collection<String>) {
        getSharedPreferences(PHONE_PERMISSIONS_PREFS, MODE_PRIVATE).edit().apply {
            permissions.forEach { putBoolean(askedKey(it), true) }
        }.apply()
    }

    /** Android stops showing the prompt after repeated denials; then only App info can grant it. */
    private fun isBlocked(permission: String): Boolean =
        !isGranted(permission) &&
            getSharedPreferences(PHONE_PERMISSIONS_PREFS, MODE_PRIVATE).getBoolean(askedKey(permission), false) &&
            !ActivityCompat.shouldShowRequestPermissionRationale(this, permission)

    private fun phonePermissionStatus(): Map<String, Any?> {
        val status = PhoneActions.permissionStatus(this)
        status["blocked"] = listOf("call_phone", "send_sms", "read_contacts", "post_notifications").filter { key ->
            runtimePermissionFor(key)?.let { isBlocked(it) } ?: false
        }
        return status
    }

    /** Prompts for the runtime permissions behind [keys]; replies with the new status once answered. */
    private fun requestPhonePermissions(keys: List<String>, result: MethodChannel.Result) {
        try {
            // Notifications switched off in system settings can only be switched back on there.
            if ("post_notifications" in keys && !NotificationManagerCompat.from(this).areNotificationsEnabled() &&
                (Build.VERSION.SDK_INT < 33 || isGranted(Manifest.permission.POST_NOTIFICATIONS))
            ) {
                val opened = openAppNotificationSettings()
                result.success(phonePermissionStatus() + ("opened_settings" to opened))
                return
            }
            val wanted = keys.mapNotNull { runtimePermissionFor(it) }.filter { !isGranted(it) }.distinct()
            if (wanted.isEmpty()) {
                result.success(phonePermissionStatus())
                return
            }
            val askable = wanted.filter { !isBlocked(it) }
            if (askable.isEmpty()) {
                val opened = openAppDetailsSettings()
                result.success(phonePermissionStatus() + ("opened_settings" to opened))
                return
            }
            // One prompt at a time: settle an older, unanswered request first.
            pendingPermissionResult?.success(phonePermissionStatus())
            pendingPermissionResult = result
            markAsked(askable)
            ActivityCompat.requestPermissions(this, askable.toTypedArray(), PHONE_PERMISSIONS_REQUEST)
        } catch (e: Exception) {
            result.error("PERMISSION_ERROR", e.message, null)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PHONE_PERMISSIONS_REQUEST) {
            val pending = pendingPermissionResult
            pendingPermissionResult = null
            try {
                pending?.success(phonePermissionStatus())
            } catch (e: Exception) {
                Log.w(TAG, "permission reply failed: ${e.message}")
            }
        }
    }

    private fun startFirstAvailable(intents: List<Intent>): Boolean {
        for (intent in intents) {
            try {
                startActivity(intent)
                return true
            } catch (_: Exception) {
            }
        }
        return false
    }

    private fun openNotificationAccessSettings(): Boolean {
        val intents = mutableListOf<Intent>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            intents.add(
                Intent(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS).putExtra(
                    Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME,
                    ComponentName(this, WillyNotificationListenerService::class.java).flattenToString()
                )
            )
        }
        intents.add(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        return startFirstAvailable(intents)
    }

    /** "Display over other apps": lets Willy open apps while it is in the background. */
    private fun openOverlaySettings(): Boolean = startFirstAvailable(
        listOf(
            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")),
            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION)
        )
    )

    private fun openAppDetailsSettings(): Boolean = startFirstAvailable(
        listOf(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null)))
    )

    private fun openAppNotificationSettings(): Boolean = startFirstAvailable(
        listOf(
            Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, packageName),
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null))
        )
    )

    override fun onDestroy() {
        stopWakeWordListening()
        mainHandler.removeCallbacks(stopRingRunnable)
        stopRingingAlarm()
        if (torchOn) setTorch(false)
        tts?.shutdown()
        super.onDestroy()
    }
}
