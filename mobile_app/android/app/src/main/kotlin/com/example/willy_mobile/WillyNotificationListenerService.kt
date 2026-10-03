package com.example.willy_mobile

import android.app.Notification
import android.content.pm.PackageManager
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log

class WillyNotificationListenerService : NotificationListenerService() {

    /** One posted notification, as read back by the `phone_notifications` phone skill. */
    data class RecentNotification(
        val key: String,
        val app: String,
        val pkg: String,
        val title: String,
        val text: String,
        val time: Long
    )

    companion object {
        private const val TAG = "WillyNotifs"
        private const val MAX_RECENT = 50
        private const val DUPLICATE_WINDOW_MS = 30 * 60 * 1000L

        var unreadEmailsCount: Int = 0
        var emailSenders: MutableList<String> = mutableListOf()
        var whatsappUnreadCount: Int = 0
        var whatsappSenders: MutableList<String> = mutableListOf()
        var totalNotificationsCount: Int = 0
        var otherNotifications: MutableList<String> = mutableListOf()

        /** True while Android has the listener bound (access granted and service running). */
        @Volatile var connected: Boolean = false
            private set

        // Ring buffer of the last posted notifications, oldest first. Guarded by recentLock:
        // the listener writes on the main thread, phone skills read on a worker thread.
        private val recentLock = Any()
        private val recent = ArrayDeque<RecentNotification>()

        fun reset() {
            unreadEmailsCount = 0
            emailSenders.clear()
            whatsappUnreadCount = 0
            whatsappSenders.clear()
            totalNotificationsCount = 0
            otherNotifications.clear()
        }

        /** Newest first, as maps ready for the method channel. */
        fun recentNotifications(limit: Int): List<Map<String, Any>> = synchronized(recentLock) {
            recent.sortedByDescending { it.time }.take(limit.coerceAtLeast(0)).map {
                mapOf(
                    "app" to it.app,
                    "package" to it.pkg,
                    "title" to it.title,
                    "text" to it.text,
                    "time" to it.time
                )
            }
        }

        private fun add(entry: RecentNotification) = synchronized(recentLock) {
            // Apps often re-post the same content (updates, reconnects): keep one copy.
            val duplicate = recent.any {
                it.pkg == entry.pkg && it.title == entry.title && it.text == entry.text &&
                    (it.key == entry.key || kotlin.math.abs(it.time - entry.time) < DUPLICATE_WINDOW_MS)
            }
            if (!duplicate) {
                recent.addLast(entry)
                while (recent.size > MAX_RECENT) recent.removeFirst()
            }
        }
    }

    private val labelCache = HashMap<String, String>()

    override fun onListenerConnected() {
        super.onListenerConnected()
        connected = true
        seedRecent()
        refreshNotifications()
    }

    override fun onListenerDisconnected() {
        connected = false
        super.onListenerDisconnected()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        super.onNotificationPosted(sbn)
        remember(sbn)
        refreshNotifications()
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification?) {
        super.onNotificationRemoved(sbn)
        refreshNotifications()
    }

    /** After a (re)connect, the shade's current notifications are the best "recent" history. */
    private fun seedRecent() {
        try {
            val active = activeNotifications ?: return
            active.sortedBy { it.postTime }.forEach { remember(it) }
        } catch (e: Exception) {
            Log.w(TAG, "seedRecent failed: ${e.message}")
        }
    }

    private fun remember(sbn: StatusBarNotification?) {
        try {
            if (sbn == null) return
            val pkg = sbn.packageName ?: return
            if (pkg == packageName) return
            val n = sbn.notification ?: return
            if (sbn.isOngoing) return
            val skipFlags = Notification.FLAG_ONGOING_EVENT or Notification.FLAG_FOREGROUND_SERVICE or
                Notification.FLAG_GROUP_SUMMARY
            if (n.flags and skipFlags != 0) return

            val extras = n.extras
            val title = (extras?.getCharSequence(Notification.EXTRA_TITLE)
                ?: extras?.getCharSequence(Notification.EXTRA_TITLE_BIG))?.toString()?.trim().orEmpty()
            var text = (extras?.getCharSequence(Notification.EXTRA_BIG_TEXT)
                ?: extras?.getCharSequence(Notification.EXTRA_TEXT))?.toString()?.trim().orEmpty()
            if (text.isEmpty()) {
                text = extras?.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)
                    ?.lastOrNull()?.toString()?.trim().orEmpty()
            }
            if (title.isEmpty() && text.isEmpty()) return

            add(
                RecentNotification(
                    key = sbn.key ?: "",
                    app = appLabel(pkg),
                    pkg = pkg,
                    title = title.take(200),
                    text = text.take(600),
                    time = sbn.postTime
                )
            )
        } catch (e: Exception) {
            Log.w(TAG, "remember failed: ${e.message}")
        }
    }

    private fun appLabel(pkg: String): String {
        labelCache[pkg]?.let { return it }
        val label = try {
            @Suppress("DEPRECATION")
            val info = packageManager.getApplicationInfo(pkg, 0)
            packageManager.getApplicationLabel(info).toString()
        } catch (_: PackageManager.NameNotFoundException) {
            pkg.substringAfterLast('.').replaceFirstChar { it.uppercase() }
        } catch (_: Exception) {
            pkg
        }
        labelCache[pkg] = label
        return label
    }

    private fun refreshNotifications() {
        try {
            val active = activeNotifications ?: return
            reset()
            totalNotificationsCount = active.size

            for (notif in active) {
                val pkg = notif.packageName ?: ""
                val extras = notif.notification?.extras
                val title = extras?.getCharSequence("android.title")?.toString() ?: ""
                val text = extras?.getCharSequence("android.text")?.toString() ?: ""

                // 1. WhatsApp
                if (pkg.contains("whatsapp", ignoreCase = true)) {
                    whatsappUnreadCount++
                    if (title.isNotEmpty() && !whatsappSenders.contains(title)) {
                        whatsappSenders.add(title)
                    }
                }
                // 2. Emails (Gmail, Outlook, Yahoo, Mail)
                else if (pkg.contains("gm", ignoreCase = true) ||
                         pkg.contains("email", ignoreCase = true) ||
                         pkg.contains("outlook", ignoreCase = true) ||
                         pkg.contains("mail", ignoreCase = true)) {
                    unreadEmailsCount++
                    if (title.isNotEmpty() && !emailSenders.contains(title)) {
                        emailSenders.add(title)
                    }
                } else {
                    if (title.isNotEmpty()) {
                        otherNotifications.add("$title: $text")
                    }
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }
}
