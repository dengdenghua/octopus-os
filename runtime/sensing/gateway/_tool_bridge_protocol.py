"""Public checkpoint / protocol-tag cleaning + narration helpers.

Extracted from ``tool_bridge.py`` (the Claude-native agentic loop). This
satellite owns the regexes and helpers that:

* strip leaked structural protocol tags from literal text (``text_delta``);
* clean / condense model-authored tool-round checkpoints for the main
  timeline (``_native_public_checkpoint`` and friends);
* run the bounded, optional model-authored public-narration calls
  (``_generate_native_*_checkpoint``).

The parent ``tool_bridge`` module re-exports every name here so existing
importers and tests are unchanged.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from typing import Any

from runtime.core.cerebrum.react_guards import _explicit_source_paths
from runtime.platform.models import Budget, CostEntry
from runtime.sensing.model_router.models import Message, ModelRequest, ToolCall

from ._tool_bridge_native import (
    _NATIVE_STREAM_DEADLINE,
    _iter_native_model_stream_with_deadline,
)

# Live text streaming in the native loop holds back this many chars of the
# unterminated round text so a tool-call envelope split across chunks can
# never half-leak (must exceed the longest guarded marker).
_NATIVE_TEXT_STREAM_TAIL_MARGIN = 48
# Markers that mean "this round's text is (or contains) a serialized tool
# call, not user-visible answer prose" — detected case-insensitively.
# Aligned with what ``_recover_named_xml_tool_calls`` / the ReAct-side
# pre-stream guards treat as envelopes. Streaming for the round falls
# back to buffered mode the moment one appears.
_NATIVE_TEXT_STREAM_SUPPRESS_MARKERS = (
    "<tool_call",
    "<tool_invocation",
    "<function=",
)
# Leading progress label models imitate from the ReAct "Update:" nudge
# ("Update: 已完成…" / "Progress: …"). It is a protocol artifact, not
# user prose: checkpoints strip it (``_native_public_checkpoint``) and so
# must the live/buffered round-text path, otherwise the label leaks into
# the visible timeline raw.
_NATIVE_ROUND_TEXT_PREFIX_RE = re.compile(r"^\s*(?:Update|Progress)\s*:\s*", re.IGNORECASE)
# Structural protocol tag names that must ride structured fields
# (reasoning / tool_use / tool_result), never literal text_delta prose.
# Shared between checkpoint detection (``_PUBLIC_CHECKPOINT_PROTOCOL_RE``)
# and the streaming text_delta strip (``strip_leaked_protocol_tags``) so
# the two paths cannot drift — adding a tag name here protects both.
_LEAKED_PROTOCOL_TAG_NAMES = (
    r"tool|tool_use|tool_call|function|thinking|thought|"
    r"TextBlock|ReasoningBlock|ToolCallBlock|ToolResultBlock|ExecutionBlock|"
    r"ThinkingBlock"
)
_PUBLIC_CHECKPOINT_PROTOCOL_RE = re.compile(
    r"(?:<[/]?(?:" + _LEAKED_PROTOCOL_TAG_NAMES + r")\b"
    r"|tool_use_id|```|^\s*[{[]|\b(?:Action|Observation|Thought|"
    r"Final Answer)\s*:)",
    re.IGNORECASE | re.MULTILINE,
)
# Paired structural blocks leaked as literal text — the tag AND its
# content belong in a structured field, so the whole span is removed.
_LEAKED_PROTOCOL_BLOCK_RE = re.compile(
    r"`?<(?:(?:Reasoning|ToolCall|ToolResult|Thinking|Execution)Block)\b[^<>`]*>"
    r"[\s\S]*?</(?:(?:Reasoning|ToolCall|ToolResult|Thinking|Execution)Block)>`?",
    re.IGNORECASE,
)
# Individual opening/closing tags that survive the paired-block pass
# (or arrived split across deltas). Covers the same tag-name set as
# ``_PUBLIC_CHECKPOINT_PROTOCOL_RE``.
_LEAKED_PROTOCOL_TAG_RE = re.compile(
    r"`?</?(?:" + _LEAKED_PROTOCOL_TAG_NAMES + r")\b[^<>`]*>`?",
    re.IGNORECASE,
)

_PUBLIC_CHECKPOINT_TOOL_RE = re.compile(
    r"\b(?:read_file|read_text_file|list_cwd|grep_text|glob_files|exec_shell|"
    r"shell_command|run_command|todo_write|write_todos|apply_patch|"
    r"str_replace|edit_file|write_file|web_search)\b",
    re.IGNORECASE,
)
_PUBLIC_CHECKPOINT_SECRET_RE = re.compile(
    r"(?:sk-[\w-]+|bearer\s+[a-z0-9._-]+|api[_-]?key|token|secret|"
    r"credential|password|passwd|id_rsa|id_ed25519|\.pem\b|\.key\b)",
    re.IGNORECASE,
)
_PUBLIC_CHECKPOINT_STAGE_RE = re.compile(
    r"^\s*(?:phase|stage|step|阶段|步骤)\s*[\d一二三四五六七八九十]+(?:\.\d+)?"
    r"\s*[:：.)、-]?\s*",
    re.IGNORECASE,
)
_PUBLIC_CHECKPOINT_BOILERPLATE_RE = re.compile(
    r"^(?:(?:我|我们)?(?:还在|正在|继续|接着|马上|即将)"
    r"(?:思考|处理|执行|整理|分析|工作|调用工具)|"
    r"(?:still|currently|continuing to|about to)\s+"
    r"(?:think|work|process|analy[sz]e|execute|run))[。.!！\s]*$",
    re.IGNORECASE,
)
_PUBLIC_CHECKPOINT_CODE_RE = re.compile(
    r"(?:^|\n)\s*(?:async\s+)?(?:def|class|function|const|let|var|return|raise)\b"
    r"|(?:^|\n)\s*[@#][A-Za-z_]\w*|[{}]\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_LONG_RUNNING_PUBLIC_NARRATIVE_TOOLS = {
    "exec_shell",
    "run_command",
    "shell",
    "call_agent",
    "call_agent_parallel",
    "deep-research",
    "deep_research",
    "deep-research-swarm",
}
_LONG_RUNNING_PUBLIC_NARRATIVE_PREFIXES = (
    "browser_",
    "computer_",
)

# Realtime conversation should not become a silent tool log merely because
# local reads/searches finish quickly. Every meaningful, otherwise-silent
# evidence batch gets one model-authored checkpoint by default. The narration
# call is strictly bounded and optional, so a weak provider cannot stall the
# underlying task just to produce progress prose.
PUBLIC_NARRATIVE_SILENCE_S = 0.0
PUBLIC_NARRATIVE_TIMEOUT_S = 6.0


def strip_leaked_protocol_tags(text: str) -> str:
    """Remove structural protocol tags that leaked into literal text.

    Models occasionally emit ``<ReasoningBlock>...</ReasoningBlock>`` and
    friends as literal text instead of routing them through the structured
    reasoning / tool_use / tool_result fields.  The checkpoint path rejects
    such payloads wholesale (see ``_PUBLIC_CHECKPOINT_PROTOCOL_RE``); the
    streaming ``text_delta`` path cannot drop the whole message, so it
    strips the leaked tags and keeps the surrounding prose.

    Both paired blocks (tag + content) and individual opening/closing
    tags are removed, using the same tag-name set as the checkpoint
    detector.  Normal prose is untouched: the regexes anchor on the
    structural tag names, not on ReAct prefixes, code fences, or
    JSON-looking prefixes, so legitimate text survives.
    """
    if not text:
        return text
    return _LEAKED_PROTOCOL_TAG_RE.sub("", _LEAKED_PROTOCOL_BLOCK_RE.sub("", text))


def _native_public_checkpoint(text: str) -> str:
    """Return a compact tool-round preamble safe for the main timeline."""
    value = " ".join(str(text or "").strip().split())
    value = _NATIVE_ROUND_TEXT_PREFIX_RE.sub("", value)
    value = _PUBLIC_CHECKPOINT_STAGE_RE.sub("", value).strip()
    value = re.sub(r"^#{1,6}\s+", "", value).strip()
    for marker in ("**", "__"):
        if value.startswith(marker) and value.endswith(marker) and len(value) > 4:
            value = value[len(marker) : -len(marker)].strip()
    if not value or len(value) < 8:
        return ""
    if (
        _PUBLIC_CHECKPOINT_PROTOCOL_RE.search(value)
        or _PUBLIC_CHECKPOINT_TOOL_RE.search(value)
        or _PUBLIC_CHECKPOINT_SECRET_RE.search(value)
        or _PUBLIC_CHECKPOINT_BOILERPLATE_RE.fullmatch(value)
    ):
        return ""
    if len(value) > 420:
        return ""
    if value.count("\n") >= 2 and _PUBLIC_CHECKPOINT_CODE_RE.search(value):
        return ""
    sentence_ends = list(re.finditer(r"[。！？!?]|\.(?:\s+|$)", value))
    if len(sentence_ends) >= 2:
        value = value[: sentence_ends[1].end()].strip()
    return value[:360].rstrip()


def _native_calls_with_public_checkpoint(
    calls: list[ToolCall],
) -> tuple[list[ToolCall], str]:
    """Extract the transient public field before dispatching native calls."""
    cleaned_calls: list[ToolCall] = []
    checkpoint = ""
    for call in calls:
        payload = dict(call.input or {})
        candidate = payload.pop("public_update", "")
        confirmed_fact = payload.pop("confirmed_fact", "")
        next_action = payload.pop("next_action", "")
        evidence_checkpoint = ""
        if isinstance(confirmed_fact, str) and isinstance(next_action, str):
            fact = confirmed_fact.rstrip("。.!！?？；; ")
            next_step = next_action.lstrip("；;，, ")
            if fact and next_step:
                evidence_checkpoint = f"{fact}；{next_step}"
        if not checkpoint:
            checkpoint = _native_public_checkpoint(
                evidence_checkpoint or (candidate if isinstance(candidate, str) else "")
            )
        cleaned_calls.append(call.model_copy(update={"input": payload}))
    return cleaned_calls, checkpoint


def _public_narrative_silence_s(user_context: dict[str, Any]) -> float:
    raw = user_context.get("public_narrative_silence_s")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return max(0.0, min(float(raw), 60.0))
    return PUBLIC_NARRATIVE_SILENCE_S


def _ordered_read_handoffs_requested(goal: str) -> bool:
    """Whether the user explicitly asked to hear a result between read batches."""

    if len(_explicit_source_paths(goal)) < 2:
        return False
    return bool(
        re.search(
            r"(?:每(?:一)?批.{0,28}(?:结束|读完|读取后).{0,28}(?:确认|告诉|说出|汇报)|"
            r"after\s+each\s+(?:read\s+)?batch.{0,40}(?:confirm|tell|report|say))",
            goal,
            re.IGNORECASE,
        )
    )


def _model_response_actual_cost(final: Any) -> CostEntry:
    """Return one canonical actual-cost receipt from a provider response.

    Routers historically populated the top-level token counters more
    consistently than ``final.cost`` while USD only exists on ``final.cost``.
    Prefer explicit top-level tokens when non-zero, fall back to the structured
    cost entry, and never add the two representations together.
    """

    reported = getattr(final, "cost", None)
    reported_in = int(getattr(reported, "tokens_in", 0) or 0)
    reported_out = int(getattr(reported, "tokens_out", 0) or 0)
    top_level_in = int(getattr(final, "input_tokens", 0) or 0)
    top_level_out = int(getattr(final, "output_tokens", 0) or 0)
    return CostEntry(
        tokens_in=top_level_in or reported_in,
        tokens_out=top_level_out or reported_out,
        usd=float(getattr(reported, "usd", 0.0) or 0.0),
        latency_ms=float(getattr(reported, "latency_ms", 0.0) or 0.0),
    )


def _generate_native_public_checkpoint(
    router: Any,
    *,
    model: str,
    messages: list[Message],
    prompt: str,
    budget: Budget | None = None,
) -> tuple[str, CostEntry]:
    """Run one small tools-disabled model continuation for public narration."""
    from runtime.sensing.model_router.rescue_policy import (
        next_custom_model_fallback as _next_custom_model_fallback,
    )

    message_text = " ".join(
        str(message.content) for message in messages if isinstance(message.content, str)
    )
    if re.search(r"[\uac00-\ud7af]", message_text):
        prompt += "\nThe user's language is Korean. Write this update in Korean."
    elif re.search(r"[\u3040-\u30ff]", message_text):
        prompt += "\nThe user's language is Japanese. Write this update in Japanese."
    elif re.search(r"[\u3400-\u9fff]", message_text):
        prompt += (
            "\nThe user's language is Simplified Chinese. Write this update in Simplified Chinese."
        )
    checkpoint_messages = list(messages)
    if (
        checkpoint_messages
        and checkpoint_messages[-1].role == "user"
        and isinstance(checkpoint_messages[-1].content, list)
    ):
        # Anthropic requires tool_result blocks to directly follow the
        # assistant tool_use turn. Merge the instruction into that same user
        # message instead of creating an invalid consecutive user message.
        merged_content = [
            *checkpoint_messages[-1].content,
            {"type": "text", "text": prompt},
        ]
        checkpoint_messages[-1] = Message(role="user", content=merged_content)
    else:
        checkpoint_messages.append(Message(role="user", content=prompt))
    narrator_model = (
        _next_custom_model_fallback(
            model,
            set(),
            require_tool_use=False,
        )
        or model
    )
    request = ModelRequest(
        model=narrator_model,
        messages=checkpoint_messages,
        max_tokens=180,
        temperature=0.4,
        tools=[],
    )
    reservation_id = (
        budget.reserve(CostEntry(tokens_out=request.max_tokens)) if budget is not None else None
    )
    chunks: list[str] = []
    actual_cost = CostEntry()
    visible = {"started": False}
    try:
        for event in _iter_native_model_stream_with_deadline(
            router,
            request,
            PUBLIC_NARRATIVE_TIMEOUT_S,
            visible_started=lambda: visible["started"],
        ):
            if event is _NATIVE_STREAM_DEADLINE:
                break
            if event.type == "text_delta":
                chunks.append(event.delta)
                visible["started"] = True
            elif event.type == "done":
                final = getattr(event, "final", None)
                if final is not None:
                    actual_cost = _model_response_actual_cost(final)
                    if not chunks:
                        text = str(getattr(final, "text", "") or "")
                        if text:
                            chunks.append(text)
                break
    finally:
        if budget is not None and reservation_id is not None:
            budget.commit(reservation_id, actual_cost)
    checkpoint = _native_public_checkpoint("".join(chunks))
    if checkpoint.strip().casefold() == "skip":
        checkpoint = ""
    return checkpoint, actual_cost


def _generate_native_evidence_checkpoint(
    router: Any,
    *,
    model: str,
    messages: list[Message],
    budget: Budget | None = None,
) -> tuple[str, CostEntry]:
    """Ask the model—not runtime templates—to narrate a quiet tool result."""
    return _generate_native_public_checkpoint(
        router,
        model=model,
        messages=messages,
        budget=budget,
        prompt=(
            "[PUBLIC PROGRESS UPDATE]\n"
            "Based only on the completed tool results immediately above, "
            "write one short user-facing update of at most two sentences. "
            "State one concrete result that is now known and the next "
            "decision or action. Do not mention tool names, system prompts, "
            "protocols, hidden reasoning, or claim unobserved results. This "
            "is a progress update, not the final answer. Use plain prose, not "
            "a heading, bullet list, or numbered list. If the results add "
            "no meaningful user-facing evidence, output exactly SKIP."
        ),
    )


def _batch_needs_live_public_narrative(calls: list[ToolCall]) -> bool:
    for call in calls:
        name = call.name.strip().lower()
        if name in _LONG_RUNNING_PUBLIC_NARRATIVE_TOOLS or name.startswith(
            _LONG_RUNNING_PUBLIC_NARRATIVE_PREFIXES
        ):
            return True
    return False


def _generate_native_action_checkpoint(
    router: Any,
    *,
    model: str,
    messages: list[Message],
    calls: list[ToolCall],
    budget: Budget | None = None,
) -> tuple[str, CostEntry]:
    """Narrate the purpose of a likely-long batch before it starts running."""
    return _generate_native_public_checkpoint(
        router,
        model=model,
        messages=messages,
        budget=budget,
        prompt=(
            "[PUBLIC ACTION UPDATE]\n"
            f"A batch of {len(calls)} potentially long operation(s) is about to run. "
            "Write one short user-facing sentence explaining what you are checking "
            "or changing now and what observable signal will guide the next step. "
            "Do not mention tool names, protocols, system prompts, hidden reasoning, "
            "refer to yourself as the system, use markdown emphasis, or claim the "
            "operation has already finished. This is not the final answer. If no "
            "meaningful update can be stated, output exactly SKIP."
        ),
    )


def _public_checkpoint_language(goal: str) -> str:
    if re.search(r"[\u3400-\u9fff]", goal):
        return "zh"
    if re.search(r"[\uac00-\ud7af]", goal):
        return "ko"
    if re.search(r"[\u3040-\u30ff]", goal):
        return "ja"
    return "en"


def _safe_public_source_title(value: str) -> str:
    candidate = " ".join(str(value or "").split()).strip()
    if not candidate:
        return ""
    if (
        _PUBLIC_CHECKPOINT_PROTOCOL_RE.search(candidate)
        or _PUBLIC_CHECKPOINT_TOOL_RE.search(candidate)
        or _PUBLIC_CHECKPOINT_SECRET_RE.search(candidate)
    ):
        return ""
    return candidate


def _render_result_checkpoint(
    *,
    language: str,
    kind: str,
    count: int,
    titles: list[str] | None = None,
) -> str:
    safe_titles = [title for title in titles or [] if title][:3]
    if language == "zh":
        if safe_titles:
            rendered = "、".join(f"《{title}》" for title in safe_titles)
            return f"已拿到 {rendered} 等可用资料；接下来基于这些证据继续收束判断。"
        if kind == "web_fetch":
            return f"已读取 {count} 份网页正文；接下来基于正文证据整理判断。"
        if kind == "web_search":
            return f"已完成 {count} 项资料检索并取得可用结果；接下来打开可靠来源核验。"
        return ""
    if language == "ja":
        if safe_titles:
            rendered = "、".join(f"「{title}」" for title in safe_titles)
            return f"{rendered} などの利用できる資料を確認しました。次はその証拠をもとに判断を整理します。"
        if kind == "web_fetch":
            return f"{count} 件のページ本文を確認しました。次は本文の証拠をもとに判断を整理します。"
        if kind == "web_search":
            return f"{count} 件の検索結果を得ました。次は信頼できる本文で確認します。"
        return ""
    if language == "ko":
        if safe_titles:
            rendered = "、".join(f"「{title}」" for title in safe_titles)
            return f"{rendered} 등 사용할 수 있는 자료를 확인했습니다. 다음에는 이 근거를 바탕으로 판단을 정리하겠습니다."
        if kind == "web_fetch":
            return f"웹 본문 {count}건을 확인했습니다. 다음에는 본문 근거를 바탕으로 판단을 정리하겠습니다."
        if kind == "web_search":
            return f"검색 결과 {count}건을 확보했습니다. 다음에는 신뢰할 수 있는 원문으로 확인하겠습니다."
        return ""
    if safe_titles:
        rendered = ", ".join(f"“{title}”" for title in safe_titles)
        return f"I found usable evidence from {rendered}; next I’ll synthesize what those sources support."
    if kind == "web_fetch":
        return f"I read {count} webpage body {'entry' if count == 1 else 'entries'}; next I’ll synthesize what the text supports."
    if kind == "web_search":
        return f"I found {count} usable search {'result' if count == 1 else 'results'}; next I’ll open reliable sources to verify them."
    return ""


def _native_result_checkpoint(
    calls: list[ToolCall],
    result_blocks: list[dict[str, Any]],
    *,
    goal: str = "",
) -> str:
    """Build a factual public checkpoint from completed tool results.

    Some native-tool models emit protocol calls with zero surrounding prose.
    In that case the UI would otherwise show only execution rows.  Extracting
    source titles from the actual observations gives the user a concise,
    evidence-backed stage result without inventing model reasoning.
    """
    successful: list[tuple[ToolCall, str]] = []
    language = _public_checkpoint_language(goal)
    for call, block in zip(calls, result_blocks, strict=False):
        if block.get("is_error") or call.name == "todo_write":
            continue
        successful.append((call, str(block.get("content") or "")))
    if not successful:
        return ""

    titles: list[str] = []

    def _collect(value: Any) -> None:
        if len(titles) >= 3:
            return
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).lower() in {"title", "page_title", "name"} and isinstance(nested, str):
                    candidate = _safe_public_source_title(nested)
                    if 8 <= len(candidate) <= 140 and candidate not in titles:
                        titles.append(candidate)
                else:
                    _collect(nested)
        elif isinstance(value, list):
            for nested in value:
                _collect(nested)

    for _call, output in successful:
        try:
            _collect(json.loads(output))
        except (json.JSONDecodeError, TypeError, ValueError):
            for match in re.finditer(
                r"(?:title|page_title)['\"\s:=]+([^\n\r]{8,140})",
                output,
                flags=re.IGNORECASE,
            ):
                candidate = _safe_public_source_title(match.group(1).strip(" '\",}"))
                if candidate and candidate not in titles:
                    titles.append(candidate)
                if len(titles) >= 3:
                    break

    names = {call.name for call, _output in successful}
    if titles:
        return _render_result_checkpoint(
            language=language,
            kind="sources",
            count=len(successful),
            titles=titles,
        )
    if names & {"web_fetch", "fetch_url", "read_url", "browser_read"}:
        return _render_result_checkpoint(
            language=language,
            kind="web_fetch",
            count=len(successful),
        )
    if names & {"web_search", "search_web", "browser_search"}:
        return _render_result_checkpoint(
            language=language,
            kind="web_search",
            count=len(successful),
        )
    local_reads = [
        (call, output)
        for call, output in successful
        if call.name in {"read_file", "read_text_file", "read_file_range"}
    ]
    if local_reads:
        call, output = local_reads[-1]
        path = str((call.input or {}).get("path") or "").replace("\\", "/")
        label = os.path.basename(path) or "目标文件"
        size_match = re.search(r'"size"\s*:\s*(\d+)', output)
        complete = bool(re.search(r'"truncated"\s*:\s*false', output, re.IGNORECASE))
        size = int(size_match.group(1)) if size_match else None
        requested = [item.replace("\\", "/").lstrip("./") for item in _explicit_source_paths(goal)]
        current = path.lstrip("./")
        next_path = ""
        with contextlib.suppress(ValueError, IndexError):
            next_path = requested[requested.index(current) + 1]
        next_label = os.path.basename(next_path) if next_path else ""
        if language == "zh":
            if size is not None:
                fact = (
                    f"已完整取得 {label} 的 {size:,} 字节内容"
                    if complete
                    else f"已取得 {label} 的 {size:,} 字节可用内容"
                )
            else:
                fact = f"已取得 {label} 的实际内容"
            return (
                f"{fact}；接下来核对 {next_label}。"
                if next_label
                else f"{fact}；所需证据已经齐全，现在收束结论。"
            )
        if language == "ja":
            fact = (
                f"{label} の {size:,} バイトの内容を取得しました"
                if size is not None
                else f"{label} の実際の内容を取得しました"
            )
            return (
                f"{fact}。次に {next_label} を確認します。"
                if next_label
                else f"{fact}。必要な証拠がそろったので結論をまとめます。"
            )
        if language == "ko":
            fact = (
                f"{label}의 {size:,}바이트 내용을 확보했습니다"
                if size is not None
                else f"{label}의 실제 내용을 확보했습니다"
            )
            return (
                f"{fact}. 다음에는 {next_label}을 확인하겠습니다."
                if next_label
                else f"{fact}. 필요한 근거가 모였으니 결론을 정리하겠습니다."
            )
        fact = (
            f"I now have all {size:,} bytes of {label}"
            if size is not None and complete
            else f"I now have the actual contents of {label}"
        )
        return (
            f"{fact}; next I’ll check {next_label}."
            if next_label
            else f"{fact}; the requested evidence is complete, so I’m wrapping up the conclusion."
        )
    return ""
