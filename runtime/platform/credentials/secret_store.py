"""OS keychain backed secret store for at-rest encryption keys.

Motivation: at-rest encryption of credential files (MCP OAuth tokens today)
was already implemented but gated behind a hand-configured env var holding
the key. In practice nobody sets it, so the default stayed plaintext-on-disk.
A key that the process can fetch from the platform keychain closes that gap
without asking the operator to manage key material by hand.

Design constraints that shaped this module:

* **No new hard dependency.** ``keyring`` is not in the core dependency set,
  so the backends here shell out to tools that ship with the OS (``security``
  on macOS, ``secret-tool`` on Linux, PowerShell's DPAPI on Windows). When a
  backend is unavailable every operation degrades to ``None`` rather than
  raising — callers keep their previous behavior.
* **Env var still wins.** An explicitly configured key overrides the keychain
  so deployments that inject secrets through their own vault are unaffected,
  and so a container with no keychain has a supported path.
* **Never log secret values.** Failures log the backend and the error class,
  never stdout/stderr of the helper (which can echo the secret back).

The store is keyed by an opaque ``name``; the caller decides the namespace.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import sys
import threading

_LOG = logging.getLogger("echo.credentials.secret_store")

# Resolved keys, including negative results. See get_or_create_fernet_key.
_KEY_CACHE: dict[str, str | None] = {}
_KEY_CACHE_LOCK = threading.Lock()

# Keychain entries are grouped under this service so they are easy to find
# and revoke by hand (`security find-generic-password -s echo-agent`).
_SERVICE = "echo-agent"

_TIMEOUT = 10.0


class SecretStoreUnavailable(RuntimeError):
    """Raised only by :func:`require_secret` when no backend can serve."""


def _run(argv: list[str], *, stdin: str | None = None) -> tuple[int, str]:
    """Run a helper, returning ``(returncode, stdout)``.

    stderr is captured but deliberately dropped: some helpers echo the
    secret into diagnostics, and this function's output reaches logs.
    """
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _LOG.debug("secret helper %s failed: %s", argv[0], type(exc).__name__)
        return (1, "")
    return (proc.returncode, proc.stdout or "")


# ── macOS · security(1) ──


def _macos_available() -> bool:
    return sys.platform == "darwin" and shutil.which("security") is not None


def _macos_get(name: str) -> str | None:
    code, out = _run(["security", "find-generic-password", "-s", _SERVICE, "-a", name, "-w"])
    if code != 0:
        return None
    return out.strip() or None


def _macos_set(name: str, value: str) -> bool:
    # ``-U`` updates in place when the item already exists. The value is
    # passed as an argument because ``security`` has no stdin path for it;
    # this is visible in the process table for the duration of the call,
    # which is why we only ever store generated keys here, never
    # user-supplied passwords.
    code, _ = _run(
        [
            "security",
            "add-generic-password",
            "-s",
            _SERVICE,
            "-a",
            name,
            "-w",
            value,
            "-U",
        ]
    )
    return code == 0


# ── Linux · secret-tool(1) (libsecret / Secret Service) ──


def _linux_available() -> bool:
    return sys.platform.startswith("linux") and shutil.which("secret-tool") is not None


def _linux_get(name: str) -> str | None:
    code, out = _run(["secret-tool", "lookup", "service", _SERVICE, "account", name])
    if code != 0:
        return None
    # secret-tool emits the raw secret with no trailing newline.
    return out or None


def _linux_set(name: str, value: str) -> bool:
    code, _ = _run(
        [
            "secret-tool",
            "store",
            "--label",
            f"{_SERVICE} · {name}",
            "service",
            _SERVICE,
            "account",
            name,
        ],
        stdin=value,
    )
    return code == 0


# ── Windows · DPAPI via PowerShell ──
#
# Windows has no CLI equivalent of `security`, so we use DPAPI to encrypt
# the value for the current user and keep the ciphertext in a file. DPAPI
# ties decryption to the user account, which is the property we want.


def _windows_available() -> bool:
    return sys.platform == "win32" and shutil.which("powershell") is not None


def _windows_path(name: str) -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    safe = base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii").rstrip("=")
    return os.path.join(base, _SERVICE, f"{safe}.dpapi")


def _windows_get(name: str) -> str | None:
    path = _windows_path(name)
    if not os.path.exists(path):
        return None
    script = (
        "$ErrorActionPreference='Stop';"
        "Add-Type -AssemblyName System.Security;"
        f"$b=[IO.File]::ReadAllBytes('{path}');"
        "$p=[Security.Cryptography.ProtectedData]::Unprotect("
        "$b,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);"
        "[Text.Encoding]::UTF8.GetString($p)"
    )
    code, out = _run(["powershell", "-NoProfile", "-Command", script])
    if code != 0:
        return None
    return out.strip() or None


def _windows_set(name: str, value: str) -> bool:
    path = _windows_path(name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        return False
    # The value reaches PowerShell over stdin so it stays out of the
    # process table and the command line.
    script = (
        "$ErrorActionPreference='Stop';"
        "Add-Type -AssemblyName System.Security;"
        "$v=[Console]::In.ReadToEnd();"
        "$b=[Text.Encoding]::UTF8.GetBytes($v);"
        "$e=[Security.Cryptography.ProtectedData]::Protect("
        "$b,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);"
        f"[IO.File]::WriteAllBytes('{path}',$e)"
    )
    code, _ = _run(["powershell", "-NoProfile", "-Command", script], stdin=value)
    return code == 0


def keychain_backend() -> str | None:
    """Return the active backend name, or ``None`` when unavailable.

    ``ECHO_KEYCHAIN=off`` disables keychain access entirely, for
    containers and CI where the helpers exist but no keyring daemon does.
    """
    if (os.environ.get("ECHO_KEYCHAIN") or "").strip().lower() in {
        "off",
        "0",
        "false",
        "disabled",
    }:
        return None
    if _macos_available():
        return "macos-keychain"
    if _linux_available():
        return "secret-service"
    if _windows_available():
        return "dpapi"
    return None


def get_secret(name: str, *, env_var: str | None = None) -> str | None:
    """Return the secret for ``name``, or ``None`` when not stored.

    Resolution order: ``env_var`` (when given and set) → OS keychain →
    ``None``. Never raises.
    """
    if env_var:
        value = (os.environ.get(env_var) or "").strip()
        if value:
            return value
    backend = keychain_backend()
    if backend == "macos-keychain":
        return _macos_get(name)
    if backend == "secret-service":
        return _linux_get(name)
    if backend == "dpapi":
        return _windows_get(name)
    return None


def set_secret(name: str, value: str) -> bool:
    """Persist ``value`` under ``name``. Returns success."""
    if not value:
        return False
    backend = keychain_backend()
    if backend == "macos-keychain":
        ok = _macos_set(name, value)
    elif backend == "secret-service":
        ok = _linux_set(name, value)
    elif backend == "dpapi":
        ok = _windows_set(name, value)
    else:
        return False
    if not ok:
        _LOG.debug("secret store write failed via %s", backend)
    return ok


def get_or_create_fernet_key(name: str, *, env_var: str | None = None) -> str | None:
    """Return a urlsafe-base64 32-byte key, generating and storing one once.

    This is the entry point that turns opt-in at-rest encryption into the
    default: the first call on a machine with a keychain mints a key and
    persists it, and every later call returns the same key so previously
    encrypted files stay readable. Returns ``None`` when no keychain is
    available *and* no env var is set, which callers must treat as "keep
    the previous plaintext behavior" rather than as an error — losing the
    ability to store tokens would be worse than storing them unencrypted.

    An explicitly configured ``env_var`` is re-read on every call so a
    deployment can rotate it. The *keychain* result is cached per ``name``
    for the process lifetime, including the ``None`` outcome: callers sit on
    hot paths (the MCP token store resolves a cipher on every load *and*
    every save), each miss forks a keychain helper, and on a host where the
    keychain is readable but not writable an uncached miss would retry a
    doomed mint every single time.
    """
    if env_var:
        configured = (os.environ.get(env_var) or "").strip()
        if configured:
            return configured
    with _KEY_CACHE_LOCK:
        if name in _KEY_CACHE:
            return _KEY_CACHE[name]
    key = _resolve_or_mint_key(name)
    with _KEY_CACHE_LOCK:
        _KEY_CACHE[name] = key
    return key


def _resolve_or_mint_key(name: str) -> str | None:
    existing = get_secret(name)
    if existing:
        return existing
    if keychain_backend() is None:
        return None
    try:
        from cryptography.fernet import Fernet
    except Exception:
        # cryptography is an optional extra; without it there is nothing
        # to encrypt with, so minting a key would be pointless.
        return None
    key = Fernet.generate_key().decode("ascii")
    if not set_secret(name, key):
        # A key we cannot persist would encrypt data that becomes
        # unreadable at the next restart. Discard it.
        _LOG.warning(
            "could not persist an at-rest encryption key via %s; "
            "credentials stay unencrypted on disk",
            keychain_backend(),
        )
        return None
    _LOG.info("minted new at-rest encryption key in %s", keychain_backend())
    return key


def reset_key_cache_for_tests() -> None:
    """Drop the process-wide key cache (test seam)."""
    with _KEY_CACHE_LOCK:
        _KEY_CACHE.clear()


def require_secret(name: str, *, env_var: str | None = None) -> str:
    """Like :func:`get_secret` but raises when the secret is missing."""
    value = get_secret(name, env_var=env_var)
    if not value:
        raise SecretStoreUnavailable(
            f"secret {name!r} is not available "
            f"(no keychain backend and {env_var or 'no env var'} unset)"
        )
    return value


__all__ = [
    "SecretStoreUnavailable",
    "get_or_create_fernet_key",
    "get_secret",
    "keychain_backend",
    "require_secret",
    "reset_key_cache_for_tests",
    "set_secret",
]
