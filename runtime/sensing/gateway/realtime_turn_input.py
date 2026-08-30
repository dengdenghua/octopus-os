"""Turn-input shaping for the realtime runtime.

Split out of ``realtime_cerebrum.py``: everything that turns the raw
``turn/start`` params into structured execution inputs — text/attachment
extraction, metadata/mode lookup, resume-intent parsing and confirmation
texts, default planning-mode / topology routing, and the final
``ParsedIntent`` assembly via :func:`_build_intent`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from runtime.platform.models import ParsedIntent, TaskId
from runtime.protocol import TurnParams
from runtime.safety.auth.scope import TenantScope
from runtime.safety.recovery.tenant_scope import (
    AUTHORITATIVE_SCOPE_CONTEXT_KEY,
    authoritative_scope_context,
)

_logger = logging.getLogger(__name__)

_RESUME_CONFIRM_RE = re.compile(
    r"(?:确认|同意|开始|继续)\s*恢复\s*checkpoint\s*#?\s*(\d+)",
    re.IGNORECASE,
)
_CODEX_COMPOSER_MODE_RE = re.compile(
    r"^\s*/(codex|mode)\s+(plan|spec|goal)(?:\s+|$)",
    re.IGNORECASE,
)
_PRODUCTION_DEPLOYMENT_MODES = frozenset({"commercial", "production", "server", "shared"})


def _validated_local_project_workspace(
    context: dict[str, Any],
    *,
    allow_authenticated_selection: bool = False,
) -> str | None:
    """Resolve a desktop-selected project without weakening remote isolation.

    The local realtime client carries its chosen folder in turn context rather
    than in ``TurnParams.cwd``.  Historically ``WorkspaceManager.resolve_cwd``
    ran first and silently replaced that project with the thread scratch root.
    Accept the context path only for a local deployment and only when it is a
    real directory. Anonymous local clients remain bounded by the same
    process-wide roots used by the filesystem API. An authenticated client on
    an explicitly loopback-only desktop server may use the directory it chose
    in the native picker even when that directory is a sibling of the server
    checkout; shared/authenticated deployments never call this resolver.
    """

    deployment = str(os.environ.get("ECHO_DEPLOYMENT_MODE") or "local").strip().lower()
    if deployment in _PRODUCTION_DEPLOYMENT_MODES:
        return None
    if str(context.get("workspace_scope") or "").strip().lower() != "project":
        return None
    raw = context.get("workspace_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not resolved.is_dir():
        return None

    if allow_authenticated_selection:
        return str(resolved)

    from runtime.sensing.gateway._fs_router_paths import _allowed_fs_roots

    for root in _allowed_fs_roots():
        try:
            resolved.relative_to(root.resolve(strict=True))
        except (OSError, RuntimeError, ValueError):
            continue
        return str(resolved)
    return None


def _extract_codex_composer_mode(text: str) -> tuple[str, str | None]:
    """Strip a visible composer marker and return its Codex mode.

    The frontend inserts markers like ``/codex plan`` into the text box so
    users can see what will be applied. Realtime clients should normally turn
    that into metadata before sending, but the backend keeps this parser as a
    safety net for non-React clients and stale bundles.
    """
    match = _CODEX_COMPOSER_MODE_RE.match(text or "")
    if match is None:
        return text, None
    mode = match.group(2).lower()
    return (text or "")[match.end() :].lstrip(), mode


def _resume_task_id_from_intent(intent: ParsedIntent) -> TaskId | None:
    resume_intent = (intent.user_context or {}).get("resume_intent")
    if not isinstance(resume_intent, dict):
        return None
    if resume_intent.get("confirmed") is not True:
        return None
    if (resume_intent.get("checkpoint_type") or "").lower() != "react":
        return None
    raw_task_id = str(resume_intent.get("task_id") or "").strip()
    if not raw_task_id:
        return None
    try:
        return TaskId(UUID(raw_task_id))
    except (TypeError, ValueError):
        _logger.debug("resume intent has non-UUID react task_id: %s", raw_task_id)
        return None


def _should_default_planning_mode(text: str, params: TurnParams) -> bool:
    """Default complex execution turns into plan-mode (write plan first
    before tool work). Plan-mode no longer blocks tool execution as of
    2026-05-31 — it just nudges the prompt; see ``react_loop`` for
    the new semantics. So this can return True freely without
    stranding a turn in "(未执行观察) 本次 ReAct 未启用工具执行".
    """
    if getattr(params, "planning_mode", False):
        return False
    if "planning_mode" in getattr(params, "model_fields_set", set()):
        return False
    if "planningMode" in getattr(params, "model_fields_set", set()):
        return False
    mode = _turn_mode(params)
    # Chat = casual conversation, never auto-plan.
    # React = single-agent tool use; planning mode is overkill for
    # one-shot tool invocations like "测试工具链：请调用 list_cwd".
    # Code mode is execution-first: an explicit project workspace plus an
    # implement/fix request must enter the native tool loop. Users can still
    # request planning explicitly with planningMode=true or `/codex plan`.
    if mode in ("chat", "react", "code"):
        return False
    # Planning-oriented modes always get the plan-first nudge. This mirrors
    # the pre-2026-08 behavior where ``should_require_todo_protocol``
    # defaulted to True for these modes before it was narrowed to explicit
    # contracts; planning-mode defaulting is a non-blocking nudge, so the
    # mode signal stays here rather than moving into the enforcement rule.
    if mode in {"deep", "deep_research", "research"}:
        return True
    metadata = _input_metadata(params)
    context = metadata.get("context")
    user_context = context if isinstance(context, dict) else metadata
    from runtime.core.cerebrum.todo_protocol import should_require_todo_protocol

    # A structured orchestration contract (goal/team/swarm) always warrants a
    # plan-first nudge. ``should_require_todo_protocol`` is deliberately
    # contract-only — natural language never drives its enforcement — but
    # planning-mode defaulting is a separate, non-blocking UX heuristic (see
    # the docstring above), so complex natural-language tasks still get the
    # nudge via ``_looks_complex``.
    if should_require_todo_protocol(text, user_context):
        return True
    return _looks_complex(text)


# Planning-mode defaulting is a prompt nudge, not an enforcement gate (see
# ``_should_default_planning_mode``). todo-protocol enforcement is contract-
# only (``todo_protocol.should_require_todo_protocol``), so this module keeps
# its own light multi-step heuristic instead of delegating — otherwise a
# natural-language implementation/research request would stop defaulting to
# plan-mode. Being generous here is fine: plan-mode no longer blocks tool
# execution, it only nudges the prompt.
_COMPLEX_EXECUTION_RE = re.compile(
    r"("
    r"完整|全面|深度|彻底|全局|多步|复杂|"
    r"实现|修复|重构|改造|调研|研究|审计|排查|开发|设计|搭建|迁移|部署|集成|接入|"
    r"implement|fix|refactor|research|investigate|analy[sz]e|audit|develop|"
    r"design|build|migrate|deploy|integrate"
    r")",
    re.IGNORECASE,
)


def _looks_complex(text: str) -> bool:
    """Light multi-step signal for the plan-mode nudge only."""
    return bool(_COMPLEX_EXECUTION_RE.search(text or ""))


# Keyword → built-in topology id auto-dispatch.
# When the user message strongly suggests a category of work that
# benefits from a multi-agent topology, route to the matching
# built-in (seeded by ``runtime.safety.organization.builtin_topologies``).
# The user can override by explicitly setting ``topology_id`` in the
# turn params; this default only fires when no topology was specified
# AND the message clearly matches one of the categories below.
#
# Order matters — ``code_review`` must come before ``refactor`` because
# "review the refactor PR" mentions both keywords; the more specific
# match wins.
_TOPOLOGY_KEYWORD_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "code_review_team_v1",
        re.compile(
            r"代码评审|代码审查|代码 review|"
            r"\bcode\s*review\b|\bsecurity\s*audit\b|"
            r"安全审查|安全审计|"
            r"PR review|review (?:the |this )?PR",
            re.IGNORECASE,
        ),
    ),
    (
        "debug_team_v1",
        re.compile(
            r"调试|排查|debug\b|找出.*bug|"
            r"\bstack\s*trace\b|\btraceback\b|"
            r"为什么.*报错|为什么.*失败|"
            r"重现.*问题|reproduce.*bug",
            re.IGNORECASE,
        ),
    ),
    (
        "refactor_pair_v1",
        re.compile(
            r"重构|refactor\b|"
            r"重新组织.*代码|重新设计.*结构|"
            r"拆分.*文件|拆分.*模块|"
            r"提取.*公共|抽取.*函数",
            re.IGNORECASE,
        ),
    ),
    (
        "research_swarm_v1",
        re.compile(
            r"调研|研究报告|市场研究|行业报告|竞品分析|竞争分析|"
            r"\bdeep\s*research\b|\bmarket\s*research\b|\bresearch\s*report\b|"
            r"\bcompetitive\s*analysis\b|"
            r"做.*调研|做一份.*报告|写.*研究",
            re.IGNORECASE,
        ),
    ),
)


def _should_default_topology(text: str, params: TurnParams) -> str | None:
    """Pick a built-in topology id for unscoped multi-agent dispatch.

    Auto-dispatch is **disabled by default** as of 2026-05-31. The
    swarm path (``_drive_team_topology`` → ``TeamRunner`` →
    ``ephemeral_runner``) is a separate operating mode from the
    single-agent ReAct loop, with different model-capability needs
    (native ``tools`` support) and different observability semantics.
    Letting a keyword silently flip the user from "single agent" to
    "swarm" caused recurring "the model says it can't call tools"
    reports — the upstream model didn't support native function
    calling, but the swarm path requires it.

    The two modes stay decoupled: users opt into swarm by setting
    ``topology_id`` explicitly (UI selector, API param, or via the
    ``deep-research-swarm`` skill the model can invoke from inside
    the single-agent loop). When that opt-in is absent we always
    return ``None`` and ride the single-agent path.

    Kept the keyword rules around because the operator-tunable opt-in
    flag (``user_ctx["enable_auto_topology"] = True``) re-enables the
    classifier for power users who want it back. We also still honor
    the explicit ``disable_auto_topology`` for operators who built on
    top of the old behaviour and want to lock it off forever.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    # Explicit topology beats everything else — that's how the user
    # opts into swarm now.
    if getattr(params, "topology_id", None):
        return None
    if "topology_id" in getattr(params, "model_fields_set", set()):
        return None
    if _turn_mode(params) == "chat":
        return None
    metadata = _input_metadata(params)
    user_ctx = metadata.get("context") if isinstance(metadata.get("context"), dict) else metadata
    enable_auto = False
    if isinstance(user_ctx, dict):
        if user_ctx.get("disable_auto_topology") is True:
            return None
        if user_ctx.get("enable_auto_topology") is True:
            enable_auto = True
        meta_inner = user_ctx.get("metadata")
        if isinstance(meta_inner, dict):
            if meta_inner.get("disable_auto_topology") is True:
                return None
            if meta_inner.get("enable_auto_topology") is True:
                enable_auto = True
    # Default: do NOT auto-dispatch. Single-agent stays single-agent.
    if not enable_auto:
        return None
    for topology_id, pattern in _TOPOLOGY_KEYWORD_RULES:
        if pattern.search(text):
            return topology_id
    return None


def _join_text(blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def _input_attachments(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        raw = block.get("attachments")
        if not isinstance(raw, list):
            continue
        attachments.extend(item for item in raw if isinstance(item, dict))
    return attachments


def _input_metadata(params: TurnParams) -> dict[str, Any]:
    for block in params.input:
        if not isinstance(block, dict):
            continue
        metadata = block.get("metadata")
        if isinstance(metadata, dict):
            return metadata
    return {}


def _agent_id_from_params(params: TurnParams) -> str | None:
    metadata = _input_metadata(params)
    candidates: list[Any] = [
        metadata.get("agent_id"),
        metadata.get("agent"),
        metadata.get("agent_name"),
    ]
    context = metadata.get("context")
    if isinstance(context, dict):
        candidates.extend(
            [
                context.get("agent_id"),
                context.get("agent"),
                context.get("agent_name"),
            ]
        )
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _preview_text(text: str, *, limit: int = 120) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "..."


def _conversation_messages_from_params(params: TurnParams) -> list[dict[str, object]]:
    metadata = _input_metadata(params)
    candidates: object = None
    context = metadata.get("context")
    if isinstance(context, dict):
        candidates = context.get("conversation_messages") or context.get("messages")
    if not isinstance(candidates, list):
        candidates = metadata.get("conversation_messages") or metadata.get("messages")
    if not isinstance(candidates, list):
        return []
    return [message for message in candidates if isinstance(message, dict)]


def _reflex_response_to_text(response: Any) -> str | None:
    if isinstance(response, str):
        return response.strip() or None
    if isinstance(response, dict):
        for key in ("reply", "text", "message", "response"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _parse_resume_intent(text: str) -> dict[str, Any] | None:
    raw_json = _extract_resume_proposal_json(text)
    if raw_json is None:
        return None
    try:
        raw = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None

    checkpoint_id = _safe_int(raw.get("checkpoint_id"))
    iteration = _safe_int(raw.get("iteration"))
    if checkpoint_id is None or iteration is None:
        return None

    resume_plan = [
        str(step).strip()
        for step in raw.get("resume_plan", [])
        if isinstance(step, str) and step.strip()
    ][:12]
    working_set = [
        str(path).strip()
        for path in raw.get("working_set", [])
        if isinstance(path, str) and path.strip()
    ][:32]
    recent_tool_calls = _sanitize_recent_tool_calls(raw.get("recent_tool_calls"))

    intent = {
        "schema": "echo.resume_intent.v1",
        "requires_confirmation": True,
        "source": "resume_proposal_block",
        "checkpoint_id": checkpoint_id,
        "task_id": _safe_str(raw.get("task_id")),
        "checkpoint_type": _safe_str(raw.get("checkpoint_type")) or "unknown",
        "iteration": iteration,
        "continue_from_iteration": iteration + 1,
        "phase": _safe_str(raw.get("phase")),
        "progress": _safe_str(raw.get("progress")),
        "working_set": working_set,
        "resume_plan": resume_plan,
        "safety": {
            "raw_state_included": bool(raw.get("raw_state_included") is True),
            "raw_message_snapshots_included": bool(
                raw.get("raw_message_snapshots_included") is True,
            ),
        },
    }
    if recent_tool_calls:
        intent["recent_tool_calls"] = recent_tool_calls
    return intent


def _extract_resume_proposal_json(text: str) -> str | None:
    marker = "<echo_resume_proposal>"
    end_marker = "</echo_resume_proposal>"
    source = text or ""
    start = source.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = source.find(end_marker, start)
    if end < 0:
        return None
    body = source[start:end].strip()
    open_at = body.find("{")
    if open_at < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for idx, char in enumerate(body[open_at:], start=open_at):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return body[open_at : idx + 1]
    return None


def _parse_resume_confirmation(text: str) -> int | None:
    match = _RESUME_CONFIRM_RE.search(text or "")
    if match is None:
        return None
    return _safe_int(match.group(1))


def _execution_resume_intent(
    pending: dict[str, Any],
    checkpoint_id: int,
) -> dict[str, Any]:
    intent = {
        "schema": "echo.resume_intent.v1",
        "requires_confirmation": False,
        "confirmed": True,
        "source": pending.get("source") or "resume_proposal_block",
        "checkpoint_id": checkpoint_id,
        "task_id": _safe_str(pending.get("task_id")),
        "checkpoint_type": _safe_str(pending.get("checkpoint_type")) or "unknown",
        "iteration": _safe_int(pending.get("iteration")),
        "continue_from_iteration": _safe_int(
            pending.get("continue_from_iteration"),
        ),
        "phase": _safe_str(pending.get("phase")),
        "working_set": [
            path
            for path in pending.get("working_set", [])
            if isinstance(path, str) and path.strip()
        ][:32],
        "resume_plan": [
            step
            for step in pending.get("resume_plan", [])
            if isinstance(step, str) and step.strip()
        ][:12],
        "recent_tool_calls": _sanitize_recent_tool_calls(pending.get("recent_tool_calls")),
        "safety": {
            "raw_state_included": bool(
                (pending.get("safety") or {}).get("raw_state_included") is True,
            )
            if isinstance(pending.get("safety"), dict)
            else False,
            "raw_message_snapshots_included": bool(
                (pending.get("safety") or {}).get("raw_message_snapshots_included") is True,
            )
            if isinstance(pending.get("safety"), dict)
            else False,
        },
        "confirmation_text": f"确认恢复 checkpoint #{checkpoint_id}",
    }
    if not intent["recent_tool_calls"]:
        intent.pop("recent_tool_calls")
    return intent


def _resume_confirmation_text(resume_intent: dict[str, Any]) -> str:
    checkpoint_id = resume_intent.get("checkpoint_id")
    iteration = resume_intent.get("iteration")
    continue_from = resume_intent.get("continue_from_iteration")
    task_id = resume_intent.get("task_id") or "unknown"
    checkpoint_type = resume_intent.get("checkpoint_type") or "unknown"
    phase = resume_intent.get("phase") or "unknown"
    working_set = [
        path
        for path in resume_intent.get("working_set", [])
        if isinstance(path, str) and path.strip()
    ][:8]
    resume_plan = [
        step
        for step in resume_intent.get("resume_plan", [])
        if isinstance(step, str) and step.strip()
    ][:6]

    lines = [
        f"恢复请求已准备：checkpoint #{checkpoint_id}，需要你明确确认后才会继续执行。",
        "",
        f"- 任务：{task_id}",
        f"- 类型：{checkpoint_type}",
        f"- 迭代：{iteration} -> {continue_from}",
        f"- 阶段：{phase}",
        "- 进展：已读取安全恢复摘要",
    ]
    if working_set:
        lines.append(f"- 工作文件：{', '.join(working_set)}")
    if resume_plan:
        lines.append("")
        lines.append(f"建议恢复计划：{len(resume_plan)} 步")
    recent_tool_calls = _sanitize_recent_tool_calls(resume_intent.get("recent_tool_calls"))
    if recent_tool_calls:
        tools = ", ".join(call["tool"] for call in recent_tool_calls[:4])
        lines.append(f"- 最近工具：{tools}")
    lines.append("")
    lines.append(f"如需继续，请回复：确认恢复 checkpoint #{checkpoint_id}")
    return "\n".join(lines)


def _sanitize_recent_tool_calls(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tool = _safe_str(item.get("tool"))
        if not tool:
            continue
        out.append(
            {
                "iteration": _safe_int(item.get("iteration")) or 0,
                "tool": tool,
                "input_preview": _sanitize_preview_text(item.get("input_preview"), 240),
                "observation_preview": _sanitize_preview_text(
                    item.get("observation_preview"),
                    280,
                ),
            }
        )
        if len(out) >= 8:
            break
    return out


def _sanitize_preview_text(value: Any, limit: int) -> str:
    return _truncate_text(_redact_preview_text(value), limit)


def _redact_preview_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return text
    try:
        from runtime.platform.observability.redactor import redact_text

        return redact_text(text)
    except Exception:  # pragma: no cover - resume sanitation must not block runs
        return text


def _truncate_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _safe_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _context_requests_code_workspace(context: dict[str, Any]) -> bool:
    """Return True when a turn should get a writable coding workspace.

    ``workspace_path`` remains the user's bound project directory. Personal
    threads also get a per-thread cwd; this helper decides when to expose that
    cwd as an effective coding workspace without mislabelling it as a project.
    """
    mode = str(context.get("mode") or "").strip().lower()
    capability = str(context.get("capability_mode") or "").strip().lower()
    code_mode = str(context.get("code_mode") or "").strip().lower()
    scope = str(context.get("workspace_scope") or "").strip().lower()
    if mode in {"chat", "flash", "inspiration", "conversation", "discuss"}:
        return False
    return (
        mode == "code"
        or capability == "code"
        or bool(code_mode)
        or scope == "personal"
        or context.get("personal_workspace_enabled") is True
    )


def _apply_runtime_surface_context(
    text: str,
    context_payload: dict[str, Any],
) -> dict[str, Any]:
    """Promote explicit @Surface tokens into runtime context.

    The frontend may insert markers like ``@Browser`` into the input box,
    mirroring Codex's native browser invocation. Treat those as stronger than a
    stale chat/react mode because the user is explicitly asking for a tool
    surface, not casual conversation.
    """
    try:
        from runtime.core.cerebrum.input_mentions import parse_input_mentions

        surfaces = parse_input_mentions(text).surfaces
    except Exception:  # noqa: BLE001 - mention parsing must not block a turn
        surfaces = ()
    if not surfaces:
        return context_payload

    out = dict(context_payload)
    out.setdefault("runtime_surfaces", list(surfaces))
    out.setdefault("tool_surface", surfaces[0])

    if "chrome" in surfaces:
        current_mode = str(out.get("mode") or "").strip().lower()
        if current_mode in {"", "chat", "react", "flash", "conversation", "discuss"}:
            out["mode"] = "chrome"
        out.setdefault("capability_mode", "browser")
        out.setdefault("mode_preset", "codex.chrome")
        out.setdefault("workflow_preset", "codex.chrome")
        out["browser_operation_mode"] = True
        out["chrome_operation_mode"] = True
        out.setdefault("browser_surface", "chrome")
        out.setdefault("browser_session_policy", "thread_native_external_chrome")
        out.setdefault("browser_track_preference", "extension")
        out.setdefault("browser_permission_policy", "site_policy_required")
        out.setdefault(
            "browser_evidence_policy",
            "state_first_screenshot_only_for_visual_evidence",
        )
        out.setdefault("native_tool_loop", True)
    elif "browser" in surfaces:
        current_mode = str(out.get("mode") or "").strip().lower()
        if current_mode in {"", "chat", "react", "flash", "conversation", "discuss"}:
            out["mode"] = "browser"
        out.setdefault("capability_mode", "browser")
        out.setdefault("mode_preset", "codex.browser")
        out.setdefault("workflow_preset", "codex.browser")
        out["browser_operation_mode"] = True
        out.setdefault("browser_surface", "browser")
        out.setdefault("browser_session_policy", "thread_native")
        out.setdefault("browser_track_preference", "electron")
        out.setdefault(
            "browser_evidence_policy",
            "state_first_screenshot_only_for_visual_evidence",
        )
        out.setdefault("native_tool_loop", True)
    return out


def _turn_mode(params: TurnParams) -> str:
    metadata = _input_metadata(params)
    context = metadata.get("context")
    if isinstance(context, dict) and isinstance(context.get("mode"), str):
        return context["mode"].strip().lower()
    mode = metadata.get("mode")
    if isinstance(mode, str):
        return mode.strip().lower()
    return ""


# Audit / review intent detection. "审计项目 / 审计一下 / 审查 / code review" are
# read-heavy inspection tasks: the agent's job is a report, not silent
# code edits. We do NOT hard-block writes (an audit may legitimately fix a
# found issue after stating it), but we inject a prompt-level audit contract
# so the default behaviour is inspect-and-report, and any edit must be
# explicitly justified in the same turn.
_AUDIT_INTENT_RE = re.compile(
    r"审计|审查|审计项目|audit\b|security\s*audit\b|code\s*review\b|"
    r"代码评审|代码审查|检查项目|盘点|体检|review\s+the\s+project",
    re.IGNORECASE,
)


def _is_audit_intent(text: str, context_payload: dict[str, Any]) -> bool:
    if _AUDIT_INTENT_RE.search(text):
        return True
    # A declared read-only / audit capability also opts in.
    mode = str(context_payload.get("mode") or "").strip().lower()
    capability = str(context_payload.get("capability_mode") or "").strip().lower()
    return mode == "audit" or capability == "audit" or bool(context_payload.get("audit_mode"))


def _build_intent(
    text: str,
    params: TurnParams,
    *,
    workspaces: Any = None,
    thread_store: Any = None,
    allow_client_auto_approve: bool = False,
    allow_local_workspace_access: bool = False,
    conversation_messages: list[dict[str, str]] | None = None,
) -> ParsedIntent:
    owner_actor_id = str(getattr(params, "owner_actor_id", None) or "").strip()
    tenant_id = str(getattr(params, "tenant_id", None) or "").strip()
    authenticated_principal = bool(owner_actor_id or tenant_id)
    if authenticated_principal and (not owner_actor_id or not tenant_id):
        raise RuntimeError("authenticated realtime principal is incomplete")
    authenticated_workspace = authenticated_principal and not allow_local_workspace_access

    managed_layout: Any = None
    cwd: str | None
    if authenticated_workspace:
        if workspaces is None or thread_store is None:
            raise RuntimeError("authenticated realtime workspace service unavailable")
        from runtime.sensing.gateway.thread_workspace import (
            ensure_managed_thread_workspace,
        )

        managed_workspace = ensure_managed_thread_workspace(
            getattr(workspaces, "root", None),
            thread_id=params.thread_id,
            actor_id=owner_actor_id,
            tenant_id=tenant_id,
            store=thread_store,
        )
        managed_layout = workspaces.bind_managed(params.thread_id, managed_workspace)
        cwd = str(managed_layout.root)
    else:
        cwd = params.cwd
        if workspaces is not None:
            cwd = workspaces.resolve_cwd(params.thread_id, params.cwd)
    text, marker_mode = _extract_codex_composer_mode(text)
    metadata = _input_metadata(params)
    context = metadata.get("context")
    context_payload = context if isinstance(context, dict) else {}
    local_project_workspace = (
        None
        if authenticated_workspace
        else _validated_local_project_workspace(
            context_payload,
            allow_authenticated_selection=(
                authenticated_principal and allow_local_workspace_access
            ),
        )
    )
    if local_project_workspace is not None:
        cwd = local_project_workspace
    if thread_store is not None:
        from runtime.sensing.gateway.turn_session import build_turn_metadata

        context_payload = build_turn_metadata(
            thread_id=params.thread_id,
            body={"context": context_payload},
            store=thread_store,
            authoritative_workspace=(managed_layout.root if managed_layout is not None else None),
            owner_actor_id=owner_actor_id or None,
            tenant_id=tenant_id or None,
        )
    context_payload = dict(context_payload)
    # This private marker is consumed by memory/context readers.  It must
    # never survive from client metadata; authenticated TurnParams are the
    # server-overwritten authority and are re-injected below.
    context_payload.pop(AUTHORITATIVE_SCOPE_CONTEXT_KEY, None)
    if authenticated_principal:
        context_payload[AUTHORITATIVE_SCOPE_CONTEXT_KEY] = authoritative_scope_context(
            TenantScope(tenant_id=tenant_id, actor_id=owner_actor_id)
        )
    attachments = _input_attachments(params.input)
    # Attachment paths arrive from the client and therefore are not authority.
    # Grant read access only to the upload directory derived server-side from
    # this thread's WorkspaceManager.  The execution scope consumes this as a
    # read-only root; it is never added to writable roots.
    if attachments and workspaces is not None:
        try:
            upload_root = workspaces.layout(params.thread_id).upload.resolve()
        except (OSError, ValueError):
            upload_root = None
        if upload_root is not None:
            context_payload["attachment_read_roots"] = [str(upload_root)]
    # ``TurnParams.cwd`` is the public single-shot / power-user working
    # directory contract.  Merely carrying it as ``user_context.cwd`` leaves
    # the execution scope in chat mode, where filesystem tools default to the
    # thread's empty artifact folder.  Promote an explicit caller-supplied cwd
    # to the same project context the interactive work-directory selector
    # emits.  Auto-allocated per-thread cwd values still follow the personal
    # workspace path below and do not gain project scope implicitly.
    explicit_cwd = not authenticated_workspace and (
        (isinstance(params.cwd, str) and bool(params.cwd.strip()))
        or local_project_workspace is not None
    )
    if explicit_cwd and isinstance(cwd, str) and cwd.strip():
        context_payload.setdefault("workspace_path", cwd.strip())
        context_payload.setdefault("workspace_scope", "project")
        context_payload.setdefault("mode", "code")
    if marker_mode:
        context_payload.setdefault("workflow_mode", marker_mode)
        context_payload.setdefault("completion_policy", marker_mode)
        context_payload.setdefault("mode_preset", f"{marker_mode}.mode")
        context_payload.setdefault("workflow_preset", f"{marker_mode}.mode")
        if marker_mode == "goal":
            context_payload.setdefault("goal_mode", True)
    context_payload = _apply_runtime_surface_context(text, context_payload)
    actor_id = owner_actor_id or metadata.get("actor_id") or metadata.get("actorId")
    if isinstance(actor_id, str) and actor_id.strip():
        if authenticated_principal:
            context_payload["owner_actor_id"] = actor_id.strip()
            context_payload["tenant_id"] = tenant_id
        else:
            context_payload.setdefault("owner_actor_id", actor_id.strip())
    if managed_layout is not None:
        # Reassert the execution boundary after all client-controlled context
        # shaping. These paths are consumed independently by cwd resolution,
        # filesystem scope, attachments and artifact publishing.
        for key in (
            "extra_workspaces",
            "personal_workspace_path",
            "allowed_write_paths",
        ):
            context_payload.pop(key, None)
        context_payload["workspace_path"] = str(managed_layout.root)
        context_payload["workspace_scope"] = "project"
        context_payload["_artifact_output_root"] = str(managed_layout.final)
    if conversation_messages and not isinstance(
        context_payload.get("conversation_messages"),
        list,
    ):
        context_payload["conversation_messages"] = conversation_messages
    if _context_requests_code_workspace(context_payload):
        if (
            isinstance(context_payload.get("workspace_path"), str)
            and context_payload["workspace_path"].strip()
        ):
            context_payload.setdefault("workspace_scope", "project")
        else:
            context_payload.setdefault("workspace_scope", "personal")
        if (
            context_payload.get("workspace_scope") == "personal"
            and isinstance(cwd, str)
            and cwd.strip()
        ):
            context_payload.setdefault("personal_workspace_path", cwd.strip())
    resume_intent = _parse_resume_intent(text)
    if resume_intent is not None:
        context_payload["resume_intent"] = resume_intent
    if "effort" in getattr(params, "model_fields_set", set()):
        context_payload["reasoning_effort"] = params.effort
    # Defense in depth: ``RealtimeGateway._sanitize_turn_params`` already
    # rewrites ``approvalPolicy="never"`` to ``"on-request"`` when the
    # operator hasn't opted in. We re-check here so tests that drive
    # CerebrumRuntime directly (bypassing the gateway) cannot silently
    # disable approval gates either.
    approval_policy = params.approval_policy
    if approval_policy == "never" and not allow_client_auto_approve:
        approval_policy = "on-request"
    # Audit / review turns get a prompt-level audit contract (inspect →
    # report; any code edit must be explicitly justified). This is a
    # behavioural nudge, not a permission gate — an audit may legitimately
    # fix a found issue after stating it.
    audit_mode = _is_audit_intent(text, context_payload)
    if audit_mode:
        context_payload = {**context_payload, "audit_mode": True}
    # Thread the turn's declared sandbox policy through to execution so
    # exec_shell can honour ``sandboxPolicy.networkAccess``. Default when
    # absent is network denied — a turn must explicitly opt in.
    sb_policy = getattr(params, "sandbox_policy", None) or {}
    if isinstance(sb_policy, dict) and sb_policy:
        context_payload = {
            **context_payload,
            "sandbox_policy": {"type": str(sb_policy.get("type") or ""), **sb_policy},
        }
    return ParsedIntent(
        raw=text,
        intent_type="task",
        normalized_goal=text,
        user_context={
            **context_payload,
            "approval_policy": approval_policy,
            "auto_approve": approval_policy == "never",
            "cwd": cwd,
            "mode": context_payload.get("mode") or _turn_mode(params),
            "planning_mode": bool(getattr(params, "planning_mode", False)),
            # Native tool models often emit protocol calls with no surrounding
            # prose. The realtime bridge may request one evidence-grounded
            # public update after a genuinely quiet, long-running batch.
            "realtime_public_narrative": True,
            # Ask the working model itself for one natural public sentence
            # before its first real tool round.
            "realtime_public_orientation": True,
            # Reasoning providers can spend minutes producing only private
            # tokens before they reach ordinary text or a tool call. Ask the
            # lightweight, thinking-disabled narrator for the first public
            # sentence independently so the main conversation never waits on
            # the working model's hidden chain of thought.
            "realtime_public_preface": True,
            # Pass attachments through so images become image_url blocks and
            # documents become a path/metadata manifest with bounded previews.
            "attachments": attachments,
        },
    )
