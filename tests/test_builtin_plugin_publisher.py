from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def test_configure_builtin_publisher_cross_checks_keypair(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    output = tmp_path / "builtin-publishers.json"
    env = os.environ.copy()
    env.update(
        {
            "ECHO_PLUGIN_SIGNING_PRIVATE_KEY": base64.b64encode(private_bytes).decode(),
            "ECHO_PLUGIN_SIGNING_PUBLIC_KEY": base64.b64encode(public_bytes).decode(),
            "ECHO_PLUGIN_SIGNING_PUBLISHER_ID": "echoai",
            "ECHO_PLUGIN_SIGNING_KEY_ID": "release-2026",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/configure_builtin_plugin_publisher.py",
            "--output",
            str(output),
        ],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema"] == "echo.plugin_publisher_trust_store.v1"
    assert payload["publishers"][0]["publisher_id"] == "echoai"
    assert payload["publishers"][0]["keys"][0]["key_id"] == "release-2026"

    env["ECHO_PLUGIN_SIGNING_PUBLIC_KEY"] = base64.b64encode(b"x" * 32).decode()
    mismatch = subprocess.run(
        [
            sys.executable,
            "scripts/configure_builtin_plugin_publisher.py",
            "--output",
            str(tmp_path / "mismatch.json"),
        ],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert mismatch.returncode != 0
    assert "do not match" in mismatch.stderr


