"""Tool-call / XML action parsing helpers for the ReAct trajectory.

Extracted from ``react_parsing.py``. This module owns the loose-output
tool-call recovery (``_extract_tool_actions_from_loose_output``), the
XML argument coercion helpers (``_xml_args_from_body`` /
``_coerce_xml_arg_value``), the canonical action formatter
(``_format_action``), the format-violation / placeholder-observation
predicates, and the action-name + ``_parse_action`` parsing primitives.

Depends only on ``react_types``; leaf module imported by the other
``_react_parsing_*`` submodules.
"""

from __future__ import annotations

import json
import re
from typing import Any

from runtime.core.cerebrum.react_types import ReActStep

_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*<function\s*=\s*(?P<name>[A-Za-z_][A-Za-z0-9_./:-]*)\s*>"
    r"(?P<body>.*?)</function>\s*</tool_call>",
    re.IGNORECASE | re.DOTALL,
)
_STANDALONE_NAMED_TOOL_CALL_RE = re.compile(
    r"<tool_call\s+name\s*=\s*[\"']?(?P<name>[A-Za-z_][A-Za-z0-9_./:-]*)[\"']?\s*>"
    r"\s*(?P<args>\{.*?\})\s*</tool_call>",
    re.IGNORECASE | re.DOTALL,
)
_FUNCTION_TYPE_CONTAINER_RE = re.compile(
    r"<tool_calls>\s*"
    r"<function_type>\s*(?P<name>[A-Za-z_][A-Za-z0-9_./:-]*)\s*</function_type>\s*"
    r"<function_params>\s*(?P<args>.*?)\s*</function_params>\s*"
    r"</tool_calls>",
    re.IGNORECASE | re.DOTALL,
)
_NAMED_TOOL_CONTAINER_RE = re.compile(
    r"<tool_calls>\s*"
    r"<tool_call\s+name\s*=\s*[\"']?(?P<name>[A-Za-z_][A-Za-z0-9_./:-]*)[\"']?\s*>"
    r"(?P<body>.*?)</tool_calls>",
    re.IGNORECASE | re.DOTALL,
)
_NAMED_TOOL_ARG_RE = re.compile(
    r"<tool_call\s+name\s*=\s*[\"']?(?P<key>[A-Za-z_][A-Za-z0-9_:-]*)[\"']?\s*>"
    r"(?P<value>.*?)</tool_call>",
    re.IGNORECASE | re.DOTALL,
)
_DIRECT_NAMED_TOOL_CONTAINER_RE = re.compile(
    r"<tool_calls>\s*"
    r"<(?P<name>[A-Za-z_][A-Za-z0-9_./:-]*)>\s*"
    r"(?P<body>.*?)\s*"
    r"</(?P=name)>\s*"
    r"</tool_calls>",
    re.IGNORECASE | re.DOTALL,
)
_MAIN_NAMED_TOOL_CONTAINER_RE = re.compile(
    r"<main>\s*"
    r"<(?P<name>[A-Za-z_][A-Za-z0-9_./:-]*)>\s*"
    r"(?P<args>.*?)\s*"
    r"</(?P=name)>\s*"
    r"</main>",
    re.IGNORECASE | re.DOTALL,
)
_BARE_NAMED_TOOL_TAG_RE = re.compile(
    r"<(?P<name>[a-z][a-z0-9]*(?:_[a-z0-9]+)+)>\s*"
    r"(?P<args>\{.*?\})\s*"
    r"</(?P=name)>",
    re.DOTALL,
)
_BARE_TODO_ARRAY_TAG_RE = re.compile(
    r"<todo_write>\s*(?P<args>\[.*?\])\s*</todo_write>",
    re.IGNORECASE | re.DOTALL,
)
_XML_ARG_RE = re.compile(
    r"<(?P<key>[A-Za-z_][A-Za-z0-9_:-]*)>(?P<value>.*?)</(?P=key)>",
    re.IGNORECASE | re.DOTALL,
)
_PARAM_ARG_RE = re.compile(
    r"<parameter\s*=\s*[\"']?(?P<key>[A-Za-z_][A-Za-z0-9_:-]*)[\"']?\s*>"
    r"(?P<value>.*?)</parameter>",
    re.IGNORECASE | re.DOTALL,
)
_NAMED_PARAM_ARG_RE = re.compile(
    r"<parameter\s+name\s*=\s*[\"'](?P<key>[A-Za-z_][A-Za-z0-9_:-]*)[\"']\s*>"
    r"(?P<value>.*?)</parameter>",
    re.IGNORECASE | re.DOTALL,
)
_INVOKE_TOOL_CALL_RE = re.compile(
    r"<invoke\s+name\s*=\s*[\"'](?P<name>[A-Za-z_][A-Za-z0-9_./:-]*)[\"']\s*>"
    r"(?P<body>.*?)</invoke>",
    re.IGNORECASE | re.DOTALL,
)
_FENCED_JSON_RE = re.compile(
    r"```(?:json)?\s*(?P<body>\{.*?\})\s*```",
    re.IGNORECASE | re.DOTALL,
)
# Some providers put a complete JSON function call directly in the assistant
# text lane (for example ``web_search({"query": "..."})``).  Keep this
# deliberately anchored to the entire response; snippets mentioned in prose
# must never become executable actions.
_BARE_INLINE_TOOL_CALL_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_./:-]*)\s*\(\s*(?P<args>\{.*\})\s*\)\s*$",
    re.DOTALL,
)
_ACTION_XML_CONTAINER_RE = re.compile(
    r"<Action>\s*(?P<body>.*?)\s*</Action>",
    re.IGNORECASE | re.DOTALL,
)
# Ark/Seed-compatible providers occasionally serialize a function call as
# ``<seed:tool_call><function name="list_cwd"></function></seed:tool_call>``
# in the assistant text lane.  It is still an explicit, closed execution
# envelope; treating it as prose makes the loop terminate with guard_impasse
# before the tool ever runs.  Keep the boundary strict and accept only a
# function name plus an optional JSON object body.
_SEEDED_TOOL_CALL_RE = re.compile(
    r"<seed:tool_call\s*>\s*<function\s+name\s*=\s*[\"']?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_./:-]*)[\"']?\s*>"
    r"(?P<body>.*?)</function>\s*</seed:tool_call\s*>",
    re.IGNORECASE | re.DOTALL,
)
_SPECIAL_TOOL_ENVELOPE_MARKERS = (
    "<|tool_calls_section_begin|>",
    "<|tool_calls_begin|>",
    "<|tool_calls_end|>",
    "<|tool_calls_section_end|>",
    "<tool_calls",
    "<invoke name=",
    "<seed:tool_call",
)

_ACTION_CALL_RE = re.compile(
    r"""
    ^\s*
    (?P<name>[A-Za-z_][A-Za-z0-9_./:-]*)   # skill 名(容许 /、:、.、-)
    \s*
    [\(\[]                                 # ( 或 [
    (?P<args>.*)                           # 参数体
    [\)\]]                                 # ) 或 ]
    \s*$
    """,
    re.VERBOSE | re.DOTALL,
)

_ACTION_NAME_ALIASES = {
    "deep-research_swarm": "deep-research-swarm",
    "deep_research_swarm": "deep-research-swarm",
    "deep_research-swarm": "deep-research-swarm",
    "deep_research": "deep-research",
    "write_file": "write_text_file",
}


def _coerce_xml_arg_value(value: str) -> Any:
    stripped = value.strip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
    return stripped


def _xml_args_from_body(body: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for m in _XML_ARG_RE.finditer(body or ""):
        args[m.group("key")] = _coerce_xml_arg_value(m.group("value"))
    for m in _PARAM_ARG_RE.finditer(body or ""):
        args[m.group("key")] = _coerce_xml_arg_value(m.group("value"))
    for m in _NAMED_PARAM_ARG_RE.finditer(body or ""):
        args[m.group("key")] = _coerce_xml_arg_value(m.group("value"))

    kwargs = args.get("kwargs")
    if isinstance(kwargs, dict):
        return kwargs
    if isinstance(kwargs, str):
        try:
            parsed_kwargs = json.loads(kwargs)
        except json.JSONDecodeError:
            parsed_kwargs = None
        if isinstance(parsed_kwargs, dict):
            return parsed_kwargs
    return args


def _extract_tool_actions_from_loose_output(text: str) -> list[str]:
    actions: list[str] = []
    # Seed/Ark text-wire calls are checked before generic envelopes so their
    # namespace is never accidentally rendered as ordinary answer prose.
    for xml in _SEEDED_TOOL_CALL_RE.finditer(text):
        raw_body = (xml.group("body") or "").strip()
        args: dict[str, Any] = {}
        if raw_body:
            try:
                parsed = json.loads(raw_body)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                args = parsed
        name = _normalize_action_name(xml.group("name").strip())
        actions.append(_format_action(name, args))
    if actions:
        return actions

    # DeepSeek-compatible endpoints may expose a complete tool call through a
    # ``<main><tool_name>{json}</tool_name></main>`` envelope.  ``todo_write``
    # commonly carries a top-level JSON array while ordinary tools carry an
    # object.  The explicit outer boundary plus a successful JSON decode keeps
    # this conservative: normal HTML ``<main>`` content is not executable.
    for xml in _MAIN_NAMED_TOOL_CONTAINER_RE.finditer(text):
        try:
            payload = json.loads(xml.group("args"))
        except json.JSONDecodeError:
            continue
        name = _normalize_action_name(xml.group("name").strip())
        if isinstance(payload, list) and name == "todo_write":
            args = {"items": payload}
        elif isinstance(payload, dict):
            args = payload
            if name == "todo_write" and "todos" in args and "items" not in args:
                args["items"] = args.pop("todos")
        else:
            continue
        actions.append(_format_action(name, args))
    if actions:
        return actions

    # Some OpenAI-compatible providers expose their internal function wire
    # format as assistant text:
    # ``<tool_calls><invoke name="fn"><parameter name="arg">...``.
    # A complete, closed invoke is an explicit execution boundary; recover
    # every call in the container rather than treating it as zero-anchor prose.
    for xml in _INVOKE_TOOL_CALL_RE.finditer(text):
        name = _normalize_action_name(xml.group("name").strip())
        args = _xml_args_from_body(xml.group("body") or "")
        if name == "todo_write" and "todos" in args and "items" not in args:
            args["items"] = args.pop("todos")
        actions.append(_format_action(name, args))
    if actions:
        return actions

    for xml in _TOOL_CALL_RE.finditer(text):
        name = _normalize_action_name(xml.group("name").strip())
        args = _xml_args_from_body(xml.group("body") or "")
        actions.append(_format_action(name, args))
    if actions:
        return actions

    # Some reasoning providers emit a complete, standalone named call with
    # a JSON body, without the plural ``<tool_calls>`` wrapper.  Require both
    # an explicit tool name and a closed JSON object so XML examples or
    # incomplete streamed fragments in prose are never executed.
    for xml in _STANDALONE_NAMED_TOOL_CALL_RE.finditer(text):
        try:
            args = json.loads(xml.group("args"))
        except json.JSONDecodeError:
            continue
        if not isinstance(args, dict):
            continue
        name = _normalize_action_name(xml.group("name").strip())
        if name == "todo_write" and "todos" in args and "items" not in args:
            args["items"] = args.pop("todos")
        actions.append(_format_action(name, args))
    if actions:
        return actions

    # A few OpenAI-compatible reasoning providers emit one explicit XML
    # container per call, with a JSON object in ``function_params``.  Keep
    # recovery scoped to the complete container so XML examples in prose do
    # not become executable actions.
    for xml in _FUNCTION_TYPE_CONTAINER_RE.finditer(text):
        try:
            args = json.loads(xml.group("args"))
        except json.JSONDecodeError:
            continue
        if not isinstance(args, dict):
            continue
        name = _normalize_action_name(xml.group("name").strip())
        if name == "todo_write" and "todos" in args and "items" not in args:
            args["items"] = args.pop("todos")
        actions.append(_format_action(name, args))
    if actions:
        return actions

    # Some OpenAI-compatible reasoning models serialize function calls as
    # ``<tool_calls><tool_call name="fn"><tool_call name="arg">...``
    # instead of returning protocol-level tool_calls.  Recover only inside
    # the explicit container so ordinary XML/code examples are not executed.
    for xml in _NAMED_TOOL_CONTAINER_RE.finditer(text):
        name = _normalize_action_name(xml.group("name").strip())
        args = {
            arg.group("key"): _coerce_xml_arg_value(arg.group("value"))
            for arg in _NAMED_TOOL_ARG_RE.finditer(xml.group("body") or "")
        }
        if name == "todo_write" and "todos" in args and "items" not in args:
            args["items"] = args.pop("todos")
        actions.append(_format_action(name, args))
    if actions:
        return actions

    # Kimi-style reasoning occasionally uses the tool name itself as the XML
    # element: ``<tool_calls><glob_files><pattern>…``.  The outer marker is an
    # explicit execution boundary, so recover the named child and its args.
    for xml in _DIRECT_NAMED_TOOL_CONTAINER_RE.finditer(text):
        name = _normalize_action_name(xml.group("name").strip())
        args = _xml_args_from_body(xml.group("body") or "")
        actions.append(_format_action(name, args))
    if actions:
        return actions

    # DeepSeek-style bare tool tags: ``<write_text_file>\n{json}\n
    # </write_text_file>`` with no wrapper at all.  There is no container
    # marker to anchor on, so the gates are strict instead: the tag must be
    # lowercase snake_case (real tool names always carry an underscore —
    # prose XML like ``<summary>`` or ``<Action>`` never matches), the tag
    # must close with the same name, and the body must be one closed JSON
    # object.  This path only runs after every anchored format above found
    # nothing, so ordinary responses never reach it.
    # The checklist tool is the one deliberate array-valued exception.  Keep
    # it in a dedicated, exact-name parser instead of broadening every bare
    # tool tag to array payloads.
    for xml in _BARE_TODO_ARRAY_TAG_RE.finditer(text):
        try:
            items = json.loads(xml.group("args"))
        except json.JSONDecodeError:
            continue
        if not isinstance(items, list):
            continue
        actions.append(_format_action("todo_write", {"items": items}))
    if actions:
        return actions

    for xml in _BARE_NAMED_TOOL_TAG_RE.finditer(text):
        try:
            args = json.loads(xml.group("args"))
        except json.JSONDecodeError:
            continue
        if not isinstance(args, dict):
            continue
        name = _normalize_action_name(xml.group("name").strip())
        if name == "todo_write" and "todos" in args and "items" not in args:
            args["items"] = args.pop("todos")
        actions.append(_format_action(name, args))
    if actions:
        return actions

    # A few reasoning models wrap otherwise-valid ReAct calls in a literal
    # ``<Action>`` block and put one call on each line.  This is still an
    # explicit execution boundary, so recover the lines conservatively; do
    # not scan arbitrary prose for call-looking snippets.
    for container in _ACTION_XML_CONTAINER_RE.finditer(text):
        for line in (container.group("body") or "").splitlines():
            candidate = line.strip().lstrip("-*").strip()
            parsed = _parse_action(candidate)
            if parsed is None:
                continue
            actions.append(_format_action(*parsed))
    if actions:
        return actions

    for fenced in _FENCED_JSON_RE.finditer(text):
        try:
            payload = json.loads(fenced.group("body"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        raw_name = (
            payload.get("command")
            or payload.get("tool")
            or payload.get("name")
            or payload.get("action")
        )
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        args = payload.get("kwargs") or payload.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        actions.append(_format_action(_normalize_action_name(raw_name.strip()), args))
    if actions:
        return actions

    # Last-resort recovery for a complete bare ``tool_name({...})`` response.
    # This is intentionally full-string only, so ordinary prose containing a
    # tool example remains prose and cannot trigger execution.
    bare = _BARE_INLINE_TOOL_CALL_RE.fullmatch(text or "")
    if bare:
        try:
            args = json.loads(bare.group("args"))
        except json.JSONDecodeError:
            args = None
        if isinstance(args, dict):
            actions.append(_format_action(_normalize_action_name(bare.group("name").strip()), args))
    return actions


def _extract_tool_action_from_loose_output(text: str) -> str | None:
    """Recover tool calls from common non-ReAct envelopes.

    Some OpenAI-compatible models stream XML-ish tool tags or fenced JSON
    commands instead of the expected ``Action: tool({...})`` line. Treat those
    as an Action so the loop executes the real tool instead of displaying a
    fake tool call as assistant prose.
    """
    actions = _extract_tool_actions_from_loose_output(text)
    return actions[0] if actions else None


def _format_action(name: str, args: dict[str, Any]) -> str:
    return f"{name}({json.dumps(args, ensure_ascii=False)})"


def _is_format_violation(
    step: ReActStep,
    final_answer: str | None,
) -> bool:
    """True when the LLM returned text but produced zero ReAct anchors.

    Signals "the LLM is not following Thought/Action/Final-Answer
    format" — usually because it dumped a JSON plan, a tool-call
    envelope, or free-form prose instead. Two consecutive violations
    means we should stop poking the same rake and hand back to the
    caller's direct-LLM fallback, which doesn't force ReAct format.
    """
    raw = (step.raw_llm_output or "").strip()
    if not raw:
        # Truly empty response is a different failure (network /
        # upstream error); caller's existing exception path handles
        # that.
        return False
    return (
        final_answer is None
        and not step.thought
        and not step.action
        and not step.observation
        and not step.public_update
    )


def _placeholder_observation(action: str) -> str:
    if not action or action.lower() in {"none", "n/a", ""}:
        return "N/A"
    return (
        f"(未执行观察) Action '{action}' 没有解析为可执行的已注册工具调用。"
        "工具系统仍然可用；请检查工具名，并改用 skill_name({JSON}) 格式重试。"
    )


def _normalize_action_name(name: str) -> str:
    return _ACTION_NAME_ALIASES.get(name, name)


def _parse_action(action_text: str) -> tuple[str, dict[str, Any]] | None:
    if not action_text:
        return None
    text = action_text.strip().rstrip(".").rstrip(";")
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_./:-]*", text):
        return (_normalize_action_name(text), {})
    m = _ACTION_CALL_RE.match(text)
    if not m:
        return None
    name = _normalize_action_name(m.group("name"))
    args_raw = (m.group("args") or "").strip()
    if not args_raw:
        return (name, {})
    try:
        parsed = json.loads(args_raw)
    except json.JSONDecodeError:
        try:
            kv_pairs: dict[str, Any] = {}
            for pair in re.split(r",(?![^{}\[\]]*[}\]])", args_raw):
                if "=" not in pair:
                    continue
                k, _, v = pair.partition("=")
                kv_pairs[k.strip()] = v.strip().strip("\"'")
            parsed = kv_pairs if kv_pairs else None
        except (TypeError, ValueError):
            parsed = None
    if isinstance(parsed, list) and name == "todo_write":
        parsed = {"items": parsed}
    if not isinstance(parsed, dict):
        return None
    return (name, parsed)
