from __future__ import annotations

"""LLM-backed replay for native evolution.

This is the L4 replay layer: unlike static replay and sandbox probes, it
actually asks a model to answer a replay case under the candidate prompt.
When the router returns native tool calls, this module executes a small
deterministic mock-tool set inside an isolated workspace and sends
``tool_result`` blocks back for another model turn.
"""

import json  # noqa: E402
import re  # noqa: E402
import tempfile  # noqa: E402
from dataclasses import asdict, dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from runtime.platform.models.llm import Message, ModelRequest, ToolSpec  # noqa: E402
from runtime.safety.recovery.native_turn_replay import (  # noqa: E402
    TurnReplayCase,
    build_turn_replay_cases,
)


@dataclass(frozen=True, slots=True)
class LLMReplayCaseResult:
    case_id: str
    kind: str
    score: float
    passed: bool
    finish_reason: str = ""
    output_preview: str = ""
    tool_calls: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LLMReplayCandidateReport:
    candidate_id: str
    total: float
    passed: bool
    case_results: list[LLMReplayCaseResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "total": self.total,
            "passed": self.passed,
            "case_results": [result.to_dict() for result in self.case_results],
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class LLMReplayReport:
    candidates: list[LLMReplayCandidateReport] = field(default_factory=list)
    cases: list[TurnReplayCase] = field(default_factory=list)
    enabled: bool = True
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "cases": [case.to_dict() for case in self.cases],
            "enabled": self.enabled,
            "error": self.error,
        }


def replay_llm_candidates(
    candidates: list[Any],
    *,
    router: Any,
    model: str,
    failures: list[dict[str, Any]] | None = None,
    cases: list[TurnReplayCase] | None = None,
    workspace_root: str | Path | None = None,
    max_cases: int = 3,
    max_tool_rounds: int = 2,
    min_case_score: float = 0.62,
) -> LLMReplayReport:
    replay_cases = (
        cases
        if cases is not None
        else build_turn_replay_cases(
            failures=failures,
        )
    )[: max(0, int(max_cases))]
    root_context: Any
    if workspace_root is None:
        root_context = tempfile.TemporaryDirectory(prefix="echo-llm-replay-")
        root = Path(root_context.name)
    else:
        root_context = None
        root = Path(workspace_root)
        root.mkdir(parents=True, exist_ok=True)
    try:
        reports = [
            _replay_candidate(
                candidate,
                replay_cases,
                router=router,
                model=model,
                root=root,
                max_tool_rounds=max_tool_rounds,
                min_case_score=min_case_score,
            )
            for candidate in candidates
        ]
        reports.sort(key=lambda report: (-report.total, report.candidate_id))
        return LLMReplayReport(candidates=reports, cases=replay_cases)
    except Exception as exc:  # noqa: BLE001
        return LLMReplayReport(
            candidates=[],
            cases=replay_cases,
            enabled=True,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if root_context is not None:
            root_context.cleanup()


def _replay_candidate(
    candidate: Any,
    cases: list[TurnReplayCase],
    *,
    router: Any,
    model: str,
    root: Path,
    max_tool_rounds: int,
    min_case_score: float,
) -> LLMReplayCandidateReport:
    candidate_id = str(getattr(candidate, "candidate_id", "") or "")
    prompt = str(getattr(candidate, "prompt", "") or "")
    case_results = [
        _replay_case(
            prompt,
            case,
            router=router,
            model=model,
            workspace=root / _safe_name(candidate_id or "candidate") / _safe_name(case.case_id),
            max_tool_rounds=max_tool_rounds,
            min_case_score=min_case_score,
        )
        for case in cases
    ]
    total = _average(case_results)
    weak = [result.case_id for result in case_results if not result.passed]
    return LLMReplayCandidateReport(
        candidate_id=candidate_id,
        total=total,
        passed=not weak,
        case_results=case_results,
        reasons=(
            [f"llm replay weak cases: {', '.join(weak[:3])}"] if weak else ["llm replay passed"]
        ),
    )


def _replay_case(
    prompt: str,
    case: TurnReplayCase,
    *,
    router: Any,
    model: str,
    workspace: Path,
    max_tool_rounds: int,
    min_case_score: float,
) -> LLMReplayCaseResult:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "case.json").write_text(
        json.dumps(case.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    messages = [
        Message(role="system", content=_system_prompt(prompt)),
        Message(role="user", content=_case_prompt(case)),
    ]
    tool_names: list[str] = []
    text = ""
    finish_reason = ""
    for _round in range(max(1, int(max_tool_rounds)) + 1):
        response = router.call(
            ModelRequest(
                model=model,
                messages=messages,
                max_tokens=1200,
                temperature=0.0,
                tools=_tool_specs(),
            )
        )
        text = str(getattr(response, "text", "") or "")
        finish_reason = str(getattr(response, "finish_reason", "") or "")
        calls = list(getattr(response, "tool_calls", []) or [])
        if not calls:
            break
        messages.append(Message(role="assistant", content=text or ""))
        tool_blocks: list[dict[str, Any]] = []
        for call in calls:
            name = str(getattr(call, "name", "") or "")
            tool_names.append(name)
            tool_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": str(getattr(call, "id", "") or name or "tool"),
                    "content": _execute_mock_tool(
                        name, getattr(call, "input", {}) or {}, workspace
                    ),
                }
            )
        messages.append(Message(role="user", content=tool_blocks))
    score, reason = _score_llm_output(
        case,
        text=text,
        finish_reason=finish_reason,
        tool_names=tool_names,
    )
    return LLMReplayCaseResult(
        case_id=case.case_id,
        kind=case.kind,
        score=score,
        passed=score >= max(0.0, float(min_case_score)),
        finish_reason=finish_reason,
        output_preview=_compact(text, 360),
        tool_calls=tool_names,
        reason=reason,
    )


def _system_prompt(candidate_prompt: str) -> str:
    return (
        "You are replaying an Echo planner behavior test. Follow the "
        "candidate planner instruction below. If tools are useful and "
        "available, call them. Keep the answer concise but complete.\n\n"
        f"<candidate_prompt>\n{candidate_prompt}\n</candidate_prompt>"
    )


def _case_prompt(case: TurnReplayCase) -> str:
    return (
        f"Replay case: {case.kind}\n"
        f"User task: {case.task_input}\n"
        f"Expected behavior: {case.expected_behavior}\n"
        "Respond as the planner should behave in this situation."
    )


def _tool_specs() -> list[ToolSpec]:
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "additionalProperties": True,
    }
    return [
        ToolSpec(
            name="web_search", description="Search the web replay fixture.", input_schema=schema
        ),
        ToolSpec(
            name="read_file",
            description="Read a file in the replay workspace.",
            input_schema=schema,
        ),
        ToolSpec(
            name="write_text_file",
            description="Write a file in the replay workspace.",
            input_schema=schema,
        ),
        ToolSpec(
            name="todo_write", description="Update replay todo/progress state.", input_schema=schema
        ),
    ]


def _execute_mock_tool(name: str, payload: dict[str, Any], workspace: Path) -> str:
    normalized = name.strip().lower()
    if normalized in {"web_search", "search"}:
        query = str(payload.get("query") or payload.get("q") or "")
        return json.dumps(
            {
                "results": [
                    {
                        "title": f"Replay result for {query}",
                        "snippet": "deterministic replay search result",
                    }
                ]
            },
            ensure_ascii=False,
        )
    if normalized == "read_file":
        target = _safe_workspace_path(workspace, str(payload.get("path") or "case.json"))
        if not target.exists():
            return "ERROR: file not found"
        return target.read_text(encoding="utf-8", errors="replace")[:4000]
    if normalized in {"write_text_file", "write_file"}:
        target = _safe_workspace_path(workspace, str(payload.get("path") or "output.txt"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(payload.get("content") or ""), encoding="utf-8")
        return json.dumps({"ok": True, "path": str(target)}, ensure_ascii=False)
    if normalized == "todo_write":
        (workspace / "todo.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return json.dumps({"ok": True, "status": "updated"}, ensure_ascii=False)
    return f"ERROR: unknown replay tool {name}"


def _score_llm_output(
    case: TurnReplayCase,
    *,
    text: str,
    finish_reason: str,
    tool_names: list[str],
) -> tuple[float, str]:
    lower = text.lower()
    finish = finish_reason.lower()
    if finish in {"length", "max_tokens", "max_output_tokens", "output_limit", "token_limit"}:
        return 0.2, "model output was truncated"
    if case.kind == "report_truncation":
        matched = _count_matches(
            lower, [r"continue|resume|checkpoint|from where", r"complete|final|report|delivered"]
        )
        score = 0.35 + 0.3 * matched
        return (
            round(min(1.0, score), 3),
            "report continuation behavior replayed"
            if matched >= 2
            else "missing continuation behavior",
        )
    if case.kind == "tool_permission_confusion":
        if re.search(
            r"cannot use tools|can't use tools|no tools|without tools|无法调用工具|不能调用工具",
            lower,
        ):
            return 0.1, "claimed tools are unavailable"
        used_tool = bool(tool_names)
        matched = used_tool or bool(
            re.search(r"tool|search|read_file|web_search|available|can use", lower)
        )
        return (
            (0.9 if used_tool else 0.7, "tool-capable behavior replayed")
            if matched
            else (0.35, "did not show tool-capable behavior")
        )
    if case.kind == "final_step_stuck":
        if re.search(r"in_progress|keep running|still running|继续转圈", lower):
            return 0.2, "left progress running"
        matched = _count_matches(
            lower, [r"complete|done|completed|finished|final", r"mark|close|stop|progress|todo"]
        )
        score = 0.35 + 0.3 * matched
        return round(
            min(1.0, score), 3
        ), "final step closure replayed" if matched >= 2 else "missing final step closure"
    if text.strip():
        return 0.65, "generic replay produced output"
    return 0.0, "empty replay output"


def _count_matches(text: str, patterns: list[str]) -> int:
    return sum(1 for pattern in patterns if re.search(pattern, text))


def _average(results: list[LLMReplayCaseResult]) -> float:
    if not results:
        return 0.5
    return round(sum(result.score for result in results) / len(results), 3)


def _safe_workspace_path(workspace: Path, raw: str) -> Path:
    candidate = (workspace / raw).resolve()
    root = workspace.resolve()
    if candidate == root or root in candidate.parents:
        return candidate
    raise ValueError("path escapes replay workspace")


def _safe_name(text: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    return safe[:80] or "case"


def _compact(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[: max(0, int(limit))]


__all__ = [
    "LLMReplayCandidateReport",
    "LLMReplayCaseResult",
    "LLMReplayReport",
    "replay_llm_candidates",
]
