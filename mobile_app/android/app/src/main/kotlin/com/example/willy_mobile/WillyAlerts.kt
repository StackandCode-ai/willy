package com.example.willy_mobile

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import android.util.Log

/**
 * The "willy_alerts" notification channel: reminders, alarms and notes from Willy, shown by the
 * app itself and by Firebase Cloud Messaging (AndroidManifest names it the FCM default channel).
 * Created at startup so pushes that arrive while Willy is in the background land on it too.
 */
object WillyAlerts {
    const val CHANNEL_ID = "willy_alerts"

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        try {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            // Android keeps a channel's sound/vibration once created; this only matters on first creation.
            if (nm.getNotificationChannel(CHANNEL_ID) != null) return
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Willy alerts", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Reminders, alarms and notes from Willy"
                    enableVibration(true)
                    vibrationPattern = longArrayOf(0, 400, 200, 400)
                    setSound(
                        RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION),
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_NOTIFICATION)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                            .build()
                    )
                }
            )
        } catch (e: Exception) {
            Log.e("WillyAlerts", "creating the alerts channel failed: ${e.message}")
        }
    }
}
