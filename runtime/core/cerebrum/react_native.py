"""Native tool-use path for the single-agent ReAct loop.

The default ReAct loop drives the model through a *text* protocol
(``Thought:`` / ``Action: name({...})`` / ``Observation:``) and recovers
the action with ~56 regexes in ``react_parsing.py``. That text layer is a
robustness floor for weak / local models (ollama, etc.) that cannot do
native function calling.

For models that *do* advertise ``supports_tool_use`` we can skip the text
round-trip entirely: pass the registered skills as native ``tools`` and
read the model's ``tool_calls`` straight off the response. This removes the
single biggest brittleness source — parsing the action out of free text.

The integration is deliberately surgical. Rather than rewrite the
2900-line dispatch section (an inline generator with approval / retry /
cancel / background-task / checkpoint behaviour), we *synthesise* the
loop's existing ``ReActStep.action`` / ``.actions`` strings from the native
``tool_calls`` and let the proven dispatch run unchanged. ``json.dumps`` of
the tool input round-trips losslessly through the dispatch's
``_parse_action`` (``json.loads``), so there is no regex involved on the
native path.

Gating (both must hold):

* ``ECHO_NATIVE_TOOLUSE`` env flag is truthy. Default OFF — the native
  path stays opt-in until validated against a live tool-use API, so the
  out-of-the-box behaviour (and the whole test suite, which uses mock
  routers) is byte-identical to before.
* The router resolves a provider whose ``capabilities.supports_tool_use``
  is True for the effective model. Mock / weak routers report False and
  transparently keep the text protocol.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from runtime.core.cerebrum.react_types import ReActStep

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
STRICT_EXPLICIT_READ_TOOL_NAMES = frozenset(
    {
        "grep_text",
        "read_file",
        "read_file_range",
    }
)
_PUBLIC_UPDATE_FROM_THINKING_RE = re.compile(
    r"(?:^|\n)\s*(?:Update|Progress)\s*:\s*(.+?)"
    r"(?=\n\s*(?:Action|Observation|Thought|Final)\s*:|\n\n|$)",
    re.IGNORECASE | re.DOTALL,
)


def _explicit_public_update_from_thinking(value: str) -> str:
    """Recover only the model's explicitly labelled public checkpoint.

    Some OpenAI-compatible reasoning providers place all pre-tool prose in
    ``reasoning_content`` even when the prompt gives ``Update:`` a dedicated
    public contract. Never surface the surrounding chain of thought; only the
    delimited Update/Progress field is eligible for the conversation lane.
    """

    match = _PUBLIC_UPDATE_FROM_THINKING_RE.search(value or "")
    return match.group(1).strip() if match else ""


def native_tool_use_flag_enabled() -> bool:
    """Read ``ECHO_NATIVE_TOOLUSE`` fresh each call (operator can flip
    without a restart).

    Default ON: validated 2026-06 against a live function-calling API
    (structured tool_calls dispatched + correct answer end-to-end). The
    capability gate still applies, so only models advertising
    ``supports_tool_use`` actually take the native path; everything else
    transparently keeps the text protocol. Set ``ECHO_NATIVE_TOOLUSE=0``
    (or false/no/off) to force the text protocol even on capable models.
    """
    raw = os.environ.get("ECHO_NATIVE_TOOLUSE", "").strip().lower()
    return raw not in _FALSY


def model_supports_tool_use(router: Any, model: str) -> bool:
    """Whether ``router`` can serve ``model`` via native tool-use.

    Handles both router topologies defensively:

    * a direct provider router exposes ``capabilities.supports_tool_use``;
    * a ``ModelDispatchRouter`` resolves a per-model sub-router — we peek
      at the resolved sub-router's capabilities.

    Any uncertainty (missing attribute, resolve failure) returns False so
    the loop falls back to the text protocol — never the other way round.
    """
    if _caps_support(getattr(router, "capabilities", None)):
        return True
    resolve = getattr(router, "_resolve", None)
    if callable(resolve):
        try:
            sub = resolve(model)
        except Exception:  # noqa: BLE001 — resolution must never break gating
            return False
        if sub is not None and sub is not router:
            return _caps_support(getattr(sub, "capabilities", None))
    return False


def _caps_support(caps: Any) -> bool:
    return bool(caps is not None and getattr(caps, "supports_tool_use", False))


def native_tool_use_active(router: Any, model: str) -> bool:
    """Combined gate: flag on AND the resolved model advertises tool-use."""
    return native_tool_use_flag_enabled() and model_supports_tool_use(router, model)


def build_loop_tool_specs(
    executor: Any,
    *,
    agent: Any = None,
    goal: str = "",
    user_context: dict[str, Any] | None = None,
    strict_explicit_reads: bool = False,
) -> list[Any]:
    """Build the native ``ToolSpec`` catalog from the loop's skill registry.

    Returns ``[]`` on any failure (missing registry, builder error) so a
    spec-build problem silently downgrades to the text protocol rather than
    aborting the turn.
    """
    registry = getattr(executor, "registry", None)
    if registry is None:
        return []
    try:
        from runtime.execution.tool_spec_builder import build_anthropic_tool_specs

        specs = list(
            build_anthropic_tool_specs(
                registry,
                agent=agent,
                goal=goal,
                user_context=user_context,
            )
            or []
        )
        from runtime.execution.misc.skill_policy import (
            filter_audit_read_only_tool_specs,
        )

        specs = filter_audit_read_only_tool_specs(
            specs,
            context=user_context,
        )
        if strict_explicit_reads:
            specs = [
                spec
                for spec in specs
                if getattr(spec, "name", "") in STRICT_EXPLICIT_READ_TOOL_NAMES
            ]
        return specs
    except Exception:  # noqa: BLE001 — spec build is best-effort; fall back to text
        return []


def require_public_update_on_tool_specs(
    specs: list[Any],
    *,
    evidence_round: bool = False,
) -> list[Any]:
    """Require one model-authored public sentence on every native tool round.

    Some function-calling providers emit a tool call without any ordinary text,
    even when explicitly prompted to keep the user informed. Adding a transient
    schema field gives that same model a structured place to author each public
    beat. The field is removed before dispatch, so tool handlers never see it.
    """

    augmented: list[Any] = []
    for spec in specs:
        schema = dict(getattr(spec, "input_schema", None) or {})
        properties = dict(schema.get("properties") or {})
        required = list(schema.get("required") or [])
        if evidence_round:
            properties["confirmed_fact"] = {
                "type": "string",
                "minLength": 8,
                "maxLength": 280,
                "description": (
                    "One concrete user-facing fact established by the immediately "
                    "preceding tool results. It must name the actual finding, not merely "
                    "say that files were read or work continues. Use the user's language; "
                    "do not expose hidden reasoning or use a heading/stage label."
                ),
            }
            properties["next_action"] = {
                "type": "string",
                "minLength": 4,
                "maxLength": 220,
                "description": (
                    "What this tool call will now establish, in one natural user-facing "
                    "clause. Do not mention internal protocol names or claim completion."
                ),
            }
            for field in ("confirmed_fact", "next_action"):
                if field not in required:
                    required.append(field)
        else:
            properties["public_update"] = {
                "type": "string",
                "maxLength": 420,
                "description": (
                    "One short user-facing sentence in the user's language naming the "
                    "concrete scope being inspected or changed and what that will "
                    "establish. Do not include hidden reasoning, stage labels, tool or "
                    "protocol names, generic status filler, repeated wording, or an "
                    "unsupported completion claim."
                ),
            }
            if "public_update" not in required:
                required.append("public_update")
        schema["type"] = "object"
        schema["properties"] = properties
        schema["required"] = required
        try:
            augmented.append(spec.model_copy(update={"input_schema": schema}))
        except AttributeError:
            augmented.append(spec)
    return augmented


def step_from_tool_calls(
    tool_calls: list[Any],
    *,
    text: str = "",
    thinking: str = "",
    iteration: int = 0,
    evidence_round: bool = False,
) -> ReActStep:
    """Synthesise a ``ReActStep`` from native ``tool_calls``.

    Each call becomes a ``name({json})`` action string — exactly the shape
    the dispatch's ``_parse_action`` already parses — so the existing
    single/parallel dispatch path runs unchanged. ``json.dumps`` →
    ``json.loads`` round-trips the args with no regex and no lossy
    formatting. The model's prose (``text``) is explicitly public and becomes
    a progress checkpoint; ``thinking`` remains the private extended-thinking
    trace.
    """
    actions: list[str] = []
    structured_public_update = ""
    structured_confirmed_fact = ""
    structured_next_action = ""
    for call in tool_calls:
        name = str(getattr(call, "name", "") or "").strip()
        if not name:
            continue
        raw_input = getattr(call, "input", None)
        args = dict(raw_input) if isinstance(raw_input, dict) else {}
        candidate_update = args.pop("public_update", "")
        candidate_fact = args.pop("confirmed_fact", "")
        candidate_next_action = args.pop("next_action", "")
        if not structured_public_update and isinstance(candidate_update, str):
            structured_public_update = candidate_update.strip()
        if not structured_confirmed_fact and isinstance(candidate_fact, str):
            structured_confirmed_fact = candidate_fact.strip()
        if not structured_next_action and isinstance(candidate_next_action, str):
            structured_next_action = candidate_next_action.strip()
        try:
            arg_json = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            arg_json = "{}"
        actions.append(f"{name}({arg_json})")

    thought = (thinking or "").strip()
    tagged_public_update = _explicit_public_update_from_thinking(thought)
    structured_evidence_update = ""
    if structured_confirmed_fact and structured_next_action:
        fact = structured_confirmed_fact.rstrip("。.!！?？；; ")
        next_action = structured_next_action.lstrip("；;，, ")
        structured_evidence_update = f"{fact}；{next_action}"
    return ReActStep(
        iteration=iteration,
        thought=thought,
        public_update=(
            structured_evidence_update
            or ("" if evidence_round else (structured_public_update or tagged_public_update))
        ),
        action="; ".join(actions),
        observation="",
        raw_llm_output=text or "",
        actions=actions,
        action_results=[],
    )


def trim_text_protocol_for_native(system_prompt: str) -> str:
    """Phase 1: drop the redundant text-protocol scaffolding for native mode.

    When tools are passed natively the model emits ``tool_use`` blocks and
    ignores the ``Action: name({...})`` *text* instructions (Anthropic
    prioritises ``tools`` over a competing text protocol). Those lines are
    then pure token overhead. We strip the worked ``Thought/Action/
    Observation`` example block and append a one-line native directive,
    leaving the rest of the prompt (role, policies, skills guidance) intact.

    Conservative: if the expected anchors are absent (prompt changed) the
    original string is returned unmodified.
    """
    marker = "Thought:"
    end_marker = "Observation:"
    start = system_prompt.find(marker)
    if start == -1:
        return system_prompt
    end = system_prompt.find(end_marker, start)
    if end == -1:
        return system_prompt
    # Cut from the first worked Thought/Action example through the line that
    # closes the Observation placeholder.
    line_end = system_prompt.find("\n", end)
    if line_end == -1:
        line_end = len(system_prompt)
    native_note = (
        "你已获得原生工具调用能力(tools)。直接调用所需工具即可,"
        "无需输出 Thought/Action/Observation 文本协议。第一批工具前先用普通文本给用户"
        "一句具体的范围说明，不写阶段标题、工具名或完成式结论；"
        "收到工具结果后若还要继续调用工具，先用普通文本给用户 1-3 句进度，"
        "概括刚确认的事实以及它如何影响下一步，不要暴露私有思考或工具参数；"
        "完成后给出最终答案。"
    )
    return system_prompt[:start] + native_note + system_prompt[line_end:]
