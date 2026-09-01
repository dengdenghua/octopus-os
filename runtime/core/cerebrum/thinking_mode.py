"""Structured thinking-mode helpers.

This module keeps the "thinking" path as a medium-weight route between
plain chat and deep research. It does not expose hidden chain-of-thought;
it builds a visible execution scaffold that prompts the model to reason
carefully, verify when needed, and suggest Deep Research when the task
has a broad research/report shape.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

THINKING_MODES = {"react", "thinking"}

_SEARCH_PATTERNS = (
    "latest",
    "today",
    "current",
    "recent",
    "news",
    "price",
    "stock",
    "weather",
    "law",
    "policy",
    "regulation",
    "release",
    "search",
    "source",
    "website",
    "url",
    "http://",
    "https://",
    "最新",
    "今天",
    "现在",
    "新闻",
    "价格",
    "股价",
    "天气",
    "政策",
    "法规",
    "搜索",
    "来源",
    "网站",
)

_DEEP_RESEARCH_PATTERNS = (
    "market research",
    "deep research",
    "industry report",
    "competitive analysis",
    "competitor",
    "compare vendors",
    "white paper",
    "多来源",
    "深度研究",
    "市场调研",
    "调研",
    "行业报告",
    "竞品",
    "竞争分析",
    "对比",
    "报告",
)


@dataclass(slots=True)
class ThinkingPlanStep:
    title: str
    detail: str
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ThinkingPlan:
    id: str
    mode: str
    goal: str
    assumptions: list[str] = field(default_factory=list)
    steps: list[ThinkingPlanStep] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    needs_search: bool = False
    suggest_deep_research: bool = False
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["steps"] = [step.to_dict() for step in self.steps]
        return payload


def is_thinking_mode(mode: str | None) -> bool:
    return (mode or "").strip().lower() in THINKING_MODES


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def _looks_like_url(text: str) -> bool:
    return bool(re.search(r"https?://\S+|www\.\S+", text, flags=re.I))


def _context_material_count(user_context: dict[str, Any] | None) -> int:
    if not isinstance(user_context, dict):
        return 0
    total = 0
    for key in ("files", "research_materials", "materials"):
        value = user_context.get(key)
        if isinstance(value, list):
            total += len(value)
    return total


def build_thinking_plan(
    goal: str,
    user_context: dict[str, Any] | None = None,
    *,
    mode: str = "react",
) -> ThinkingPlan:
    """Build a visible, serializable plan for a structured thinking turn."""

    clean_goal = (goal or "").strip()
    material_count = _context_material_count(user_context)
    needs_search = _looks_like_url(clean_goal) or _contains_any(
        clean_goal,
        _SEARCH_PATTERNS,
    )
    suggest_deep = _contains_any(clean_goal, _DEEP_RESEARCH_PATTERNS)
    if material_count >= 3:
        suggest_deep = True

    assumptions = [
        "Use the selected main agent persona and its available memory as the stable context.",
        "Keep any helper roles virtual and ephemeral for this turn.",
    ]
    if material_count:
        assumptions.append(
            f"Consider the {material_count} attached material item(s) before general web evidence.",
        )

    risks = [
        "Do not reveal hidden chain-of-thought; show only concise reasoning checkpoints.",
    ]
    if needs_search:
        risks.append(
            "The answer may depend on fresh or source-backed facts, so verify before asserting.",
        )
    if suggest_deep:
        risks.append(
            "The request has a broad research/report shape; Deep Research may produce stronger coverage.",
        )

    evidence_detail = (
        "Check current sources or supplied URLs before making time-sensitive claims."
        if needs_search
        else "Inspect the current thread, selected agent memory, and supplied materials first."
    )
    steps = [
        ThinkingPlanStep(
            title="Frame the ask",
            detail="Restate the objective, constraints, and expected output shape.",
            status="in_progress",
        ),
        ThinkingPlanStep(
            title="Gather context",
            detail=evidence_detail,
        ),
        ThinkingPlanStep(
            title="Reason across options",
            detail="Compare likely interpretations and trade-offs before committing.",
        ),
        ThinkingPlanStep(
            title="Verify",
            detail="Check for stale facts, missing assumptions, and contradictions.",
        ),
        ThinkingPlanStep(
            title="Answer",
            detail="Give the user the result first, then the compact rationale and next steps.",
        ),
    ]

    return ThinkingPlan(
        id=f"think-{uuid4().hex[:12]}",
        mode=(mode or "react").strip().lower(),
        goal=clean_goal,
        assumptions=assumptions,
        steps=steps,
        risks=risks,
        needs_search=needs_search,
        suggest_deep_research=suggest_deep,
    )


def update_thinking_plan_status(
    plan: dict[str, Any] | ThinkingPlan | None,
    *,
    iteration: int | None = None,
    final: bool = False,
) -> dict[str, Any] | None:
    """Return a copy of ``plan`` with visible step status advanced.

    ``iteration`` is the completed ReAct iteration count. The mapping is
    intentionally coarse: after iteration 1, step 1 is complete and step
    2 becomes current. When the final answer lands, all steps complete.
    """

    if plan is None:
        return None
    payload = plan.to_dict() if isinstance(plan, ThinkingPlan) else dict(plan)
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return payload

    steps: list[dict[str, Any]] = []
    for raw_step in raw_steps:
        if isinstance(raw_step, dict):
            steps.append(dict(raw_step))
    if not steps:
        return payload

    if final:
        current_index: int | None = None
        for step in steps:
            step["status"] = "completed"
        progress = 1.0
    else:
        completed_count = max(0, int(iteration or 0))
        current_index = min(completed_count, len(steps) - 1)
        for index, step in enumerate(steps):
            if index < completed_count:
                step["status"] = "completed"
            elif index == current_index:
                step["status"] = "in_progress"
            else:
                step["status"] = "pending"
        progress = min(0.98, completed_count / max(1, len(steps)))

    payload["steps"] = steps
    payload["current_step_index"] = current_index
    payload["progress"] = progress
    payload["updated_at"] = datetime.now(UTC).isoformat()
    return payload


def render_thinking_guidance(plan: dict[str, Any] | ThinkingPlan | None) -> str:
    """Render non-template guidance for system prompts."""

    if plan is None:
        return ""
    payload = plan.to_dict() if isinstance(plan, ThinkingPlan) else plan
    if not isinstance(payload, dict):
        return ""

    risks = payload.get("risks")
    risk_lines = []
    if isinstance(risks, list):
        risk_lines = [f"- {item}" for item in risks if isinstance(item, str) and item.strip()]

    flags = []
    if payload.get("needs_search"):
        flags.append("fresh/source-backed verification may be needed")
    if payload.get("suggest_deep_research"):
        flags.append("mention Deep Research if the user needs broader coverage")

    # Step scaffold — the planner sees named checkpoints so it can
    # describe what *happened* rather than the generic phase labels the
    # prompt above forbids. Steps come from the plan, which is built
    # once per turn.
    step_lines: list[str] = []
    raw_steps = payload.get("steps")
    if isinstance(raw_steps, list):
        for step in raw_steps:
            if not isinstance(step, dict):
                continue
            title = step.get("title")
            detail = step.get("detail")
            if isinstance(title, str) and title.strip():
                if isinstance(detail, str) and detail.strip():
                    step_lines.append(f"- {title}: {detail}")
                else:
                    step_lines.append(f"- {title}")

    return "\n".join(
        line
        for line in [
            "<echo-thinking-mode>",
            "This turn is in structured thinking mode: the medium path between quick chat and Deep Research.",
            "Do not expose hidden chain-of-thought. If the provider supports explicit reasoning_content, stream that field as-is through the reasoning channel.",
            "When you surface progress, only describe concrete actions that happened: routing decisions, tool calls, observations, subagent dispatch, and completion state.",
            "Do not invent or repeat generic phase labels such as request intake, task framing, tool routing, model dispatch, gather context, verify, or answer.",
            "The selected main agent persona and memory remain active. Any helper roles are virtual, ephemeral, and must not create independent memory.",
            *(["", "Steps:"] + step_lines if step_lines else []),
            "",
            "Risks:",
            *risk_lines,
            "",
            f"Flags: {', '.join(flags) if flags else 'none'}",
            "</echo-thinking-mode>",
        ]
        if line is not None
    )
