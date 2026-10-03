package com.example.willy_mobile

import android.Manifest
import android.app.Activity
import android.app.KeyguardManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ActivityNotFoundException
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.provider.AlarmClock
import android.provider.MediaStore
import android.provider.ContactsContract.CommonDataKinds.Phone
import android.provider.ContactsContract.PhoneLookup
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.telecom.TelecomManager
import android.telephony.PhoneNumberUtils
import android.telephony.SmsManager
import android.telephony.TelephonyManager
import android.util.Log
import android.view.KeyEvent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * "Phone skills": actions the hub (or the PC, through the hub) runs on this phone —
 * calls, texts, WhatsApp, apps, alarms, timers, volume, media keys, navigation,
 * recent notifications and contacts.
 *
 * [handle] never throws and always returns the contract's result map:
 *   done              -> {"success": true, "message": "...", ...extra}
 *   failed            -> {"success": false, "error": "..."}
 *   the user must tap -> {"success": true, "needs_tap": true, "message": "..."}
 * Messages are spoken by the assistant, so they are short and natural.
 *
 * Android 10+ silently drops activity starts from the background. Actions that need an
 * activity start it directly when Willy is on screen ([appInFront]) or may draw over other
 * apps; otherwise they post a "tap to finish" notification that performs the action.
 */
class PhoneActions(context: Context, private val appInFront: () -> Boolean) {

    private val ctx: Context = context.applicationContext
    private val pm: PackageManager = ctx.packageManager

    companion object {
        private const val TAG = "WillyPhone"
        const val ACTIONS_CHANNEL = "willy_actions"
        private const val MAPS_PACKAGE = "com.google.android.apps.maps"
        private const val PLAY_STORE_PACKAGE = "com.android.vending"
        private val PACKAGE_NAME = Regex("^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)+$")
        private val WHATSAPP_PACKAGES = listOf("com.whatsapp", "com.whatsapp.w4b")
        private const val SMS_SENT_ACTION = "com.example.willy_mobile.SMS_SENT"
        private const val SMS_STATUS_WAIT_SEC = 8L
        private const val TAP_NOTIFICATION_TIMEOUT_MS = 10 * 60 * 1000L
        private const val APP_CACHE_MS = 5 * 60 * 1000L
        private const val BLOCKED_MESSAGE =
            "Android blocked Willy from opening that in the background. On the phone, open Willy > " +
                "Phone skills and turn on Background actions."

        // Request codes / notification ids: unique per tap notification and SMS status intent.
        private val codes = AtomicInteger(20000 + ((System.currentTimeMillis() / 1000) % 50000).toInt())
        private fun nextCode(): Int = codes.incrementAndGet()

        fun notificationAccessGranted(context: Context): Boolean = try {
            NotificationManagerCompat.getEnabledListenerPackages(context).contains(context.packageName)
        } catch (_: Exception) {
            false
        }

        /** Asks Android to (re)bind the notification listener, e.g. after an app update. */
        fun requestListenerRebind(context: Context) {
            try {
                NotificationListenerService.requestRebind(
                    ComponentName(context, WillyNotificationListenerService::class.java)
                )
            } catch (_: Exception) {
            }
        }

        private fun isInstalled(context: Context, pkg: String): Boolean = try {
            context.packageManager.getLaunchIntentForPackage(pkg) != null
        } catch (_: Exception) {
            false
        }

        /** What the "Phone skills" settings show: which permissions / special accesses are on. */
        fun permissionStatus(context: Context): MutableMap<String, Any?> {
            val c = context.applicationContext
            fun has(permission: String) =
                ContextCompat.checkSelfPermission(c, permission) == PackageManager.PERMISSION_GRANTED
            val notificationsOn = NotificationManagerCompat.from(c).areNotificationsEnabled() &&
                (Build.VERSION.SDK_INT < 33 || has(Manifest.permission.POST_NOTIFICATIONS))
            return linkedMapOf<String, Any?>(
                "call_phone" to has(Manifest.permission.CALL_PHONE),
                "send_sms" to has(Manifest.permission.SEND_SMS),
                "read_contacts" to has(Manifest.permission.READ_CONTACTS),
                "notification_access" to notificationAccessGranted(c),
                "notification_listener_connected" to WillyNotificationListenerService.connected,
                "overlay" to Settings.canDrawOverlays(c),
                "post_notifications" to notificationsOn,
                "has_telephony" to c.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY),
                "whatsapp_installed" to WHATSAPP_PACKAGES.any { isInstalled(c, it) },
                "sdk_int" to Build.VERSION.SDK_INT
            )
        }
    }

    // ------------------------------------------------------------------ entry point

    fun handle(action: String, payload: Map<String, Any?>): Map<String, Any?> = try {
        when (action) {
            "phone_call" -> call(payload)
            "phone_sms" -> sms(payload)
            "phone_whatsapp" -> whatsapp(payload)
            "phone_open_app" -> openApp(payload)
            "phone_alarm" -> alarm(payload)
            "phone_timer" -> timer(payload)
            "phone_volume" -> volume(payload)
            "phone_media" -> media(payload)
            "phone_navigate" -> navigate(payload)
            "phone_notifications" -> notifications(payload)
            "phone_contacts" -> contacts(payload)
            "phone_open_url" -> openUrl(payload)
            "phone_install_app" -> installApp(payload)
            "phone_uninstall_app" -> uninstallApp(payload)
            "phone_camera" -> camera(payload)
            else -> fail("The phone doesn't know how to do \"$action\" yet.")
        }
    } catch (e: SecurityException) {
        Log.w(TAG, "$action refused: ${e.message}")
        fail("Android didn't allow that on the phone. Check Willy's Phone skills settings.")
    } catch (e: Exception) {
        Log.e(TAG, "$action failed", e)
        fail("That didn't work on the phone (${e.message ?: e.javaClass.simpleName}).")
    }

    // --------------------------------------------------------------- result helpers

    private fun ok(message: String, vararg extra: Pair<String, Any?>): Map<String, Any?> {
        val out = linkedMapOf<String, Any?>("success" to true, "message" to message)
        extra.forEach { out[it.first] = it.second }
        return out
    }

    private fun needsTap(message: String, vararg extra: Pair<String, Any?>): Map<String, Any?> =
        ok(message, "needs_tap" to true, *extra)

    private fun fail(error: String, vararg extra: Pair<String, Any?>): Map<String, Any?> {
        val out = linkedMapOf<String, Any?>("success" to false, "error" to error)
        extra.forEach { out[it.first] = it.second }
        return out
    }

    private fun Map<String, Any?>.text(key: String): String = this[key]?.toString()?.trim() ?: ""

    private fun Map<String, Any?>.int(key: String): Int? {
        val d = when (val v = this[key]) {
            is Number -> v.toDouble()
            is String -> v.trim().removeSuffix("%").trim().toDoubleOrNull()
            else -> null
        } ?: return null
        return if (d.isNaN() || d.isInfinite()) null else d.roundToInt()
    }

    private fun granted(permission: String): Boolean =
        ContextCompat.checkSelfPermission(ctx, permission) == PackageManager.PERMISSION_GRANTED

    private fun contactsGranted() = granted(Manifest.permission.READ_CONTACTS)

    private fun hasTelephony() = pm.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)

    private fun phoneLocked(): Boolean = try {
        ctx.getSystemService(KeyguardManager::class.java)?.isKeyguardLocked == true
    } catch (_: Exception) {
        false
    }

    // ------------------------------------------------------- starting other apps

    private enum class Launch { STARTED, POSTED, NO_HANDLER, BLOCKED }

    private fun canStartDirectly(): Boolean = appInFront() || Settings.canDrawOverlays(ctx)

    /**
     * Starts [intent] now when Android allows it (Willy on screen or "display over other apps"
     * granted); otherwise posts a notification that starts it when tapped.
     */
    private fun launch(intent: Intent, tapTitle: String, tapText: String, icon: Int): Launch {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val resolvable = intent.resolveActivity(pm) != null
        if (canStartDirectly()) {
            try {
                ctx.startActivity(intent)
                return Launch.STARTED
            } catch (_: ActivityNotFoundException) {
                return Launch.NO_HANDLER
            } catch (e: SecurityException) {
                Log.w(TAG, "Direct start refused, posting a tap notification: ${e.message}")
            }
        }
        if (!resolvable) return Launch.NO_HANDLER
        return if (postTapNotification(intent, tapTitle, tapText, icon)) Launch.POSTED else Launch.BLOCKED
    }

    private fun postTapNotification(target: Intent, title: String, text: String, icon: Int): Boolean {
        return try {
            if (!NotificationManagerCompat.from(ctx).areNotificationsEnabled()) return false
            val nm = ctx.getSystemService(NotificationManager::class.java) ?: return false
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                if (nm.getNotificationChannel(ACTIONS_CHANNEL) == null) {
                    nm.createNotificationChannel(
                        NotificationChannel(ACTIONS_CHANNEL, "Willy actions", NotificationManager.IMPORTANCE_HIGH).apply {
                            description = "Tap to finish something you asked Willy to do on this phone"
                            enableVibration(true)
                        }
                    )
                }
                if (nm.getNotificationChannel(ACTIONS_CHANNEL)?.importance == NotificationManager.IMPORTANCE_NONE) {
                    return false
                }
            }
            val code = nextCode()
            val tap = PendingIntent.getActivity(
                ctx, code, target, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            )
            val notification = NotificationCompat.Builder(ctx, ACTIONS_CHANNEL)
                .setSmallIcon(icon)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_REMINDER)
                .setDefaults(NotificationCompat.DEFAULT_ALL)
                .setAutoCancel(true)
                .setTimeoutAfter(TAP_NOTIFICATION_TIMEOUT_MS)
                .setContentIntent(tap)
                .build()
            nm.notify(code, notification)
            true
        } catch (e: Exception) {
            Log.w(TAG, "Tap notification failed: ${e.message}")
            false
        }
    }

    // --------------------------------------------------------------- recipients

    private class Recipient(val name: String?, val number: String)

    /** Either a recipient or a user-facing reason why there is none. */
    private class Resolution(val recipient: Recipient?, val error: String?)

    /** Contract: 3+ digits means a number; otherwise a contact name (exact > starts-with > contains). */
    private fun resolve(rawTo: String): Resolution {
        val to = rawTo.trim().trim('"', '\'', '“', '”').trim()
        if (to.count { it.isDigit() } >= 3) {
            val number = dialable(to)
            // "Office 123" might still be a saved name; an exact contact match wins.
            if (to.any { it.isLetter() } && contactsGranted()) {
                bestContact(to, exactOnly = true)?.let { return Resolution(it, null) }
            }
            return Resolution(Recipient(nameForNumber(number), number), null)
        }
        if (!contactsGranted()) {
            return Resolution(null, "Allow Contacts for Willy on the phone, or give me the number.")
        }
        val hit = bestContact(to, exactOnly = false)
            ?: return Resolution(null, "No contact named '$to' on your phone.")
        return Resolution(hit, null)
    }

    /** Keeps what a dialer understands: digits, a leading +, and * / # for service codes. */
    private fun dialable(s: String): String {
        val kept = s.filter { it.isDigit() || it == '+' || it == '*' || it == '#' }
        val rest = kept.filter { it != '+' }
        return if (kept.startsWith("+")) "+$rest" else rest
    }

    private fun countryIso(): String {
        val tm = try {
            ctx.getSystemService(TelephonyManager::class.java)
        } catch (_: Exception) {
            null
        }
        val candidates = listOf(
            try { tm?.simCountryIso } catch (_: Exception) { null },
            try { tm?.networkCountryIso } catch (_: Exception) { null },
            Locale.getDefault().country
        )
        return (candidates.firstOrNull { !it.isNullOrBlank() } ?: "").uppercase(Locale.ROOT)
    }

    /** "0412 345 678" style, for speaking a number that has no contact name. */
    private fun spokenNumber(number: String): String = try {
        PhoneNumberUtils.formatNumber(number, countryIso()) ?: number
    } catch (_: Exception) {
        number
    }

    private fun who(r: Recipient): String = r.name ?: spokenNumber(r.number)

    /** "Mom's number" or the number itself. */
    private fun numberOf(r: Recipient): String = if (r.name != null) "${r.name}'s number" else spokenNumber(r.number)

    private fun recipientExtras(r: Recipient): Array<Pair<String, Any?>> =
        arrayOf<Pair<String, Any?>>("name" to (r.name ?: r.number), "number" to r.number)

    // ------------------------------------------------------------------ contacts

    private class PhoneRow(
        val contactId: Long,
        val name: String,
        val number: String,
        val rawDigits: String,
        val normDigits: String,
        val type: Int,
        val label: String?,
        val superPrimary: Boolean,
        val primary: Boolean,
        val starred: Boolean
    )

    private fun loadPhoneRows(): List<PhoneRow> {
        val rows = ArrayList<PhoneRow>()
        val projection = arrayOf(
            Phone.CONTACT_ID, Phone.DISPLAY_NAME, Phone.NUMBER, Phone.NORMALIZED_NUMBER, Phone.TYPE,
            Phone.LABEL, Phone.IS_SUPER_PRIMARY, Phone.IS_PRIMARY, Phone.STARRED
        )
        ctx.contentResolver.query(Phone.CONTENT_URI, projection, null, null, null)?.use { c ->
            val iId = c.getColumnIndex(Phone.CONTACT_ID)
            val iName = c.getColumnIndex(Phone.DISPLAY_NAME)
            val iNumber = c.getColumnIndex(Phone.NUMBER)
            val iNormalized = c.getColumnIndex(Phone.NORMALIZED_NUMBER)
            val iType = c.getColumnIndex(Phone.TYPE)
            val iLabel = c.getColumnIndex(Phone.LABEL)
            val iSuper = c.getColumnIndex(Phone.IS_SUPER_PRIMARY)
            val iPrimary = c.getColumnIndex(Phone.IS_PRIMARY)
            val iStarred = c.getColumnIndex(Phone.STARRED)
            while (c.moveToNext()) {
                val number = (if (iNumber >= 0) c.getString(iNumber) else null)?.trim().orEmpty()
                if (number.isEmpty()) continue
                val name = (if (iName >= 0) c.getString(iName) else null)?.trim().orEmpty()
                val normalized = if (iNormalized >= 0) c.getString(iNormalized) else null
                rows.add(
                    PhoneRow(
                        contactId = if (iId >= 0) c.getLong(iId) else -1L,
                        name = name.ifEmpty { number },
                        number = number,
                        rawDigits = number.filter { it.isDigit() },
                        normDigits = normalized?.filter { it.isDigit() }.orEmpty(),
                        type = if (iType >= 0) c.getInt(iType) else 0,
                        label = if (iLabel >= 0) c.getString(iLabel) else null,
                        superPrimary = iSuper >= 0 && c.getInt(iSuper) != 0,
                        primary = iPrimary >= 0 && c.getInt(iPrimary) != 0,
                        starred = iStarred >= 0 && c.getInt(iStarred) != 0
                    )
                )
            }
        }
        return rows
    }

    private val whitespace = Regex("\\s+")

    /** Letters and digits of any script (combining marks kept, e.g. Tamil vowel signs). */
    private fun isNameChar(ch: Char): Boolean {
        if (ch.isLetterOrDigit()) return true
        val t = Character.getType(ch)
        return t == Character.NON_SPACING_MARK.toInt() || t == Character.COMBINING_SPACING_MARK.toInt() ||
            t == Character.ENCLOSING_MARK.toInt()
    }

    /** "Mom ❤️" -> "mom", "Dad's work" -> "dad s work". */
    private fun normName(s: String): String {
        val sb = StringBuilder(s.length)
        for (ch in s.lowercase(Locale.ROOT)) sb.append(if (isNameChar(ch)) ch else ' ')
        return sb.toString().trim().replace(whitespace, " ")
    }

    /** 0 exact, 1 starts-with, 2 a word starts with it, 3 contains, -1 no match. */
    private fun nameRank(name: String, query: String): Int {
        if (query.isEmpty() || name.isEmpty()) return -1
        return when {
            name == query || name.replace(" ", "") == query.replace(" ", "") -> 0
            name.startsWith(query) -> 1
            name.split(' ').any { it.startsWith(query) } -> 2
            name.contains(query) -> 3
            else -> -1
        }
    }

    private class Candidate(val rank: Int, val name: String, val starred: Boolean, val rows: List<PhoneRow>)

    /** Best contact for a spoken name, with its best number (mobile first). */
    private fun bestContact(rawQuery: String, exactOnly: Boolean): Recipient? {
        val rows = loadPhoneRows()
        if (rows.isEmpty()) return null
        val first = normName(rawQuery)
        val queries = mutableListOf(first)
        if (first.startsWith("my ")) queries.add(first.removePrefix("my ").trim()) // "my mom" -> "mom"
        val contacts = rows.groupBy { it.contactId }.values
        for (q in queries) {
            if (q.isEmpty()) continue
            val best = contacts.mapNotNull { group ->
                val name = group.first().name
                val rank = nameRank(normName(name), q)
                if (rank < 0 || (exactOnly && rank > 0)) null
                else Candidate(rank, name, group.any { it.starred }, group)
            }.minWithOrNull(
                compareBy<Candidate>({ it.rank }, { if (it.starred) 0 else 1 }, { it.name.length }, { it.name.lowercase(Locale.ROOT) })
            ) ?: continue
            return Recipient(best.name, pickNumber(best.rows).number)
        }
        return null
    }

    private fun pickNumber(rows: List<PhoneRow>): PhoneRow = rows.sortedWith(
        compareBy<PhoneRow>(
            { if (it.type == Phone.TYPE_MOBILE) 0 else 1 },
            { if (it.superPrimary) 0 else 1 },
            { if (it.primary) 0 else 1 }
        )
    ).first()

    private fun nameForNumber(number: String): String? {
        if (!contactsGranted() || number.count { it.isDigit() } < 3) return null
        return try {
            val uri = Uri.withAppendedPath(PhoneLookup.CONTENT_FILTER_URI, Uri.encode(number))
            ctx.contentResolver.query(uri, arrayOf(PhoneLookup.DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getString(0)?.takeIf { it.isNotBlank() } else null
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun typeLabel(r: PhoneRow): String = try {
        Phone.getTypeLabel(ctx.resources, r.type, r.label).toString().lowercase(Locale.ROOT)
    } catch (_: Exception) {
        "phone"
    }

    // --------------------------------------------------------------------- calls

    private fun call(p: Map<String, Any?>): Map<String, Any?> {
        val to = p.text("to")
        if (to.isEmpty()) return fail("Who should I call?")
        if (!hasTelephony()) return fail("This phone can't make calls.")
        val res = resolve(to)
        val r = res.recipient ?: return fail(res.error ?: "I couldn't find who to call.")
        val who = who(r)
        val extra = recipientExtras(r)

        if (granted(Manifest.permission.CALL_PHONE)) {
            try {
                val telecom = ctx.getSystemService(TelecomManager::class.java)
                if (telecom != null) {
                    telecom.placeCall(Uri.fromParts("tel", r.number, null), Bundle())
                    return ok("Calling $who.", *extra)
                }
            } catch (e: Exception) {
                Log.w(TAG, "placeCall failed, falling back to the dialer: ${e.message}")
            }
        }
        val dial = Intent(Intent.ACTION_DIAL, Uri.fromParts("tel", r.number, null))
        return when (launch(dial, "Tap to call $who", "Opens your dialer ready to call $who.", android.R.drawable.ic_menu_call)) {
            Launch.STARTED -> needsTap(
                if (phoneLocked()) "Your dialer is ready to call $who — unlock your phone and tap call."
                else "Your dialer is open with ${numberOf(r)} — tap call.",
                *extra
            )
            Launch.POSTED -> needsTap("Tap the Willy notification on your phone to call $who.", *extra)
            Launch.NO_HANDLER -> fail("There's no dialer app on your phone.")
            Launch.BLOCKED -> fail(BLOCKED_MESSAGE)
        }
    }

    // ---------------------------------------------------------------------- SMS

    private fun sms(p: Map<String, Any?>): Map<String, Any?> {
        val to = p.text("to")
        val message = p.text("message").ifEmpty { p.text("text") }
        if (to.isEmpty()) return fail("Who should I text?")
        if (message.isEmpty()) return fail("What should the text say?")
        val res = resolve(to)
        val r = res.recipient ?: return fail(res.error ?: "I couldn't find who to text.")
        val who = who(r)
        val extra = recipientExtras(r)

        if (granted(Manifest.permission.SEND_SMS) && hasTelephony()) {
            try {
                return when (sendSms(r.number, message)) {
                    null -> ok("Sending your text to $who.", *extra)
                    Activity.RESULT_OK -> ok("Text sent to $who.", *extra)
                    SmsManager.RESULT_ERROR_NO_SERVICE -> fail("There's no mobile signal, so the text to $who didn't send.")
                    SmsManager.RESULT_ERROR_RADIO_OFF -> fail("The phone's mobile network is off, so the text to $who didn't send.")
                    else -> fail("The text to $who didn't send.")
                }
            } catch (e: Exception) {
                Log.w(TAG, "SmsManager failed, opening the compose screen: ${e.message}")
            }
        }
        val compose = Intent(Intent.ACTION_SENDTO, Uri.fromParts("smsto", r.number, null))
            .putExtra("sms_body", message)
        return when (launch(compose, "Tap to text $who", message, android.R.drawable.stat_notify_chat)) {
            Launch.STARTED -> needsTap(
                if (phoneLocked()) "Your text to $who is ready — unlock your phone and tap send."
                else "Your messages app is open with the text to $who — tap send.",
                *extra
            )
            Launch.POSTED -> needsTap("Tap the Willy notification on your phone to text $who.", *extra)
            Launch.NO_HANDLER -> fail("There's no messaging app on your phone.")
            Launch.BLOCKED -> fail(BLOCKED_MESSAGE)
        }
    }

    /**
     * Sends through the default SIM and waits briefly for the radio's verdict.
     * Returns the sent-status result code, or null when it didn't arrive in time.
     */
    private fun sendSms(number: String, message: String): Int? {
        val sms: SmsManager = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            ctx.getSystemService(SmsManager::class.java) ?: throw IllegalStateException("SMS isn't available")
        } else {
            @Suppress("DEPRECATION")
            SmsManager.getDefault()
        }
        val parts = sms.divideMessage(message).takeIf { it.isNotEmpty() } ?: arrayListOf(message)
        val action = "$SMS_SENT_ACTION.${nextCode()}"
        val outcome = AtomicInteger(Int.MIN_VALUE)
        val latch = CountDownLatch(1)
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                outcome.compareAndSet(Int.MIN_VALUE, resultCode)
                latch.countDown()
            }
        }
        // The status arrives through our own PendingIntent, so a not-exported receiver gets it.
        ContextCompat.registerReceiver(ctx, receiver, IntentFilter(action), ContextCompat.RECEIVER_NOT_EXPORTED)
        try {
            val sent = ArrayList<PendingIntent>(parts.size)
            for (i in parts.indices) {
                sent.add(
                    PendingIntent.getBroadcast(
                        ctx, nextCode(), Intent(action).setPackage(ctx.packageName),
                        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_ONE_SHOT
                    )
                )
            }
            if (parts.size > 1) {
                sms.sendMultipartTextMessage(number, null, parts, sent, null)
            } else {
                sms.sendTextMessage(number, null, parts[0], sent[0], null)
            }
            latch.await(SMS_STATUS_WAIT_SEC, TimeUnit.SECONDS)
        } finally {
            try {
                ctx.unregisterReceiver(receiver)
            } catch (_: Exception) {
            }
        }
        return outcome.get().takeIf { it != Int.MIN_VALUE }
    }

    // ------------------------------------------------------------------ WhatsApp

    /** The intent aimed at WhatsApp, then WhatsApp Business, then any handler (package null). */
    private fun forWhatsApp(base: Intent): Pair<Intent, String?> {
        for (pkg in WHATSAPP_PACKAGES) {
            val candidate = Intent(base).setPackage(pkg)
            if (candidate.resolveActivity(pm) != null) return candidate to pkg
        }
        return Intent(base) to null
    }

    /** International digits for wa.me: keeps a given +cc, otherwise uses the SIM/network country. */
    private fun whatsappDigits(number: String): String {
        val cleaned = number.filter { it.isDigit() || it == '+' }
        if (cleaned.startsWith("+")) return cleaned.filter { it.isDigit() }
        if (cleaned.startsWith("00")) return cleaned.drop(2).filter { it.isDigit() }
        val e164 = try {
            PhoneNumberUtils.formatNumberToE164(cleaned, countryIso())
        } catch (_: Exception) {
            null
        }
        return (e164 ?: cleaned).filter { it.isDigit() }
    }

    private fun whatsapp(p: Map<String, Any?>): Map<String, Any?> {
        val to = p.text("to")
        val message = p.text("message").ifEmpty { p.text("text") }
        val icon = android.R.drawable.stat_notify_chat

        if (to.isEmpty()) {
            if (message.isEmpty()) return fail("Who should I WhatsApp, and what should it say?")
            val (share, pkg) = forWhatsApp(
                Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, message)
            )
            if (pkg == null) return fail("WhatsApp isn't installed on your phone.")
            return when (launch(share, "Tap to send in WhatsApp", message, icon)) {
                Launch.STARTED -> needsTap("WhatsApp is open with your message — pick a chat and tap send.")
                Launch.POSTED -> needsTap("Tap the Willy notification on your phone to open WhatsApp.")
                Launch.NO_HANDLER -> fail("WhatsApp isn't installed on your phone.")
                Launch.BLOCKED -> fail(BLOCKED_MESSAGE)
            }
        }

        val res = resolve(to)
        val r = res.recipient ?: return fail(res.error ?: "I couldn't find who to message.")
        val who = who(r)
        val digits = whatsappDigits(r.number)
        if (digits.length < 6) return fail("${r.name ?: r.number} doesn't look like a WhatsApp number.")
        val url = buildString {
            append("https://wa.me/").append(digits)
            if (message.isNotEmpty()) append("?text=").append(Uri.encode(message))
        }
        val (view, pkg) = forWhatsApp(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        val extra = arrayOf<Pair<String, Any?>>("name" to (r.name ?: r.number), "number" to "+$digits")
        val tapText = if (message.isEmpty()) "Opens your chat with $who." else "To $who: $message"
        return when (launch(view, "Tap to open WhatsApp", tapText, icon)) {
            Launch.STARTED -> {
                val locked = phoneLocked()
                needsTap(
                    when {
                        pkg == null -> "WhatsApp isn't installed, so I opened the chat link in your browser."
                        message.isEmpty() && locked -> "WhatsApp is open on your chat with $who — unlock your phone."
                        message.isEmpty() -> "WhatsApp is open on your chat with $who."
                        locked -> "WhatsApp is ready with your message to $who — unlock your phone and tap send."
                        else -> "WhatsApp is open with your message to $who — tap send."
                    },
                    *extra
                )
            }
            Launch.POSTED -> needsTap("Tap the Willy notification on your phone to open WhatsApp.", *extra)
            Launch.NO_HANDLER -> fail("WhatsApp isn't installed on your phone.")
            Launch.BLOCKED -> fail(BLOCKED_MESSAGE)
        }
    }

    // ---------------------------------------------------------------------- apps

    private class AppEntry(val label: String, val pkg: String, val activity: String, val norm: String)

    @Volatile private var appCache: List<AppEntry>? = null
    @Volatile private var appCacheAt = 0L

    private fun normApp(s: String): String = s.lowercase(Locale.ROOT).filter { isNameChar(it) }

    @Suppress("DEPRECATION")
    private fun launcherApps(fresh: Boolean): List<AppEntry> {
        val now = SystemClock.elapsedRealtime()
        val cached = appCache
        if (!fresh && cached != null && now - appCacheAt < APP_CACHE_MS) return cached
        val main = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val apps = pm.queryIntentActivities(main, 0).mapNotNull { info ->
            val activity = info.activityInfo ?: return@mapNotNull null
            val label = info.loadLabel(pm).toString().trim().ifEmpty { activity.packageName }
            AppEntry(label, activity.packageName, activity.name, normApp(label))
        }
        appCache = apps
        appCacheAt = now
        return apps
    }

    /** exact > starts-with > contains (labels compared without case, spaces or punctuation). */
    private fun findApp(query: String, apps: List<AppEntry>): AppEntry? = findAppRanked(query, apps)?.first

    /** The best match and its rank: 0 exact label or package, 1 starts-with, 2 contains, 3 contained. */
    private fun findAppRanked(query: String, apps: List<AppEntry>): Pair<AppEntry, Int>? {
        val q = normApp(query)
        if (q.isEmpty()) return null
        fun rank(a: AppEntry): Int = when {
            a.norm.isEmpty() -> -1
            a.norm == q || a.pkg.equals(query.trim(), ignoreCase = true) -> 0
            a.norm.startsWith(q) -> 1
            a.norm.contains(q) -> 2
            a.norm.length >= 4 && q.contains(a.norm) -> 3 // "google maps" finds "Maps"
            else -> -1
        }
        return apps.map { it to rank(it) }
            .filter { it.second >= 0 }
            .minWithOrNull(
                compareBy<Pair<AppEntry, Int>>({ it.second }, { it.first.label.length }, { it.first.label.lowercase(Locale.ROOT) })
            )
    }

    private val fillerWords = setOf("the", "app", "application", "my", "on", "phone")

    private fun findAppAnyWording(query: String, apps: List<AppEntry>): AppEntry? = findAppAnyWordingRanked(query, apps)?.first

    private fun withoutFiller(query: String): String =
        query.split(whitespace).filter { it.lowercase(Locale.ROOT) !in fillerWords }.joinToString(" ")

    private fun findAppAnyWordingRanked(query: String, apps: List<AppEntry>): Pair<AppEntry, Int>? {
        findAppRanked(query, apps)?.let { return it }
        val trimmed = withoutFiller(query)
        return if (trimmed.isNotEmpty() && trimmed != query) findAppRanked(trimmed, apps) else null
    }

    /**
     * Everyday names that often don't match a launcher label exactly ("play store" may be
     * "Google Play Store", the camera "OPPO Camera"): the system's own intent for them.
     */
    private fun wellKnownApp(query: String): Intent? {
        val key = normApp(withoutFiller(query).ifEmpty { query })
        fun pkg(p: String): Intent? = pm.getLaunchIntentForPackage(p)
        fun category(c: String): Intent = Intent.makeMainSelectorActivity(Intent.ACTION_MAIN, c)
        val intent = when (key) {
            "camera", "cam", "phonecamera" -> Intent(MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA)
            "playstore", "googleplay", "googleplaystore", "play", "appstore", "store" -> pkg(PLAY_STORE_PACKAGE)
            "settings", "setting", "phonesettings", "systemsettings" -> Intent(Settings.ACTION_SETTINGS)
            "chrome", "googlechrome" -> pkg("com.android.chrome")
            "youtube", "yt" -> pkg("com.google.android.youtube")
            "browser", "internet", "webbrowser" -> category(Intent.CATEGORY_APP_BROWSER)
            "gallery", "photos", "pictures" -> category(Intent.CATEGORY_APP_GALLERY)
            "calculator" -> category(Intent.CATEGORY_APP_CALCULATOR)
            "contacts" -> category(Intent.CATEGORY_APP_CONTACTS)
            "messages", "messaging", "sms", "texts" -> category(Intent.CATEGORY_APP_MESSAGING)
            "email", "mail" -> category(Intent.CATEGORY_APP_EMAIL)
            "maps", "map" -> category(Intent.CATEGORY_APP_MAPS)
            "music" -> category(Intent.CATEGORY_APP_MUSIC)
            "calendar" -> category(Intent.CATEGORY_APP_CALENDAR)
            "files", "filemanager", "myfiles" ->
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) category(Intent.CATEGORY_APP_FILES) else null
            "phone", "dialer", "dialpad" -> Intent(Intent.ACTION_DIAL)
            "clock", "alarms" -> Intent(AlarmClock.ACTION_SHOW_ALARMS)
            else -> null
        } ?: return null
        return intent.takeIf { it.resolveActivity(pm) != null }
    }

    private fun labelFor(intent: Intent, fallback: String): String {
        val fromSystem = try {
            pm.resolveActivity(intent, 0)?.loadLabel(pm)?.toString()?.trim()
        } catch (_: Exception) {
            null
        }
        // A chooser ("android") or an empty label says nothing: use what was asked for.
        return fromSystem?.takeIf { it.isNotEmpty() && !it.equals("android", ignoreCase = true) }
            ?: fallback.trim().replaceFirstChar { it.titlecase(Locale.ROOT) }
    }

    private fun levenshtein(a: String, b: String): Int {
        if (a == b) return 0
        if (a.isEmpty()) return b.length
        if (b.isEmpty()) return a.length
        var prev = IntArray(b.length + 1) { it }
        var cur = IntArray(b.length + 1)
        for (i in 1..a.length) {
            cur[0] = i
            for (j in 1..b.length) {
                val cost = if (a[i - 1] == b[j - 1]) 0 else 1
                cur[j] = min(min(cur[j - 1] + 1, prev[j] + 1), prev[j - 1] + cost)
            }
            val t = prev
            prev = cur
            cur = t
        }
        return prev[b.length]
    }

    /** Up to 3 labels that look like what was asked for ("Spotfy" -> Spotify). */
    private fun closeLabels(query: String, apps: List<AppEntry>): List<String> {
        val q = normApp(query).take(40)
        if (q.isEmpty()) return emptyList()
        val limit = max(2, q.length / 2)
        return apps.asSequence()
            .filter { it.norm.isNotEmpty() }
            .map { app ->
                val norm = app.norm.take(40)
                app to min(levenshtein(q, norm), levenshtein(q, norm.take(q.length)) + 1)
            }
            .filter { it.second <= limit }
            .sortedWith(compareBy<Pair<AppEntry, Int>>({ it.second }, { it.first.label.length }))
            .map { it.first.label }
            .distinct()
            .take(3)
            .toList()
    }

    private fun joinOr(items: List<String>): String = when (items.size) {
        0 -> ""
        1 -> items[0]
        else -> items.dropLast(1).joinToString(", ") + " or " + items.last()
    }

    private fun openApp(p: Map<String, Any?>): Map<String, Any?> {
        val name = p.text("name").ifEmpty { p.text("app") }
        if (name.isEmpty()) return fail("Which app should I open?")
        var apps = launcherApps(fresh = false)
        var hit = findAppAnyWordingRanked(name, apps)
        // An exact label wins; otherwise everyday names ("camera", "play store", "settings") use the system's app.
        if (hit == null || hit.second > 0) {
            wellKnownApp(name)?.let { return startApp(it, labelFor(it, name), null) }
        }
        if (hit == null) { // maybe installed since the list was cached
            apps = launcherApps(fresh = true)
            hit = findAppAnyWordingRanked(name, apps)
        }
        val app = hit?.first
        if (app == null) {
            val close = closeLabels(name, apps)
            return fail(
                if (close.isEmpty()) "There's no app called '$name' on your phone."
                else "There's no app called '$name' on your phone. Did you mean ${joinOr(close)}?"
            )
        }
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
            .setComponent(ComponentName(app.pkg, app.activity))
            .addFlags(Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
        return startApp(intent, app.label, app.pkg)
    }

    private fun startApp(intent: Intent, label: String, pkg: String?): Map<String, Any?> {
        val resolvedPkg = pkg ?: intent.`package` ?: intent.component?.packageName ?: try {
            pm.resolveActivity(intent, 0)?.activityInfo?.packageName
        } catch (_: Exception) {
            null
        }
        val extra = arrayOf<Pair<String, Any?>>("app" to label, "package" to resolvedPkg)
        return when (launch(intent, "Tap to open $label", "You asked Willy to open $label.", android.R.drawable.ic_menu_view)) {
            Launch.STARTED -> ok(
                if (phoneLocked()) "Opening $label — unlock your phone to see it." else "Opening $label.",
                *extra
            )
            Launch.POSTED -> needsTap("Tap the Willy notification on your phone to open $label.", *extra)
            Launch.NO_HANDLER -> fail("$label can't be opened right now.")
            Launch.BLOCKED -> fail(BLOCKED_MESSAGE)
        }
    }

    // ------------------------------------------------ links, Play Store, uninstall, camera

    private fun openUrl(p: Map<String, Any?>): Map<String, Any?> {
        var url = p.text("url").ifEmpty { p.text("link") }
        if (url.isEmpty()) return fail("Which link should I open?")
        if (!url.contains("://")) url = "https://$url"
        val uri = Uri.parse(url)
        val scheme = uri.scheme?.lowercase(Locale.ROOT)
        if (scheme != "http" && scheme != "https") return fail("I can only open web links (http or https) on the phone.")
        val host = uri.host?.takeIf { it.isNotBlank() } ?: return fail("That link doesn't look right.")
        val shownHost = host.lowercase(Locale.ROOT).removePrefix("www.")
        val intent = Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE)
        val extra = arrayOf<Pair<String, Any?>>("url" to url, "host" to shownHost)
        return when (launch(intent, "Tap to open $shownHost", url, android.R.drawable.ic_menu_view)) {
            Launch.STARTED -> ok("Opened $shownHost on your phone.", *extra)
            Launch.POSTED -> needsTap("Tap the Willy notification on your phone to open $shownHost.", *extra)
            Launch.NO_HANDLER -> fail("There's no browser on your phone.")
            Launch.BLOCKED -> fail(BLOCKED_MESSAGE)
        }
    }

    /** Opens the Play Store listing (or a search); the user taps Install themselves. */
    private fun installApp(p: Map<String, Any?>): Map<String, Any?> {
        val name = p.text("name").ifEmpty { p.text("app") }
        val pkg = p.text("package").takeIf { PACKAGE_NAME.matches(it) }
        if (name.isEmpty() && pkg == null) return fail("Which app should I find in the Play Store?")
        val shown = name.ifEmpty { pkg ?: "that app" }
        val market = if (pkg != null) "market://details?id=$pkg" else "market://search?q=${Uri.encode(name)}&c=apps"
        val web = if (pkg != null) {
            "https://play.google.com/store/apps/details?id=$pkg"
        } else {
            "https://play.google.com/store/search?q=${Uri.encode(name)}&c=apps"
        }
        val viaStore = Intent(Intent.ACTION_VIEW, Uri.parse(market)).setPackage(PLAY_STORE_PACKAGE)
        val anyMarket = Intent(Intent.ACTION_VIEW, Uri.parse(market))
        val (intent, onWeb) = when {
            viaStore.resolveActivity(pm) != null -> viaStore to false
            anyMarket.resolveActivity(pm) != null -> anyMarket to false
            else -> Intent(Intent.ACTION_VIEW, Uri.parse(web)).addCategory(Intent.CATEGORY_BROWSABLE) to true
        }
        val installed = pkg != null && isInstalled(ctx, pkg)
        val extra = arrayOf<Pair<String, Any?>>("app" to shown, "package" to pkg, "installed" to installed)
        return when (launch(intent, "Tap to find $shown in the Play Store", "Opens the Play Store — you tap Install.",
            android.R.drawable.stat_sys_download)) {
            Launch.STARTED -> needsTap(
                when {
                    installed -> "$shown is already on your phone — I opened its Play Store page."
                    onWeb -> "Opened $shown on the Play Store website — tap Install."
                    else -> "Opened $shown in the Play Store — tap Install."
                },
                *extra
            )
            Launch.POSTED -> needsTap("Tap the Willy notification on your phone to open $shown in the Play Store.", *extra)
            Launch.NO_HANDLER -> fail("There's no Play Store or browser on your phone.")
            Launch.BLOCKED -> fail(BLOCKED_MESSAGE)
        }
    }

    /** Shows Android's own uninstall confirmation; nothing is removed unless the user confirms. */
    private fun uninstallApp(p: Map<String, Any?>): Map<String, Any?> {
        val name = p.text("name").ifEmpty { p.text("app") }
        val pkgArg = p.text("package")
        if (name.isEmpty() && pkgArg.isEmpty()) return fail("Which app should I uninstall?")
        val apps = launcherApps(fresh = true)
        val byPackage = pkgArg.takeIf { it.isNotEmpty() }?.let { wanted ->
            apps.firstOrNull { it.pkg.equals(wanted, ignoreCase = true) }
        }
        val app = byPackage ?: findAppAnyWording(name.ifEmpty { pkgArg }, apps)
        val pkg: String
        val label: String
        if (app != null) {
            pkg = app.pkg
            label = app.label
        } else if (PACKAGE_NAME.matches(pkgArg) && isInstalledPackage(pkgArg)) {
            pkg = pkgArg
            label = appLabel(pkgArg) ?: pkgArg
        } else {
            val asked = name.ifEmpty { pkgArg }
            val close = closeLabels(asked, apps)
            return fail(
                if (close.isEmpty()) "There's no app called '$asked' on your phone."
                else "There's no app called '$asked' on your phone. Did you mean ${joinOr(close)}?"
            )
        }
        val info = try {
            pm.getApplicationInfo(pkg, 0)
        } catch (_: Exception) {
            null
        }
        if (info != null && (info.flags and ApplicationInfo.FLAG_SYSTEM) != 0) {
            return fail("$label came with the phone, so it can't be uninstalled — you can disable it in Settings > Apps.")
        }
        val intent = Intent(Intent.ACTION_DELETE, Uri.fromParts("package", pkg, null))
        val extra = arrayOf<Pair<String, Any?>>("app" to label, "package" to pkg)
        return when (launch(intent, "Tap to uninstall $label", "Android will ask you to confirm.", android.R.drawable.ic_menu_delete)) {
            Launch.STARTED -> needsTap("Asked to uninstall $label — confirm on your phone.", *extra)
            Launch.POSTED -> needsTap("Tap the Willy notification on your phone, then confirm uninstalling $label.", *extra)
            Launch.NO_HANDLER -> fail("This phone doesn't let apps start an uninstall.")
            Launch.BLOCKED -> fail(BLOCKED_MESSAGE)
        }
    }

    private fun isInstalledPackage(pkg: String): Boolean = try {
        pm.getApplicationInfo(pkg, 0)
        true
    } catch (_: Exception) {
        false
    }

    private fun appLabel(pkg: String): String? = try {
        pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString().trim().takeIf { it.isNotEmpty() }
    } catch (_: Exception) {
        null
    }

    /** Opens the camera ready to shoot; the user takes the photo or video. */
    private fun camera(p: Map<String, Any?>): Map<String, Any?> {
        val mode = p.text("mode").lowercase(Locale.ROOT)
        val video = mode in setOf("video", "videos", "record", "movie")
        val selfie = mode in setOf("selfie", "front", "front_camera", "frontcamera")
        val intent = Intent(if (video) MediaStore.INTENT_ACTION_VIDEO_CAMERA else MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA)
        if (selfie) {
            // Not standardised: the extras common camera apps read to start on the front lens.
            intent.putExtra("android.intent.extras.CAMERA_FACING", 1)
            intent.putExtra("android.intent.extras.LENS_FACING_FRONT", 1)
            intent.putExtra("android.intent.extra.USE_FRONT_CAMERA", true)
            intent.putExtra("com.google.assistant.extra.USE_FRONT_CAMERA", true)
        }
        val target = if (intent.resolveActivity(pm) != null) intent else {
            val app = findApp("camera", launcherApps(fresh = false)) ?: return fail("There's no camera app on your phone.")
            Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER).setComponent(ComponentName(app.pkg, app.activity))
        }
        val what = if (video) "video" else if (selfie) "selfie" else "photo"
        val extra = arrayOf<Pair<String, Any?>>("mode" to what)
        return when (launch(target, "Tap to open the camera", "Ready for a $what.", android.R.drawable.ic_menu_camera)) {
            Launch.STARTED -> needsTap(
                if (phoneLocked()) "Camera is open on your phone — unlock it if it asks." else "Camera is open on your phone.",
                *extra
            )
            Launch.POSTED -> needsTap("Tap the Willy notification on your phone to open the camera.", *extra)
            Launch.NO_HANDLER -> fail("There's no camera app on your phone.")
            Launch.BLOCKED -> fail(BLOCKED_MESSAGE)
        }
    }

    // ------------------------------------------------------------ alarms & timers

    private fun clockTime(hour: Int, minute: Int): String {
        val h12 = if (hour % 12 == 0) 12 else hour % 12
        return "$h12:${minute.toString().padStart(2, '0')} ${if (hour < 12) "AM" else "PM"}"
    }

    private fun plural(n: Int, unit: String) = if (n == 1) "1 $unit" else "$n ${unit}s"

    /** 600 -> "10 minutes", 5400 -> "1 hour 30 minutes". */
    private fun spokenDuration(total: Int): String {
        val parts = mutableListOf<String>()
        val h = total / 3600
        val m = (total % 3600) / 60
        val s = total % 60
        if (h > 0) parts.add(plural(h, "hour"))
        if (m > 0) parts.add(plural(m, "minute"))
        if (s > 0) parts.add(plural(s, "second"))
        return parts.joinToString(" ")
    }

    private fun alarm(p: Map<String, Any?>): Map<String, Any?> {
        val hour = p.int("hour")
        val minute = p.int("minute") ?: 0
        if (hour == null || hour !in 0..23 || minute !in 0..59) {
            return fail("Tell me a time for the alarm, like 7:30 AM.")
        }
        val label = p.text("label")
        val time = clockTime(hour, minute)
        val intent = Intent(AlarmClock.ACTION_SET_ALARM)
            .putExtra(AlarmClock.EXTRA_HOUR, hour)
            .putExtra(AlarmClock.EXTRA_MINUTES, minute)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
        if (label.isNotEmpty()) intent.putExtra(AlarmClock.EXTRA_MESSAGE, label)
        val extra = arrayOf<Pair<String, Any?>>("hour" to hour, "minute" to minute)
        return when (launch(intent, "Tap to set your $time alarm", label.ifEmpty { "Alarm for $time" }, android.R.drawable.ic_lock_idle_alarm)) {
            Launch.STARTED -> ok("Alarm set for $time on your phone.", *extra)
            Launch.POSTED -> needsTap("Tap the Willy notification on your phone to set the $time alarm.", *extra)
            Launch.NO_HANDLER -> fail("The phone's clock app doesn't accept alarms from other apps.")
            Launch.BLOCKED -> fail(BLOCKED_MESSAGE)
        }
    }

    private fun timer(p: Map<String, Any?>): Map<String, Any?> {
        val seconds = p.int("seconds") ?: p.int("minutes")?.let { it * 60 }
        if (seconds == null || seconds <= 0) return fail("How long should the timer be?")
        if (seconds > 24 * 3600) return fail("Timers can be up to 24 hours.")
        val label = p.text("label")
        val length = spokenDuration(seconds)
        val intent = Intent(AlarmClock.ACTION_SET_TIMER)
            .putExtra(AlarmClock.EXTRA_LENGTH, seconds)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
        if (label.isNotEmpty()) intent.putExtra(AlarmClock.EXTRA_MESSAGE, label)
        return when (launch(intent, "Tap to start a timer for $length", label.ifEmpty { "Timer for $length" }, android.R.drawable.ic_lock_idle_alarm)) {
            Launch.STARTED -> ok("Timer set for $length on your phone.", "seconds" to seconds)
            Launch.POSTED -> needsTap("Tap the Willy notification on your phone to start the timer for $length.", "seconds" to seconds)
            Launch.NO_HANDLER -> fail("The phone's clock app doesn't accept timers from other apps.")
            Launch.BLOCKED -> fail(BLOCKED_MESSAGE)
        }
    }

    // --------------------------------------------------------------- volume & media

    private fun volume(p: Map<String, Any?>): Map<String, Any?> {
        val am = ctx.getSystemService(AudioManager::class.java) ?: return fail("The phone's volume controls aren't available.")
        val (stream, label) = when (p.text("stream").lowercase(Locale.ROOT)) {
            "", "media", "music" -> AudioManager.STREAM_MUSIC to "media"
            "ring", "ringer", "ringtone" -> AudioManager.STREAM_RING to "ring"
            "alarm", "alarms" -> AudioManager.STREAM_ALARM to "alarm"
            else -> return fail("I can change the media, ring or alarm volume on the phone.")
        }
        val op = p.text("action").lowercase(Locale.ROOT)
        val level = p.int("level")
        val flags = if (p["quiet"] == true) 0 else AudioManager.FLAG_SHOW_UI
        val maxIndex = am.getStreamMaxVolume(stream).coerceAtLeast(1)
        fun percent(): Int =
            if (am.isStreamMute(stream)) 0 else (am.getStreamVolume(stream) * 100.0 / maxIndex).roundToInt().coerceIn(0, 100)

        return try {
            val message = when {
                level != null -> {
                    val target = (level.coerceIn(0, 100) * maxIndex / 100.0).roundToInt()
                    if (target > 0 && am.isStreamMute(stream)) am.adjustStreamVolume(stream, AudioManager.ADJUST_UNMUTE, 0)
                    am.setStreamVolume(stream, target, flags)
                    "Phone $label volume set to ${percent()}%."
                }
                op == "mute" -> {
                    am.adjustStreamVolume(stream, AudioManager.ADJUST_MUTE, flags)
                    "Phone $label volume muted."
                }
                op == "unmute" -> {
                    am.adjustStreamVolume(stream, AudioManager.ADJUST_UNMUTE, flags)
                    // Unmuting a stream that sits at 0 would still be silent.
                    if (am.getStreamVolume(stream) == 0) {
                        am.setStreamVolume(stream, max(1, (maxIndex * 0.3).roundToInt()), flags)
                    }
                    "Phone $label volume unmuted, at ${percent()}%."
                }
                op == "up" || op == "down" || op == "raise" || op == "lower" -> {
                    val up = op == "up" || op == "raise"
                    val step = max(1, (maxIndex * 0.1).roundToInt())
                    if (up && am.isStreamMute(stream)) am.adjustStreamVolume(stream, AudioManager.ADJUST_UNMUTE, 0)
                    val current = am.getStreamVolume(stream)
                    am.setStreamVolume(stream, (if (up) current + step else current - step).coerceIn(0, maxIndex), flags)
                    "Phone $label volume ${if (up) "up" else "down"} to ${percent()}%."
                }
                else -> return fail("Tell me a volume from 0 to 100, or mute, unmute, up or down.")
            }
            ok(message, "level" to percent(), "stream" to label)
        } catch (e: SecurityException) {
            Log.w(TAG, "Volume change refused: ${e.message}")
            fail("Android blocked changing the $label volume — Do Not Disturb may be on.")
        }
    }

    private fun media(p: Map<String, Any?>): Map<String, Any?> {
        val am = ctx.getSystemService(AudioManager::class.java) ?: return fail("The phone's media controls aren't available.")
        val op = p.text("action").lowercase(Locale.ROOT).replace('-', '_').replace(' ', '_')
        val (code, message) = when (op) {
            "", "play_pause", "playpause", "toggle" -> KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE to
                (if (am.isMusicActive) "Paused playback on your phone." else "Resumed playback on your phone.")
            "play", "resume" -> KeyEvent.KEYCODE_MEDIA_PLAY to "Resumed playback on your phone."
            "pause" -> KeyEvent.KEYCODE_MEDIA_PAUSE to "Paused playback on your phone."
            "next", "skip", "next_track" -> KeyEvent.KEYCODE_MEDIA_NEXT to "Skipped to the next track on your phone."
            "prev", "previous", "back", "previous_track" -> KeyEvent.KEYCODE_MEDIA_PREVIOUS to "Back to the previous track on your phone."
            "stop" -> KeyEvent.KEYCODE_MEDIA_STOP to "Stopped playback on your phone."
            else -> return fail("I can play, pause, skip, go back or stop what's playing on the phone.")
        }
        val now = SystemClock.uptimeMillis()
        am.dispatchMediaKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_DOWN, code, 0))
        am.dispatchMediaKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_UP, code, 0))
        return ok(message)
    }

    // ---------------------------------------------------------------- navigation

    private fun navigate(p: Map<String, Any?>): Map<String, Any?> {
        val destination = p.text("destination").ifEmpty { p.text("to") }
        if (destination.isEmpty()) return fail("Where do you want to go?")
        val (mode, how) = when (p.text("mode").lowercase(Locale.ROOT)) {
            "walking", "walk", "w" -> "w" to "walking"
            "bicycling", "cycling", "bike", "bicycle", "b" -> "b" to "cycling"
            "transit", "bus", "train", "public transport", "r" -> "r" to "transit"
            else -> "d" to "driving"
        }
        val q = Uri.encode(destination)
        // Turn-by-turn has no transit mode, so transit opens Google Maps' transit directions instead.
        val mapsUri = if (mode == "r") {
            "https://www.google.com/maps/dir/?api=1&destination=$q&travelmode=transit"
        } else {
            "google.navigation:q=$q&mode=$mode"
        }
        val maps = Intent(Intent.ACTION_VIEW, Uri.parse(mapsUri)).setPackage(MAPS_PACKAGE)
        val intent = if (maps.resolveActivity(pm) != null) maps else Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=$q"))
        val extra = arrayOf<Pair<String, Any?>>("destination" to destination, "mode" to how)
        return when (launch(intent, "Tap for directions to $destination", "Opens your maps app.", android.R.drawable.ic_menu_directions)) {
            Launch.STARTED -> ok(
                if (mode == "r") "Showing transit directions to $destination on your phone."
                else "Starting $how directions to $destination on your phone.",
                *extra
            )
            Launch.POSTED -> needsTap("Tap the Willy notification on your phone to start directions to $destination.", *extra)
            Launch.NO_HANDLER -> fail("There's no maps app on your phone.")
            Launch.BLOCKED -> fail(BLOCKED_MESSAGE)
        }
    }

    // ---------------------------------------------------- notifications & contacts

    private fun notifications(p: Map<String, Any?>): Map<String, Any?> {
        if (!notificationAccessGranted(ctx)) {
            return fail(
                "Notification access is off — open Willy on the phone > Phone skills to allow it.",
                "access" to false
            )
        }
        if (!WillyNotificationListenerService.connected) requestListenerRebind(ctx)
        val limit = (p.int("limit") ?: 20).coerceIn(1, 50)
        val items = WillyNotificationListenerService.recentNotifications(limit)
        val message = when (items.size) {
            0 -> "No recent notifications on your phone."
            1 -> "You have 1 recent notification on your phone."
            else -> "You have ${items.size} recent notifications on your phone."
        }
        return ok(message, "access" to true, "notifications" to items, "count" to items.size)
    }

    private fun contacts(p: Map<String, Any?>): Map<String, Any?> {
        if (!contactsGranted()) {
            return fail("Contacts access is off — open Willy on the phone > Phone skills to allow it.")
        }
        val query = p.text("query").ifEmpty { p.text("name") }
        val limit = (p.int("limit") ?: 10).coerceIn(1, 50)
        val rows = loadPhoneRows()
        val queryDigits = query.filter { it.isDigit() }
        val mobileFirst: (PhoneRow) -> Int = { if (it.type == Phone.TYPE_MOBILE) 0 else 1 }

        val matches: List<PhoneRow> = when {
            query.isEmpty() -> rows.sortedWith(
                compareBy<PhoneRow>({ if (it.starred) 0 else 1 }, { it.name.lowercase(Locale.ROOT) }, mobileFirst)
            )
            queryDigits.length >= 3 && query.none { it.isLetter() } -> rows
                .filter { it.rawDigits.contains(queryDigits) || it.normDigits.contains(queryDigits) }
                .sortedWith(compareBy<PhoneRow>({ it.name.lowercase(Locale.ROOT) }, mobileFirst))
            else -> {
                val q = normName(query)
                rows.mapNotNull { row -> nameRank(normName(row.name), q).takeIf { it >= 0 }?.let { row to it } }
                    .sortedWith(
                        compareBy<Pair<PhoneRow, Int>>(
                            { it.second },
                            { if (it.first.starred) 0 else 1 },
                            { it.first.name.length },
                            { it.first.name.lowercase(Locale.ROOT) },
                            { mobileFirst(it.first) }
                        )
                    )
                    .map { it.first }
            }
        }

        // One entry per number (contacts often hold the same number twice, e.g. 0412… and +61412…).
        val seen = HashSet<String>()
        val out = ArrayList<Map<String, Any?>>()
        for (row in matches) {
            if (!seen.add("${row.contactId}:${row.rawDigits.takeLast(9)}")) continue
            out.add(mapOf("name" to row.name, "number" to row.number, "type" to typeLabel(row)))
            if (out.size >= limit) break
        }
        val message = when {
            out.isEmpty() && query.isEmpty() -> "There are no contacts with phone numbers on your phone."
            out.isEmpty() -> "No contacts match '$query' on your phone."
            query.isEmpty() -> "Here are ${out.size} of your contacts."
            out.size == 1 -> "Found 1 match for '$query'."
            else -> "Found ${out.size} matches for '$query'."
        }
        return ok(message, "contacts" to out, "count" to out.size)
    }
}
