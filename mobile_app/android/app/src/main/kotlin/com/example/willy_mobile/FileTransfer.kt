package com.example.willy_mobile

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.SystemClock
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.util.Log
import android.webkit.MimeTypeMap
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * Files between the phone and the PC, through the hub. Everything here blocks (network,
 * storage), so callers run it off the UI thread. Bytes are streamed in small buffers, so
 * large files (100 MB+) never sit in memory.
 *
 *   receive -> downloads a hub file into the public Downloads/Willy folder and posts a
 *              "tap to open" notification; returns the contract result map.
 *   upload  -> streams a content:// URI to the hub as multipart/form-data (field "file").
 *
 * [onProgress] gets (transferId, bytesDone, bytesTotal or -1), at most a few times a second.
 */
class FileTransfer(context: Context, private val onProgress: (String, Long, Long) -> Unit) {

    private val ctx: Context = context.applicationContext

    companion object {
        private const val TAG = "WillyFiles"
        const val FILES_CHANNEL = "willy_files"
        const val FOLDER = "Willy"
        private const val SHOWN_DIR = "Download/$FOLDER"
        private const val BUFFER = 64 * 1024
        private const val CONNECT_TIMEOUT_MS = 15_000
        private const val READ_TIMEOUT_MS = 60_000
        private const val PROGRESS_EVERY_MS = 400L
        private const val OCTET = "application/octet-stream"
        private val notificationIds = AtomicInteger(41000 + ((System.currentTimeMillis() / 1000) % 9000).toInt())
    }

    private fun ok(message: String, vararg extra: Pair<String, Any?>): Map<String, Any?> {
        val out = linkedMapOf<String, Any?>("success" to true, "message" to message)
        extra.forEach { out[it.first] = it.second }
        return out
    }

    private fun fail(error: String): Map<String, Any?> = linkedMapOf("success" to false, "error" to error)

    // ------------------------------------------------------------------ describing

    /** Name, size and type of a content:// (or file://) URI, as the Dart side expects. */
    fun describe(uri: Uri): Map<String, Any?> {
        var name: String? = null
        var size = -1L
        try {
            ctx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)
                ?.use { c ->
                    if (c.moveToFirst()) {
                        val iName = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                        val iSize = c.getColumnIndex(OpenableColumns.SIZE)
                        if (iName >= 0) name = c.getString(iName)
                        if (iSize >= 0 && !c.isNull(iSize)) size = c.getLong(iSize)
                    }
                }
        } catch (e: Exception) {
            Log.w(TAG, "describe($uri) failed: ${e.message}")
        }
        val shownName = safeName(name ?: uri.lastPathSegment ?: "file")
        val mime = ctx.contentResolver.getType(uri)?.takeIf { it.isNotBlank() } ?: mimeFromName(shownName) ?: OCTET
        return mapOf("uri" to uri.toString(), "name" to shownName, "size" to size, "mime" to mime)
    }

    private fun extensionOf(name: String): String =
        name.substringAfterLast('.', "").lowercase(Locale.ROOT).takeIf { it.isNotEmpty() && it.length <= 10 } ?: ""

    private fun mimeFromName(name: String): String? =
        extensionOf(name).takeIf { it.isNotEmpty() }?.let { MimeTypeMap.getSingleton().getMimeTypeFromExtension(it) }

    /** A plain file name: no folders, no characters Android or Windows refuse. */
    private fun safeName(raw: String): String {
        val base = raw.replace('\\', '/').substringAfterLast('/')
        val cleaned = base.map { if (it < ' ' || it in "\\/:*?\"<>|") '_' else it }.joinToString("")
            .trim().trimStart('.').trim()
        val short = if (cleaned.length > 150) {
            val ext = extensionOf(cleaned)
            cleaned.take(150 - ext.length - 1).trimEnd() + (if (ext.isNotEmpty()) ".$ext" else "")
        } else cleaned
        return short.ifEmpty { "file" }
    }

    // ------------------------------------------------------------------ receiving

    private class Target(val uri: Uri, val name: String, val out: OutputStream, val finish: () -> Uri, val abort: () -> Unit)

    fun receive(
        url: String,
        token: String?,
        rawName: String,
        hubMime: String?,
        expectedSize: Long,
        from: String,
        transferId: String
    ): Map<String, Any?> {
        val name = safeName(rawName)
        val notifyId = notificationIds.incrementAndGet()
        var conn: HttpURLConnection? = null
        var target: Target? = null
        try {
            val parsed = URL(url)
            if (parsed.protocol != "http" && parsed.protocol != "https") return fail("That file link doesn't look right.")
            conn = (parsed.openConnection() as HttpURLConnection).apply {
                connectTimeout = CONNECT_TIMEOUT_MS
                readTimeout = READ_TIMEOUT_MS
                instanceFollowRedirects = false // never carry the token to another host
                if (!token.isNullOrEmpty()) setRequestProperty("Authorization", "Bearer $token")
                setRequestProperty("X-Willy-Client", "mobile")
            }
            val code = conn.responseCode
            when {
                code == 401 || code == 403 -> return fail("The hub refused the download — check the Remote token in Willy's settings.")
                code == 404 || code == 410 -> return fail("$name is no longer on the hub.")
                code !in 200..299 -> return fail("The hub returned $code for $name.")
            }
            val total = conn.contentLengthLong.takeIf { it > 0 } ?: expectedSize
            if (total > 0 && !enoughSpace(total)) return fail("There isn't enough free space on the phone for $name.")

            // A type that matches the extension keeps MediaStore from renaming the file.
            val nameMime = mimeFromName(name)
            val served = hubMime?.takeIf { it.isNotBlank() && it != OCTET }
                ?: conn.contentType?.substringBefore(';')?.trim()?.takeIf { it.isNotBlank() && it != OCTET }
            val storeMime = nameMime ?: (if (extensionOf(name).isEmpty()) served else null) ?: OCTET
            val viewMime = nameMime ?: served ?: OCTET

            target = createTarget(name, storeMime)
            val filesOn = ensureChannel()
            if (filesOn) progressNotification(notifyId, name, from, 0, total)

            var done = 0L
            var lastTick = 0L
            conn.inputStream.use { input ->
                target.out.use { output ->
                    val buf = ByteArray(BUFFER)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        output.write(buf, 0, n)
                        done += n
                        val now = SystemClock.elapsedRealtime()
                        if (now - lastTick >= PROGRESS_EVERY_MS) {
                            lastTick = now
                            onProgress(transferId, done, total)
                            if (filesOn) progressNotification(notifyId, name, from, done, total)
                        }
                    }
                }
            }
            if (total > 0 && done < total) throw IOException("the download stopped at ${done * 100 / total}%")
            val openUri = target.finish()
            onProgress(transferId, done, if (total > 0) total else done)
            if (filesOn) doneNotification(notifyId, target.name, from, openUri, viewMime)
            return ok(
                "Saved ${target.name} to $SHOWN_DIR on your phone.",
                "path" to "$SHOWN_DIR/${target.name}",
                "name" to target.name,
                "size" to done,
                "uri" to openUri.toString()
            )
        } catch (e: SecurityException) {
            Log.w(TAG, "receive refused: ${e.message}")
            target?.abort()
            cancelNotification(notifyId)
            return fail("Android didn't let Willy save $name to Downloads.")
        } catch (e: NeedsStorageAccess) {
            return fail("Saving to Downloads on this Android version needs Storage access — allow it for Willy in App info.")
        } catch (e: Exception) {
            Log.w(TAG, "receive failed", e)
            target?.abort()
            cancelNotification(notifyId)
            return fail("Couldn't download $name to the phone (${e.message ?: e.javaClass.simpleName}).")
        } finally {
            conn?.disconnect()
        }
    }

    private class NeedsStorageAccess : Exception()

    private fun enoughSpace(bytes: Long): Boolean = try {
        @Suppress("DEPRECATION")
        val dir = Environment.getExternalStorageDirectory()
        dir.usableSpace <= 0 || dir.usableSpace > bytes + 20L * 1024 * 1024
    } catch (_: Exception) {
        true
    }

    private fun createTarget(name: String, mime: String): Target =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) mediaStoreTarget(name, mime) else legacyTarget(name, mime)

    /** Android 10+: MediaStore.Downloads, no storage permission needed. */
    private fun mediaStoreTarget(name: String, mime: String): Target {
        val resolver = ctx.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, name)
            put(MediaStore.MediaColumns.MIME_TYPE, mime)
            put(MediaStore.MediaColumns.RELATIVE_PATH, "${Environment.DIRECTORY_DOWNLOADS}/$FOLDER")
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: throw IOException("Downloads refused the new file")
        val out = try {
            resolver.openOutputStream(uri, "w") ?: throw IOException("can't write to Downloads")
        } catch (e: Exception) {
            try { resolver.delete(uri, null, null) } catch (_: Exception) {}
            throw e
        }
        // MediaStore adds " (1)" when the name is taken: report the name it really used.
        val actual = try {
            resolver.query(uri, arrayOf(MediaStore.MediaColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getString(0) else null
            }
        } catch (_: Exception) {
            null
        } ?: name
        return Target(
            uri, actual, out,
            finish = {
                val done = ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }
                resolver.update(uri, done, null, null)
                uri
            },
            abort = {
                try { out.close() } catch (_: Exception) {}
                try { resolver.delete(uri, null, null) } catch (_: Exception) {}
            }
        )
    }

    /** Android 9 and older: the public Downloads folder, which needs the storage permission. */
    private fun legacyTarget(name: String, mime: String): Target {
        if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
            throw NeedsStorageAccess()
        }
        @Suppress("DEPRECATION")
        val dir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), FOLDER)
        if (!dir.exists() && !dir.mkdirs()) throw IOException("can't create Downloads/$FOLDER")
        var file = File(dir, name)
        val ext = extensionOf(name)
        val stem = if (ext.isNotEmpty()) name.dropLast(ext.length + 1) else name
        var i = 1
        while (file.exists()) {
            file = File(dir, if (ext.isNotEmpty()) "$stem ($i).$ext" else "$stem ($i)")
            i++
        }
        val out = FileOutputStream(file)
        return Target(
            Uri.fromFile(file), file.name, out,
            finish = { scan(file, mime) ?: Uri.fromFile(file) },
            abort = {
                try { out.close() } catch (_: Exception) {}
                file.delete()
            }
        )
    }

    /** Registers a file with MediaStore and returns its content:// URI (openable by other apps). */
    private fun scan(file: File, mime: String): Uri? {
        val latch = CountDownLatch(1)
        var result: Uri? = null
        MediaScannerConnection.scanFile(ctx, arrayOf(file.absolutePath), arrayOf(mime)) { _, uri ->
            result = uri
            latch.countDown()
        }
        latch.await(5, TimeUnit.SECONDS)
        return result
    }

    // ------------------------------------------------------------------ gallery

    /** Copies a photo the app took (a file in its cache) into Pictures/Willy, so it shows in the gallery. */
    fun saveImageToGallery(path: String, rawName: String, mime: String?): Map<String, Any?> {
        val src = File(path)
        if (!src.isFile) return fail("The photo is gone — take it again.")
        val name = safeName(rawName)
        val type = mime?.takeIf { it.isNotBlank() } ?: mimeFromName(name) ?: "image/jpeg"
        val shown = "${Environment.DIRECTORY_PICTURES}/$FOLDER"
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val resolver = ctx.contentResolver
                val values = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, name)
                    put(MediaStore.MediaColumns.MIME_TYPE, type)
                    put(MediaStore.MediaColumns.RELATIVE_PATH, shown)
                    put(MediaStore.MediaColumns.IS_PENDING, 1)
                }
                val uri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
                    ?: return fail("The gallery refused the photo.")
                try {
                    val out = resolver.openOutputStream(uri, "w") ?: throw IOException("can't write to the gallery")
                    out.use { o -> src.inputStream().use { it.copyTo(o, BUFFER) } }
                    resolver.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
                } catch (e: Exception) {
                    try { resolver.delete(uri, null, null) } catch (_: Exception) {}
                    throw e
                }
                ok("Saved the photo to $shown.", "uri" to uri.toString())
            } else {
                if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
                    return fail("Saving photos on this Android version needs Storage access — allow it for Willy in App info.")
                }
                @Suppress("DEPRECATION")
                val dir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_PICTURES), FOLDER)
                if (!dir.exists() && !dir.mkdirs()) throw IOException("can't create $shown")
                var dest = File(dir, name)
                val ext = extensionOf(name)
                val stem = if (ext.isNotEmpty()) name.dropLast(ext.length + 1) else name
                var i = 1
                while (dest.exists()) {
                    dest = File(dir, if (ext.isNotEmpty()) "$stem ($i).$ext" else "$stem ($i)")
                    i++
                }
                src.copyTo(dest)
                scan(dest, type)
                ok("Saved the photo to $shown.", "path" to dest.absolutePath)
            }
        } catch (e: SecurityException) {
            Log.w(TAG, "gallery save refused: ${e.message}")
            fail("Android didn't let Willy save the photo.")
        } catch (e: Exception) {
            Log.w(TAG, "gallery save failed", e)
            fail("Couldn't save the photo (${e.message ?: e.javaClass.simpleName}).")
        }
    }

    // ------------------------------------------------------------------ notifications

    private fun ensureChannel(): Boolean {
        return try {
            if (!NotificationManagerCompat.from(ctx).areNotificationsEnabled()) return false
            val nm = ctx.getSystemService(NotificationManager::class.java) ?: return false
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && nm.getNotificationChannel(FILES_CHANNEL) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(FILES_CHANNEL, "Files from your PC", NotificationManager.IMPORTANCE_DEFAULT).apply {
                        description = "Files your PC sends to this phone through Willy"
                    }
                )
            }
            true
        } catch (e: Exception) {
            Log.w(TAG, "files channel: ${e.message}")
            false
        }
    }

    private fun post(id: Int, n: android.app.Notification) {
        try {
            ctx.getSystemService(NotificationManager::class.java)?.notify(id, n)
        } catch (e: SecurityException) {
            Log.w(TAG, "notify refused: ${e.message}")
        }
    }

    private fun cancelNotification(id: Int) {
        try {
            ctx.getSystemService(NotificationManager::class.java)?.cancel(id)
        } catch (_: Exception) {
        }
    }

    private fun progressNotification(id: Int, name: String, from: String, done: Long, total: Long) {
        val pct = if (total > 0) (done * 100 / total).toInt().coerceIn(0, 100) else 0
        post(
            id,
            NotificationCompat.Builder(ctx, FILES_CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentTitle("Receiving $name")
                .setContentText(if (total > 0) "From $from · $pct%" else "From $from")
                .setProgress(100, pct, total <= 0)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setSilent(true)
                .build()
        )
    }

    private fun doneNotification(id: Int, name: String, from: String, uri: Uri, mime: String) {
        val view = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, mime)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        val tap = PendingIntent.getActivity(ctx, id, view, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        post(
            id,
            NotificationCompat.Builder(ctx, FILES_CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_download_done)
                .setContentTitle("File from $from: $name")
                .setContentText("Saved to $SHOWN_DIR · tap to open")
                .setPriority(NotificationCompat.PRIORITY_DEFAULT)
                .setAutoCancel(true)
                .setOngoing(false)
                .setContentIntent(tap)
                .build()
        )
    }

    // ------------------------------------------------------------------ sending

    /**
     * Streams [uriString] to [url] as multipart/form-data. Returns {"status": http code, "body": text}
     * or {"status": -1, "error": reason}; the Dart side interprets the hub's JSON.
     */
    fun upload(
        uriString: String,
        url: String,
        token: String?,
        rawName: String,
        mime: String?,
        knownSize: Long,
        transferId: String
    ): Map<String, Any?> {
        val uri = Uri.parse(uriString)
        val name = safeName(rawName)
        val type = mime?.takeIf { it.isNotBlank() } ?: mimeFromName(name) ?: OCTET
        var conn: HttpURLConnection? = null
        try {
            val size = if (knownSize >= 0) knownSize else lengthOf(uri)
            val boundary = "----WillyUpload" + UUID.randomUUID().toString().replace("-", "")
            // Starlette decodes the filename as UTF-8; quotes and line breaks can't appear in it.
            val quoted = name.replace("\"", "%22").replace("\r", "").replace("\n", "")
            val head = ("--$boundary\r\n" +
                "Content-Disposition: form-data; name=\"file\"; filename=\"$quoted\"\r\n" +
                "Content-Type: $type\r\n\r\n").toByteArray(Charsets.UTF_8)
            val tail = "\r\n--$boundary--\r\n".toByteArray(Charsets.UTF_8)

            val input: InputStream = ctx.contentResolver.openInputStream(uri)
                ?: return mapOf("status" to -1, "error" to "The phone couldn't open $name.")
            input.use { source ->
                conn = (URL(url).openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    doOutput = true
                    connectTimeout = CONNECT_TIMEOUT_MS
                    readTimeout = READ_TIMEOUT_MS
                    instanceFollowRedirects = false
                    useCaches = false
                    setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
                    setRequestProperty("Accept", "application/json")
                    setRequestProperty("X-Willy-Client", "mobile")
                    if (!token.isNullOrEmpty()) setRequestProperty("Authorization", "Bearer $token")
                    if (size >= 0) setFixedLengthStreamingMode(head.size + size + tail.size) else setChunkedStreamingMode(BUFFER)
                }
                val c = conn!!
                c.outputStream.use { out ->
                    out.write(head)
                    val buf = ByteArray(BUFFER)
                    var done = 0L
                    var lastTick = 0L
                    while (true) {
                        val n = source.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        done += n
                        val now = SystemClock.elapsedRealtime()
                        if (now - lastTick >= PROGRESS_EVERY_MS) {
                            lastTick = now
                            onProgress(transferId, done, size)
                        }
                    }
                    out.write(tail)
                    onProgress(transferId, done, if (size >= 0) size else done)
                }
            }
            val c = conn!!
            val status = c.responseCode
            val stream = if (status in 200..299) c.inputStream else c.errorStream
            val body = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            return mapOf("status" to status, "body" to body)
        } catch (e: SecurityException) {
            Log.w(TAG, "upload refused: ${e.message}")
            return mapOf("status" to -1, "error" to "Android didn't let Willy read $name.")
        } catch (e: Exception) {
            Log.w(TAG, "upload failed", e)
            return mapOf("status" to -1, "error" to "Couldn't send $name to the hub (${e.message ?: e.javaClass.simpleName}).")
        } finally {
            conn?.disconnect()
        }
    }

    private fun lengthOf(uri: Uri): Long = try {
        ctx.contentResolver.openAssetFileDescriptor(uri, "r")?.use { it.length } ?: -1L
    } catch (_: Exception) {
        -1L
    }
}
