"""Build Anthropic ``tools`` specs from the skill registry.

``build_anthropic_tool_specs`` translates an Echo ``SkillRegistry``
into the ``ToolSpec`` list the model providers' tool-use API accepts,
applying capability activation, mode-based culling and an agent's
allow-list. It lived in ``sensing/gateway/tool_bridge`` but is pure
registry→spec conversion — its inputs come from ``core`` (capability /
todo protocol), ``execution`` (skill policy) and ``platform``
(``ToolSpec``), never the gateway — so the execution-layer ephemeral
runner shouldn't reach up into the web layer to call it. ``tool_bridge``
re-exports it for the gateway's streaming path.
"""

from __future__ import annotations

import inspect
import re
from functools import lru_cache
from typing import Any

from runtime.core.cerebrum.capability_router import (
    activate_capabilities,
    filter_surface_compatible_skills,
    order_skill_names,
)
from runtime.core.cerebrum.todo_protocol import context_mode
from runtime.platform.models.llm import ToolSpec

PRIORITY_SKILLS: frozenset[str] = frozenset(
    {
        "todo_write",
        "search_capabilities",
        "query_capability",
        "use_capability",
        "execute_skill",
        "call_agent_parallel",
        "bb_keys",
        "bb_read",
        "bb_write",
        "deep-research-swarm",
        "deep-research",
        "report-writing",
        "docx",
    }
)


TASK_CHAIN_SKILLS: frozenset[str] = frozenset(
    {
        "deep-research-swarm",
        "deep-research",
        "report-writing",
        "docx",
    }
)


def _goal_tokens(goal: str) -> frozenset[str]:
    """Lowercased, stopword-filtered tokens of the current goal.

    Used as the relevance query for tool search — matching skill names /
    descriptions against the user's actual intent so a large registry
    (many MCP servers, plugin packs) doesn't drown the tool budget with
    unrelated tools.
    """
    _stop = frozenset(
        {
            "the",
            "a",
            "an",
            "and",
            "or",
            "for",
            "with",
            "into",
            "from",
            "this",
            "that",
            "these",
            "those",
            "please",
            "help",
            "me",
            "i",
            "you",
            "we",
            "my",
            "your",
            "of",
            "to",
            "in",
            "on",
            "at",
            "by",
            "is",
            "are",
            "was",
            "be",
            "do",
            "does",
            "did",
            "can",
            "could",
            "should",
            "would",
            "will",
            "what",
            "when",
            "where",
            "which",
            "how",
            "who",
            "there",
            "about",
            "it",
            "as",
            "if",
            "not",
            "no",
            "using",
            "use",
            "want",
            "need",
            "then",
            "than",
            "so",
            "also",
        }
    )
    text = (goal or "").lower()
    words = [w for w in re.findall(r"[a-z][a-z0-9_\-]{1,}", text)]
    return frozenset(w for w in words if len(w) > 2 and w not in _stop)


def _skill_text(name: str, description: str) -> str:
    """Searchable text for a skill: the name plus a compact description.

    The MCP bridge registers tools as ``mcp_<server>_<tool>`` skills whose
    descriptions come from the MCP tool descriptions, so tool search ranks
    remote tools by the same overlap metric as local skills.
    """
    name_tokens = " ".join(re.split(r"[^a-z0-9]", name.lower()))
    return f"{name_tokens} {description or ''}".lower()


def _skill_description(registry: Any, name: str) -> str:
    """Safe description lookup for relevance scoring (never raises)."""
    try:
        skill = registry.get(name)
        return str(getattr(skill, "description", "") or "")
    except (AttributeError, TypeError, KeyError, ValueError):
        return ""


def _relevance_score(name: str, description: str, tokens: frozenset[str]) -> int:
    """Keyword-overlap score between the goal and a skill.

    A token in the skill name counts 2 (the name is the strongest signal);
    a token in the description counts 1. No embeddings needed — this is the
    honest, dependency-free tool-search primitive that Claude Code's
    ToolSearch formalizes.
    """
    if not tokens:
        return 0
    text = _skill_text(name, description)
    score = 0
    for tok in tokens:
        if tok in name.lower():
            score += 2
        elif tok in text:
            score += 1
    return score


TASK_CHAIN_MODES: frozenset[str] = frozenset(
    {
        "agent",
        "react",
        "deep",
        "deep_research",
        "research",
        "swarm",
        "swarms",
        "team",
        "code",
    }
)


_INTERNAL_PARAMS = frozenset(
    {
        "sandbox_dir",
        "allow_sensitive",
        "self",
        "cls",
        "_kw",
        "kwargs",
        "args",
    }
)


@lru_cache(maxsize=512)
def _input_schema_from_handler(handler: Any) -> tuple[dict[str, Any], ...]:
    """Derive a JSON-Schema ``input_schema`` from a Python handler's
    signature so the LLM sees the correct parameter names and types.

    Parameters starting with ``_`` (e.g. ``_kw``), runtime-injected
    ones (``sandbox_dir``, ``allow_sensitive``), and ``self``/``cls``
    are excluded from the schema.

    Result is cached (``lru_cache``) because ``inspect.signature`` is
    expensive and handlers are immutable — a typical turn builds 50
    specs, and the same handler objects persist across turns.
    """
    try:
        sig = inspect.signature(handler)
    except (ValueError, TypeError):
        return ({"type": "object", "properties": {}, "additionalProperties": True},)

    properties: dict[str, Any] = {}
    required: list[str] = []

    _SIMPLE_TYPE_MAP: dict[str, str] = {  # noqa: N806
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
        "bool_": "boolean",
    }

    # A VAR_KEYWORD (``**kwargs``) handler legitimately accepts arbitrary
    # parameters, so its schema must stay permissive; without one we close
    # ``additionalProperties`` so the model cannot smuggle internal privilege
    # overrides (``allow_sensitive`` / ``allow_private``) past the published
    # tool schema (audit C2). The executor still strips them at dispatch as a
    # second, independent boundary.
    has_var_keyword = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

    for pname, param in sig.parameters.items():
        if pname in _INTERNAL_PARAMS or pname.startswith("_"):
            continue
        kind = param.kind
        if kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        prop: dict[str, Any] = {}
        annotation = param.annotation

        if annotation is inspect.Parameter.empty:
            prop["type"] = "string"
        else:
            ann_str = str(annotation)
            origin = getattr(annotation, "__origin__", None)

            # Handle both real types (list, dict) and their string
            # forms ("list", "dict") — the latter appear when the
            # module uses ``from __future__ import annotations``.
            if origin is list or ann_str in ("list", "List"):
                prop["type"] = "array"
            elif origin is dict or ann_str in ("dict", "Dict"):
                prop["type"] = "object"
            else:
                matched = False
                for py_type, json_type in _SIMPLE_TYPE_MAP.items():
                    if ann_str == py_type or ann_str.startswith(py_type + "."):
                        prop["type"] = json_type
                        matched = True
                        break
                if not matched:
                    # ``typing.Any`` (or any unrecognized annotation)
                    # falls back to string — handlers that need array
                    # semantics should annotate with ``list``.
                    prop["type"] = "string"

        if param.default is inspect.Parameter.empty:
            required.append(pname)
        else:
            default = param.default
            if isinstance(default, (bool, int, float, str)):
                prop["default"] = default

        properties[pname] = prop

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": has_var_keyword,
    }
    if required:
        schema["required"] = required
    return (schema,)


def build_anthropic_tool_specs(
    registry: Any,
    *,
    max_skills: int = 50,
    agent: Any = None,
    user_context: dict[str, Any] | None = None,
    goal: str = "",
) -> list[ToolSpec]:
    """Translate an Echo ``SkillRegistry`` into a list of
    ``ToolSpec`` the Anthropic ``tools`` param can accept.

    Input schema is intentionally permissive (``{"type":"object",
    "additionalProperties": true}``) because Echo skills don't
    carry a formal parameter schema — the description already
    documents the shape. Claude infers reasonable arg names from
    the description; in practice this works well for common
    skills like ``list_cwd``, ``read_file``, ``web_search``.

    The ``max_skills`` cap keeps the tool list from blowing up
    prompt-cache friendliness on installs with hundreds of
    skills. The first N in registry order win, but ``PRIORITY_SKILLS``
    are *always* included even if they would otherwise be culled —
    that's how UI-critical meta-tools like ``todo_write`` survive
    a registration order that puts ``agent_meta`` last.

    Hidden skills: ``exit_plan_mode`` is a plan-mode protocol
    primitive, not a real tool. ``call_agent`` USED to be hidden
    too on the theory delegation is "planner-only", but that
    conflated cross-roster routing (which IS planner-only · uses
    ``[ROUTE TO: ...]``) with sub-agent spawning (one-shot
    delegation pattern · researcher / debugger / reviewer / ...). The latter
    is genuinely a tool the lead agent should be able to call,
    so it's now in the catalog · see ``delegation_skills.py``.
    """
    HIDDEN: set[str] = {"exit_plan_mode"}  # noqa: N806
    try:
        all_names = [n for n in registry.all_names() if n not in HIDDEN]
    except (AttributeError, TypeError):
        return []

    def _enabled(name: str) -> bool:
        try:
            return bool(registry.is_enabled(name))
        except (AttributeError, TypeError, ValueError):
            return True

    all_names = [name for name in all_names if _enabled(name)]
    all_names = filter_surface_compatible_skills(
        all_names,
        user_context=user_context,
        goal=goal,
    )

    mode = context_mode(user_context)
    if mode == "chat" or (mode and mode not in TASK_CHAIN_MODES):
        all_names = [name for name in all_names if name not in TASK_CHAIN_SKILLS]

    if agent is not None:
        try:
            from runtime.execution.misc.skill_policy import filter_allowed_names

            all_names = filter_allowed_names(all_names, agent=agent)
        except (AttributeError, TypeError, ValueError):
            # ``filter_allowed_names`` is the agent's tool allow-list gate —
            # swallowing this and leaving ``all_names`` as-is used to fail
            # OPEN (the agent got the FULL unrestricted skill list on any
            # unexpected error, e.g. a malformed ``agent`` object). Fail
            # CLOSED instead: an agent whose allow-list can't be computed
            # gets no tools, not every tool.
            import logging

            logging.getLogger(__name__).warning(
                "filter_allowed_names failed for agent=%r; denying all skills",
                getattr(agent, "agent_id", agent),
                exc_info=True,
            )
            all_names = []

    activation = activate_capabilities(
        goal,
        user_context=user_context,
        registry=registry,
    )
    all_names = order_skill_names(
        all_names,
        activation=activation,
        registry=registry,
    )

    # Surface package-level / meta tools first, then fill the remaining
    # budget with ordinary tools. This keeps the model's first mental
    # frame at the plugin / skill-pack level instead of a flat pile of
    # child tools. Priority entries still survive catalog clipping.
    forced: list[str] = []
    forced_set: set[str] = set()
    activation_priority = set(activation.priority_skills)
    workflow_preset = str((user_context or {}).get("workflow_preset") or "").strip().lower()
    personal_mode = str((user_context or {}).get("personal_mode") or "").strip().lower()
    for name in all_names:
        if (
            name in PRIORITY_SKILLS
            or name in activation_priority
            or (workflow_preset == "audit.ultracode" and name == "run_orchestration")
            or (personal_mode == "research" and name == "deep-research")
        ):
            forced.append(name)
            forced_set.add(name)
    selected = list(forced)
    budget = max_skills + len(forced)
    candidates = [name for name in all_names if name not in forced_set]
    tokens = _goal_tokens(goal)
    if tokens and len(candidates) + len(forced) > budget:
        # Tool search: when the registry outgrows the budget, keep the
        # skills most relevant to the current goal instead of the first N
        # in registration order. MCP tools-as-skills rank by their real
        # descriptions, so remote tools follow the same relevance signal.
        # A stable tiebreak (original registry order) keeps the selection
        # deterministic across calls.
        _order_index = {name: i for i, name in enumerate(all_names)}
        scored = [
            (name, _relevance_score(name, _skill_description(registry, name), tokens))
            for name in candidates
        ]
        scored.sort(key=lambda pair: (-pair[1], _order_index.get(pair[0], 0)))
        for name, _score in scored:
            if len(selected) >= budget:
                break
            selected.append(name)
    else:
        for name in candidates:
            if len(selected) >= budget:
                break
            selected.append(name)

    specs: list[ToolSpec] = []
    for name in selected:
        try:
            skill = registry.get(name)
            desc = (getattr(skill, "description", "") or "").strip()
            handler = getattr(skill, "handler", None)
        except (AttributeError, TypeError, KeyError):
            desc = ""
            handler = None
        if not desc:
            desc = f"Run the `{name}` skill."
        input_schema = (
            _input_schema_from_handler(handler)[0]
            if handler is not None
            else {"type": "object", "properties": {}, "additionalProperties": True}
        )
        specs.append(
            ToolSpec(
                name=name,
                description=desc,
                input_schema=input_schema,
            )
        )
    return specs
