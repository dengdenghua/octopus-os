"""Tests for runtime.workspace.store + crypto.

Covers:
  1. Workspace CRUD (create, get, list, list_for_user, delete)
  2. Member management (add, remove, list, get_member_role, role upsert)
  3. Crypto round-trip (sensitive fields, nested fields, plaintext fallback)
  4. Cascade delete (workspace removal wipes members)
  5. Model dataclass round-trip

Uses a tmp-path SQLite DB for isolation. The crypto fixture forces a known
Fernet key via ``ECHO_WORKSPACE_KEY`` so encryption behavior is
deterministic across hosts.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runtime.workspace import (
    Workspace,
    WorkspaceMember,
    WorkspaceStore,
    decrypt_options,
    encrypt_options,
)
from runtime.workspace import crypto as crypto_mod

# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> WorkspaceStore:
    return WorkspaceStore(db_path=tmp_path / "workspaces.db")


@pytest.fixture(autouse=True)
def _reset_crypto_cache() -> None:
    """Reset the workspace crypto module cache before and after each test so
    per-test env-var changes (``ECHO_WORKSPACE_KEY``) take effect cleanly
    and don't leak into the next test.
    """
    crypto_mod._CIPHER_CACHE = None
    crypto_mod._CIPHER_KEY_CACHE = None
    crypto_mod._MACHINE_ID_CACHE = None
    yield
    crypto_mod._CIPHER_CACHE = None
    crypto_mod._CIPHER_KEY_CACHE = None
    crypto_mod._MACHINE_ID_CACHE = None


@pytest.fixture
def crypto_enabled(monkeypatch: pytest.MonkeyPatch) -> str:
    """Force the crypto module to use a known Fernet key for tests.

    Skips the test if ``cryptography`` is not installed — the rest of the
    suite still passes because non-crypto tests don't depend on encryption.
    """
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("ECHO_WORKSPACE_KEY", key)
    # The autouse ``_reset_crypto_cache`` already cleared the cache, so the
    # next call to ``_cipher()`` will pick up the new env var.
    return key


# ─── 1. Workspace CRUD ─────────────────────────────────────────────────────


def test_create_and_get_workspace(store: WorkspaceStore) -> None:
    ws = store.create_workspace(
        name="My NAS",
        mount_type="smb",
        mount_target="smb://nas.local/projects",
        mount_options={"username": "alice", "password": "hunter2"},
        owner_id="user-1",
    )
    assert ws.id
    assert ws.name == "My NAS"
    assert ws.mount_type == "smb"
    assert ws.mount_target == "smb://nas.local/projects"
    assert ws.mount_options == {"username": "alice", "password": "hunter2"}
    assert ws.owner_id == "user-1"
    assert ws.created_at > 0

    fetched = store.get_workspace(ws.id)
    assert fetched is not None
    assert fetched.id == ws.id
    assert fetched.name == "My NAS"
    assert fetched.mount_options == {"username": "alice", "password": "hunter2"}
    assert fetched.owner_id == "user-1"
    assert fetched.created_at == ws.created_at


def test_get_workspace_returns_none_for_unknown(store: WorkspaceStore) -> None:
    assert store.get_workspace("nope") is None


def test_list_workspaces_orders_by_created_at(store: WorkspaceStore) -> None:
    a = store.create_workspace(
        name="A",
        mount_type="local",
        mount_target="/tmp/a",
        owner_id="u1",
        created_at=1.0,
    )
    b = store.create_workspace(
        name="B",
        mount_type="local",
        mount_target="/tmp/b",
        owner_id="u1",
        created_at=2.0,
    )
    listed = store.list_workspaces()
    assert [w.id for w in listed] == [a.id, b.id]


def test_list_workspaces_for_user_returns_only_member_workspaces(
    store: WorkspaceStore,
) -> None:
    w1 = store.create_workspace(
        name="w1",
        mount_type="local",
        mount_target="/x",
        owner_id="u1",
    )
    w2 = store.create_workspace(
        name="w2",
        mount_type="local",
        mount_target="/y",
        owner_id="u2",
    )
    store.add_member(w2.id, "u1", role="viewer")

    listed = store.list_workspaces_for_user("u1")
    assert {w.id for w in listed} == {w1.id, w2.id}

    # u3 is not a member of any workspace.
    assert store.list_workspaces_for_user("u3") == []


def test_list_workspaces_for_user_returns_empty_for_empty_id(
    store: WorkspaceStore,
) -> None:
    assert store.list_workspaces_for_user("") == []


def test_delete_workspace_returns_true_then_false(store: WorkspaceStore) -> None:
    ws = store.create_workspace(
        name="x",
        mount_type="local",
        mount_target="/x",
        owner_id="u1",
    )
    assert store.delete_workspace(ws.id) is True
    # Idempotent — second call returns False (already gone).
    assert store.delete_workspace(ws.id) is False
    assert store.get_workspace(ws.id) is None


def test_delete_workspace_with_empty_id_returns_false(store: WorkspaceStore) -> None:
    assert store.delete_workspace("") is False


def test_create_workspace_rejects_invalid_mount_type(store: WorkspaceStore) -> None:
    with pytest.raises(ValueError, match="mount_type"):
        store.create_workspace(
            name="x",
            mount_type="ftp",
            mount_target="/x",
            owner_id="u1",
        )


def test_create_workspace_rejects_empty_name(store: WorkspaceStore) -> None:
    with pytest.raises(ValueError, match="name"):
        store.create_workspace(
            name=" ",
            mount_type="local",
            mount_target="/x",
            owner_id="u1",
        )


def test_create_workspace_rejects_empty_owner(store: WorkspaceStore) -> None:
    with pytest.raises(ValueError, match="owner_id"):
        store.create_workspace(
            name="x",
            mount_type="local",
            mount_target="/x",
            owner_id="",
        )


def test_create_workspace_accepts_explicit_id_and_timestamp(
    store: WorkspaceStore,
) -> None:
    ws = store.create_workspace(
        name="x",
        mount_type="local",
        mount_target="/x",
        owner_id="u1",
        workspace_id="ws-fixed",
        created_at=12345.0,
    )
    assert ws.id == "ws-fixed"
    assert ws.created_at == 12345.0
    fetched = store.get_workspace("ws-fixed")
    assert fetched is not None
    assert fetched.created_at == 12345.0


def test_create_workspace_auto_adds_owner_as_member(store: WorkspaceStore) -> None:
    ws = store.create_workspace(
        name="x",
        mount_type="local",
        mount_target="/x",
        owner_id="u1",
    )
    members = store.list_members(ws.id)
    assert len(members) == 1
    assert members[0].member_id == "u1"
    assert members[0].role == "owner"
    # Owner shows up in list_workspaces_for_user without an explicit add.
    assert store.list_workspaces_for_user("u1") == [ws]


def test_all_mount_types_round_trip(store: WorkspaceStore) -> None:
    for mount_type in ("local", "smb", "nfs", "webdav", "sftp", "s3"):
        ws = store.create_workspace(
            name=f"ws-{mount_type}",
            mount_type=mount_type,
            mount_target=f"{mount_type}://example",
            owner_id="u1",
        )
        fetched = store.get_workspace(ws.id)
        assert fetched is not None
        assert fetched.mount_type == mount_type


# ─── 2. Members ─────────────────────────────────────────────────────────────


def test_add_member_and_list(store: WorkspaceStore) -> None:
    ws = store.create_workspace(
        name="w",
        mount_type="local",
        mount_target="/x",
        owner_id="u1",
    )
    # Owner is auto-added.
    members = store.list_members(ws.id)
    assert len(members) == 1
    assert members[0].member_id == "u1"
    assert members[0].role == "owner"

    m = store.add_member(ws.id, "u2", role="editor")
    assert m.workspace_id == ws.id
    assert m.member_id == "u2"
    assert m.role == "editor"
    assert m.added_at > 0

    members = store.list_members(ws.id)
    assert {m.member_id for m in members} == {"u1", "u2"}


def test_add_member_upserts_role_on_conflict(store: WorkspaceStore) -> None:
    ws = store.create_workspace(
        name="w",
        mount_type="local",
        mount_target="/x",
        owner_id="u1",
    )
    store.add_member(ws.id, "u2", role="viewer")
    assert store.get_member_role(ws.id, "u2") == "viewer"

    # Re-add with a new role — should update, not duplicate.
    store.add_member(ws.id, "u2", role="editor")
    assert store.get_member_role(ws.id, "u2") == "editor"

    members = store.list_members(ws.id)
    u2_rows = [m for m in members if m.member_id == "u2"]
    assert len(u2_rows) == 1


def test_get_member_role_returns_none_for_unknown(store: WorkspaceStore) -> None:
    ws = store.create_workspace(
        name="w",
        mount_type="local",
        mount_target="/x",
        owner_id="u1",
    )
    assert store.get_member_role(ws.id, "ghost") is None
    # Unknown workspace should also return None rather than raise.
    assert store.get_member_role("ghost-ws", "u1") is None


def test_remove_member_returns_bool(store: WorkspaceStore) -> None:
    ws = store.create_workspace(
        name="w",
        mount_type="local",
        mount_target="/x",
        owner_id="u1",
    )
    store.add_member(ws.id, "u2", role="viewer")
    assert store.remove_member(ws.id, "u2") is True
    # Second call returns False — already removed.
    assert store.remove_member(ws.id, "u2") is False
    assert store.get_member_role(ws.id, "u2") is None


def test_remove_member_with_empty_ids_returns_false(store: WorkspaceStore) -> None:
    assert store.remove_member("", "u1") is False
    assert store.remove_member("ws-1", "") is False


def test_add_member_rejects_invalid_role(store: WorkspaceStore) -> None:
    ws = store.create_workspace(
        name="w",
        mount_type="local",
        mount_target="/x",
        owner_id="u1",
    )
    with pytest.raises(ValueError, match="role"):
        store.add_member(ws.id, "u2", role="admin")


def test_add_member_rejects_empty_member_id(store: WorkspaceStore) -> None:
    ws = store.create_workspace(
        name="w",
        mount_type="local",
        mount_target="/x",
        owner_id="u1",
    )
    with pytest.raises(ValueError, match="member_id"):
        store.add_member(ws.id, "", role="viewer")


def test_add_member_rejects_unknown_workspace(store: WorkspaceStore) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        store.add_member("ghost-ws", "u1", role="viewer")


def test_list_members_returns_empty_for_empty_id(store: WorkspaceStore) -> None:
    assert store.list_members("") == []


def test_all_member_roles_round_trip(store: WorkspaceStore) -> None:
    ws = store.create_workspace(
        name="w",
        mount_type="local",
        mount_target="/x",
        owner_id="u1",
    )
    for role in ("editor", "reviewer", "viewer"):
        store.add_member(ws.id, f"u-{role}", role=role)
        assert store.get_member_role(ws.id, f"u-{role}") == role


# ─── 3. Cascade delete ─────────────────────────────────────────────────────


def test_delete_workspace_cascades_members(store: WorkspaceStore) -> None:
    ws = store.create_workspace(
        name="w",
        mount_type="local",
        mount_target="/x",
        owner_id="u1",
    )
    store.add_member(ws.id, "u2", role="editor")
    store.add_member(ws.id, "u3", role="viewer")
    assert len(store.list_members(ws.id)) == 3

    assert store.delete_workspace(ws.id) is True

    # No members should remain for the deleted workspace.
    assert store.list_members(ws.id) == []
    # The members should not reappear in any user's workspace listing.
    assert store.list_workspaces_for_user("u1") == []
    assert store.list_workspaces_for_user("u2") == []
    assert store.list_workspaces_for_user("u3") == []


def test_delete_workspace_does_not_affect_others(store: WorkspaceStore) -> None:
    w1 = store.create_workspace(
        name="w1",
        mount_type="local",
        mount_target="/x",
        owner_id="u1",
    )
    w2 = store.create_workspace(
        name="w2",
        mount_type="local",
        mount_target="/y",
        owner_id="u1",
    )
    store.add_member(w2.id, "u2", role="editor")

    assert store.delete_workspace(w1.id) is True

    # w2 should be untouched.
    assert store.get_workspace(w2.id) is not None
    assert len(store.list_members(w2.id)) == 2  # owner u1 + u2
    assert store.get_member_role(w2.id, "u2") == "editor"


# ─── 4. Crypto ─────────────────────────────────────────────────────────────


def test_encrypt_options_round_trip(crypto_enabled: str) -> None:
    options = {
        "username": "alice",
        "password": "hunter2",
        "endpoint": "https://nas.local",
    }
    encrypted = encrypt_options(options)
    # Sensitive field value is NOT plaintext in the JSON blob.
    assert "hunter2" not in encrypted
    # Non-sensitive field stays plaintext (human-readable on disk).
    assert "alice" in encrypted
    assert "https://nas.local" in encrypted

    decrypted = decrypt_options(encrypted)
    assert decrypted == options


def test_encrypt_options_handles_nested_sensitive_fields(
    crypto_enabled: str,
) -> None:
    options = {
        "s3": {
            "access_key": "AKIAEXAMPLE",
            "secret_key": "shh",
            "region": "us-east-1",
        },
        "password": "top",
        "credentials": [
            {"token": "tok-1", "label": "first"},
            {"token": "tok-2", "label": "second"},
        ],
    }
    encrypted = encrypt_options(options)
    # All sensitive values are encrypted.
    assert "AKIAEXAMPLE" not in encrypted
    assert "shh" not in encrypted
    assert "top" not in encrypted
    assert "tok-1" not in encrypted
    assert "tok-2" not in encrypted
    # Non-sensitive values stay plaintext.
    assert "us-east-1" in encrypted
    assert "first" in encrypted
    assert "second" in encrypted

    decrypted = decrypt_options(encrypted)
    assert decrypted == options


def test_encrypt_options_is_case_insensitive(crypto_enabled: str) -> None:
    options = {"Password": "secret", "TOKEN": "tok"}
    encrypted = encrypt_options(options)
    assert "secret" not in encrypted
    assert "tok" not in encrypted
    assert decrypt_options(encrypted) == options


def test_encrypt_options_does_not_double_encrypt(crypto_enabled: str) -> None:
    """Re-encrypting an already-encrypted value should be a no-op for that
    value (idempotent), so a store round-trip followed by another
    ``encrypt_options`` call doesn't corrupt the value.
    """
    options = {"password": "secret"}
    once = encrypt_options(options)
    # `once` is a JSON string; parse it and re-encrypt to simulate double-encrypt.
    import json as _json

    parsed = _json.loads(once)
    twice = encrypt_options(parsed)
    assert decrypt_options(twice) == options


def test_decrypt_options_returns_empty_dict_for_empty_input() -> None:
    assert decrypt_options("") == {}


def test_decrypt_options_returns_empty_dict_for_invalid_json() -> None:
    assert decrypt_options("not json") == {}


def test_decrypt_options_returns_empty_dict_for_non_dict_json() -> None:
    assert decrypt_options("[1, 2, 3]") == {}


def test_encrypt_options_handles_empty_dict(crypto_enabled: str) -> None:
    assert encrypt_options({}) == "{}"
    assert decrypt_options("{}") == {}


def test_workspace_store_round_trips_mount_options_with_crypto(
    store: WorkspaceStore,
    crypto_enabled: str,
) -> None:
    options = {"username": "bob", "password": "p@ss", "share": "data"}
    ws = store.create_workspace(
        name="ws",
        mount_type="smb",
        mount_target="smb://nas/share",
        mount_options=options,
        owner_id="u1",
    )
    fetched = store.get_workspace(ws.id)
    assert fetched is not None
    assert fetched.mount_options == options


def test_mount_options_json_on_disk_has_encrypted_password(
    store: WorkspaceStore,
    crypto_enabled: str,
) -> None:
    """Inspect the raw SQLite column to confirm the secret is not plaintext
    at rest — i.e. the encryption happens before the row is written, not
    just on read.
    """
    options = {"password": "secret123", "username": "carol"}
    ws = store.create_workspace(
        name="ws",
        mount_type="smb",
        mount_target="smb://nas/share",
        mount_options=options,
        owner_id="u1",
    )
    with sqlite3.connect(str(store.db_path)) as conn:
        row = conn.execute(
            "SELECT mount_options_json FROM workspaces WHERE id=?",
            (ws.id,),
        ).fetchone()
    raw = row[0]
    assert "secret123" not in raw
    assert "carol" in raw  # non-sensitive preserved
    assert "ENC:" in raw


def test_two_workspaces_share_encryption_state(
    store: WorkspaceStore,
    crypto_enabled: str,
) -> None:
    """Two workspaces created with the same key must both decrypt cleanly."""
    opts1 = {"password": "p1", "tag": "a"}
    opts2 = {"password": "p2", "tag": "b"}
    w1 = store.create_workspace(
        name="w1",
        mount_type="local",
        mount_target="/x",
        mount_options=opts1,
        owner_id="u1",
    )
    w2 = store.create_workspace(
        name="w2",
        mount_type="local",
        mount_target="/y",
        mount_options=opts2,
        owner_id="u1",
    )
    assert store.get_workspace(w1.id).mount_options == opts1
    assert store.get_workspace(w2.id).mount_options == opts2


# ─── 5. Model round-trip ───────────────────────────────────────────────────


def test_workspace_model_round_trip() -> None:
    ws = Workspace(
        id="ws1",
        name="Test",
        mount_type="local",
        mount_target="/tmp/x",
        mount_options={"a": 1, "b": [1, 2, 3]},
        owner_id="u1",
        created_at=12345.0,
    )
    restored = Workspace.from_dict(ws.to_dict())
    assert restored == ws


def test_workspace_model_defaults_unknown_mount_type_to_local() -> None:
    ws = Workspace.from_dict(
        {
            "id": "ws1",
            "name": "n",
            "mount_type": "exotic",  # not in VALID_MOUNT_TYPES
            "mount_target": "/x",
        }
    )
    assert ws.mount_type == "local"
    assert ws.owner_id == ""
    assert ws.created_at == 0.0
    assert ws.mount_options == {}


def test_workspace_member_model_round_trip() -> None:
    m = WorkspaceMember(
        workspace_id="ws1",
        member_id="u1",
        role="owner",
        added_at=1.0,
    )
    assert WorkspaceMember.from_dict(m.to_dict()) == m


def test_workspace_member_model_defaults_unknown_role_to_viewer() -> None:
    m = WorkspaceMember.from_dict(
        {
            "workspace_id": "ws1",
            "member_id": "u1",
            "role": "superuser",  # not in VALID_MEMBER_ROLES
        }
    )
    assert m.role == "viewer"
    assert m.added_at == 0.0

