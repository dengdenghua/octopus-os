"""
End-to-end regression tests for the mode-gated write scope.

These tests lock the contract between:
    * ``runtime/platform/scope.py`` — WriteScope resolver
    * ``runtime/execution/agents/base.py`` — Agent.capabilities
    * ``runtime/execution/beak/executor.py`` — sandbox_dir injection /
      enforcement when a Session is bound

Covered scenarios (mirrored from the design ladder in scope.py docstring):

    1. chat / coder                → own workspace only · /tmp/x rejected
    2. team / coder + team_id       → own + teams/<id>
    3. code / coder + extras        → own + extra_workspaces
    4. code / non-code-capable agent → silently downgrades to chat;
                                        extra_workspaces ignored
    5. executor integration         → out-of-scope path → PermissionError
                                        + no Session → enforcement skipped

Scope-integration tests exist because the enforcement spans three
layers (Session → resolver → executor) and any one can silently
degrade the permission model. A single unit test on the resolver
wouldn't catch a regression where the executor stops looking at the
session, or vice-versa.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ECHO_DATA_DIR at a scratch dir so the resolver roots
    land under tmp_path instead of CWD. Keeps tests hermetic."""
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def mk_session(data_dir: Path):
    """Build a Session with a stub Agent carrying the right capability
    flag. Returns a factory so each test picks its own tier.

    We bypass the real agent loader: it reads from disk and runs the
    full profile pipeline. For scope tests we only care about two
    Agent attributes — ``agent_id`` and ``capabilities`` — so a
    minimal stand-in keeps the tests fast and decoupled from profile
    file layouts changing."""
    from runtime.platform.process.session import Session

    class _StubAgent:
        def __init__(self, agent_id: str, caps: dict | None = None):
            self.agent_id = agent_id
            self.capabilities = dict(caps or {})

    def _make(
        agent_id: str = "coder",
        caps: dict | None = None,
        mode: str = "chat",
        team_id: str | None = None,
        extra_workspaces: list[str] | None = None,
        thread_id: str = "t-test",
    ) -> Session:
        meta: dict = {"mode": mode}
        if team_id:
            meta["team_id"] = team_id
        if extra_workspaces is not None:
            meta["extra_workspaces"] = extra_workspaces
        return Session(
            actor="u-test",
            agent=_StubAgent(agent_id, caps),
            thread_id=thread_id,
            metadata=meta,
        )

    return _make


# ═══════════════════════════════════════════════════════════
# 1. chat tier — own workspace only
# ═══════════════════════════════════════════════════════════


class TestChatTier:
    def test_coder_chat_allows_own_workspace(self, mk_session, data_dir):
        from runtime.platform.process.scope import resolve_write_scope

        sess = mk_session(
            agent_id="coder",
            caps={"code_mode_unlock": True},  # unlock irrelevant at chat tier
            mode="chat",
        )
        scope = resolve_write_scope(sess)

        assert scope.mode == "chat"
        assert len(scope.roots) == 1
        assert scope.primary == (data_dir / "workspaces" / "t-test" / "output" / "final")
        # File inside own workspace → allowed
        own = scope.primary / "draft.py"
        assert scope.allows(own)

    def test_coder_chat_rejects_system_paths(self, mk_session):
        from runtime.platform.process.scope import resolve_write_scope

        sess = mk_session(mode="chat")
        scope = resolve_write_scope(sess)

        # Absolute system path → rejected
        assert not scope.allows("/etc/passwd")
        assert not scope.allows("/tmp/evil.sh")
        # Windows system path — just another absolute path the scope doesn't own
        assert not scope.allows("C:\\Windows\\System32\\drivers\\etc\\hosts")

    def test_chat_extras_ignored_even_when_provided(self, mk_session):
        """Extras in metadata should NOT leak into chat tier. The tier
        is what gates them, not the presence of the list — this is
        what prevents a persisted ``extra_workspaces`` entry from
        unintentionally expanding scope after the user drops back to
        chat mode."""
        from runtime.platform.process.scope import resolve_write_scope

        sess = mk_session(
            mode="chat",
            caps={"code_mode_unlock": True},
            extra_workspaces=["/opt/project"],
        )
        scope = resolve_write_scope(sess)

        assert scope.mode == "chat"
        assert not scope.allows("/opt/project/file.txt")


# ═══════════════════════════════════════════════════════════
# 2. team tier — own + teams/<id>
# ═══════════════════════════════════════════════════════════


class TestTeamTier:
    def test_team_adds_team_workspace(self, mk_session, data_dir):
        from runtime.platform.process.scope import resolve_write_scope

        sess = mk_session(mode="team", team_id="team-alpha")
        scope = resolve_write_scope(sess)

        assert scope.mode == "team"
        assert len(scope.roots) == 2
        # Primary is still the agent's own workspace
        assert scope.roots[0] == (data_dir / "workspaces" / "t-test" / "output" / "final")
        # Team workspace joins
        assert scope.roots[1] == data_dir / "teams" / "team-alpha" / "workspace"

        assert scope.allows(scope.roots[0] / "a.py")
        assert scope.allows(scope.roots[1] / "shared.md")
        assert not scope.allows("/etc/passwd")

    def test_team_mode_without_team_id_degrades(self, mk_session):
        """``team`` mode without a team_id has nowhere to route the
        second root — we must degrade to chat instead of either
        granting access to an undefined team or crashing."""
        from runtime.platform.process.scope import resolve_write_scope

        sess = mk_session(mode="team", team_id=None)
        scope = resolve_write_scope(sess)

        assert scope.mode == "chat"
        assert len(scope.roots) == 1


# ═══════════════════════════════════════════════════════════
# 3. code tier — own + authorized extras (coder only)
# ═══════════════════════════════════════════════════════════


class TestCodeTier:
    def test_code_adds_extra_workspaces(
        self,
        mk_session,
        data_dir: Path,
        tmp_path: Path,
    ):
        from runtime.platform.process.scope import resolve_write_scope

        # Extra paths must be absolute AND exist (resolver does a
        # ``resolve()`` call; we materialize the dir so resolve sees it)
        extra = tmp_path / "some-project"
        extra.mkdir()

        sess = mk_session(
            mode="code",
            caps={"code_mode_unlock": True},
            extra_workspaces=[str(extra)],
        )
        scope = resolve_write_scope(sess)

        assert scope.mode == "code"
        assert scope.allows(extra / "src" / "main.py")
        # The isolated artifact output folder remains the default root.
        assert scope.roots[0] == (data_dir / "workspaces" / "t-test" / "output" / "final")

    def test_code_drops_relative_extras(self, mk_session):
        """Non-absolute entries are dropped silently — not errored —
        so one stale config entry doesn't brick the whole turn."""
        from runtime.platform.process.scope import resolve_write_scope

        sess = mk_session(
            mode="code",
            caps={"code_mode_unlock": True},
            extra_workspaces=["relative/path", "", "./x"],
        )
        scope = resolve_write_scope(sess)

        assert scope.mode == "code"
        # Only the primary survived
        assert len(scope.roots) == 1


# ═══════════════════════════════════════════════════════════
# 4. code tier availability — every agent, permission-scoped
# ═══════════════════════════════════════════════════════════


class TestCodeCapabilityAvailability:
    def test_agent_without_legacy_unlock_uses_authorized_code_scope(
        self, mk_session, tmp_path: Path
    ):
        """Code mode is available to every agent and remains constrained to
        the workspace roots explicitly granted by the session."""
        from runtime.platform.process.scope import resolve_write_scope

        extra = tmp_path / "secret-project"
        extra.mkdir()

        sess = mk_session(
            agent_id="vibe_selling",
            caps={},
            mode="code",
            extra_workspaces=[str(extra)],
        )
        scope = resolve_write_scope(sess)

        assert scope.mode == "code"
        assert scope.requested_mode == "code"
        assert scope.allows(extra / "file.py")

    def test_legacy_agent_without_capabilities_uses_authorized_code_scope(
        self,
        mk_session,
        tmp_path: Path,
    ):
        """Legacy Agent objects without a capabilities field follow the same
        session permission model instead of being silently downgraded."""
        from runtime.platform.process.scope import resolve_write_scope
        from runtime.platform.process.session import Session

        class _LegacyAgent:
            def __init__(self):
                self.agent_id = "legacy"
                # No capabilities attribute at all

        extra = tmp_path / "x"
        extra.mkdir()
        sess = Session(
            actor="u",
            agent=_LegacyAgent(),
            thread_id="t",
            metadata={"mode": "code", "extra_workspaces": [str(extra)]},
        )
        scope = resolve_write_scope(sess)

        assert scope.mode == "code"
        assert scope.allows(extra / "a.py")


# ═══════════════════════════════════════════════════════════
# 5. executor integration — Session-bound enforcement
# ═══════════════════════════════════════════════════════════


class TestExecutorEnforcement:
    """The scope resolver on its own only returns a WriteScope; the
    executor is what turns "allowed roots" into "this skill call
    fails". These tests pin the integration so the enforcement
    doesn't silently fall out during a future executor refactor."""

    def _setup(self, tmp_path: Path):
        """Build a registered write skill + executor. Returns
        (executor, budget_factory)."""
        from runtime.execution.suckers import SkillRegistry
        from runtime.execution.suckers.write_skills import register_write_skills
        from runtime.execution.tool_engine import ToolExecutor
        from runtime.memory.journal import InMemoryJournal
        from runtime.safety.auth import TrustEngine

        reg = SkillRegistry()
        register_write_skills(reg)
        return ToolExecutor(
            registry=reg,
            immunity=TrustEngine(trusted_sources=["skill://public/*"]),
            journal=InMemoryJournal(),
        )

    def _budget(self):
        from runtime.platform.models import Budget, BudgetLimits, TaskId

        tid = TaskId(uuid4())
        return tid, Budget(
            task_id=tid,
            limits=BudgetLimits(tokens=1000, usd=0.01),
        )

    def _run(self, executor, args: dict, tid, budget):
        from runtime.platform.models import ArmId, SkillId

        return executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args=args,
            caller="arms/x",
            task_id=tid,
            arm_id=ArmId("x"),
            budget=budget,
        )

    def test_no_session_no_enforcement(self, tmp_path: Path):
        """Direct / programmatic callers without a Session bypass the
        scope ladder — the LLM-chooses-sandbox_dir contract still
        holds. This is the backwards-compat escape hatch for tests
        and CLI usage."""
        executor = self._setup(tmp_path)
        tid, budget = self._budget()

        step = self._run(
            executor,
            {
                "path": "out.txt",
                "content": "direct",
                "sandbox_dir": str(tmp_path),
            },
            tid,
            budget,
        )
        assert step.success
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "direct"

    def test_session_bound_rejects_out_of_scope(
        self,
        tmp_path: Path,
        data_dir: Path,
        mk_session,
    ):
        """With a Session bound, an LLM-supplied sandbox_dir pointing
        outside the scope roots must fail the step — the failure is
        what the user actually experiences when Coder tries to write
        `/etc/passwd` from chat mode."""
        from runtime.platform.process.session import session_scope

        executor = self._setup(tmp_path)
        tid, budget = self._budget()

        escape_target = tmp_path / "outside-workspace"
        escape_target.mkdir()

        sess = mk_session(mode="chat")
        with session_scope(sess):
            step = self._run(
                executor,
                {
                    "path": "pwned.txt",
                    "content": "should-not-write",
                    "sandbox_dir": str(escape_target),
                },
                tid,
                budget,
            )

        # Step must have failed — the executor caught PermissionError
        # and tagged the step rather than letting the write happen.
        assert not step.success
        assert not (escape_target / "pwned.txt").exists()

    def test_session_bound_injects_default_sandbox(
        self,
        data_dir: Path,
        mk_session,
    ):
        """When the LLM omits sandbox_dir entirely, the executor fills
        in the scope's primary root. The skill must write into the
        per-agent/per-thread workspace created on-the-fly."""
        from runtime.platform.process.session import session_scope

        executor = self._setup(data_dir)
        tid, budget = self._budget()

        sess = mk_session(mode="chat", thread_id="t-inject")
        with session_scope(sess):
            step = self._run(
                executor,
                {
                    "path": "hello.txt",
                    "content": "auto-sandbox",
                    # sandbox_dir deliberately omitted
                },
                tid,
                budget,
            )

        assert step.success
        expected = data_dir / "workspaces" / "t-inject" / "output" / "final" / "hello.txt"
        assert expected.read_text(encoding="utf-8") == "auto-sandbox"

    def test_session_bound_allows_in_scope_path(
        self,
        data_dir: Path,
        mk_session,
    ):
        """The sibling of the rejection test: a sandbox_dir that DOES
        land under an allowed root must succeed. Otherwise we'd be
        over-enforcing (every LLM call fails)."""
        from runtime.platform.process.session import session_scope

        executor = self._setup(data_dir)
        tid, budget = self._budget()

        sess = mk_session(mode="chat", thread_id="t-allow")
        allowed = data_dir / "workspaces" / "t-allow" / "output" / "final"
        allowed.mkdir(parents=True, exist_ok=True)

        with session_scope(sess):
            step = self._run(
                executor,
                {
                    "path": "ok.txt",
                    "content": "within-scope",
                    "sandbox_dir": str(allowed),
                },
                tid,
                budget,
            )

        assert step.success
        assert (allowed / "ok.txt").read_text(encoding="utf-8") == "within-scope"


# ═══════════════════════════════════════════════════════════
# 6. sandbox mode — .echo-work isolation
# ═══════════════════════════════════════════════════════════


class TestSandboxMode:
    def test_sandbox_mode_creates_echo_work_primary(
        self,
        mk_session,
        tmp_path: Path,
    ):
        """sandbox_mode='sandbox' should set primary to
        <workspace_path>/.echo-work/<thread_id>/ while keeping
        workspace_path in roots for read access."""
        from runtime.platform.process.scope import resolve_write_scope

        wp = tmp_path / "my-project"
        wp.mkdir()

        sess = mk_session(
            mode="code",
            caps={"code_mode_unlock": True},
            extra_workspaces=[str(wp)],
            thread_id="t-sandbox",
        )
        sess.metadata["workspace_path"] = str(wp)
        sess.metadata["sandbox_mode"] = "sandbox"
        scope = resolve_write_scope(sess)

        assert scope.mode == "code"
        sandboxed = wp / ".echo-work" / "t-sandbox"
        assert scope.primary == sandboxed
        assert scope.allows(sandboxed / "output.py")
        assert scope.allows(wp / "src" / "main.py")

    def test_sandbox_mode_full_uses_workspace_directly(
        self,
        mk_session,
        tmp_path: Path,
    ):
        """sandbox_mode='full' (default) should use workspace_path as primary."""
        from runtime.platform.process.scope import resolve_write_scope

        wp = tmp_path / "my-project"
        wp.mkdir()

        sess = mk_session(
            mode="code",
            caps={"code_mode_unlock": True},
            thread_id="t-full",
        )
        sess.metadata["workspace_path"] = str(wp)
        sess.metadata["sandbox_mode"] = "full"
        scope = resolve_write_scope(sess)

        assert scope.mode == "code"
        assert scope.primary == wp

    def test_sandbox_mode_absent_defaults_to_full(
        self,
        mk_session,
        tmp_path: Path,
    ):
        from runtime.platform.process.scope import resolve_write_scope

        wp = tmp_path / "proj"
        wp.mkdir()

        sess = mk_session(
            mode="code",
            caps={"code_mode_unlock": True},
        )
        sess.metadata["workspace_path"] = str(wp)
        scope = resolve_write_scope(sess)

        assert scope.primary == wp

    def test_sandbox_mode_invalid_value_treated_as_full(
        self,
        mk_session,
        tmp_path: Path,
    ):
        from runtime.platform.process.scope import resolve_write_scope

        wp = tmp_path / "proj"
        wp.mkdir()

        sess = mk_session(
            mode="code",
            caps={"code_mode_unlock": True},
        )
        sess.metadata["workspace_path"] = str(wp)
        sess.metadata["sandbox_mode"] = "bogus"
        scope = resolve_write_scope(sess)

        assert scope.primary == wp


# ═══════════════════════════════════════════════════════════
# 7. symlink escape prevention
# ═══════════════════════════════════════════════════════════


class TestSymlinkEscape:
    def test_symlink_pointing_outside_is_rejected(
        self,
        mk_session,
        data_dir: Path,
        tmp_path: Path,
    ):
        """A symlink inside the workspace that points to an external
        directory must be rejected by scope.allows()."""
        from runtime.platform.process.scope import resolve_write_scope

        sess = mk_session(mode="chat", thread_id="t-sym")
        scope = resolve_write_scope(sess)

        workspace = scope.primary
        workspace.mkdir(parents=True, exist_ok=True)

        outside = tmp_path / "outside-secret"
        outside.mkdir()

        link = workspace / "escape-link"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation not supported")

        target_via_link = link / "secret.txt"
        assert not scope.allows(target_via_link)


# ═══════════════════════════════════════════════════════════
# 8. extra_workspaces validation enhancements
# ═══════════════════════════════════════════════════════════


class TestExtraWorkspacesValidation:
    def test_nonexistent_directory_dropped(self, mk_session, tmp_path: Path):
        from runtime.platform.process.scope import resolve_write_scope

        nonexistent = tmp_path / "does-not-exist"

        sess = mk_session(
            mode="code",
            caps={"code_mode_unlock": True},
            extra_workspaces=[str(nonexistent)],
        )
        scope = resolve_write_scope(sess)

        assert not scope.allows(nonexistent / "file.py")

    def test_overlapping_subpath_deduplicated(self, mk_session, tmp_path: Path):
        from runtime.platform.process.scope import resolve_write_scope

        parent = tmp_path / "project"
        parent.mkdir()
        child = parent / "subdir"
        child.mkdir()

        sess = mk_session(
            mode="code",
            caps={"code_mode_unlock": True},
            extra_workspaces=[str(parent), str(child)],
        )
        scope = resolve_write_scope(sess)

        parent_in_roots = any(r == parent for r in scope.roots)
        child_in_roots = any(r == child for r in scope.roots)
        assert parent_in_roots
        assert not child_in_roots
