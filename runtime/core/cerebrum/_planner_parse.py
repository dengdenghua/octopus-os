"""Plan-JSON extraction + node validation for :class:`LLMPlanner`.

Extracted from :mod:`runtime.core.cerebrum.llm_planner` so the plan
parsing / validation logic lives apart from the planner class. Nothing
here is public API — ``llm_planner`` re-exports only the class methods
that delegate to these functions.
"""

from __future__ import annotations

import json
from typing import Any

from ._planner_helpers import _JSON_FENCED_RE, _NON_SKILL_ACTION_NAMES, _scan_balanced_object
from .planner import PlannerError


def extract_plan_json(text: str) -> dict:
    """Extract the LLM's JSON plan from free-form text.

    Strategy (tries in order, returns the first that parses):

    1. **Fenced block** — ``` ```json\\n{...}\\n``` ``` — matches
       the planner prompt's stated output format exactly. This is
       the fast path for well-behaved models.
    2. **Balanced-brace scan** — walk from each ``{`` looking for
       the matching close, string-aware. Tolerates the LLM
       putting prose before/after or mixing in example JSON the
       way some models do.

    History: the previous implementation was a single greedy
    ``r"\\{.*\\}"`` regex. When the LLM's response contained any
    extra ``{`` (example args, emoji, a braces-in-string literal)
    it would grab from the *first* ``{`` through the *last* ``}``
    across the whole document, producing an unparseable mega-blob.
    The failure mode was intermittent parse errors with no hint
    that the extractor itself was to blame.
    """
    # Path 1 · fenced block
    fenced = _JSON_FENCED_RE.search(text)
    if fenced:
        candidate = fenced.group(1)
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:  # noqa: BLE001 — fenced block wasn't valid JSON; fall through to balanced-scan
            pass

    # Path 2 · balanced-brace scan from each ``{`` in text order.
    # We can't short-circuit on the first ``{`` because the
    # model might write ``"{example}"`` or ``{foo}`` in prose
    # before the real plan object. Try each position and use the
    # first one that parses as a dict.
    i = 0
    last_parse_error: Exception | None = None
    while True:
        idx = text.find("{", i)
        if idx < 0:
            break
        slice_ = _scan_balanced_object(text, idx)
        # If this ``{`` has no matching ``}`` (unbalanced remainder),
        # skip past it and try the next ``{`` rather than giving up —
        # a malformed opener earlier in the text shouldn't prevent
        # finding a well-formed object later.
        if slice_ is None:
            i = idx + 1
            continue
        try:
            parsed = json.loads(slice_)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as e:
            last_parse_error = e
        i = idx + 1  # try next ``{``

    # Exhausted — raise with both error kinds distinguished so
    # the caller's log tells "no JSON found" apart from "JSON
    # present but all candidates malformed".
    if last_parse_error is not None:
        raise PlannerError(
            f"LLM JSON parse failed (no balanced candidate parsed): {last_parse_error}"
        ) from last_parse_error
    raise PlannerError(f"LLM response lacks JSON: {text[:200]!r}")


def validate_plan_nodes(
    nodes: list,
    registry: Any,
    max_nodes: int,
) -> list[dict]:
    """Validate the LLM's raw plan nodes against the skill registry.

    Returns the cleaned node list (skill + args, plus a preserved
    ``depends_on`` when the LLM emitted one). Raises ``PlannerError``
    on any malformed / unknown / over-long node.
    """
    if not isinstance(nodes, list) or not nodes:
        raise PlannerError("LLM plan has no nodes")
    if len(nodes) > max_nodes:
        raise PlannerError(f"LLM plan too long: {len(nodes)} > {max_nodes}")

    validated: list[dict] = []
    for i, nd in enumerate(nodes):
        if not isinstance(nd, dict):
            raise PlannerError(f"node {i} is not a dict")
        skill = nd.get("skill")
        if not isinstance(skill, str) or not skill:
            raise PlannerError(f"node {i} missing skill name")
        if skill in _NON_SKILL_ACTION_NAMES:
            raise PlannerError(
                f"node {i}: {skill!r} is a subagent action, not a skill; "
                "use the team routing/subagent dispatch channel instead"
            )
        if not registry.has(skill):
            raise PlannerError(
                f"node {i}: unknown skill {skill!r} "
                f"(available: {', '.join(registry.all_names()[:10])}...)"
            )
        args = nd.get("args", {})
        if not isinstance(args, dict):
            raise PlannerError(f"node {i}: args must be dict, got {type(args).__name__}")
        out: dict[str, Any] = {"skill": skill, "args": args}
        # Preserve explicit ``depends_on`` so ``_extract_edges``
        # can honor it. Pre-2026-04 this field got stripped here
        # before ``plan()`` built edges · meaning the LLM's
        # parallel-DAG signal was silently dropped and swarm
        # split_strategy="topo_layers" always degenerated.
        # Validation keeps the "explicit vs absent" distinction
        # the extractor relies on: the KEY must survive when the
        # LLM provided one (even if the list is empty · "[]" is
        # the explicit "no deps" signal).
        if "depends_on" in nd:
            raw = nd.get("depends_on")
            if raw is None:
                # None is ambiguous · treat like absent to avoid
                # conflating "field present set to null" with
                # "explicit empty list". Drop the key.
                pass
            elif isinstance(raw, list):
                cleaned: list[Any] = []
                for entry in raw:
                    # Accept ints (node index), "nX" ids, bare
                    # ``n`` numerics. Anything else is silently
                    # dropped · no crash on slightly-off LLM output.
                    if isinstance(entry, int) or isinstance(entry, str) and entry:
                        cleaned.append(entry)
                out["depends_on"] = cleaned
            else:
                # Not a list · ignore · extractor interprets as
                # "no explicit signal".
                pass
        validated.append(out)
    return validated
