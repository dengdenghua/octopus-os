package com.apk.claw.android.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class EchoPairingBootstrapTest {
    @Test
    fun parsesLanRuntimeAndSyncAsOneBoundPairing() {
        val config = EchoPairingBootstrap.parse(
            "echo://join?" +
                "ws=ws%3A%2F%2F192.168.50.10%3A8765&" +
                "token=abcdefghijklmnopqrstuvwxyz123456&" +
                "sync=http%3A%2F%2F192.168.50.10%3A8000",
        )

        assertEquals("ws://192.168.50.10:8765", config.runtimeUrl)
        assertEquals("abcdefghijklmnopqrstuvwxyz123456", config.deviceCredential)
        assertEquals("http://192.168.50.10:8000", config.syncBaseUrl)
    }

    @Test
    fun acceptsTailnetHttpsSyncForLanTentacle() {
        val config = EchoPairingBootstrap.parse(
            "echo://join?" +
                "ws=ws%3A%2F%2F10.0.0.8%3A8765&" +
                "token=abcdefghijklmnopqrstuvwxyz123456&" +
                "sync=https%3A%2F%2Fecho-os.example.ts.net",
        )

        assertEquals("https://echo-os.example.ts.net", config.syncBaseUrl)
    }

    @Test
    fun keepsOlderPairingLinksCompatibleWithoutInventingSync() {
        val config = EchoPairingBootstrap.parse(
            "echo://join?" +
                "ws=ws%3A%2F%2F192.168.1.8%3A8765&" +
                "token=abcdefghijklmnopqrstuvwxyz123456",
        )

        assertNull(config.syncBaseUrl)
    }

    @Test
    fun rejectsDuplicateCredentialAndUnknownFields() {
        assertRejected(
            "echo://join?ws=ws%3A%2F%2F10.0.0.8%3A8765&" +
                "token=abcdefghijklmnopqrstuvwxyz123456&" +
                "token=second-credential-123456",
        )
        assertRejected(
            "echo://join?ws=ws%3A%2F%2F10.0.0.8%3A8765&" +
                "token=abcdefghijklmnopqrstuvwxyz123456&redirect=https%3A%2F%2Fevil.example",
        )
    }

    @Test
    fun rejectsPublicCleartextAndForeignSyncHost() {
        assertRejected(
            "echo://join?ws=ws%3A%2F%2F203.0.113.8%3A8765&" +
                "token=abcdefghijklmnopqrstuvwxyz123456",
        )
        assertRejected(
            "echo://join?ws=ws%3A%2F%2F10.0.0.8%3A8765&" +
                "token=abcdefghijklmnopqrstuvwxyz123456&" +
                "sync=https%3A%2F%2Fcredential-sink.example.com",
        )
    }

    private fun assertRejected(value: String) {
        runCatching { EchoPairingBootstrap.parse(value) }
            .onSuccess { throw AssertionError("unsafe pairing was accepted: $value") }
            .onFailure { error ->
                if (error !is IllegalArgumentException) throw error
            }
    }
}
