"""Fail-closed workspace credentials through real SQLite and HTTP/FS routers.

All credentials and encryption keys here are synthetic test data. No network
connections or existing application databases are used.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform import feature_flags as ff
from runtime.platform.io.lease import LeaseStore
from runtime.safety.auth import Identity, IdentityStore
from runtime.safety.auth.scope import TenantScope
from runtime.sensing.gateway import _fs_router_endpoints as fs_endpoints
from runtime.sensing.gateway.fs_router import create_fs_router
from runtime.sensing.gateway.workspace_api_router import create_workspace_api_router
from runtime.sensing.server.mount_backend import LocalMountBackend, MountBackendRegistry
from runtime.workspace import WorkspaceCryptoError, WorkspaceStore, crypto

_OPTIONS = {"username": "fixture-user", "password": "synthetic-workspace-password"}
_HEADERS = {"Authorization": "Bearer fixture-alice-api-key"}


def _reset_cipher() -> None:
    crypto._CIPHER_CACHE = None
    crypto._CIPHER_KEY_CACHE = None
    crypto._MACHINE_ID_CACHE = None


@pytest.fixture(autouse=True)
def isolated_crypto_and_flags(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    original_specs = dict(ff._SPECS)
    original_snapshot = ff._SNAPSHOT
    original_file = ff._FILE_PATH
    monkeypatch.setenv("ECHO_WORKSPACE_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("ECHO_FF_UI_REMOTE_WORKSPACE", "1")
    ff.reload()
    _reset_cipher()
    yield
    _reset_cipher()
    ff._SPECS.clear()
    ff._SPECS.update(original_specs)
    ff._SNAPSHOT = original_snapshot
    ff._FILE_PATH = original_file


@pytest.fixture
def store(tmp_path: Path) -> WorkspaceStore:
    return WorkspaceStore(tmp_path / "workspaces.db")


def _deny_cryptography(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "cryptography" or name.startswith("cryptography."):
            raise ModuleNotFoundError("synthetic missing cryptography")
        return original_import(name, *args, **kwargs)

    _reset_cipher()
    monkeypatch.setattr(builtins, "__import__", guarded_import)


def _create(store: WorkspaceStore, options: dict[str, Any] | None = None, **kwargs: Any):
    return store.create_workspace(
        name="fixture-workspace",
        mount_type="smb",
        mount_target="smb://fixture.invalid/share",
        mount_options=_OPTIONS if options is None else options,
        owner_id="alice",
        tenant_id="tenant-a",
        **kwargs,
    )


def _counts(store: WorkspaceStore) -> tuple[int, int]:
    with closing(sqlite3.connect(store.db_path)) as conn:
        return (
            conn.execute("SELECT count(*) FROM workspaces").fetchone()[0],
            conn.execute("SELECT count(*) FROM workspace_members").fetchone()[0],
        )


def _raw(store: WorkspaceStore, workspace_id: str) -> str:
    with closing(sqlite3.connect(store.db_path)) as conn:
        return conn.execute(
            "SELECT mount_options_json FROM workspaces WHERE id=?", (workspace_id,)
        ).fetchone()[0]


def _replace_raw(store: WorkspaceStore, workspace_id: str, raw: str) -> None:
    with closing(sqlite3.connect(store.db_path)) as conn, conn:
        conn.execute("UPDATE workspaces SET mount_options_json=? WHERE id=?", (raw, workspace_id))


def _break_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_WORKSPACE_KEY", Fernet.generate_key().decode("ascii"))
    _reset_cipher()


@pytest.mark.parametrize("failure", ["dependency", "key", "encrypt", "no_cipher"])
def test_sensitive_write_failure_commits_neither_workspace_nor_members(
    store: WorkspaceStore, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    if failure == "dependency":
        _deny_cryptography(monkeypatch)
    elif failure == "key":

        def broken_key() -> bytes:
            raise RuntimeError(_OPTIONS["password"])

        monkeypatch.setattr(crypto, "_resolve_key", broken_key)
    elif failure == "encrypt":

        class BrokenCipher:
            def encrypt(self, _value: bytes) -> bytes:
                raise RuntimeError(_OPTIONS["password"])

        monkeypatch.setattr(crypto, "_cipher", BrokenCipher)
    else:
        monkeypatch.setattr(crypto, "_cipher", lambda: None)
    with pytest.raises(WorkspaceCryptoError) as error:
        _create(store)
    assert error.value.code == "workspace_encryption_unavailable"
    assert "cryptography" in error.value.hint
    assert _OPTIONS["password"] not in str(error.value)
    assert _counts(store) == (0, 0)


def test_owner_insert_failure_rolls_back_encrypted_workspace(store: WorkspaceStore) -> None:
    with closing(sqlite3.connect(store.db_path)) as conn, conn:
        conn.executescript(
            "CREATE TRIGGER reject_owner BEFORE INSERT ON workspace_members "
            "BEGIN SELECT RAISE(ABORT, 'synthetic membership failure'); END;"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic membership failure"):
        _create(store)
    assert _counts(store) == (0, 0)


def test_non_sensitive_local_workspace_needs_no_crypto(
    store: WorkspaceStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _deny_cryptography(monkeypatch)
    workspace = store.create_workspace(
        name="local",
        mount_type="local",
        mount_target=str(tmp_path),
        mount_options={"label": "ENC:literal-label", "ignored_dirs": ["cache"]},
        owner_id="alice",
    )
    assert store.get_workspace(workspace.id) == workspace
    assert store.list_workspaces_for_user("alice") == [workspace]
    assert _counts(store) == (1, 1)


@pytest.mark.parametrize(
    "value",
    [
        "ENC:synthetic-password",
        "ENC:json:synthetic-password",
        123456,
        None,
        {"arbitrary_secret": "synthetic-object-secret"},
        ["synthetic-list-secret", 42],
    ],
)
def test_sensitive_values_always_encrypted_and_preserve_type(
    store: WorkspaceStore, value: Any
) -> None:
    options = {"nested": [{"Credential": value, "label": "ENC:literal-label"}]}
    workspace = _create(store, options)
    persisted = json.loads(_raw(store, workspace.id))
    sealed = persisted["nested"][0]["Credential"]
    assert isinstance(sealed, str) and sealed.startswith("ENC:")
    assert sealed != value
    assert "synthetic-" not in sealed
    assert persisted["nested"][0]["label"] == "ENC:literal-label"
    assert store.get_workspace(workspace.id).mount_options == options


def test_valid_ciphertext_input_is_a_literal_and_cannot_bypass_encryption(
    store: WorkspaceStore,
) -> None:
    previous = json.loads(crypto.encrypt_options(_OPTIONS))["password"]
    workspace = _create(store, {"password": previous})
    assert json.loads(_raw(store, workspace.id))["password"] != previous
    assert store.get_workspace(workspace.id).mount_options == {"password": previous}


def test_old_plaintext_row_readable_without_crypto_and_not_silently_rewritten(
    store: WorkspaceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _create(store, {})
    legacy = json.dumps({**_OPTIONS, "nested": [{"token": "synthetic-legacy-token"}]})
    _replace_raw(store, workspace.id, legacy)
    _deny_cryptography(monkeypatch)
    assert store.get_workspace(workspace.id).mount_options == json.loads(legacy)
    assert store.list_workspaces_for_user("alice")[0].mount_options == json.loads(legacy)
    assert _raw(store, workspace.id) == legacy


def test_legacy_envelope_requires_original_key_and_preserves_stored_data(
    store: WorkspaceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_key = Fernet.generate_key()
    legacy = json.dumps(
        {"password": "ENC:" + Fernet(original_key).encrypt(b"synthetic-old-password").decode()}
    )
    workspace = _create(store, {})
    _replace_raw(store, workspace.id, legacy)
    _break_key(monkeypatch)
    with pytest.raises(WorkspaceCryptoError) as error:
        store.get_workspace(workspace.id)
    assert error.value.code == "workspace_credentials_unavailable"
    assert "ECHO_WORKSPACE_KEY" in error.value.hint
    assert "synthetic-old-password" not in str(error.value)
    assert _raw(store, workspace.id) == legacy
    monkeypatch.setenv("ECHO_WORKSPACE_KEY", original_key.decode())
    _reset_cipher()
    assert store.get_workspace(workspace.id).mount_options == {"password": "synthetic-old-password"}
    assert _raw(store, workspace.id) == legacy


@pytest.mark.parametrize("raw", ["not JSON", "[]", '{"password":"ENC:damaged"}'])
def test_corrupt_stored_configuration_never_becomes_empty_or_usable_credentials(
    store: WorkspaceStore, raw: str
) -> None:
    workspace = _create(store, {})
    _replace_raw(store, workspace.id, raw)
    with pytest.raises(WorkspaceCryptoError):
        store.get_workspace(workspace.id)
    assert _raw(store, workspace.id) == raw


def test_other_tenant_invalid_credentials_are_not_decrypted(
    store: WorkspaceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice_workspace = _create(store)
    bob_store = store.with_scope(TenantScope(tenant_id="tenant-b", actor_id="bob"))
    bob_workspace = bob_store.create_workspace(
        name="bob", mount_type="local", mount_target="/fixture", owner_id="bob"
    )
    _break_key(monkeypatch)
    assert bob_store.get_workspace(alice_workspace.id) is None
    assert bob_store.get_member_role(alice_workspace.id, "alice") is None
    assert bob_store.list_workspaces() == [bob_workspace]
    # Membership checks/revocation do not depend on decrypting credentials.
    assert store.get_member_role(alice_workspace.id, "alice") == "owner"


def _client(
    tmp_path: Path, store: WorkspaceStore
) -> tuple[TestClient, MountBackendRegistry, list[str]]:
    calls: list[str] = []

    class ObservedBackend(LocalMountBackend):
        def __init__(self, root: str, **_options: Any) -> None:
            calls.append("construct")
            super().__init__(root)

        async def test_connection(self) -> bool:
            calls.append("connect")
            return await super().test_connection()

        async def read_file(self, path: str) -> bytes:
            calls.append("read")
            return await super().read_file(path)

        async def write_file(self, path: str, content: bytes) -> None:
            calls.append("write")
            await super().write_file(path, content)

        async def list_dir(self, path: str, depth: int = 1):
            calls.append("list")
            return await super().list_dir(path, depth=depth)

    registry = MountBackendRegistry()
    registry.register("smb", ObservedBackend)
    registry.register("local", ObservedBackend)
    identities = IdentityStore()
    for actor in ("alice", "bob"):
        identities.add(
            Identity(actor_id=actor, metadata={"tenant_id": "tenant-a"}),
            api_key_plaintext=f"fixture-{actor}-api-key",
        )
    leases = LeaseStore(tmp_path / "leases.db")
    app = FastAPI()
    app.include_router(
        create_workspace_api_router(
            workspace_store=store,
            lease_store=leases,
            registry=registry,
            identity_store=identities,
            require_auth=True,
        )
    )
    app.include_router(
        create_fs_router(
            workspace_store=store,
            lease_store=leases,
            mount_registry=registry,
            identity_store=identities,
            require_auth=True,
        )
    )
    return TestClient(app), registry, calls


def _body(tmp_path: Path) -> dict[str, Any]:
    return {
        "name": "fixture",
        "mount_type": "smb",
        "mount_target": str(tmp_path),
        "mount_options": _OPTIONS,
        "owner_id": "alice",
    }


def test_create_api_checks_crypto_before_backend_connection(
    tmp_path: Path, store: WorkspaceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, calls = _client(tmp_path, store)
    _deny_cryptography(monkeypatch)
    with client:
        response = client.post("/api/workspaces", json=_body(tmp_path), headers=_HEADERS)
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "workspace_encryption_unavailable"
    assert _OPTIONS["password"] not in response.text
    assert calls == []
    assert _counts(store) == (0, 0)


def test_create_api_keeps_auth_owner_type_and_flag_checks_before_crypto(
    tmp_path: Path, store: WorkspaceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, calls = _client(tmp_path, store)
    _deny_cryptography(monkeypatch)
    with client:
        assert client.post("/api/workspaces", json=_body(tmp_path)).status_code == 401
        assert (
            client.post(
                "/api/workspaces", json={**_body(tmp_path), "owner_id": "bob"}, headers=_HEADERS
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/workspaces",
                json={**_body(tmp_path), "mount_type": "invalid"},
                headers=_HEADERS,
            ).status_code
            == 400
        )
        monkeypatch.setenv("ECHO_FF_UI_REMOTE_WORKSPACE", "0")
        ff.reload()
        assert (
            client.post("/api/workspaces", json=_body(tmp_path), headers=_HEADERS).status_code
            == 403
        )
    assert calls == []
    assert _counts(store) == (0, 0)


def test_local_create_and_health_api_work_without_crypto(
    tmp_path: Path, store: WorkspaceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, calls = _client(tmp_path, store)
    _deny_cryptography(monkeypatch)
    with client:
        response = client.post(
            "/api/workspaces",
            json={**_body(tmp_path), "mount_type": "local", "mount_options": {}},
            headers=_HEADERS,
        )
        assert response.status_code == 200
        workspace_id = response.json()["workspace"]["id"]
        assert (
            client.post(f"/api/workspaces/{workspace_id}/health", headers=_HEADERS).status_code
            == 200
        )
    assert "connect" in calls
    assert _counts(store) == (1, 1)


@pytest.mark.parametrize("operation", ["health", "get", "list", "read", "tree", "write"])
@pytest.mark.parametrize("failure", ["wrong_key", "missing_dependency", "corrupt"])
def test_api_never_connects_or_falls_back_locally_with_unusable_credentials(
    tmp_path: Path,
    store: WorkspaceStore,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failure: str,
) -> None:
    client, registry, calls = _client(tmp_path, store)
    (tmp_path / "fixture.txt").write_text("original", encoding="utf-8")
    local_fallbacks: list[str] = []
    original_local_check = fs_endpoints._assert_local_request_scope

    def observe_local_check(*args: Any, **kwargs: Any) -> Any:
        local_fallbacks.append("fallback")
        return original_local_check(*args, **kwargs)

    monkeypatch.setattr(fs_endpoints, "_assert_local_request_scope", observe_local_check)
    with client:
        response = client.post("/api/workspaces", json=_body(tmp_path), headers=_HEADERS)
        assert response.status_code == 200
        workspace_id = response.json()["workspace"]["id"]
        assert workspace_id in registry._instances
        path = f"{workspace_id}:fixture.txt"
        good = client.get("/api/fs/read", params={"path": path}, headers=_HEADERS)
        assert good.status_code == 200 and good.json()["content"] == "original"
        calls.clear()
        if failure == "wrong_key":
            _break_key(monkeypatch)
        elif failure == "missing_dependency":
            _deny_cryptography(monkeypatch)
        else:
            _replace_raw(store, workspace_id, '{"password":"ENC:damaged"}')
        raw_before = _raw(store, workspace_id)
        if operation == "write":
            response = client.post(
                "/api/fs/write", json={"path": path, "content": "changed"}, headers=_HEADERS
            )
        elif operation == "health":
            response = client.post(f"/api/workspaces/{workspace_id}/health", headers=_HEADERS)
        elif operation in {"read", "tree"}:
            response = client.get(f"/api/fs/{operation}", params={"path": path}, headers=_HEADERS)
        else:
            endpoint = {
                "get": f"/api/workspaces/{workspace_id}",
                "list": "/api/workspaces",
            }[operation]
            response = client.get(endpoint, headers=_HEADERS)
        assert response.status_code == 503
        assert response.json()["detail"]["error"] == "workspace_credentials_unavailable"
        assert _OPTIONS["password"] not in response.text
        assert "ENC:damaged" not in response.text
        assert calls == []
        assert local_fallbacks == []
        assert _raw(store, workspace_id) == raw_before
        assert (tmp_path / "fixture.txt").read_text(encoding="utf-8") == "original"


def test_api_hides_undecryptable_workspace_from_nonmember(
    tmp_path: Path, store: WorkspaceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _create(store)
    client, _, calls = _client(tmp_path, store)
    _break_key(monkeypatch)
    with client:
        response = client.post(
            f"/api/workspaces/{workspace.id}/health",
            headers={"Authorization": "Bearer fixture-bob-api-key"},
        )
    assert response.status_code == 404
    assert calls == []


@pytest.mark.parametrize(
    ("operation", "role"), [("read", None), ("tree", None), ("write", None), ("write", "viewer")]
)
def test_fs_api_checks_acl_before_revealing_credential_failure(
    tmp_path: Path,
    store: WorkspaceStore,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    role: str | None,
) -> None:
    workspace = _create(store)
    if role:
        store.add_member(workspace.id, "bob", role=role)
    client, _, calls = _client(tmp_path, store)
    _break_key(monkeypatch)
    headers = {"Authorization": "Bearer fixture-bob-api-key"}
    path = f"{workspace.id}:fixture.txt"
    with client:
        if operation == "write":
            response = client.post(
                "/api/fs/write", json={"path": path, "content": "changed"}, headers=headers
            )
        else:
            response = client.get(f"/api/fs/{operation}", params={"path": path}, headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == (
        "write_requires_editor" if role else "not_a_member"
    )
    assert "workspace_credentials_unavailable" not in response.text
    assert calls == []
