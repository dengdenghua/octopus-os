"""Human password step-up and one-shot high-risk appliance approvals."""

from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.app_registry.router import create_appliance_router
from appliance.approval import (
    APPROVAL_HEADER,
    ApprovalError,
    HighRiskApprovalService,
    create_approval_router,
)
from appliance.audit import ApplianceAudit
from appliance.files import FileManager, create_files_router
from appliance.identifiers import is_container_id
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "Approval-Secret_1234567890123456789012345678"
PASSWORD = "device-admin-password"
PASSWORD_HASH = hashlib.sha256(PASSWORD.encode()).hexdigest()


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("a" * 12, True),
        ("0123456789abcdef" * 4, True),
        ("a" * 11, False),
        ("a" * 65, False),
        ("A" * 12, False),
        ("a" * 12 + "/start", False),
        (None, False),
    ],
)
def test_canonical_container_identifier_contract(value, valid):
    assert is_container_id(value) is valid


class _Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _service(tmp_path, *, clock=None, max_failures=5, nonce=b"n" * 32):
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    service = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        clock=clock or _Clock(),
        boot_nonce=nonce,
        ttl_seconds=30,
        max_failures=max_failures,
    )
    return service, audit


def _issue(service, *, action="app.start", target="a" * 12):
    return service.issue(
        actor="local:admin",
        action=action,
        target=target,
        password=PASSWORD,
        client_ip="127.0.0.1",
    )[0]


def test_token_is_bound_to_actor_action_target_and_single_use(tmp_path):
    service, audit = _service(tmp_path)
    token = _issue(service)

    service.consume(
        token=token,
        actor="local:admin",
        action="app.start",
        target="a" * 12,
    )
    with pytest.raises(ApprovalError, match="valid high-risk approval"):
        service.consume(
            token=token,
            actor="local:admin",
            action="app.start",
            target="a" * 12,
        )

    outcomes = [event["payload"]["outcome"] for event in audit.recent(10)]
    assert outcomes == ["issued", "consumed", "denied"]


@pytest.mark.parametrize(
    ("actor", "action", "target"),
    [
        ("local:other", "app.start", "a" * 12),
        ("local:admin", "app.stop", "a" * 12),
        ("local:admin", "app.start", "b" * 12),
    ],
)
def test_token_rejects_intent_binding_changes(tmp_path, actor, action, target):
    service, _audit = _service(tmp_path)
    token = _issue(service)

    with pytest.raises(ApprovalError):
        service.consume(token=token, actor=actor, action=action, target=target)


def test_token_expires_and_dies_on_service_restart(tmp_path):
    clock = _Clock()
    service, _audit = _service(tmp_path, clock=clock, nonce=b"a" * 32)
    token = _issue(service)
    clock.now += 31

    with pytest.raises(ApprovalError):
        service.consume(
            token=token,
            actor="local:admin",
            action="app.start",
            target="a" * 12,
        )

    restarted, _audit = _service(tmp_path, clock=clock, nonce=b"b" * 32)
    fresh = _issue(restarted)
    other_restart, _audit = _service(tmp_path, clock=clock, nonce=b"c" * 32)
    with pytest.raises(ApprovalError):
        other_restart.consume(
            token=fresh,
            actor="local:admin",
            action="app.start",
            target="a" * 12,
        )


def test_wrong_password_is_rate_limited_without_logging_password(tmp_path):
    service, audit = _service(tmp_path, max_failures=2)

    with pytest.raises(ApprovalError) as first:
        service.issue(
            actor="local:admin",
            action="files.trash.empty",
            target="recycle-bin",
            password="wrong-one",
            client_ip="127.0.0.1",
        )
    with pytest.raises(ApprovalError) as locked:
        service.issue(
            actor="local:admin",
            action="files.trash.empty",
            target="recycle-bin",
            password="wrong-two",
            client_ip="127.0.0.1",
        )

    assert first.value.status_code == 403
    assert locked.value.status_code == 429
    assert locked.value.retry_after == 60
    log = audit.path.read_text(encoding="utf-8")
    assert "wrong-one" not in log and "wrong-two" not in log


def test_approval_issue_api_accepts_session_cookie_and_never_returns_password(tmp_path):
    service, _audit = _service(tmp_path)
    app = FastAPI()
    app.include_router(create_approval_router(service, jwt_secret=JWT_SECRET))
    client = TestClient(app)
    jwt = encode_jwt_hs256(
        {"sub": "local:admin", "iat": 0, "exp": 9_999_999_999},
        secret=JWT_SECRET,
    )
    client.cookies.set("echo_session", jwt)

    response = client.post(
        "/api/appliance/approvals",
        json={"action": "app.stop", "target": "a" * 12, "password": PASSWORD},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "app.stop"
    assert body["expiresIn"] == 30
    assert body["approvalToken"].count(".") == 1
    assert PASSWORD not in response.text
    assert APPROVAL_HEADER == "X-Echo-Approval"


class _Docker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def list_containers(self, include_stopped: bool = True):
        return []

    def start(self, container_id: str) -> None:
        self.calls.append(("start", container_id))

    def stop(self, container_id: str) -> None:
        self.calls.append(("stop", container_id))


def _authenticated_client(app: FastAPI) -> TestClient:
    client = TestClient(app)
    token = encode_jwt_hs256(
        {"sub": "local:admin", "iat": 0, "exp": 9_999_999_999},
        secret=JWT_SECRET,
    )
    client.cookies.set("echo_session", token)
    return client


def _issue_via_api(client: TestClient, *, action: str, target: str) -> str:
    response = client.post(
        "/api/appliance/approvals",
        json={"action": action, "target": target, "password": PASSWORD},
    )
    assert response.status_code == 200
    return str(response.json()["approvalToken"])


def test_app_control_requires_and_consumes_real_step_up(tmp_path):
    service, audit = _service(tmp_path)
    docker = _Docker()
    app = FastAPI()
    app.include_router(create_approval_router(service, jwt_secret=JWT_SECRET))
    app.include_router(
        create_appliance_router(
            docker=docker,
            jwt_secret=JWT_SECRET,
            approval=service,
            audit=audit,
        )
    )
    client = _authenticated_client(app)
    target = "a" * 12

    assert client.post(f"/api/appliance/apps/{target}/start").status_code == 403
    approval_token = _issue_via_api(client, action="app.start", target=target)
    response = client.post(
        f"/api/appliance/apps/{target}/start",
        headers={APPROVAL_HEADER: approval_token},
    )

    assert response.status_code == 200
    assert docker.calls == [("start", target)]
    assert (
        client.post(
            f"/api/appliance/apps/{target}/start",
            headers={APPROVAL_HEADER: approval_token},
        ).status_code
        == 403
    )
    records = [entry["payload"] for entry in audit.recent(20)]
    assert any(
        record["action"] == "app.start"
        and record["outcome"] == "succeeded"
        and record["actor"] == "local:admin"
        for record in records
    )


def test_physical_empty_trash_requires_password_step_up(tmp_path):
    service, audit = _service(tmp_path / "data")
    nas_root = tmp_path / "nas"
    nas_root.mkdir()
    (nas_root / "delete-me.txt").write_text("recoverable", encoding="utf-8")
    manager = FileManager(nas_root)
    manager.trash("delete-me.txt")
    app = FastAPI()
    app.include_router(create_approval_router(service, jwt_secret=JWT_SECRET))
    app.include_router(
        create_files_router(
            manager,
            jwt_secret=JWT_SECRET,
            approval=service,
            audit=audit,
        )
    )
    client = _authenticated_client(app)

    assert client.post("/api/appliance/files/trash/empty").status_code == 403
    approval_token = _issue_via_api(
        client,
        action="files.trash.empty",
        target="recycle-bin",
    )
    response = client.post(
        "/api/appliance/files/trash/empty",
        headers={APPROVAL_HEADER: approval_token},
    )

    assert response.json() == {"ok": True, "emptied": 1}
    assert manager.list_trash() == []
    records = [entry["payload"] for entry in audit.recent(20)]
    assert any(
        record["action"] == "files.trash.empty"
        and record["outcome"] == "succeeded"
        and record["metadata"]["emptied"] == 1
        for record in records
    )
