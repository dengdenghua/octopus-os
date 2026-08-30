@file:Suppress("MagicNumber") // Android API levels, HTTP classes and bounded byte sizes are domain literals.

package com.apk.claw.android.sync

import android.Manifest
import android.content.ContentResolver
import android.content.ContentUris
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.DocumentsContract
import android.provider.MediaStore
import android.provider.OpenableColumns
import androidx.core.content.ContextCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.apk.claw.android.tentacle.DeviceRegistration
import com.apk.claw.android.utils.KVUtils
import com.apk.claw.android.utils.OctoHttp
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.CertificatePinner
import java.io.IOException
import java.io.InputStream
import java.net.URI
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/**
 * WorkManager adapter for Echo device-sync v1.
 *
 * It is deliberately incremental and bounded: one run uploads at most
 * [MAX_PHOTOS_PER_RUN] MediaStore rows, persists the last successful row, and
 * lets WorkManager continue later. The server remains authoritative for upload
 * offsets and idempotency, so process death never requires a local upload DB.
 * User-selected SAF files are re-preflighted each run; their SHA is cached only
 * while size and provider mtime stay unchanged.
 */
@Suppress("TooManyFunctions") // Worker keeps content-provider I/O and scheduling in one lifecycle boundary.
class EchoDeviceSyncWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    private val resolver = applicationContext.contentResolver
    private val gson = Gson()

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val baseUrl = KVUtils.getString(KEY_BASE_URL, "").trim()
        val token = KVUtils.getEchoAuthToken().trim()
        if (baseUrl.isBlank() || token.isBlank()) return@withContext failure("not-paired")

        val deviceId = DeviceRegistration(applicationContext).deviceId
        val client = runCatching { createClient(baseUrl, deviceId, token) }
            .getOrElse { return@withContext failure("invalid-sync-origin") }

        try {
            val status = client.status()
            if (KVUtils.getBoolean(KEY_PHOTOS_ENABLED, false)) {
                if ("photos" !in status.grantedScopes) return@withContext failure("photos-not-granted")
                if (!hasPhotoPermission()) return@withContext failure("photos-permission-required")
                uploadPhotoBatch(client)
            }
            if (KVUtils.getBoolean(KEY_FILES_ENABLED, false)) {
                if ("files" !in status.grantedScopes) return@withContext failure("files-not-granted")
                uploadSelectedFiles(client)
            }
            Result.success()
        } catch (failure: DeviceSyncClient.HttpFailure) {
            if (failure.permanent || failure.statusCode in 400..499) {
                failure("http-${failure.statusCode}")
            } else {
                Result.retry()
            }
        } catch (_: SecurityException) {
            failure("content-permission-required")
        } catch (_: IOException) {
            Result.retry()
        }
    }

    private fun createClient(baseUrl: String, deviceId: String, token: String): DeviceSyncClient {
        val builder = OctoHttp.shared.newBuilder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
        val pin = KVUtils.getString(KEY_CERT_PIN, "").trim()
        val host = URI(baseUrl).host
        if (pin.isNotBlank() && !host.isNullOrBlank()) {
            builder.certificatePinner(CertificatePinner.Builder().add(host, pin).build())
        }
        return DeviceSyncClient(baseUrl, deviceId, token, builder.build(), gson)
    }

    @Suppress("LongMethod") // Cursor columns must stay adjacent to their guarded row processing.
    private fun uploadPhotoBatch(client: DeviceSyncClient) {
        val cursorModified = KVUtils.getLong(KEY_PHOTO_CURSOR_MODIFIED, 0L)
        val cursorId = KVUtils.getLong(KEY_PHOTO_CURSOR_ID, 0L)
        val projection = mutableListOf(
            MediaStore.Images.Media._ID,
            MediaStore.Images.Media.DISPLAY_NAME,
            MediaStore.Images.Media.SIZE,
            MediaStore.Images.Media.DATE_MODIFIED,
        ).apply {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                add(MediaStore.Images.Media.RELATIVE_PATH)
            }
        }.toTypedArray()
        val selection = "(${MediaStore.Images.Media.DATE_MODIFIED} > ?) OR " +
            "(${MediaStore.Images.Media.DATE_MODIFIED} = ? AND ${MediaStore.Images.Media._ID} > ?)"
        val selectionArgs = arrayOf(
            cursorModified.toString(),
            cursorModified.toString(),
            cursorId.toString(),
        )
        val queryArgs = Bundle().apply {
            putString(ContentResolver.QUERY_ARG_SQL_SELECTION, selection)
            putStringArray(ContentResolver.QUERY_ARG_SQL_SELECTION_ARGS, selectionArgs)
            putStringArray(
                ContentResolver.QUERY_ARG_SORT_COLUMNS,
                arrayOf(MediaStore.Images.Media.DATE_MODIFIED, MediaStore.Images.Media._ID),
            )
            putInt(
                ContentResolver.QUERY_ARG_SORT_DIRECTION,
                ContentResolver.QUERY_SORT_DIRECTION_ASCENDING,
            )
            putInt(ContentResolver.QUERY_ARG_LIMIT, MAX_PHOTOS_PER_RUN)
        }
        val rows = resolver.query(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            projection,
            queryArgs,
            null,
        ) ?: throw IOException("MediaStore photo query is unavailable")
        rows.use {
            val idIndex = rows.getColumnIndexOrThrow(MediaStore.Images.Media._ID)
            val nameIndex = rows.getColumnIndexOrThrow(MediaStore.Images.Media.DISPLAY_NAME)
            val pathIndex = rows.getColumnIndex(MediaStore.Images.Media.RELATIVE_PATH)
            val sizeIndex = rows.getColumnIndexOrThrow(MediaStore.Images.Media.SIZE)
            val modifiedIndex = rows.getColumnIndexOrThrow(MediaStore.Images.Media.DATE_MODIFIED)
            while (rows.moveToNext()) {
                val id = rows.getLong(idIndex)
                val modifiedSeconds = rows.getLong(modifiedIndex).coerceAtLeast(0)
                val displayName = safeFileName(rows.getString(nameIndex), id, "jpg")
                if (extension(displayName) !in PHOTO_EXTENSIONS) {
                    savePhotoCursor(modifiedSeconds, id)
                    continue
                }
                val relativePath = safeRelativePath(
                    if (pathIndex >= 0 && !rows.isNull(pathIndex)) rows.getString(pathIndex) else null,
                )
                val uri = ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id)
                val declaredSize = rows.getLong(sizeIndex).coerceAtLeast(0)
                val fingerprint = fingerprint(uri, declaredSize)
                client.upload(
                    DeviceSyncClient.Asset(
                        assetId = "media-image-$id",
                        scope = "photos",
                        path = listOf(relativePath, displayName).filter { it.isNotBlank() }.joinToString("/"),
                        size = fingerprint.size,
                        sha256 = fingerprint.sha256,
                        modifiedAt = modifiedSeconds * 1000,
                    ),
                ) { open(uri) }
                savePhotoCursor(modifiedSeconds, id)
            }
        }
    }

    @Suppress("ComplexCondition") // Cached SHA reuse is valid only when all three metadata fields match.
    private fun uploadSelectedFiles(client: DeviceSyncClient) {
        selectedFileUris().forEach { uri ->
            val metadata = fileMetadata(uri)
            val key = digestText(uri.toString()).take(32)
            val version = "${metadata.size}:${metadata.modifiedAt}"
            val cached = KVUtils.getString("$KEY_FILE_FINGERPRINT_PREFIX$key", "")
            val cachedParts = cached.split(':', limit = 3)
            val fingerprint = if (
                cachedParts.size == 3 &&
                cachedParts[0] == metadata.size.toString() &&
                cachedParts[1] == metadata.modifiedAt.toString() &&
                cachedParts[2].matches(Regex("[0-9a-f]{64}"))
            ) {
                Fingerprint(metadata.size, cachedParts[2])
            } else {
                fingerprint(uri, metadata.size)
            }
            client.upload(
                DeviceSyncClient.Asset(
                    assetId = "saf-$key",
                    scope = "files",
                    path = "Selected/${safeFileName(metadata.name, key.hashCode().toLong(), "bin")}",
                    size = fingerprint.size,
                    sha256 = fingerprint.sha256,
                    modifiedAt = metadata.modifiedAt.takeIf { it > 0 },
                ),
            ) { open(uri) }
            KVUtils.putString(
                "$KEY_FILE_FINGERPRINT_PREFIX$key",
                "$version:${fingerprint.sha256}",
            )
        }
    }

    private fun fileMetadata(uri: Uri): FileMetadata {
        val projection = arrayOf(
            OpenableColumns.DISPLAY_NAME,
            OpenableColumns.SIZE,
            DocumentsContract.Document.COLUMN_LAST_MODIFIED,
        )
        resolver.query(uri, projection, null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                val name = cursor.stringOrNull(OpenableColumns.DISPLAY_NAME).orEmpty()
                val size = cursor.longOrNull(OpenableColumns.SIZE) ?: 0
                val modified = cursor.longOrNull(DocumentsContract.Document.COLUMN_LAST_MODIFIED) ?: 0
                return FileMetadata(name, size.coerceAtLeast(0), modified.coerceAtLeast(0))
            }
        }
        throw IOException("selected file is unavailable")
    }

    private fun selectedFileUris(): List<Uri> {
        val raw = KVUtils.getString(KEY_SELECTED_FILE_URIS, "[]")
        val type = object : TypeToken<List<String>>() {}.type
        val values = runCatching { gson.fromJson<List<String>>(raw, type) }.getOrNull().orEmpty()
        return values.distinct().take(MAX_SELECTED_FILES).map(Uri::parse)
    }

    private fun fingerprint(uri: Uri, declaredSize: Long): Fingerprint {
        val digest = MessageDigest.getInstance("SHA-256")
        var actualSize = 0L
        open(uri).use { source ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val read = source.read(buffer)
                if (read < 0) break
                if (read > 0) {
                    digest.update(buffer, 0, read)
                    actualSize += read
                }
            }
        }
        if (declaredSize > 0 && actualSize != declaredSize) {
            throw IOException("content size changed during scan")
        }
        return Fingerprint(actualSize, digest.digest().joinToString("") { "%02x".format(it) })
    }

    private fun open(uri: Uri): InputStream =
        resolver.openInputStream(uri) ?: throw IOException("content stream is unavailable")

    private fun savePhotoCursor(modified: Long, id: Long) {
        KVUtils.putLong(KEY_PHOTO_CURSOR_MODIFIED, modified)
        KVUtils.putLong(KEY_PHOTO_CURSOR_ID, id)
    }

    private fun hasPhotoPermission(): Boolean {
        val permission = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            Manifest.permission.READ_MEDIA_IMAGES
        } else {
            Manifest.permission.READ_EXTERNAL_STORAGE
        }
        return ContextCompat.checkSelfPermission(applicationContext, permission) ==
            PackageManager.PERMISSION_GRANTED
    }

    private fun failure(reason: String): Result = Result.failure(
        Data.Builder().putString(OUTPUT_REASON, reason).build(),
    )

    private data class Fingerprint(val size: Long, val sha256: String)
    private data class FileMetadata(val name: String, val size: Long, val modifiedAt: Long)

    companion object {
        const val KEY_BASE_URL = "ECHO_DEVICE_SYNC_BASE_URL"
        const val KEY_CERT_PIN = "ECHO_DEVICE_SYNC_CERT_PIN"
        const val KEY_PHOTOS_ENABLED = "ECHO_DEVICE_SYNC_PHOTOS_ENABLED"
        const val KEY_FILES_ENABLED = "ECHO_DEVICE_SYNC_FILES_ENABLED"
        const val KEY_SELECTED_FILE_URIS = "ECHO_DEVICE_SYNC_SELECTED_FILE_URIS"
        const val KEY_WIFI_ONLY = "ECHO_DEVICE_SYNC_WIFI_ONLY"
        const val KEY_CHARGING_ONLY = "ECHO_DEVICE_SYNC_CHARGING_ONLY"
        const val OUTPUT_REASON = "echo_device_sync_reason"
        private const val KEY_PHOTO_CURSOR_MODIFIED = "ECHO_DEVICE_SYNC_PHOTO_CURSOR_MODIFIED"
        private const val KEY_PHOTO_CURSOR_ID = "ECHO_DEVICE_SYNC_PHOTO_CURSOR_ID"
        private const val KEY_FILE_FINGERPRINT_PREFIX = "ECHO_DEVICE_SYNC_FILE_FP_"
        private const val UNIQUE_PERIODIC_WORK = "echo-device-sync-v1"
        private const val UNIQUE_IMMEDIATE_WORK = "echo-device-sync-now-v1"
        private const val MAX_PHOTOS_PER_RUN = 25
        private const val MAX_SELECTED_FILES = 500
        private val PHOTO_EXTENSIONS = setOf(
            "jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff", "heic", "heif",
        )

        fun applyPairingSyncBase(baseUrl: String) {
            val previous = KVUtils.getString(KEY_BASE_URL, "")
            if (previous != baseUrl) {
                KVUtils.putLong(KEY_PHOTO_CURSOR_MODIFIED, 0)
                KVUtils.putLong(KEY_PHOTO_CURSOR_ID, 0)
            }
            KVUtils.putString(KEY_BASE_URL, baseUrl.trim().trimEnd('/'))
        }

        fun setSelectedFiles(context: Context, uris: List<Uri>) {
            val bounded = uris.distinct().take(MAX_SELECTED_FILES)
            bounded.forEach { uri ->
                context.contentResolver.takePersistableUriPermission(
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION,
                )
            }
            KVUtils.putString(KEY_SELECTED_FILE_URIS, Gson().toJson(bounded.map(Uri::toString)))
        }

        fun schedule(
            context: Context,
            wifiOnly: Boolean = KVUtils.getBoolean(KEY_WIFI_ONLY, true),
            chargingOnly: Boolean = KVUtils.getBoolean(KEY_CHARGING_ONLY, false),
        ) {
            KVUtils.putBoolean(KEY_WIFI_ONLY, wifiOnly)
            KVUtils.putBoolean(KEY_CHARGING_ONLY, chargingOnly)
            val enabled = KVUtils.getBoolean(KEY_PHOTOS_ENABLED, false) ||
                KVUtils.getBoolean(KEY_FILES_ENABLED, false)
            val manager = WorkManager.getInstance(context)
            if (!enabled) {
                manager.cancelUniqueWork(UNIQUE_PERIODIC_WORK)
                return
            }
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(if (wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED)
                .setRequiresCharging(chargingOnly)
                .build()
            val request = PeriodicWorkRequestBuilder<EchoDeviceSyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            manager.enqueueUniquePeriodicWork(
                UNIQUE_PERIODIC_WORK,
                ExistingPeriodicWorkPolicy.UPDATE,
                request,
            )
        }

        fun runNow(context: Context): java.util.UUID? {
            val enabled = KVUtils.getBoolean(KEY_PHOTOS_ENABLED, false) ||
                KVUtils.getBoolean(KEY_FILES_ENABLED, false)
            if (!enabled) return null
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(
                    if (KVUtils.getBoolean(KEY_WIFI_ONLY, true)) {
                        NetworkType.UNMETERED
                    } else {
                        NetworkType.CONNECTED
                    },
                )
                .setRequiresCharging(KVUtils.getBoolean(KEY_CHARGING_ONLY, false))
                .build()
            val request = OneTimeWorkRequestBuilder<EchoDeviceSyncWorker>()
                .setConstraints(constraints)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                UNIQUE_IMMEDIATE_WORK,
                ExistingWorkPolicy.REPLACE,
                request,
            )
            return request.id
        }
    }
}

private fun android.database.Cursor.stringOrNull(column: String): String? {
    val index = getColumnIndex(column)
    return if (index >= 0 && !isNull(index)) getString(index) else null
}

private fun android.database.Cursor.longOrNull(column: String): Long? {
    val index = getColumnIndex(column)
    return if (index >= 0 && !isNull(index)) getLong(index) else null
}

private fun safeRelativePath(raw: String?): String = raw.orEmpty()
    .replace('\\', '/')
    .split('/')
    .mapNotNull { segment -> safeSegment(segment).takeIf(String::isNotBlank) }
    .take(10)
    .joinToString("/")

private fun safeFileName(raw: String?, fallbackId: Long, fallbackExtension: String): String {
    val value = safeSegment(raw.orEmpty())
    return value.ifBlank { "asset-$fallbackId.$fallbackExtension" }.take(180)
}

private fun safeSegment(raw: String): String = raw
    .filter { character -> character.code >= 32 && character.code != 127 }
    .replace(Regex("[/\\\\]+"), "-")
    .trim()
    .trim('.')
    .replace(Regex("\\s+"), " ")

private fun extension(name: String): String = name.substringAfterLast('.', "").lowercase()

private fun digestText(value: String): String = MessageDigest.getInstance("SHA-256")
    .digest(value.toByteArray(Charsets.UTF_8))
    .joinToString("") { "%02x".format(it) }
