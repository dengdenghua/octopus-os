"""Load-test execution engine for the Kimi Swarm load-test family.

Split out of the former ~1960-line kimi_swarm_load_test.py — the single
largest function in that file (dispatches the concurrent per-step provider
calls and persists replay evidence). Depends on kimi_swarm_types.py,
kimi_swarm_failure_taxonomy.py, and kimi_swarm_resume_planner.py (turning a
``provider_full_reference_resume`` config into the execution indices to
run); used by kimi_swarm_load_test.py's public API.
"""

from __future__ import annotations

import concurrent.futures as _cf
import time
from typing import Any

from runtime.memory.control_sessions import ControlSessionStore
from runtime.platform.models.llm import Message, ModelRequest
from runtime.platform.process.paths import app_paths

from .kimi_swarm_failure_taxonomy import (
    _failure_summary_from_step_records,
    _is_retryable_provider_error,
)
from .kimi_swarm_resume_planner import _resume_execution_indices
from .kimi_swarm_types import (
    _PROVIDER_STEP_ATTEMPTS,
    _SCHEMA,
    _STEP_SCHEMA,
    _SUMMARY_EVIDENCE_SCHEMA,
    KimiSwarmLoadTestConfig,
    ProviderCaller,
    _normalize_counts,
    _resolve_stage,
    _stage_plan,
)


def run_kimi_swarm_load_test(
    *,
    config: KimiSwarmLoadTestConfig | None = None,
    store: ControlSessionStore | None = None,
    provider_caller: ProviderCaller | None = None,
) -> dict[str, Any]:
    """Run a Kimi Swarm reference load test and persist replay evidence.

    The default mode is intentionally a dry-run: it proves the 300-agent /
    4000-step control-session replay, metrics, pagination, and export path
    without spending model tokens. Passing ``real_provider=True`` requires an
    explicit ``provider_caller`` and records every provider call result with the
    same replay schema.
    """
    cfg = config or KimiSwarmLoadTestConfig()
    requested_agent_count, requested_step_count, requested_max_concurrency = _normalize_counts(cfg)
    stage = _resolve_stage(
        config=cfg,
        requested_agent_count=requested_agent_count,
        requested_step_count=requested_step_count,
        requested_max_concurrency=requested_max_concurrency,
    )
    agent_count = int(stage["agent_count"])
    step_count = int(stage["step_count"])
    max_concurrency = int(stage["max_concurrency"])
    control_store = store or ControlSessionStore(app_paths().data_dir / "control_sessions")
    execution_indices = list(range(step_count))
    if str(stage.get("id") or "") == "provider_full_reference_resume":
        execution_indices = _resume_execution_indices(
            cfg,
            control_store=control_store,
            requested_step_count=requested_step_count,
        )
        step_count = len(execution_indices)
        if step_count <= 0:
            raise ValueError("provider_full_reference_resume has no remaining steps")
    if cfg.real_provider and provider_caller is None:
        raise ValueError("real_provider=True requires provider_caller")
    if cfg.real_provider:
        if not cfg.confirm_real_provider:
            raise ValueError("confirm_real_provider=True is required for real_provider runs")
        max_provider_calls = int(cfg.max_provider_calls or 0)
        estimated_max_tokens = int(cfg.estimated_max_tokens or 0)
        if max_provider_calls < step_count:
            raise ValueError(
                "max_provider_calls must be >= executed stage step_count for real_provider runs"
            )
        if estimated_max_tokens <= 0:
            raise ValueError("estimated_max_tokens is required for real_provider runs")
        if estimated_max_tokens < step_count:
            raise ValueError("estimated_max_tokens must cover every provider call in the stage")
    session = control_store.upsert_session(
        session_id=cfg.session_id,
        owner_id="kimi_swarm_load_test",
        owner_label="Kimi Swarm Load Test",
        surface="backend_preview",
        target_id="agent-collaboration",
        status="running",
        metadata={
            "schema": _SCHEMA,
            "provider_id": cfg.provider_id,
            "model": cfg.model,
            "stage_id": stage["id"],
            "stage_title": stage["title"],
            "agent_count": agent_count,
            "step_count": step_count,
            "reference_step_count": requested_step_count,
            "max_concurrency": max_concurrency,
            "requested_agent_count": requested_agent_count,
            "requested_step_count": requested_step_count,
            "requested_max_concurrency": requested_max_concurrency,
            "resume_from_session_id": cfg.resume_from_session_id,
            "resume_step_ranges": list(cfg.resume_step_ranges),
            "real_provider": bool(cfg.real_provider),
            "confirm_real_provider": bool(cfg.confirm_real_provider),
            "record_every_step": bool(cfg.record_every_step),
            "max_provider_calls": int(cfg.max_provider_calls or 0),
            "estimated_max_tokens": int(cfg.estimated_max_tokens or 0),
            "reference": {
                "kimi_subagents": 300,
                "kimi_tool_calls": 4000,
            },
        },
    )
    action = control_store.append_action(
        cfg.session_id,
        action_id=f"{cfg.session_id}:load-test",
        action_type="kimi_swarm_load_test",
        status="running",
        descriptor={
            "schema": _SCHEMA,
            "stage": stage,
            "agent_count": agent_count,
            "step_count": step_count,
            "reference_step_count": requested_step_count,
            "max_concurrency": max_concurrency,
            "requested_agent_count": requested_agent_count,
            "requested_step_count": requested_step_count,
            "requested_max_concurrency": requested_max_concurrency,
            "resume_from_session_id": cfg.resume_from_session_id,
            "resume_step_ranges": list(cfg.resume_step_ranges),
            "provider_id": cfg.provider_id,
            "model": cfg.model,
            "real_provider": bool(cfg.real_provider),
            "confirm_real_provider": bool(cfg.confirm_real_provider),
            "max_provider_calls": int(cfg.max_provider_calls or 0),
            "estimated_max_tokens": int(cfg.estimated_max_tokens or 0),
        },
    )
    started = time.perf_counter()
    step_records: list[dict[str, Any]] = []
    failures = 0
    total_input_tokens = 0
    total_output_tokens = 0
    per_step_output_budget = (
        max(1, int(cfg.estimated_max_tokens or 0) // max(1, step_count))
        if cfg.real_provider
        else None
    )
    control_store.append_evidence(
        cfg.session_id,
        action_id=str(action["action_id"]),
        evidence_id=f"{cfg.session_id}:stage:{stage['id']}",
        kind="log",
        action="kimi_swarm_load_stage_start",
        ok=True,
        summary=(
            f"{stage['title']}: {agent_count} agents / {step_count} steps / "
            f"concurrency {max_concurrency}"
        ),
        detail={
            "schema": "echo.kimi_swarm_load_stage.v1",
            "stage": stage,
            "requested": {
                "agent_count": requested_agent_count,
                "step_count": requested_step_count,
                "max_concurrency": requested_max_concurrency,
            },
        },
    )

    def _run_step(index: int) -> dict[str, Any]:
        agent_index = index % agent_count
        agent_id = f"agent-{agent_index:03d}"
        step_id = f"step-{index:04d}"
        prompt = (
            "Kimi Swarm load-test step. Reply with one compact JSON-like status. "
            f"agent={agent_id} step={index}"
        )
        step_started = time.perf_counter()
        ok = True
        text = "dry-run-ok"
        error = ""
        input_tokens = 0
        output_tokens = 0
        attempts: list[dict[str, Any]] = []
        if cfg.real_provider:
            for attempt in range(1, _PROVIDER_STEP_ATTEMPTS + 1):
                try:
                    response = provider_caller(  # type: ignore[misc]
                        ModelRequest(
                            model=cfg.model,
                            system_provider=cfg.provider_id,
                            max_tokens=per_step_output_budget,
                            messages=[Message(role="user", content=prompt)],
                        )
                    )
                    text = str(getattr(response, "text", "") or "")
                    input_tokens = int(getattr(response, "input_tokens", 0) or 0)
                    output_tokens = int(getattr(response, "output_tokens", 0) or 0)
                    attempts.append(
                        {
                            "attempt": attempt,
                            "ok": True,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        }
                    )
                    if (
                        per_step_output_budget is not None
                        and output_tokens > per_step_output_budget
                    ):
                        ok = False
                        error = (
                            "provider exceeded per-step output budget: "
                            f"{output_tokens}>{per_step_output_budget}"
                        )
                    else:
                        ok = True
                        error = ""
                    break
                except Exception as exc:  # noqa: BLE001 - load tests record isolated failures
                    ok = False
                    error = f"{type(exc).__name__}: {exc}"
                    attempts.append(
                        {
                            "attempt": attempt,
                            "ok": False,
                            "error": error,
                            "retryable": _is_retryable_provider_error(error),
                        }
                    )
                    if attempt >= _PROVIDER_STEP_ATTEMPTS or not _is_retryable_provider_error(
                        error,
                    ):
                        break
                    time.sleep(min(2.0, 0.25 * attempt))
        else:
            input_tokens = max(1, len(prompt) // 4)
            output_tokens = 3
        elapsed_ms = int((time.perf_counter() - step_started) * 1000)
        return {
            "schema": _STEP_SCHEMA,
            "step_id": step_id,
            "step_index": index,
            "agent_id": agent_id,
            "ok": ok,
            "error": error,
            "output_preview": text[:240],
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "max_tokens": per_step_output_budget,
            "attempt_count": len(attempts) if cfg.real_provider else 1,
            "attempts": attempts,
            "elapsed_ms": elapsed_ms,
            "real_provider": bool(cfg.real_provider),
        }

    with _cf.ThreadPoolExecutor(
        max_workers=max_concurrency,
        thread_name_prefix="kimi-swarm-load",
    ) as pool:
        futures = [pool.submit(_run_step, index) for index in execution_indices]
        for future in _cf.as_completed(futures):
            record = future.result()
            step_records.append(record)
            if not bool(record["ok"]):
                failures += 1
            total_input_tokens += int(record["input_tokens"])
            total_output_tokens += int(record["output_tokens"])

    step_records.sort(key=lambda row: int(row["step_index"]))
    recorded_step_evidence_count = 0
    if cfg.record_every_step:
        for record in step_records:
            control_store.append_evidence(
                cfg.session_id,
                action_id=str(action["action_id"]),
                evidence_id=f"{cfg.session_id}:{record['step_id']}",
                kind="log",
                action="kimi_swarm_load_step",
                ok=bool(record["ok"]),
                summary=(
                    f"{record['agent_id']} {record['step_id']} {'ok' if record['ok'] else 'failed'}"
                ),
                detail=record,
            )
            recorded_step_evidence_count += 1

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    success_count = step_count - failures
    failure_summary = _failure_summary_from_step_records(step_records)
    summary = {
        "schema": _SCHEMA,
        "session_id": cfg.session_id,
        "provider_id": cfg.provider_id,
        "model": cfg.model,
        "stage_id": stage["id"],
        "stage_title": stage["title"],
        "stage_plan": _stage_plan(
            agent_count=requested_agent_count,
            step_count=requested_step_count,
            max_concurrency=requested_max_concurrency,
            real_provider=bool(cfg.real_provider),
        ),
        "real_provider": bool(cfg.real_provider),
        "confirm_real_provider": bool(cfg.confirm_real_provider),
        "max_provider_calls": int(cfg.max_provider_calls or 0),
        "estimated_max_tokens": int(cfg.estimated_max_tokens or 0),
        "agent_count": agent_count,
        "step_count": step_count,
        "reference_step_count": requested_step_count,
        "max_concurrency": max_concurrency,
        "requested_agent_count": requested_agent_count,
        "requested_step_count": requested_step_count,
        "requested_max_concurrency": requested_max_concurrency,
        "resume_from_session_id": cfg.resume_from_session_id,
        "resume_step_ranges": list(cfg.resume_step_ranges),
        "record_every_step": bool(cfg.record_every_step),
        "recorded_step_evidence_count": recorded_step_evidence_count,
        "successful_steps": success_count,
        "failed_steps": failures,
        "failure_rate": round(failures / max(1, step_count), 6),
        "failure_summary": failure_summary,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "per_step_output_budget": per_step_output_budget,
        "elapsed_ms": elapsed_ms,
        "meets_kimi_reference": (
            stage["id"] == "provider_full_reference" and agent_count >= 300 and step_count >= 4000
        )
        or (not cfg.real_provider and agent_count >= 300 and step_count >= 4000),
        "provider_backed": bool(cfg.real_provider),
        "control_session_replay": {
            "session_id": cfg.session_id,
            "action_id": action["action_id"],
            "surface": session["surface"],
        },
    }
    control_store.append_evidence(
        cfg.session_id,
        action_id=str(action["action_id"]),
        evidence_id=f"{cfg.session_id}:summary",
        kind="result",
        action="kimi_swarm_load_test_summary",
        ok=failures == 0,
        summary=(
            f"Kimi swarm load test: {success_count}/{step_count} steps across {agent_count} agents"
        ),
        detail={**summary, "schema": _SUMMARY_EVIDENCE_SCHEMA},
    )
    control_store.update_action(
        cfg.session_id,
        str(action["action_id"]),
        status="done" if failures == 0 else "failed",
        result=summary,
        error="" if failures == 0 else f"{failures} step(s) failed",
    )
    control_store.set_session_state(
        cfg.session_id,
        status="idle" if failures == 0 else "paused",
        metadata={
            "last_kimi_swarm_load_test": summary,
        },
    )
    return summary


__all__ = ["run_kimi_swarm_load_test"]
