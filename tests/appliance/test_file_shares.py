from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.data_access import DataAccessDenied, DataAccessScope, DataPathRule
from appliance.file_share_routes import create_file_share_router
from appliance.file_shares import FileShareError, FileShareService
from appliance.files import FileManager


class _Approval:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def consume(self, **kwargs) -> None:
        assert kwargs["token"] == "approved"
        self.calls.append(kwargs)


class _Policy:
    def __init__(self) -> None:
        self.allowed = True

    def scope_for_actor(self, actor: str) -> DataAccessScope:
        if not self.allowed:
            raise DataAccessDenied("revoked")
        return DataAccessScope(
            actor=actor,
            operator=False,
            rules=(DataPathRule(("docs",), "read"),),
        )


@pytest.fixture()
def service(tmp_path):
    nas = tmp_path / "nas"
    (nas / "docs").mkdir(parents=True)
    (nas / "docs" / "report.txt").write_text("0123456789", encoding="utf-8")
    clock = [1_700_000_000]
    policy = _Policy()
    value = FileShareService(
        FileManager(nas),
        tmp_path / "state",
        token_pepper="test-secret",
        data_access=policy,
        clock=lambda: clock[0],
    )
    return value, clock, policy


def _create(service: FileShareService, *, limit: int = 2):
    plan = service.plan_create(
        "member:alice", "docs/report.txt", ttl_seconds=300, max_downloads=limit
    )
    return service.create(
        "member:alice",
        "docs/report.txt",
        ttl_seconds=300,
        max_downloads=limit,
        plan_id=plan["planId"],
    )


def test_token_is_returned_once_and_never_persisted_or_listed(service):
    shares, _, _ = service
    created = _create(shares)
    token = created["token"]
    registry = shares.registry.read_text(encoding="utf-8")

    assert token not in registry
    assert "docs/report.txt" in registry
    listed = shares.list("member:alice")
    assert listed == [created["share"] | {"active": True}]
    assert "path" not in listed[0]
    assert "token" not in json.dumps(listed)


def test_download_limit_expiry_identity_and_acl_are_fail_closed(service):
    shares, clock, policy = service
    first = _create(shares, limit=1)
    opened = shares.redeem(first["token"])
    assert opened.stream.read() == b"0123456789"
    opened.stream.close()
    with pytest.raises(FileShareError, match="not found"):
        shares.redeem(first["token"])

    expiring = _create(shares)
    clock[0] += 301
    with pytest.raises(FileShareError, match="not found"):
        shares.redeem(expiring["token"])

    # Expired records are pruned during the next plan/apply, so they cannot
    # permanently consume the bounded registry.
    changed = _create(shares)
    assert expiring["share"]["id"] not in shares.registry.read_text(encoding="utf-8")
    shares.manager.file_for_download("docs/report.txt").write_text("changed")
    with pytest.raises(FileShareError, match="not found"):
        shares.redeem(changed["token"])

    shares.manager.file_for_download("docs/report.txt").write_text("0123456789")
    revoked_acl = _create(shares)
    policy.allowed = False
    with pytest.raises(DataAccessDenied):
        shares.redeem(revoked_acl["token"])


def test_plan_is_bound_to_registry_revision(service):
    shares, _, _ = service
    stale = shares.plan_create("member:alice", "docs/report.txt", ttl_seconds=300, max_downloads=2)
    _create(shares)
    with pytest.raises(FileShareError, match="plan changed"):
        shares.create(
            "member:alice",
            "docs/report.txt",
            ttl_seconds=300,
            max_downloads=2,
            plan_id=stale["planId"],
        )


def test_router_uses_approval_and_public_route_supports_range(service):
    shares, _, policy = service
    approval = _Approval()
    app = FastAPI()
    app.include_router(create_file_share_router(shares, approval=approval))

    with TestClient(app) as client:
        plan = client.post(
            "/api/appliance/file-shares/plans",
            json={"path": "docs/report.txt", "ttlSeconds": 300, "maxDownloads": 2},
        ).json()
        created = client.post(
            "/api/appliance/file-shares/apply",
            headers={"X-Echo-Approval": "approved"},
            json={
                "path": "docs/report.txt",
                "ttlSeconds": 300,
                "maxDownloads": 2,
                "planId": plan["planId"],
            },
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["url"].startswith("/api/public/file-shares/")
        assert approval.calls[0]["action"] == "files.share.create"
        assert approval.calls[0]["target"] == plan["planId"]

        downloaded = client.get(payload["url"], headers={"Range": "bytes=2-5"})
        assert downloaded.status_code == 206
        assert downloaded.content == b"2345"
        assert downloaded.headers["content-range"] == "bytes 2-5/10"
        assert downloaded.headers["cache-control"] == "private, no-store"
        assert "docs/report.txt" not in json.dumps(client.get("/api/appliance/file-shares").json())
        policy.allowed = False
        assert client.get(payload["url"]).status_code == 404


def test_revocation_invalidates_public_url(service):
    shares, _, _ = service
    created = _create(shares)
    plan = shares.plan_revoke("member:alice", created["share"]["id"])
    shares.revoke("member:alice", created["share"]["id"], plan_id=plan["planId"])
    with pytest.raises(FileShareError, match="not found"):
        shares.redeem(created["token"])


def test_corrupt_registry_fails_closed(service):
    shares, _, _ = service
    shares.registry.write_text('{"schema":"echo.file-shares.v1","shares":[]}', encoding="utf-8")
    with pytest.raises(OSError, match="invalid file-share registry"):
        shares.list("member:alice")
