from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

from appliance import entrypoint

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "script_name",
    [
        "prepare-agent-bundle.sh",
        "prepare-agent-wheel.sh",
        "prepare-agent-resources.sh",
        "prepare-agent-codex.sh",
    ],
)
def test_agent_prepare_scripts_use_the_current_echo_repository(script_name: str):
    source = (REPO_ROOT / "deploy/appliance" / script_name).read_text()

    assert (
        'AGENT_SRC="$OS_ROOT"' in source or 'AGENT_SRC="${ECHO_BUNDLE_SOURCE:-$OS_ROOT}"' in source
    )
    assert "/../echo-agent" not in source
    assert "/../" + "octo" + "pus-agent" not in source
    assert 'mktemp "$HERE/' not in source
    assert 'mktemp -d "$HERE/' not in source


@pytest.mark.parametrize(
    "script_name",
    [
        "prepare-agent-wheel.sh",
        "prepare-agent-resources.sh",
        "prepare-agent-codex.sh",
    ],
)
def test_agent_artifact_builders_honor_the_frozen_source(script_name: str):
    source = (REPO_ROOT / "deploy/appliance" / script_name).read_text()

    assert 'if [ -n "${ECHO_AGENT_SRC:-}" ]' in source
    assert 'AGENT_SRC="$ECHO_AGENT_SRC"' in source


def test_codex_prepare_uses_only_the_captured_repository_dependency_tree():
    bundle = (REPO_ROOT / "deploy/appliance/prepare-agent-bundle.sh").read_text()
    codex = (REPO_ROOT / "deploy/appliance/prepare-agent-codex.sh").read_text()

    assert 'export ECHO_AGENT_DEPENDENCY_SRC="$AGENT_SRC"' in bundle
    assert 'DEPENDENCY_SRC="${ECHO_AGENT_DEPENDENCY_SRC:-$AGENT_SRC}"' in codex
    assert "install --frozen-lockfile --ignore-scripts" in codex
    assert 'NODE_PATH="$DEPENDENCY_SRC/frontend/node_modules${NODE_PATH:+:$NODE_PATH}"' in codex
    assert "ECHO_LINUX_ARCH=x64" in codex
    assert "OCTO" + "PUS_LINUX_ARCH" not in codex
    assert "octo" + "pus-codex-bundle.json" not in codex


def test_codex_prepare_cleans_external_staging_after_atomic_promotion():
    codex = (REPO_ROOT / "deploy/appliance/prepare-agent-codex.sh").read_text()
    promotion = codex[codex.index('"$PYTHON" "$HERE/agent_bundle.py" promote-dir') :]

    assert 'find "$STAGE" -depth -delete' in codex
    assert 'STAGE=""' not in promotion


def test_local_dev_backend_loads_the_os_appliance_extension():
    package = json.loads((REPO_ROOT / "frontend/package.json").read_text())
    scripts = package["scripts"]
    launcher = (REPO_ROOT / "frontend/scripts/dev-appliance-backend.mjs").read_text()

    assert scripts["dev:backend"] == "node scripts/dev-appliance-backend.mjs"
    assert "pnpm dev:backend" in scripts["dev:with-agent"]
    assert 'ECHO_APPLIANCE: "1"' in launcher
    assert 'ECHO_APP_EXTENSIONS: "appliance.extension"' in launcher
    assert "ECHO_APPLIANCE_TRUSTED_ORIGINS: trustedOrigins" in launcher
    assert 'ECHO_APPLIANCE_DEV_PASSWORDLESS:' in launcher
    assert 'persistedApplianceJwtSecret()' in launcher
    assert 'const frontendPort = process.env.FRONTEND_PORT || "3000"' in launcher
    assert "`http://localhost:${frontendPort}`" in launcher
    assert "`http://127.0.0.1:${frontendPort}`" in launcher
    assert "FRONTEND_PORT must be a valid TCP port" in launcher
    assert '"-m",\n    "runtime"' in launcher
    assert '"--host",\n    "127.0.0.1"' in launcher
    assert "ECHO_AGENT_UI_BASE_URL" not in launcher
    assert '"http://localhost:3001"' not in launcher
    assert "../echo-agent" not in launcher


@pytest.mark.parametrize(
    "value",
    ["127.0.0.1", "172.20.0.10", "172.20.0.0/24", "::1", "fd00::/8", ""],
)
def test_trusted_proxy_boundary_accepts_only_explicit_ip_networks(value, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)
    entrypoint._validate_trusted_proxy_ips()


@pytest.mark.parametrize("value", ["*", "reverse-proxy", "172.20.0.999"])
def test_trusted_proxy_boundary_rejects_wildcard_and_hostnames(value, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)
    with pytest.raises(RuntimeError, match="FORWARDED_ALLOW_IPS"):
        entrypoint._validate_trusted_proxy_ips()


def test_runtime_config_injects_auth_and_reserves_tentacle_for_device_link(tmp_path, monkeypatch):
    template = tmp_path / "config.example.yaml"
    template.write_text(
        "name: echo-test\nlocal_auth:\n  enabled: false\ntentacle:\n  enabled: true\n  port: 9999\n"
    )
    output = tmp_path / "data" / "echo-agent-config.yaml"
    monkeypatch.setenv("ECHO_DATA_DIR", str(output.parent))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "device-pass-123")
    monkeypatch.delenv(entrypoint.AUTH_HASH_ENV, raising=False)
    monkeypatch.delenv(entrypoint.AUTH_JWT_ENV, raising=False)

    runtime_path, generated = entrypoint.prepare_runtime_config(template, output)
    config = yaml.safe_load(runtime_path.read_text())
    stored = json.loads((output.parent / "appliance-auth.json").read_text())
    from runtime.adapters.integrations.local_auth.config import verify_password
    from runtime.platform.config.loader import load_from_yaml

    loaded = load_from_yaml(runtime_path).local_auth

    assert generated is None
    assert config["name"] == "echo-test"
    assert config["local_auth"]["enabled"] is True
    assert config["local_auth"]["allow_any_username"] is False
    assert config["local_auth"]["users"]["admin"] == f"${entrypoint.AUTH_HASH_ENV}"
    assert config["local_auth"]["jwt_secret"] == f"${entrypoint.AUTH_JWT_ENV}"
    assert config["tentacle"] == {"enabled": False, "port": 9999}
    assert os.environ[entrypoint.AUTH_HASH_ENV] == stored["password_hash"]
    assert os.environ[entrypoint.AUTH_JWT_ENV] == stored["jwt_secret"]
    assert loaded.users["admin"] == stored["password_hash"]
    assert loaded.jwt_secret == stored["jwt_secret"]
    assert verify_password("device-pass-123", loaded.users["admin"])
    assert runtime_path.stat().st_mode & 0o777 == 0o600


def test_runtime_config_references_each_family_hash_without_writing_it(tmp_path, monkeypatch):
    template = tmp_path / "config.example.yaml"
    template.write_text("name: echo-test\n")
    output = tmp_path / "data" / "echo-agent-config.yaml"
    monkeypatch.setenv("ECHO_DATA_DIR", str(output.parent))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "device-pass-123")

    from appliance.agent_api.auth import hash_password
    from appliance.auth import load_or_bootstrap_auth, read_auth_store, write_auth_store

    load_or_bootstrap_auth()
    store = read_auth_store()
    alice_hash = hash_password("Alice-independent-Echo-42!")
    store["accounts"]["alice"] = {
        "display_name": "Alice",
        "role": "member",
        "password_hash": alice_hash,
        "omv_username": "alice",
        "active": True,
    }
    write_auth_store(store)

    runtime_path, _generated = entrypoint.prepare_runtime_config(template, output)
    runtime_text = runtime_path.read_text()
    config = yaml.safe_load(runtime_text)
    member_environment = entrypoint._password_hash_environment("alice")

    assert config["local_auth"]["users"]["alice"] == f"${member_environment}"
    assert os.environ[member_environment] == alice_hash
    assert alice_hash not in runtime_text


def test_runtime_config_rejects_a_non_mapping_tentacle_section(tmp_path, monkeypatch):
    template = tmp_path / "config.example.yaml"
    template.write_text("name: echo-test\ntentacle: shared-token\n")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "device-pass-123")

    with pytest.raises(RuntimeError, match="tentacle section must be a mapping"):
        entrypoint.prepare_runtime_config(template, tmp_path / "data/runtime.yaml")


def test_main_rewrites_explicit_config_to_secure_runtime_copy(tmp_path, monkeypatch):
    from appliance import agent_ui

    template = tmp_path / "custom.yaml"
    runtime_config = tmp_path / "runtime.yaml"
    observed: dict[str, object] = {}

    def _prepare(template_path: Path, output_path: Path):
        observed["template"] = template_path
        observed["output"] = output_path
        return runtime_config, None

    def _exec(program: str, args: list[str]):
        observed["program"] = program
        observed["args"] = args
        raise OSError("exec captured")

    monkeypatch.setenv("ECHO_RUNTIME_CONFIG", str(runtime_config))
    for name in (
        "ECHO_PACKAGED_CODEX_VERSION",
        "ECHO_APPLIANCE",
        "ECHO_APP_EXTENSIONS",
        "ECHO_SKILL_EXTENSIONS",
    ):
        # Track an initially absent key so values written by entrypoint.main()
        # are removed even though the mocked exec call returns via exception.
        monkeypatch.setenv(name, "pytest-restore-guard")
        monkeypatch.delenv(name)
    monkeypatch.setattr(entrypoint, "_drop_container_privileges", lambda _root: None)
    monkeypatch.setattr(entrypoint, "prepare_runtime_config", _prepare)
    monkeypatch.setattr(
        agent_ui,
        "agent_bundle_status",
        lambda: {
            "source_id": "a" * 40,
            "version": "1.2.3",
            "packaged_codex_version": "0.149.0",
        },
    )
    monkeypatch.setattr(os, "execv", _exec)

    with pytest.raises(OSError, match="exec captured"):
        entrypoint.main(["serve", "--config", str(template), "--port", "9000"])

    assert observed["template"] == template
    assert observed["output"] == runtime_config
    assert observed["program"] == sys.executable
    assert os.environ["ECHO_PACKAGED_CODEX_VERSION"] == "0.149.0"
    assert os.environ["ECHO_APPLIANCE"] == "1"
    assert os.environ["ECHO_APP_EXTENSIONS"] == "appliance.extension"
    assert os.environ["ECHO_SKILL_EXTENSIONS"] == "appliance.pm_skills:register_pm_skills"
    assert observed["args"] == [
        sys.executable,
        "-m",
        "runtime",
        "serve",
        "--config",
        str(runtime_config),
        "--port",
        "9000",
    ]


def test_container_entrypoint_migrates_state_but_never_nas_data(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    nas_root = data_root / "nas"
    internal = data_root / "journal" / "events.jsonl"
    nas_file = nas_root / "family-photo.jpg"
    internal.parent.mkdir(parents=True)
    nas_root.mkdir()
    internal.write_text("state")
    nas_file.write_text("user-data")
    state: dict[str, object] = {"uid": 0}
    chowned: list[Path] = []
    commands: list[list[str]] = []

    monkeypatch.setenv("ECHO_PUID", "1001")
    monkeypatch.setenv("ECHO_PGID", "1002")
    monkeypatch.setenv("ECHO_NAS_ROOT", str(nas_root))
    monkeypatch.setenv("HOME", "before-home")
    monkeypatch.setenv("USER", "before-user")
    monkeypatch.setenv("LOGNAME", "before-logname")
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: state["uid"])
    monkeypatch.setattr(
        entrypoint.os,
        "chown",
        lambda path, _uid, _gid, **_kwargs: chowned.append(Path(path)),
    )
    monkeypatch.setattr(
        entrypoint.subprocess,
        "run",
        lambda args, check: commands.append(list(args)),
    )
    monkeypatch.setattr(entrypoint.os, "setgroups", lambda value: state.update(groups=value))
    monkeypatch.setattr(entrypoint.os, "setgid", lambda value: state.update(gid=value))
    monkeypatch.setattr(entrypoint.os, "setuid", lambda value: state.update(uid=value))
    monkeypatch.setattr(entrypoint.os, "umask", lambda _value: 0o022)

    entrypoint._drop_container_privileges(data_root)

    assert data_root in chowned
    assert internal in chowned
    assert data_root / entrypoint.OWNER_MARKER in chowned
    assert nas_root not in chowned
    assert nas_file not in chowned
    assert commands == [
        ["groupmod", "-o", "-g", "1002", "echo"],
        ["usermod", "-o", "-u", "1001", "-g", "1002", "echo"],
    ]
    assert state == {"uid": 1001, "gid": 1002, "groups": [1002]}
    assert os.environ["HOME"] == str(data_root)
    assert (data_root / entrypoint.OWNER_MARKER).read_text() == "1001:1002\n"


@pytest.mark.parametrize(
    ("name", "value"),
    [("ECHO_PUID", "0"), ("ECHO_PGID", "-1"), ("ECHO_PUID", "root")],
)
def test_container_entrypoint_rejects_unsafe_numeric_identity(name, value, tmp_path, monkeypatch):
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 0)
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        entrypoint._drop_container_privileges(tmp_path / "data")
