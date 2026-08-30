"""Shared UI app state."""

from __future__ import annotations

from pathlib import Path

from runtime.execution.suckers import SkillRegistry
from runtime.memory.journal import InMemoryJournal, Journal, JSONLJournal
from runtime.platform.observability.redactor import Redactor
from runtime.sensing.gateway import StreamingJournal


class AppState:
    """Runtime state shared by UI routers."""

    def __init__(
        self,
        journal_path: Path | None = None,
        *,
        journal: Journal | None = None,
        registry: SkillRegistry | None = None,
        trace_store_path: Path | None = None,
    ) -> None:
        # ``serve`` injects the already-built stack journal. When that journal
        # is JSONL-backed, retain its real path even if the caller did not also
        # duplicate it through ``journal_path``. Health/self-check, thread
        # storage inference and operators must not report a durable learning
        # journal as "in-memory".
        injected_path = getattr(journal, "_path", None) if journal is not None else None
        if journal_path is None and isinstance(injected_path, Path):
            journal_path = injected_path
        self.journal_path = journal_path
        self.trace_store_path = trace_store_path
        if registry is not None:
            self.registry = registry
        else:
            self.registry = SkillRegistry()
            self.registry.set_state_file(Path("data/skill_state.json"))
            from runtime.execution.suckers.builtins import register_all

            register_all(self.registry)
        try:
            from runtime.execution.suckers import load_forged_skills_from_dir
            from runtime.platform.process.paths import app_paths

            load_forged_skills_from_dir(
                app_paths().data_dir / "forged_skills",
                self.registry,
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            from runtime.safety.evolution.runtime_deployment import (
                load_governed_candidate_skills,
            )

            load_governed_candidate_skills(self.registry)
        except Exception:  # noqa: BLE001 - invalid candidates stay unavailable
            pass

        trace_store = None
        if trace_store_path is not None and (
            (journal is None and journal_path) or isinstance(journal, JSONLJournal)
        ):
            from runtime.memory.diagnostics.trace_store import AgentTraceStore

            trace_store = AgentTraceStore(trace_store_path)
            if isinstance(journal, JSONLJournal):
                journal.attach_trace_store(trace_store)
        self.trace_store = trace_store
        self.task_supervisor = None
        try:
            from runtime.platform.process.paths import app_paths
            from runtime.platform.process.task_supervisor import TaskSupervisor

            self.task_supervisor = TaskSupervisor.from_path(app_paths().task_runs_path)
        except Exception:  # noqa: BLE001
            self.task_supervisor = None

        base_journal = (
            journal
            if journal is not None
            else (
                JSONLJournal(journal_path, trace_store=trace_store, redactor=Redactor())
                if journal_path
                else InMemoryJournal()
            )
        )
        if isinstance(base_journal, StreamingJournal):
            self.journal = base_journal
        else:
            self.journal = StreamingJournal(base_journal)
