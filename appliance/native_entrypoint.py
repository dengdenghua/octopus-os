"""Fail-closed entrypoint for the Agent bundled into the native OS image."""

from __future__ import annotations

import os
import re
import secrets
import stat
import sys
from pathlib import Path

CI_SESSION_CREDENTIAL = "echo.os.ci-session"


def _has_ci_session_credential() -> bool:
    raw_directory = os.environ.get("CREDENTIALS_DIRECTORY", "").strip()
    if not raw_directory:
        return False
    directory = Path(raw_directory)
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
        raise RuntimeError("native Agent credentials directory is unsafe")
    credential = directory / CI_SESSION_CREDENTIAL
    if credential.is_symlink() or not credential.is_file():
        raise RuntimeError("native Agent CI credential is unsafe or missing")
    info = credential.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_size != 1:
        raise RuntimeError("native Agent CI credential is invalid")
    if credential.read_bytes() != b"1":
        raise RuntimeError("native Agent CI credential is invalid")
    return True


def _provision_ci_auth() -> bool:
    """Create opaque test-only auth when the boot manager supplied its credential."""

    if os.environ.get("ECHO_APPLIANCE_REQUIRE_PROVISIONED_AUTH") != "1":
        return False
    from appliance.auth import auth_store_path

    if auth_store_path().exists() or not _has_ci_session_credential():
        return False

    from appliance.auth import load_or_bootstrap_auth

    password = secrets.token_urlsafe(32)
    os.environ["ECHO_ADMIN_PASSWORD"] = password
    try:
        _config, generated = load_or_bootstrap_auth()
        if generated is not None:
            raise RuntimeError("native Agent CI authentication was not explicitly provisioned")
    finally:
        os.environ.pop("ECHO_ADMIN_PASSWORD", None)
        password = ""
    return True


def _port() -> str:
    value = os.environ.get("ECHO_NATIVE_AGENT_PORT", "8000").strip()
    if re.fullmatch(r"[0-9]{1,5}", value) is None or not 1 <= int(value) <= 65535:
        raise RuntimeError("ECHO_NATIVE_AGENT_PORT must be a valid TCP port")
    return value


def main() -> int:
    """Verify provenance, then replace this process with the official CLI.

    Native Echo OS intentionally binds only to loopback.  Authentication at
    the device boundary is provided by SDDM/PAM, while a non-loopback Agent
    bind remains rejected by the upstream runtime's own safety gate.
    """

    if os.environ.get("ECHO_NATIVE_OS") != "1":
        raise RuntimeError("native Agent entrypoint requires ECHO_NATIVE_OS=1")

    from appliance.agent_ui import agent_bundle_status

    bundle = agent_bundle_status()
    if not bundle or not bundle.get("verified"):
        raise RuntimeError("native Echo OS Agent bundle did not verify")

    source_id = str(bundle.get("source_id") or "").strip()
    if re.fullmatch(r"[0-9a-f]{40}", source_id) is None:
        raise RuntimeError("native Echo OS Agent bundle has no clean source revision")
    os.environ["ECHO_RUNTIME_SOURCE_ID"] = source_id
    os.environ["ECHO_RUNTIME_BUNDLE_VERIFIED"] = "1"
    _provision_ci_auth()

    codex_version = str(bundle.get("packaged_codex_version") or "").strip()
    if codex_version:
        os.environ.setdefault("ECHO_PACKAGED_CODEX_VERSION", codex_version)

    config = os.environ.get("ECHO_NATIVE_AGENT_CONFIG", "").strip()
    if not config or not os.path.isabs(config):
        raise RuntimeError("ECHO_NATIVE_AGENT_CONFIG must name an absolute file")
    if os.path.islink(config) or not os.path.isfile(config):
        raise RuntimeError("ECHO_NATIVE_AGENT_CONFIG must name a regular file")
    argv = [
        sys.executable,
        "-m",
        "runtime.cli",
        "serve",
        "--config",
        config,
        "--host",
        "127.0.0.1",
        "--port",
        _port(),
    ]

    print(
        "Echo OS native Agent verified: "
        f"source={source_id} version={bundle['version']} loopback=127.0.0.1",
        file=sys.stderr,
        flush=True,
    )
    os.execv(sys.executable, argv)
    return 127  # pragma: no cover - os.execv only returns by raising


if __name__ == "__main__":
    raise SystemExit(main())
