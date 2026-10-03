package com.example.willy_mobile

import android.app.Activity
import android.content.ClipData
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.Log

/**
 * "Willy" in the Android share sheet. Invisible: it hands what was shared (files, or a
 * link / text) to the running Willy screen and closes, so sharing never starts a second
 * copy of the app inside the other app's task.
 */
class ShareReceiverActivity : Activity() {

    companion object {
        const val ACTION_SHARE_TO_PC = "com.example.willy_mobile.SHARE_TO_PC"
        const val EXTRA_URIS = "willy_shared_uris"
        const val EXTRA_TEXT = "willy_shared_text"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        try {
            forward(intent)
        } catch (e: Exception) {
            Log.w("WillyShare", "share failed: ${e.message}")
        }
        finish()
    }

    @Suppress("DEPRECATION")
    private fun streams(source: Intent): ArrayList<Uri> {
        val out = ArrayList<Uri>()
        when (source.action) {
            Intent.ACTION_SEND -> {
                val one: Uri? = if (Build.VERSION.SDK_INT >= 33) {
                    source.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
                } else {
                    source.getParcelableExtra(Intent.EXTRA_STREAM)
                }
                one?.let { out.add(it) }
            }
            Intent.ACTION_SEND_MULTIPLE -> {
                val many: List<Uri>? = if (Build.VERSION.SDK_INT >= 33) {
                    source.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
                } else {
                    source.getParcelableArrayListExtra(Intent.EXTRA_STREAM)
                }
                many?.let { out.addAll(it) }
            }
        }
        // Some apps only put the files in the clip data.
        if (out.isEmpty()) {
            val clip = source.clipData
            if (clip != null) for (i in 0 until clip.itemCount) clip.getItemAt(i).uri?.let { out.add(it) }
        }
        // Never forward Willy's own private files.
        return ArrayList(out.distinct().filter { it.scheme == "content" || it.scheme == "file" }
            .filter { !(it.authority ?: "").startsWith(packageName) })
    }

    private fun forward(source: Intent) {
        val uris = streams(source)
        val text = source.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()?.trim()?.takeIf { it.isNotEmpty() }
        if (uris.isEmpty() && text == null) return
        val target = Intent(this, MainActivity::class.java)
            .setAction(ACTION_SHARE_TO_PC)
            .putParcelableArrayListExtra(EXTRA_URIS, uris)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        if (text != null && uris.isEmpty()) target.putExtra(EXTRA_TEXT, text)
        if (uris.isNotEmpty()) {
            // Passes this activity's read access on to Willy's main screen.
            val clip = ClipData.newRawUri("shared", uris[0])
            for (i in 1 until uris.size) clip.addItem(ClipData.Item(uris[i]))
            target.clipData = clip
            target.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        startActivity(target)
    }
}
