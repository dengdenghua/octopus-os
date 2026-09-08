"""Real JWT -> runtime tool -> shared photo SQLite, with member filtering."""

from __future__ import annotations

import asyncio
import contextvars
import json
import sqlite3
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from appliance.agent_authorization import ApplianceAgentAuthorizationMiddleware
from appliance.data_access import DataAccessScope, DataAccessUnavailable, DataPathRule
from appliance.photo_tools import register_photo_tools
from appliance.photos import PhotoLibraryService, create_photos_router
from appliance.security import ApplianceAuthenticator
from runtime.execution.codex_backend.dynamic_tools import CodexDynamicToolBroker
from runtime.execution.codex_backend.types import ApprovalRequest
from runtime.execution.host_boundary import create_host_execution_boundary
from runtime.execution.suckers.registry import SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.hemolymph import image_semantic_index as index
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
from runtime.platform.process.session import Session, session_scope
from runtime.safety.approval.approval_gate import AutoDenyProvider
from runtime.safety.auth import TrustEngine
from runtime.safety.auth.identity import encode_jwt_hs256

SECRET = "only-a-synthetic-photo-tool-integration-test-secret"


class _Security:
    floor = 0
    active = {"local:alice", "local:bob", "local:admin"}

    def claims_are_current(self, claims):
        return claims.get("sub") in self.active and claims.get("iat", -1) >= self.floor


class _Policy:
    def __init__(self, root):
        self.root = root
        self.available = True
        self.revoked = False

    def scope_for_actor(self, actor):
        if not self.available:
            raise DataAccessUnavailable("private native storage diagnostic")
        if actor == "local:admin":
            return DataAccessScope.unrestricted(actor)
        return DataAccessScope(
            actor=actor,
            operator=False,
            root=self.root,
            rules=()
            if self.revoked
            else (
                DataPathRule(("Family",), "read"),
                DataPathRule(("Family", "Private"), "none"),
            )
            if actor == "local:alice"
            else (DataPathRule(("Bob",), "read"),),
        )


class _Model:
    def embed(self, values):
        return [[1.0, 0.0] for _ in values]


class _Backend:
    on_search = None

    def available(self):
        return True

    def search_by_text(self, query, *, top_k, db_path):
        rows = index.search_by_text(query, top_k=top_k, db_path=db_path)
        if self.on_search:
            self.on_search()
        return rows

    def search_by_text_in_paths(self, query, *, allowed_paths, top_k, db_path):
        rows = index.search_by_text(
            query, top_k=top_k, db_path=db_path, allowed_paths=allowed_paths
        )
        if self.on_search:
            self.on_search()
        return rows


@pytest.fixture
def household(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "auto")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "runtime-data"))
    monkeypatch.setattr(index, "_image_model", lambda: _Model())
    monkeypatch.setattr(index, "_text_model", lambda: _Model())
    root = tmp_path / "nas"
    for relative in ("Family/beach.jpg", "Family/Private/hidden.jpg", "Bob/private.jpg"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8)).save(path)
    backend = _Backend()
    service = PhotoLibraryService(root, tmp_path / "state", backend=backend)
    assert index.build_index(root, db_path=service.db_path, include_faces=False)["indexed"] == 3
    policy, security = _Policy(root), _Security()
    registry, journal = SkillRegistry(), InMemoryJournal()
    bridge = register_photo_tools(registry, service, policy, account_security=security)
    executor = ToolExecutor(
        registry,
        TrustEngine(trusted_sources=["builtin://appliance/*"]),
        journal,
        effect_store_path=tmp_path / "effects.sqlite3",
    )
    app = FastAPI()
    app.state.echo_appliance_account_security = security
    auth = ApplianceAuthenticator(SECRET)
    app.include_router(create_photos_router(service, authenticator=auth, data_access=policy))
    app.add_middleware(
        ApplianceAgentAuthorizationMiddleware,
        authenticator=auth,
        account_security=security,
    )
    task_id = TaskId(uuid4())
    captured_contexts = []

    @app.post("/test/agent/{tool}")
    def invoke(tool: str, arguments: dict):
        # Exercise the actual tool executor and its journal with a trusted
        # runtime Session, not a direct callback or model-provided identity.
        from appliance.agent_authorization import require_appliance_actor

        actor = require_appliance_actor()
        budget = Budget(task_id=task_id, limits=BudgetLimits(tokens=10_000, usd=1.0))
        with session_scope(
            Session(
                actor=actor,
                metadata={"tenant_id": "household", "owner_actor_id": actor},
            )
        ):
            captured_contexts.append(contextvars.copy_context())
            step = executor.execute_step(
                0,
                "photos",
                SkillId(tool),
                arguments,
                caller="react_loop",
                task_id=budget.task_id,
                arm_id=ArmId("photos"),
                budget=budget,
                actor=actor,
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
            bridge=bridge,
            registry=registry,
            policy=policy,
            security=security,
            backend=backend,
            journal=journal,
            executor=executor,
            task_id=task_id,
            captured_contexts=captured_contexts,
        )


def _login(client, actor="local:alice"):
    token = encode_jwt_hs256(
        {"sub": actor, "iat": int(time.time()), "exp": int(time.time()) + 3600},
        secret=SECRET,
    )
    client.cookies.set("echo_session", token)


def test_search_and_browse_use_the_desktop_database_and_member_scope(household):
    _login(household.client)
    desktop = household.client.post("/api/appliance/photos/search", json={"query": "family"}).json()
    agent = household.client.post("/test/agent/photos_search", json={"query": "family"}).json()
    assert agent["status"] == "success"
    assert agent["output"]["mode"] == desktop["mode"] == "semantic"
    assert agent["output"]["items"] == desktop["items"]
    assert [item["path"] for item in desktop["items"]] == ["Family/beach.jpg"]
    assert agent["output"]["source"] == "appliance.photos"
    assert agent["output"]["resourceSource"]["id"] == desktop["source"]["id"]
    library = household.client.post("/test/agent/photos_library", json={}).json()["output"]
    assert library["total"] == 1
    assert [item["path"] for item in library["items"]] == ["Family/beach.jpg"]
    assert len(household.journal) > 0


def test_host_execution_envelope_is_server_owned_and_matches_photo_scope(household, monkeypatch):
    monkeypatch.setattr(
        "appliance.photo_tools.require_appliance_actor",
        lambda **_kwargs: "local:alice",
    )
    boundary = create_host_execution_boundary(
        task_id="photo-task-01",
        thread_id="photo-thread-01",
        actor_id="local:alice",
        tenant_id="household",
        goal="Find my family photos",
        timeout_s=30,
    )
    with session_scope(boundary.session):
        result = household.bridge.library(limit=1)

    assert result["execution"] == {
        "taskId": "photo-task-01",
        "intentId": "photo-task-01",
        "threadId": "photo-thread-01",
    }
    assert result["source"] == "appliance.photos"
    assert result["resourceSource"]["kind"] == "photo-library"


def test_host_execution_identity_mismatch_fails_closed(household, monkeypatch):
    monkeypatch.setattr(
        "appliance.photo_tools.require_appliance_actor",
        lambda **_kwargs: "local:alice",
    )
    boundary = create_host_execution_boundary(
        task_id="photo-task-02",
        thread_id="photo-thread-02",
        actor_id="local:bob",
        tenant_id="household",
        goal="Find private photos",
        timeout_s=30,
    )
    with session_scope(boundary.session):
        result = household.bridge.library(limit=1)

    assert result == {
        "ok": False,
        "code": "photo_execution_identity_mismatch",
        "error": "照片任务身份已变化，请重新发起查询。",
        "retryable": True,
    }


def test_principals_do_not_leak_between_requests_or_background_calls(household):
    _login(household.client, "local:bob")
    bob = household.client.post("/test/agent/photos_search", json={"query": "family"}).json()[
        "output"
    ]
    assert [item["path"] for item in bob["items"]] == ["Bob/private.jpg"]
    _login(household.client, "local:alice")
    alice = household.client.post("/test/agent/photos_library", json={}).json()["output"]
    assert [item["path"] for item in alice["items"]] == ["Family/beach.jpg"]
    assert household.bridge.library()["code"] == "photo_access_denied"


def test_member_cannot_plan_a_whole_library_index_or_forge_an_actor(household):
    _login(household.client)
    plan = household.client.post("/test/agent/photos_index_plan", json={}).json()
    assert plan["output"]["code"] == "photo_access_denied"
    forged = household.client.post(
        "/test/agent/photos_library",
        json={"actor": "local:admin"},
    ).json()
    assert forged["status"] != "success"
    _login(household.client, "local:admin")
    allowed = household.client.post("/test/agent/photos_index_plan", json={}).json()["output"]
    assert allowed["requiresApproval"] is True
    assert allowed["imageCount"] == 3
    assert household.service.status()["job"]["state"] == "idle"


def test_revoked_grant_cannot_publish_a_search_result(household):
    _login(household.client)
    household.backend.on_search = lambda: setattr(household.security, "floor", int(time.time()) + 2)
    result = household.client.post("/test/agent/photos_search", json={"query": "family"}).json()
    assert result["output"]["code"] == "photo_access_denied"
    assert "items" not in result["output"]


def test_permission_changes_during_search_discard_the_old_projection(household):
    _login(household.client)
    household.backend.on_search = lambda: setattr(household.policy, "revoked", True)
    result = household.client.post("/test/agent/photos_search", json={"query": "family"}).json()
    assert result["output"]["code"] == "photo_access_changed"
    assert "items" not in result["output"]


def test_unavailable_permissions_fail_without_exposing_internal_diagnostics(household):
    _login(household.client)
    household.policy.available = False
    result = household.client.post("/test/agent/photos_library", json={}).json()["output"]
    assert result["code"] == "photo_access_unavailable"
    assert "private" not in str(result)


def test_search_reports_filename_fallback_without_claiming_semantics(household, monkeypatch):
    _login(household.client)
    monkeypatch.setattr(index, "_text_model", lambda: None)
    result = household.client.post("/test/agent/photos_search", json={"query": "beach"}).json()[
        "output"
    ]
    assert result["mode"] == "filename"
    assert [item["path"] for item in result["items"]] == ["Family/beach.jpg"]


@pytest.mark.parametrize("arguments", [{"limit": True}, {"offset": -1}, {"limit": 501}])
def test_invalid_pagination_never_reaches_the_photo_service(household, arguments):
    _login(household.client)
    result = household.client.post("/test/agent/photos_library", json=arguments).json()["output"]
    assert result["code"] == "invalid_argument"


def _repeat_captured_step(household, tool, arguments):
    def execute():
        return household.executor.execute_step(
            0,
            "photos",
            SkillId(tool),
            arguments,
            caller="react_loop",
            task_id=household.task_id,
            arm_id=ArmId("photos"),
            budget=Budget(task_id=household.task_id, limits=BudgetLimits(tokens=10_000, usd=1.0)),
            actor="local:alice",
        )

    return household.captured_contexts[-1].run(execute)


def test_same_react_step_refreshes_result_after_permissions_narrow(household):
    _login(household.client)
    first = household.client.post("/test/agent/photos_library", json={}).json()
    assert first["output"]["total"] == 1
    household.policy.revoked = True
    second = household.client.post("/test/agent/photos_library", json={}).json()
    assert second["output"]["total"] == 0
    assert second["output"]["items"] == []
    assert "durable_effect_replay" not in second["stderr_tags"]


@pytest.mark.parametrize("expiration", [False, True])
def test_same_react_step_cannot_replay_photos_after_grant_revocation_or_expiry(
    household, monkeypatch, expiration
):
    _login(household.client)
    arguments = {"query": "family"}
    first = household.client.post("/test/agent/photos_search", json=arguments).json()
    assert first["output"]["items"]
    if expiration:
        later = time.time() + 7200
        monkeypatch.setattr("appliance.agent_authorization.time.time", lambda: later)
    else:
        household.security.floor = int(time.time()) + 2
    repeated = _repeat_captured_step(household, "photos_search", arguments)
    assert repeated.result.output["code"] == "photo_access_denied"
    assert "items" not in repeated.result.output
    assert "durable_effect_replay" not in repeated.result.stderr_tags


def test_same_react_status_step_reads_updated_sqlite_counts(household):
    _login(household.client)
    first = household.client.post("/test/agent/photos_status", json={}).json()
    assert first["output"]["index"]["faces"] == 0
    with sqlite3.connect(household.service.db_path) as conn:
        conn.execute(
            "INSERT INTO image_faces VALUES (?, 0, ?)",
            ("Family/beach.jpg", index._vec_to_blob([1.0, 0.0])),
        )
    second = household.client.post("/test/agent/photos_status", json={}).json()
    assert second["output"]["index"]["faces"] == 1
    assert "durable_effect_replay" not in second["stderr_tags"]
    assert all(
        household.registry.get(name).replay_policy == "refresh_read"
        for name in ("photos_library", "photos_search", "photos_status", "photos_index_plan")
    )


@pytest.mark.parametrize("change", ["narrow", "revoke", "expire"])
def test_duplicate_codex_call_rechecks_the_real_photo_grant(household, monkeypatch, change):
    _login(household.client)
    initial = household.client.post("/test/agent/photos_library", json={}).json()
    assert initial["output"]["total"] == 1

    async def exercise():
        broker = CodexDynamicToolBroker(
            SimpleNamespace(executor=household.executor),
            SimpleNamespace(
                agent_id="photos",
                arms=[SimpleNamespace(arm_id="photos", allowed_skills=["photos_library"])],
                extra_skills=[],
            ),
            context={},
            goal="Browse my photo library",
            outer_thread_id="photo-thread",
            outer_turn_id="photo-turn",
            workspace=str(household.service.db_path.parent),
            tenant_id="household",
            principal_id="local:alice",
            approval_provider=AutoDenyProvider(),
            is_interrupted=lambda: False,
        )
        broker.bind_inner_scope(thread_id="inner-thread", turn_id="inner-turn")
        request = ApprovalRequest(
            request_id=1,
            method="item/tool/call",
            params={
                "threadId": "inner-thread",
                "turnId": "inner-turn",
                "callId": "same-photo-call",
                "tool": broker.catalog.names[0],
                "arguments": {},
            },
        )
        first = await broker(request)
        assert first["success"] is True
        assert json.loads(first["contentItems"][0]["text"])["total"] == 1
        if change == "narrow":
            household.policy.revoked = True
        elif change == "revoke":
            household.security.floor = int(time.time()) + 2
        else:
            later = time.time() + 7200
            monkeypatch.setattr("appliance.agent_authorization.time.time", lambda: later)
        second = await broker(request)
        result = json.loads(second["contentItems"][0]["text"])
        if change == "narrow":
            assert second["success"] is True
            assert result["total"] == 0
            assert result["items"] == []
        else:
            assert second["success"] is False
            assert result["code"] == "photo_access_denied"
            assert "items" not in result

    # The grant comes from the signed HTTP request. Its private ContextVar
    # must survive the broker's real asyncio.to_thread dispatch, then expire
    # or revoke in place without an old callId result bypassing the handler.
    household.captured_contexts[-1].run(asyncio.run, exercise())
