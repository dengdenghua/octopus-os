@file:Suppress("MagicNumber") // Credential bounds and RFC1918 octets are security policy literals.

package com.apk.claw.android.sync

import com.apk.claw.android.echo_mobile.MobileRuntimeSecurity
import com.apk.claw.android.utils.KVUtils
import java.net.URI
import java.net.URLDecoder
import java.nio.charset.StandardCharsets

/** Strict parser and atomic persistence boundary for Echo pairing deep links. */
object EchoPairingBootstrap {
    data class PairingConfig(
        val runtimeUrl: String,
        val deviceCredential: String,
        val syncBaseUrl: String?,
    )

    fun parse(raw: String): PairingConfig {
        val invitation = parseUri(raw, "pairing invitation")
        require(invitation.scheme.equals("echo", ignoreCase = true)) {
            "pairing invitation must use echo://"
        }
        require(invitation.host.equals("join", ignoreCase = true)) {
            "pairing invitation host must be join"
        }
        require(invitation.userInfo == null && invitation.fragment == null) {
            "pairing invitation contains forbidden URL fields"
        }
        require(invitation.path.isNullOrEmpty() || invitation.path == "/") {
            "pairing invitation path is invalid"
        }
        val query = parseQuery(invitation.rawQuery.orEmpty())
        require(query.keys.all { it in setOf("ws", "token", "sync") }) {
            "pairing invitation contains unknown fields"
        }
        val runtimeUrl = requiredOnce(query, "ws")
        val credential = requiredOnce(query, "token")
        require(credential.length in 16..512 && credential.none { it.isISOControl() }) {
            "pairing credential is invalid"
        }
        val runtime = validateRuntime(runtimeUrl)
        val sync = optionalOnce(query, "sync")?.let { validateSync(it, runtime) }
        return PairingConfig(
            runtimeUrl = runtime.toASCIIString(),
            deviceCredential = credential,
            syncBaseUrl = sync?.toASCIIString()?.trimEnd('/'),
        )
    }

    /** Persist only after the entire invitation has passed validation. */
    fun apply(raw: String): PairingConfig {
        val config = parse(raw)
        KVUtils.setEchoRpcUrl(config.runtimeUrl)
        KVUtils.setEchoAuthToken(config.deviceCredential)
        EchoDeviceSyncWorker.applyPairingSyncBase(config.syncBaseUrl.orEmpty())
        return config
    }

    private fun validateRuntime(value: String): URI {
        val runtime = parseUri(value, "Runtime URL")
        require(
            runtime.scheme.equals("ws", ignoreCase = true) ||
                runtime.scheme.equals("wss", ignoreCase = true),
        ) { "Runtime URL must use ws:// or wss://" }
        require(
            !runtime.host.isNullOrBlank() &&
                runtime.userInfo == null &&
                runtime.query == null &&
                runtime.fragment == null &&
                (runtime.path.isNullOrEmpty() || runtime.path == "/"),
        ) { "Runtime URL must be an origin" }
        require(MobileRuntimeSecurity.assess(runtime.toASCIIString()).allowed) {
            "Runtime transport is not allowed"
        }
        return runtime
    }

    private fun validateSync(value: String, runtime: URI): URI {
        val sync = parseUri(value, "sync URL")
        val scheme = sync.scheme?.lowercase()
        require(
            scheme in setOf("http", "https") &&
                !sync.host.isNullOrBlank() &&
                sync.userInfo == null &&
                sync.query == null &&
                sync.fragment == null &&
                (sync.path.isNullOrEmpty() || sync.path == "/"),
        ) { "sync URL must be an HTTP(S) origin" }
        val syncHost = sync.host.lowercase()
        val runtimeHost = runtime.host.lowercase()
        if (scheme == "http") {
            require(syncHost == runtimeHost && isPrivateDevelopmentHost(syncHost)) {
                "cleartext sync must stay on the paired private host"
            }
        } else {
            require(syncHost == runtimeHost || syncHost.endsWith(".ts.net")) {
                "HTTPS sync host is not bound to this pairing"
            }
        }
        return sync
    }

    private fun parseQuery(raw: String): Map<String, List<String>> {
        if (raw.isBlank()) return emptyMap()
        val values = linkedMapOf<String, MutableList<String>>()
        raw.split('&').forEach { field ->
            require(field.isNotBlank() && field.count { it == '=' } <= 1) {
                "pairing query is malformed"
            }
            val key = decode(field.substringBefore('='))
            val value = decode(field.substringAfter('=', ""))
            require(key.isNotBlank()) { "pairing query contains an empty key" }
            values.getOrPut(key) { mutableListOf() }.add(value)
        }
        return values
    }

    private fun requiredOnce(query: Map<String, List<String>>, key: String): String {
        val values = query[key]
        require(values?.size == 1 && values.single().isNotBlank()) {
            "pairing invitation requires one $key"
        }
        return values.single()
    }

    private fun optionalOnce(query: Map<String, List<String>>, key: String): String? {
        val values = query[key] ?: return null
        require(values.size == 1 && values.single().isNotBlank()) {
            "pairing invitation accepts at most one $key"
        }
        return values.single()
    }

    private fun parseUri(raw: String, label: String): URI = runCatching { URI(raw.trim()) }
        .getOrElse { throw IllegalArgumentException("$label is invalid") }

    private fun decode(value: String): String = runCatching {
        URLDecoder.decode(value, StandardCharsets.UTF_8.name())
    }.getOrElse { throw IllegalArgumentException("pairing query encoding is invalid") }

    @Suppress("ReturnCount") // Fast exits make the private-host allowlist auditable.
    private fun isPrivateDevelopmentHost(host: String): Boolean {
        if (host in setOf("localhost", "127.0.0.1", "::1", "10.0.2.2")) return true
        val octets = host.split('.').mapNotNull(String::toIntOrNull)
        if (octets.size != 4 || octets.any { it !in 0..255 }) return false
        return octets[0] == 10 ||
            (octets[0] == 172 && octets[1] in 16..31) ||
            (octets[0] == 192 && octets[1] == 168)
    }
}
