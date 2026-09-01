"""Implementation note."""

from __future__ import annotations

from pathlib import Path

import pytest
from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.agents import AgentRegistry, make_desktop_operator_agent
from runtime.execution.arms.presets import make_desktop_operator_arm

# ═══════════════════════════════════════════════════════════
# Desktop Operator preset
# ═══════════════════════════════════════════════════════════


class _FakeExecutor:
    journal = None


def _rt():
    return GraphRuntime(executor=_FakeExecutor(), journal=None)


class TestDesktopOperatorArm:
    def test_arm_includes_computer_skills(self):
        arm = make_desktop_operator_arm(_rt())
        names = {str(s) for s in arm.allowed_skills}
        assert "screen_capture" in names
        assert "mouse_click" in names
        assert "keyboard_type" in names
        assert "computer_use_loop" in names
        assert "computer_uia_tree" in names

    def test_arm_affinity_mentions_vision(self):
        arm = make_desktop_operator_arm(_rt())
        tags = set(arm.affinity)
        assert "desktop" in tags
        assert "vision" in tags

    def test_arm_has_display_name_and_icon(self):
        arm = make_desktop_operator_arm(_rt())
        assert arm.display_name == "Desktop Operator"
        assert arm.icon == "🖥️"


class TestDesktopOperatorAgent:
    def test_agent_constructs(self):
        agent = make_desktop_operator_agent(_rt())
        assert agent.agent_id == "desktop_operator"
        assert agent.display_name == "Desktop Operator"
        assert agent.icon == "🖥️"
        assert "desktop" in agent.extra_affinity

    def test_agent_has_one_arm(self):
        agent = make_desktop_operator_agent(_rt())
        assert len(agent.arms) == 1

    def test_agent_can_use_core_desktop_skills(self):
        agent = make_desktop_operator_agent(_rt())
        assert agent.can_use("screen_capture")
        assert agent.can_use("mouse_click")
        assert agent.can_use("keyboard_type")
        assert agent.can_use("computer_use_loop")
        assert agent.can_use("computer_uia_find")
        # Implementation note.
        assert agent.can_use("read_file")

    def test_in_default_directory_roster(self):
        """The desktop operator is a first-class, directory-backed persona."""
        from runtime.execution.agents import make_all_agent_presets

        agent_ids = {str(agent.agent_id) for agent in make_all_agent_presets(_rt())}
        assert "desktop_operator" in agent_ids

    def test_general_agent_has_desktop_arm(self):
        """Implementation note."""
        from runtime.execution.agents import make_general_agent

        agent = make_general_agent(_rt())
        arm_ids = [str(a.arm_id) for a in agent.arms]
        assert "desktop_operator_arm" in arm_ids


# ═══════════════════════════════════════════════════════════
# /api/health
# ═══════════════════════════════════════════════════════════


fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from runtime.platform.ui import create_app  # noqa: E402


class TestHealthEndpoint:
    def test_basic_shape(self, tmp_path: Path):
        app = create_app(journal_path=tmp_path / "events.jsonl")
        r = TestClient(app).get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "ts" in data
        assert "skills" in data
        assert isinstance(data["skills"], int)
        assert "journal_events" in data
        assert isinstance(data["journal_events"], int)
        assert isinstance(data["channels"], list)

    def test_reports_agents_count_when_registered(self, tmp_path: Path):
        from runtime.execution.agents import make_all_agent_presets

        presets = make_all_agent_presets(_rt())
        reg = AgentRegistry()
        reg.register_all(presets)

        app = create_app(
            journal_path=tmp_path / "events.jsonl",
            agent_registry=reg,
        )
        r = TestClient(app).get("/api/health")
        data = r.json()
        # health endpoint should report exactly the agents we registered
        assert data["agents"] == len(presets)
        assert data["agents"] >= 4, "expected at least the four user-facing presets"

    def test_reports_channels_when_manager_wired(self, tmp_path: Path):
        from runtime.adapters.channels import (
            Channel,
            ChannelManager,
            OutboundMessage,
        )

        class _Fake(Channel):
            channel_id = "fake_x"

            def start(self):
                pass

            def stop(self):
                pass

            def send(self, msg: OutboundMessage):
                pass

        # Implementation note.
        class _S:
            pass

        s = _S()
        s.planner = None
        s.runtime = None
        s.registry = None
        s.journal = None

        reg = AgentRegistry()
        mgr = ChannelManager(stack=s, agent_registry=reg)
        mgr.register(_Fake())

        app = create_app(
            journal_path=tmp_path / "events.jsonl",
            agent_registry=reg,
            channel_manager=mgr,
        )
        data = TestClient(app).get("/api/health").json()
        assert "fake_x" in data["channels"]

    def test_defaults_when_nothing_extra_wired(self, tmp_path: Path):
        """Implementation note."""
        app = create_app(journal_path=tmp_path / "events.jsonl")
        data = TestClient(app).get("/api/health").json()
        assert data["agents"] == 0
        assert data["channels"] == []
        assert data["groups"] == 0
