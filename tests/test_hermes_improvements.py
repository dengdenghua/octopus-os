"""Tests for the lifecycle and discovery subsystems:

1. Progressive Disclosure — Skill.effective_summary + SkillRegistry.index()
2. SkillCurator — stale/archive lifecycle + record_use
3. ShareGPT trajectory export — Journal.export_trajectories()
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from runtime.execution.suckers.registry import Skill, SkillRegistry

# ═══════════════════════════════════════════════════════════════
# 1. Progressive Disclosure
# ═══════════════════════════════════════════════════════════════


def _dummy_handler(**_kw):
    return {}


def _make_skill(name: str, description: str = "", summary: str = "") -> Skill:
    return Skill(
        name=name,
        description=description,
        summary=summary,
        trusted_source=f"builtin://{name}",
        handler=_dummy_handler,
    )


class TestEffectiveSummary:
    def test_explicit_summary_returned_as_is(self):
        s = _make_skill("x", description="Long desc.", summary="Short")
        assert s.effective_summary == "Short"

    def test_falls_back_to_first_sentence(self):
        s = _make_skill("x", description="First sentence. Second sentence.")
        assert s.effective_summary == "First sentence."

    def test_falls_back_to_first_newline(self):
        s = _make_skill("x", description="Line one\nLine two")
        assert s.effective_summary == "Line one"

    def test_caps_at_100_chars_with_ellipsis(self):
        long = "A" * 150
        s = _make_skill("x", description=long)
        assert len(s.effective_summary) <= 103  # 100 + "…"
        assert s.effective_summary.endswith("…")

    def test_empty_description_returns_empty(self):
        s = _make_skill("x", description="")
        assert s.effective_summary == ""


class TestSkillRegistryIndex:
    def test_index_returns_compact_entries(self):
        reg = SkillRegistry()
        reg.register(
            _make_skill(
                "web_search",
                description="Search the web for information. Returns results.",
                summary="Search the web.",
            ),
            verify_tests=False,
        )
        reg.register(
            _make_skill(
                "exec_shell",
                description="Execute a shell command.",
            ),
            verify_tests=False,
        )
        idx = reg.index()
        assert len(idx) == 2
        names = {e["name"] for e in idx}
        assert names == {"web_search", "exec_shell"}
        # Each entry has the four compact keys only.
        for entry in idx:
            assert set(entry.keys()) == {"name", "summary", "cost_profile", "affinity"}

    def test_index_uses_effective_summary(self):
        reg = SkillRegistry()
        reg.register(
            _make_skill(
                "read_file",
                description="Read a file from disk. Returns content.",
                summary="Read a file.",
            ),
            verify_tests=False,
        )
        idx = reg.index()
        assert idx[0]["summary"] == "Read a file."

    def test_index_excludes_disabled_skills(self):
        reg = SkillRegistry()
        reg.register(_make_skill("a", summary="A"), verify_tests=False)
        reg.register(_make_skill("b", summary="B"), verify_tests=False)
        reg.disable("b")
        idx = reg.index(only_enabled=True)
        assert len(idx) == 1
        assert idx[0]["name"] == "a"

    def test_load_full_description(self):
        reg = SkillRegistry()
        reg.register(
            _make_skill(
                "write_file",
                description="Write content to a file on disk.",
                summary="Write a file.",
            ),
            verify_tests=False,
        )
        full = reg.load_full_description("write_file")
        assert full == "Write content to a file on disk."


# ═══════════════════════════════════════════════════════════════
# 2. SkillCurator
# ═══════════════════════════════════════════════════════════════


def _write_skill(
    skills_dir: Path,
    name: str,
    *,
    last_used_at: datetime | None = None,
    use_count: int = 0,
    status: str = "active",
) -> Path:
    """Write a minimal skill file with frontmatter."""
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = skills_dir / f"{name}.md"
    ts = (last_used_at or datetime.now(UTC)).isoformat()
    path.write_text(
        f"---\nname: {name}\ndescription: Test skill\n"
        f"last_used_at: {ts}\nuse_count: {use_count}\n"
        f"status: {status}\n---\n\n# Body\n",
        encoding="utf-8",
    )
    return path


class TestSkillCurator:
    @pytest.fixture
    def skills_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Redirect _project_root to tmp_path."""
        import runtime.memory.skills_lib.skill_curator as _mod

        monkeypatch.setattr(_mod, "_project_root", lambda: tmp_path)
        return tmp_path / "agents" / "test_agent" / "skills"

    def test_run_pass_marks_stale(self, skills_dir: Path):
        from runtime.memory.skills_lib.skill_curator import SkillCurator

        old_ts = datetime.now(UTC) - timedelta(days=35)
        _write_skill(skills_dir, "old_skill", last_used_at=old_ts)

        curator = SkillCurator("test_agent", stale_days=30, archive_days=90)
        result = curator.run_pass()

        assert result["marked_stale"] == 1
        assert result["archived"] == 0

        # Verify frontmatter updated.
        text = (skills_dir / "old_skill.md").read_text(encoding="utf-8")
        assert "status: stale" in text

    def test_run_pass_archives_very_old(self, skills_dir: Path):
        from runtime.memory.skills_lib.skill_curator import SkillCurator

        very_old = datetime.now(UTC) - timedelta(days=95)
        _write_skill(skills_dir, "dead_skill", last_used_at=very_old)

        curator = SkillCurator("test_agent", stale_days=30, archive_days=90)
        result = curator.run_pass()

        assert result["archived"] == 1
        # Original file removed.
        assert not (skills_dir / "dead_skill.md").exists()
        # Archived copy exists.
        archive = skills_dir / "_archive" / "dead_skill.md"
        assert archive.exists()
        text = archive.read_text(encoding="utf-8")
        assert "status: archived" in text

    def test_run_pass_leaves_recent_skill_alone(self, skills_dir: Path):
        from runtime.memory.skills_lib.skill_curator import SkillCurator

        recent = datetime.now(UTC) - timedelta(days=5)
        _write_skill(skills_dir, "fresh_skill", last_used_at=recent)

        curator = SkillCurator("test_agent", stale_days=30, archive_days=90)
        result = curator.run_pass()

        assert result["marked_stale"] == 0
        assert result["archived"] == 0

    def test_restore_archived_skill(self, skills_dir: Path):
        from runtime.memory.skills_lib.skill_curator import SkillCurator

        very_old = datetime.now(UTC) - timedelta(days=95)
        _write_skill(skills_dir, "restore_me", last_used_at=very_old)

        curator = SkillCurator("test_agent", stale_days=30, archive_days=90)
        curator.run_pass()

        assert not (skills_dir / "restore_me.md").exists()
        ok = curator.restore("restore_me")
        assert ok is True
        assert (skills_dir / "restore_me.md").exists()
        text = (skills_dir / "restore_me.md").read_text(encoding="utf-8")
        assert "status: active" in text

    def test_record_use_bumps_count(self, skills_dir: Path):
        from runtime.memory.skills_lib.skill_curator import record_use

        _write_skill(skills_dir, "used_skill", use_count=3)
        record_use("test_agent", "used_skill")

        text = (skills_dir / "used_skill.md").read_text(encoding="utf-8")
        assert "use_count: 4" in text
        assert "last_used_at:" in text

    def test_record_use_resets_stale_status(self, skills_dir: Path):
        from runtime.memory.skills_lib.skill_curator import record_use

        old_ts = datetime.now(UTC) - timedelta(days=35)
        _write_skill(
            skills_dir,
            "stale_skill",
            last_used_at=old_ts,
            status="stale",
        )
        record_use("test_agent", "stale_skill")

        text = (skills_dir / "stale_skill.md").read_text(encoding="utf-8")
        assert "status: active" in text

    def test_list_stale(self, skills_dir: Path):
        from runtime.memory.skills_lib.skill_curator import SkillCurator

        old_ts = datetime.now(UTC) - timedelta(days=35)
        _write_skill(skills_dir, "stale_one", last_used_at=old_ts)
        _write_skill(skills_dir, "fresh_one")

        curator = SkillCurator("test_agent", stale_days=30, archive_days=90)
        curator.run_pass()

        stale = curator.list_stale()
        assert len(stale) == 1
        assert stale[0]["name"] == "stale_one"


# ═══════════════════════════════════════════════════════════════
# 3. ShareGPT Trajectory Export
# ═══════════════════════════════════════════════════════════════


class TestExportTrajectories:
    def test_empty_journal_returns_empty(self):
        from runtime.memory.journal import InMemoryJournal

        j = InMemoryJournal()
        result = j.export_trajectories()
        assert result == []

    def test_raw_format_returns_all_events(self):
        from uuid import uuid4

        from runtime.memory.journal import InMemoryJournal, StepEvent
        from runtime.platform.models import CostEntry, ExecutionResult, Step, ToolCall

        tid = uuid4()
        call_id = uuid4()
        call = ToolCall(
            call_id=call_id, caller="arm:test", sucker_id="exec_shell", args={"cmd": "ls"}
        )
        result = ExecutionResult(
            call_id=call_id, status="success", output="file.txt", cost=CostEntry(tokens=10, usd=0.0)
        )
        step = Step(step_id=1, node_id="n1", action=call, result=result)

        j = InMemoryJournal()
        j.write(StepEvent(task_id=tid, arm_id="arm-1", step=step))

        raw = j.export_trajectories(format="raw")
        assert len(raw) == 1
        assert raw[0]["event_type"] == "step"

    def test_sharegpt_format_groups_by_task(self):
        from uuid import uuid4

        from runtime.memory.journal import InMemoryJournal, StepEvent
        from runtime.platform.models import CostEntry, ExecutionResult, Step, ToolCall

        j = InMemoryJournal()
        tid = uuid4()

        def _step(n: int) -> StepEvent:
            cid = uuid4()
            call = ToolCall(call_id=cid, caller="arm:test", sucker_id=f"skill_{n}", args={"n": n})
            res = ExecutionResult(
                call_id=cid,
                status="success",
                output=f"out_{n}",
                cost=CostEntry(tokens_in=3, tokens_out=2, usd=0.0),
            )
            return StepEvent(
                task_id=tid,
                arm_id="arm-1",
                step=Step(step_id=n, node_id=f"n{n}", action=call, result=res),
            )

        j.write(_step(1))
        j.write(_step(2))

        records = j.export_trajectories(format="sharegpt")
        assert len(records) == 1
        rec = records[0]
        assert rec["task_id"] == str(tid)
        assert len(rec["conversations"]) == 4
        assert rec["conversations"][0]["from"] == "tool"
        assert "skill_1" in rec["conversations"][0]["value"]
        assert rec["conversations"][1]["from"] == "tool_result"
        assert (
            rec["total_cost_tokens"] == 20
        )  # 2 steps × (3+2) × 2 (tokens_in+tokens_out counted separately)

    def test_sharegpt_filter_by_task_id(self):
        from uuid import uuid4

        from runtime.memory.journal import InMemoryJournal, StepEvent
        from runtime.platform.models import CostEntry, ExecutionResult, Step, ToolCall

        j = InMemoryJournal()
        tid_a = uuid4()
        tid_b = uuid4()

        for tid in (tid_a, tid_b):
            cid = uuid4()
            call = ToolCall(call_id=cid, caller="arm:x", sucker_id="s", args={})
            res = ExecutionResult(
                call_id=cid, status="success", output="ok", cost=CostEntry(tokens=1, usd=0.0)
            )
            j.write(
                StepEvent(
                    task_id=tid,
                    arm_id="arm-1",
                    step=Step(step_id=1, node_id="n1", action=call, result=res),
                )
            )

        records = j.export_trajectories(task_id=tid_a, format="sharegpt")
        assert len(records) == 1
        assert records[0]["task_id"] == str(tid_a)
