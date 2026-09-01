"""
Tests for ``runtime.adapters.mcp_client.trust`` and its integration
with the skill-registration bridge.

Contract pinned
---------------

1. Empty store · is_approved returns False
2. approve() persists to disk · next store instance reads it back
3. approve() with tools pins a digest · same tool list → approved,
   mutated tool list → NOT approved (requires re-confirmation)
4. approve() without tools creates legacy entry · any tool list approved
5. revoke() flips approved=False · digest retained for audit
6. forget() removes the entry entirely
7. Malformed JSON store tolerated · empty dict in memory
8. bridge.register_mcp_tools_as_skills(require_trust=True):
   - untrusted server → returns [] (no tools registered)
   - trusted server → returns full list
   - server_name explicit param overrides tool.server_name
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def trust_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    from runtime.adapters.mcp_client.trust import (
        reset_trust_store_for_tests,
    )

    reset_trust_store_for_tests()
    yield tmp_path
    reset_trust_store_for_tests()


# ═══════════════════════════════════════════════════════════
# Store basics
# ═══════════════════════════════════════════════════════════


class TestTrustStore:
    def test_empty_store_nothing_approved(self, trust_home: Path):
        from runtime.adapters.mcp_client.trust import get_trust_store

        store = get_trust_store()
        assert store.is_approved("anything") is False
        assert store.list_all() == []

    def test_approve_persists(self, trust_home: Path):
        from runtime.adapters.mcp_client.trust import (
            MCPTrustStore,
            get_trust_store,
        )

        store = get_trust_store()
        store.approve("fs-mcp", ["read_file", "write_file"], note="trusted")
        assert store.is_approved("fs-mcp") is True

        # New instance · reads from disk
        fresh = MCPTrustStore()
        assert fresh.is_approved("fs-mcp") is True
        entries = fresh.list_all()
        assert len(entries) == 1
        assert entries[0].note == "trusted"

    def test_digest_pinning_catches_mutation(self, trust_home: Path):
        from runtime.adapters.mcp_client.trust import get_trust_store

        store = get_trust_store()
        store.approve("weather", ["get_forecast"])
        # Same tools → still approved
        assert store.is_approved("weather", ["get_forecast"]) is True
        # Mutation → digest miss → NOT approved
        assert (
            store.is_approved(
                "weather",
                ["get_forecast", "exec_shell"],
            )
            is False
        )

    def test_digest_order_insensitive(self, trust_home: Path):
        from runtime.adapters.mcp_client.trust import get_trust_store

        store = get_trust_store()
        store.approve("srv", ["b", "a", "c"])
        assert store.is_approved("srv", ["c", "a", "b"]) is True

    def test_legacy_entry_no_digest_accepts_any(self, trust_home: Path):
        """Manually write an entry without tool_digest (simulating
        a legacy install) · verify is_approved accepts any tool list."""
        from runtime.adapters.mcp_client.trust import (
            MCPTrustStore,
            _trust_store_path,
        )

        path = _trust_store_path()
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "server_name": "legacy",
                            "approved": True,
                            "added_ts": 1.0,
                            "tool_digest": "",
                            "note": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        store = MCPTrustStore()
        assert store.is_approved("legacy", ["anything"]) is True

    def test_revoke_flips_approved(self, trust_home: Path):
        from runtime.adapters.mcp_client.trust import get_trust_store

        store = get_trust_store()
        store.approve("srv", ["t"])
        assert store.revoke("srv") is True
        assert store.is_approved("srv") is False
        # Revoke non-existent → False
        assert store.revoke("missing") is False

    def test_forget_removes_entry(self, trust_home: Path):
        from runtime.adapters.mcp_client.trust import get_trust_store

        store = get_trust_store()
        store.approve("srv", ["t"])
        assert store.forget("srv") is True
        assert store.list_all() == []
        assert store.forget("srv") is False

    def test_malformed_json_tolerated(self, trust_home: Path):
        from runtime.adapters.mcp_client.trust import (
            MCPTrustStore,
            _trust_store_path,
        )

        _trust_store_path().write_text("not json {", encoding="utf-8")
        # Should not raise
        store = MCPTrustStore()
        assert store.list_all() == []


# ═══════════════════════════════════════════════════════════
# Bridge integration · require_trust gate
# ═══════════════════════════════════════════════════════════


class _FakeTool:
    def __init__(self, name: str, server: str = "test-srv"):
        self.name = name
        self.description = f"tool {name}"
        self.server_name = server


class _FakeClient:
    def __init__(self, tools: list[_FakeTool]):
        self._tools = tools

    def list_tools(self) -> list[_FakeTool]:
        return list(self._tools)

    def call_tool(self, name: str, args):
        class _R:
            success = True
            output = {}
            error = None
            latency_ms = 0.0

        return _R()


class TestBridgeTrustGate:
    def test_untrusted_server_registers_nothing(self, trust_home: Path):
        from runtime.adapters.mcp_client.bridge import (
            register_mcp_tools_as_skills,
        )
        from runtime.execution.suckers import SkillRegistry

        reg = SkillRegistry()
        client = _FakeClient(
            [
                _FakeTool("read", server="spooky-srv"),
                _FakeTool("write", server="spooky-srv"),
            ]
        )
        registered = register_mcp_tools_as_skills(reg, client)
        assert registered == []
        assert not reg.has("read")

    def test_trusted_server_registers_all(self, trust_home: Path):
        from runtime.adapters.mcp_client.bridge import (
            register_mcp_tools_as_skills,
        )
        from runtime.adapters.mcp_client.trust import get_trust_store
        from runtime.execution.suckers import SkillRegistry

        get_trust_store().approve("good-srv", ["read", "write"])
        reg = SkillRegistry()
        client = _FakeClient(
            [
                _FakeTool("read", server="good-srv"),
                _FakeTool("write", server="good-srv"),
            ]
        )
        registered = register_mcp_tools_as_skills(
            reg,
            client,
            require_trust=True,
            name_prefix="",
        )
        assert set(registered) == {"read", "write"}

    def test_explicit_server_name_overrides_tool_attr(
        self,
        trust_home: Path,
    ):
        """When caller passes server_name, it's used for trust lookup
        regardless of what tool.server_name says — useful when the
        server advertises a different internal name."""
        from runtime.adapters.mcp_client.bridge import (
            register_mcp_tools_as_skills,
        )
        from runtime.adapters.mcp_client.trust import get_trust_store
        from runtime.execution.suckers import SkillRegistry

        get_trust_store().approve("outer-name", ["t1"])
        reg = SkillRegistry()
        client = _FakeClient([_FakeTool("t1", server="inner-name")])
        registered = register_mcp_tools_as_skills(
            reg,
            client,
            require_trust=True,
            server_name="outer-name",
            name_prefix="",
        )
        assert registered == ["t1"]

    def test_require_trust_false_skips_check(self, trust_home: Path):
        """Test / mock callers pass require_trust=False · untrusted
        tools still register (mocks aren't running user binaries)."""
        from runtime.adapters.mcp_client.bridge import (
            register_mcp_tools_as_skills,
        )
        from runtime.execution.suckers import SkillRegistry

        reg = SkillRegistry()
        client = _FakeClient([_FakeTool("t1", server="unknown")])
        registered = register_mcp_tools_as_skills(
            reg,
            client,
            require_trust=False,
        )
        assert registered == ["mcp_unknown_t1"]

    def test_default_prefix_prevents_builtin_overwrite(self, trust_home: Path):
        from runtime.adapters.mcp_client.bridge import register_mcp_tools_as_skills
        from runtime.adapters.mcp_client.trust import get_trust_store
        from runtime.execution.suckers import Skill, SkillRegistry

        reg = SkillRegistry()
        reg.register(
            Skill(
                name="exec_shell",
                trusted_source="builtin://exec_shell",
                handler=lambda **kw: "builtin",
            ),
            verify_tests=False,
        )
        get_trust_store().approve("shell-srv", ["exec_shell"])
        client = _FakeClient([_FakeTool("exec_shell", server="shell-srv")])

        registered = register_mcp_tools_as_skills(reg, client)

        assert registered == ["mcp_shell_srv_exec_shell"]
        assert reg.get("exec_shell").trusted_source == "builtin://exec_shell"
        assert reg.get("mcp_shell_srv_exec_shell").trusted_source == ("mcp://shell-srv/exec_shell")

    def test_explicit_unprefixed_collision_is_rejected(self, trust_home: Path):
        from runtime.adapters.mcp_client.bridge import register_mcp_tools_as_skills
        from runtime.execution.suckers import Skill, SkillRegistry

        reg = SkillRegistry()
        reg.register(
            Skill(
                name="exec_shell",
                trusted_source="builtin://exec_shell",
                handler=lambda **kw: "builtin",
            ),
            verify_tests=False,
        )
        client = _FakeClient([_FakeTool("exec_shell", server="unknown")])

        registered = register_mcp_tools_as_skills(
            reg,
            client,
            require_trust=False,
            name_prefix="",
        )

        assert registered == []
        assert reg.get("exec_shell").trusted_source == "builtin://exec_shell"
