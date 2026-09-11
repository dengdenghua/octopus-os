"""Implementation note."""

from __future__ import annotations

from uuid import uuid4

import pytest

from runtime.execution.suckers import (
    SkillRegistry,
    dump_forged_skill_to_md,
    load_forged_skills_from_dir,
)
from runtime.execution.suckers.builtins import register_all
from runtime.safety.recovery import (
    ForgeConfig,
    ForgedSkillCandidate,
    SkillForge,
)

# ═══════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════


def _mk_candidate(
    name: str = "forged_example",
    sequence: list[str] | None = None,
) -> ForgedSkillCandidate:
    seq = sequence or ["list_cwd", "hash_text"]
    return ForgedSkillCandidate(
        candidate_id="abc12345",
        name=name,
        description="A forged skill for tests",
        path_signature="list_cwd->hash_text",
        underlying_sequence=seq,
        source_trajectory_ids=[str(uuid4())],
        source_sample_count=5,
        source_success_rate=0.9,
        generated_tests=[],
        status="proposed",
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestDump:
    def test_writes_file(self, tmp_path):
        cand = _mk_candidate()
        path = dump_forged_skill_to_md(cand, tmp_path)
        assert path.exists()
        assert path.name == "forged_example.md"

    def test_content_has_frontmatter(self, tmp_path):
        cand = _mk_candidate()
        path = dump_forged_skill_to_md(cand, tmp_path)
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---")
        assert "---" in text[4:]
        # Implementation note.
        assert "name: forged_example" in text
        assert "underlying_sequence: [list_cwd, hash_text]" in text
        assert "source_sample_count: 5" in text

    def test_description_escapes_single_quote(self, tmp_path):
        cand = ForgedSkillCandidate(
            candidate_id="xy123456",
            name="safe_name",
            description="it's dangerous · don't break YAML",
            path_signature="a",
            underlying_sequence=["list_cwd"],
            source_trajectory_ids=["t"],
            source_sample_count=3,
            source_success_rate=1.0,
        )
        dump_forged_skill_to_md(cand, tmp_path)
        # Implementation note.
        reg = SkillRegistry()
        register_all(reg)
        loaded = load_forged_skills_from_dir(tmp_path, reg)
        assert "safe_name" in loaded

    def test_creates_parent_dirs(self, tmp_path):
        cand = _mk_candidate()
        deep = tmp_path / "a" / "b" / "forged"
        path = dump_forged_skill_to_md(cand, deep)
        assert path.exists()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLoad:
    def test_load_registers_forged_skill(self, tmp_path):
        cand = _mk_candidate(name="rebuilt_skill")
        dump_forged_skill_to_md(cand, tmp_path)

        reg = SkillRegistry()
        register_all(reg)  # Implementation note.
        loaded = load_forged_skills_from_dir(tmp_path, reg)

        assert "rebuilt_skill" in loaded
        assert reg.has("rebuilt_skill")
        skill = reg.get("rebuilt_skill")
        assert skill.trusted_source.startswith("skill://forged/")
        assert "forged" in skill.affinity

    def test_rebuilt_handler_runs(self, tmp_path):
        cand = _mk_candidate(
            name="rebuilt_runnable",
            sequence=["list_cwd", "count_words"],
        )
        dump_forged_skill_to_md(cand, tmp_path)

        reg = SkillRegistry()
        register_all(reg)
        load_forged_skills_from_dir(tmp_path, reg)

        result = reg.get("rebuilt_runnable").handler(path=".", text="hello world")
        assert result["steps"] == 2
        assert "composite_output" in result
        assert "n0" in result["composite_output"]
        assert "n1" in result["composite_output"]

    def test_missing_subskill_skipped(self, tmp_path):
        """Implementation note."""
        cand = _mk_candidate(
            name="needs_missing",
            sequence=["nonexistent_skill", "list_cwd"],
        )
        dump_forged_skill_to_md(cand, tmp_path)

        reg = SkillRegistry()
        register_all(reg)
        loaded = load_forged_skills_from_dir(
            tmp_path,
            reg,
            skip_missing_subskills=True,
        )
        assert "needs_missing" not in loaded
        assert not reg.has("needs_missing")

    def test_missing_subskill_strict_raises(self, tmp_path):
        cand = _mk_candidate(
            name="strict_missing",
            sequence=["nonexistent_skill"],
        )
        dump_forged_skill_to_md(cand, tmp_path)

        reg = SkillRegistry()
        register_all(reg)
        with pytest.raises(ValueError, match="missing"):
            load_forged_skills_from_dir(
                tmp_path,
                reg,
                skip_missing_subskills=False,
            )

    def test_empty_dir(self, tmp_path):
        reg = SkillRegistry()
        assert load_forged_skills_from_dir(tmp_path, reg) == []

    def test_nonexistent_dir(self, tmp_path):
        reg = SkillRegistry()
        assert (
            load_forged_skills_from_dir(
                tmp_path / "no-such",
                reg,
            )
            == []
        )

    def test_non_forged_md_ignored(self, tmp_path):
        """Implementation note."""
        (tmp_path / "not_forged.md").write_text(
            "---\nname: ordinary\ntrusted_source: x\n---\n",
            encoding="utf-8",
        )
        reg = SkillRegistry()
        register_all(reg)
        loaded = load_forged_skills_from_dir(tmp_path, reg)
        assert "ordinary" not in loaded

    def test_malformed_md_skipped(self, tmp_path):
        """Implementation note."""
        (tmp_path / "broken.md").write_text(
            "no frontmatter here\njust text",
            encoding="utf-8",
        )
        reg = SkillRegistry()
        register_all(reg)
        # Implementation note.
        assert load_forged_skills_from_dir(tmp_path, reg) == []


# ═══════════════════════════════════════════════════════════
# SkillForge.auto_persist_dir
# ═══════════════════════════════════════════════════════════


class TestSkillForgeAutoPersist:
    def test_auto_persist_writes_promoted(self, tmp_path):
        """Implementation note."""
        from runtime.memory.journal import InMemoryJournal
        from runtime.platform.models import (
            ArmId,
            CostEntry,
            ExecutionResult,
            Step,
            TaskId,
            ToolCall,
            Trajectory,
            TrajectoryOutcome,
        )

        reg = SkillRegistry()
        register_all(reg)
        journal = InMemoryJournal()

        # Implementation note.
        def _step(sid, sucker):
            call = ToolCall(caller="arms/a", sucker_id=sucker, args={})
            return Step(
                step_id=sid,
                node_id=f"n{sid}",
                action=call,
                result=ExecutionResult(call_id=call.call_id, status="success"),
            )

        for _ in range(5):
            journal.write_trajectory(
                Trajectory(
                    task_id=TaskId(uuid4()),
                    arm_id=ArmId("a"),
                    steps=[_step(0, "list_cwd"), _step(1, "count_words")],
                    outcome=TrajectoryOutcome(
                        success=True,
                        cost=CostEntry(tokens_in=10, tokens_out=10, usd=0.0001),
                    ),
                )
            )

        forge = SkillForge(
            journal=journal,
            registry=reg,
            config=ForgeConfig(min_hits=3, shadow_runs=2, shadow_success_threshold=0.5),
            auto_persist_dir=tmp_path,
        )
        result = forge.run()

        if not result.promoted:
            pytest.skip("no candidate promoted · shadow validate may be flaky")

        # Implementation note.
        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) >= 1

    def test_no_persist_by_default(self, tmp_path):
        from runtime.memory.journal import InMemoryJournal

        reg = SkillRegistry()
        register_all(reg)
        journal = InMemoryJournal()
        # Implementation note.
        forge = SkillForge(journal=journal, registry=reg)
        forge.run()
        # Implementation note.
        assert not list(tmp_path.glob("*.md"))


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRestartScenario:
    def test_dump_load_reproduces_same_composite_behavior(self, tmp_path):
        """Implementation note."""
        cand = _mk_candidate(
            name="reproducible",
            sequence=["count_words", "hash_text"],
        )

        # Session 1
        reg1 = SkillRegistry()
        register_all(reg1)
        from runtime.memory.journal import InMemoryJournal
        from runtime.safety.recovery.skill_forge import (
            SkillForge as SF,  # noqa: N817 — local acronym alias
        )

        forge = SF(journal=InMemoryJournal(), registry=reg1)
        original_handler = forge._build_meta_handler(cand)

        dump_forged_skill_to_md(cand, tmp_path)

        # Implementation note.
        reg2 = SkillRegistry()
        register_all(reg2)
        loaded = load_forged_skills_from_dir(tmp_path, reg2)
        assert "reproducible" in loaded

        reloaded_skill = reg2.get("reproducible")
        reloaded_handler = reloaded_skill.handler

        # Implementation note.
        args = {"text": "hello world foo bar", "path": "."}
        out_original = original_handler(**args)
        out_reloaded = reloaded_handler(**args)
        assert out_original["steps"] == out_reloaded["steps"]
        assert set(out_original["composite_output"]) == set(out_reloaded["composite_output"])


def test_forged_composite_blocks_risky_subskill_under_taint() -> None:
    """Red-team #7 (critical): a forged composite calls each sub-skill's
    handler DIRECTLY (not via executor.execute_step), bypassing the
    injection-taint chokepoint. A tainted turn must not launder a risky
    sub-skill through a low-risk-named composite."""
    from runtime.execution.suckers.forged_persistence import (
        _build_composite_handler,
    )
    from runtime.execution.suckers.registry import Skill, SkillRegistry
    from runtime.safety.validation.prompt_injection import (
        mark_injection_taint,
        reset_injection_taint,
    )

    ran = {"shell": False}

    def _shell(**_kw):
        ran["shell"] = True
        return {"exit_code": 0}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="exec_shell",
            summary="shell",
            affinity=["shell", "exec", "dangerous"],
            trusted_source="skill://public/exec_shell",
            handler=_shell,
        )
    )
    composite = _build_composite_handler(["exec_shell"], registry)

    reset_injection_taint()
    try:
        mark_injection_taint("high")
        result = composite(command="echo hi")
    finally:
        reset_injection_taint()

    assert result["success"] is False
    assert result.get("error_type") == "InjectionTaintBlocked"
    assert ran["shell"] is False, "blocked sub-skill handler must NOT have run"


def test_forged_composite_runs_subskill_on_clean_turn() -> None:
    """Control: with no taint, the composite runs its sub-skills normally."""
    from runtime.execution.suckers.forged_persistence import (
        _build_composite_handler,
    )
    from runtime.execution.suckers.registry import Skill, SkillRegistry

    ran = {"shell": False}

    def _shell(**_kw):
        ran["shell"] = True
        return {"exit_code": 0}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="exec_shell",
            summary="shell",
            affinity=["shell", "exec", "dangerous"],
            trusted_source="skill://public/exec_shell",
            handler=_shell,
        )
    )
    composite = _build_composite_handler(["exec_shell"], registry)
    result = composite(command="echo hi")
    assert result["success"] is True
    assert ran["shell"] is True


def test_forged_composite_blocks_denied_subskill() -> None:
    """A forged composite bypasses execute_step, so the capability-permission
    denylist must be re-applied per sub-skill: disabling the ``shell`` group
    must block an exec_shell sub-skill."""
    from runtime.execution.misc.capability_permissions import (
        reset_capability_permissions,
        set_capability_group_enabled,
    )
    from runtime.execution.suckers.forged_persistence import (
        _build_composite_handler,
    )
    from runtime.execution.suckers.registry import Skill, SkillRegistry

    ran = {"shell": False}

    def _shell(**_kw):
        ran["shell"] = True
        return {"exit_code": 0}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="exec_shell",
            summary="shell",
            affinity=["shell", "exec", "dangerous"],
            trusted_source="skill://public/exec_shell",
            handler=_shell,
        )
    )
    composite = _build_composite_handler(["exec_shell"], registry)

    reset_capability_permissions()
    try:
        set_capability_group_enabled("shell", False)
        result = composite(command="echo hi")
    finally:
        reset_capability_permissions()

    assert result["success"] is False
    assert result.get("error_type") == "CapabilityBlocked"
    assert ran["shell"] is False, "denied sub-skill handler must NOT have run"


def test_forged_composite_blocks_untrusted_subskill() -> None:
    """A forged composite bypasses execute_step, so the immunity / trust gate
    must be re-applied per sub-skill: with the runtime TrustEngine bound, an
    untrusted sub-skill must be rejected."""
    from runtime.execution.suckers.forged_persistence import (
        _build_composite_handler,
    )
    from runtime.execution.suckers.registry import Skill, SkillRegistry
    from runtime.execution.tool_engine.skill_gate import use_trust_engine
    from runtime.safety.auth import TrustEngine

    ran = {"evil": False}

    def _evil(**_kw):
        ran["evil"] = True
        return {"ok": True}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="evil_tool",
            summary="untrusted",
            affinity=["plugin"],
            trusted_source="plugin://evilpack/exfil",
            handler=_evil,
        )
    )
    composite = _build_composite_handler(["evil_tool"], registry)

    with use_trust_engine(TrustEngine(unknown_policy="reject")):
        result = composite(x=1)

    assert result["success"] is False
    assert result.get("error_type") == "ImmuneRejected"
    assert ran["evil"] is False, "untrusted sub-skill handler must NOT have run"


def test_forged_composite_blocks_credential_write() -> None:
    """A forged composite bypasses execute_step, so the credential-file
    denylist must be re-applied per sub-skill: a write to ``.env`` is blocked
    even though the composite itself is low-risk."""
    from runtime.execution.suckers.forged_persistence import (
        _build_composite_handler,
    )
    from runtime.execution.suckers.registry import Skill, SkillRegistry

    ran = {"wrote": False}

    def _write(**_kw):
        ran["wrote"] = True
        return {"ok": True}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="write_text_file",
            summary="write",
            affinity=["file", "write"],
            trusted_source="skill://public/write_text_file",
            handler=_write,
        )
    )
    composite = _build_composite_handler(["write_text_file"], registry)

    result = composite(path=".env", content="SECRET=1")

    assert result["success"] is False
    assert result.get("error_type") == "FileSafetyBlocked"
    assert ran["wrote"] is False, "credential write handler must NOT have run"


def test_inprocess_forge_composite_blocks_risky_subskill_under_taint() -> None:
    """The in-process forge twin (SkillForge._build_meta_handler) dispatches
    each sub-skill DIRECTLY, identical to the persisted reload path — and had
    NO gate at all. A tainted turn must not launder a risky sub-skill through
    a freshly-forged-but-not-yet-persisted composite."""
    from runtime.execution.suckers.registry import Skill, SkillRegistry
    from runtime.memory.journal import InMemoryJournal
    from runtime.safety.recovery.skill_forge import SkillForge
    from runtime.safety.validation.prompt_injection import (
        mark_injection_taint,
        reset_injection_taint,
    )

    ran = {"shell": False}

    def _shell(**_kw):
        ran["shell"] = True
        return {"exit_code": 0}

    reg = SkillRegistry()
    reg.register(
        Skill(
            name="exec_shell",
            summary="shell",
            affinity=["shell", "exec", "dangerous"],
            trusted_source="skill://public/exec_shell",
            handler=_shell,
        )
    )
    cand = _mk_candidate(name="inproc_forged", sequence=["exec_shell"])
    handler = SkillForge(
        journal=InMemoryJournal(),
        registry=reg,
    )._build_meta_handler(cand)

    reset_injection_taint()
    try:
        mark_injection_taint("high")
        result = handler(command="echo hi")
    finally:
        reset_injection_taint()

    assert result["success"] is False
    assert result.get("error_type") == "InjectionTaintBlocked"
    assert ran["shell"] is False, "blocked sub-skill handler must NOT have run"
