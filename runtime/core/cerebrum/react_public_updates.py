"""Public progress-update plumbing for the ReAct loop.

Extracted from ``react_loop.py`` (Wave 1 of the split documented in
``docs/design/react-loop-split-plan.md``). These helpers sanitize the
model's ``Update:`` channel for the main conversation lane, build the
privacy-safe narrator inputs, stream a model-authored public checkpoint, and
produce deterministic fallbacks when narration is unavailable.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Generator
from typing import Any

from runtime.core.cerebrum.react_convergence import (
    EvidenceConvergence,
    build_evidence_digest,
)
from runtime.core.cerebrum.react_guards import _explicit_source_paths
from runtime.core.cerebrum.react_model_deadlines import (
    _MODEL_STREAM_DEADLINE,
    _iter_model_stream_with_deadline,
)
from runtime.core.cerebrum.react_parsing import (
    _looks_like_special_tool_envelope,
    _parse_action,
)
from runtime.core.cerebrum.react_types import ReActStep
from runtime.platform.models.rescue_policy import next_custom_model_fallback

_PUBLIC_UPDATE_PROTOCOL_RE = re.compile(
    r"(?:^|\n)\s*(?:Thought|Action|Observation|Final\s*Answer)\s*:",
    re.IGNORECASE,
)
_PUBLIC_UPDATE_TOOL_CALL_RE = re.compile(
    r"(?:<tool_call\b|<function=|[A-Za-z_][A-Za-z0-9_./:-]*\s*\(\s*\{)",
    re.IGNORECASE,
)
_PUBLIC_UPDATE_BOILERPLATE_RE = re.compile(
    r"^(?:(?:我|我们)?(?:还在|正在|继续|接着|马上|即将)"
    r"(?:思考|处理|执行|整理|分析|工作)|(?:still|currently|continuing to|about to)\s+"
    r"(?:think|work|process|analy[sz]e|execute))[。.!！\s]*$",
    re.IGNORECASE,
)
# A pure clarification request ("请说明您需要我处理的具体内容") is NOT public
# progress — it is an answer the model should deliver through the normal answer
# channel (where the completion guards and _final_answer_requests_user_help can
# handle it), not an Update: checkpoint. Surfacing it as the first visible
# commentary is exactly the "先泛化一句、让人感觉敷衍" experience. Only suppress
# when the whole checkpoint is a generic ask, so a real plan that happens to
# end in "请告诉我" still streams.
_PUBLIC_UPDATE_GENERIC_CLARIFY_RE = re.compile(
    r"^(?:"
    r"请(?:您|你)?(?:再|详细|具体)?(?:说明|告诉我|告诉我您|告诉我你|提供|描述|补充说明|把需求说清楚)[^。.!！]{0,50}"
    r"|(?:您|你)(?:需要|想要|希望)我(?:处理|做什么|提供|给出)[^。.!！]{0,40}"
    r"|我(?:需要|想|希望)(?:您|你)(?:告诉我|提供|说明|描述)[^。.!！]{0,40}"
    r"|请提供更多信息|请详细描述|请补充说明|请把需求说清楚"
    r")[。.!！]?\s*$",
    re.IGNORECASE,
)


_PUBLIC_UPDATE_CODE_RE = re.compile(
    r"(?:^|\n)\s*(?:async\s+)?(?:def|class|function|const|let|var|return|raise)\b"
    r"|(?:^|\n)\s*[@#][A-Za-z_]\w*|[{}]\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_PUBLIC_EVIDENCE_NARRATIVE_TIMEOUT_S = 5.0
_PUBLIC_EVIDENCE_STREAM_GATE_CHARS = 24


def _safe_public_update(value: str | None) -> str:
    """Return a bounded checkpoint safe for the main conversation lane."""
    if not isinstance(value, str):
        return ""
    cleaned = re.sub(
        r"^\s*(?:Update|Progress)\s*:\s*",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned).strip()
    for marker in ("**", "__"):
        if cleaned.startswith(marker) and cleaned.endswith(marker) and len(cleaned) > 4:
            cleaned = cleaned[len(marker) : -len(marker)].strip()
    if not cleaned or _PUBLIC_UPDATE_PROTOCOL_RE.search(cleaned):
        return ""
    if _PUBLIC_UPDATE_TOOL_CALL_RE.search(cleaned) or _looks_like_special_tool_envelope(cleaned):
        return ""
    if _PUBLIC_UPDATE_BOILERPLATE_RE.fullmatch(cleaned):
        return ""
    if _PUBLIC_UPDATE_GENERIC_CLARIFY_RE.match(cleaned):
        # A clarification is not progress; let it go through the answer lane.
        return ""
    # Public progress is a conversational beat, never a source excerpt. Some
    # OpenAI-compatible providers echo retrieved context as ordinary text next
    # to a native tool call; reject that channel instead of truncating code into
    # the transcript and let the evidence narrator repair the missing beat.
    if len(cleaned) > 420:
        return ""
    if cleaned.count("\n") >= 2 and _PUBLIC_UPDATE_CODE_RE.search(cleaned):
        return ""
    return cleaned.rstrip()


def _bounded_public_evidence_excerpt(value: Any, *, max_chars: int = 700) -> str:
    """Keep the latest tool evidence useful without replaying a huge payload."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    text = value.strip()
    if not text:
        return ""
    # Runtime convergence/guard directives are instructions for the working
    # model, not evidence the public narrator should paraphrase to the user.
    text = re.split(
        r"\n\n(?:\[(?:green-verification-convergence|duplicate-tools-collapsed|"
        r"redundant-tool-skipped)\]|The user's requested read-only evidence is complete\.)",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return f"{text[:head]}\n…\n{text[-tail:]}"


def _build_public_evidence_narrative_input(
    *,
    goal: str,
    step: ReActStep,
    convergence: EvidenceConvergence | None,
    evidence_steps: list[ReActStep] | None = None,
) -> str:
    """Build a compact, attributed snapshot of the just-finished milestone."""
    actions = step.actions or ([step.action] if step.action else [])
    sections: list[str] = [
        "[original-user-request]",
        (goal or "").strip()[:1600],
        "[/original-user-request]",
        "[just-completed-evidence]",
    ]
    if convergence is not None:
        digest = build_evidence_digest(
            convergence,
            evidence_steps or [step],
            max_chars_per_target=700,
        )
        if digest:
            sections.append(digest)

    results = step.action_results
    if len(results) == len(actions):
        for index, (action, result) in enumerate(zip(actions, results, strict=True), start=1):
            parsed = _parse_action(action)
            target = ""
            if parsed is not None:
                _name, args = parsed
                target = _public_tool_target(args if isinstance(args, dict) else {})
            status = "completed" if result.get("ok") is True else "failed"
            excerpt = _bounded_public_evidence_excerpt(result.get("observation") or "")
            sections.append(
                f"Result {index} ({target or 'requested operation'}): {status}"
                + (f"\n{excerpt}" if excerpt else "")
            )
    else:
        parsed_targets: list[str] = []
        for action in actions:
            parsed = _parse_action(action)
            if parsed is None:
                continue
            _name, args = parsed
            target = _public_tool_target(args if isinstance(args, dict) else {})
            if target and target not in parsed_targets:
                parsed_targets.append(target)
        if parsed_targets:
            sections.append("Completed scope: " + ", ".join(parsed_targets[:8]))
        excerpt = _bounded_public_evidence_excerpt(step.observation or "")
        if excerpt:
            sections.append(excerpt)
    sections.append("[/just-completed-evidence]")
    return "\n\n".join(part for part in sections if part)


def _public_narrative_language_instruction(goal: str) -> str:
    """Make the narrator follow the user's script, not the provider default."""

    text = str(goal or "")
    if re.search(r"[\uac00-\ud7af]", text):
        return " The user's language is Korean; write the update in Korean."
    if re.search(r"[\u3040-\u30ff]", text):
        return " The user's language is Japanese; write the update in Japanese."
    if re.search(r"[\u3400-\u9fff]", text):
        return " The user's language is Simplified Chinese; write the update in Simplified Chinese."
    return ""


def _observed_read_fallback_update(*, goal: str, step: ReActStep) -> str:
    """Return a truthful conversational handoff when narration times out.

    The fallback only uses the completed read receipt: target, byte size,
    truncation state, and the next user-named path. It keeps a long ordered
    read visibly alive without inventing a source-level conclusion.
    """

    actions = step.actions or ([step.action] if step.action else [])
    current_path = ""
    for action in reversed(actions):
        parsed = _parse_action(action)
        if parsed is None:
            continue
        name, args = parsed
        candidate = args.get("path") if isinstance(args, dict) else None
        if name in {"read_file", "read_file_range"} and isinstance(candidate, str):
            current_path = candidate.replace("\\", "/").lstrip("./")
            break
    if not current_path:
        return ""

    observation = str(step.observation or "")
    size_match = re.search(r'"size"\s*:\s*(\d+)', observation)
    complete = bool(re.search(r'"truncated"\s*:\s*false', observation, re.IGNORECASE))
    size = int(size_match.group(1)) if size_match else None
    requested = _explicit_source_paths(goal)
    normalized = [path.replace("\\", "/").lstrip("./") for path in requested]
    next_path = ""
    with contextlib.suppress(ValueError, IndexError):
        next_path = normalized[normalized.index(current_path) + 1]

    current_label = os.path.basename(current_path)
    next_label = os.path.basename(next_path) if next_path else ""
    if re.search(r"[\u3400-\u9fff]", str(goal or "")):
        if size is not None:
            fact = (
                f"已完整取得 {current_label} 的 {size:,} 字节内容"
                if complete
                else f"已取得 {current_label} 的 {size:,} 字节可用内容"
            )
        else:
            fact = f"已取得 {current_label} 的实际内容"
        return (
            f"{fact}；接下来核对 {next_label}。"
            if next_label
            else f"{fact}；所需证据已经齐全，现在收束结论。"
        )

    fact = (
        f"I now have all {size:,} bytes of {current_label}"
        if size is not None and complete
        else f"I now have the actual contents of {current_label}"
    )
    return (
        f"{fact}; next I’ll check {next_label}."
        if next_label
        else f"{fact}; the requested evidence is complete, so I’m wrapping up the conclusion."
    )


def _build_public_action_orientation_input(*, goal: str, step: ReActStep) -> str:
    """Build a privacy-safe scope snapshot for a missing public update.

    The working model's private thought is deliberately excluded.  A focused
    repair request only needs the user's request and non-sensitive targets from
    the already-proposed actions to author one ordinary progress sentence.
    """
    actions = step.actions or ([step.action] if step.action else [])
    targets: list[str] = []
    for action in actions:
        parsed = _parse_action(action)
        if parsed is None:
            continue
        _name, args = parsed
        target = _public_tool_target(args if isinstance(args, dict) else {})
        if target and target not in targets:
            targets.append(target)
    sections = [
        "[original-user-request]",
        (goal or "").strip()[:1600],
        "[/original-user-request]",
    ]
    if targets:
        sections.extend(
            [
                "[next-public-scope]",
                ", ".join(targets[:8]),
                "[/next-public-scope]",
            ]
        )
    return "\n\n".join(sections)


def _stream_public_evidence_narrative(
    router: Any,
    *,
    model: str,
    goal: str,
    step: ReActStep,
    convergence: EvidenceConvergence | None,
    evidence_steps: list[ReActStep] | None = None,
    iteration: int,
    previous_key: str = "",
    succeeded: bool | None = None,
    pending_action: bool = False,
) -> Generator[dict[str, Any], None, str]:
    """Stream one model-authored public update into a single timeline item.

    The tools-disabled narrator receives either completed evidence or, when a
    provider omitted its required update, only the pending public scope. A
    short prefix gate prevents control values such as ``SKIP`` from flashing
    in the conversation, then later deltas extend the same commentary item
    instead of manufacturing one avatar/message per provider chunk.
    """
    from runtime.platform.models.llm import Message, ModelRequest

    system_content = (
        (
            "Write exactly one brief public progress update for the next concrete "
            "action. Use a natural sentence in the user's language. Name the specific "
            "scope when it is supplied and say what checking or changing it will "
            "establish. Do not expose hidden reasoning, mention tool or protocol names, "
            "refer to yourself as the system, use markdown emphasis, use a heading/list/"
            "stage label, repeat the request, or claim a result before the action has "
            "completed. Output only the user-facing sentence."
        )
        if pending_action
        else (
            "Write a brief public progress update from completed evidence only. "
            "Use one or two natural sentences in the user's language. State one "
            "concrete thing now known and the next decision, correction, or action. "
            "Do not expose hidden reasoning, mention tool names or internal protocols, "
            "use a heading/list, repeat the request, or pretend this is the final answer. "
            "Never claim anything absent from the evidence. If there is no meaningful "
            "user-facing result, output exactly SKIP."
        )
    )
    system_content += _public_narrative_language_instruction(goal)
    # A public checkpoint is latency-sensitive and tool-free. Prefer another
    # configured lightweight endpoint when available so a slow primary
    # reasoning model does not leave the conversation silent between batches.
    narrator_model = (
        next_custom_model_fallback(
            model,
            set(),
            require_tool_use=False,
        )
        or model
    )
    request = ModelRequest(
        model=narrator_model,
        messages=[
            Message(
                role="system",
                content=system_content,
            ),
            Message(
                role="user",
                content=(
                    _build_public_action_orientation_input(goal=goal, step=step)
                    if pending_action
                    else _build_public_evidence_narrative_input(
                        goal=goal,
                        step=step,
                        convergence=convergence,
                        evidence_steps=evidence_steps,
                    )
                ),
            ),
        ],
        max_tokens=192,
        temperature=0.35,
        enable_thinking=False,
        reasoning_effort="low",
        tools=[],
    )

    raw_text = ""
    emitted = ""
    final_response = None
    visible_state = {"chars": 0}

    def _checkpoint(value: str) -> str:
        checkpoint = _safe_public_update(value)[:420].rstrip()
        if checkpoint.strip().casefold() == "skip":
            return ""
        return checkpoint

    def _ready_to_start(checkpoint: str) -> bool:
        key = re.sub(r"\s+", " ", checkpoint).strip().casefold()
        if not key:
            return False
        # A duplicate may arrive token by token. Wait until it either diverges
        # from the previous checkpoint or proves to be new content.
        if previous_key and previous_key.startswith(key):
            return False
        if len(checkpoint) >= _PUBLIC_EVIDENCE_STREAM_GATE_CHARS:
            return True
        return bool(re.search(r"[。.!！?？；;]\s*$", checkpoint))

    def _event(delta: str, *, start_new_segment: bool) -> dict[str, Any]:
        return {
            "type": "commentary_delta",
            "delta": delta,
            "progress_source": "model",
            "start_new_segment": start_new_segment,
            "iteration": iteration,
        }

    def _visible_started(state: dict[str, Any] = visible_state) -> Any:
        return state["chars"]

    for event in _iter_model_stream_with_deadline(
        router,
        request,
        _PUBLIC_EVIDENCE_NARRATIVE_TIMEOUT_S,
        _visible_started,
    ):
        if event is _MODEL_STREAM_DEADLINE:
            return emitted
        event_type = getattr(event, "type", "")
        if event_type == "text_delta":
            delta = str(getattr(event, "delta", "") or "")
            if not delta:
                continue
            raw_text += delta
            visible_state["chars"] = len(raw_text)

            # Do not render a partial sentinel (S → SK → SKIP).
            folded_raw = raw_text.strip().casefold()
            if folded_raw and "skip".startswith(folded_raw):
                continue
            checkpoint = _checkpoint(raw_text)
            if not checkpoint:
                continue
            if not emitted:
                if not _ready_to_start(checkpoint):
                    continue
                yield _event(
                    checkpoint,
                    start_new_segment=True,
                )
                emitted = checkpoint
                continue
            if checkpoint.startswith(emitted) and len(checkpoint) > len(emitted):
                suffix = checkpoint[len(emitted) :]
                yield _event(
                    suffix,
                    start_new_segment=False,
                )
                emitted = checkpoint
        elif event_type in {"done", "response_end"}:
            final_response = getattr(event, "final", None) or getattr(event, "response", None)

    # Most providers send text deltas, but preserve the final-response fallback
    # for adapters that only attach text to the terminal event.
    if not raw_text and final_response is not None:
        raw_text = str(getattr(final_response, "text", "") or "")
    checkpoint = _checkpoint(raw_text)
    if not emitted:
        if checkpoint and _ready_to_start(checkpoint):
            yield _event(
                checkpoint,
                start_new_segment=True,
            )
            emitted = checkpoint
    elif checkpoint.startswith(emitted) and len(checkpoint) > len(emitted):
        suffix = checkpoint[len(emitted) :]
        yield _event(
            suffix,
            start_new_segment=False,
        )
        emitted = checkpoint
    return emitted


def _public_tool_target(args: dict[str, Any]) -> str:
    """Return a short, non-sensitive subject for a public tool checkpoint."""
    for key in ("path", "file_path", "filepath", "filename"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return os.path.basename(value.strip())[:80]
    url = args.get("url")
    if isinstance(url, str) and url.strip():
        match = re.match(r"https?://([^/]+)", url.strip(), re.IGNORECASE)
        return (match.group(1) if match else "目标网页")[:80]
    query = args.get("query")
    if isinstance(query, str) and query.strip():
        return query.strip()[:60]
    return ""
