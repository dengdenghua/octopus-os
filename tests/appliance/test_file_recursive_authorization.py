"""Authenticated directory operations must respect nested share permissions."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.audit import ApplianceAudit
from appliance.data_access import DataAccessScope, DataPathRule
from appliance.files import FileManager, create_files_router
from runtime.safety.auth.identity import encode_jwt_hs256

_SECRET = "synthetic-file-recursive-authorization-test-key"


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "nas"
    (root / "Family" / "Private").mkdir(parents=True)
    (root / "Drop Box").mkdir()
    (root / "Family" / "public.txt").write_bytes(b"public synthetic document")
    (root / "Family" / "Private" / "secret.txt").write_bytes(b"private synthetic document")
    return FileManager(root, upload_reserve_bytes=0)


def _client(manager, rules, *, operator=False):
    class Policy:
        def scope_for_actor(self, actor):
            assert actor == "local:alice"
            return DataAccessScope(
                actor=actor, operator=operator, rules=tuple(rules), root=manager.root
            )

    audit = ApplianceAudit.from_data_dir(manager.root.parent / "audit", jwt_secret=_SECRET)
    app = FastAPI()
    app.include_router(
        create_files_router(manager, jwt_secret=_SECRET, audit=audit, data_access=Policy())
    )
    client = TestClient(app)
    client.cookies.set(
        "echo_session",
        encode_jwt_hs256(
            {"sub": "local:alice", "iat": 0, "exp": 9_999_999_999},
            secret=_SECRET,
        ),
    )
    return client


def _rules(private="none"):
    return (
        DataPathRule(("Family",), "readWrite"),
        DataPathRule(("Family", "Private"), private),
        DataPathRule(("Drop Box",), "readWrite"),
    )


def _hashes(root: Path):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("operation", ["copy", "move", "trash"])
def test_parent_operation_cannot_copy_or_mutate_denied_child(library, operation):
    client = _client(library, _rules())
    before = _hashes(library.root)
    assert (
        client.get(
            "/api/appliance/files/download", params={"path": "Family/Private/secret.txt"}
        ).status_code
        == 403
    )
    body = {"path": "Family"} if operation == "trash" else {"src": "Family", "dst": "Drop Box"}
    response = client.post(f"/api/appliance/files/{operation}", json=body)
    assert response.status_code == 403
    assert "secret.txt" not in response.text
    assert _hashes(library.root) == before
    assert not (library.root / "Drop Box" / "Family").exists()
    assert client.get("/api/appliance/files/trash").json()["entries"] == []


@pytest.mark.parametrize("operation", ["move", "trash"])
def test_read_only_child_cannot_be_modified_through_writable_parent(library, operation):
    client = _client(library, _rules("read"))
    before = _hashes(library.root)
    body = {"path": "Family"} if operation == "trash" else {"src": "Family", "dst": "Drop Box"}
    assert client.post(f"/api/appliance/files/{operation}", json=body).status_code == 403
    assert _hashes(library.root) == before


def test_read_only_subtree_can_be_copied_when_every_source_is_readable(library):
    client = _client(library, _rules("read"))
    before = _hashes(library.root / "Family")
    response = client.post("/api/appliance/files/copy", json={"src": "Family", "dst": "Drop Box"})
    assert response.status_code == 200, response.text
    assert response.json()["entry"]["path"] == "Drop Box/Family"
    assert _hashes(library.root / "Drop Box" / "Family") == before
    assert _hashes(library.root / "Family") == before


@pytest.mark.parametrize("operation", ["copy", "move"])
def test_actual_destination_tree_is_authorized_before_any_write(library, operation):
    rules = (*_rules("readWrite"), DataPathRule(("Drop Box", "Family", "Private"), "none"))
    client = _client(library, rules)
    before = _hashes(library.root)
    response = client.post(
        f"/api/appliance/files/{operation}", json={"src": "Family", "dst": "Drop Box"}
    )
    assert response.status_code == 403
    assert _hashes(library.root) == before
    assert not (library.root / "Drop Box" / "Family").exists()


def test_restore_checks_original_subtree_permissions_and_retains_denied_entry(library):
    record = library.trash("Family")
    client = _client(library, _rules())
    before = _hashes(library.root)
    response = client.post("/api/appliance/files/trash/restore", json={"id": record["id"]})
    assert response.status_code == 403
    assert _hashes(library.root) == before
    assert library.list_trash() == [record]
    assert not (library.root / "Family").exists()


def test_restore_checks_the_collision_renamed_destination(library):
    record = library.trash("Family/public.txt")
    (library.root / "Family" / "public.txt").write_bytes(b"new independent document")
    fallback = f"public-restored-{record['id'][:6]}.txt"
    rules = (*_rules("readWrite"), DataPathRule(("Family", fallback), "none"))
    client = _client(library, rules)
    before = _hashes(library.root)
    response = client.post("/api/appliance/files/trash/restore", json={"id": record["id"]})
    assert response.status_code == 403
    assert _hashes(library.root) == before
    assert library.list_trash() == [record]


def test_occupied_restore_fallback_is_not_overwritten(library):
    record = library.trash("Family/public.txt")
    (library.root / "Family" / "public.txt").write_bytes(b"new independent document")
    fallback = library.root / "Family" / f"public-restored-{record['id'][:6]}.txt"
    fallback.write_bytes(b"another independent document")
    client = _client(library, (), operator=True)
    before = _hashes(library.root)
    response = client.post("/api/appliance/files/trash/restore", json={"id": record["id"]})
    assert response.status_code == 409
    assert _hashes(library.root) == before
    assert library.list_trash() == [record]


def test_denial_for_a_different_tree_does_not_block_an_authorized_directory(library):
    rules = (*_rules("readWrite"), DataPathRule(("Family Elsewhere", "Private"), "none"))
    client = _client(library, rules)
    original = _hashes(library.root / "Family")
    moved = client.post("/api/appliance/files/move", json={"src": "Family", "dst": "Drop Box"})
    assert moved.status_code == 200, moved.text
    assert _hashes(library.root / "Drop Box" / "Family") == original
    assert not (library.root / "Family").exists()


def test_operator_can_restore_tree_and_unauthenticated_client_cannot_mutate_it(library):
    client = _client(library, _rules(), operator=True)
    original = _hashes(library.root / "Family")
    trashed = client.post("/api/appliance/files/trash", json={"path": "Family"})
    assert trashed.status_code == 200
    record = trashed.json()["trashed"]
    assert (
        client.post("/api/appliance/files/trash/restore", json={"id": record["id"]}).status_code
        == 200
    )
    assert _hashes(library.root / "Family") == original
    client.cookies.clear()
    assert client.post("/api/appliance/files/trash", json={"path": "Family"}).status_code == 401
    assert _hashes(library.root / "Family") == original
