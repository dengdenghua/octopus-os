from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from runtime.adapters.instrumentation import trace_stage
from runtime.execution.suckers import SkillNotFound, SkillRegistry
from runtime.memory.journal import Journal
from runtime.platform.models import (
    ArmId,
    ExecutionResult,
    SkillId,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
    new_id,
    now_utc,
)

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class IntelSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    max_results: int = Field(default=5, gt=0, le=50)
    fetch_top_n: int = Field(default=0, ge=0, le=10)
    frequency_seconds: int = Field(default=3600, gt=0)
    tags: list[str] = Field(default_factory=list)


class IntelRunReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    started_at: Any
    completed_at: Any
    sources_scanned: int
    searches_succeeded: int
    searches_failed: int
    urls_fetched: int
    events_written: int


# ═══════════════════════════════════════════════════════════
# IntelCollector
# ═══════════════════════════════════════════════════════════


@dataclass
class CollectorConfig:
    default_interval_seconds: int = 3600
    jitter_seconds: int = 60
    stop_after_n_runs: int | None = None


class IntelCollector:
    CALLER_PREFIX = "intel_collector"

    def __init__(
        self,
        sources: list[IntelSource],
        journal: Journal,
        registry: SkillRegistry,
        config: CollectorConfig | None = None,
    ) -> None:
        self.sources = list(sources)
        self.journal = journal
        self.registry = registry
        self.config = config or CollectorConfig()

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_count = 0

    def run_once(self) -> IntelRunReport:
        with trace_stage("regeneration.intel_collector.run_once") as span:
            span.set_attribute("echo.intel.sources_count", len(self.sources))

            started = now_utc()
            searches_ok = 0
            searches_fail = 0
            urls_fetched = 0
            events_written = 0
            collected_steps: list[Step] = []

            task_id = TaskId(new_id())

            for i, source in enumerate(self.sources):
                if self._stop_event.is_set():
                    break
                step, fetch_steps = self._scan_one_source(i, source, task_id)
                collected_steps.append(step)
                collected_steps.extend(fetch_steps)
                events_written += 1 + len(fetch_steps)
                urls_fetched += len(fetch_steps)

                if step.success:
                    searches_ok += 1
                else:
                    searches_fail += 1

                self.journal.write_step(
                    task_id=task_id,
                    arm_id=ArmId(f"{self.CALLER_PREFIX}/{source.source_id}"),
                    step=step,
                )
                for fs in fetch_steps:
                    self.journal.write_step(
                        task_id=task_id,
                        arm_id=ArmId(f"{self.CALLER_PREFIX}/{source.source_id}"),
                        step=fs,
                    )

            traj = Trajectory(
                task_id=task_id,
                arm_id=ArmId(f"{self.CALLER_PREFIX}/batch"),
                strategy_id="intel_scan",
                steps=collected_steps,
                outcome=TrajectoryOutcome(
                    success=(searches_fail == 0),
                ),
            )
            self.journal.write_trajectory(traj)
            events_written += 1

            completed = now_utc()
            self._run_count += 1

            return IntelRunReport(
                started_at=started,
                completed_at=completed,
                sources_scanned=len(self.sources),
                searches_succeeded=searches_ok,
                searches_failed=searches_fail,
                urls_fetched=urls_fetched,
                events_written=events_written,
            )

    def _scan_one_source(
        self,
        idx: int,
        source: IntelSource,
        task_id: TaskId,
    ) -> tuple[Step, list[Step]]:
        # 1. web_search
        search_call = ToolCall(
            caller=f"{self.CALLER_PREFIX}/{source.source_id}",
            sucker_id=SkillId("web_search"),
            args={"query": source.query, "max_results": source.max_results},
        )

        search_output: Any
        search_status = "success"
        search_error: str | None = None

        try:
            handler = self.registry.get("web_search").handler
            search_output = handler(query=source.query, max_results=source.max_results)
            if isinstance(search_output, dict) and "error" in search_output:
                search_status = "failed"
                search_error = str(search_output["error"])
        except SkillNotFound:
            search_output = {"error": "web_search skill not registered"}
            search_status = "failed"
            search_error = "web_search not registered"
        except Exception as e:  # noqa: BLE001
            search_output = {"error": f"{type(e).__name__}: {e}"}
            search_status = "failed"
            search_error = str(e)

        search_step = Step(
            step_id=idx * 100,
            node_id=f"intel_{source.source_id}_search",
            action=search_call,
            result=ExecutionResult(
                call_id=search_call.call_id,
                status=search_status,  # type: ignore[arg-type]
                output=search_output,
                error_type=search_error,
                stderr_tags=source.tags,
            ),
        )

        fetch_steps: list[Step] = []
        if (
            source.fetch_top_n > 0
            and search_status == "success"
            and isinstance(search_output, dict)
            and "results" in search_output
        ):
            for j, r in enumerate(search_output["results"][: source.fetch_top_n]):
                url = r.get("url", "")
                if not url:
                    continue
                fetch_steps.append(self._fetch_one(idx, j, url, source))

        return search_step, fetch_steps

    def _fetch_one(
        self,
        source_idx: int,
        fetch_idx: int,
        url: str,
        source: IntelSource,
    ) -> Step:
        call = ToolCall(
            caller=f"{self.CALLER_PREFIX}/{source.source_id}",
            sucker_id=SkillId("fetch_url"),
            args={"url": url},
        )
        try:
            handler = self.registry.get("fetch_url").handler
            output = handler(url=url)
            status = "success" if "error" not in output else "failed"
            err = output.get("error") if isinstance(output, dict) else None
        except SkillNotFound:
            output = {"error": "fetch_url skill not registered"}
            status = "failed"
            err = "fetch_url not registered"
        except Exception as e:  # noqa: BLE001
            output = {"error": f"{type(e).__name__}: {e}"}
            status = "failed"
            err = str(e)

        return Step(
            step_id=source_idx * 100 + fetch_idx + 1,
            node_id=f"intel_{source.source_id}_fetch_{fetch_idx}",
            action=call,
            result=ExecutionResult(
                call_id=call.call_id,
                status=status,  # type: ignore[arg-type]
                output=output,
                error_type=err,
            ),
        )

    def start_background(self, tick_seconds: int = 60) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("collector already running")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._background_loop,
            args=(tick_seconds,),
            daemon=True,
            name="intel-collector",
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def run_count(self) -> int:
        return self._run_count

    def _background_loop(self, tick_seconds: int) -> None:
        last_runs: dict[str, float] = {}
        while not self._stop_event.is_set():
            now = time.time()
            to_run: list[IntelSource] = [
                s
                for s in self.sources
                if now - last_runs.get(s.source_id, 0) >= s.frequency_seconds
            ]
            if to_run:
                with contextlib.suppress(Exception):
                    self.run_once()
                for s in to_run:
                    last_runs[s.source_id] = time.time()

            if (
                self.config.stop_after_n_runs is not None
                and self._run_count >= self.config.stop_after_n_runs
            ):
                self._stop_event.set()
                break
            self._stop_event.wait(timeout=tick_seconds)
