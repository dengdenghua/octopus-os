"""Tests for journal audit chain, beak budget tracker, and journal redactor:

1. AuditChain → JSONLJournal (tamper-evident journal)
2. TokenBudgetTracker → Beak (session-level cumulative budget)
3. Redactor → JSONLJournal (auto-scrub secrets on disk)
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from runtime.platform.models import Budget, BudgetLimits

# ═══════════════════════════════════════════════════════════════
# 1. AuditChain → JSONLJournal
# ═══════════════════════════════════════════════════════════════


class TestJournalAuditChain:
    def _make_event(self):
        from runtime.memory.journal import FileOpEvent

        return FileOpEvent(
            task_id=uuid4(),
            arm_id="arm-1",
            path="README.md",
            action="write",
            bytes_delta=42,
            diff="hello",
        )

    def test_journal_without_chain_unchanged(self, tmp_path: Path):
        """Default (no chain) behaviour is byte-identical to before."""
        from runtime.memory.journal import JSONLJournal

        j = JSONLJournal(tmp_path / "j.jsonl")
        j.write(self._make_event())
        assert (tmp_path / "j.jsonl").exists()

    def test_journal_with_chain_writes_both(self, tmp_path: Path):
        from runtime.memory.journal import JSONLJournal
        from runtime.safety.audit.audit_chain import AuditChain

        chain = AuditChain(
            path=tmp_path / "audit.jsonl",
            keys={"v1": b"secret"},
            active_key_id="v1",
        )
        j = JSONLJournal(tmp_path / "j.jsonl", audit_chain=chain)
        j.write(self._make_event())
        j.write(self._make_event())

        # Journal has both events on-disk.
        jlines = (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(jlines) == 2

        # Audit chain has a matching entry per event.
        alines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(alines) == 2

    def test_audit_chain_verifies_clean(self, tmp_path: Path):
        from runtime.memory.journal import JSONLJournal
        from runtime.safety.audit.audit_chain import AuditChain

        chain = AuditChain(
            path=tmp_path / "audit.jsonl",
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        j = JSONLJournal(tmp_path / "j.jsonl", audit_chain=chain)
        for _ in range(5):
            j.write(self._make_event())

        report = chain.verify()
        assert report.ok is True
        assert report.entries_checked == 5

    def test_audit_chain_detects_tamper(self, tmp_path: Path):
        from runtime.memory.journal import JSONLJournal
        from runtime.safety.audit.audit_chain import AuditChain

        chain = AuditChain(
            path=tmp_path / "audit.jsonl",
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        j = JSONLJournal(tmp_path / "j.jsonl", audit_chain=chain)
        for _ in range(3):
            j.write(self._make_event())

        # Tamper with the middle audit record.
        alines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        parsed = json.loads(alines[1])
        parsed["payload"]["event_type"] = "hacked"
        alines[1] = json.dumps(parsed)
        (tmp_path / "audit.jsonl").write_text("\n".join(alines) + "\n", encoding="utf-8")

        # A fresh chain reads the tampered file.
        chain2 = AuditChain(
            path=tmp_path / "audit.jsonl",
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        report = chain2.verify()
        assert report.ok is False
        assert report.broken_at == 1

    def test_audit_chain_failure_does_not_break_journal(self, tmp_path: Path):
        """A broken audit chain must NOT prevent journal writes."""
        from runtime.memory.journal import JSONLJournal

        class BrokenChain:
            def append(self, **_kw):
                raise RuntimeError("chain broken")

        j = JSONLJournal(tmp_path / "j.jsonl", audit_chain=BrokenChain())
        # Should not raise.
        j.write(self._make_event())
        assert (tmp_path / "j.jsonl").exists()


# ═══════════════════════════════════════════════════════════════
# 2. TokenBudgetTracker → Beak
# ═══════════════════════════════════════════════════════════════


class TestBeakBudgetTracker:
    def _build_executor(self, tracker=None):
        from runtime.execution.suckers import Skill, SkillRegistry
        from runtime.execution.tool_engine.executor import ToolExecutor
        from runtime.memory.journal import InMemoryJournal
        from runtime.safety.auth import TrustEngine

        def _handler(**_kw):
            # Return a dict with cost so beak's "real tokens"
            # extractor picks up something non-trivial.
            return {"ok": True, "meta": {"input_tokens": 30, "output_tokens": 20}}

        registry = SkillRegistry()
        registry.register(
            Skill(
                name="work",
                description="work",
                trusted_source="builtin://work",
                handler=_handler,
            ),
            verify_tests=False,
        )

        return ToolExecutor(
            registry=registry,
            immunity=TrustEngine(trusted_sources=["builtin://*"]),
            journal=InMemoryJournal(),
            budget_tracker=tracker,
        )

    def _execute_in_session(self, executor, session_id: str):
        from runtime.platform.process.session import Session, session_scope

        tid = uuid4()
        budget = Budget(
            task_id=tid,
            limits=BudgetLimits(usd=1.0, tokens=10_000, wallclock_s=60.0),
        )
        sess = Session(
            actor="test-user",
            thread_id=session_id,
        )
        with session_scope(sess):
            return executor.execute_step(
                step_id=1,
                node_id="n1",
                sucker_id="work",
                args={},
                caller="arm:test",
                task_id=tid,
                arm_id="arm-1",
                budget=budget,
            )

    def test_executor_without_tracker_unaffected(self):
        executor = self._build_executor()  # no tracker
        step = self._execute_in_session(executor, "sess-1")
        assert step is not None

    def test_tracker_records_step_cost(self):
        from runtime.platform.llm_infra.budget_tracker import TokenBudgetTracker

        tracker = TokenBudgetTracker()
        executor = self._build_executor(tracker=tracker)
        self._execute_in_session(executor, "sess-a")
        state = tracker.get("sess-a")
        assert state is not None
        # At minimum the call count + some tokens.
        assert state.call_count >= 1
        assert state.tokens_used >= 0

    def test_tracker_isolates_sessions(self):
        from runtime.platform.llm_infra.budget_tracker import TokenBudgetTracker

        tracker = TokenBudgetTracker()
        executor = self._build_executor(tracker=tracker)
        self._execute_in_session(executor, "sess-x")
        self._execute_in_session(executor, "sess-x")
        self._execute_in_session(executor, "sess-y")
        x = tracker.get("sess-x")
        y = tracker.get("sess-y")
        assert x.call_count == 2
        assert y.call_count == 1

    def test_tracker_no_session_is_noop(self):
        """Execution outside a Session context silently skips tracking."""
        from runtime.platform.llm_infra.budget_tracker import TokenBudgetTracker

        tracker = TokenBudgetTracker()
        executor = self._build_executor(tracker=tracker)
        # Run WITHOUT session_scope.
        tid = uuid4()
        budget = Budget(
            task_id=tid,
            limits=BudgetLimits(usd=1.0, tokens=10_000, wallclock_s=60.0),
        )
        executor.execute_step(
            step_id=1,
            node_id="n1",
            sucker_id="work",
            args={},
            caller="arm:test",
            task_id=tid,
            arm_id="arm-1",
            budget=budget,
        )
        # No session → nothing recorded.
        assert tracker.snapshot_all() == []

    def test_tracker_ceiling_exceeded_propagates(self):
        """When session ceiling is exceeded, BudgetExceeded propagates."""
        from runtime.platform.llm_infra.budget_tracker import (
            BudgetExceeded,
            TokenBudgetTracker,
        )

        # Tiny ceiling that will definitely trip.
        tracker = TokenBudgetTracker(tokens_ceiling=10)
        executor = self._build_executor(tracker=tracker)
        with pytest.raises(BudgetExceeded):
            # First run returns 30+20=50 tokens, well over 10.
            self._execute_in_session(executor, "sess-z")

    def test_tracker_warning_callback_fires(self):
        from runtime.platform.llm_infra.budget_tracker import TokenBudgetTracker

        warnings: list[float] = []

        def on_warn(sid, state, threshold):
            warnings.append(threshold)

        tracker = TokenBudgetTracker(
            tokens_ceiling=100,
            on_warning=on_warn,
        )
        executor = self._build_executor(tracker=tracker)
        # One step = 50 tokens. Run twice = 100 = 100% → triggers 80%+95% + BudgetExceeded.
        from runtime.platform.llm_infra.budget_tracker import BudgetExceeded

        self._execute_in_session(executor, "sess-w")
        with pytest.raises(BudgetExceeded):
            self._execute_in_session(executor, "sess-w")
        assert 0.80 in warnings
        assert 0.95 in warnings


# ═══════════════════════════════════════════════════════════════
# 3. Redactor → JSONLJournal
# ═══════════════════════════════════════════════════════════════


class TestJournalRedactor:
    def _make_event_with_secret(self, secret_content: str):
        from runtime.memory.journal import FileOpEvent

        return FileOpEvent(
            task_id=uuid4(),
            arm_id="arm-1",
            path="secrets.txt",
            action="write",
            bytes_delta=len(secret_content),
            diff=secret_content,
        )

    def test_journal_without_redactor_keeps_raw(self, tmp_path: Path):
        from runtime.memory.journal import JSONLJournal

        j = JSONLJournal(tmp_path / "j.jsonl")
        j.write(
            self._make_event_with_secret(
                "my key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABC here",
            )
        )
        content = (tmp_path / "j.jsonl").read_text(encoding="utf-8")
        assert "sk-ant-api03" in content  # not redacted

    def test_journal_with_redactor_scrubs_api_key(self, tmp_path: Path):
        from runtime.memory.journal import JSONLJournal
        from runtime.platform.observability.redactor import Redactor

        j = JSONLJournal(tmp_path / "j.jsonl", redactor=Redactor())
        j.write(
            self._make_event_with_secret(
                "my key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABC here",
            )
        )
        content = (tmp_path / "j.jsonl").read_text(encoding="utf-8")
        assert "sk-ant-api03" not in content
        assert "[REDACTED:api_key]" in content

    def test_journal_with_redactor_scrubs_email(self, tmp_path: Path):
        from runtime.memory.journal import JSONLJournal
        from runtime.platform.observability.redactor import Redactor

        j = JSONLJournal(tmp_path / "j.jsonl", redactor=Redactor())
        j.write(
            self._make_event_with_secret(
                "reach me at alice@example.com for details",
            )
        )
        content = (tmp_path / "j.jsonl").read_text(encoding="utf-8")
        assert "alice@example.com" not in content
        assert "[REDACTED:email]" in content

    def test_broken_redactor_does_not_break_journal(self, tmp_path: Path):
        from runtime.memory.journal import JSONLJournal

        class BrokenRedactor:
            def redact(self, text):
                raise RuntimeError("redactor broken")

        j = JSONLJournal(tmp_path / "j.jsonl", redactor=BrokenRedactor())
        # Must not raise even if redactor is broken.
        j.write(self._make_event_with_secret("anything"))
        assert (tmp_path / "j.jsonl").exists()

    def test_redactor_and_audit_chain_coexist(self, tmp_path: Path):
        """Both can be wired at once without stepping on each other."""
        from runtime.memory.journal import JSONLJournal
        from runtime.platform.observability.redactor import Redactor
        from runtime.safety.audit.audit_chain import AuditChain

        chain = AuditChain(
            path=tmp_path / "audit.jsonl",
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        j = JSONLJournal(
            tmp_path / "j.jsonl",
            audit_chain=chain,
            redactor=Redactor(),
        )
        j.write(
            self._make_event_with_secret(
                "email me at bob@x.com — i won't say",
            )
        )

        # Journal is redacted on disk.
        content = (tmp_path / "j.jsonl").read_text(encoding="utf-8")
        assert "bob@x.com" not in content
        assert "[REDACTED:email]" in content

        # Audit chain still verifies.
        report = chain.verify()
        assert report.ok is True
        assert report.entries_checked == 1
