"""CLI 连接器生命周期:init/version/runtime/设备流解析 + oneid-token 注入。

Hermetic —— 不执行真实 CLI;subprocess 与 Popen 全部 monkeypatch。
"""

from __future__ import annotations

import io

from runtime.platform.connectors import cli_lifecycle
from runtime.platform.connectors.auth_orchestrator import AuthOrchestrator
from runtime.platform.connectors.connector_registry import ConnectorDefinition
from runtime.platform.connectors.credential_store import CredentialStore


def _conn(cli: dict | None = None, auth_mode: str = "token") -> ConnectorDefinition:
    return ConnectorDefinition(id="fake", name="Fake", cli=cli or {}, auth_mode=auth_mode)


# ── 版本 ──────────────────────────────────────────────
def test_version_ge():
    assert cli_lifecycle.version_ge("2.2.1", "2.2.0") is True
    assert cli_lifecycle.version_ge("2.2", "2.2.1") is False
    assert cli_lifecycle.version_ge("5.2.0", "5.2.0") is True
    assert cli_lifecycle.version_ge("5.2.0", "5.1.9") is True


# ── versionCheck / runtime / init ─────────────────────
def test_check_version_passes(monkeypatch):
    def fake_run(cmd, *a, **k):  # noqa: ARG001

        class R:
            returncode = 0
            stdout = "xparse-cli version v2.3.0"
            stderr = ""

        return R()

    monkeypatch.setattr(cli_lifecycle.subprocess, "run", fake_run)
    res = cli_lifecycle.check_version(
        _conn(
            {
                "versionCheck": {
                    "command": "xparse-cli version",
                    "minVersion": "2.2.1",
                    "versionPattern": r"v?(\d+\.\d+\.\d+)",
                }
            }
        )
    )
    assert res["ok"] is True
    assert res["version"] == "2.3.0"


def test_check_version_too_low(monkeypatch):
    def fake_run(cmd, *a, **k):  # noqa: ARG001
        class R:
            returncode = 0
            stdout = "xparse-cli version v2.0.0"
            stderr = ""

        return R()

    monkeypatch.setattr(cli_lifecycle.subprocess, "run", fake_run)
    res = cli_lifecycle.check_version(
        _conn(
            {
                "versionCheck": {
                    "command": "xparse-cli version",
                    "minVersion": "2.2.1",
                    "versionPattern": r"v?(\d+\.\d+\.\d+)",
                }
            }
        )
    )
    assert res["ok"] is False
    assert "版本过低" in (res["error"] or "")


def test_run_init_executes(monkeypatch):
    captured = {}

    def fake_run(cmd, *a, **k):  # noqa: ARG001
        captured["cmd"] = cmd

        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return R()

    monkeypatch.setattr(cli_lifecycle.subprocess, "run", fake_run)
    res = cli_lifecycle.run_init(_conn({"init": "npm install -g xparse-cli"}))
    assert res["ok"] is True
    assert captured["cmd"][0] == "npm"
    assert captured["cmd"][-1] == "xparse-cli"


def test_detect_command_uses_declared_executable_only_when_called(tmp_path):
    executable = tmp_path / "opencode"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    result = cli_lifecycle.detect_command(
        _conn({"detect": {"commands": [str(executable), "opencode"]}})
    )

    assert result == {
        "found": True,
        "command": str(executable),
        "executable": str(executable.resolve()),
    }


def test_run_init_skips_install_when_declared_cli_already_exists(monkeypatch):
    monkeypatch.setattr(
        cli_lifecycle,
        "detect_command",
        lambda conn: {
            "found": True,
            "command": "opencode",
            "executable": "/tmp/bin/opencode",
        },
    )

    def unexpected_run(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("init command should not run")

    monkeypatch.setattr(cli_lifecycle.subprocess, "run", unexpected_run)
    result = cli_lifecycle.run_init(
        _conn(
            {
                "detect": {"commands": ["opencode"]},
                "initIfMissing": True,
                "init": "npm install -g opencode-ai@latest",
            }
        )
    )

    assert result["ok"] is True
    assert result["reason"] == "already_installed"


def test_runtime_node_missing(monkeypatch):
    def fake_run(cmd, *a, **k):  # noqa: ARG001
        raise FileNotFoundError("node")

    monkeypatch.setattr(cli_lifecycle.subprocess, "run", fake_run)
    res = cli_lifecycle.check_runtime(_conn({"runtime": {"type": "node", "version": ">=18"}}))
    assert res["ok"] is False
    assert "node" in (res["error"] or "")


# ── 设备流解析 / URI 校验 ─────────────────────────────
def test_extract_device_flow_textin():
    spec = {
        "uriPattern": r"\"verification_uri_complete\"\s*:\s*\"(https?://[^\"]+)\"",
        "codePattern": r"\"user_code\"\s*:\s*\"([A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4})\"",
    }
    out = '{"verification_uri_complete": "https://api.textin.com/auth?code=ABCD-EFGH", "user_code": "ABCD-EFGH"}'
    r = cli_lifecycle.extract_device_flow(out, spec)
    assert r["uri"] == "https://api.textin.com/auth?code=ABCD-EFGH"
    assert r["code"] == "ABCD-EFGH"


def test_validate_auth_uri_allows_subdomain():
    conn = _conn({"authUrlDomain": "api.textin.com"})
    assert cli_lifecycle.validate_auth_uri("https://api.textin.com/auth?code=x", conn) is True
    assert cli_lifecycle.validate_auth_uri("https://evil.com/auth", conn) is False
    assert cli_lifecycle.validate_auth_uri("https://sub.api.textin.com/auth", conn) is True


# ── 设备流会话(AuthOrchestrator)───────────────────────
def test_start_device_flow_returns_uri(monkeypatch, tmp_path):
    creds = CredentialStore(
        root=tmp_path, master_key_file=tmp_path / "key", credentials_file=tmp_path / "c.json"
    )
    orch = AuthOrchestrator(credentials=creds)
    conn = _conn(
        {
            "auth": "fake-cli auth device --output=jsonl",
            "authUrlDomain": "api.textin.com",
            "authDeviceFlow": {
                "uriPattern": r"\"verification_uri_complete\"\s*:\s*\"(https?://[^\"]+)\"",
                "codePattern": r"\"user_code\"\s*:\s*\"([A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4})\"",
                "defaultExpiresInSeconds": 240,
                "codeEmbeddedInUri": True,
            },
        }
    )

    class FakeProc:
        def __init__(self):
            self.poll_calls = 0
            self.stdout = io.StringIO(
                '{"verification_uri_complete": "https://api.textin.com/auth?code=ABCD-EFGH", "user_code": "ABCD-EFGH"}\n'
            )

        def poll(self):
            self.poll_calls += 1
            return 0

        def terminate(self):
            pass

    fake = FakeProc()
    monkeypatch.setattr(orch.__class__, "resolve_env", lambda self, c: {})
    monkeypatch.setattr(
        "runtime.platform.connectors.auth_orchestrator.subprocess.Popen",
        lambda *a, **k: fake,
    )
    res = orch.start_device_flow(conn)
    assert res["connected"] is False
    df = res["device_flow"]
    assert df["verification_uri"] == "https://api.textin.com/auth?code=ABCD-EFGH"
    assert df["user_code"] == "ABCD-EFGH"
    assert df["code_embedded_in_uri"] is True
    assert df["flow_id"]
    # cancel 清理
    orch.cancel_device_flow(conn, expected_flow_id=df["flow_id"])
    assert orch.device_flow_status(conn)["active"] is False


def test_oneid_token_injected_header(tmp_path):
    creds = CredentialStore(
        root=tmp_path, master_key_file=tmp_path / "key", credentials_file=tmp_path / "c.json"
    )
    orch = AuthOrchestrator(credentials=creds)
    conn = _conn(auth_mode="oneid-token")
    creds.set_secret("fake", "oneid_token", "tok-123")
    headers = orch.resolve_headers(conn)
    assert headers.get("X-ONEID-ACCESS-TOKEN") == "tok-123"

