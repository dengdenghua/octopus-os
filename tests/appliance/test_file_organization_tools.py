"""Real JWT, ToolExecutor and file service; all files and approvals are synthetic."""

from __future__ import annotations

import contextvars
import hashlib
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.agent_authorization import (
    ApplianceAgentAuthorizationMiddleware,
    require_appliance_actor,
)
from appliance.approval import HighRiskApprovalService, create_approval_router
from appliance.audit import ApplianceAudit
from appliance.data_access import DataAccessScope, DataAccessUnavailable, DataPathRule
from appliance.file_organization_tools import register_file_organization_tools
from appliance.files.manager import FileManager
from appliance.files.organization import FileOrganizationService
from appliance.files.organization_router import create_file_organization_router
from appliance.security import ApplianceAuthenticator
from runtime.execution.suckers.registry import SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
from runtime.platform.process.session import Session, session_scope
from runtime.platform.process.task_supervisor import TaskSupervisor
from runtime.safety.auth import TrustEngine
from runtime.safety.auth.identity import encode_jwt_hs256

SECRET = "synthetic-organization-agent-tool-jwt-key-only"
PASSWORD = "synthetic-approval-password"
BASE = "/api/appliance/files/organize/plans"
INVOICE = b"Invoice\r\nInvoice Date: 2026-09-05\r\nTotal: USD 12.50\r\n"


class Security:
    def __init__(self):
        self.floor = 0
        self.active = {"local:alice", "local:bob"}

    def claims_are_current(self, claims):
        return claims.get("sub") in self.active and claims.get("iat", -1) >= self.floor


class Policy:
    def __init__(self, root):
        self.root = root
        self.available = True
        self.permission = "readWrite"

    def scope_for_actor(self, actor):
        if not self.available:
            raise DataAccessUnavailable("secret native storage credentials")
        return DataAccessScope(
            actor,
            False,
            (DataPathRule(("Invoices",), self.permission),),
            self.root,
        )


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "runtime-data"))
    root, state = tmp_path / "nas", tmp_path / "state"
    (root / "Invoices").mkdir(parents=True)
    state.mkdir()
    source = root / "Invoices/one.txt"
    source.write_bytes(INVOICE)
    policy, security = Policy(root), Security()
    supervisor = TaskSupervisor.from_path(state / "tasks.json", holder_id="synthetic-tool-provider")
    audit = ApplianceAudit.from_data_dir(state, jwt_secret=SECRET)
    service = FileOrganizationService(
        FileManager(root),
        state,
        data_access=policy,
        supervisor=supervisor,
        audit=audit,
        auth_required=True,
    )
    registry, journal = SkillRegistry(), InMemoryJournal()
    bridge = register_file_organization_tools(registry, service, security=security)
    executor = ToolExecutor(
        registry,
        TrustEngine(trusted_sources=["builtin://appliance/*"]),
        journal,
        effect_store_path=tmp_path / "tool-effects.sqlite3",
    )
    auth = ApplianceAuthenticator(SECRET)
    approval = HighRiskApprovalService(
        password_hash=hashlib.sha256(PASSWORD.encode()).hexdigest(),
        jwt_secret=SECRET,
        audit=audit,
    )
    app = FastAPI()
    app.state.echo_appliance_account_security = security
    app.include_router(create_approval_router(approval, authenticator=auth))
    app.include_router(
        create_file_organization_router(service, authenticator=auth, approval=approval)
    )
    app.add_middleware(
        ApplianceAgentAuthorizationMiddleware,
        authenticator=auth,
        account_security=security,
    )
    task_id = TaskId(uuid4())
    captured = []

    @app.post("/test/agent/{tool}")
    def invoke(tool: str, arguments: dict):
        actor = require_appliance_actor()
        with session_scope(
            Session(actor=actor, metadata={"tenant_id": "synthetic", "owner_actor_id": actor})
        ):
            captured.append(contextvars.copy_context())
            step = executor.execute_step(
                0,
                "organize",
                SkillId(tool),
                arguments,
                caller="react_loop",
                task_id=task_id,
                arm_id=ArmId("organize"),
                actor=actor,
                budget=Budget(task_id=task_id, limits=BudgetLimits(tokens=10000, usd=1.0)),
            )
        return {
            "status": step.result.status,
            "output": step.result.output,
            "stderr_tags": step.result.stderr_tags,
        }

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            service=service,
            policy=policy,
            security=security,
            registry=registry,
            journal=journal,
            bridge=bridge,
            executor=executor,
            task_id=task_id,
            captured=captured,
            source=source,
            root=root,
            supervisor=supervisor,
        )


def login(rig, actor="local:alice"):
    token = encode_jwt_hs256(
        {"sub": actor, "iat": int(time.time()), "exp": int(time.time()) + 3600},
        secret=SECRET,
    )
    rig.client.cookies.set("echo_session", token)


def invoke(rig, tool, arguments):
    response = rig.client.post(f"/test/agent/{tool}", json=arguments)
    assert response.status_code == 200, response.text
    return response.json()


def preview(rig):
    return invoke(rig, "files_organize_plan", {"path": "Invoices"})["output"]


def approve_apply(rig, plan):
    issued = rig.client.post(
        "/api/appliance/approvals",
        json={
            "action": plan["approval"]["action"],
            "target": plan["planId"],
            "password": PASSWORD,
        },
    )
    assert issued.status_code == 200, issued.text
    response = rig.client.post(
        f"{BASE}/{plan['planId']}/apply",
        json={},
        headers={"X-Echo-Approval": issued.json()["approvalToken"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_agent_preview_is_a_real_persisted_plan_and_never_moves_originals(rig):
    login(rig)
    before = rig.source.read_bytes(), rig.source.stat().st_mtime_ns
    result = preview(rig)
    assert result["ok"] is True
    assert result["schema"] == "echo.files.organize.plan.v1"
    assert result["approval"] == {"action": "files.organize.apply", "target": result["planId"]}
    assert result["path"] == "Invoices"
    assert result["entries"][0]["target"] == "Invoices/2026/09/one.txt"
    assert "result" not in result
    assert "尚未移动" in result["message"]
    assert (rig.source.read_bytes(), rig.source.stat().st_mtime_ns) == before
    assert not (rig.root / "Invoices/2026").exists()
    public = rig.client.get(f"{BASE}/{result['planId']}").json()
    assert public["entries"] == result["entries"]
    assert not any(key in result for key in ("snapshots", "workspacePath", "owner", "nonce"))
    assert str(rig.root) not in str(result)
    unapproved = rig.client.post(f"{BASE}/{result['planId']}/apply", json={})
    assert unapproved.status_code == 403
    assert rig.source.read_bytes() == INVOICE


def test_status_refreshes_after_independent_http_approval_and_real_task_execution(rig):
    login(rig)
    plan = preview(rig)
    arguments = {"plan_id": plan["planId"]}
    first = invoke(rig, "files_organize_status", arguments)
    assert "result" not in first["output"]
    executed = approve_apply(rig, plan)
    assert executed["state"] == "completed"
    second = invoke(rig, "files_organize_status", arguments)
    result = second["output"]["result"]
    assert result["state"] == "completed" and result["executionComplete"] is True
    assert result["counts"]["moved"] == 1
    assert result["results"][0]["actualPath"] == "Invoices/2026/09/one.txt"
    assert str(rig.supervisor.store.get(result["taskId"]).status) == "completed"
    assert (rig.root / "Invoices/2026/09/one.txt").read_bytes() == INVOICE
    assert not rig.source.exists()
    assert "durable_effect_replay" not in second["stderr_tags"]


def test_read_only_share_preview_cannot_be_mistaken_for_an_executable_plan(rig):
    login(rig)
    rig.policy.permission = "read"
    plan = preview(rig)
    assert plan["ok"] is True and plan["ready"] is False
    assert plan["entries"][0]["reason"] == "write_access_denied"
    assert "当前不能执行" in plan["message"]
    assert "result" not in plan
    assert rig.source.read_bytes() == INVOICE


def test_unknown_document_is_preserved_for_review_in_agent_preview(rig):
    login(rig)
    rig.source.write_bytes(b"Meeting notes with an unrelated date: 2026-09-05")
    original = rig.source.read_bytes()
    plan = preview(rig)
    assert plan["ok"] is True and plan["ready"] is False
    assert plan["entries"][0]["status"] == "needs_review"
    assert plan["entries"][0]["date"] is None
    assert "当前不能执行" in plan["message"]
    assert rig.source.read_bytes() == original


def test_partial_and_later_changed_results_are_reported_without_claiming_completion(rig):
    login(rig)
    other = rig.root / "Invoices/two.txt"
    other.write_bytes(INVOICE)
    plan = preview(rig)
    other.write_bytes(b"independent edit")
    executed = approve_apply(rig, plan)
    assert executed["state"] == "partial"
    result = invoke(rig, "files_organize_status", {"plan_id": plan["planId"]})["output"]["result"]
    assert result["state"] == "partial"
    assert result["executionComplete"] is False
    assert result["counts"]["moved"] == 1 and result["counts"]["conflicts"] == 1
    (rig.root / "Invoices/2026/09/one.txt").write_bytes(b"later external edit")
    reread = invoke(rig, "files_organize_status", {"plan_id": plan["planId"]})["output"]["result"]
    assert reread["state"] == "uncertain"
    assert reread["executionComplete"] is False
    assert reread["results"][0]["reason"] == "committed_file_changed"


def test_tools_require_private_grant_even_if_runtime_session_names_an_actor(rig):
    with session_scope(Session(actor="local:alice", metadata={"owner_actor_id": "local:alice"})):
        result = rig.bridge.plan(path="Invoices")
    assert result["code"] == "organization_access_denied"
    assert rig.source.read_bytes() == INVOICE
    with pytest.raises(ValueError):
        register_file_organization_tools(SkillRegistry(), rig.service, security=None)


def test_captured_grant_cannot_authorize_another_device_service(rig):
    login(rig)
    plan = preview(rig)
    other = register_file_organization_tools(SkillRegistry(), rig.service, security=Security())
    result = rig.captured[-1].run(other.status, plan_id=plan["planId"])
    assert result["code"] == "organization_access_denied"
    assert "entries" not in result


def test_another_actor_cannot_read_the_owners_plan(rig):
    login(rig)
    plan = preview(rig)
    login(rig, "local:bob")
    result = invoke(rig, "files_organize_status", {"plan_id": plan["planId"]})["output"]
    assert result["code"] == "organization_access_denied"
    assert "entries" not in result


def test_duplicate_plan_step_reads_new_files_instead_of_replaying_old_preview(rig):
    login(rig)
    first = preview(rig)
    (rig.root / "Invoices/two.txt").write_bytes(INVOICE)
    second = preview(rig)
    assert first["summary"]["scanned"] == 1
    assert second["summary"]["scanned"] == 2
    assert second["planId"] != first["planId"]
    assert all(
        rig.registry.get(name).replay_policy == "refresh_read"
        for name in (
            "files_organize_plan",
            "files_organize_status",
        )
    )
    assert rig.registry.get("files_organize_plan").path_resolution == "service"


def test_repeated_status_cannot_replay_content_after_scope_revocation(rig):
    login(rig)
    plan = preview(rig)
    arguments = {"plan_id": plan["planId"]}
    assert invoke(rig, "files_organize_status", arguments)["output"]["entries"]
    rig.policy.permission = "none"
    repeated = invoke(rig, "files_organize_status", arguments)
    assert repeated["output"]["code"] == "organization_access_denied"
    assert "entries" not in repeated["output"]
    assert "durable_effect_replay" not in repeated["stderr_tags"]


def test_captured_repeated_step_revalidates_revoked_device_grant(rig):
    login(rig)
    plan = preview(rig)
    arguments = {"plan_id": plan["planId"]}
    invoke(rig, "files_organize_status", arguments)
    rig.security.floor = int(time.time()) + 2

    def repeat():
        return rig.executor.execute_step(
            0,
            "organize",
            SkillId("files_organize_status"),
            arguments,
            caller="react_loop",
            task_id=rig.task_id,
            arm_id=ArmId("organize"),
            actor="local:alice",
            budget=Budget(task_id=rig.task_id, limits=BudgetLimits(tokens=10000, usd=1.0)),
        )

    repeated = rig.captured[-1].run(repeat)
    assert repeated.result.output["code"] == "organization_access_denied"
    assert "entries" not in repeated.result.output
    assert "durable_effect_replay" not in repeated.result.stderr_tags


@pytest.mark.parametrize("change", ["scope", "grant"])
def test_permission_or_grant_change_before_return_discards_the_plan(rig, monkeypatch, change):
    login(rig)
    original = rig.service.create_plan

    def changed(actor, path):
        result = original(actor, path)
        if change == "scope":
            rig.policy.permission = "read"
        else:
            rig.security.floor = int(time.time()) + 2
        return result

    monkeypatch.setattr(rig.service, "create_plan", changed)
    result = preview(rig)
    assert result["code"] in {"organization_access_changed", "organization_access_denied"}
    assert "entries" not in result
    assert rig.source.read_bytes() == INVOICE


def test_permission_probe_failure_does_not_expose_private_diagnostics(rig):
    login(rig)
    rig.policy.available = False
    result = preview(rig)
    assert result["code"] == "organization_access_unavailable"
    assert "secret" not in str(result)


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "/Invoices"},
        {"path": "../Invoices"},
        {"path": "Invoices\\nested"},
        {"path": None},
        {"path": "x" * 4097},
        {"path": ".echo-trash"},
    ],
)
def test_invalid_paths_never_create_a_plan(rig, arguments):
    login(rig)
    before = set(rig.service.store.directory.iterdir())
    result = invoke(rig, "files_organize_plan", arguments)["output"]
    assert result["code"] == "invalid_argument"
    assert set(rig.service.store.directory.iterdir()) == before


@pytest.mark.parametrize(
    "extra",
    [
        {"actor": "local:bob"},
        {"hostpath": "C:/private"},
        {"db_path": "elsewhere"},
        {"path_resolution": "workspace"},
        {"approval_token": "forged"},
        {"apply": True},
        {"password": PASSWORD},
    ],
)
def test_tool_arguments_cannot_supply_identity_credentials_or_execution_authorization(rig, extra):
    login(rig)
    result = invoke(rig, "files_organize_plan", {"path": "Invoices", **extra})
    assert result["status"] != "success"
    assert rig.source.read_bytes() == INVOICE
    assert not (rig.root / "Invoices/2026").exists()


@pytest.mark.parametrize("identifier", ["", "../plan", "A" * 64, "a" * 63, None])
def test_invalid_plan_identifier_is_not_used_as_a_store_path(rig, identifier):
    login(rig)
    result = invoke(rig, "files_organize_status", {"plan_id": identifier})["output"]
    assert result["code"] == "invalid_argument"
