"""Per-field encryption for sensitive ``mount_options`` entries.

Pattern mirrors ``runtime/adapters/mcp_client/oauth.py``: opt-in via env var
``ECHO_WORKSPACE_KEY`` (a urlsafe-base64 32-byte Fernet key from
``cryptography.fernet.Fernet.generate_key()``, or any passphrase which we
derive through PBKDF2). When unset, a key is derived from the host machine
id — enough to keep creds opaque at rest on a single host but not a
substitute for a real secret store in shared deployments.

Sensitive fields (matched by key name, case-insensitive, recursively
through nested dicts and lists):
    password, secret_key, access_key, token, credential

Encrypted values are written as ``"ENC:<base64_ciphertext>"`` strings so
the rest of the ``mount_options`` dict stays human-readable for debugging,
querying, and schema migrations. Non-string sensitive values use
``"ENC:json:<base64_ciphertext>"`` with a JSON payload to preserve their type.
``encrypt_options`` returns the modified dict serialized as JSON;
``decrypt_options`` is the inverse. Credential failures raise without changing
stored data; old unmarked plaintext remains readable without automatic migration.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import platform
import subprocess
import uuid
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.workspace.crypto")

# Field names whose values get encrypted at rest. Matched case-insensitively
# so ``Password`` / ``PASSWORD`` / ``password`` all hit.
SENSITIVE_FIELDS = frozenset({"password", "secret_key", "access_key", "token", "credential"})
_ENC_PREFIX = "ENC:"
_JSON_ENC_PREFIX = "ENC:json:"

# Fixed salt for the PBKDF2 derivation. We're not protecting against offline
# brute-force on a stolen DB (the host has the key anyway); the derivation
# just gives us key-shape conformance for Fernet (32 urlsafe-base64 bytes).
_KDF_SALT = b"echo-workspace-key-v1"
_KDF_ITERATIONS = 100_000

_MACHINE_ID_CACHE: str | None = None
_CIPHER_CACHE: Any = None
_CIPHER_KEY_CACHE: bytes | None = None


class WorkspaceCryptoError(RuntimeError):
    """A safe, actionable failure; never contains key material or options."""

    def __init__(self, code: str, hint: str) -> None:
        self.code = code
        self.hint = hint
        super().__init__(hint)

    def to_detail(self) -> dict[str, str]:
        return {"error": self.code, "hint": self.hint}


def _encryption_error() -> WorkspaceCryptoError:
    return WorkspaceCryptoError(
        "workspace_encryption_unavailable",
        "Workspace credentials could not be encrypted. Install the cryptography package "
        "and verify the workspace key configuration before retrying. No workspace was saved.",
    )


def _decryption_error() -> WorkspaceCryptoError:
    return WorkspaceCryptoError(
        "workspace_credentials_unavailable",
        "Workspace credentials could not be decrypted. Restore the original "
        "ECHO_WORKSPACE_KEY (or the original host identity when no explicit key was used), "
        "ensure cryptography is installed, and restart the service. Stored data was not changed.",
    )


def _configuration_error() -> WorkspaceCryptoError:
    return WorkspaceCryptoError(
        "workspace_configuration_invalid",
        "Workspace mount options are not a valid JSON object. Restore the original "
        "configuration from a trusted backup. Stored data was not changed.",
    )


# ─── machine id ────────────────────────────────────────────────────────────


def _read_machine_id() -> str:
    """Cross-platform best-effort stable machine id. Never raises — falls
    back to the host's MAC address via ``uuid.getnode()`` which is always
    available but not stable across hardware changes.
    """
    system = platform.system()
    if system == "Darwin":
        try:
            out = subprocess.check_output(
                ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).decode("utf-8", "replace")
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    parts = line.split('"')
                    if len(parts) >= 4:
                        return parts[-2]
        except (OSError, subprocess.SubprocessError):
            _LOG.debug("macOS platform UUID probe failed; using fallback", exc_info=True)
    if system == "Linux":
        for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                return Path(candidate).read_text(encoding="utf-8").strip()
            except OSError:
                continue
    if system == "Windows":
        try:
            out = subprocess.check_output(
                ["reg", "query", r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"],
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).decode("utf-8", "replace")
            for line in out.splitlines():
                if "MachineGuid" in line:
                    return line.split()[-1]
        except (OSError, subprocess.SubprocessError):
            _LOG.debug("Windows machine GUID probe failed; using fallback", exc_info=True)
    # Last resort: MAC address via uuid.getnode() (always available, stable
    # within a single host's lifetime, not stable across hardware swaps).
    return f"mac:{uuid.getnode():012x}"


def _machine_id() -> str:
    global _MACHINE_ID_CACHE
    if _MACHINE_ID_CACHE is None:
        _MACHINE_ID_CACHE = _read_machine_id()
    return _MACHINE_ID_CACHE


# ─── key derivation ─────────────────────────────────────────────────────────


def _derive_fernet_key(material: str) -> bytes:
    """Derive a urlsafe-base64 32-byte Fernet key from arbitrary text."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_KDF_SALT,
        iterations=_KDF_ITERATIONS,
    )
    raw = kdf.derive(material.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def _resolve_key() -> bytes:
    """Return the active Fernet key bytes. Prefers ``ECHO_WORKSPACE_KEY``,
    falls back to a machine-derived key. Caches the result for the process.
    """
    global _CIPHER_KEY_CACHE
    if _CIPHER_KEY_CACHE is not None:
        return _CIPHER_KEY_CACHE
    env_key = os.environ.get("ECHO_WORKSPACE_KEY")
    if env_key:
        # Accept either a ready-made Fernet key (urlsafe base64 32 bytes)
        # or any passphrase (derive via PBKDF2). We detect a "raw Fernet
        # key" by checking it decodes to exactly 32 bytes.
        try:
            decoded = base64.urlsafe_b64decode(env_key.encode("utf-8"))
            if len(decoded) == 32:
                _CIPHER_KEY_CACHE = env_key.encode("utf-8")
                return _CIPHER_KEY_CACHE
        except (ValueError, base64.binascii.Error):
            _LOG.debug("workspace key is a passphrase rather than a Fernet key")
        _CIPHER_KEY_CACHE = _derive_fernet_key(env_key)
        return _CIPHER_KEY_CACHE
    _CIPHER_KEY_CACHE = _derive_fernet_key(_machine_id())
    return _CIPHER_KEY_CACHE


def _cipher() -> Any:
    """Return a cached Fernet instance. Failure must never permit plaintext writes."""
    global _CIPHER_CACHE
    if _CIPHER_CACHE is not None:
        return _CIPHER_CACHE
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise _encryption_error() from None
    try:
        _CIPHER_CACHE = Fernet(_resolve_key())
    except Exception:  # noqa: BLE001 — do not expose key derivation failures
        raise _encryption_error() from None
    return _CIPHER_CACHE


# ─── tree walkers ──────────────────────────────────────────────────────────


def _is_sensitive(key: str) -> bool:
    return isinstance(key, str) and key.lower() in SENSITIVE_FIELDS


def _walk_encrypt(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _is_sensitive(k):
                # Every caller-supplied value is plaintext, even if it starts
                # with ENC:. Only the database read path interprets envelopes.
                # Seal non-string values as a whole to avoid leaking credentials
                # supplied as a number, list, or an object with arbitrary keys.
                is_text = isinstance(v, str)
                payload = v if is_text else json.dumps(v, ensure_ascii=False)
                token = _cipher().encrypt(payload.encode("utf-8")).decode("ascii")
                prefix = _ENC_PREFIX if is_text else _JSON_ENC_PREFIX
                out[k] = f"{prefix}{token}"
            else:
                out[k] = _walk_encrypt(v)
        return out
    if isinstance(value, list):
        return [_walk_encrypt(v) for v in value]
    return value


def _walk_decrypt(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _is_sensitive(k) and isinstance(v, str) and v.startswith(_ENC_PREFIX):
                is_json = v.startswith(_JSON_ENC_PREFIX)
                prefix = _JSON_ENC_PREFIX if is_json else _ENC_PREFIX
                token = v[len(prefix) :]
                try:
                    plaintext = _cipher().decrypt(token.encode("ascii")).decode("utf-8")
                    out[k] = json.loads(plaintext) if is_json else plaintext
                except Exception:  # noqa: BLE001 — ciphertext is never usable credentials
                    raise _decryption_error() from None
            else:
                # Old unmarked plaintext remains readable without cryptography.
                # Non-sensitive labels/paths may legitimately start with ENC:.
                out[k] = _walk_decrypt(v)
        return out
    if isinstance(value, list):
        return [_walk_decrypt(v) for v in value]
    return value


# ─── public API ─────────────────────────────────────────────────────────────


def encrypt_options(options: dict[str, Any]) -> str:
    """Walk ``options`` recursively, encrypt values whose key matches
    SENSITIVE_FIELDS, then return the result as a JSON string.
    Non-sensitive fields stay human-readable in the SQLite column.
    Sensitive values require working encryption, including user strings
    beginning with ENC:. No cipher is needed for non-sensitive options.
    """
    if not isinstance(options, dict):
        raise _configuration_error()
    try:
        return json.dumps(_walk_encrypt(options), ensure_ascii=False)
    except WorkspaceCryptoError:
        raise
    except Exception:  # noqa: BLE001 — serialization/encryption errors can contain secrets
        raise _encryption_error() from None


def decrypt_options(encrypted: str) -> dict[str, Any]:
    """Inverse of ``encrypt_options``: parse the JSON string, walk the
    tree, and decrypt sensitive ``ENC:``-prefixed values. Unmarked legacy
    plaintext remains readable. Invalid data or unavailable credentials raise
    WorkspaceCryptoError; they never become empty config or usable ciphertext.
    """
    if not encrypted:
        return {}
    try:
        raw = json.loads(encrypted)
    except (TypeError, ValueError):
        raise _configuration_error() from None
    if not isinstance(raw, dict):
        raise _configuration_error()
    return _walk_decrypt(raw)
