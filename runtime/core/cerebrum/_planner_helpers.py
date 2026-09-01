"""Pure helper functions extracted from :mod:`runtime.core.cerebrum.llm_planner`.

Kept here so the main module stays under 1000 lines. These helpers cover
plan-JSON extraction, edge inference (explicit ``depends_on`` → template
refs → linear fallback), cycle detection, and prompt-section rendering
(team roster / conversation history). Everything is re-exported from
``llm_planner`` — no public API lives in this module.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from runtime.platform.models import ParsedIntent

from runtime.platform.models import WorkflowEdge

from .planner import PlannerError

# Fenced ```json ... ``` code block · preferred form because it's
# unambiguous even when the LLM's prose contains other braces. The
# planner prompt itself asks for this form, so the happy path is
# fenced; non-fenced is the fallback.
_JSON_FENCED_RE = re.compile(
    r"```(?:json)?\s*\n(\{.*?\})\s*\n```",
    re.DOTALL | re.IGNORECASE,
)


# Template-reference scan pattern — match ``{nN}`` / ``{nN.field}`` /
# ``{nN.field.sub}`` embedded anywhere in a string. Used to infer
# data dependencies when the LLM didn't emit explicit ``depends_on``.
_TEMPLATE_REF_RE = re.compile(r"\{(n\d+)(?:\.[a-zA-Z0-9_.]+)?\}")

_NON_SKILL_ACTION_NAMES: frozenset[str] = frozenset(
    {
        "call_agent",
    }
)


def _render_team_roster_section(user_context: dict) -> str:
    roster = user_context.get("agent_roster") if isinstance(user_context, dict) else None
    if not isinstance(roster, list) or not roster:
        return ""
    team_mode = str(user_context.get("team_mode") or "chat").strip().lower()
    team_phase = str(user_context.get("team_phase") or "").strip().lower()

    # Find the active speaker · the per-turn user_context tags the
    # currently-running agent with ``is_self=True``. Without a
    # "YOU ARE" banner the LLM looks at the roster and cosplays the
    # first-listed teammate (previously this meant every agent, including
    # Coder, said "I'm Echo"). We also render a per-entry ``(YOU)``
    # marker so the association survives any prompt reordering.
    self_entry: dict | None = None
    for entry in roster:
        if isinstance(entry, dict) and entry.get("is_self"):
            self_entry = entry
            break
    self_role = str((self_entry or {}).get("role") or "").strip().lower()

    lines: list[str] = []
    if self_entry is not None:
        self_id = str(self_entry.get("agent_id") or "").strip()
        self_name = str(self_entry.get("display_name") or self_id)
        lines.append("## YOUR IDENTITY")
        lines.append(
            f"You are **{self_name}** (agent id: `{self_id}`). When asked "
            f"who you are, say you are {self_name}. Do NOT impersonate "
            "teammates from the roster below · they are DIFFERENT agents "
            "sharing this thread with you."
        )
        # Persona vocabulary rule · the framework's organ-naming
        # convention ("19 organs", "tentacle", "siphon", "eyes", etc.)
        # is INTERNAL · the user sees a team of named characters /
        # their soul or training data and sound like organs instead
        # of teammates.
        lines.append(
            "Refer to yourself and your teammates as people / characters "
            "/ team members (人物 / 队友 / 成员) — NEVER as "
            '"tentacles" or "触手". The internal organ names are '
            "an implementation detail; the user-facing team is "
            "a cast of personas."
        )
        lines.append("")

    lines.append("## TEAM ROSTER")
    lines.append("You are part of a multi-agent team in this thread. Your teammates:")
    for entry in roster:
        if not isinstance(entry, dict):
            continue
        aid = str(entry.get("agent_id") or "").strip()
        if not aid:
            continue
        name = str(entry.get("display_name") or aid)
        role = str(entry.get("role") or "").strip()
        role_tag = " · **TL (team lead)**" if role == "tl" else ""
        self_tag = " · **(YOU)**" if entry.get("is_self") else ""
        lines.append(f"- `{aid}` — {name}{role_tag}{self_tag}")
    lines.append(
        "\nWhen the user asks who else is on the team, list these "
        "teammates by name. Do NOT claim to be alone · they are real "
        "agents."
    )
    if team_mode == "chat":
        lines.append(
            "\n### ROUTING PROTOCOL (chat mode · team lead only)\n"
            "You have TWO directive sentinels. Use at most ONE per turn, "
            "and only when the situation warrants it.\n\n"
            "**1 · Handoff** · route to a single specialist:\n\n"
            "    [ROUTE TO: <agent_id>]\n"
            "    <one-sentence handoff note>\n\n"
            "Use when one teammate is clearly the right fit. After the "
            "sentinel line STOP — do not attempt to answer yourself.\n\n"
            "**2 · Vote** · collect answers from the whole roster and "
            "arbitrate (MAJORITY / synthesis):\n\n"
            "    [VOTE: <question to put to the team>]\n\n"
            "Use when the question is CONTENTIOUS or BENEFITS from "
            "multiple perspectives (architecture choices, 'should we …', "
            "strategy decisions with tradeoffs). The dispatcher runs "
            "every teammate on the question in parallel, then an "
            "arbiter agent compares the candidate answers and writes a "
            "consolidated verdict. After the sentinel line STOP.\n\n"
            "Rules for BOTH:\n"
            "- Use SPARINGLY · default to answering yourself.\n"
            "- DO NOT self-route · never emit ``[ROUTE TO: <your_own_id>]``.\n"
            "- DO NOT route or vote for conversational exchanges ('你好', "
            "'谢谢', '介绍一下') · handle directly.\n"
            "- Voting is heavier than routing · prefer routing when one "
            "specialist suffices."
        )
    elif team_mode == "cowork":
        phase_label = team_phase or "work"
        lines.append(
            "\n### COWORK PROTOCOL\n"
            f"Current cowork phase: `{phase_label}`. Your roster role: "
            f"`{self_role or 'member'}`.\n\n"
            "Cowork mode is a coordinated team round: the TL plans, "
            "members contribute from their own expertise, then the TL "
            "synthesizes the final answer. Do NOT emit [ROUTE TO] or "
            "[VOTE] sentinels in cowork mode.\n\n"
            "Phase rules:\n"
            "- `plan` + TL: write a brief plan and assign what each "
            "member should inspect. Stop after the plan; do not present "
            "the final answer yet.\n"
            "- `work` + member: provide your focused contribution, "
            "evidence, risks, assumptions, and recommended next step. "
            "Do not impersonate the TL and do not claim final authority.\n"
            "- `synthesize` + TL: merge teammate messages into one final "
            "answer, name important disagreements or uncertainty, and "
            "give concrete next actions."
        )
    return "\n".join(lines)


def _render_conversation_history(
    intent: ParsedIntent,
    *,
    max_messages: int = 12,
    max_chars: int = 4_000,
) -> str:
    """Render OpenAI-compatible chat history carried by the gateway."""
    payload = intent.user_context.get("conversation_messages", [])
    if not isinstance(payload, list):
        return ""
    rendered: list[str] = []
    total = 0
    for item in payload[-max_messages:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("system", "user", "assistant"):
            continue
        if isinstance(content, list):
            # Multimodal turn (an image upload). The planner reads text only,
            # but dropping the whole message would hide what was asked; note
            # the image so the plan can account for it.
            texts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("text")
            ]
            images = sum(
                1
                for part in content
                if isinstance(part, dict) and part.get("type") in ("image_url", "image")
            )
            content = " ".join(t for t in texts if t.strip())
            if images:
                content = f"{content} [附带 {images} 张图片]".strip()
        if not isinstance(content, str) or not content.strip():
            continue
        line = f"[{role}] {content.replace(chr(10), ' ').strip()}"
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(line) > remaining:
            line = line[: max(0, remaining - 1)] + "…"
        rendered.append(line)
        total += len(line) + 1
    return "\n".join(rendered)


def _extract_edges(
    plan_nodes: list[dict],
    node_count: int,
) -> list[WorkflowEdge]:
    """Compute the TaskGraph edges for a plan.

    Three signals are tried, in priority order:

    1. **Explicit ``depends_on``** in the plan node. A list of node
       index integers (``0``, ``1``, ...) or node-id strings
       (``"n0"``, ``"n1"``, ...). Most expressive · the LLM says
       outright "n2 needs n0 and n1".
    2. **Template-reference inference.** Scan every arg string for
       ``{nX}`` / ``{nX.key}`` patterns. If n2's args reference n0,
       we add the edge n0→n2 even without ``depends_on``. This is
       the bridge for existing prompts that already use the
       template syntax (see planner_base doc) but haven't been
       updated to emit ``depends_on``.
    3. **Linear fallback.** If neither signal yields any edge for a
       given node, stitch it to the previous one — the pre-2026-04
       behavior. Preserves the "always produce a valid serial plan"
       property as a floor.

    Why three signals and not just one:
    * Removing the linear fallback would break every existing
      planner_base prompt in the wild, and the tests that assert
      "2 nodes → 1 edge" in downstream pipelines.
    * Requiring ``depends_on`` would require every deployment to
      update their prompt before the new code works.
    * Template inference alone can miss cases where the LLM
      passes intermediate values via metadata or closures (rare
      but not impossible).

    The merged edge set is deduplicated and sanity-checked for
    cycles — cyclic deps would cause topo sort to deadlock.
    """
    edges: set[tuple[str, str]] = set()

    # Nodes whose ``depends_on`` field is a deliberate signal · pass
    # 3's linear fallback must respect these and NOT add a spurious
    # edge from the previous node.
    #
    # A ``depends_on`` is "deliberate" iff:
    #   (a) it's an empty list → LLM explicitly said "no deps", or
    #   (b) at least one of its entries resolved to a valid node →
    #       the LLM gave a usable dependency; trust the rest of
    #       its intent too
    #
    # A list of all-invalid entries (``["n99", "typo"]``) is
    # treated as absence-of-signal and falls back to linear · that
    # matches the pre-fix "typos don't break plans" behavior so
    # an LLM that miscounts indices still produces a runnable plan.
    explicit_deps_nodes: set[str] = set()

    # ── Pass 1: explicit depends_on ─────────────────────────
    for i, nd in enumerate(plan_nodes):
        # ``nd.get("depends_on") or []`` would conflate ``None`` and
        # ``[]`` — tell them apart via ``"depends_on" in nd`` so
        # only an actually-present field can register as explicit.
        has_field = "depends_on" in nd
        raw_deps = nd.get("depends_on")
        if not isinstance(raw_deps, list):
            # Non-list (None / string / dict / ...) · no signal.
            continue
        if has_field and len(raw_deps) == 0:
            # depends_on=[] · explicit "no deps".
            explicit_deps_nodes.add(f"n{i}")
        accepted_any = False
        for dep in raw_deps:
            src = _normalize_node_ref(dep)
            if src is None:
                continue
            # Self-reference / out-of-range → drop silently so an
            # LLM typo doesn't sink the whole plan. Topological
            # validation catches any remaining issue later.
            if src == f"n{i}":
                continue
            idx = _node_index(src)
            if idx is None or idx >= node_count:
                continue
            edges.add((src, f"n{i}"))
            accepted_any = True
        if accepted_any:
            # At least one usable dep · we take that as the LLM
            # having successfully declared its parents · skip the
            # fallback here too.
            explicit_deps_nodes.add(f"n{i}")

    # ── Pass 2: template reference scan ─────────────────────
    for i, nd in enumerate(plan_nodes):
        args = nd.get("args") or {}
        if not isinstance(args, dict):
            continue
        target = f"n{i}"
        for value in args.values():
            if not isinstance(value, str):
                continue
            for m in _TEMPLATE_REF_RE.finditer(value):
                src = m.group(1)
                if src == target:
                    continue
                idx = _node_index(src)
                if idx is None or idx >= node_count:
                    continue
                edges.add((src, target))

    # ── Pass 3: linear fallback for orphans ─────────────────
    # A node is "orphan" iff:
    #   - it's not the first node, AND
    #   - it has no incoming edge from pass 1 or 2, AND
    #   - it did NOT explicitly declare depends_on (any form).
    # The third clause is what makes ``depends_on: []`` mean "no
    # dependencies, run in parallel with siblings" · without it
    # that explicit signal would get squashed back into a linear
    # chain here.
    in_degrees = {f"n{i}": 0 for i in range(node_count)}
    for _src, dst in edges:
        in_degrees[dst] = in_degrees.get(dst, 0) + 1
    for i in range(1, node_count):
        node_id = f"n{i}"
        if node_id in explicit_deps_nodes:
            # LLM spoke · trust it · no fallback.
            continue
        if in_degrees.get(node_id, 0) == 0:
            edges.add((f"n{i - 1}", node_id))

    # ── Cycle check ─────────────────────────────────────────
    # If the LLM emitted contradictory depends_on (a cycle), we'd
    # rather raise here than have GraphRuntime deadlock on topo
    # sort later. Simple DFS white/gray/black.
    if _has_cycle(node_count, edges):
        raise PlannerError(f"LLM plan has cyclic dependencies: {sorted(edges)}")

    # Stable order for deterministic tests · sort by (src index, dst index).
    ordered = sorted(
        edges,
        key=lambda e: (_node_index(e[0]) or 0, _node_index(e[1]) or 0),
    )
    return [WorkflowEdge(from_node=s, to_node=d) for s, d in ordered]


def _normalize_node_ref(ref: Any) -> str | None:
    """Accept ``0`` / ``"0"`` / ``"n0"`` / ``"N0"`` — any form a
    reasonable LLM might emit · return the canonical ``"n0"`` form."""
    if isinstance(ref, int):
        return f"n{ref}" if ref >= 0 else None
    if isinstance(ref, str):
        r = ref.strip().lower()
        if r.startswith("n") and r[1:].isdigit():
            return r
        if r.isdigit():
            return f"n{r}"
    return None


def _node_index(node_id: str) -> int | None:
    if not node_id.startswith("n"):
        return None
    try:
        return int(node_id[1:])
    except ValueError:
        return None


def _has_cycle(node_count: int, edges: set[tuple[str, str]]) -> bool:
    """DFS cycle detection on the directed edge set. O(V + E)."""
    adj: dict[str, list[str]] = {f"n{i}": [] for i in range(node_count)}
    for src, dst in edges:
        adj.setdefault(src, []).append(dst)
    WHITE, GRAY, BLACK = 0, 1, 2  # noqa: N806
    color = {n: WHITE for n in adj}

    def visit(n: str) -> bool:
        color[n] = GRAY
        for nxt in adj.get(n, []):
            c = color.get(nxt, WHITE)
            if c == GRAY:
                return True
            if c == WHITE and visit(nxt):
                return True
        color[n] = BLACK
        return False

    return any(visit(n) for n in adj if color[n] == WHITE)


def _scan_balanced_object(text: str, start: int) -> str | None:
    """Walk forward from ``start`` (must point at a ``{``) and return
    the slice ending at the matching close brace, or ``None`` if no
    balanced close exists.

    String-aware: any ``{``/``}`` inside a double-quoted string — even
    an escaped ``\"`` — does not affect the nesting counter. This is
    what makes this function robust to LLM output like::

        Here is your plan: {
          "reasoning": "the path is {foo}/bar.txt",
          "nodes": [...]
        }
        (hope that helps!)

    The naive ``r"\\{.*\\}"`` regex would grab from the first ``{``
    through the ``}`` inside the string literal — or through any later
    brace in the free-form tail — and produce unparseable junk.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _derive_task_type(intent: ParsedIntent) -> str:
    mapping = {
        "debug": "code_fix",
        "refactor": "code_design",
        "plan": "multi_step_reasoning",
        "query": "quick_lookup",
        "chitchat": "chitchat",
    }
    return mapping.get(intent.intent_type, "general")
