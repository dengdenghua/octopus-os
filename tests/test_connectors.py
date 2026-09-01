"""连接器认证编排层测试:加密凭据库 + 注册表 + auth 编排器 + 网关路由。"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import contextlib
import json
import multiprocessing
import os
import shlex
import stat
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from runtime.platform.capabilities.tenant_context import use_capability_scope
from runtime.platform.connectors.auth_orchestrator import AuthOrchestrator, DeviceFlowSession
from runtime.platform.connectors.connector_registry import ConnectorDefinition, ConnectorRegistry
from runtime.platform.connectors.credential_store import CredentialStore
from runtime.safety.auth.scope import TenantScope

# 仓库内 WorkBuddy 连接器 fork(与 ConnectorRegistry 默认一致)
FORK = Path(__file__).resolve().parents[1] / "extensions" / "workbuddy-connectors"


def _refresh_process_connector() -> ConnectorDefinition:
    return ConnectorDefinition(
        id="cross-process-refresher",
        auth_mode="token",
        cli={
            "tokenRefresh": {
                "command": shlex.join([sys.executable, "-c", "import time; time.sleep(60)"]),
                "storeKey": "access_token",
                "tokenPattern": "access_token=([A-Za-z0-9_-]+)",
                "defaultExpiresInSeconds": 300,
            }
        },
    )


def _run_refresh_owner_process(root: str, ready, results) -> None:
    """Spawn/supervise a refresh child in another interpreter process."""

    child = None
    orchestrator = None
    try:
        orchestrator = AuthOrchestrator(credentials=CredentialStore(root=root))
        conn = _refresh_process_connector()
        orchestrator.connect(conn, tokens={"access_token": "initial"})
        with orchestrator._refresher._lock:
            entry = orchestrator._refresher._entries[conn.id]
        deadline = time.time() + 5
        while time.time() < deadline:
            with entry.child_lock:
                child = entry.child
            if child is not None and orchestrator._credentials.refresh_lease(conn.id):
                break
            time.sleep(0.01)
        if child is None:
            raise RuntimeError("refresh child did not start")
        results.put({"child_pid": child.pid})
        ready.set()

        deadline = time.time() + 10
        while time.time() < deadline and orchestrator._refresher.is_scheduled(conn.id):
            time.sleep(0.01)
        results.put(
            {
                "scheduled": orchestrator._refresher.is_scheduled(conn.id),
                "child_exit": child.poll(),
            }
        )
    except BaseException as exc:  # pragma: no cover - relayed to the parent assertion
        results.put({"error": repr(exc)})
        ready.set()
        raise
    finally:
        if orchestrator is not None:
            with contextlib.suppress(Exception):
                orchestrator._refresher.stop("cross-process-refresher")


class TestCredentialStore:
    def test_credentials_are_partitioned_by_tenant_and_principal(self, tmp_path):
        store = CredentialStore(root=tmp_path)
        alice = TenantScope(tenant_id="family", actor_id="alice")
        bob = TenantScope(tenant_id="family", actor_id="bob")

        with use_capability_scope(alice):
            store.set_secret("documents", "access_token", "alice-token")
            alice_identity = store.storage_identity
        with use_capability_scope(bob):
            assert store.get_secret("documents", "access_token") is None
            store.set_secret("documents", "access_token", "bob-token")
            bob_identity = store.storage_identity
        with use_capability_scope(alice):
            assert store.get_secret("documents", "access_token") == "alice-token"
        with use_capability_scope(bob):
            assert store.get_secret("documents", "access_token") == "bob-token"

        assert alice_identity != bob_identity
        assert not (tmp_path / "credentials.v1.json").exists()

    def test_root_override_owns_default_files(self, tmp_path):
        root = tmp_path / "isolated-connectors"

        store = CredentialStore(root=root)
        store.set_secret("x", "token", "secret")

        assert (root / "master.key").is_file()
        assert (root / "credentials.v1.json").is_file()
        assert store.get_secret("x", "token") == "secret"

    def test_roundtrip_encrypted(self, tmp_path):
        s = CredentialStore(
            root=tmp_path,
            master_key_file=tmp_path / "key",
            credentials_file=tmp_path / "cred.json",
        )
        s.set_secret("westock-mcp", "access_token", "top-secret-42")
        assert s.get_secret("westock-mcp", "access_token") == "top-secret-42"
        assert s.has_credentials("westock-mcp")
        assert s.list_secrets("westock-mcp") == ["access_token"]

    def test_no_plaintext_on_disk(self, tmp_path):
        s = CredentialStore(
            root=tmp_path,
            master_key_file=tmp_path / "key",
            credentials_file=tmp_path / "cred.json",
        )
        s.set_secret("x", "api_key", "SUPER-SECRET-VALUE")
        raw = (tmp_path / "cred.json").read_text()
        assert "SUPER-SECRET-VALUE" not in raw
        assert '"nonce"' in raw and '"ciphertext"' in raw

    def test_delete_and_clear(self, tmp_path):
        s = CredentialStore(
            root=tmp_path,
            master_key_file=tmp_path / "key",
            credentials_file=tmp_path / "cred.json",
        )
        s.set_secret("a", "k1", "v1")
        s.set_secret("a", "k2", "v2")
        assert s.delete_secret("a", "k1") is True
        assert s.list_secrets("a") == ["k2"]
        assert s.clear_connector("a") is True
        assert not s.has_credentials("a")

    def test_concurrent_updates_do_not_lose_secrets(self, tmp_path):
        store = CredentialStore(root=tmp_path)

        def write(index: int) -> None:
            store.set_secret("parallel", f"key-{index}", f"value-{index}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            list(executor.map(write, range(40)))

        assert set(store.list_secrets("parallel")) == {f"key-{index}" for index in range(40)}
        for index in range(40):
            assert store.get_secret("parallel", f"key-{index}") == f"value-{index}"

    def test_failed_atomic_replace_preserves_original_file(self, tmp_path, monkeypatch):
        store = CredentialStore(root=tmp_path)
        store.set_secret("x", "first", "value-1")
        credential_file = tmp_path / "credentials.v1.json"
        original = credential_file.read_bytes()
        real_replace = os.replace

        def fail_credential_replace(source, destination):
            if Path(destination) == credential_file:
                raise OSError("injected replace failure")
            real_replace(source, destination)

        monkeypatch.setattr(os, "replace", fail_credential_replace)
        with pytest.raises(OSError, match="injected replace failure"):
            store.set_secret("x", "second", "value-2")

        assert credential_file.read_bytes() == original
        assert store.get_secret("x", "first") == "value-1"
        assert store.get_secret("x", "second") is None
        assert not list(tmp_path.glob(".credentials.v1.json.*.tmp"))

    def test_existing_files_are_tightened_to_owner_only(self, tmp_path):
        store = CredentialStore(root=tmp_path)
        store.set_secret("x", "token", "secret")
        key_file = tmp_path / "master.key"
        credential_file = tmp_path / "credentials.v1.json"
        key_file.chmod(0o666)
        credential_file.chmod(0o666)

        CredentialStore(root=tmp_path)

        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(credential_file.stat().st_mode) == 0o600

    @pytest.mark.parametrize(
        "encoded_key",
        [b"not-base64!", base64.b64encode(b"too-short")],
    )
    def test_invalid_existing_master_key_fails_closed(self, tmp_path, encoded_key):
        key_file = tmp_path / "master.key"
        key_file.write_bytes(encoded_key)

        with pytest.raises(RuntimeError, match="connector master key"):
            CredentialStore(root=tmp_path)

        assert key_file.read_bytes() == encoded_key
        assert not (tmp_path / "credentials.v1.json").exists()

    def test_missing_master_key_for_existing_credentials_fails_closed(self, tmp_path):
        credential_file = tmp_path / "credentials.v1.json"
        credential_file.write_text(
            '{"version": 1, "connectors": {"x": {}}}',
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="master key is missing"):
            CredentialStore(root=tmp_path)

        assert not (tmp_path / "master.key").exists()

    def test_two_instances_share_first_key_and_preserve_parallel_connector_writes(
        self,
        tmp_path,
        monkeypatch,
    ):
        from runtime.platform.connectors import credential_store as credential_module

        generated_key_lengths: list[int] = []
        generated_lock = threading.Lock()
        real_token_bytes = credential_module.secrets.token_bytes

        def tracked_token_bytes(length: int) -> bytes:
            value = real_token_bytes(length)
            if length == 32:
                with generated_lock:
                    generated_key_lengths.append(length)
            return value

        monkeypatch.setattr(credential_module.secrets, "token_bytes", tracked_token_bytes)
        constructor_barrier = threading.Barrier(2)

        def construct() -> CredentialStore:
            constructor_barrier.wait(timeout=2)
            return CredentialStore(root=tmp_path)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            stores = list(executor.map(lambda _index: construct(), range(2)))

        first, second = stores
        assert generated_key_lengths == [32]
        assert first._key == second._key

        first.set_secret("seed", "token", "ready")
        credential_file = tmp_path / "credentials.v1.json"
        parse_errors: list[Exception] = []
        stop_reader = threading.Event()
        reader_ready = threading.Event()

        def observe_json() -> None:
            while not stop_reader.wait(0.0005):
                try:
                    json.loads(credential_file.read_text(encoding="utf-8"))
                    reader_ready.set()
                except Exception as exc:  # pragma: no cover - regression evidence
                    parse_errors.append(exc)
                    return

        update_barrier = threading.Barrier(2)
        first_mutate = first._mutate_all
        second_mutate = second._mutate_all

        def first_gated(mutate):
            update_barrier.wait(timeout=2)
            return first_mutate(mutate)

        def second_gated(mutate):
            update_barrier.wait(timeout=2)
            return second_mutate(mutate)

        monkeypatch.setattr(first, "_mutate_all", first_gated)
        monkeypatch.setattr(second, "_mutate_all", second_gated)
        reader = threading.Thread(target=observe_json)
        reader.start()
        try:
            assert reader_ready.wait(timeout=1)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                writes = [
                    executor.submit(first.set_secret, "connector-a", "token", "alpha"),
                    executor.submit(second.set_secret, "connector-b", "token", "bravo"),
                ]
                for future in writes:
                    future.result(timeout=3)
        finally:
            stop_reader.set()
            reader.join(timeout=2)

        assert not parse_errors
        assert first.get_secret("connector-b", "token") == "bravo"
        assert second.get_secret("connector-a", "token") == "alpha"


class TestConnectorRegistry:
    def test_list_fork(self):
        reg = ConnectorRegistry(marketplace_root=FORK)
        conns = reg.list()
        assert len(conns) == 111
        types = {c["type"] for c in conns}
        assert {"mcp", "cli", "skill-only"} <= types

    def test_get_details(self):
        reg = ConnectorRegistry(marketplace_root=FORK)
        feishu = reg.get("feishu")
        assert feishu is not None
        assert feishu.type == "cli"
        assert feishu.skill_count() >= 1
        assert feishu.cli.get("auth")

        westock = reg.get("westock-mcp")
        assert westock is not None
        assert "westock-mcp" in westock.mcp_servers
        assert westock.mcp_servers["westock-mcp"]["type"] == "streamableHttp"

        # These servers expose standard MCP OAuth discovery. They must not be
        # treated as WorkBuddy-owned server-side sessions or the UI will skip
        # Echo' PKCE authorization flow.
        linear = reg.get("linear-mcp")
        tdx = reg.get("tdx-connector")
        assert linear is not None and linear.auth_mode == "oauth"
        assert tdx is not None and tdx.auth_mode == "oauth"
        assert "tdx-finance" in tdx.mcp_servers

        freebuff = reg.get("freebuff-cli")
        assert freebuff is not None
        assert freebuff.type == "cli"
        assert freebuff.source == "echo"
        assert freebuff.cli["detect"]["commands"] == ["freebuff"]
        assert freebuff.cli["initIfMissing"] is True
        assert freebuff.cli["auth"]["darwin"] == "freebuff login"
        assert freebuff.cli["authUrlDomain"] == "freebuff.com"
        assert freebuff.cli["authDeviceFlow"]["uriPattern"]
        assert freebuff.skill_count() == 1

    def test_opencode_zen_install_never_runs_cli_detection(self, tmp_path, monkeypatch):
        from runtime.platform.connectors import cli_lifecycle

        monkeypatch.setattr(
            cli_lifecycle,
            "detect_command",
            lambda _conn: (_ for _ in ()).throw(AssertionError("CLI detection is forbidden")),
        )
        reg = ConnectorRegistry(
            marketplace_root=FORK,
            skills_root=tmp_path / "skills",
            state_file=tmp_path / "state.json",
        )

        catalog_item = next(item for item in reg.list() if item["id"] == "opencode-zen")
        assert catalog_item["installed"] is False
        assert catalog_item["type"] == "plugin"
        assert catalog_item["mcp_servers"] == []
        assert catalog_item["model_provider"]["base_url"] == "https://opencode.ai/zen/v1"
        assert "big-pickle" in catalog_item["model_provider"]["free_models"]

        result = reg.install("opencode-zen")
        assert result["installed"] is True
        assert result["cli_lifecycle"] == {"has_cli": False}
        assert reg.installed_ids() == {"opencode-zen"}

    def test_freebuff2api_is_an_explicit_community_model_adapter(self, tmp_path):
        reg = ConnectorRegistry(
            marketplace_root=FORK,
            skills_root=tmp_path / "skills",
            state_file=tmp_path / "state.json",
        )

        item = next(item for item in reg.list() if item["id"] == "freebuff2api-community")
        assert item["type"] == "plugin"
        assert item["mcp_servers"] == []
        assert item["model_provider"]["configurable_base_url"] is True
        assert item["model_provider"]["discover_all_models"] is True
        assert "非 Freebuff 官方" in item["description_zh"]

    def test_cli_install_defers_processes_until_exact_permission_grant(
        self,
        tmp_path,
        monkeypatch,
    ):
        from runtime.platform.connectors import cli_lifecycle

        calls: list[str] = []
        monkeypatch.setattr(
            cli_lifecycle,
            "detect_command",
            lambda _conn: calls.append("detect") or {"found": True},
        )
        monkeypatch.setattr(
            cli_lifecycle,
            "check_runtime",
            lambda _conn: calls.append("runtime") or {"ok": True},
        )
        monkeypatch.setattr(
            cli_lifecycle,
            "run_init",
            lambda _conn, env=None: calls.append("init") or {"ok": True},
        )
        monkeypatch.setattr(
            cli_lifecycle,
            "check_version",
            lambda _conn: calls.append("version") or {"ok": True},
        )
        reg = ConnectorRegistry(
            marketplace_root=FORK,
            skills_root=tmp_path / "skills",
            state_file=tmp_path / "state.json",
        )

        installed = reg.install("feishu")

        assert calls == []
        assert installed["cli_lifecycle"] == {"has_cli": True, "deferred": True}
        assert installed["permission_review_required"] is True
        with pytest.raises(PermissionError, match="require review"):
            reg.set_enabled("feishu", True)

        reg.grant_permissions("feishu", installed["permissions"])
        assert reg.set_enabled("feishu", True) is True
        assert calls == ["detect", "runtime", "init", "detect", "version"]
        assert reg._permissions.get("feishu")["active"] is True

    def test_install_uninstall_skills(self, tmp_path):
        reg = ConnectorRegistry(
            marketplace_root=FORK, skills_root=tmp_path / "skills", state_file=tmp_path / "st.json"
        )
        res = reg.install("westock-mcp")
        assert res["installed"] is True
        assert res["copied_skills"], "should have copied skills"
        assert reg.installed_ids() == {"westock-mcp"}
        # registry.json 登记
        regfile = tmp_path / "skills" / "registry.json"
        assert regfile.exists()
        names = [e["name"] for e in json.loads(regfile.read_text("utf-8"))]
        assert names, "registry should be rebuilt"

        assert reg.uninstall("westock-mcp") is True
        assert reg.installed_ids() == set()

    def test_two_instances_preserve_parallel_enable_disable_updates(
        self,
        tmp_path,
        monkeypatch,
    ):
        state_file = tmp_path / "state.json"
        skills_root = tmp_path / "skills"
        first = ConnectorRegistry(
            marketplace_root=FORK,
            skills_root=skills_root,
            state_file=state_file,
        )
        second = ConnectorRegistry(
            marketplace_root=FORK,
            skills_root=skills_root,
            state_file=state_file,
        )
        first._set_state("westock-mcp", installed=True, enabled=False)
        first._set_state("cnb-api", installed=True, enabled=True)
        requirements = first._requirements("westock-mcp")
        first._permissions.stage(
            "westock-mcp",
            kind="connector",
            required=requirements["permissions"],
        )
        first.grant_permissions("westock-mcp", requirements["permissions"])

        barrier = threading.Barrier(2)
        first_mutate = first._mutate_state
        second_mutate = second._mutate_state

        def first_gated(mutate):
            barrier.wait(timeout=2)
            return first_mutate(mutate)

        def second_gated(mutate):
            barrier.wait(timeout=2)
            return second_mutate(mutate)

        monkeypatch.setattr(first, "_mutate_state", first_gated)
        monkeypatch.setattr(second, "_mutate_state", second_gated)
        parse_errors: list[Exception] = []
        stop_reader = threading.Event()
        reader_ready = threading.Event()

        def observe_json() -> None:
            while not stop_reader.wait(0.0005):
                try:
                    json.loads(state_file.read_text(encoding="utf-8"))
                    reader_ready.set()
                except Exception as exc:  # pragma: no cover - regression evidence
                    parse_errors.append(exc)
                    return

        reader = threading.Thread(target=observe_json)
        reader.start()
        try:
            assert reader_ready.wait(timeout=1)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                updates = [
                    executor.submit(first.set_enabled, "westock-mcp", True),
                    executor.submit(second.set_enabled, "cnb-api", False),
                ]
                assert [future.result(timeout=3) for future in updates] == [True, True]
        finally:
            stop_reader.set()
            reader.join(timeout=2)

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert not parse_errors
        assert state["westock-mcp"]["enabled"] is True
        assert state["cnb-api"]["enabled"] is False

    def test_two_instances_preserve_parallel_install_uninstall_updates(
        self,
        tmp_path,
        monkeypatch,
    ):
        state_file = tmp_path / "state.json"
        skills_root = tmp_path / "skills"
        first = ConnectorRegistry(
            marketplace_root=FORK,
            skills_root=skills_root,
            state_file=state_file,
        )
        second = ConnectorRegistry(
            marketplace_root=FORK,
            skills_root=skills_root,
            state_file=state_file,
        )
        first._set_state("westock-mcp", installed=True, enabled=False)
        barrier = threading.Barrier(2)
        first_mutate = first._mutate_state
        second_mutate = second._mutate_state

        def first_gated(mutate):
            barrier.wait(timeout=2)
            return first_mutate(mutate)

        def second_gated(mutate):
            barrier.wait(timeout=2)
            return second_mutate(mutate)

        monkeypatch.setattr(first, "_mutate_state", first_gated)
        monkeypatch.setattr(second, "_mutate_state", second_gated)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            removed = executor.submit(first.uninstall, "westock-mcp")
            installed = executor.submit(second.install, "ctrip-wendao")
            assert removed.result(timeout=5) is True
            assert installed.result(timeout=5)["installed"] is True

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert "westock-mcp" not in state
        assert state["ctrip-wendao"]["installed"] is True

    def test_install_state_commit_failure_is_not_reported_as_success(
        self,
        tmp_path,
        monkeypatch,
    ):
        from runtime.platform.io import transactional

        state_file = tmp_path / "state.json"
        reg = ConnectorRegistry(
            marketplace_root=FORK,
            skills_root=tmp_path / "skills",
            state_file=state_file,
        )
        real_replace = transactional.os.replace

        def fail_state_replace(source, destination):
            if Path(destination) == state_file:
                raise OSError("injected connector state commit failure")
            return real_replace(source, destination)

        monkeypatch.setattr(transactional.os, "replace", fail_state_replace)
        with pytest.raises(OSError, match="injected connector state commit failure"):
            reg.install("ctrip-wendao")

        assert reg.installed_ids() == set()


class TestAuthOrchestrator:
    def test_token_connect_and_header_injection(self, tmp_path):
        creds = CredentialStore(
            root=tmp_path,
            master_key_file=tmp_path / "key",
            credentials_file=tmp_path / "cred.json",
        )
        rules = [
            {
                "id": "tencent-docs-dual-token",
                "applies_to_connectors": ["tencent-docs", "tencent-docs-oa"],
                "inject": [
                    {
                        "from_connector": "tencent-docs",
                        "token_type": "access_token",
                        "header": "Authorization",
                        "value_template": "Bearer ${access_token}",
                    }
                ],
            }
        ]
        orch = AuthOrchestrator(credentials=creds, auth_injection_rules=rules)
        reg = ConnectorRegistry(marketplace_root=FORK)
        conn = reg.get("tencent-docs")
        assert conn is not None

        orch.connect(conn, tokens={"access_token": "tok-ABC"})
        assert orch.status(conn)["connected"] is True
        headers = orch.resolve_headers(conn)
        assert headers.get("Authorization") == "Bearer tok-ABC"

        orch.disconnect(conn)
        assert orch.resolve_headers(conn) == {}
        assert orch.status(conn)["connected"] is False

    def test_env_resolution(self, tmp_path):
        creds = CredentialStore(
            root=tmp_path,
            master_key_file=tmp_path / "key",
            credentials_file=tmp_path / "cred.json",
        )
        orch = AuthOrchestrator(credentials=creds)
        reg = ConnectorRegistry(marketplace_root=FORK)
        conn = reg.get("feishu")
        orch.connect(conn, tokens={"LARK_TOKEN": "env-val"})
        env = orch.resolve_env(conn)
        assert env.get("LARK_TOKEN") == "env-val"


class TestConnectorRouter:
    def test_router_endpoints(self, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from runtime.sensing.gateway.connector_router import create_connector_router

        reg = ConnectorRegistry(
            marketplace_root=FORK, skills_root=tmp_path / "skills", state_file=tmp_path / "st.json"
        )
        creds = CredentialStore(
            root=tmp_path,
            master_key_file=tmp_path / "key",
            credentials_file=tmp_path / "cred.json",
        )
        orch = AuthOrchestrator(credentials=creds)
        app = FastAPI()
        app.include_router(create_connector_router(registry=reg, orchestrator=orch))
        client = TestClient(app)

        r = client.get("/api/connectors", params={"limit": 500})
        assert r.status_code == 200
        assert r.json()["total"] == 111

        r = client.post("/api/connectors/westock-mcp/install")
        assert r.status_code == 200 and r.json()["installed"] is True
        required_permissions = r.json()["permissions"]

        r = client.post(
            "/api/connectors/westock-mcp/connect", json={"tokens": {"access_token": "abc"}}
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "PERMISSION_REVIEW_REQUIRED"

        r = client.post(
            "/api/connectors/westock-mcp/connect",
            json={
                "tokens": {"access_token": "abc"},
                "grant_permissions": required_permissions,
            },
        )
        assert r.status_code == 200 and r.json()["connected"] is True

        r = client.get("/api/connectors/westock-mcp/headers")
        assert r.status_code == 200
        assert r.json() == {
            "configured": True,
            "header_names": ["Authorization"],
        }
        assert "abc" not in r.text

        r = client.get("/api/connectors/nope")
        assert r.status_code == 404

    def test_stale_refresh_lease_returns_recovery_required_conflict(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from runtime.platform.connectors import _token_refresher as refresh_module
        from runtime.sensing.gateway.connector_router import create_connector_router

        monkeypatch.setattr(refresh_module, "_REFRESH_WORKER_WAIT_SECONDS", 0.03)
        monkeypatch.setattr(refresh_module, "_REFRESH_POLL_SECONDS", 0.005)
        reg = ConnectorRegistry(
            marketplace_root=FORK,
            skills_root=tmp_path / "skills",
            state_file=tmp_path / "state.json",
        )
        reg.install("westock-mcp")
        creds = CredentialStore(root=tmp_path / "credentials")
        generation = creds.begin_auth_generation(
            "westock-mcp",
            {"access_token": "initial"},
        )
        assert creds.register_refresh_lease(
            "westock-mcp",
            expected_generation=generation,
            worker_nonce="crashed-owner",
            owner_pid=999_991,
            child_pid=999_992,
            started_at=1.0,
        )
        app = FastAPI()
        app.include_router(
            create_connector_router(
                registry=reg,
                orchestrator=AuthOrchestrator(credentials=creds),
            )
        )

        response = TestClient(app).post("/api/connectors/westock-mcp/disconnect")

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "connector_refresh_cleanup_required",
            "connector_id": "westock-mcp",
            "recovery_required": True,
            "reason": "refresh owner did not acknowledge revocation before timeout",
            "generation": generation,
            "owner_pid": 999_991,
            "child_pid": 999_992,
            "started_at": 1.0,
        }
        assert creds.get_secret("westock-mcp", "access_token") is None
        assert creds.refresh_lease("westock-mcp") is not None

    def test_connect_keeps_event_loop_responsive_during_slow_auth(self) -> None:
        from fastapi import FastAPI

        from runtime.sensing.gateway.connector_router import create_connector_router

        connector = object()

        class FakeRegistry:
            @staticmethod
            def get(connector_id: str) -> object | None:
                return connector if connector_id == "slow-cli" else None

            @staticmethod
            def installed_ids() -> set[str]:
                return {"slow-cli"}

            @staticmethod
            def require_permissions(
                connector_id: str,
                permissions=(),
                *,
                require_active: bool = False,
            ) -> dict[str, object]:
                del connector_id, permissions, require_active
                return {"installed": True, "granted": []}

        class SlowOrchestrator:
            def __init__(self) -> None:
                self.started = threading.Event()
                self.release = threading.Event()
                self.calls: list[tuple[object, dict | None, bool]] = []

            @staticmethod
            def run_connector_lifecycle(
                _selected: object,
                operation,
                *,
                cancel_device_flow: bool = False,
            ):
                assert cancel_device_flow is False
                return operation()

            def connect(
                self,
                selected: object,
                *,
                tokens: dict | None,
                run_cli: bool,
            ) -> dict[str, object]:
                self.calls.append((selected, tokens, run_cli))
                self.started.set()
                self.release.wait(timeout=0.5)
                return {
                    "connected": False,
                    "device_flow": {
                        "flow_id": "slow-flow",
                        "connector_id": "slow-cli",
                        "verification_uri": "https://example.test/device",
                        "user_code": "SLOW",
                        "expires_in": 240,
                        "code_embedded_in_uri": False,
                    },
                }

        orchestrator = SlowOrchestrator()
        app = FastAPI()
        app.include_router(
            create_connector_router(
                registry=FakeRegistry(),
                orchestrator=orchestrator,
            )
        )

        async def exercise() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                request_task = asyncio.create_task(
                    client.post(
                        "/api/connectors/slow-cli/connect",
                        json={"tokens": {"access_token": "secret"}, "run_cli": True},
                    )
                )
                try:
                    assert await asyncio.to_thread(orchestrator.started.wait, 1)
                    assert not request_task.done()
                    heartbeat = asyncio.Event()
                    asyncio.get_running_loop().call_soon(heartbeat.set)
                    await asyncio.wait_for(heartbeat.wait(), timeout=0.1)
                    assert not request_task.done()
                finally:
                    orchestrator.release.set()
                return await asyncio.wait_for(request_task, timeout=1)

        response = asyncio.run(exercise())

        assert response.status_code == 200
        assert response.json()["device_flow"]["user_code"] == "SLOW"
        assert orchestrator.calls == [
            (connector, {"access_token": "secret"}, True),
        ]

    def test_real_lifecycle_wrapper_connects_tokens_and_device_flow_without_deadlock(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The registry guard is re-entrant for both inner connect paths."""
        from fastapi import FastAPI

        from runtime.platform.connectors import auth_orchestrator as ao
        from runtime.sensing.gateway.connector_router import create_connector_router

        reg = ConnectorRegistry(
            marketplace_root=FORK,
            skills_root=tmp_path / "skills",
            state_file=tmp_path / "state.json",
        )
        reg._set_state("westock-mcp", installed=True, enabled=False)
        reg._set_state("cnb-api", installed=True, enabled=False)
        for connector_id in ("westock-mcp", "cnb-api"):
            requirements = reg._requirements(connector_id)
            reg._permissions.stage(
                connector_id,
                kind="connector",
                required=requirements["permissions"],
            )
            reg.grant_permissions(connector_id, requirements["permissions"])
        orch = AuthOrchestrator(credentials=CredentialStore(root=tmp_path / "credentials"))
        proc = _FakeProc([])

        def parse_device_flow(
            _self: AuthOrchestrator,
            _conn,
            session: DeviceFlowSession,
        ) -> None:
            session.verification_uri = "https://cnb.cool/oauth2/device/verify?user_code=SAFE"
            session.user_code = "SAFE"

        monkeypatch.setattr(ao.subprocess, "Popen", lambda *_args, **_kwargs: proc)
        monkeypatch.setattr(AuthOrchestrator, "_drain_device_output", parse_device_flow)
        app = FastAPI()
        app.include_router(create_connector_router(registry=reg, orchestrator=orch))

        async def exercise() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                token = await asyncio.wait_for(
                    client.post(
                        "/api/connectors/westock-mcp/connect",
                        json={"tokens": {"access_token": "secret"}},
                    ),
                    timeout=1,
                )
                device = await asyncio.wait_for(
                    client.post(
                        "/api/connectors/cnb-api/connect",
                        json={"run_cli": True},
                    ),
                    timeout=1,
                )
                flow_id = device.json()["device_flow"]["flow_id"]
                cancelled = await asyncio.wait_for(
                    client.delete(
                        "/api/connectors/cnb-api/device-flow",
                        params={"expected_flow_id": flow_id},
                    ),
                    timeout=1,
                )
                return token, device, cancelled

        try:
            token, device, cancelled = asyncio.run(exercise())
            assert token.status_code == 200
            assert token.json()["connected"] is True
            assert device.status_code == 200
            assert device.json()["device_flow"]["user_code"] == "SAFE"
            assert cancelled.status_code == 200
            assert cancelled.json()["cancelled"] is True
            assert proc.terminated is True
            assert proc.wait_calls == 1
        finally:
            with ao._device_lock:
                ao._device_flows.pop("cnb-api", None)
            orch._refresher.stop("westock-mcp")
            orch._refresher.stop("cnb-api")

    def test_device_flow_endpoints(self, tmp_path):
        """官网授权(设备流)的查询/取消路由必须暴露给前端。

        前端弹窗刷新后要靠 GET 恢复授权态,用户关弹窗要靠 DELETE 回收后台登录进程。
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from runtime.sensing.gateway.connector_router import create_connector_router

        reg = ConnectorRegistry(
            marketplace_root=FORK, skills_root=tmp_path / "skills", state_file=tmp_path / "st.json"
        )
        creds = CredentialStore(
            root=tmp_path,
            master_key_file=tmp_path / "key",
            credentials_file=tmp_path / "cred.json",
        )
        app = FastAPI()
        app.include_router(
            create_connector_router(registry=reg, orchestrator=AuthOrchestrator(credentials=creds))
        )
        client = TestClient(app)

        # 未起流 → active=False,不报错
        r = client.get("/api/connectors/cnb-api/device-flow")
        assert r.status_code == 200
        assert r.json()["active"] is False
        assert r.json()["device_flow"] is None

        # DELETE 必须绑定客户端观测到的会话代际。
        r = client.delete("/api/connectors/cnb-api/device-flow")
        assert r.status_code == 422
        r = client.delete(
            "/api/connectors/cnb-api/device-flow",
            params={"expected_flow_id": "flow-from-client-a"},
        )
        assert r.status_code == 200
        assert r.json() == {
            "cancelled": False,
            "connector_id": "cnb-api",
            "reason": "inactive",
        }

        r = client.get("/api/connectors/nope/device-flow")
        assert r.status_code == 404


class _FakeProc:
    """模拟 CLI 登录子进程:stdout 可迭代,terminate 可观测。"""

    def __init__(self, lines: list[str]) -> None:
        self.stdout = iter(lines)
        self.terminated = False
        self.killed = False
        self.wait_calls = 0
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False

    def kill(self) -> None:
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        self.wait_calls += 1
        self._alive = False
        return 0


def _device_flow_connector() -> ConnectorDefinition:
    conn = ConnectorRegistry(marketplace_root=FORK).get("cnb-api")
    assert conn is not None
    return conn


class TestDeviceFlowDrain:
    """设备流输出解析 —— 锁住两个曾经踩过的坑。"""

    @staticmethod
    def _orchestrator(tmp_path: Path) -> AuthOrchestrator:
        return AuthOrchestrator(
            credentials=CredentialStore(
                root=tmp_path,
                master_key_file=tmp_path / "key",
                credentials_file=tmp_path / "cred.json",
            )
        )

    @staticmethod
    def _session(proc) -> DeviceFlowSession:
        return DeviceFlowSession(
            connector_id="cnb-api",
            proc=proc,
            verification_uri="",
            user_code="",
            expires_in=300,
            started_at=time.time(),
        )

    def test_process_survives_after_auth_url_parsed(self, tmp_path: Path):
        """解析出官网授权地址后,登录进程必须存活等用户授权(回归防护)。

        曾经的 bug:`opened` 字段永远为 False,finally 里 `not sess.opened` 恒真,
        拿到授权地址后立刻 terminate → 用户在官网授权成功,本地已无进程收 token。
        """
        conn = _device_flow_connector()
        orch = self._orchestrator(tmp_path)
        proc = _FakeProc(
            [
                "正在启动登录…\n",
                "请打开 https://cnb.cool/oauth2/device/verify?user_code=ABC123 完成授权\n",
                "等待授权中…\n",
                "still waiting\n",
            ]
        )
        sess = self._session(proc)

        orch._drain_device_output(conn, sess)

        assert sess.verification_uri == "https://cnb.cool/oauth2/device/verify?user_code=ABC123"
        assert sess.user_code == "ABC123"
        assert sess.opened is True
        # 核心:进程没被杀,交给 _finish_device_flow 在登录成功/超时后回收
        assert proc.terminated is False
        # 必须读完 stdout(不能 break),否则 CLI 后续输出会写满 PIPE 并阻塞
        assert list(proc.stdout) == []

    def test_process_reclaimed_when_no_auth_url(self, tmp_path: Path):
        """CLI 启动/登录异常(拿不到授权地址)→ 立即回收,避免进程泄漏。"""
        conn = _device_flow_connector()
        orch = self._orchestrator(tmp_path)
        proc = _FakeProc(["error: network unreachable\n", "login failed\n"])
        sess = self._session(proc)

        orch._drain_device_output(conn, sess)

        assert sess.verification_uri == ""
        assert sess.opened is False
        assert proc.terminated is True

    def test_untrusted_auth_url_is_rejected(self, tmp_path: Path):
        """授权地址必须过 authUrlDomain 白名单,钓鱼域名不得交付前端。"""
        conn = _device_flow_connector()
        orch = self._orchestrator(tmp_path)
        proc = _FakeProc(["请打开 https://evil.example.com/oauth2/device?user_code=BAD 授权\n"])
        sess = self._session(proc)

        orch._drain_device_output(conn, sess)

        assert sess.verification_uri == ""
        assert proc.terminated is True


class TestDeviceFlowStatus:
    """设备流状态查询 —— 会话结束必须如实上报,不能留死链接。"""

    @staticmethod
    def _orchestrator(tmp_path: Path) -> AuthOrchestrator:
        return AuthOrchestrator(
            credentials=CredentialStore(
                root=tmp_path,
                master_key_file=tmp_path / "key",
                credentials_file=tmp_path / "cred.json",
            )
        )

    @staticmethod
    def _register(proc, *, started_at: float | None = None) -> DeviceFlowSession:
        from runtime.platform.connectors import auth_orchestrator as ao

        sess = DeviceFlowSession(
            connector_id="cnb-api",
            proc=proc,
            verification_uri="https://cnb.cool/oauth2/device/verify?user_code=ABC123",
            user_code="ABC123",
            expires_in=300,
            started_at=started_at if started_at is not None else time.time(),
        )
        ao._device_flows["cnb-api"] = sess
        return sess

    @staticmethod
    def _cleanup() -> None:
        from runtime.platform.connectors import auth_orchestrator as ao

        with ao._device_lock:
            sess = ao._device_flows.pop("cnb-api", None)
        if sess is not None:
            sess.watchdog_stop.set()

    def test_active_while_process_alive(self, tmp_path: Path):
        conn = _device_flow_connector()
        proc = _FakeProc(["waiting\n"])
        self._register(proc)
        try:
            out = self._orchestrator(tmp_path).device_flow_status(conn)
            assert out["active"] is True
            assert out["device_flow"]["user_code"] == "ABC123"
            assert out["device_flow"]["flow_id"]
        finally:
            self._cleanup()

    def test_inactive_when_auth_process_exited(self, tmp_path: Path):
        """CLI 自行退出(自带超时/用户在官网取消)→ active=False,不再返回失效链接。

        曾经的 bug:只看会话字典存不存在,进程死了仍报 active:true,
        前端会一直轮询并展示一个早已过期的授权地址。
        """
        conn = _device_flow_connector()
        proc = _FakeProc([])
        self._register(proc)
        proc._alive = False  # CLI 自行退出
        try:
            out = self._orchestrator(tmp_path).device_flow_status(conn)
            assert out["active"] is False
            assert out["device_flow"] is None
            assert out["ended_reason"] == "auth_process_exited"
            assert proc.wait_calls == 1
            # 会话已清理,后续 connect 可以干净重开
            from runtime.platform.connectors import auth_orchestrator as ao

            assert "cnb-api" not in ao._device_flows
        finally:
            self._cleanup()

    def test_inactive_when_expired(self, tmp_path: Path):
        """超过 expires_in → 终止并 wait 回收,不能只丢掉会话引用。"""
        conn = _device_flow_connector()
        proc = _FakeProc(["waiting\n"])
        self._register(proc, started_at=time.time() - 400)
        try:
            out = self._orchestrator(tmp_path).device_flow_status(conn)
            assert out["active"] is False
            assert out["ended_reason"] == "expired"
            assert proc.terminated is True
            assert proc.wait_calls == 1
        finally:
            self._cleanup()

    def test_stale_status_result_cannot_reap_replacement_flow(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.platform.connectors import auth_orchestrator as ao

        conn = _device_flow_connector()
        orch = self._orchestrator(tmp_path)
        old_proc = _FakeProc(["waiting\n"])
        replacement_proc = _FakeProc(["waiting\n"])
        old_session = self._register(old_proc)
        status_entered = threading.Event()
        release_status = threading.Event()

        def delayed_connected_status(*_args, **_kwargs):
            status_entered.set()
            assert release_status.wait(timeout=3), "test did not release CLI status"
            return 0, "已登录"

        monkeypatch.setattr(orch, "_run_cli", delayed_connected_status)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                status_result = pool.submit(orch.status, conn)
                assert status_entered.wait(timeout=1)

                orch.cancel_device_flow(conn, expected_flow_id=old_session.flow_id)
                replacement = self._register(replacement_proc)
                release_status.set()
                result = status_result.result(timeout=3)

            assert result["cli_status"]["connected"] is True
            assert old_proc.terminated is True
            assert old_proc.wait_calls == 1
            assert replacement_proc.terminated is False
            assert replacement_proc.wait_calls == 0
            with ao._device_lock:
                assert ao._device_flows[conn.id] is replacement
        finally:
            release_status.set()
            self._cleanup()

    def test_stale_client_cancel_cannot_reap_replacement_flow(self, tmp_path: Path) -> None:
        """A 的迟到 DELETE 不得终止 B 新启动的同 connector 授权流。"""
        from runtime.platform.connectors import auth_orchestrator as ao

        conn = _device_flow_connector()
        orch = self._orchestrator(tmp_path)
        first_proc = _FakeProc(["waiting\n"])
        replacement_proc = _FakeProc(["waiting\n"])
        first = self._register(first_proc)
        try:
            cancelled = orch.cancel_device_flow(conn, expected_flow_id=first.flow_id)
            assert cancelled["cancelled"] is True
            assert first_proc.terminated is True
            assert first_proc.wait_calls == 1

            replacement = self._register(replacement_proc)
            assert replacement.flow_id != first.flow_id

            late = orch.cancel_device_flow(conn, expected_flow_id=first.flow_id)

            assert late == {
                "cancelled": False,
                "connector_id": conn.id,
                "reason": "generation_mismatch",
            }
            assert replacement_proc.terminated is False
            assert replacement_proc.wait_calls == 0
            with ao._device_lock:
                assert ao._device_flows[conn.id] is replacement
        finally:
            self._cleanup()

    def test_disconnect_reaps_inflight_auth_before_logout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.platform.connectors import auth_orchestrator as ao

        conn = _device_flow_connector()
        proc = _FakeProc(["waiting\n"])
        self._register(proc)
        orch = self._orchestrator(tmp_path)
        monkeypatch.setattr(orch, "_run_cli", lambda *_args, **_kwargs: (0, "logged out"))
        try:
            result = orch.disconnect(conn)
            assert result["disconnected"] is True
            assert proc.terminated is True
            assert proc.wait_calls == 1
            with ao._device_lock:
                assert "cnb-api" not in ao._device_flows
        finally:
            self._cleanup()

    def test_token_connect_reaps_inflight_device_flow_before_storing_credentials(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.platform.connectors import auth_orchestrator as ao

        conn = _device_flow_connector()
        proc = _FakeProc(["waiting\n"])
        self._register(proc)
        credentials = CredentialStore(root=tmp_path)
        orch = AuthOrchestrator(credentials=credentials)
        writes_after_reap: list[bool] = []
        real_begin_generation = credentials.begin_auth_generation

        def observe_begin_generation(connector_id: str, values: dict[str, str]) -> int:
            writes_after_reap.append(proc.terminated and proc.wait_calls == 1)
            return real_begin_generation(connector_id, values)

        monkeypatch.setattr(credentials, "begin_auth_generation", observe_begin_generation)
        try:
            result = orch.connect(conn, tokens={"access_token": "new-token"})

            assert result["connected"] is True
            assert writes_after_reap == [True]
            assert proc.terminated is True
            assert proc.wait_calls == 1
            with ao._device_lock:
                assert conn.id not in ao._device_flows
        finally:
            self._cleanup()


class TestDeviceFlowStartLifecycle:
    """设备流启动的并发预留与旧进程回收。"""

    @staticmethod
    def _orchestrator(tmp_path: Path) -> AuthOrchestrator:
        return AuthOrchestrator(
            credentials=CredentialStore(
                root=tmp_path,
                master_key_file=tmp_path / "key",
                credentials_file=tmp_path / "cred.json",
            )
        )

    @staticmethod
    def _cleanup(connector_id: str, processes: list[_FakeProc]) -> None:
        from runtime.platform.connectors import auth_orchestrator as ao

        with ao._device_lock:
            sess = ao._device_flows.pop(connector_id, None)
            reservations = getattr(ao, "_device_flow_starts", None)
            if isinstance(reservations, dict):
                reservations.pop(connector_id, None)
        if sess is not None:
            sess.watchdog_stop.set()
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
            if proc.wait_calls == 0:
                proc.wait(timeout=1)

    def test_concurrent_start_spawns_and_registers_one_process(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.platform.connectors import auth_orchestrator as ao

        connector_id = "cnb-api"
        conn = _device_flow_connector()
        orch = self._orchestrator(tmp_path)
        workers = 8
        callers_ready = threading.Barrier(workers + 1)
        first_popen_entered = threading.Event()
        release_popen = threading.Event()
        process_lock = threading.Lock()
        processes: list[_FakeProc] = []

        def fake_popen(*_args, **_kwargs):
            proc = _FakeProc([])
            with process_lock:
                processes.append(proc)
            first_popen_entered.set()
            assert release_popen.wait(timeout=3), "test did not release Popen"
            return proc

        def fake_drain(
            _self: AuthOrchestrator,
            _conn,
            session: DeviceFlowSession,
        ) -> None:
            session.verification_uri = "https://cnb.cool/oauth2/device/verify?user_code=ABC123"
            session.user_code = "ABC123"

        monkeypatch.setattr(ao.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(AuthOrchestrator, "_drain_device_output", fake_drain)

        def start() -> dict:
            callers_ready.wait()
            return orch.start_device_flow(conn)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(start) for _ in range(workers)]
                callers_ready.wait()
                assert first_popen_entered.wait(timeout=1)
                # Keep the first spawn unpublished long enough for every other
                # caller to hit the connector reservation. The legacy check-
                # then-spawn race deterministically enters Popen more than once.
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline:
                    with process_lock:
                        if len(processes) > 1:
                            break
                    time.sleep(0.01)
                release_popen.set()
                results = [future.result(timeout=5) for future in futures]

            assert all(result.get("device_flow") for result in results)
            assert len(processes) == 1
            with ao._device_lock:
                assert ao._device_flows[connector_id].proc is processes[0]
        finally:
            release_popen.set()
            self._cleanup(connector_id, processes)

    def test_expired_session_is_reaped_before_replacement_spawn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.platform.connectors import auth_orchestrator as ao

        connector_id = "cnb-api"
        conn = _device_flow_connector()
        orch = self._orchestrator(tmp_path)
        expired = _FakeProc([])
        replacement = _FakeProc([])
        with ao._device_lock:
            ao._device_flows[connector_id] = DeviceFlowSession(
                connector_id=connector_id,
                proc=expired,
                verification_uri="https://cnb.cool/expired",
                user_code="OLD",
                expires_in=300,
                started_at=time.time() - 400,
            )

        def fake_popen(*_args, **_kwargs):
            assert expired.terminated is True
            assert expired.wait_calls == 1
            return replacement

        def fake_drain(
            _self: AuthOrchestrator,
            _conn,
            session: DeviceFlowSession,
        ) -> None:
            session.verification_uri = "https://cnb.cool/oauth2/device/verify?user_code=NEW123"
            session.user_code = "NEW123"

        monkeypatch.setattr(ao.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(AuthOrchestrator, "_drain_device_output", fake_drain)

        processes = [expired, replacement]
        try:
            result = orch.start_device_flow(conn)
            assert result["device_flow"]["user_code"] == "NEW123"
            assert expired.terminated is True
            assert expired.wait_calls == 1
            with ao._device_lock:
                assert ao._device_flows[connector_id].proc is replacement
        finally:
            self._cleanup(connector_id, processes)

    def test_disconnect_serializes_logout_against_concurrent_device_flow_start(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.platform.connectors import auth_orchestrator as ao

        connector_id = "cnb-api"
        conn = _device_flow_connector()
        orch = self._orchestrator(tmp_path)
        existing = _FakeProc(["waiting\n"])
        candidate = _FakeProc([])
        logout_entered = threading.Event()
        release_logout = threading.Event()
        candidate_spawned = threading.Event()
        ordering: list[str] = []
        ordering_lock = threading.Lock()
        with ao._device_lock:
            ao._device_flows[connector_id] = DeviceFlowSession(
                connector_id=connector_id,
                proc=existing,
                verification_uri="https://cnb.cool/old",
                user_code="OLD",
                expires_in=300,
                started_at=time.time(),
            )

        def fake_logout(*_args, **_kwargs):
            with ordering_lock:
                ordering.append("logout_started")
            logout_entered.set()
            assert release_logout.wait(timeout=3), "test did not release logout"
            with ordering_lock:
                ordering.append("logout_finished")
            return 0, "logged out"

        def fake_popen(*_args, **_kwargs):
            with ordering_lock:
                ordering.append("device_flow_spawned")
            candidate_spawned.set()
            return candidate

        def fake_drain(
            _self: AuthOrchestrator,
            _conn,
            session: DeviceFlowSession,
        ) -> None:
            session.verification_uri = "https://cnb.cool/oauth2/device/verify?user_code=NEW"
            session.user_code = "NEW"

        monkeypatch.setattr(orch, "_run_cli", fake_logout)
        monkeypatch.setattr(ao.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(AuthOrchestrator, "_drain_device_output", fake_drain)

        processes = [existing, candidate]
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                disconnect_future = pool.submit(orch.disconnect, conn)
                assert logout_entered.wait(timeout=1)
                start_future = pool.submit(orch.start_device_flow, conn)
                try:
                    assert not candidate_spawned.wait(timeout=0.2)
                    assert not start_future.done()
                finally:
                    release_logout.set()

                assert disconnect_future.result(timeout=3)["disconnected"] is True
                assert start_future.result(timeout=3)["device_flow"]["user_code"] == "NEW"

            assert existing.terminated is True
            assert existing.wait_calls == 1
            assert ordering == [
                "logout_started",
                "logout_finished",
                "device_flow_spawned",
            ]
        finally:
            release_logout.set()
            self._cleanup(connector_id, processes)

    def test_device_flow_ttl_watchdog_reaps_without_status_polling(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.platform.connectors import auth_orchestrator as ao

        connector_id = "cnb-api"
        conn = _device_flow_connector()
        assert conn.cli is not None
        device_spec = dict(conn.cli.get("authDeviceFlow") or {})
        device_spec["defaultExpiresInSeconds"] = 0.05
        conn.cli["authDeviceFlow"] = device_spec
        orch = self._orchestrator(tmp_path)
        proc = _FakeProc([])

        def fake_drain(
            _self: AuthOrchestrator,
            _conn,
            session: DeviceFlowSession,
        ) -> None:
            session.verification_uri = "https://cnb.cool/oauth2/device/verify?user_code=TTL"
            session.user_code = "TTL"

        monkeypatch.setattr(ao.subprocess, "Popen", lambda *_args, **_kwargs: proc)
        monkeypatch.setattr(AuthOrchestrator, "_drain_device_output", fake_drain)

        try:
            result = orch.start_device_flow(conn)
            assert result["device_flow"]["user_code"] == "TTL"

            deadline = time.monotonic() + 1
            while proc.wait_calls == 0 and time.monotonic() < deadline:
                time.sleep(0.01)

            assert proc.terminated is True
            assert proc.wait_calls == 1
            with ao._device_lock:
                assert connector_id not in ao._device_flows
        finally:
            self._cleanup(connector_id, [proc])

    def test_drain_thread_start_failure_reaps_child_and_registration(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.platform.connectors import auth_orchestrator as ao

        connector_id = "cnb-api"
        conn = _device_flow_connector()
        orch = self._orchestrator(tmp_path)
        proc = _FakeProc([])

        class FailingThread:
            def __init__(self, **_kwargs) -> None:
                pass

            def start(self) -> None:
                raise RuntimeError("thread unavailable")

        monkeypatch.setattr(ao.subprocess, "Popen", lambda *_args, **_kwargs: proc)
        monkeypatch.setattr(ao.threading, "Thread", FailingThread)

        try:
            result = orch.start_device_flow(conn)
            assert result["connected"] is False
            assert "thread unavailable" in result["message"]
            assert proc.terminated is True
            assert proc.wait_calls == 1
            with ao._device_lock:
                assert connector_id not in ao._device_flows
        finally:
            self._cleanup(connector_id, [proc])

    def test_spawn_conflict_keeps_canonical_session_and_reaps_candidate(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A legacy/direct writer appearing during Popen cannot be overwritten."""

        from runtime.platform.connectors import auth_orchestrator as ao

        connector_id = "cnb-api"
        conn = _device_flow_connector()
        orch = self._orchestrator(tmp_path)
        canonical_proc = _FakeProc([])
        candidate_proc = _FakeProc([])
        canonical = DeviceFlowSession(
            connector_id=connector_id,
            proc=canonical_proc,
            verification_uri="https://cnb.cool/canonical",
            user_code="CANONICAL",
            expires_in=300,
            started_at=time.time(),
        )

        def fake_popen(*_args, **_kwargs):
            with ao._device_lock:
                ao._device_flows[connector_id] = canonical
            return candidate_proc

        monkeypatch.setattr(ao.subprocess, "Popen", fake_popen)

        processes = [canonical_proc, candidate_proc]
        try:
            result = orch.start_device_flow(conn)
            assert result["device_flow"]["user_code"] == "CANONICAL"
            assert candidate_proc.terminated is True
            assert candidate_proc.wait_calls == 1
            assert canonical_proc.terminated is False
            with ao._device_lock:
                assert ao._device_flows[connector_id] is canonical
        finally:
            self._cleanup(connector_id, processes)


class TestNextAction:
    """服务端 next_action 决策 —— 对齐 WorkBuddy connect 编排。"""

    @staticmethod
    def _orchestrator(tmp_path: Path) -> AuthOrchestrator:
        return AuthOrchestrator(
            credentials=CredentialStore(
                root=tmp_path,
                master_key_file=tmp_path / "key",
                credentials_file=tmp_path / "cred.json",
            )
        )

    def test_none_auth_mode_connected(self, tmp_path: Path):
        conn = ConnectorDefinition(id="no-auth", auth_mode="none")
        result = self._orchestrator(tmp_path).connect(conn)
        assert result["connected"] is True
        assert result["next_action"] == "connected"

    def test_token_connect_connected(self, tmp_path: Path):
        orch = self._orchestrator(tmp_path)
        reg = ConnectorRegistry(marketplace_root=FORK)
        conn = reg.get("tencent-docs")
        assert conn is not None
        result = orch.connect(conn, tokens={"access_token": "tok-ABC"})
        assert result["connected"] is True
        assert result["next_action"] == "connected"
        assert result["stored_keys"] == ["access_token"]

    def test_cli_no_run_action_is_cli_command(self, tmp_path: Path):
        orch = self._orchestrator(tmp_path)
        conn = _device_flow_connector()  # cnb-api,有 auth 命令
        result = orch.connect(conn)  # 不带 run_cli / tokens
        assert result["next_action"] == "cli_command"
        assert "cnb login" in result["command"]

    def test_no_cli_no_token_action_is_form(self, tmp_path: Path):
        conn = ConnectorDefinition(id="pure-token", auth_mode="token", cli={})
        result = self._orchestrator(tmp_path).connect(conn)
        assert result["connected"] is False
        assert result["next_action"] == "form"

    def test_device_flow_redirect_when_uri_ready(self, tmp_path: Path):
        from runtime.platform.connectors import auth_orchestrator as ao

        orch = self._orchestrator(tmp_path)
        conn = _device_flow_connector()
        proc = _FakeProc(["waiting\n"])
        sess = DeviceFlowSession(
            connector_id="cnb-api",
            proc=proc,
            verification_uri="https://cnb.cool/oauth2/device/verify?user_code=ABC123",
            user_code="ABC123",
            expires_in=300,
            started_at=time.time(),
        )
        with ao._device_lock:
            ao._device_flows["cnb-api"] = sess
        try:
            result = orch.start_device_flow(conn)
            assert result["next_action"] == "redirect"
            assert result["device_flow"]["verification_uri"].startswith("https://cnb.cool")
        finally:
            with ao._device_lock:
                s = ao._device_flows.pop("cnb-api", None)
            if s is not None:
                s.watchdog_stop.set()

    def test_device_flow_poll_when_no_uri_yet(self, tmp_path: Path):
        from runtime.platform.connectors import auth_orchestrator as ao

        orch = self._orchestrator(tmp_path)
        conn = _device_flow_connector()
        proc = _FakeProc(["starting…\n"])
        sess = DeviceFlowSession(
            connector_id="cnb-api",
            proc=proc,
            verification_uri="",
            user_code="",
            expires_in=300,
            started_at=time.time(),
        )
        with ao._device_lock:
            ao._device_flows["cnb-api"] = sess
        try:
            result = orch.start_device_flow(conn)
            assert result["next_action"] == "poll"
        finally:
            with ao._device_lock:
                s = ao._device_flows.pop("cnb-api", None)
            if s is not None:
                s.watchdog_stop.set()


class TestTokenRefresher:
    """token 自动刷新 —— 对齐 WorkBuddy ServerSideOauthRefresher。"""

    @staticmethod
    def _orchestrator(tmp_path: Path) -> AuthOrchestrator:
        return AuthOrchestrator(
            credentials=CredentialStore(
                root=tmp_path,
                master_key_file=tmp_path / "key",
                credentials_file=tmp_path / "cred.json",
            )
        )

    @staticmethod
    def _connector_with_refresh() -> ConnectorDefinition:
        return ConnectorDefinition(
            id="refresher-test",
            auth_mode="token",
            cli={
                "tokenRefresh": {
                    "command": "fake-refresh-token",
                    "storeKey": "access_token",
                    "tokenPattern": "access_token=([A-Za-z0-9_-]+)",
                    "expiresInPattern": "expires_in=(\\d+)",
                    "defaultExpiresInSeconds": 300,
                }
            },
        )

    @staticmethod
    def _terminal_operation(
        tmp_path: Path,
        terminal_action: str,
        orchestrator: AuthOrchestrator,
        conn: ConnectorDefinition,
    ) -> Callable[[], object]:
        if terminal_action == "disconnect":
            return lambda: orchestrator.disconnect(conn)

        from runtime.platform.capabilities.capability_registry import CapabilityRegistry

        class SingleConnectorRegistry:
            installed = True
            enabled = True

            def list(self):
                return [
                    conn.to_dict(
                        installed=self.installed,
                        enabled=self.enabled,
                    )
                ]

            @staticmethod
            def get(connector_id: str):
                return conn if connector_id == conn.id else None

            def uninstall(self, connector_id: str) -> bool:
                if connector_id != conn.id or not self.installed:
                    return False
                self.installed = False
                self.enabled = False
                return True

            def set_enabled(self, connector_id: str, enabled: bool) -> bool:
                if connector_id != conn.id or not self.installed:
                    return False
                self.enabled = enabled
                return True

        codex_cache = tmp_path / f"codex-cache-{terminal_action}"
        codex_cache.mkdir(exist_ok=True)
        capabilities = CapabilityRegistry(
            connector_registry=SingleConnectorRegistry(),
            auth_orchestrator=orchestrator,
            codex_cache=codex_cache,
            capability_state_file=tmp_path / f"capabilities-{terminal_action}.json",
            skills_root=tmp_path / "skills",
        )
        if terminal_action == "disable":
            return lambda: capabilities.set_enabled(conn.id, False)
        return lambda: capabilities.uninstall(conn.id)

    def test_schedule_requires_token_refresh_declaration(self, tmp_path: Path):
        orch = self._orchestrator(tmp_path)
        conn = ConnectorDefinition(id="plain", auth_mode="token", cli={})
        assert orch._refresher.schedule(conn) is False
        assert orch._refresher.is_scheduled("plain") is False

    def test_schedule_runs_immediate_refresh_and_stores_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        orch = self._orchestrator(tmp_path)
        conn = self._connector_with_refresh()

        def fake_run(_self, _conn, _cmd, _entry):
            return 0, "access_token=new-token-123 expires_in=600"

        monkeypatch.setattr(type(orch._refresher), "_run_refresh_cmd", fake_run)

        assert orch._refresher.schedule(conn) is True
        assert orch._refresher.is_scheduled("refresher-test") is True
        # 立即刷一次(同步等线程完成)
        import time as _t

        deadline = _t.time() + 3
        while (
            _t.time() < deadline
            and orch._credentials.get_secret("refresher-test", "access_token") != "new-token-123"
        ):
            _t.sleep(0.05)
        assert orch._credentials.get_secret("refresher-test", "access_token") == "new-token-123"
        orch._refresher.stop("refresher-test")

    def test_stop_cancels_scheduled_refresh(self, tmp_path: Path):
        orch = self._orchestrator(tmp_path)
        conn = self._connector_with_refresh()
        assert orch._refresher.schedule(conn) is True
        orch._refresher.stop("refresher-test")
        assert orch._refresher.is_scheduled("refresher-test") is False

    def test_schedule_replacement_fences_inflight_refresh(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        orch = self._orchestrator(tmp_path)
        conn = self._connector_with_refresh()
        first_refresh_started = threading.Event()
        release_first_refresh = threading.Event()
        call_lock = threading.Lock()
        call_count = 0

        def replaceable_refresh(_self, _conn, _cmd, _entry):
            nonlocal call_count
            with call_lock:
                call_count += 1
                current_call = call_count
            if current_call == 1:
                first_refresh_started.set()
                assert release_first_refresh.wait(timeout=3)
                return 0, "access_token=stale-token expires_in=600"
            return 0, "access_token=replacement-token expires_in=600"

        monkeypatch.setattr(
            type(orch._refresher),
            "_run_refresh_cmd",
            replaceable_refresh,
        )
        try:
            assert orch._refresher.schedule(conn) is True
            assert first_refresh_started.wait(timeout=2)
            with orch._refresher._lock:
                first_entry = orch._refresher._entries[conn.id]

            assert orch._refresher.schedule(conn) is True
            release_first_refresh.set()
            deadline = time.time() + 3
            while (
                time.time() < deadline
                and orch._credentials.get_secret(conn.id, "access_token") != "replacement-token"
            ):
                time.sleep(0.01)

            assert first_entry.cancelled.is_set()
            assert orch._credentials.get_secret(conn.id, "access_token") == "replacement-token"
        finally:
            release_first_refresh.set()
            orch._refresher.stop(conn.id)

    def test_disconnect_stops_refresh_and_clears_credentials(self, tmp_path: Path):
        orch = self._orchestrator(tmp_path)
        conn = self._connector_with_refresh()
        assert orch._refresher.schedule(conn) is True
        orch.connect(conn, tokens={"access_token": "initial"})
        orch.disconnect(conn)
        assert orch._refresher.is_scheduled("refresher-test") is False
        assert not orch._credentials.has_credentials("refresher-test")

    @pytest.mark.parametrize("terminal_action", ["disconnect", "disable", "uninstall"])
    def test_cross_orchestrator_terminal_action_fences_blocked_refresh(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        terminal_action: str,
    ) -> None:
        credential_root = tmp_path / "credentials"
        first = AuthOrchestrator(credentials=CredentialStore(root=credential_root))
        second = AuthOrchestrator(credentials=CredentialStore(root=credential_root))
        conn = self._connector_with_refresh()
        refresh_started = threading.Event()
        release_refresh = threading.Event()

        def blocked_refresh(_self, _conn, _cmd, _entry):
            refresh_started.set()
            assert release_refresh.wait(timeout=3)
            return 0, "access_token=refreshed-after-revoke expires_in=600"

        monkeypatch.setattr(type(first._refresher), "_run_refresh_cmd", blocked_refresh)
        first.connect(conn, tokens={"access_token": "initial"})
        assert refresh_started.wait(timeout=2)

        terminal_operation = self._terminal_operation(
            tmp_path,
            terminal_action,
            second,
            conn,
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            terminal = executor.submit(terminal_operation)
            deadline = time.time() + 2
            while (
                time.time() < deadline
                and second._credentials.get_secret(conn.id, "access_token") is not None
            ):
                time.sleep(0.01)
            assert second._credentials.get_secret(conn.id, "access_token") is None
            assert not terminal.done()
            release_refresh.set()
            assert terminal.result(timeout=3)

        deadline = time.time() + 3
        while time.time() < deadline and first._refresher.is_scheduled(conn.id):
            time.sleep(0.01)

        assert first._refresher.is_scheduled(conn.id) is False
        assert first._credentials.get_secret(conn.id, "access_token") is None
        assert second._credentials.get_secret(conn.id, "access_token") is None

    @pytest.mark.parametrize("terminal_action", ["disconnect", "disable", "uninstall"])
    def test_terminal_action_reaps_refresh_child_before_return(
        self,
        tmp_path: Path,
        terminal_action: str,
    ) -> None:
        credential_root = tmp_path / "credentials"
        first = AuthOrchestrator(credentials=CredentialStore(root=credential_root))
        second = AuthOrchestrator(credentials=CredentialStore(root=credential_root))
        conn = self._connector_with_refresh()
        refresh_spec = dict((conn.cli or {}).get("tokenRefresh") or {})
        refresh_spec["command"] = shlex.join([sys.executable, "-c", "import time; time.sleep(60)"])
        conn.cli["tokenRefresh"] = refresh_spec

        first.connect(conn, tokens={"access_token": "initial"})
        with first._refresher._lock:
            entry = first._refresher._entries[conn.id]
        deadline = time.time() + 3
        child = None
        while time.time() < deadline:
            with entry.child_lock:
                child = entry.child
            if child is not None and first._credentials.refresh_lease(conn.id):
                break
            time.sleep(0.01)
        assert child is not None
        assert child.poll() is None
        assert entry.worker is not None and entry.worker.is_alive()

        terminal_operation = self._terminal_operation(
            tmp_path,
            terminal_action,
            second,
            conn,
        )
        try:
            assert terminal_operation()
            assert child.poll() is not None
            assert entry.worker is not None and not entry.worker.is_alive()
            assert first._credentials.refresh_lease(conn.id) is None
            assert first._credentials.get_secret(conn.id, "access_token") is None
            assert second._credentials.get_secret(conn.id, "access_token") is None
        finally:
            first._refresher.stop(conn.id)

    def test_disconnect_runs_unauth_only_after_refresh_child_exit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        credential_root = tmp_path / "credentials"
        first = AuthOrchestrator(credentials=CredentialStore(root=credential_root))
        second = AuthOrchestrator(credentials=CredentialStore(root=credential_root))
        conn = self._connector_with_refresh()
        refresh_spec = dict((conn.cli or {}).get("tokenRefresh") or {})
        refresh_spec["command"] = shlex.join([sys.executable, "-c", "import time; time.sleep(60)"])
        conn.cli["tokenRefresh"] = refresh_spec
        conn.cli["unAuth"] = {
            "darwin": "fake-logout",
            "linux": "fake-logout",
            "win32": "fake-logout",
        }

        first.connect(conn, tokens={"access_token": "initial"})
        with first._refresher._lock:
            entry = first._refresher._entries[conn.id]
        deadline = time.time() + 3
        child = None
        while time.time() < deadline:
            with entry.child_lock:
                child = entry.child
            if child is not None and first._credentials.refresh_lease(conn.id):
                break
            time.sleep(0.01)
        assert child is not None and child.poll() is None
        logout_entered = threading.Event()

        def observe_logout(_conn, _cmd, **_kwargs):
            assert child.poll() is not None
            logout_entered.set()
            return 0, "logged out"

        monkeypatch.setattr(second, "_run_cli", observe_logout)
        try:
            result = second.disconnect(conn)
            assert result["disconnected"] is True
            assert logout_entered.is_set()
            assert entry.worker is not None and not entry.worker.is_alive()
        finally:
            first._refresher.stop(conn.id)

    def test_cross_process_disconnect_waits_for_owner_to_reap_child(
        self,
        tmp_path: Path,
    ) -> None:
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        results = context.Queue()
        owner = context.Process(
            target=_run_refresh_owner_process,
            args=(str(tmp_path / "credentials"), ready, results),
        )
        owner.start()
        try:
            assert ready.wait(timeout=10)
            started = results.get(timeout=2)
            assert "error" not in started
            child_pid = started["child_pid"]

            second = AuthOrchestrator(credentials=CredentialStore(root=tmp_path / "credentials"))
            result = second.disconnect(_refresh_process_connector())

            assert result["disconnected"] is True
            owner.join(timeout=10)
            assert owner.exitcode == 0
            stopped = results.get(timeout=2)
            assert stopped["scheduled"] is False
            assert stopped["child_exit"] is not None
            assert child_pid > 0
            assert second._credentials.refresh_lease("cross-process-refresher") is None
            assert second._credentials.get_secret("cross-process-refresher", "access_token") is None
        finally:
            if owner.is_alive():
                owner.terminate()
                owner.join(timeout=3)

    def test_prechecked_connect_is_serialized_with_external_uninstall(
        self,
        tmp_path: Path,
    ) -> None:
        state_file = tmp_path / "connector-state.json"
        credential_root = tmp_path / "credentials"
        first_registry = ConnectorRegistry(
            marketplace_root=FORK,
            skills_root=tmp_path / "skills",
            state_file=state_file,
        )
        second_registry = ConnectorRegistry(
            marketplace_root=FORK,
            skills_root=tmp_path / "skills",
            state_file=state_file,
        )
        first_registry._set_state("westock-mcp", installed=True, enabled=False)
        first = AuthOrchestrator(credentials=CredentialStore(root=credential_root))
        second = AuthOrchestrator(credentials=CredentialStore(root=credential_root))
        conn = first_registry.get("westock-mcp")
        assert conn is not None
        checked_installed = threading.Event()
        release_connect = threading.Event()
        uninstall_entered = threading.Event()

        def connect_after_check() -> dict[str, object]:
            assert conn.id in first_registry.installed_ids()
            checked_installed.set()
            assert release_connect.wait(timeout=3)
            return first.connect(conn, tokens={"access_token": "stale-connect"})

        def uninstall() -> bool:
            uninstall_entered.set()
            return second_registry.uninstall(conn.id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            connecting = executor.submit(
                first.run_connector_lifecycle,
                conn,
                connect_after_check,
            )
            assert checked_installed.wait(timeout=2)
            removing = executor.submit(
                second.run_connector_lifecycle,
                conn,
                uninstall,
                cancel_device_flow=True,
            )
            assert not uninstall_entered.wait(timeout=0.05)
            release_connect.set()
            assert connecting.result(timeout=3)["connected"] is True
            assert removing.result(timeout=3) is True

        assert conn.id not in second_registry.installed_ids()
        assert first._credentials.get_secret(conn.id, "access_token") is None
        assert second._credentials.get_secret(conn.id, "access_token") is None

