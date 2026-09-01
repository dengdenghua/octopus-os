"""Marketplace permissions remain inactive until exact local approval."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.execution.tool_engine.skill_gate import GATE_CAPABILITY, gate_inner_dispatch
from runtime.memory.journal import InMemoryJournal
from runtime.platform.capabilities.capability_registry import CapabilityRegistry
from runtime.platform.capabilities.permission_grants import (
    CapabilityPermissionStore,
    use_capability_permission_store,
)
from runtime.platform.capabilities.tenant_context import use_capability_scope
from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
from runtime.safety.auth import TrustEngine
from runtime.safety.auth.scope import TenantScope


def test_signed_generation_requires_exact_grant_and_reapproval_after_update(
    tmp_path: Path,
) -> None:
    store = CapabilityPermissionStore(tmp_path / "permission-grants.json")
    first = store.stage(
        "documents",
        kind="codex",
        required=["content.read"],
        manifest_digest="sha256:first",
        runtime_sources=["plugin://documents/"],
    )

    assert first["active"] is False
    assert store.runtime_allows("documents__read", "plugin://documents/read")[0] is False
    try:
        store.grant("documents", [])
    except ValueError:
        pass
    else:  # pragma: no cover - regression guard
        raise AssertionError("partial grants must be rejected")

    store.grant("documents", ["content.read"])
    store.set_active("documents", True)
    assert store.runtime_allows("documents__read", "plugin://documents/read") == (True, None)

    updated = store.stage(
        "documents",
        kind="codex",
        required=["content.read", "content.write"],
        manifest_digest="sha256:second",
        runtime_sources=["plugin://documents/"],
    )
    assert updated["granted"] == []
    assert updated["active"] is False
    assert store.runtime_allows("documents__read", "plugin://documents/read")[0] is False


def test_invalid_permission_state_fails_closed_only_for_marketplace_sources(
    tmp_path: Path,
) -> None:
    state = tmp_path / "permission-grants.json"
    state.write_text('{"schema":"wrong","records":{}}', encoding="utf-8")
    store = CapabilityPermissionStore(state)

    assert store.runtime_allows("read", "plugin://documents/read")[0] is False
    assert store.runtime_allows("read", "mcp://remote/read")[0] is False
    assert store.runtime_allows("read_file", "builtin://read_file") == (True, None)


def test_shared_runtime_source_cannot_bypass_an_inactive_owner(tmp_path: Path) -> None:
    store = CapabilityPermissionStore(tmp_path / "permission-grants.json")
    for capability_id in ("first", "second"):
        store.stage(
            capability_id,
            kind="connector",
            required=["network.remote"],
            runtime_sources=["mcp://shared/"],
        )
        store.grant(capability_id, ["network.remote"])
    store.set_active("first", True)

    allowed, reason = store.runtime_allows("shared_tool", "mcp://shared/read")

    assert allowed is False
    assert reason == "capability permission denied: second"


def test_executor_and_inner_dispatch_share_marketplace_permission_gate(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []
    skill = Skill(
        name="documents__read",
        description="read a marketplace document",
        trusted_source="plugin://documents/read",
        handler=lambda **kwargs: calls.append(kwargs) or {"ok": True},
    )
    registry = SkillRegistry()
    registry.register(skill)
    store = CapabilityPermissionStore(tmp_path / "permission-grants.json")
    store.stage(
        "documents",
        kind="codex",
        required=["content.read"],
        manifest_digest="sha256:first",
        runtime_sources=["plugin://documents/"],
    )

    task_id = TaskId(uuid4())
    executor = ToolExecutor(
        registry,
        TrustEngine(trusted_sources=["plugin://documents/*"]),
        InMemoryJournal(),
    )
    with use_capability_permission_store(store):
        blocked = executor.execute_step(
            step_id=1,
            node_id="marketplace_1",
            sucker_id=SkillId(skill.name),
            args={"path": "memo.txt"},
            caller="test",
            task_id=task_id,
            arm_id=ArmId("arm_1"),
            budget=Budget(task_id=task_id, limits=BudgetLimits(tokens=1000, usd=1)),
        )
        inner_block = gate_inner_dispatch(skill, {}, caller="test")

    assert blocked.result.status == "immune_reject"
    assert any(
        "capability permission denied: documents" in tag for tag in blocked.result.stderr_tags
    )
    assert inner_block is not None and inner_block.gate == GATE_CAPABILITY
    assert calls == []

    store.grant("documents", ["content.read"])
    store.set_active("documents", True)
    with use_capability_permission_store(store):
        allowed = executor.execute_step(
            step_id=2,
            node_id="marketplace_2",
            sucker_id=SkillId(skill.name),
            args={"path": "memo.txt"},
            caller="test",
            task_id=task_id,
            arm_id=ArmId("arm_1"),
            budget=Budget(task_id=task_id, limits=BudgetLimits(tokens=1000, usd=1)),
        )
        assert gate_inner_dispatch(skill, {}, caller="test") is None

    assert allowed.result.status == "success"
    assert calls == [{"path": "memo.txt"}]


def test_install_plan_is_deterministic_and_side_effect_free(tmp_path: Path) -> None:
    class ConnectorCatalog:
        _permissions = None

        @staticmethod
        def list() -> list[dict[str, object]]:
            return [
                {
                    "id": "plan-only",
                    "name": "Plan only",
                    "name_zh": "仅规划",
                    "description": "",
                    "description_zh": "",
                    "type": "mcp",
                    "auth_mode": "oauth",
                    "mcp_servers": [{"name": "plan-only", "url": "https://example.test"}],
                    "installed": False,
                    "enabled": False,
                    "version": "1.0.0",
                    "host_api": ">=0",
                    "permissions": ["account.credentials", "network.remote"],
                    "auth_modes": ["oauth"],
                    "dependencies": [],
                    "runtime_dependencies": ["vendor-runtime"],
                }
            ]

        @staticmethod
        def get(connector_id: str):
            return object() if connector_id == "plan-only" else None

    state = tmp_path / "capabilities.json"
    permission_state = tmp_path / "permission-grants.json"
    cache = tmp_path / "codex-cache"
    cache.mkdir()
    registry = CapabilityRegistry(
        connector_registry=ConnectorCatalog(),
        auth_orchestrator=object(),
        codex_cache=cache,
        capability_state_file=state,
        skills_root=tmp_path / "skills",
        permission_store=CapabilityPermissionStore(permission_state),
    )

    first = registry.install_plan("plan-only")
    second = registry.install_plan("plan-only")

    assert first == second
    assert first["schema"] == "echo.capability_install_plan.v1"
    assert first["can_install"] is True
    assert first["permissions"] == ["account.credentials", "network.remote"]
    assert first["runtime_dependencies"] == [{"name": "vendor-runtime", "bundled": True}]
    assert not state.exists()
    assert not permission_state.exists()


def test_install_plan_resolves_nested_marketplace_dependencies(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from runtime.platform.plugins.cloud_catalog import CloudCatalog

    class ConnectorCatalog:
        _permissions = None

        @staticmethod
        def list() -> list[dict[str, object]]:
            return [
                {
                    "id": "root-package",
                    "name": "Root package",
                    "type": "mcp",
                    "auth_mode": "none",
                    "mcp_servers": [],
                    "installed": False,
                    "enabled": False,
                    "dependencies": ["middle-package"],
                }
            ]

        @staticmethod
        def get(connector_id: str):
            return object() if connector_id == "root-package" else None

    monkeypatch.setattr(CloudCatalog, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        CloudCatalog,
        "items",
        lambda self: [
            {
                "id": "root-package",
                "plugin": "root-package",
                "kind": "connector",
                "dependencies": ["middle-package"],
            },
            {
                "id": "middle-package",
                "plugin": "middle-package",
                "kind": "codex",
                "dependencies": ["leaf-package"],
            },
            {
                "id": "leaf-package",
                "plugin": "leaf-package",
                "kind": "codex",
                "dependencies": [],
            },
        ],
    )
    monkeypatch.setattr(CloudCatalog, "plugin_statuses", lambda self: {})

    registry = CapabilityRegistry(
        connector_registry=ConnectorCatalog(),
        auth_orchestrator=object(),
        codex_cache=tmp_path / "codex-cache",
        capability_state_file=tmp_path / "capabilities.json",
        skills_root=tmp_path / "skills",
        permission_store=CapabilityPermissionStore(tmp_path / "permissions.json"),
    )

    plan = registry.install_plan("root-package")

    assert plan["can_install"] is True
    assert plan["dependencies"] == [
        {
            "id": "leaf-package",
            "required_by": "middle-package",
            "ready": False,
            "will_install": True,
            "state": "planned",
        },
        {
            "id": "middle-package",
            "required_by": "root-package",
            "ready": False,
            "will_install": True,
            "state": "planned",
        },
    ]


def test_permission_state_is_valid_json_after_parallel_safe_mutations(tmp_path: Path) -> None:
    store = CapabilityPermissionStore(tmp_path / "permission-grants.json")
    store.stage("sample", kind="connector", required=[], manifest_digest="v1")
    store.grant("sample", [])
    store.set_active("sample", True)

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["schema"] == "echo.capability_permission_grants.v1"
    assert payload["records"]["sample"]["active"] is True


def test_principal_grants_are_isolated_and_upgrade_invalidates_every_scope(
    tmp_path: Path,
) -> None:
    store = CapabilityPermissionStore(tmp_path / "permission-grants.json")
    alice = TenantScope(tenant_id="family", actor_id="alice")
    bob = TenantScope(tenant_id="family", actor_id="bob")

    with use_capability_scope(alice):
        store.stage(
            "documents",
            kind="codex",
            required=["content.read"],
            manifest_digest="sha256:first",
            runtime_sources=["plugin://documents/"],
        )
        store.grant("documents", ["content.read"])
        store.set_active("documents", True)
        assert store.runtime_allows("documents__read", "plugin://documents/read") == (
            True,
            None,
        )

    with use_capability_scope(bob):
        assert store.get("documents") is None
        assert store.runtime_allows("documents__read", "plugin://documents/read")[0] is False
        store.stage_principal("documents")
        store.grant("documents", ["content.read"])
        store.set_active("documents", True)
        assert store.runtime_allows("documents__read", "plugin://documents/read")[0] is True

    with use_capability_scope(alice):
        store.stage(
            "documents",
            kind="codex",
            required=["content.read", "content.write"],
            manifest_digest="sha256:second",
            runtime_sources=["plugin://documents/"],
        )
        assert store.runtime_allows("documents__read", "plugin://documents/read")[0] is False

    # Bob's old record was not rewritten, but the device generation changed;
    # it is therefore stale and denied without reading Alice's partition.
    with use_capability_scope(bob):
        assert store.generation_current("documents") is False
        assert store.runtime_allows("documents__read", "plugin://documents/read")[0] is False
        store.stage_principal("documents")
        assert store.get("documents")["granted"] == []


def test_partitioned_permission_snapshot_restores_generation_and_principal(
    tmp_path: Path,
) -> None:
    store = CapabilityPermissionStore(tmp_path / "permission-grants.json")
    alice = TenantScope(tenant_id="family", actor_id="alice")
    with use_capability_scope(alice):
        store.stage(
            "documents",
            kind="codex",
            required=["content.read"],
            manifest_digest="sha256:first",
        )
        store.grant("documents", ["content.read"])
        store.set_active("documents", True)
        before = store.snapshot("documents")
        store.stage(
            "documents",
            kind="codex",
            required=["content.read", "content.write"],
            manifest_digest="sha256:second",
        )

        store.restore("documents", before)

        assert store.require_granted("documents", require_active=True)["manifest_digest"] == (
            "sha256:first"
        )


