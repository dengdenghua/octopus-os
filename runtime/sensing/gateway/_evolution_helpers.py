from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

try:
    from fastapi import HTTPException
except ImportError:
    HTTPException = None  # type: ignore[assignment,misc]

from runtime.sensing.gateway._evolution_models import ScorecardGapQueueBody

if TYPE_CHECKING:
    from fastapi import Request


def _scorecard_gap_priority(gap: int) -> str:
    if gap >= 10:
        return "P0"
    if gap >= 5:
        return "P1"
    return "P2"


def _actor_from_request(request: Any) -> str:
    return str(getattr(getattr(request, "state", None), "actor_id", "") or "local_operator")


def _validate_kimi_swarm_real_provider_request(body: Any) -> None:
    try:
        from runtime.safety.evolution import kimi_swarm_load_test as load_test_module
    except Exception:
        load_test_module = None  # type: ignore[assignment]
    if not bool(getattr(body, "confirm_real_provider", False)):
        raise HTTPException(
            status_code=400,
            detail="confirm_real_provider=true is required for real provider load tests",
        )
    step_count = int(getattr(body, "step_count", 0) or 0)
    max_provider_calls = int(getattr(body, "max_provider_calls", 0) or 0)
    estimated_max_tokens = int(getattr(body, "estimated_max_tokens", 0) or 0)
    max_concurrency = int(getattr(body, "max_concurrency", 0) or 0)
    if max_concurrency > 64:
        raise HTTPException(
            status_code=400,
            detail="real provider load tests cap max_concurrency at 64",
        )
    if load_test_module is not None:
        preflight = load_test_module.build_kimi_swarm_load_test_preflight(
            config=load_test_module.KimiSwarmLoadTestConfig(
                session_id=getattr(body, "session_id", "kimi-swarm-load-test"),
                provider_id=getattr(body, "provider_id", "dry_run"),
                model=getattr(body, "model", "dry-run-swarm"),
                agent_count=int(getattr(body, "agent_count", 300) or 300),
                step_count=step_count,
                max_concurrency=max_concurrency,
                real_provider=True,
                confirm_real_provider=True,
                max_provider_calls=max_provider_calls,
                estimated_max_tokens=estimated_max_tokens,
                stage_id=str(getattr(body, "stage_id", "auto") or "auto"),
                resume_from_session_id=str(
                    getattr(body, "resume_from_session_id", "") or "",
                ),
                resume_step_ranges=tuple(
                    getattr(body, "resume_step_ranges", []) or [],
                ),
            ),
            provider_configured=True,
        )
        if preflight.get("blocking_failures"):
            first = preflight["blocking_failures"][0]
            raise HTTPException(
                status_code=400,
                detail=f"kimi swarm load-test preflight failed: {first.get('id')}",
            )
    elif max_provider_calls < step_count or estimated_max_tokens < step_count:
        raise HTTPException(
            status_code=400,
            detail="provider budgets must cover every requested step",
        )


def _validate_kimi_swarm_quota_probe_request(body: Any) -> None:
    if not bool(getattr(body, "confirm_real_provider", False)):
        raise HTTPException(
            status_code=400,
            detail="confirm_real_provider=true is required for quota probes",
        )
    if not _kimi_swarm_provider_configured(str(getattr(body, "model", "") or "")):
        raise HTTPException(
            status_code=400,
            detail=(
                "no custom model router configured for quota probe; "
                "add data/custom_models.json first"
            ),
        )


def _kimi_swarm_provider_caller(model: str) -> Any:
    try:
        from runtime.sensing.model_router.openai_router import (
            build_fallback_router_from_custom_models,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    router = build_fallback_router_from_custom_models(str(model or "").strip())
    if router is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "no custom model router configured for real provider load test; "
                "add data/custom_models.json first"
            ),
        )

    def _call(request: Any) -> Any:
        return router.call(request)

    return _call


def _kimi_swarm_provider_configured(model: str) -> bool:
    try:
        from runtime.sensing.model_router.openai_router import (
            build_fallback_router_from_custom_models,
        )
    except Exception:
        return False
    return build_fallback_router_from_custom_models(str(model or "").strip()) is not None


def _resolve_api_base_url(value: str | None, *, request: Request | None) -> str:
    explicit = _normalize_api_base_url(value)
    if explicit:
        return explicit
    for env_name in (
        "ECHO_INTERNAL_GATEWAY_BASE_URL",
        "ECHO_BACKEND_BASE_URL",
        "VITE_BACKEND_BASE_URL",
    ):
        from_env = _normalize_api_base_url(os.environ.get(env_name))
        if from_env:
            return from_env
    if request is not None:
        from_request = _normalize_api_base_url(str(request.base_url))
        if from_request:
            return from_request
    return "http://127.0.0.1:8000"


def _normalize_api_base_url(value: str | None) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return text


def _scorecard_gap_text(row: dict[str, Any], *, reason: str) -> str:
    raw_scores = row.get("scores")
    scores = raw_scores if isinstance(raw_scores, dict) else {}
    raw_adjusted = row.get("evidence_adjusted_scores")
    adjusted = raw_adjusted if isinstance(raw_adjusted, dict) else {}
    actions = [str(action) for action in row.get("echo_next_actions") or [] if action]
    lines = [
        f"Real baseline gap for `{row.get('title') or row.get('id')}`.",
        (
            f"Echo baseline: {scores.get('echo', 0)}; "
            f"target gap: {row.get('echo_gap_to_target', 0)}; "
            f"effective gap: {row.get('echo_gap_to_effective_target', 0)}."
        ),
        (
            "Best external: "
            f"{row.get('best_external_competitor', 'unknown')} "
            f"{row.get('best_external_score', 0)}; "
            f"surpass target: {row.get('surpass_target_score', 0)}; "
            f"surpass gap: {row.get('echo_gap_to_surpass', 0)}."
        ),
    ]
    if adjusted:
        lines.append(
            "Internal evidence-adjusted score: "
            f"{adjusted.get('echo', row.get('echo_evidence_adjusted_score', 0))}."
        )
    if actions:
        lines.append("Next actions:")
        lines.extend(f"- {action}" for action in actions[:3])
    if reason:
        lines.append(f"Reason: {reason[:500]}")
    return "\n".join(lines)


def _queue_agent_scorecard_gaps_impl(body: Any) -> dict[str, Any]:
    try:
        from runtime.memory.learning.review_queue import ReviewQueue
        from runtime.platform.process.paths import app_paths
        from runtime.safety.evolution.agent_competitor_scorecard import (
            compute_agent_competitor_scorecard,
        )

        body = body or ScorecardGapQueueBody()
        report = compute_agent_competitor_scorecard(
            target_score=body.target_score,
        )
        queue = ReviewQueue(app_paths().review_queue_path)
        created = 0
        updated = 0
        items: list[dict[str, Any]] = []
        rows = sorted(
            report.get("echo_focus_gaps") or [],
            key=lambda row: (
                int(row.get("echo_gap_to_effective_target") or 0),
                int(row.get("echo_gap_to_surpass") or 0),
                int(row.get("echo_gap_to_target") or 0),
                int(row.get("weight") or 0),
            ),
            reverse=True,
        )
        if body.dimension_id:
            wanted_dimension = str(body.dimension_id).strip()
            rows = [
                row
                for row in rows
                if isinstance(row, dict) and str(row.get("id") or "") == wanted_dimension
            ]
        rows = rows[: body.limit]
        for row in rows:
            if not isinstance(row, dict):
                continue
            dimension_id = str(row.get("id") or "unknown")
            next_actions = row.get("echo_next_actions")
            result = queue.upsert_item(
                source="agent_scorecard_gap",
                source_kind="scorecard_gap",
                candidate_kind=f"scorecard_gap:{dimension_id}",
                priority=_scorecard_gap_priority(
                    int(row.get("echo_gap_to_effective_target") or 0),
                ),
                target_bucket="scorecard_gap_backlog",
                title=f"Raise {row.get('title') or row.get('id') or 'scorecard gap'}",
                text=_scorecard_gap_text(row, reason=body.reason),
                metadata={
                    "schema": "echo.agent_scorecard_gap.v1",
                    "dimension_id": row.get("id"),
                    "title": row.get("title"),
                    "target_score": body.target_score,
                    "gap": row.get("echo_gap_to_target"),
                    "effective_target_score": row.get("effective_target_score"),
                    "gap_to_effective_target": row.get(
                        "echo_gap_to_effective_target",
                    ),
                    "surpass_target_score": row.get("surpass_target_score"),
                    "gap_to_surpass": row.get("echo_gap_to_surpass"),
                    "best_external_competitor": row.get(
                        "best_external_competitor",
                    ),
                    "best_external_score": row.get("best_external_score"),
                    "echo_surpasses_best_external": row.get(
                        "echo_surpasses_best_external",
                    ),
                    "scores": row.get("scores"),
                    "evidence_adjusted_scores": row.get(
                        "evidence_adjusted_scores",
                    ),
                    "echo_evidence_adjusted_score": row.get(
                        "echo_evidence_adjusted_score",
                    ),
                    "operator_drilldown": row.get("operator_drilldown"),
                    "next_actions": next_actions,
                    "remediation": {
                        "schema": "echo.scorecard_gap_remediation.v1",
                        "dimension_id": dimension_id,
                        "status": "queued",
                        "primary_action": (
                            str(next_actions[0])
                            if isinstance(next_actions, list) and next_actions
                            else ""
                        ),
                        "evidence_checklist": row.get(
                            "echo_evidence_checklist",
                        ),
                        "operator_drilldown": row.get("operator_drilldown"),
                    },
                    "scorecard_policy": report.get("scorecard_policy"),
                },
                tags=[
                    "scorecard_gap",
                    "real_baseline",
                    dimension_id,
                ],
            )
            created += int(result.get("created") or 0)
            updated += int(result.get("updated") or 0)
            items.extend(result.get("items") or [])

        return {
            "ok": True,
            "schema": "echo.agent_scorecard_gap_queue.v1",
            "created": created,
            "updated": updated,
            "total": created + updated,
            "items": items,
            "scorecard": {
                "target_score": report.get("target_score"),
                "overall": report.get("overall"),
                "verdict": report.get("verdict"),
                "evidence_adjusted_overall": report.get(
                    "evidence_adjusted_overall",
                ),
                "below_target_count": len(report.get("echo_below_target") or []),
                "external_gap_count": len(report.get("echo_external_gap_dimensions") or []),
                "focus_gap_count": len(report.get("echo_focus_gaps") or []),
                "surpass_summary": report.get("surpass_summary"),
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
