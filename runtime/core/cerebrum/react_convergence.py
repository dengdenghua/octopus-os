"""Deterministic evidence-to-answer convergence for bounded ReAct turns.

The model is still responsible for writing the answer.  This module only
decides when a read-only request has collected the evidence the user
explicitly asked for, so the runtime can stop offering more tools and ask for
the answer instead of trusting weaker models to stop exploring on their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from runtime.core.cerebrum.react_guards import (
    _explicit_source_paths,
    _path_evidence_matches,
    _successful_read_paths,
)
from runtime.core.cerebrum.react_parsing import _parse_action
from runtime.core.cerebrum.react_types import ReActStep


@dataclass(frozen=True, slots=True)
class EvidenceConvergence:
    reason: str
    covered: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExplicitReadScopeConstraint:
    actions: tuple[str, ...]
    missing: tuple[str, ...]
    skipped: tuple[str, ...]

    def observation_note(self) -> str:
        skipped = ", ".join(self.skipped)
        missing = ", ".join(self.missing)
        return (
            "[explicit-read-scope] The runtime skipped duplicate or unrequested "
            f"file inspections: {skipped}. Continue only with the explicitly requested "
            f"files that still lack successful evidence: {missing}. Do not guess "
            "alternate paths or expand into related files, tests, or type files."
        )


def ordered_explicit_read_groups(goal: str) -> tuple[tuple[str, ...], ...]:
    """Recover user-authored read batches while preserving textual order."""

    requested = _explicit_source_paths(goal)
    if not requested:
        return ()
    groups = [
        tuple(_explicit_source_paths(segment))
        for segment in re.split(r"[；;。\n]+", str(goal or ""))
    ]
    groups = [group for group in groups if group]
    flattened = [path for group in groups for path in group]
    if len(groups) >= 2 and flattened == requested:
        return tuple(groups)
    if re.search(r"(?:依次|逐个)\s*(?:读取|阅读|核对|检查)", goal):
        return tuple((path,) for path in requested)
    if re.search(r"(?:并行|同时)\s*(?:读取|阅读|核对|检查)", goal):
        return (tuple(requested),)
    return ()


_BOUNDED_ANSWER_RE = re.compile(
    r"(?:"
    r"(?:只|仅)(?:用|需|要)?\s*(?:一|1)\s*(?:句|句话|行)"
    r"|(?:一句|一行)(?:话|回答|结论)?"
    r"|(?:最多|不超过)\s*(?:一|1)\s*(?:句|句话|行)"
    r"|\b(?:one[- ](?:sentence|line)|in\s+one\s+(?:sentence|line))\b"
    r"|\b(?:briefly|concise(?:ly)?)\b"
    r")",
    re.IGNORECASE,
)

_SUBSTANTIVE_EVIDENCE_TOOLS = frozenset(
    {
        "bb_read",
        "fetch_url",
        "grep_text",
        "read_file",
        "read_files",
        "read_text_file",
        "search_documents",
        "web_fetch",
        "web_search",
    }
)

_URL_RE = re.compile(r"https?://[^\s<>'\"`，。；、）)]+", re.IGNORECASE)

_MISSING_TASK_ANSWER_RE = re.compile(
    r"(?:"
    r"没有(?:正在进行的|实际的|具体的)?(?:用户)?任务"
    r"|没有(?:任何)?(?:待办|工具结果|实际动作|工作需要收尾)"
    r"|(?:看不到|没有)(?:任何)?(?:具体)?(?:待办的)?用户请求"
    r"|没有可继续的工作"
    r"|你要我做什么[^。！？!?]{0,100}(?:说一句|告诉我|开工)"
    r"|如果你有具体需求[^。！？!?]{0,80}(?:说一句|告诉我|开工)"
    r"|\b(?:there(?:'s|\s+is)\s+no\s+(?:actual\s+|concrete\s+|user\s+)?task"
    r"|no\s+(?:actual\s+|concrete\s+|user\s+)?task\s+here"
    r"|nothing\s+(?:is\s+)?in\s+progress"
    r"|no\s+tool\s+results?)\b"
    r")",
    re.IGNORECASE,
)

_TASK_STATUS_GOAL_RE = re.compile(
    r"(?:是否|有没有|有无|检查|确认|核对).{0,20}(?:任务|待办|工作|工具结果)"
    r"|\b(?:whether|check|confirm|verify).{0,30}(?:task|todo|work\s+in\s+progress)\b",
    re.IGNORECASE,
)


def _action_succeeded(step: ReActStep, index: int) -> bool:
    if index < len(step.action_results):
        return step.action_results[index].get("ok") is True
    observation = (step.observation or "").strip().lower()
    return bool(observation) and not any(
        marker in observation
        for marker in (
            "not executed",
            "timed_out",
            "工具失败",
            "工具执行异常",
            "未执行观察",
            '"error":',
        )
    )


def _successful_substantive_evidence(steps: list[ReActStep]) -> tuple[str, ...]:
    evidence: list[str] = []
    for step in steps:
        actions = step.actions or ([step.action] if step.action else [])
        for index, action in enumerate(actions):
            parsed = _parse_action(action)
            if parsed is None or not _action_succeeded(step, index):
                continue
            name, args = parsed
            if name.lower() not in _SUBSTANTIVE_EVIDENCE_TOOLS:
                continue
            target = next(
                (
                    str(args[key]).strip()
                    for key in ("path", "file_path", "filepath", "file", "url", "query")
                    if isinstance(args.get(key), str) and str(args[key]).strip()
                ),
                name,
            )
            evidence.append(target)
    return tuple(dict.fromkeys(evidence))


def _successful_fetched_urls(steps: list[ReActStep]) -> tuple[str, ...]:
    urls: list[str] = []
    for step in steps:
        actions = step.actions or ([step.action] if step.action else [])
        for index, action in enumerate(actions):
            parsed = _parse_action(action)
            if parsed is None or not _action_succeeded(step, index):
                continue
            name, args = parsed
            if name.lower() not in {"fetch_url", "web_fetch"}:
                continue
            value = args.get("url")
            if isinstance(value, str) and value.strip():
                urls.append(value.strip().rstrip(".,;，。；"))
    return tuple(dict.fromkeys(urls))


def constrain_explicit_read_scope(
    *,
    goal: str,
    steps: list[ReActStep],
    actions: list[str],
    read_only: bool,
    enforce_order: bool = False,
) -> ExplicitReadScopeConstraint | None:
    """Filter duplicate and out-of-scope reads after explicit coverage begins.

    The first evidence round remains unconstrained so an agent can orient in an
    unfamiliar workspace. Once at least one user-named file has successful read
    evidence, however, the remaining explicit paths are authoritative. This
    prevents weak providers from re-reading covered files or expanding into
    guessed neighbours while the requested set is still incomplete.
    """

    if not read_only or not actions:
        return None
    requested = _explicit_source_paths(goal)
    if not requested:
        return None

    observed = _successful_read_paths(steps)
    covered = [
        path
        for path in requested
        if any(_path_evidence_matches(path, candidate) for candidate in observed)
    ]
    if not covered and not enforce_order:
        return None
    missing = tuple(path for path in requested if path not in covered)
    if not missing:
        return None
    allowed_missing = missing
    if enforce_order:
        for group in ordered_explicit_read_groups(goal):
            group_missing = tuple(path for path in group if path in missing)
            if group_missing:
                allowed_missing = group_missing
                break

    path_inspection_tools = {
        "bb_read",
        "grep_text",
        "read_file",
        "read_file_range",
        "read_files",
        "read_text_file",
    }
    kept: list[str] = []
    skipped: list[str] = []
    for action in actions:
        parsed = _parse_action(action)
        if parsed is None:
            kept.append(action)
            continue
        name, args = parsed
        if name.lower() not in path_inspection_tools:
            kept.append(action)
            continue
        targets = [
            str(args[key]).strip()
            for key in ("path", "file_path", "filepath", "file")
            if isinstance(args.get(key), str) and str(args[key]).strip()
        ]
        values = args.get("paths") or args.get("files")
        if isinstance(values, list):
            targets.extend(str(value).strip() for value in values if isinstance(value, str))
        if not targets:
            kept.append(action)
            continue
        if all(
            any(
                _path_evidence_matches(requested_path, target) for requested_path in allowed_missing
            )
            for target in targets
        ):
            kept.append(action)
            continue
        skipped.extend(targets)

    if not skipped:
        return None
    return ExplicitReadScopeConstraint(
        actions=tuple(kept),
        missing=allowed_missing,
        skipped=tuple(dict.fromkeys(skipped)),
    )


def read_only_evidence_convergence(
    *,
    goal: str,
    steps: list[ReActStep],
    read_only: bool,
) -> EvidenceConvergence | None:
    """Return terminal evidence coverage for a bounded read-only request.

    Explicit files and URLs are authoritative scope: every named target must
    be covered before convergence.  Requests without explicit targets only
    converge automatically when the user also asks for a deliberately short
    answer and at least one substantive evidence tool succeeded.  Open-ended
    architecture reviews and research therefore retain their full exploration
    budget.
    """

    if not read_only or not steps:
        return None

    requested_paths = _explicit_source_paths(goal)
    if requested_paths:
        observed_paths = _successful_read_paths(steps)
        missing = [
            requested
            for requested in requested_paths
            if not any(_path_evidence_matches(requested, observed) for observed in observed_paths)
        ]
        if not missing:
            return EvidenceConvergence(
                reason="all explicitly requested files have successful read evidence",
                covered=tuple(requested_paths),
            )
        return None

    requested_urls = tuple(dict.fromkeys(url.rstrip(".,;，。；") for url in _URL_RE.findall(goal)))
    if requested_urls:
        fetched_urls = _successful_fetched_urls(steps)
        if all(url in fetched_urls for url in requested_urls):
            return EvidenceConvergence(
                reason="all explicitly requested URLs have successful fetch evidence",
                covered=requested_urls,
            )
        return None

    evidence = _successful_substantive_evidence(steps)
    if evidence and _BOUNDED_ANSWER_RE.search(goal):
        return EvidenceConvergence(
            reason="the user requested a bounded answer and substantive evidence is available",
            covered=evidence,
        )
    return None


def build_evidence_digest(
    decision: EvidenceConvergence,
    steps: list[ReActStep],
    *,
    max_chars_per_target: int = 2400,
) -> str:
    """Build a bounded per-target digest for the direct-answer round.

    Several full source files can arrive in one parallel observation. Generic
    context truncation then keeps only the tail, silently discarding earlier
    files. Preserve a head/tail excerpt for every covered target so the
    tools-disabled synthesis request sees the complete evidence set without
    inheriting tens of thousands of raw source tokens.
    """

    if not decision.covered or max_chars_per_target <= 0:
        return ""

    excerpts: dict[str, str] = {}
    for step in steps:
        actions = step.actions or ([step.action] if step.action else [])
        for index, action in enumerate(actions):
            parsed = _parse_action(action)
            if parsed is None or not _action_succeeded(step, index):
                continue
            name, args = parsed
            if name.lower() not in {
                "bb_read",
                "read_file",
                "read_files",
                "read_text_file",
            }:
                continue
            observed_target = next(
                (
                    str(args[key]).strip()
                    for key in ("path", "file_path", "filepath", "file")
                    if isinstance(args.get(key), str) and str(args[key]).strip()
                ),
                "",
            )
            if not observed_target:
                continue
            requested_target = next(
                (
                    requested
                    for requested in decision.covered
                    if _path_evidence_matches(requested, observed_target)
                ),
                None,
            )
            if requested_target is None or requested_target in excerpts:
                continue
            result = step.action_results[index] if index < len(step.action_results) else None
            raw_observation = (
                result.get("observation") if isinstance(result, dict) else step.observation
            )
            if not isinstance(raw_observation, str) or not raw_observation.strip():
                continue
            text = raw_observation.strip()
            if len(text) > max_chars_per_target:
                head_size = max_chars_per_target // 2
                tail_size = max_chars_per_target - head_size
                text = (
                    text[:head_size]
                    + "\n...[middle omitted for bounded synthesis]...\n"
                    + text[-tail_size:]
                )
            excerpts[requested_target] = text

    if not excerpts:
        return ""
    sections = [
        "[bounded-read-evidence]",
        "Every explicitly requested target has been read. Use only this evidence "
        "to answer now; do not call tools or expand scope.",
    ]
    for target in decision.covered:
        excerpt = excerpts.get(target)
        if excerpt:
            sections.append(f"--- {target} ---\n{excerpt}")
    sections.append("[/bounded-read-evidence]")
    return "\n\n".join(sections)


def build_direct_answer_directive(
    *,
    goal: str,
    decision: EvidenceConvergence,
    steps: list[ReActStep],
) -> str:
    """Keep the original task next to bounded evidence during synthesis.

    Generic context compression may retain the latest observation while
    dropping the first user message.  A tools-disabled recovery model would
    then see evidence plus a generic "continue" nudge but not know what the
    user asked.  Put the request at the *tail* of the convergence observation
    so even head/tail truncation preserves the contract it must answer.
    """

    digest = build_evidence_digest(decision, steps)
    original_goal = (goal or "").strip()
    parts = [digest] if digest else []
    if original_goal:
        parts.append(
            "[original-user-request]\n"
            f"{original_goal}\n"
            "[/original-user-request]\n"
            "Answer this exact request now from the completed evidence. "
            "Do not reinterpret it as a new conversation, deny the completed "
            "work, call another tool, or expand scope. "
            "Reply in a conversational tone that addresses the user naturally, "
            "not just a dry execution report."
        )
    return "\n\n".join(parts)


def evidence_answer_conflicts_with_goal(*, goal: str, answer: str) -> bool:
    """Reject a synthesis answer that falsely claims there was no task.

    This is deliberately narrow: it does not grade the answer or require file
    names to be repeated (a valid one-line answer may omit them).  It only
    catches the contradictory idle/greeting response observed when a fallback
    model loses the original user request after context compression.
    """

    if not answer or _TASK_STATUS_GOAL_RE.search(goal or ""):
        return False
    return bool(_MISSING_TASK_ANSWER_RE.search(answer))


__all__ = [
    "EvidenceConvergence",
    "ExplicitReadScopeConstraint",
    "build_direct_answer_directive",
    "build_evidence_digest",
    "constrain_explicit_read_scope",
    "ordered_explicit_read_groups",
    "evidence_answer_conflicts_with_goal",
    "read_only_evidence_convergence",
]
