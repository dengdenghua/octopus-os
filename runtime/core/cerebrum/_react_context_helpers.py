"""Token estimation, context-compression helpers, and skill-catalog formatting
for the ReAct loop.

Extracted from ``react_context.py``. Pure helpers/formatting — no behaviour change.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Collection
from typing import Any

from runtime.core.cerebrum._visibility_trace import (
    active_trace,
    record_visibility,
    reset_active_trace,
    set_active_trace,
)
from runtime.core.cerebrum.capability_router import (
    activate_capabilities,
    order_skill_names,
)

_logger = logging.getLogger(__name__)

_PATH_IN_CONTEXT_RE = re.compile(
    r"(?:(?:/|\./|\.\./)[^\s'\"`:,;]+|[A-Za-z0-9_.-]+/(?:[^\s'\"`:,;]+))"
)
_RECEIPT_MARKERS = (
    "exit code",
    "exit_code",
    "passed",
    "failed",
    "error",
    "applied",
    "success",
    "成功",
    "失败",
    "通过",
)


def _content_to_text(content: Any) -> str:
    """Best-effort text projection for string or structured LLM content blocks."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part for part in (_content_to_text(item) for item in content) if part)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        nested = content.get("content")
        if nested is not None:
            return _content_to_text(nested)
        image_url = content.get("image_url")
        if isinstance(image_url, str):
            return image_url
        if isinstance(image_url, dict):
            return str(image_url.get("url") or "")
        try:
            return json.dumps(content, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(content)
    return str(content)


def _estimate_tokens(text: Any) -> int:
    text = _content_to_text(text)
    cn = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    en = len(text) - cn
    return int(cn / 1.5 + en / 4)


def _estimate_messages_tokens(messages: list) -> int:
    return sum(_estimate_tokens(getattr(m, "content", "") or "") for m in messages)


def context_budget_tokens_for_model(model: str | None) -> int:
    """Return the coarse context budget used by pressure + compression.

    The hot path intentionally avoids tokenizer imports.  Budgets are in
    the same approximate token units as ``_estimate_tokens`` so Chinese
    text no longer gets treated as if one character were one English
    character.
    """
    name = (model or "").lower()
    try:
        from runtime.platform.models.custom_model_flags import model_context_window

        configured_window = model_context_window(model or "")
    except ImportError:
        configured_window = None
    if configured_window is not None:
        # Reserve 10% for the next response, tool schemas and provider-side
        # accounting differences instead of filling the advertised window.
        return max(25_000, int(configured_window * 0.9))
    # ChatGPT-login Codex requests currently hit a 64k transport boundary even
    # when the same model family advertises a larger direct-API context window.
    # Keep this route-specific so direct gpt-5.x API models retain 100k here.
    if name.startswith(("chatgpt/", "chatgpt:")):
        return 64_000
    if any(model_id in name for model_id in ("glm-5.2", "deepseek-v4-flash", "deepseek-v4-pro")):
        return 230_400
    if "claude-3-5" in name or "claude-4" in name or "claude-sonnet" in name:
        return 150_000
    if "gpt-4o" in name or "gpt-5" in name:
        return 100_000
    return 25_000


def context_compaction_target_tokens(current_tokens: int, capacity_tokens: int) -> int:
    """Return the working-set target for the next in-turn compaction.

    Below 80% pressure the normal capacity is returned (no proactive
    compaction). At or above 80%, target 60% so one tool result cannot put the
    following provider request immediately back on the cliff.
    """

    capacity = max(1, int(capacity_tokens))
    current = max(0, int(current_tokens))
    if current >= int(capacity * 0.80):
        return max(1, int(capacity * 0.60))
    return capacity


def context_compaction_message_target_tokens(
    message_tokens: int,
    *,
    provider_context_tokens: int,
    capacity_tokens: int,
) -> int:
    """Map real provider pressure back onto the local message working set.

    Provider input usage includes native tool schemas and request envelopes
    absent from the local message estimator. Scale visible messages by the
    same ratio when that real request is near its limit, so the next tool round
    compacts before the provider rejects it.
    """

    messages = max(0, int(message_tokens))
    provider = max(0, int(provider_context_tokens))
    pressure = max(messages, provider)
    total_target = context_compaction_target_tokens(pressure, capacity_tokens)
    if total_target >= pressure or pressure <= 0:
        return max(1, int(capacity_tokens))
    return max(1, int(messages * (total_target / pressure)))


def _compress_context(
    messages: list,
    *,
    max_tokens: int = 60000,
    router: Any = None,
    model: str = "",
    is_code_mode: bool = False,
    progress_summary: Any = None,
    current_phase: Any = None,
    working_set: Any = None,
) -> list:
    total = _estimate_messages_tokens(messages)
    if total <= max_tokens:
        return messages

    keep_head = 0
    for j, m in enumerate(messages):
        if getattr(m, "role", "") == "system":
            keep_head = j + 1
        else:
            break

    keep_tail = 12
    if len(messages) <= keep_head + keep_tail:
        return _ensure_context_budget(messages, max_tokens=max_tokens)

    mid_start = keep_head
    mid_end = len(messages) - keep_tail
    mid_messages = messages[mid_start:mid_end]

    # Code trajectories need an auditable execution history.  A generated
    # summary can accidentally promote a failed shell/edit attempt into a
    # claimed file mutation or passing test, so code mode always uses the
    # deterministic observation-preserving branch below.
    if router is not None and len(mid_messages) > 4 and not is_code_mode:
        summary = _summarize_messages(mid_messages, router, model)
        if summary:
            from runtime.platform.models.llm import Message

            compressed = list(messages[:mid_start])
            compressed.append(
                Message(
                    role="system",
                    content=(f"[以下是之前对话的摘要]\n{summary}\n[摘要结束 · 最近对话如下]"),
                )
            )
            compressed.extend(messages[mid_end:])
            _logger.info(
                "context compressed with LLM summary: %d tokens → ~%d tokens",
                total,
                _estimate_messages_tokens(compressed),
            )
            return _ensure_context_budget(compressed, max_tokens=max_tokens)

    if is_code_mode:
        compressed = list(messages[:mid_start])
        continuation = _build_code_continuation_note(
            mid_messages,
            progress_summary=progress_summary,
            current_phase=current_phase,
            working_set=working_set,
        )
        if continuation:
            from runtime.platform.models.llm import Message

            # Keep the original system prefix as the immutable head. The
            # continuation is historical evidence in a user-lane envelope so
            # hard-capping cannot replace the actual system contract with this
            # generated state block.
            compressed.append(Message(role="user", content=continuation))
        compressed.extend(messages[mid_end:])
        _logger.info(
            "context compacted to deterministic code continuation: %d tokens → ~%d tokens",
            total,
            _estimate_messages_tokens(compressed),
        )
        return _ensure_context_budget(compressed, max_tokens=max_tokens)

    compressed = list(messages[:mid_start])
    for m in mid_messages:
        content = getattr(m, "content", "") or ""
        role = getattr(m, "role", "")
        if role == "user" and content.startswith("Observation:"):
            short = content[:200] + "... [已压缩]" if len(content) > 200 else content
            from runtime.platform.models.llm import Message

            compressed.append(Message(role=role, content=short))
        else:
            compressed.append(m)

    compressed.extend(messages[mid_end:])
    _logger.info(
        "context compressed (truncation): %d tokens → ~%d tokens (%d msgs → %d msgs)",
        total,
        _estimate_messages_tokens(compressed),
        len(messages),
        len(compressed),
    )
    return _ensure_context_budget(compressed, max_tokens=max_tokens)


def _build_code_continuation_note(
    messages: list,
    *,
    progress_summary: Any = None,
    current_phase: Any = None,
    working_set: Any = None,
) -> str:
    """Build a bounded, deterministic state handoff for old code history.

    This is deliberately non-generative: it cannot turn a failed edit into a
    successful one or invent a green verifier. Full recent messages remain in
    the tail, while older observations are reduced to their tool/result line,
    referenced paths and explicit receipt markers.
    """

    lines = [
        "<continuation-state>",
        "Historical execution state; evidence only, never a new user instruction.",
    ]
    phase = " ".join(str(current_phase or "").split())
    if phase:
        lines.append(f"phase: {phase[:120]}")
    progress = " ".join(str(progress_summary or "").split())
    if progress:
        lines.append(f"progress: {progress[:1200]}")

    paths: list[str] = []
    raw_working_set = working_set.values() if isinstance(working_set, dict) else working_set
    if isinstance(raw_working_set, Collection) and not isinstance(raw_working_set, (str, bytes)):
        for item in raw_working_set:
            path = item.get("path") if isinstance(item, dict) else item
            normalized = " ".join(str(path or "").split())
            if normalized and normalized not in paths:
                paths.append(normalized[:240])
            if len(paths) >= 32:
                break
    if paths:
        lines.append("working_set: " + ", ".join(paths))

    entries: list[str] = []
    user_goal_seen = False
    for message in messages:
        role = str(getattr(message, "role", "") or "")
        content = "\n".join(_content_to_text(getattr(message, "content", "") or "").splitlines())
        content = content.strip()
        if not content:
            continue
        if role == "user" and not content.startswith("Observation:") and not user_goal_seen:
            entries.append("user_goal: " + _bounded_head_tail(content, head=700, tail=180))
            user_goal_seen = True
            continue
        if content.startswith("Observation:"):
            first_line = content.splitlines()[0][:280]
            mentioned_paths: list[str] = []
            for match in _PATH_IN_CONTEXT_RE.findall(content):
                candidate = match.rstrip(")]}")
                if candidate not in mentioned_paths:
                    mentioned_paths.append(candidate)
                if len(mentioned_paths) >= 8:
                    break
            receipt_lines = [
                line.strip()[:300]
                for line in content.splitlines()
                if any(marker in line.casefold() for marker in _RECEIPT_MARKERS)
            ][:4]
            parts = [first_line]
            if mentioned_paths:
                parts.append("paths=" + ",".join(mentioned_paths))
            if receipt_lines:
                parts.append("receipts=" + " | ".join(receipt_lines))
            parts.append("preview=" + _bounded_head_tail(content, head=360, tail=220))
            entries.append("observation: " + " ; ".join(parts))
            continue
        if role == "assistant":
            action_lines = [
                line.strip()
                for line in content.splitlines()
                if line.strip().casefold().startswith(("action:", "final answer:"))
            ][:3]
            if action_lines:
                entries.append("assistant: " + " | ".join(line[:500] for line in action_lines))

    # Favor the first user objective plus the newest receipts when the old
    # segment itself is very long.
    if len(entries) > 25:
        entries = entries[:1] + entries[-24:]
    lines.extend(f"- {entry}" for entry in entries)
    lines.append(
        "Full raw receipts remain in the checkpoint/journal; re-read referenced ranges when needed."
    )
    lines.append("</continuation-state>")
    note = "\n".join(lines)
    # A continuation is a state index, not another transcript. Bound it so a
    # single compaction still leaves meaningful room for recent raw evidence.
    if _estimate_tokens(note) > 2_000:
        note = _bounded_head_tail(note, head=5_000, tail=2_500)
    return note


def _bounded_head_tail(text: str, *, head: int, tail: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= head + tail + 20:
        return normalized
    return normalized[:head].rstrip() + " …[middle compacted]… " + normalized[-tail:].lstrip()


def _ensure_context_budget(messages: list, *, max_tokens: int) -> list:
    """Hard cap compressed context when soft summarization still runs long."""
    if max_tokens <= 0 or _estimate_messages_tokens(messages) <= max_tokens:
        return messages

    keep_head = 0
    for j, m in enumerate(messages):
        if getattr(m, "role", "") == "system":
            keep_head = j + 1
        else:
            break

    head = list(messages[:keep_head])
    if _estimate_messages_tokens(head) >= max_tokens:
        out = (
            [_trim_message_to_budget(head[-1], head_tokens=0, max_tokens=max_tokens)]
            if head
            else []
        )
        _logger.info(
            "context hard-capped oversized system head: ~%d tokens → ~%d tokens (%d msgs → %d msgs)",
            _estimate_messages_tokens(messages),
            _estimate_messages_tokens(out),
            len(messages),
            len(out),
        )
        return out

    body = list(messages[keep_head:])
    sticky = [
        message
        for message in body
        if _content_to_text(getattr(message, "content", "") or "").startswith(
            "<continuation-state>"
        )
    ][:1]
    if sticky:
        sticky_source = list(sticky)
        sticky_tokens = _estimate_messages_tokens(head + sticky)
        if sticky_tokens >= max_tokens:
            sticky = [
                _trim_message_head_tail_to_budget(
                    sticky[0],
                    head_tokens=_estimate_messages_tokens(head),
                    max_tokens=max_tokens,
                )
            ]
        body = [message for message in body if message not in sticky_source]

    kept_tail: list[Any] = []
    for m in reversed(body):
        fixed_tokens = _estimate_messages_tokens(head + sticky)
        content = _content_to_text(getattr(m, "content", "") or "")
        candidate_message = m
        if content.startswith("Observation:") and _estimate_tokens(content) > 1_000:
            candidate_message = _trim_message_head_tail_to_budget(
                m,
                head_tokens=fixed_tokens,
                max_tokens=min(max_tokens, fixed_tokens + 1_000),
            )
        candidate = head + sticky + [candidate_message] + list(reversed(kept_tail))
        if _estimate_messages_tokens(candidate) <= max_tokens:
            kept_tail.append(candidate_message)
            continue
        if not kept_tail:
            if content.startswith("Observation:"):
                # One oversized recent tool receipt must not evict the compact
                # task state. Its full body is journaled/spilled; retain a
                # bounded head/tail with the tool identity and terminal status.
                observation_cap = min(max_tokens, fixed_tokens + 1_000)
                kept_tail.append(
                    _trim_message_head_tail_to_budget(
                        m,
                        head_tokens=fixed_tokens,
                        max_tokens=observation_cap,
                    )
                )
            else:
                kept_tail.append(
                    _trim_message_to_budget(
                        m,
                        head_tokens=fixed_tokens,
                        max_tokens=max_tokens,
                    )
                )
        break

    out = head + sticky + list(reversed(kept_tail))
    _logger.info(
        "context hard-capped after compression: ~%d tokens → ~%d tokens (%d msgs → %d msgs)",
        _estimate_messages_tokens(messages),
        _estimate_messages_tokens(out),
        len(messages),
        len(out),
    )
    return out


def _trim_message_head_tail_to_budget(message: Any, *, head_tokens: int, max_tokens: int) -> Any:
    """Trim a sticky continuation while retaining both objective and receipts."""

    from runtime.platform.models.llm import Message

    content = _content_to_text(getattr(message, "content", "") or "")
    role = getattr(message, "role", "") or "user"
    remaining_tokens = max(1, max_tokens - head_tokens)
    target_chars = max(200, remaining_tokens * 3)
    while target_chars > 200:
        candidate = _bounded_head_tail(
            content,
            head=max(120, int(target_chars * 0.68)),
            tail=max(60, int(target_chars * 0.32)),
        )
        if _estimate_tokens(candidate) <= remaining_tokens:
            return Message(role=role, content=candidate)
        target_chars = int(target_chars * 0.8)
    return Message(role=role, content=_bounded_head_tail(content, head=120, tail=60))


def _trim_message_to_budget(message: Any, *, head_tokens: int, max_tokens: int) -> Any:
    content = _content_to_text(getattr(message, "content", "") or "")
    role = getattr(message, "role", "")
    remaining_tokens = max(1, max_tokens - head_tokens)
    prefix = "[前文因上下文预算已截断]\n"
    prefix_tokens = _estimate_tokens(prefix)
    trimmed = _suffix_within_token_budget(content, max(1, remaining_tokens - prefix_tokens))
    if len(trimmed) < len(content):
        trimmed = prefix + trimmed
    from runtime.platform.models.llm import Message

    return Message(role=role or "user", content=trimmed)


def _suffix_within_token_budget(content: str, max_tokens: int) -> str:
    if _estimate_tokens(content) <= max_tokens:
        return content
    lo = 0
    hi = len(content)
    best = ""
    while lo <= hi:
        size = (lo + hi) // 2
        candidate = content[-size:] if size else ""
        if _estimate_tokens(candidate) <= max_tokens:
            best = candidate
            lo = size + 1
        else:
            hi = size - 1
    return best


def _summarize_messages(messages: list, router: Any, model: str) -> str:
    try:
        from runtime.platform.models.llm import Message, ModelRequest

        content_parts = []
        for m in messages:
            role = getattr(m, "role", "")
            text = _content_to_text(getattr(m, "content", "") or "")[:300]
            if text.strip():
                content_parts.append(f"[{role}] {text}")
        if not content_parts:
            return ""
        conversation = "\n".join(content_parts[-20:])
        req = ModelRequest(
            model=model or "auto",
            messages=[
                Message(
                    role="system",
                    content=(
                        "你是一个对话摘要助手。把以下对话压缩成 3-5 句话的摘要，"
                        "保留关键信息（工具调用结果、决策、发现）。严格区分工具调用尝试与"
                        "已确认成功的结果：只有明确的成功 Observation 才能写成已完成；"
                        "失败、缺少结果或不确定时必须标成未验证，禁止推断文件已写入、"
                        "测试已通过或命令已成功。只输出摘要，不要解释。"
                    ),
                ),
                Message(role="user", content=conversation),
            ],
            max_tokens=300,
            temperature=0.1,
        )
        resp = router.call(req)
        return (resp.text or "").strip()
    except (ConnectionError, TimeoutError, TypeError, ValueError):  # noqa: BLE001
        return ""


def _format_skill_catalog(
    registry: Any,
    *,
    max_skills: int = 100,
    user_context: dict | None = None,
    agent: Any = None,
    goal: str = "",
    include_names: Collection[str] | None = None,
) -> str:
    try:
        names = list(registry.all_names())
    except (AttributeError, TypeError, ValueError):  # noqa: BLE001
        return ""

    # Skills hidden from the single-agent ReAct catalog. They're
    # registered (so the bridge can dispatch when invoked) but kept
    # out of the prompt's skill listing so the model doesn't try
    # to use them when there's no swarm context.
    #
    # ``deep-research-swarm`` belongs to swarm mode only — it
    # dispatches into ``research_swarm_v1`` via TeamRunner, which
    # spawns sub-agents through ``ephemeral_runner``. That path
    # requires native ``tools`` support; offering it from a single-
    # agent loop tempts the model to call it from Agent / Inspiration
    # mode where the upstream model may not support function calling
    # and the call ends up doing nothing visible.
    # ``deep-research`` is the Agent-mode counterpart: it returns
    # the 7-phase instruction document the parent ReAct loop drives
    # via atomic web_search / fetch_url. Keep that one available.
    # ``call_agent`` is blocked by the ReAct executor because serial
    # single-subagent delegation is usually worse than the lead just doing
    # the work. Keep ``call_agent_parallel`` visible: it is the real
    # Kimi-style fan-out tool for independent lanes in Agent/Swarm mode.
    #
    # OVERRIDE: ``deep-research-swarm`` force-enabled in Agent mode per
    # user request. Risk: if the primary model lacks tool support
    # (Haiku, Inspiration, or certain DeepSeek variants), invocations
    # will fail with a cryptic error. The caller is responsible for
    # using a tool-capable model (Opus, Sonnet, Kimi, DeepSeek-R1).
    hidden_in_react: set[str] = {
        "exit_plan_mode",
        # "deep-research-swarm",  # force-enabled: user accepts tool-support risk
        "call_agent",
    }

    def _enabled(name: str) -> bool:
        try:
            return bool(registry.is_enabled(name))
        except (AttributeError, TypeError, ValueError):  # noqa: BLE001
            return True

    names = [n for n in names if n not in hidden_in_react and _enabled(n)]
    if include_names is not None:
        allowed_names = frozenset(include_names)
        names = [name for name in names if name in allowed_names]

    # A code regression preview runs in Echo' isolated Playwright browser,
    # not the desktop Electron surface.  Hide incompatible live-browser tools
    # instead of relying on the model to recover after a guaranteed failure.
    from runtime.core.cerebrum.capability_router import filter_surface_compatible_skills

    names = filter_surface_compatible_skills(
        names,
        user_context=user_context,
        goal=goal,
    )

    if agent is not None:
        allowed: set[str] | None
        try:
            allowed = set(agent.allowed_skill_union())
            agent_aff = {str(a).lower() for a in agent.affinity()}
        except (AttributeError, TypeError, ValueError):  # noqa: BLE001 - fail open to old behavior
            allowed = None
            agent_aff = set()

        if allowed is not None:
            allow_all = "*" in allowed
            try:
                from runtime.execution.all_skills import skill_kind as _classify
            except ImportError:
                _classify = lambda skill_id: "domain"  # noqa: E731

            def _visible(name: str) -> bool:
                if allow_all:
                    return True
                if name in allowed:
                    return True
                kind = _classify(name)
                if kind == "domain":
                    try:
                        skill = registry.get(name)
                        skill_aff = {
                            str(a).lower() for a in (getattr(skill, "affinity", None) or [])
                        }
                    except (AttributeError, TypeError, ValueError):  # noqa: BLE001
                        return True
                    if not skill_aff:
                        return False
                    if not agent_aff:
                        return True
                    return bool(skill_aff & agent_aff)
                return False

            names = [n for n in names if _visible(n)]

    if not names:
        return ""
    activation = activate_capabilities(
        goal,
        user_context=user_context,
        registry=registry,
    )
    # Wire the capability router's trace into the ContextVar default slot so
    # record_visibility() below appends to the same trace. If the turn owner
    # already set its own trace, active_trace() returns it and this is a no-op.
    _visibility_token = None
    if active_trace() is None and getattr(activation, "trace", None) is not None:
        _visibility_token = set_active_trace(activation.trace)
    # Capability-aware priority list. The unconditional groups (planning,
    # discovery, files, web, local execution) are always front-loaded so the
    # model keeps a stable core. Capability-conditional groups (git, browser,
    # delegation, high-level docs) are only front-loaded when the turn's
    # activation points at them — a plain "hello" or prose turn no longer pays
    # for 15 browser tools + 7 git tools it will never use. The model can still
    # discover any omitted tool via search_capabilities / query_skill, which
    # stay in the always-on group.
    _labels = set(activation.labels)
    _browser_cap = bool(_labels & {"browser-ui", "external-chrome", "code-ui-regression"})
    _uc_for_browser = user_context if isinstance(user_context, dict) else {}
    _browser_surface = str(_uc_for_browser.get("browser_surface") or "").strip().lower()
    _browser_cap = _browser_cap or _browser_surface in {"browser", "chrome"}
    _git_cap = bool(_labels & {"code", "files"})
    # Delegation lane: the capability router activates it for swarm mode or
    # delegation keywords in the goal. The single-agent guidance block
    # (_react_prompt_assembly_guidance) already tells code/audit lanes to use
    # ``call_agent_parallel`` for independent subtasks, so keep the tool
    # visible there too — otherwise the model is prompted to call a tool the
    # catalog truncates out of its 100-skill view and every attempt fails
    # with "(工具未注册) 不存在名为 ... 的 skill".
    _uc_for_delegation = _uc_for_browser
    _delegation_mode = str(_uc_for_delegation.get("mode") or "").strip().lower()
    _delegation_agent_mode = str(_uc_for_delegation.get("agent_mode") or "").strip().lower()
    _workflow_preset = str(_uc_for_delegation.get("workflow_preset") or "").strip().lower()
    _personal_mode = str(_uc_for_delegation.get("personal_mode") or "").strip().lower()
    _is_deep_mode = _workflow_preset in ("audit.deep", "audit.ultracode", "ultracode")
    _delegation_cap = bool(
        (_labels & {"delegation", "swarm"})
        or _delegation_mode in {"code"}
        or _delegation_agent_mode in {"audit"}
    )
    _delegation_bits: list[str] = []
    if _labels & {"delegation", "swarm"}:
        _delegation_bits.append("标签 delegation/swarm 命中")
    if _delegation_mode in {"code"}:
        _delegation_bits.append("mode=code")
    if _delegation_agent_mode in {"audit"}:
        _delegation_bits.append("agent_mode=audit")
    record_visibility(
        "context.delegation_cap",
        conclusion=f"委派工具{'暴露' if _delegation_cap else '隐藏'}",
        basis="; ".join(_delegation_bits)
        if _delegation_bits
        else "未命中委派条件（标签/mode/agent_mode 均未匹配）",
        mode=_delegation_mode,
        agent_mode=_delegation_agent_mode,
        labels=sorted(_labels),
    )
    _research_cap = bool(
        (_labels & {"research"})
        or _personal_mode == "research"
        or _delegation_mode in {"deep", "deep_research", "research"}
    )

    priority = [
        # Planning + tool discovery (always on — the model needs these to
        # discover anything, including tools omitted below).
        "todo_write",
        "search_capabilities",
        "query_capability",
        "use_capability",
        "search_skills",
        "query_skill",
        "execute_skill",
        # Files + code inspection/editing (always on — universal primitives).
        "list_cwd",
        "read_file",
        "file_stats",
        "code_search",
        "code_find_symbol",
        "code_analyze",
        "write_text_file",
        "edit_file",
        "multi_edit_file",
        "append_text_file",
        "edit_text_file",
        # Web research + URL reading (always on).
        "web_search",
        "web_fetch",
        "fetch_url",
        # Local execution + background jobs (always on).
        "exec_shell",
        "ipython",
        "background_exec",
        "read_background_output",
        "kill_background_exec",
        # Git workflow — only for code/files turns.
        *(
            [
                "git_status",
                "git_diff",
                "git_log",
                "git_add",
                "git_commit",
                "git_branch",
            ]
            if _git_cap
            else []
        ),
        # Delegation + shared blackboard — only for swarm/delegation turns.
        *(
            [
                "call_agent_parallel",
                "bb_write",
                "bb_read",
                "bb_keys",
                *(["run_orchestration"] if _is_deep_mode else []),
            ]
            if _delegation_cap
            else []
        ),
        # Browser/Desktop observation for UI work — only for browser turns.
        *(
            [
                "browser_navigate",
                "live_browser_state",
                "live_browser_current_url",
                "live_browser_navigate",
                "live_browser_extract",
                "live_browser_find",
                "live_browser_click",
                "live_browser_type",
                "live_browser_wait",
                "live_browser_scroll",
                "live_browser_screenshot",
                "browser_get",
                "browser_extract",
                "browser_screenshot",
                "browser_click",
                "browser_type",
                "browser_upload",
                "screen_capture",
                "screen_info",
            ]
            if _browser_cap
            else []
        ),
        # High-level document/research workflows — only for research turns.
        *(["deep-research", "report-writing", "docx"] if _research_cap else []),
    ]
    priority_set = set(priority)
    names = [n for n in priority if n in names] + [n for n in names if n not in priority_set]
    names = order_skill_names(
        names,
        activation=activation,
        registry=registry,
    )
    # TF-IDF relevance selection · when the catalog would overflow
    # ``max_skills``, keep the pinned priority tools and fill the
    # remaining slots with the skills most relevant to the goal
    # (TF-IDF over name+summary+description+affinity — zero deps,
    # deterministic). The priority/capability ordering above already
    # ranks the full list; this step only decides which non-priority
    # skills survive the truncation, so a 300-skill registry no longer
    # evicts goal-relevant skills behind 100 alphabetically-lucky ones.
    _catalog_total = len(names)
    if goal and len(names) > max_skills:
        try:
            from runtime.execution.suckers.search import TfIdfSkillSearcher

            pinned = [n for n in names if n in priority_set]
            rest = [n for n in names if n not in priority_set]
            budget = max(0, max_skills - len(pinned))
            relevant_order = TfIdfSkillSearcher(registry).search(goal, k=budget)
            relevant = [n for n in relevant_order if n in rest]
            relevant_set = set(relevant)
            # TF-IDF intentionally returns only positive matches. Fill any
            # unused slots from the already capability-ordered remainder so a
            # narrow query does not turn a 100-entry budget into a 2-entry
            # catalog and silently hide useful discovery options.
            fallback = [n for n in rest if n not in relevant_set]
            names = pinned + (relevant + fallback)[:budget]
        except Exception:  # noqa: BLE001 — selection is an optimization, never fatal
            pass
    record_visibility(
        "context.skill_catalog",
        conclusion=(
            f"技能目录 {_catalog_total} -> {min(len(names), max_skills)} 保留"
            if _catalog_total > min(len(names), max_skills)
            else f"技能目录 {min(len(names), max_skills)} 条（未截断）"
        ),
        basis=(
            "pinned 优先 + TF-IDF 选择"
            if goal and _catalog_total > max_skills
            else "总数未超过 max_skills"
        ),
        total=_catalog_total,
        kept=min(len(names), max_skills),
        truncated=max(0, _catalog_total - min(len(names), max_skills)),
        max_skills=max_skills,
    )
    lines: list[str] = ["可用工具 (skill):"]
    for name in names[:max_skills]:
        try:
            skill = registry.get(name)
            # Progressive disclosure (echo optimisation lane C):
            # the catalog only lists name + ≤30字 short description.
            # The model can call ``query_skill(name)`` for the full
            # parameter schema + long description when it actually
            # needs to invoke the skill. This keeps the system prompt
            # small and stable so prompt cache stays warm.
            short = (getattr(skill, "summary", "") or "").strip() or (
                getattr(skill, "effective_summary", "") or ""
            ).strip()
            if not short:
                # Fall back to first sentence of description, capped
                # at 30 characters. Prefer to break at the first
                # punctuation so we don't dangle mid-word.
                full = (getattr(skill, "description", "") or "").strip()
                if full:
                    # Take everything up to the first sentence terminator
                    # / newline; if none, use first 30 chars.
                    cut = len(full)
                    for sep in ("。", ".", "\n", "·", ";", "；"):
                        idx = full.find(sep)
                        if 0 < idx < cut:
                            cut = idx
                    short = full[: min(cut, 30)].strip()
            if not short:
                short = "(无描述)"
        except (AttributeError, TypeError, KeyError, ValueError):  # noqa: BLE001
            short = "(无描述)"
        lines.append(f"  - {name}: {short}")
    _catalog_omitted = max(0, _catalog_total - min(len(names), max_skills))
    if _catalog_omitted:
        lines.append(f"  ... (还有 {_catalog_omitted} 个,可搜索发现)")
    lines.append(
        "提示: 上面只列名+短描述; 调用前若需完整参数 schema 请用 "
        '`query_skill(name="<skill_name>")`。',
    )
    lines.append(
        "被目录省略的只读 skill 可在 search_skills/query_skill 后通过 "
        '`execute_skill(name="<skill_name>", args={...})` 调用；写入/执行类仍走正常工具入口。'
    )
    lines.append(
        "Capability-first: prefer `search_capabilities`, "
        "`query_capability`, and `use_capability` before low-level child skills.",
    )
    # When no capability lane is active (vague goal), surface a lightweight
    # capability map so the model still knows the lanes exist even if their
    # full tool sets were trimmed away. It stays discoverable via
    # search_capabilities — the index is only a name-level hint, not schemas.
    # Skip it when ``include_names`` explicitly restricts the toolset: the
    # generic lane index would list tools that were intentionally filtered out.
    if not activation.active and include_names is None:
        try:
            from runtime.core.cerebrum.capability_router import capability_index

            _idx = capability_index()
            if _idx:
                lines.append("")
                lines.append("<capability-index>")
                lines.append("可用能力与代表工具(未激活具体路由, 供参考):")
                lines.append(_idx)
                lines.append("不确定用哪个工具时先用 search_capabilities 查询完整工具集。")
                lines.append("</capability-index>")
        except (ImportError, AttributeError):  # noqa: BLE001 — best-effort hint
            pass
    if _visibility_token is not None:
        reset_active_trace(_visibility_token)
    return "\n".join(lines)
