"""Security-boundary tests for the Echo-owned Codex dynamic tool broker."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from runtime.execution.codex_backend.dynamic_tools import CodexDynamicToolBroker
from runtime.execution.codex_backend.types import ApprovalRequest
from runtime.execution.suckers.registry import Skill, SkillRegistry
from runtime.safety.approval.approval_gate import AutoDenyProvider


def _agent(*names: str) -> SimpleNamespace:
    return SimpleNamespace(
        agent_id="coder",
        arms=[SimpleNamespace(arm_id="code", allowed_skills=list(names))],
        extra_skills=[],
    )


def _broker(
    tmp_path: Path,
    registry: SkillRegistry,
    *,
    names: tuple[str, ...],
    context: dict[str, Any] | None = None,
    goal: str = "perform the requested operation",
    tenant_id: str = "tenant-a",
) -> CodexDynamicToolBroker:
    stack = SimpleNamespace(executor=SimpleNamespace(registry=registry))
    broker = CodexDynamicToolBroker(
        stack,
        _agent(*names),
        context=context or {},
        goal=goal,
        outer_thread_id="outer-thread",
        outer_turn_id="outer-turn",
        workspace=str(tmp_path),
        tenant_id=tenant_id,
        principal_id="actor-a",
        approval_provider=AutoDenyProvider(),
        is_interrupted=lambda: False,
    )
    broker.bind_inner_scope(thread_id="inner-thread", turn_id="inner-turn")
    return broker


def _request(tool: str, arguments: dict[str, Any], *, call_id: str = "call-1") -> ApprovalRequest:
    return ApprovalRequest(
        request_id=1,
        method="item/tool/call",
        params={
            "threadId": "inner-thread",
            "turnId": "inner-turn",
            "callId": call_id,
            "tool": tool,
            "arguments": arguments,
        },
    )


@pytest.mark.asyncio
async def test_unknown_disabled_and_replaced_tools_fail_closed(tmp_path: Path) -> None:
    calls: list[str] = []

    def inspect_payload(payload: dict) -> dict[str, Any]:
        calls.append("old")
        return {"ok": True, "payload": payload}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="inspect_payload",
            description="Inspect one payload.",
            trusted_source="skill://public/inspect_payload",
            handler=inspect_payload,
            tenant_id="tenant-a",
        ),
        verify_tests=False,
    )
    broker = _broker(tmp_path, registry, names=("inspect_payload",))
    advertised = broker.catalog.names[0]

    unknown = await broker(_request("not-advertised", {}))
    assert unknown["success"] is False
    assert "catalog" in unknown["contentItems"][0]["text"]

    registry.disable("inspect_payload")
    disabled = await broker(_request(advertised, {"payload": {}}, call_id="call-disabled"))
    assert disabled["success"] is False
    assert "disabled" in disabled["contentItems"][0]["text"]
    registry.enable("inspect_payload")

    def replacement(payload: dict) -> dict[str, Any]:
        calls.append("replacement")
        return {"ok": True, "payload": payload}

    registry.register(
        Skill(
            name="inspect_payload",
            description="Replacement implementation.",
            trusted_source="skill://public/inspect_payload",
            handler=replacement,
            tenant_id="tenant-a",
        ),
        verify_tests=False,
        replace=True,
    )
    replaced = await broker(_request(advertised, {"payload": {}}, call_id="call-replaced"))
    assert replaced["success"] is False
    assert "implementation changed" in replaced["contentItems"][0]["text"]
    assert calls == []


@pytest.mark.asyncio
async def test_tenant_change_argument_limits_and_client_bypass_fail_closed(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def inspect_payload(payload: dict) -> dict[str, Any]:
        calls.append("inspect")
        return {"ok": True}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="inspect_payload",
            description="Inspect one payload.",
            trusted_source="skill://public/inspect_payload",
            handler=inspect_payload,
            tenant_id="tenant-a",
        ),
        verify_tests=False,
    )
    broker = _broker(tmp_path, registry, names=("inspect_payload",))
    advertised = broker.catalog.names[0]
    oversized = await broker(
        _request(advertised, {"payload": {"blob": "x" * 70_000}}, call_id="large")
    )
    assert oversized["success"] is False
    assert "too large" in oversized["contentItems"][0]["text"]

    registry.register(
        Skill(
            name="inspect_payload",
            description="Other tenant implementation.",
            trusted_source="skill://public/inspect_payload",
            handler=inspect_payload,
            tenant_id="tenant-b",
        ),
        verify_tests=False,
        replace=True,
    )
    tenant_changed = await broker(_request(advertised, {"payload": {}}, call_id="tenant-change"))
    assert tenant_changed["success"] is False
    assert "tenant" in tenant_changed["contentItems"][0]["text"]
    assert calls == []

    def exec_shell(command: str) -> str:
        calls.append(command)
        return "ran"

    risky_registry = SkillRegistry()
    risky_registry.register(
        Skill(
            name="exec_shell",
            description="Execute a shell command.",
            trusted_source="skill://public/exec_shell",
            handler=exec_shell,
        ),
        verify_tests=False,
    )
    risky = _broker(
        tmp_path,
        risky_registry,
        names=("exec_shell",),
        context={"permission_mode": "bypassPermissions"},
    )
    denied = await risky(_request(risky.catalog.names[0], {"command": "true"}))
    assert denied["success"] is False
    assert "approval" in denied["contentItems"][0]["text"].lower()
    assert calls == []


@pytest.mark.asyncio
async def test_argument_depth_node_and_numeric_size_limits_precede_execution(
    tmp_path: Path,
) -> None:
    calls: list[object] = []

    def inspect_payload(payload: object) -> dict[str, Any]:
        calls.append(payload)
        return {"ok": True}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="inspect_payload",
            description="Inspect one payload.",
            trusted_source="skill://public/inspect_payload",
            handler=inspect_payload,
            tenant_id="tenant-a",
        ),
        verify_tests=False,
    )
    broker = _broker(tmp_path, registry, names=("inspect_payload",))
    advertised = broker.catalog.names[0]

    nested: dict[str, Any] = {}
    cursor = nested
    for _ in range(20):
        child: dict[str, Any] = {}
        cursor["child"] = child
        cursor = child
    too_deep = await broker(_request(advertised, {"payload": nested}, call_id="deep"))
    assert too_deep["success"] is False
    assert "deeply" in too_deep["contentItems"][0]["text"]

    too_many = await broker(_request(advertised, {"payload": list(range(2_100))}, call_id="many"))
    assert too_many["success"] is False
    assert "too many" in too_many["contentItems"][0]["text"]

    huge_integer = await broker(
        _request(advertised, {"payload": 1 << 220_000}, call_id="huge-number")
    )
    assert huge_integer["success"] is False
    assert "too large" in huge_integer["contentItems"][0]["text"]
    assert calls == []


@pytest.mark.asyncio
async def test_broker_exception_response_and_log_do_not_expose_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="inspect_payload",
            description="Inspect one payload.",
            trusted_source="skill://public/inspect_payload",
            handler=lambda payload: payload,
            tenant_id="tenant-a",
        ),
        verify_tests=False,
    )
    broker = _broker(tmp_path, registry, names=("inspect_payload",))

    def raise_secret(*_args: Any, **_kwargs: Any) -> tuple[str, bool, str]:
        raise RuntimeError("token=super-secret /private/host/path")

    monkeypatch.setattr(broker, "_execute_sync", raise_secret)
    result = await broker(
        _request(broker.catalog.names[0], {"payload": {}}, call_id="secret-error")
    )
    rendered = result["contentItems"][0]["text"]
    assert result["success"] is False
    assert rendered == "Echo dynamic tool failed (RuntimeError)"
    assert "super-secret" not in rendered
    assert "super-secret" not in caplog.text
    assert "/private/host/path" not in caplog.text


@pytest.mark.asyncio
async def test_explicit_echo_plugin_grant_is_brokered_and_next_turn_revocation_is_exact(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="base_read",
            description="Read base information.",
            trusted_source="skill://public/base_read",
            handler=lambda: {"ok": True},
            tenant_id="tenant-a",
        ),
        verify_tests=False,
    )
    registry.register(
        Skill(
            name="tenant_plugin_action",
            description="Run the explicitly selected tenant plugin action.",
            trusted_source="plugin://tenant-a/example/action",
            handler=lambda value: calls.append(value) or {"ok": True},
            tenant_id="tenant-a",
        ),
        verify_tests=False,
    )

    granted = _broker(
        tmp_path,
        registry,
        names=("base_read",),
        context={
            "extra_tool_allowlist": ["tenant_plugin_action"],
            "plugin_grants": ["example"],
        },
    )
    assert set(granted.catalog.names) >= {"base_read", "tenant_plugin_action"}
    result = await granted(
        _request(
            "tenant_plugin_action",
            {"value": "ran-through-echo"},
            call_id="plugin-call",
        )
    )
    assert result["success"] is True
    assert calls == ["ran-through-echo"]

    # A new outer turn rebuilds the broker. If the plugin grant was removed,
    # its schema is absent even when App Server resumes the durable thread.
    revoked = _broker(tmp_path, registry, names=("base_read",), context={})
    assert "tenant_plugin_action" not in revoked.catalog.names

    # Even an identically shaped grant cannot cross the registry's ambient
    # tenant boundary. Missing and foreign-tenant tools are indistinguishable.
    other_tenant = _broker(
        tmp_path,
        registry,
        names=("base_read",),
        context={
            "extra_tool_allowlist": ["tenant_plugin_action"],
            "plugin_grants": ["example"],
        },
        tenant_id="tenant-b",
    )
    assert "tenant_plugin_action" not in other_tenant.catalog.names


@pytest.mark.asyncio
async def test_selected_codex_plugin_is_loaded_on_demand_without_ambient_surfaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = tmp_path / "plugins" / "demo-plugin"
    (plugin_dir / ".codex-plugin").mkdir(parents=True)
    (plugin_dir / ".codex-plugin" / "plugin.json").write_text(
        '{"name":"demo-plugin","version":"0.1.0",'
        '"interface":{"displayName":"Demo Plugin",'
        '"capabilities":[{"name":"demo","type":"codex"}]},'
        '"mcpServers":{"ambient":{"command":"must-not-run"}},'
        '"apps":[{"name":"must-not-load"}],"hooks":{"turn":"must-not-run"}}',
        encoding="utf-8",
    )
    skill_dir = plugin_dir / "skills" / "hello"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: hello\ndescription: Return the selected plugin prompt.\n---\n"
        "SELECTED_PLUGIN_INSTRUCTION\n",
        encoding="utf-8",
    )

    from runtime.platform.plugins import codex_discovery

    discovered = codex_discovery.discover_codex_plugins([plugin_dir.parent])
    assert discovered and discovered[0]["id"] == "demo-plugin"
    monkeypatch.setattr(
        codex_discovery,
        "discover_codex_plugins",
        lambda _roots=None: discovered,
    )

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="base_read",
            description="Read base information.",
            trusted_source="skill://public/base_read",
            handler=lambda: {"ok": True},
            tenant_id="tenant-a",
        ),
        verify_tests=False,
    )
    assert not registry.has("demo-plugin__hello")

    broker = _broker(
        tmp_path,
        registry,
        names=("base_read",),
        context={"plugin_grants": ["demo-plugin"]},
        goal="Use @plugin:demo-plugin for this task",
    )
    assert "demo-plugin__hello" in broker.catalog.names
    assert registry.has("demo-plugin__hello")

    result = await broker(_request("demo-plugin__hello", {}, call_id="selected-plugin-call"))
    assert result["success"] is True
    assert "SELECTED_PLUGIN_INSTRUCTION" in result["contentItems"][0]["text"]

    # The plugin remains installed in Echo' registry, but removing the
    # turn selection withdraws the action from the next App Server catalog.
    revoked = _broker(tmp_path, registry, names=("base_read",), context={})
    assert "demo-plugin__hello" not in revoked.catalog.names


