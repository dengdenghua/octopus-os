package com.apk.claw.android.sync

import com.google.gson.Gson
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException

class DeviceSyncClientTest {
    private lateinit var server: MockWebServer
    private lateinit var client: DeviceSyncClient

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = DeviceSyncClient(
            baseUrl = server.url("/").toString(),
            deviceId = "phone-1",
            deviceCredential = "device-credential-123456",
            http = OkHttpClient(),
            gson = Gson(),
        )
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun statusUsesOnlyTheBoundDeviceCredentialAndProtocolHeaders() {
        server.enqueue(json("""{
            "schema":"echo.device-sync.v1",
            "protocolVersion":1,
            "minimumClientProtocolVersion":1,
            "deviceId":"phone-1",
            "grantedScopes":["photos"],
            "latestCursor":4,
            "chunkBytes":8388608
        }"""))

        val status = client.status()
        val request = server.takeRequest()

        assertEquals("phone-1", status.deviceId)
        assertEquals(listOf("photos"), status.grantedScopes)
        assertEquals("EchoDevice device-credential-123456", request.getHeader("Authorization"))
        assertEquals("phone-1", request.getHeader("X-Echo-Device-ID"))
        assertEquals("1", request.getHeader("X-Echo-Sync-Version"))
        assertEquals(null, request.getHeader("Cookie"))
    }

    @Test
    fun rejectsStatusForADifferentPairedDevice() {
        server.enqueue(json("""{
            "schema":"echo.device-sync.v1",
            "protocolVersion":1,
            "minimumClientProtocolVersion":1,
            "deviceId":"phone-2"
        }"""))

        assertThrows(IOException::class.java) { client.status() }
    }

    @Test
    fun rejectsNonHttpPrivateOriginsAndCredentialBearingUrls() {
        assertThrows(IllegalArgumentException::class.java) {
            DeviceSyncClient(
                "ftp://192.168.1.8",
                "phone-1",
                "device-credential-123456",
                OkHttpClient(),
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            DeviceSyncClient(
                "https://user@echo.example/path",
                "phone-1",
                "device-credential-123456",
                OkHttpClient(),
            )
        }
    }

    @Test
    fun skipNeverReadsOrUploadsTheLocalAssetAgain() {
        server.enqueue(json("""{
            "protocolVersion":1,
            "decision":"skip",
            "target":"Photos/phone-1/DCIM/photo.jpg",
            "conflict":false
        }"""))
        var opened = false
        val payload = "already-there".toByteArray()
        val committed = client.upload(asset("photo-1", payload)) {
            opened = true
            ByteArrayInputStream(payload)
        }

        assertTrue(committed.skipped)
        assertFalse(opened)
        assertEquals(1, server.requestCount)
    }

    @Test
    fun reconcilesACommittedTimedOutChunkFromTheServerOffset() {
        val payload = "abcdef".toByteArray()
        val digest = DeviceSyncClient.sha256 { ByteArrayInputStream(payload) }
        server.enqueue(json("""{
            "protocolVersion":1,
            "decision":"upload",
            "target":"Files/phone-1/report.txt",
            "session":{"sessionId":"session-1","uploadedBytes":0,"totalBytes":6,"chunkBytes":3}
        }"""))
        server.enqueue(MockResponse().setResponseCode(409).setBody("offset changed"))
        server.enqueue(json("""{
            "protocolVersion":1,
            "decision":"resume",
            "session":{"sessionId":"session-1","uploadedBytes":3,"totalBytes":6,"chunkBytes":3}
        }"""))
        server.enqueue(json("""{
            "protocolVersion":1,
            "decision":"resume",
            "session":{"sessionId":"session-1","uploadedBytes":6,"totalBytes":6,"chunkBytes":3}
        }"""))
        server.enqueue(json("""{
            "protocolVersion":1,
            "state":"committed",
            "target":"Files/phone-1/report.txt",
            "sha256":"$digest",
            "size":6,
            "cursor":1
        }"""))

        val committed = client.upload(asset("file-1", payload, "files")) {
            ByteArrayInputStream(payload)
        }

        val preflight = server.takeRequest()
        val timedOutPut = server.takeRequest()
        val status = server.takeRequest()
        val resumedPut = server.takeRequest()
        val complete = server.takeRequest()
        assertEquals("/api/appliance/device-sync/assets/preflight", preflight.path)
        assertEquals("0", timedOutPut.getHeader("X-Echo-Upload-Offset"))
        assertEquals("abc", timedOutPut.body.readUtf8())
        assertEquals("GET", status.method)
        assertEquals("3", resumedPut.getHeader("X-Echo-Upload-Offset"))
        assertEquals("def", resumedPut.body.readUtf8())
        assertEquals("POST", complete.method)
        assertEquals(digest, committed.sha256)
    }

    @Test
    fun rejectsAServerThatRequiresANewerProtocol() {
        server.enqueue(json("""{
            "schema":"echo.device-sync.v2",
            "protocolVersion":2,
            "minimumClientProtocolVersion":2,
            "deviceId":"phone-1"
        }"""))

        assertThrows(IOException::class.java) { client.status() }
    }

    private fun asset(id: String, payload: ByteArray, scope: String = "photos"): DeviceSyncClient.Asset =
        DeviceSyncClient.Asset(
            assetId = id,
            scope = scope,
            path = if (scope == "photos") "DCIM/photo.jpg" else "Documents/report.txt",
            size = payload.size.toLong(),
            sha256 = DeviceSyncClient.sha256 { ByteArrayInputStream(payload) },
        )

    private fun json(body: String): MockResponse = MockResponse()
        .setResponseCode(200)
        .setHeader("Content-Type", "application/json")
        .setBody(body)
}
