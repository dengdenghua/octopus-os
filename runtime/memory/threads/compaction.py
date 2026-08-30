"""Context compaction policy.

As a thread accrues turns, replay cost + prompt context grow linearly.
This module supplies the periodic summarise-and-fold mechanic: at
regular intervals, older turns collapse into a single condensed item
so the active window stays bounded.

Design choices:

* **Mechanical summary, pluggable LLM hook.** The default summariser
  is deterministic: it concatenates item headlines (``agentMessage``
  first lines, ``commandExecution`` commands, ``fileChange`` paths)
  into a single ``agentMessage`` item. Callers can supply a
  ``summarise`` callback that takes the list of superseded turns and
  returns the summary text — that's where an LLM-backed rewriter
  plugs in.
* **Append-only, not rewrite.** The compactor never rewrites the
  thread log. It appends a ``turn_compacted`` event that replay
  treats as a splice operation. The original lines stay on disk for
  audits.
* **Dual trigger: turn count OR token volume.** The turn-count path is
  simple and model-independent; the token path catches the case the
  count path misses — few turns, huge content (a single 50k-token tool
  dump would otherwise blow the window long before 20 turns accrue).
  Token counts are a chars//3 estimate (same heuristic as
  ``runtime.memory.hemolymph.estimate_tokens``; kept local so this
  leaf module doesn't pull composer's heavier imports). Callers that
  prefer exact counts can pre-check with their tokenizer and compact
  manually via :func:`compact`.

Call sites:

* After ``turn_completed`` in the runtime: check ``should_compact``
  on the replayed state; if true, build a summary via ``compact``
  and write it with :meth:`EventLog.turn_compacted`.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from runtime.platform.models.primitives import new_id
from runtime.protocol.items import (
    AgentMessageItem,
    CommandExecutionItem,
    ErrorItem,
    FileChangeItem,
    ItemStatus,
    McpToolCallItem,
    PlanItem,
    TodoListItem,
    Turn,
    TurnStatus,
    UserMessageItem,
)

Summariser = Callable[[Sequence[Turn]], str]


@dataclass(frozen=True, slots=True)
class CompactionPolicy:
    """Knobs for when and how much to compact.

    ``keep_recent`` turns stay visible verbatim. Compaction fires when
    EITHER the turn count reaches ``trigger_at`` OR (when
    ``trigger_tokens`` is set) the estimated token volume of the whole
    thread reaches it — whichever comes first. Must satisfy
    ``trigger_at > keep_recent`` for the count path (enforced at use
    time). ``trigger_tokens`` should sit well above the typical token
    volume of the ``keep_recent`` window itself, else the thread
    re-triggers every turn.
    """

    trigger_at: int = 20
    keep_recent: int = 10
    # Token-volume trigger (estimated, chars//3). ``None`` disables the
    # volume path — the historical behaviour. Set by the realtime
    # runtime wiring; sized against the active model's context window
    # minus system prompt / tool output / completion reserves.
    trigger_tokens: int | None = None
    max_summary_chars: int = 4_000
    # Tag that goes into the summary turn's id so callers can recognise
    # compaction artefacts in logs. Appears in the turn id like
    # ``trn_<tag>_<hex>``.
    summary_tag: str = "summary"
    custom_summariser: Summariser | None = field(default=None)


@dataclass(frozen=True, slots=True)
class CompactionResult:
    summary_turn: Turn
    superseded_ids: list[str]


def should_compact(turns: Sequence[Turn], policy: CompactionPolicy) -> bool:
    """Cheap trigger check — safe to call after every turn.

    Fires on turn count OR estimated token volume (when
    ``policy.trigger_tokens`` is set). Both paths share the
    ``len(turns) > keep_recent`` floor: with nothing older than the
    keep window there is nothing to fold, and triggering would burn a
    summariser round-trip for a guaranteed no-op (or oscillate every
    turn if the kept window alone is over budget).
    """
    if len(turns) <= policy.keep_recent:
        return False
    if policy.trigger_at > policy.keep_recent and len(turns) >= policy.trigger_at:
        return True
    if policy.trigger_tokens is not None:
        return estimate_turns_tokens(turns) >= policy.trigger_tokens
    return False


def _estimate_text_tokens(text: str) -> int:
    """Same heuristic as the ReAct loop's ``_estimate_tokens``.

    MUST stay in the same token units as
    ``context_budget_tokens_for_model`` (Chinese ≈ 1.5 chars/token,
    English ≈ 4 chars/token) so the volume trigger compares against a
    budget derived from the same scale. A plain ``chars // 3`` would
    halve CJK-heavy threads and delay compaction past the window.
    """
    cn = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    en = len(text) - cn
    return int(cn / 1.5 + en / 4)


def estimate_turns_tokens(turns: Sequence[Turn]) -> int:
    """Estimate the token volume of ``turns`` (whole-thread context cost).

    Walks every item and accumulates the token estimates of the fields
    that actually ride the context: message texts, reasoning content,
    command + aggregated output, MCP tool results/errors, file diffs,
    plan/todo text, error messages. One pass, no regex, no slicing —
    safe to call after every turn. Never raises on malformed items.
    """
    tokens = 0
    try:
        for turn in turns:
            for item in getattr(turn, "items", None) or ():
                tokens += _estimate_item_tokens(item)
    except Exception:  # noqa: BLE001 — best-effort estimate
        return 0
    return tokens


def _estimate_item_tokens(item: Any) -> int:
    """Token estimate of one item's context-bearing fields.

    Flat accumulation rather than isinstance dispatch: item kinds share
    no common text field (``text`` / ``content`` / ``command`` / ...),
    and ``getattr`` misses on undeclared pydantic fields return
    ``None`` harmlessly, so each probe is a no-op for unrelated kinds.
    """
    tokens = 0
    # UserMessageItem / AgentMessageItem / PlanItem.
    text = getattr(item, "text", None)
    if isinstance(text, str):
        tokens += _estimate_text_tokens(text)
    # ReasoningItem: content + summary lines live in the thread log
    # and inflate replayed context even though the LLM summariser
    # skips them when rendering its transcript.
    content = getattr(item, "content", None)
    if isinstance(content, str):
        tokens += _estimate_text_tokens(content)
    for line in getattr(item, "summary", None) or ():
        if isinstance(line, str):
            tokens += _estimate_text_tokens(line)
    # CommandExecutionItem: command + aggregated_output.
    command = getattr(item, "command", None)
    if isinstance(command, str):
        tokens += _estimate_text_tokens(command)
        output = getattr(item, "aggregated_output", None)
        if isinstance(output, str):
            tokens += _estimate_text_tokens(output)
    # McpToolCallItem: server/tool + error, else the result payload.
    server = getattr(item, "server", None)
    if isinstance(server, str):
        tool = getattr(item, "tool", None)
        tokens += _estimate_text_tokens(server) + (
            _estimate_text_tokens(tool) if isinstance(tool, str) else 0
        )
        error = getattr(item, "error", None)
        if isinstance(error, str):
            tokens += _estimate_text_tokens(error)
        else:
            result = getattr(item, "result", None)
            if result is not None:
                with contextlib.suppress(Exception):  # noqa: BLE001 — exotic payloads
                    tokens += _estimate_text_tokens(str(result))
    # FileChangeItem: diffs dominate — the raw unified diff rides the
    # context for audit/revert even when the UI renders hunks.
    for change in getattr(item, "changes", None) or ():
        path = getattr(change, "path", None)
        if isinstance(path, str):
            tokens += _estimate_text_tokens(path)
        diff = getattr(change, "diff", None)
        if isinstance(diff, str):
            tokens += _estimate_text_tokens(diff)
    # ErrorItem.
    message = getattr(item, "message", None)
    if isinstance(message, str):
        tokens += _estimate_text_tokens(message)
    # TodoListItem: explanation + entry titles.
    explanation = getattr(item, "explanation", None)
    if isinstance(explanation, str):
        tokens += _estimate_text_tokens(explanation)
    for entry in getattr(item, "plan", None) or ():
        title = getattr(entry, "title", None)
        if isinstance(title, str):
            tokens += _estimate_text_tokens(title)
    return tokens


# Fallback trigger when the model can't be resolved (``auto`` routing,
# empty planner, unknown id). Matches ``custom_model_flags``'s
# documented 256k guess for unknown models: compacting early wastes a
# summariser round-trip, compacting late overflows the window.
_UNKNOWN_MODEL_TRIGGER_TOKENS = 230_400  # 256_000 * 0.9


def compaction_trigger_tokens(model: str | None) -> int:
    """Derive ``trigger_tokens`` from ``model``'s advertised context window.

    Reuses the ReAct loop's ``context_budget_tokens_for_model`` so the
    durable thread-log trigger and the hot-path message compression
    share one scale: operator-configured window → models.dev snapshot
    → name heuristics, each already reserving 10% for the next
    response / tool schemas. Models the resolver can't place (``auto``,
    blank, exotic relay ids) fall back to the 256k convention rather
    than the resolver's 25k floor — 25k would fire the summariser on
    nearly every thread.
    """
    try:
        from runtime.core.cerebrum._react_context_helpers import (
            context_budget_tokens_for_model,
        )

        budget = context_budget_tokens_for_model(model)
    except Exception:  # noqa: BLE001 — never block wiring on a probe
        return _UNKNOWN_MODEL_TRIGGER_TOKENS
    if budget <= 25_000:
        return _UNKNOWN_MODEL_TRIGGER_TOKENS
    return budget


def compact(
    thread_id: str,
    turns: Sequence[Turn],
    policy: CompactionPolicy,
) -> CompactionResult | None:
    """Compute the summary turn for ``turns`` under ``policy``.

    Returns ``None`` when no compaction should happen (policy disabled
    or too few turns). Caller persists via
    :meth:`EventLog.turn_compacted`.
    """
    if not should_compact(turns, policy):
        return None
    cutoff = len(turns) - policy.keep_recent
    if cutoff <= 0:
        return None
    stale = list(turns[:cutoff])
    if not stale:
        return None

    summariser = policy.custom_summariser or _default_summariser
    text = summariser(stale)

    # Sticky-facts preservation (echo optimisation §28).
    # Compaction otherwise smushes user-stated constraints into a
    # single "Previously..." paragraph and the model can lose them.
    # Extract a short bullet list of explicit constraints / prefs /
    # decisions and prepend them as a never-summarised block.
    sticky = extract_sticky_facts(stale)
    if sticky:
        sticky_block = (
            "<sticky-facts>\n"
            + "\n".join(f"- {fact}" for fact in sticky)
            + "\n</sticky-facts>\n\n<summary>\n"
            + text
            + "\n</summary>"
        )
        text = sticky_block

    if len(text) > policy.max_summary_chars:
        text = text[: policy.max_summary_chars - 1].rstrip() + "…"

    summary_id = f"trn_{policy.summary_tag}_{new_id().hex[:12]}"
    summary_item = AgentMessageItem(
        text=text,
        status=ItemStatus.COMPLETED,
    )
    started_at = _earliest_started_at(stale) or stale[0].started_at
    completed_at = _latest_completed_at(stale) or stale[-1].started_at
    summary_turn = Turn(
        id=summary_id,
        threadId=thread_id,
        status=TurnStatus.COMPLETED,
        startedAt=started_at,
        completedAt=completed_at,
        items=[summary_item],
    )
    return CompactionResult(
        summary_turn=summary_turn,
        superseded_ids=[t.id for t in stale],
    )


# ── Default mechanical summariser ─────────────────────────────


# Heuristic patterns for "sticky" user-stated constraints / prefs /
# decisions that survive compaction. The detector is intentionally
# conservative: false negatives (missing a real constraint) are
# tolerable because the LLM summary still captures most context;
# false positives (treating a question as a fact) are NOT tolerable
# because they teach the agent the wrong constraint.
_STICKY_FACT_PATTERNS: tuple[tuple[str, str], ...] = (
    # (label, regex) — label is what we tag the fact with, regex must
    # match the trigger phrase verbatim within the message
    ("constraint", r"(?:必须|一定要|强制|要求|must|always|please always)"),
    ("ban", r"(?:不要|别|禁止|don'?t|never|please never|do not)"),
    ("preference", r"(?:我喜欢|我习惯|我偏好|i prefer|by convention)"),
    ("usage", r"(?:用 [A-Za-z][\w\-]+|使用 [A-Za-z][\w\-]+|we use|we are using)"),
    ("identity", r"(?:项目名|项目叫|project (?:is|name)|我们(?:的)?项目)"),
)
_STICKY_QUESTION_RE = re.compile(r"[?？]\s*$|是否|应该|可以.*吗|能否")
_STICKY_FACTS_MAX = 12


def extract_sticky_facts(turns: Sequence[Turn] | Sequence[Mapping[str, Any]]) -> list[str]:
    """Extract user-stated sticky facts from old turns / messages.

    Accepts either a sequence of ``Turn`` objects (compaction's normal
    input) OR a sequence of dict-shaped messages (testing convenience):
    ``[{"role": "user", "content": "..."}, ...]``. Other types are
    ignored gracefully.

    Returns at most ``_STICKY_FACTS_MAX`` deduplicated short strings.
    Best-effort — never raises, returns ``[]`` on any internal error.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _consider(text: str) -> None:
        if not isinstance(text, str):
            return
        msg = text.strip()
        if len(msg) < 8:
            return
        if _STICKY_QUESTION_RE.search(msg):
            return
        for label, pat in _STICKY_FACT_PATTERNS:
            if re.search(pat, msg, re.IGNORECASE):
                # Trim to one sentence-ish — pick the first 80 chars
                # of the line containing the trigger, so we don't
                # carry across multi-sentence diatribes.
                excerpt = msg.split("\n", 1)[0][:120]
                key = excerpt.lower()
                if key in seen:
                    return
                seen.add(key)
                out.append(f"[{label}] {excerpt}")
                return

    try:
        for entry in turns:
            # dict-shaped message
            if isinstance(entry, Mapping):
                if entry.get("role") != "user":
                    continue
                _consider(str(entry.get("content") or ""))
                if len(out) >= _STICKY_FACTS_MAX:
                    break
                continue
            # Turn-shaped (compaction's real input)
            try:
                items = getattr(entry, "items", None)
                if not items:
                    continue
                for it in items:
                    if isinstance(it, UserMessageItem):
                        _consider(it.text or "")
                        if len(out) >= _STICKY_FACTS_MAX:
                            break
            except Exception:  # noqa: BLE001 — best-effort
                continue
            if len(out) >= _STICKY_FACTS_MAX:
                break
    except Exception:  # noqa: BLE001 — best-effort
        return out

    return out


def _default_summariser(turns: Sequence[Turn]) -> str:
    """Build a single-paragraph summary per turn, then concatenate.

    Format::

        [turn 1/3 · completed] user: build the login page
          assistant: added ... 2 file changes, 1 command
        [turn 2/3 · completed] ...

    No LLM. Purely a structural overview so the next turn still knows
    what happened earlier. Users who need semantic compression supply a
    ``custom_summariser`` that runs an LLM.
    """
    lines: list[str] = []
    for idx, turn in enumerate(turns, start=1):
        header = f"[turn {idx}/{len(turns)} · {turn.status.value}]"
        user_line = _first_user_text(turn)
        if user_line:
            header = f"{header} user: {_clip(user_line, 160)}"
        lines.append(header)
        agent_line = _first_agent_text(turn)
        command_count = sum(1 for it in turn.items if isinstance(it, CommandExecutionItem))
        mcp_count = sum(1 for it in turn.items if isinstance(it, McpToolCallItem))
        file_count = sum(1 for it in turn.items if isinstance(it, FileChangeItem))
        plan_count = sum(1 for it in turn.items if isinstance(it, PlanItem))
        todo_count = sum(1 for it in turn.items if isinstance(it, TodoListItem))
        error_line = _first_error_text(turn)
        summary_bits: list[str] = []
        if agent_line:
            summary_bits.append(_clip(agent_line, 200))
        if error_line:
            summary_bits.append(f"error: {_clip(error_line, 160)}")
        if command_count:
            summary_bits.append(f"{command_count} command(s)")
        if mcp_count:
            summary_bits.append(f"{mcp_count} MCP tool call(s)")
        if file_count:
            summary_bits.append(f"{file_count} file change(s)")
        if plan_count:
            summary_bits.append(f"{plan_count} plan item(s)")
        if todo_count:
            summary_bits.append(f"{todo_count} todo list(s)")
        if summary_bits:
            lines.append("  assistant: " + " · ".join(summary_bits))
    return "\n".join(lines)


def _first_user_text(turn: Turn) -> str | None:
    for it in turn.items:
        if isinstance(it, UserMessageItem):
            return it.text
    return None


def _first_agent_text(turn: Turn) -> str | None:
    for it in turn.items:
        if isinstance(it, AgentMessageItem) and it.text:
            return it.text
    return None


def _first_error_text(turn: Turn) -> str | None:
    for it in turn.items:
        if isinstance(it, ErrorItem) and it.message:
            return it.message
    return None


def _clip(text: str, n: int) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _earliest_started_at(turns: Sequence[Turn]) -> datetime | None:
    stamps = [t.started_at for t in turns if t.started_at is not None]
    return min(stamps) if stamps else None


def _latest_completed_at(turns: Sequence[Turn]) -> datetime | None:
    stamps = [t.completed_at for t in turns if t.completed_at is not None]
    if not stamps:
        stamps = [t.started_at for t in turns if t.started_at is not None]
    return max(stamps) if stamps else None


__all__ = [
    "CompactionPolicy",
    "CompactionResult",
    "Summariser",
    "compact",
    "should_compact",
]


# mypy helper: keep ``cast`` import used in case future variants need it.
_ = cast  # noqa: F841
