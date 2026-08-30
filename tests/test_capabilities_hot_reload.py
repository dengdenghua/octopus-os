# ruff: noqa: E402 — optional FastAPI import guard precedes route imports

from __future__ import annotations

from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.agents import AgentRegistry
from runtime.execution.all_skills import register_group, skills_in_group
from runtime.execution.suckers import SkillRegistry
from runtime.platform import capabilities as caps_mod
from runtime.platform.runtime_policy.capabilities import Capabilities
from runtime.sensing.gateway._agents_endpoints_system import (
    _reconcile_automation_registry,
)
from runtime.sensing.gateway.agents_router import create_agents_router

_GROUPS = ("browser", "browser_act", "computer")


def _assert_groups(registry: SkillRegistry, *, present: bool) -> None:
    for group in _GROUPS:
        for skill_id in skills_in_group(group):
            assert registry.has(skill_id) is present, (group, skill_id)


def test_reconcile_removes_and_restores_automation_groups() -> None:
    registry = SkillRegistry()
    for group in _GROUPS:
        register_group(registry, group)
    _assert_groups(registry, present=True)

    removed = _reconcile_automation_registry(
        registry,
        Capabilities(browser_automation=False, desktop_automation=False),
    )
    assert removed["removed"]
    _assert_groups(registry, present=False)

    restored = _reconcile_automation_registry(registry, Capabilities.defaults())
    assert restored["registered"]
    _assert_groups(registry, present=True)


def test_settings_capability_put_hot_applies_without_restart(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(caps_mod, "_store_path", lambda: tmp_path / "capabilities.json")
    skill_registry = SkillRegistry()
    for group in _GROUPS:
        register_group(skill_registry, group)

    app = FastAPI()
    app.state.echo_state = SimpleNamespace(registry=skill_registry)
    app.include_router(create_agents_router(registry=AgentRegistry()))
    client = TestClient(app)

    disabled = client.put(
        "/api/settings/capabilities",
        json={"browser_automation": False, "desktop_automation": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["restart_required"] is False
    _assert_groups(skill_registry, present=False)

    enabled = client.put(
        "/api/settings/capabilities",
        json={"browser_automation": True, "desktop_automation": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["restart_required"] is False
    assert caps_mod.load() == Capabilities.defaults()
    _assert_groups(skill_registry, present=True)


