"""Real HTTP auth/approval with an injected bounded organization provider.

Most tests inject a bounded provider to isolate request boundaries. The final
integration uses the actual service, private JSON receipts and filesystem IO.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.approval import HighRiskApprovalService, create_approval_router
from appliance.audit import ApplianceAudit
from appliance.files.organization import OrganizationError
from appliance.files.organization_router import create_file_organization_router
from appliance.security import ApplianceAuthenticator
from runtime.safety.auth.identity import encode_jwt_hs256

SECRET = "Organization-Router-Only_12345678901234567890123456789"
PASSWORD = "synthetic-admin-password"
PLAN_ID = "a" * 64
UNDO_ID = "b" * 64
BASE = "/api/appliance/files/organize/plans"


def headers(actor: str = "local:admin") -> dict[str, str]:
    token = encode_jwt_hs256({"sub": actor, "iat": 0, "exp": 9_999_999_999}, secret=SECRET)
    return {"Authorization": f"Bearer {token}"}


def public_plan(plan_id: str = PLAN_ID) -> dict[str, Any]:
    direction = "undo" if plan_id == UNDO_ID else "apply"
    return {
        "schema": "echo.files.organize.plan.v1",
        "planId": plan_id,
        "path": "receipts",
        "direction": direction,
        "ready": True,
        "requiresApproval": True,
        "approval": {"action": f"files.organize.{direction}", "target": plan_id},
    }


class BoundedProvider:
    def __init__(self):
        self.calls: list[tuple[str, str, str, int]] = []
        self.owner = "local:admin"
        self.plan = public_plan()
        self.failure: OrganizationError | None = None
        self.outcome = {
            "state": "partial",
            "executionComplete": True,
            "counts": {
                "moved": 1,
                "failed": 0,
                "conflicts": 1,
                "pending": 0,
                "uncertain": 0,
                "skipped": 0,
            },
            "results": [{"status": "conflict", "committed": False, "actualPath": None}],
        }

    def _record(self, method, actor, value):
        self.calls.append((method, actor, value, threading.get_ident()))
        if actor != self.owner:
            raise OrganizationError(403, "organization_owner_mismatch", "无权读取此整理计划")
        if self.failure:
            raise self.failure

    def create_plan(self, actor, path):
        self._record("create_plan", actor, path)
        return self.plan

    def get_plan(self, actor, plan_id):
        self._record("get_plan", actor, plan_id)
        return self.plan if plan_id == PLAN_ID else public_plan(plan_id)

    def list_plans(self, actor, path):
        self._record("list_plans", actor, path)
        return {
            "schema": "echo.files.organize.plans.v1",
            "path": path,
            "complete": False,
            "plans": [],
        }

    def get_result(self, actor, plan_id):
        self._record("get_result", actor, plan_id)
        return self.outcome

    def apply(self, actor, plan_id):
        self._record("apply", actor, plan_id)
        return self.outcome

    def create_undo_plan(self, actor, plan_id):
        self._record("create_undo_plan", actor, plan_id)
        return public_plan(UNDO_ID)

    def cancel(self, actor, plan_id):
        self._record("cancel", actor, plan_id)
        return {**self.outcome, "state": "cancelled"}


@pytest.fixture
def rig(tmp_path):
    provider = BoundedProvider()
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=SECRET)
    approval = HighRiskApprovalService(
        password_hash=hashlib.sha256(PASSWORD.encode()).hexdigest(),
        jwt_secret=SECRET,
        audit=audit,
    )
    auth = ApplianceAuthenticator(SECRET)
    app = FastAPI()
    app.include_router(create_approval_router(approval, authenticator=auth))
    app.include_router(
        create_file_organization_router(provider, authenticator=auth, approval=approval)
    )
    with TestClient(app) as client:
        yield client, provider, approval, audit


def issue(client, *, plan_id=PLAN_ID, action="files.organize.apply", actor="local:admin"):
    response = client.post(
        "/api/appliance/approvals",
        headers=headers(actor),
        json={"action": action, "target": plan_id, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["approvalToken"]


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "?path=receipts", None),
        ("POST", "", {"path": "receipts"}),
        ("GET", f"/{PLAN_ID}", None),
        ("GET", f"/{PLAN_ID}/result", None),
        ("POST", f"/{PLAN_ID}/apply", {}),
        ("POST", f"/{PLAN_ID}/undo-plan", {}),
        ("POST", f"/{PLAN_ID}/cancel", {}),
    ],
)
def test_every_route_requires_authentication_before_provider(rig, method, suffix, body):
    client, provider, *_ = rig
    assert client.request(method, BASE + suffix, json=body).status_code == 401
    assert not provider.calls


@pytest.mark.parametrize(
    ("suffix", "body"),
    [
        ("", {"path": "receipts", "actor": "local:admin"}),
        ("", {"path": "receipts", "project_root": "/etc"}),
        (f"/{PLAN_ID}/apply", {"targets": ["/etc/replaced"]}),
        (f"/{PLAN_ID}/apply", {"action": "files.organize.undo"}),
        (f"/{PLAN_ID}/undo-plan", {"actor": "local:admin"}),
        (f"/{PLAN_ID}/cancel", {"force": True}),
    ],
)
def test_body_cannot_supply_actor_roots_targets_or_override_approval(rig, suffix, body):
    client, provider, *_ = rig
    assert client.post(BASE + suffix, headers=headers(), json=body).status_code == 422
    assert not provider.calls


@pytest.mark.parametrize("plan_id", ["a" * 63, "A" * 64, "invalid"])
def test_plan_ids_are_validated_before_reading_provider(rig, plan_id):
    client, provider, *_ = rig
    assert client.get(f"{BASE}/{plan_id}", headers=headers()).status_code == 422
    assert not provider.calls


def test_current_cookie_identity_drives_preview_and_readback(rig):
    client, provider, *_ = rig
    client.cookies.set("echo_session", headers()["Authorization"].removeprefix("Bearer "))
    assert client.post(BASE, json={"path": "receipts"}).status_code == 200
    assert client.get(f"{BASE}/{PLAN_ID}/result").json() == provider.outcome
    assert [(method, actor, value) for method, actor, value, _ in provider.calls] == [
        ("create_plan", "local:admin", "receipts"),
        ("get_result", "local:admin", PLAN_ID),
    ]


def test_list_uses_authenticated_actor_and_exact_directory_without_mutating(rig):
    client, provider, *_ = rig
    response = client.get(BASE, headers=headers(), params={"path": "receipts/中文 2026"})
    assert response.status_code == 200
    assert response.json() == {
        "schema": "echo.files.organize.plans.v1",
        "path": "receipts/中文 2026",
        "complete": False,
        "plans": [],
    }
    assert [call[:3] for call in provider.calls] == [
        ("list_plans", "local:admin", "receipts/中文 2026")
    ]
    denied = client.get(BASE, headers=headers("local:other"), params={"path": "receipts"})
    assert denied.status_code == 403
    assert "plans" not in denied.json()


def test_list_validates_bounded_path_before_provider(rig):
    client, provider, *_ = rig
    assert client.get(BASE, headers=headers(), params={"path": "a" * 4097}).status_code == 422
    assert not provider.calls


def test_apply_consumes_a_single_use_token_and_preserves_partial_receipt(rig):
    client, provider, _, audit = rig
    token = issue(client)
    approved = {**headers(), "X-Echo-Approval": token}
    result = client.post(f"{BASE}/{PLAN_ID}/apply", json={}, headers=approved)
    assert result.status_code == 200
    assert result.json() == provider.outcome
    replay = client.post(f"{BASE}/{PLAN_ID}/apply", json={}, headers=approved)
    assert replay.status_code == 403
    assert [call[0] for call in provider.calls].count("apply") == 1
    assert any(event["payload"]["outcome"] == "consumed" for event in audit.recent(10))


@pytest.mark.parametrize(
    ("token_actor", "token_action", "token_plan"),
    [
        ("local:other", "files.organize.apply", PLAN_ID),
        ("local:admin", "files.organize.undo", PLAN_ID),
        ("local:admin", "files.organize.apply", UNDO_ID),
    ],
)
def test_token_binding_cannot_be_reused_for_different_actor_action_or_plan(
    rig, token_actor, token_action, token_plan
):
    client, provider, *_ = rig
    token = issue(client, actor=token_actor, action=token_action, plan_id=token_plan)
    response = client.post(
        f"{BASE}/{PLAN_ID}/apply", json={}, headers={**headers(), "X-Echo-Approval": token}
    )
    assert response.status_code == 403
    assert not any(call[0] == "apply" for call in provider.calls)


def test_undo_is_a_separate_plan_and_uses_its_actual_direction(rig):
    client, provider, *_ = rig
    preview = client.post(f"{BASE}/{PLAN_ID}/undo-plan", json={}, headers=headers())
    assert preview.json()["planId"] == UNDO_ID
    assert not any(call[0] == "apply" for call in provider.calls)
    token = issue(client, plan_id=UNDO_ID, action="files.organize.undo")
    response = client.post(
        f"{BASE}/{UNDO_ID}/apply", json={}, headers={**headers(), "X-Echo-Approval": token}
    )
    assert response.status_code == 200
    assert provider.calls[-1][:3] == ("apply", "local:admin", UNDO_ID)


def test_owner_denial_happens_before_token_consumption(rig):
    client, provider, approval, _ = rig
    token = issue(client, actor="local:other")
    response = client.post(
        f"{BASE}/{PLAN_ID}/apply",
        json={},
        headers={**headers("local:other"), "X-Echo-Approval": token},
    )
    assert response.status_code == 403
    approval.consume(
        token=token, actor="local:other", action="files.organize.apply", target=PLAN_ID
    )
    assert [call[0] for call in provider.calls] == ["get_plan"]


@pytest.mark.parametrize(
    "change",
    [
        {"planId": UNDO_ID},
        {"direction": "erase"},
        {"direction": []},
        {"requiresApproval": False},
        {"approval": {"action": "files.organize.undo", "target": PLAN_ID}},
        {"approval": {"action": "files.organize.apply", "target": UNDO_ID}},
    ],
)
def test_inconsistent_persisted_binding_is_rejected_without_applying(rig, change):
    client, provider, *_ = rig
    provider.plan.update(change)
    token = issue(client)
    response = client.post(
        f"{BASE}/{PLAN_ID}/apply", json={}, headers={**headers(), "X-Echo-Approval": token}
    )
    assert response.status_code == 409
    assert [call[0] for call in provider.calls] == ["get_plan"]


@pytest.mark.parametrize("status", [403, 404, 409, 503])
def test_provider_errors_remain_structured_failures(rig, status):
    client, provider, *_ = rig
    provider.failure = OrganizationError(status, "organization_test_error", "无法完成本次读取")
    response = client.get(f"{BASE}/{PLAN_ID}/result", headers=headers())
    assert response.status_code == status
    assert response.json() == {
        "detail": {"error": "organization_test_error", "message": "无法完成本次读取"}
    }


def test_cancel_uses_owner_and_does_not_report_reversal_of_committed_items(rig):
    client, provider, *_ = rig
    response = client.post(f"{BASE}/{PLAN_ID}/cancel", json={}, headers=headers())
    assert response.json()["state"] == "cancelled"
    assert response.json()["counts"]["moved"] == 1
    assert provider.calls[-1][:3] == ("cancel", "local:admin", PLAN_ID)


def test_missing_approval_service_is_explicitly_unavailable():
    provider = BoundedProvider()
    app = FastAPI()
    app.include_router(create_file_organization_router(provider, jwt_secret=SECRET))
    with TestClient(app) as client:
        response = client.post(f"{BASE}/{PLAN_ID}/apply", json={}, headers=headers())
    assert response.status_code == 503
    assert not any(call[0] == "apply" for call in provider.calls)


def test_blocking_provider_does_not_block_asgi_event_loop(rig):
    client, provider, *_ = rig
    entered, release = threading.Event(), threading.Event()
    original = provider.create_plan

    def blocking(actor, path):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test provider was not released")
        return original(actor, path)

    provider.create_plan = blocking

    @client.app.get("/ping")
    async def ping():
        return {"ok": True}

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="http://test"
        ) as http:
            request = asyncio.create_task(
                http.post(BASE, headers=headers(), json={"path": "receipts"})
            )
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                response = await asyncio.wait_for(http.get("/ping"), timeout=1)
                assert response.json() == {"ok": True}
                assert not request.done()
            finally:
                release.set()
                await request
            assert provider.calls[-1][3] != threading.get_ident()

    asyncio.run(scenario())


def test_real_service_http_preview_approval_move_readback_reopen_and_undo(tmp_path):
    """No extractor/IO/provider replacement: synthetic UTF-8 invoice bytes only."""
    from appliance.files import FileManager, create_files_router
    from appliance.files.organization import FileOrganizationService
    from runtime.platform.process.task_supervisor import TaskSupervisor

    nas = tmp_path / "nas"
    source_dir = nas / "receipts"
    source_dir.mkdir(parents=True)
    original = "电子发票\n开票日期：2026年09月05日\n价税合计：CNY 128.50\n".encode()
    (source_dir / "invoice.txt").write_bytes(original)
    (source_dir / "unconfirmed.txt").write_bytes(b"Document without an invoice date")
    pending = source_dir / ".echo-organize-existing.pending"
    pending.write_bytes(b"private unrelated prepared file")
    state = tmp_path / "state"
    state.mkdir()
    manager = FileManager(nas)
    audit = ApplianceAudit.from_data_dir(state, jwt_secret=SECRET)
    auth = ApplianceAuthenticator(SECRET)
    approval = HighRiskApprovalService(
        password_hash=hashlib.sha256(PASSWORD.encode()).hexdigest(), jwt_secret=SECRET, audit=audit
    )
    supervisor = TaskSupervisor.from_path(state / "task-runs.json", holder_id="synthetic-http")

    def application():
        service = FileOrganizationService(
            manager, state, supervisor=supervisor, audit=audit, auth_required=True
        )
        app = FastAPI()
        app.include_router(create_approval_router(approval, authenticator=auth))
        app.include_router(create_files_router(manager, authenticator=auth))
        app.include_router(
            create_file_organization_router(service, authenticator=auth, approval=approval)
        )
        return app

    with TestClient(application()) as client:
        response = client.post(BASE, headers=headers(), json={"path": "receipts"})
        assert response.status_code == 200, response.text
        plan = response.json()
        actual_id = plan["planId"]
        assert plan["ready"] and plan["scanComplete"]
        assert plan["summary"]["ready"] == 1
        assert plan["summary"]["needsReview"] == 1
        assert plan["summary"]["scanned"] == 2
        assert not any(".echo-organize-" in row["source"] for row in plan["entries"])
        assert (source_dir / "invoice.txt").read_bytes() == original
        discovered = client.get(BASE, headers=headers(), params={"path": "receipts"})
        assert discovered.status_code == 200, discovered.text
        assert discovered.json()["complete"] is True
        assert [item["planId"] for item in discovered.json()["plans"]] == [actual_id]
        assert discovered.json()["plans"][0]["state"] is None
        assert "entries" not in discovered.json()["plans"][0]
        assert "snapshots" not in discovered.text and str(nas) not in discovered.text
        other = client.get(BASE, headers=headers("local:other"), params={"path": "receipts"})
        assert other.status_code == 200 and other.json()["plans"] == []
        assert client.get(f"{BASE}/{actual_id}/result", headers=headers()).status_code == 404
        assert (
            client.post(f"{BASE}/{actual_id}/apply", headers=headers(), json={}).status_code == 403
        )
        token = issue(client, plan_id=actual_id)
        response = client.post(
            f"{BASE}/{actual_id}/apply", json={}, headers={**headers(), "X-Echo-Approval": token}
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["state"] == "completed", result
        assert result["counts"]["moved"] == 1 and result["reviewCount"] == 1
        actual_path = result["results"][0]["actualPath"]
        assert actual_path == "receipts/2026/09/invoice.txt"
        assert not (source_dir / "invoice.txt").exists()
        assert (nas / actual_path).read_bytes() == original
        downloaded = client.get(
            "/api/appliance/files/download", headers=headers(), params={"path": actual_path}
        )
        assert downloaded.content == original
        assert hashlib.sha256(downloaded.content).hexdigest() == result["results"][0]["sha256"]
        assert supervisor.store.get(result["taskId"]).owner_id == "local:admin"
        assert str(supervisor.store.get(result["taskId"]).status) == "completed"

    # Reconstruct the real provider; the JSON receipt is the authority, not UI state.
    with TestClient(application()) as reopened:
        discovered = reopened.get(BASE, headers=headers(), params={"path": "receipts"})
        assert discovered.json()["plans"][0]["planId"] == actual_id
        assert discovered.json()["plans"][0]["state"] == "completed"
        restored = reopened.get(f"{BASE}/{actual_id}", headers=headers())
        assert restored.status_code == 200
        assert restored.json()["result"]["results"][0]["actualPath"] == actual_path
        assert (
            reopened.get(f"{BASE}/{actual_id}", headers=headers("local:other")).status_code == 403
        )
        undo = reopened.post(f"{BASE}/{actual_id}/undo-plan", headers=headers(), json={})
        assert undo.status_code == 200, undo.text
        undo_plan = undo.json()
        assert undo_plan["direction"] == "undo" and undo_plan["sourcePlanId"] == actual_id
        wrong = issue(reopened, plan_id=undo_plan["planId"])
        assert (
            reopened.post(
                f"{BASE}/{undo_plan['planId']}/apply",
                json={},
                headers={**headers(), "X-Echo-Approval": wrong},
            ).status_code
            == 403
        )
        token = issue(reopened, plan_id=undo_plan["planId"], action="files.organize.undo")
        undone = reopened.post(
            f"{BASE}/{undo_plan['planId']}/apply",
            json={},
            headers={**headers(), "X-Echo-Approval": token},
        )
        assert undone.status_code == 200, undone.text
        assert undone.json()["state"] == "completed", undone.json()
        assert undone.json()["results"][0]["actualPath"] == "receipts/invoice.txt"
    assert (source_dir / "invoice.txt").read_bytes() == original
    assert not (nas / actual_path).exists()
    assert (source_dir / "unconfirmed.txt").read_bytes() == b"Document without an invoice date"
    assert pending.read_bytes() == b"private unrelated prepared file"


@pytest.fixture
def original_rig(tmp_path):
    from appliance.data_access import DataAccessScope, DataPathRule
    from appliance.files.manager import FileManager
    from appliance.files.organization import FileOrganizationService
    from runtime.platform.process.task_supervisor import TaskSupervisor

    root = tmp_path / "nas"
    folder = root / "receipts"
    folder.mkdir(parents=True)
    name = "发票 09.txt"
    data = b"Invoice\r\nInvoice Date: 2026-09-05\r\nTotal: USD 12.50\r\n"
    (folder / name).write_bytes(data)

    class Policy:
        permission = "readWrite"

        def scope_for_actor(self, actor):
            return DataAccessScope(
                actor, False, (DataPathRule(("receipts",), self.permission),), root
            )

    policy = Policy()
    state = tmp_path / "state"
    state.mkdir()
    audit = ApplianceAudit.from_data_dir(state, jwt_secret=SECRET)
    approval = HighRiskApprovalService(
        password_hash=hashlib.sha256(PASSWORD.encode()).hexdigest(), jwt_secret=SECRET, audit=audit
    )
    tasks = TaskSupervisor.from_path(state / "tasks.json", holder_id="original-http")
    service = FileOrganizationService(
        FileManager(root),
        state,
        data_access=policy,
        supervisor=tasks,
        audit=audit,
        auth_required=True,
    )
    auth = ApplianceAuthenticator(SECRET)
    app = FastAPI()
    app.include_router(create_approval_router(approval, authenticator=auth))
    app.include_router(
        create_file_organization_router(service, authenticator=auth, approval=approval)
    )
    with TestClient(app) as client:
        plan = client.post(BASE, headers=headers(), json={"path": "receipts"}).json()
        yield client, service, policy, plan, name, data


def _original_url(plan):
    return f"{BASE}/{plan['planId']}/entries/{plan['entries'][0]['entryId']}/original"


def _apply_original(client, plan):
    token = issue(client, plan_id=plan["planId"], action=plan["approval"]["action"])
    result = client.post(
        f"{BASE}/{plan['planId']}/apply", json={}, headers={**headers(), "X-Echo-Approval": token}
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_original_endpoint_returns_verified_bytes_headers_and_follows_undo(original_rig):
    client, _, _, plan, name, data = original_rig
    url = _original_url(plan)
    assert client.get(url).status_code == 401
    assert client.get(url, headers=headers()).status_code == 409
    _apply_original(client, plan)
    downloaded = client.get(url, headers=headers())
    assert downloaded.status_code == 200
    assert downloaded.content == data
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert downloaded.headers["content-length"] == str(len(data))
    assert downloaded.headers["content-disposition"] == (
        f"attachment; filename=\"original\"; filename*=UTF-8''{quote(name, safe='')}"
    )
    assert downloaded.headers["cache-control"] == "private, no-store"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["x-content-sha256"] == hashlib.sha256(data).hexdigest()
    undo = client.post(f"{BASE}/{plan['planId']}/undo-plan", headers=headers(), json={}).json()
    _apply_original(client, undo)
    assert client.get(url, headers=headers()).content == data
    undo_download = client.get(_original_url(undo), headers=headers())
    assert undo_download.status_code == 200 and undo_download.content == data


def test_original_endpoint_checks_owner_and_live_scope_and_never_uses_old_result(original_rig):
    client, service, policy, plan, _, data = original_rig
    result = _apply_original(client, plan)
    url = _original_url(plan)
    assert client.get(url, headers=headers("local:other")).status_code == 403
    policy.permission = "none"
    assert client.get(url, headers=headers()).status_code == 403
    policy.permission = "read"
    observed = client.get(f"{BASE}/{plan['planId']}/result", headers=headers())
    assert observed.status_code == 200 and observed.json()["results"][0]["actualPath"]
    target = service.manager.root / result["results"][0]["actualPath"]
    target.write_bytes(b"x" * len(data))
    rejected = client.get(url, headers=headers())
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["error"] == "original_unavailable"
    assert "x" * len(data) not in rejected.text


def test_original_endpoint_sends_captured_bytes_even_if_name_changes_after_verified_read(
    original_rig, monkeypatch
):
    client, service, _, plan, _, data = original_rig
    result = _apply_original(client, plan)
    target = service.manager.root / result["results"][0]["actualPath"]
    read = service.read_original

    def change_after_verified_read(*args):
        item = read(*args)
        target.write_bytes(b"later unrelated replacement")
        return item

    monkeypatch.setattr(service, "read_original", change_after_verified_read)
    response = client.get(_original_url(plan), headers=headers())
    assert response.status_code == 200 and response.content == data
    assert target.read_bytes() == b"later unrelated replacement"
    assert response.headers["x-content-sha256"] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("entry_id", ["a" * 23, "a" * 25, "A" * 24, "a" * 63, "a" * 65])
def test_original_endpoint_rejects_invalid_entry_identifiers(original_rig, entry_id):
    client, _, _, plan, *_ = original_rig
    url = f"{BASE}/{plan['planId']}/entries/{entry_id}/original"
    assert client.get(url, headers=headers()).status_code == 422


def test_original_endpoint_cannot_accept_an_alternate_path_or_body(original_rig):
    client, _, _, plan, *_ = original_rig
    _apply_original(client, plan)
    url = _original_url(plan)
    assert client.get(url, headers=headers(), params={"path": "../../elsewhere"}).status_code == 422
    assert (
        client.request("GET", url, headers=headers(), json={"path": "elsewhere"}).status_code == 422
    )
    assert client.post(url, headers=headers(), json={}).status_code == 405
