"""Shared mutable assembly state for the PHASE 3 prompt-assembly split,
plus the final ``messages`` composition and the memory / identity /
team-roster sections.

Leaf module — contains the ``_AssemblyState`` dataclass, ``_assemble_messages``,
and ``_assemble_memory_sections``. Imported by the ``_react_prompt_assembly_*``
submodules and the orchestrator in ``react_prompt_assembly.py``. Never imports
any react_* sibling, so there is no import cycle.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from runtime.core.cerebrum.react_context import (
    _build_code_context_prelude,
    _build_user_message_content,
)
from runtime.core.cerebrum.stable_prompt import render_volatile_as_user_message
from runtime.platform.models.llm import Message

_logger = logging.getLogger(__name__)


@dataclass
class _AssemblyState:
    """Everything the PHASE 3 assembly helpers read / write.

    ``system_parts`` / ``volatile_parts`` are the two shared buffers the
    section helpers append to; every scalar that must survive to the final
    ``_PromptAssembly`` is stored as a field so the orchestrator can read it
    back after each helper runs.
    """

    # ── inputs (set by the orchestrator) ────────────────────────────────
    intent: Any
    agent: Any
    stack: Any
    executor: Any
    approval_provider: Any
    resume_task_id: Any
    planning_mode: bool
    tools_active: bool
    native_mode: bool
    no_tool_turn: bool
    strict_explicit_reads: bool
    camouflage_suffix: str
    max_iterations: int
    max_tokens_budget: Any
    max_usd_budget: Any

    # ── derived inputs ──────────────────────────────────────────────────
    user_context: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    effective_goal: str = ""

    # ── shared buffers ──────────────────────────────────────────────────
    system_parts: list = field(default_factory=list)
    volatile_parts: list = field(default_factory=list)

    # ── early / mode resolution ─────────────────────────────────────────
    work_mode: Any = None
    wp: Any = None
    effective_wp: Any = None
    resume_context_prompt: str = ""
    is_goal_mode: bool = False
    is_code_mode: bool = False
    read_only_turn: bool = False
    observed_read_sequence: bool = False
    observed_read_groups: tuple = ()
    grounding_sources: list = field(default_factory=list)
    grounded_source_paths: frozenset = frozenset()
    final_guard_grounded_source_paths: frozenset = frozenset()
    # Tool observations from EARLIER turns of this thread, extracted from the
    # assembled conversation history. Threaded into the research grounding
    # guards so cross-turn facts aren't misflagged as fabricated.
    prior_grounding_text: str = ""
    browser_operation_mode: bool = False
    chrome_operation_mode: bool = False
    guard_impasse_state: dict = field(default_factory=dict)
    realtime_public_orientation_requested: bool = False
    mode_value: Any = None
    capability_mode_value: Any = None
    agent_mode_value: Any = None
    workflow_preset_value: Any = None
    workflow_mode_value: Any = None
    completion_policy_value: Any = None
    is_plan_or_spec_composer: bool = False
    mode_contract_value: Any = None
    personal_mode_value: Any = None
    project_signals: Any = None
    is_swarm_mode: bool = False
    is_research_mode: bool = False
    active_max_tokens_budget: Any = None
    active_max_usd_budget: Any = None
    budget_pause_threshold: float = 0.0
    budget_auto_pause_enabled: bool = False
    todo_protocol_mode: Any = None
    todo_protocol_required: bool = False
    todo_protocol_visible: bool = False
    goal_for_mode: str = ""
    browser_regression_enabled: bool = False
    browser_regression_preview_url: Any = None

    # ── tool / capability sections ──────────────────────────────────────
    file_inspection_tools_visible: bool = False
    capability_activation: Any = None

    # ── final messages ──────────────────────────────────────────────────
    messages: list = field(default_factory=list)


def _assemble_memory_sections(state: _AssemblyState) -> None:
    """Soul / constitution / team roster / memory recall / camouflage."""
    if state.agent is not None and getattr(state.agent, "soul", None):
        try:
            from runtime.execution.agents.loader import compose_runtime_soul

            runtime_soul = compose_runtime_soul(state.agent)
        except (ImportError, AttributeError):
            _logger.debug("compose_runtime_soul not available", exc_info=True)
            runtime_soul = state.agent.soul
        if runtime_soul:
            state.system_parts.insert(0, runtime_soul)
    try:
        from runtime.safety.validation import get_constitution_summary

        _constitution = get_constitution_summary()
    except ImportError:
        _logger.debug("constitution module not available", exc_info=True)
        _constitution = ""
    if _constitution:
        state.system_parts.append(_constitution)
    try:
        from runtime.core.cerebrum.llm_planner import (
            _render_team_roster_section,
        )

        _team_block = _render_team_roster_section(state.user_context or {})
    except (ImportError, AttributeError):
        _logger.debug("team roster rendering not available", exc_info=True)
        _team_block = ""
    if _team_block:
        state.system_parts.append(_team_block)

    try:
        from runtime.memory.runtime_state.hub import (
            MemoryHub,
            MemoryQuery,
            format_records_for_prompt,
        )

        _agent_id_for_memory = (
            str(getattr(state.agent, "agent_id", "") or "") if state.agent is not None else None
        )
        _project_for_memory = (
            str(state.wp).strip() if isinstance(state.wp, str) and str(state.wp).strip() else None
        )
        _team_id_for_memory = state.user_context.get("team_id") or state.metadata.get("team_id")
        _team_id_for_memory = (
            str(_team_id_for_memory).strip()
            if isinstance(_team_id_for_memory, str) and str(_team_id_for_memory).strip()
            else None
        )
        _memory_block = format_records_for_prompt(
            MemoryHub(
                repo_root=_project_for_memory,
                planner=getattr(state.stack, "planner", None),
            ).retrieve(
                MemoryQuery(
                    text=state.intent.normalized_goal,
                    agent_id=_agent_id_for_memory,
                    project=_project_for_memory,
                    team_id=_team_id_for_memory,
                    limit=8,
                )
            ),
        )
    except Exception:
        _logger.debug("memory hub prompt injection failed", exc_info=True)
        _memory_block = ""
    if _memory_block:
        # Volatile: changes per-turn with the recall query result.
        state.volatile_parts.append(_memory_block)

    if state.camouflage_suffix:
        # Volatile: A/B variant rotates per-turn.
        state.volatile_parts.append(state.camouflage_suffix)


# ── Vague-user-input guidance (P3) ────────────────────────────────
# A bare ``？`` / ``??`` / ``啥`` follow-up after a broken exchange (most
# commonly an image that never arrived) almost always means the user is
# pushing back on the previous reply. Handing that goal straight to the LLM
# produces a generic "请说明您需要我处理的具体内容" template — exactly what
# happened in thread txhjBkLKtmrjdfdJp0FQhN after the image silently failed.
# These helpers detect that shape and let the assembly inject context-aware
# steering so the model addresses the real (recent) issue instead.

# Bare punctuation: ``?`` ``？？`` ``。`` ``。。`` ``...`` — no words at all.
_VAGUE_GOAL_PUNCT_RE = re.compile(r"^[\s\u3000]*[?？!！。，,、~～.]{1,8}[\s\u3000]*$")
# Words that are inherently a "what? / huh?" pushback even without a mark.
_VAGUE_BARE_WORDS = frozenset({"啥", "什么", "咋", "咋了", "干嘛", "what", "huh", "wut"})
# Interjections that only read as pushback when followed by a question mark
# ("嗯？"), otherwise they are acknowledgments ("嗯" = got it) and must NOT
# trigger the steering.
_VAGUE_MARK_REQUIRED = frozenset({"嗯", "额", "呃", "啊", "哦", "em", "uh"})


def _vague_user_goal(goal: str) -> bool:
    """Whether the current user input is a bare, context-less interjection."""
    raw = (goal or "").strip().strip("\u3000")
    if not raw:
        return False
    if _VAGUE_GOAL_PUNCT_RE.match(raw):
        return True
    stripped = raw.casefold()
    m = re.match(r"^([^\s?？!！。，,、~～.]+)\s*[?？!！。，,、~～.]*$", stripped)
    core = m.group(1) if m else stripped
    if core in _VAGUE_BARE_WORDS:
        return True
    if core in _VAGUE_MARK_REQUIRED:
        return bool(re.search(r"[?？!！]", stripped))
    return False


_ATTACHMENT_NOT_RECEIVED_RE = re.compile(
    r"没有收到|未收到|收不到|不支持视觉|视觉输入|无法查看|看不了|看不到|未能送达|"
    r"didn'?t\s+receive|not\s+received|can'?t\s+see|no\s+image",
    re.IGNORECASE,
)
_ATTACHMENT_TERM_RE = re.compile(
    r"图片|截图|图像|附件|image|screenshot|attachment",
    re.IGNORECASE,
)


def _recent_attachment_issue(conv_history: Any) -> bool:
    """Whether a recent turn mentions an image/attachment that was not
    received or could not be seen (the ``？`` right after such a hiccup is
    almost always the user pushing back on it)."""
    if not isinstance(conv_history, list) or not conv_history:
        return False
    for item in conv_history[-8:]:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        texts: list[str] = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("text")
            )
        blob = "\n".join(texts)
        if _ATTACHMENT_TERM_RE.search(blob) and _ATTACHMENT_NOT_RECEIVED_RE.search(blob):
            return True
    return False


_VAGUE_ATTACHMENT_GUIDANCE = (
    "<vague-user-followup>\n"
    '用户这条消息非常简短（如"？"），几乎肯定是在追问或表达对上一轮回复的不满。\n'
    "最近对话里出现过用户图片/附件未能送达或无法查看的迹象。请直接围绕这一点回应：\n"
    "· 先说明当前到底能不能看到用户的图片，以及为什么（如模型不支持视觉输入 / 附件未上传成功）；\n"
    "· 给出可操作的下一步（重新上传、切换到支持图片的模型/会话，或请用户改用文字描述内容）；\n"
    '· 不要使用"请说明您需要我处理的具体内容"这类空泛澄清模板——用户已经表达过诉求。\n'
    "</vague-user-followup>"
)


def _extract_prior_observations(conv_history: Any) -> str:
    """Join tool observations recorded in EARLIER turns of the same thread.

    Each finished ReAct step is persisted back into the conversation history
    as a ``user`` message whose content starts with ``Observation:``. These
    carry the search/fetch/web evidence the model legitimately grounded on in
    previous turns. Returning them as one text blob lets the fact/citation
    grounding guards treat a figure sourced earlier in the same conversation
    as grounded instead of a fabrication.
    """
    if not isinstance(conv_history, list):
        return ""
    blobs: list[str] = []
    for item in conv_history:
        if not isinstance(item, dict):
            continue
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        stripped = content.strip()
        if stripped.startswith("Observation:"):
            blobs.append(stripped)
    return "\n".join(blobs)


def _assemble_messages(state: _AssemblyState) -> None:
    """Compose the initial ``messages`` list from the assembled parts."""
    _volatile_text = "\n\n".join(state.volatile_parts).strip() if state.volatile_parts else ""
    messages: list[Message] = [
        Message(role="system", content="\n\n".join(state.system_parts)),
    ]
    if _volatile_text:
        messages.append(
            Message(
                role="user",
                content=render_volatile_as_user_message(_volatile_text),
            ),
        )
    _uc = state.user_context
    conv_history = _uc.get("conversation_messages")
    state.prior_grounding_text = _extract_prior_observations(conv_history)
    if isinstance(conv_history, list) and conv_history:
        profile_mems = _uc.get("profile_memories")
        if isinstance(profile_mems, list) and profile_mems:
            try:
                from runtime.memory.users.profile import render_profile_memories

                mem_block = render_profile_memories(profile_mems)
            except (ImportError, AttributeError, TypeError):
                mem_block = ""
            if mem_block:
                messages.append(Message(role="system", content=mem_block))
        for item in conv_history[:-1]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in ("user", "assistant", "system"):
                continue
            if (
                isinstance(content, str)
                and content.strip()
                or isinstance(content, list)
                and content
            ):
                messages.append(Message(role=role, content=content))
    _current_goal = str(state.intent.normalized_goal or state.intent.raw or "").strip()
    if state.effective_goal and state.effective_goal != _current_goal:
        messages.append(
            Message(
                role="system",
                content=(
                    "<active-execution-contract>\n"
                    "The earlier execution request is still unfinished. The latest user "
                    "message steers that same task and does not replace its completion "
                    "requirements. Continue the work now; do not merely announce another "
                    "future action.\n"
                    f"Effective goal:\n{state.effective_goal}\n"
                    "</active-execution-contract>"
                ),
            )
        )
    _no_startup_code_context_modes = {
        "chat",
        "conversation",
        "inspiration",
        "brainstorm",
        "discuss",
    }
    _startup_code_context_allowed = (
        state.is_code_mode
        and state.mode_value not in _no_startup_code_context_modes
        and state.capability_mode_value not in _no_startup_code_context_modes
    )
    if (
        _startup_code_context_allowed
        and isinstance(state.effective_wp, str)
        and state.effective_wp.strip()
        and state.resume_task_id is None
    ):
        startup_context = _build_code_context_prelude(
            state.effective_wp.strip(),
            state.effective_goal or str(state.intent.normalized_goal or state.intent.raw or ""),
        )
        if startup_context:
            messages.append(Message(role="user", content=startup_context))
    # dsh ``prepare`` additional-context: the host resolves @session: /
    # canonical session mentions into a read-only referenced-sessions frame
    # (tagged "use as background only") and enqueues it as its own user
    # message right before the actual question.
    _ref_frame = state.user_context.get("session_reference_context")
    if isinstance(_ref_frame, str) and _ref_frame.strip():
        messages.append(Message(role="user", content=_ref_frame.strip()))
    messages.append(
        Message(
            role="user",
            content=_build_user_message_content(
                state.intent.normalized_goal,
                state.user_context.get("attachments", []),
            ),
        ),
    )
    # Bare interjection follow-up (``？``) on top of a recent image/attachment
    # failure: steer the model to address that concrete issue instead of
    # falling back to a generic "please clarify" template.
    if _vague_user_goal(_current_goal) and _recent_attachment_issue(conv_history):
        messages.append(Message(role="system", content=_VAGUE_ATTACHMENT_GUIDANCE))
    if state.user_context.get("live_steering"):
        from runtime.core.cerebrum.live_steering import (
            insert_live_steering_protocol,
        )

        insert_live_steering_protocol(messages)
    state.messages = messages
