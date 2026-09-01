@file:Suppress("MagicNumber") // HTTP codes, chunk sizes and RFC1918 octets are protocol literals.

package com.apk.claw.android.sync

import com.google.gson.Gson
import com.google.gson.annotations.SerializedName
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.io.InputStream
import java.net.URI
import java.security.MessageDigest

/**
 * Echo device-sync v1 reference client.
 *
 * This file intentionally uses only dependencies already present in
 * echo-mobile (OkHttp 4.12 and Gson). The caller supplies its existing
 * certificate-pinned OkHttpClient and the per-device credential stored by
 * KVUtils. Browser cookies and account/Agent shared tokens are never used.
 *
 * A WorkManager CoroutineWorker should call [status] once, enumerate an
 * Android MediaStore/SAF batch, and call [upload] for each asset. IOException
 * is retryable. HTTP 401/403/426 must stop the worker until the user repairs
 * pairing, grants, or app compatibility.
 */
@Suppress("TooManyFunctions") // One cohesive adapter mirrors the seven device-sync endpoints.
class DeviceSyncClient(
    baseUrl: String,
    private val deviceId: String,
    private val deviceCredential: String,
    private val http: OkHttpClient,
    private val gson: Gson = Gson(),
) {
    private val origin = baseUrl.trim().trimEnd('/')

    init {
        require(isAllowedOrigin(origin)) {
            "device sync must use HTTPS outside loopback or an RFC1918 LAN"
        }
        require(deviceId.isNotBlank()) { "paired device id is required" }
        require(deviceCredential.isNotBlank()) { "paired device credential is required" }
    }

    data class Capabilities(
        val resumableUpload: Boolean = false,
        val sha256Verification: Boolean = false,
        val conflictPolicy: String = "",
        val ownDeviceCursor: Boolean = false,
        val maxChunkBytes: Int = 0,
        val maxChangePage: Int = 0,
    )

    data class Status(
        val schema: String = "",
        val protocolVersion: Int = 0,
        val minimumClientProtocolVersion: Int = 0,
        val deviceId: String = "",
        val grantedScopes: List<String> = emptyList(),
        val latestCursor: Long = 0,
        val chunkBytes: Int = 0,
        val capabilities: Capabilities = Capabilities(),
    )

    data class Asset(
        val assetId: String,
        val scope: String,
        val path: String,
        val size: Long,
        val sha256: String,
        val modifiedAt: Long? = null,
    ) {
        init {
            require(scope == "photos" || scope == "files")
            require(size >= 0)
            require(sha256.matches(Regex("[0-9a-f]{64}")))
        }
    }

    data class UploadSession(
        val sessionId: String = "",
        val uploadedBytes: Long = 0,
        val totalBytes: Long = 0,
        val chunkBytes: Int = 0,
    )

    data class Preflight(
        val protocolVersion: Int = 0,
        val decision: String = "",
        val target: String = "",
        val conflict: Boolean = false,
        val session: UploadSession? = null,
    )

    data class Commit(
        val protocolVersion: Int = 0,
        val state: String = "",
        val target: String = "",
        val sha256: String = "",
        val size: Long = 0,
        val conflict: Boolean = false,
        val cursor: Long = 0,
        val skipped: Boolean = false,
    )

    data class Change(
        val cursor: Long = 0,
        val scope: String = "",
        val assetId: String = "",
        val target: String = "",
        val sha256: String = "",
        val size: Long = 0,
        val kind: String = "",
        val createdAt: Long = 0,
    )

    data class Changes(
        val protocolVersion: Int = 0,
        val cursor: Long = 0,
        val hasMore: Boolean = false,
        val changes: List<Change> = emptyList(),
    )

    class HttpFailure(
        val statusCode: Int,
        val responseBody: String,
    ) : IOException("Echo device sync HTTP $statusCode") {
        val permanent: Boolean
            get() = statusCode == 401 || statusCode == 403 || statusCode == 426
    }

    fun status(): Status = executeJson(
        request("/api/appliance/device-sync").get().build(),
        Status::class.java,
    ).also { status ->
        requireCompatible(status)
        if (status.deviceId != deviceId) {
            throw IOException("Echo returned status for a different device")
        }
    }

    fun changes(cursor: Long, limit: Int = 100): Changes = executeJson(
        request("/api/appliance/device-sync/changes?cursor=${cursor.coerceAtLeast(0)}&limit=${limit.coerceIn(1, 500)}")
            .get()
            .build(),
        Changes::class.java,
    ).also { requireCompatible(it.protocolVersion) }

    fun preflight(asset: Asset): Preflight = executeJson(
        request("/api/appliance/device-sync/assets/preflight")
            .post(gson.toJson(asset).toRequestBody(JSON))
            .build(),
        Preflight::class.java,
    ).also { requireCompatible(it.protocolVersion) }

    @Suppress("ThrowsCount") // Each throw protects a distinct server-authoritative upload invariant.
    fun upload(asset: Asset, openSource: () -> InputStream): Commit {
        val prepared = preflight(asset)
        if (prepared.decision == "skip") {
            return Commit(
                protocolVersion = PROTOCOL_VERSION,
                state = "committed",
                target = prepared.target,
                sha256 = asset.sha256,
                size = asset.size,
                conflict = prepared.conflict,
                skipped = true,
            )
        }
        require(prepared.decision == "upload" || prepared.decision == "resume") {
            "unknown preflight decision: ${prepared.decision}"
        }
        val initial = requireNotNull(prepared.session) { "upload decision has no session" }
        var offset = initial.uploadedBytes
        val sessionId = initial.sessionId
        val chunkBytes = sequenceOf(
            initial.chunkBytes,
            DEFAULT_CHUNK_BYTES,
            MAX_CHUNK_BYTES,
        ).filter { it > 0 }.minOrNull() ?: DEFAULT_CHUNK_BYTES

        while (offset < asset.size) {
            val wanted = minOf(chunkBytes.toLong(), asset.size - offset).toInt()
            val bytes = openSource().use { source ->
                source.skipExactly(offset)
                source.readExactly(wanted)
            }
            try {
                val updated = appendChunk(sessionId, offset, bytes)
                val serverOffset = requireNotNull(updated.session).uploadedBytes
                check(serverOffset > offset && serverOffset <= asset.size) {
                    "server returned an invalid upload offset"
                }
                offset = serverOffset
            } catch (failure: HttpFailure) {
                if (failure.statusCode != 409) throw failure
                // A timed-out PUT may have reached Echo. Re-read the server's
                // authoritative offset instead of replaying or guessing.
                val reconciledOffset = uploadStatus(sessionId).session?.uploadedBytes
                    ?: throw IOException("upload session has no offset")
                if (reconciledOffset == offset) throw failure
                offset = reconciledOffset
            }
        }
        return complete(sessionId).also { committed ->
            check(committed.sha256 == asset.sha256 && committed.size == asset.size) {
                "Echo committed a different asset"
            }
        }
    }

    fun cancel(sessionId: String) {
        executeNoContent(
            request("/api/appliance/device-sync/upload-sessions/$sessionId")
                .delete()
                .build(),
        )
    }

    private fun uploadStatus(sessionId: String): Preflight = executeJson(
        request("/api/appliance/device-sync/upload-sessions/$sessionId").get().build(),
        Preflight::class.java,
    ).also { requireCompatible(it.protocolVersion) }

    private fun appendChunk(sessionId: String, offset: Long, bytes: ByteArray): Preflight =
        executeJson(
            request("/api/appliance/device-sync/upload-sessions/$sessionId/chunk")
                .header(OFFSET_HEADER, offset.toString())
                .put(bytes.toRequestBody(OCTET_STREAM))
                .build(),
            Preflight::class.java,
        ).also { requireCompatible(it.protocolVersion) }

    private fun complete(sessionId: String): Commit = executeJson(
        request("/api/appliance/device-sync/upload-sessions/$sessionId/complete")
            .post(ByteArray(0).toRequestBody(null))
            .build(),
        Commit::class.java,
    ).also { requireCompatible(it.protocolVersion) }

    private fun request(path: String): Request.Builder = Request.Builder()
        .url(origin + path)
        .header("Authorization", "EchoDevice $deviceCredential")
        .header(DEVICE_ID_HEADER, deviceId)
        .header(VERSION_HEADER, PROTOCOL_VERSION.toString())

    private fun <T> executeJson(request: Request, type: Class<T>): T =
        http.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) throw HttpFailure(response.code, body.take(MAX_ERROR_BODY))
            gson.fromJson(body, type) ?: throw IOException("Echo returned an empty response")
        }

    private fun executeNoContent(request: Request) {
        http.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) throw HttpFailure(response.code, body.take(MAX_ERROR_BODY))
        }
    }

    private fun requireCompatible(status: Status) {
        if (status.minimumClientProtocolVersion > PROTOCOL_VERSION) {
            throw IOException(
                "Echo requires device sync protocol ${status.minimumClientProtocolVersion}",
            )
        }
        requireCompatible(status.protocolVersion)
    }

    private fun requireCompatible(protocolVersion: Int) {
        if (protocolVersion != PROTOCOL_VERSION) {
            throw IOException("unsupported Echo device sync protocol: $protocolVersion")
        }
    }

    companion object {
        const val PROTOCOL_VERSION = 1
        const val VERSION_HEADER = "X-Echo-Sync-Version"
        const val DEVICE_ID_HEADER = "X-Echo-Device-ID"
        const val OFFSET_HEADER = "X-Echo-Upload-Offset"
        const val MAX_CHUNK_BYTES = 8 * 1024 * 1024
        private const val DEFAULT_CHUNK_BYTES = MAX_CHUNK_BYTES
        private const val MAX_ERROR_BODY = 4 * 1024
        private val JSON = "application/json; charset=utf-8".toMediaType()
        private val OCTET_STREAM = "application/octet-stream".toMediaType()

        fun sha256(openSource: () -> InputStream): String {
            val digest = MessageDigest.getInstance("SHA-256")
            openSource().use { source ->
                val buffer = ByteArray(1024 * 1024)
                while (true) {
                    val read = source.read(buffer)
                    if (read < 0) break
                    if (read > 0) digest.update(buffer, 0, read)
                }
            }
            return digest.digest().joinToString("") { byte -> "%02x".format(byte) }
        }

        @Suppress("CyclomaticComplexMethod", "ComplexCondition", "ReturnCount")
        private fun isAllowedOrigin(origin: String): Boolean {
            val parsed = runCatching { URI(origin) }.getOrNull() ?: return false
            val host = parsed.host ?: return false
            val scheme = parsed.scheme?.lowercase()
            if (
                parsed.userInfo != null || parsed.query != null || parsed.fragment != null ||
                !(parsed.path.isNullOrEmpty() || parsed.path == "/")
            ) return false
            if (scheme == "https") return true
            if (scheme != "http") return false
            if (host in setOf("localhost", "127.0.0.1", "::1", "10.0.2.2")) return true
            val octets = host.split('.').mapNotNull { it.toIntOrNull() }
            if (octets.size != 4 || octets.any { it !in 0..255 }) return false
            return octets[0] == 10 ||
                (octets[0] == 172 && octets[1] in 16..31) ||
                (octets[0] == 192 && octets[1] == 168)
        }
    }
}

private fun InputStream.skipExactly(byteCount: Long) {
    var remaining = byteCount
    while (remaining > 0) {
        val skipped = skip(remaining)
        if (skipped > 0) {
            remaining -= skipped
        } else if (read() >= 0) {
            remaining -= 1
        } else {
            throw IOException("source ended before the resume offset")
        }
    }
}

private fun InputStream.readExactly(byteCount: Int): ByteArray {
    val output = ByteArray(byteCount)
    var offset = 0
    while (offset < byteCount) {
        val read = read(output, offset, byteCount - offset)
        if (read < 0) throw IOException("source size changed during upload")
        if (read > 0) offset += read
    }
    return output
}
