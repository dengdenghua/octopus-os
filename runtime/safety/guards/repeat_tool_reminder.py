"""Advisory per-agent repeat-call detector (dsh ``repeat-tool-reminder``).

Port of ``@deepseek-ai/dsh-repeat-tool-reminder`` (MIT, 2026-08-13).
The guard is **not** a model-facing tool: it never appears in the tool
list, never vetoes or rewrites a call, and adds exactly one behavior —
it watches each agent's stream of tool calls, counts runs of consecutive
calls to the same tool with identical canonicalized arguments, and at
configured run lengths injects an escalating advisory reminder telling
the model to stop repeating itself, re-read the last result, and either
change approach or conclude. The decision (retry differently, gather
more evidence, or finish) stays entirely with the model: a legitimately
repeated call is delayed by nothing and blocked by nothing.

Chain semantics (verbatim from dsh):

- The chain key is ``(tool name, canonical arguments)`` — canonicalization
  is a deep key-sort plus ``JSON.stringify``, so argument objects differing
  only in property order count as identical.
- **Untracked calls are transparent**: a call excluded by ``include`` /
  ``exclude`` neither increments nor resets the counter.
- **Denied calls count**: detection sits where denied/rejected calls also
  flow, so a model hammering a denied call is exactly the loop worth
  breaking.
- **Per-agent keying**: each loop owns one guard instance (one chain per
  agent), so one agent's repetition never trips another's reminder; a user
  interjection resets the submitting agent's chain.
- **In-memory only**: a turn resumed from persistence starts with a fresh
  chain — the guard is a heuristic nudge, not a logged invariant.

The reminder rides the model context as a synthetic user-role message
marked ``[REPEAT-CALL REMINDER]`` (our ``Message`` model carries no source
field; the marker keeps the model from reading it as a real user prompt).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger(__name__)

GENTLE_REMINDER = (
    "[REPEAT-CALL REMINDER] You are repeating the exact same tool call with "
    "identical arguments. Carefully analyze the previous result before calling "
    "again: if the task is not complete, try a different approach or different "
    "arguments instead of repeating the call."
)


def detailed_reminder(tool_name: str, count: int, canonical_arguments: str) -> str:
    """The detailed later-threshold reminder (dsh template, plus marker)."""

    return (
        "[REPEAT-CALL REMINDER] Repeated tool call detected:\n"
        f"- tool: {tool_name}\n"
        f"- consecutive_calls: {count}\n"
        f"- arguments: {canonical_arguments}\n"
        "The repeated calls are not making progress. Do not call this tool with "
        "these exact arguments again. Inspect the latest result and choose a "
        "different action, different arguments, or finish the task if enough "
        "evidence has been gathered."
    )


@dataclass(frozen=True)
class RepeatToolReminderConfig:
    """Guard config. ``thresholds`` must be non-empty integers >= 2 without
    duplicates (fail-loud via :meth:`from_mapping`); ``arguments_preview_chars``
    must be an integer >= 1."""

    thresholds: tuple[int, ...] = (3, 5, 8)
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    arguments_preview_chars: int = 500

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> RepeatToolReminderConfig:
        """Build from a config dict, fail-loud on invalid values.

        Raises ``ValueError`` (never a silent fall-back) — the caller decides
        whether a client-supplied config should degrade instead.
        """
        thresholds = raw.get("thresholds", (3, 5, 8))
        if not isinstance(thresholds, (list, tuple)):
            raise ValueError("repeat_tool_reminder: `thresholds` must be a list of integers")
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in thresholds):
            raise ValueError("repeat_tool_reminder: `thresholds` must be a list of integers")
        validated = validate_thresholds(list(thresholds))

        include = raw.get("include", ())
        exclude = raw.get("exclude", ())
        for field_name, values in (("include", include), ("exclude", exclude)):
            if not isinstance(values, (list, tuple)) or not all(
                isinstance(value, str) for value in values
            ):
                raise ValueError(
                    f"repeat_tool_reminder: `{field_name}` must be a list of wildcard strings"
                )

        preview_chars = raw.get("arguments_preview_chars", 500)
        if isinstance(preview_chars, bool) or not isinstance(preview_chars, int):
            raise ValueError(
                "repeat_tool_reminder: invalid arguments_preview_chars "
                f"{preview_chars!r} — must be an integer >= 1"
            )
        if preview_chars < 1:
            raise ValueError(
                "repeat_tool_reminder: invalid arguments_preview_chars "
                f"{preview_chars} — must be an integer >= 1"
            )

        return cls(
            thresholds=tuple(validated),
            include=tuple(include),
            exclude=tuple(exclude),
            arguments_preview_chars=preview_chars,
        )


def validate_thresholds(values: list[int]) -> list[int]:
    """Validate ``thresholds`` per the fail-loud contract and return them
    sorted ascending (the escalation rule reads ``thresholds[0]`` as the
    gentle tier, so order is normalized once)."""

    if not values:
        raise ValueError("repeat_tool_reminder: `thresholds` must not be empty")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value < 2:
            raise ValueError(
                f"repeat_tool_reminder: invalid threshold {value!r} — every "
                "threshold must be an integer >= 2"
            )
    if len(set(values)) != len(values):
        raise ValueError("repeat_tool_reminder: `thresholds` must not contain duplicates")
    return sorted(values)


def _sort_json_value(value: Any) -> Any:
    """Deep key-sort of a JSON value so two argument objects that differ only
    in property order canonicalize identically (dsh ``sortJsonValue``)."""

    if isinstance(value, list):
        return [_sort_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sort_json_value(value[key]) for key in sorted(value)}
    return value


def canonicalize(arguments: Any) -> str:
    """Canonical string form of a call's arguments: deep key-sort, then
    stringify (dsh ``canonicalize``). Non-JSON leaves degrade via ``str``."""

    return json.dumps(
        _sort_json_value(arguments),
        ensure_ascii=False,
        default=str,
    )


def wildcard_to_regexp(pattern: str) -> re.Pattern[str]:
    """Compile one ``*``-wildcard pattern to an anchored RegExp; every other
    regex metacharacter is matched literally (dsh ``wildcardToRegExp``)."""

    escaped = re.escape(pattern).replace(r"\*", ".*")
    return re.compile(f"^{escaped}$")


def preview_arguments(canonical: str, cap: int) -> str:
    """Head-truncate the canonical arguments for quoting in the detailed
    reminder, marking how much was omitted. Bounds only the model-visible
    text — the chain key always uses the full canonical string."""

    if len(canonical) <= cap:
        return canonical
    return f"{canonical[:cap]}… (+{len(canonical) - cap} more chars)"


class _Chain:
    __slots__ = ("key", "count")

    def __init__(self, key: str, count: int) -> None:
        self.key = key
        self.count = count


class RepeatToolReminderGuard:
    """One guard instance per running agent loop (per turn).

    Instantiate via :func:`build_repeat_tool_reminder` — it wires the
    config source (per-turn ``user_context`` + env kill-switch) and never
    raises into the turn.
    """

    def __init__(self, config: RepeatToolReminderConfig) -> None:
        self._config = config
        self._thresholds = validate_thresholds(list(config.thresholds))
        self._threshold_set = set(self._thresholds)
        self._include_patterns = [wildcard_to_regexp(p) for p in config.include]
        self._exclude_patterns = [wildcard_to_regexp(p) for p in config.exclude]
        self._chains: dict[str, _Chain] = {}

    def _tracked(self, tool_name: str) -> bool:
        """Whether a tool participates in the chain (untracked calls are
        transparent: they neither count nor reset)."""

        if self._include_patterns and not any(
            pattern.match(tool_name) for pattern in self._include_patterns
        ):
            return False
        return not any(pattern.match(tool_name) for pattern in self._exclude_patterns)

    def observe(self, tool_name: str, arguments: Any, agent_key: str = "default") -> str | None:
        """Advance the agent's chain for one attempted call and return the
        reminder to deliver, if this attempt's run length hits a configured
        threshold."""

        if not self._tracked(tool_name):
            return None
        canonical = canonicalize(arguments)
        key = json.dumps([tool_name, canonical], ensure_ascii=False)
        chain = self._chains.get(agent_key)
        count = chain.count + 1 if chain is not None and chain.key == key else 1
        self._chains[agent_key] = _Chain(key=key, count=count)
        if count not in self._threshold_set:
            return None
        if count == self._thresholds[0]:
            return GENTLE_REMINDER
        return detailed_reminder(
            tool_name,
            count,
            preview_arguments(canonical, self._config.arguments_preview_chars),
        )

    def reset(self, agent_key: str = "default") -> None:
        """Drop the agent's chain (a user interjection changes the context;
        repetition across it is not a loop)."""

        self._chains.pop(agent_key, None)

    @property
    def chain_counts(self) -> dict[str, int]:
        """Current per-agent run lengths (test/introspection surface)."""

        return {key: chain.count for key, chain in self._chains.items()}


def build_repeat_tool_reminder(
    user_context: dict[str, Any] | None,
) -> RepeatToolReminderGuard | None:
    """Build the guard from a per-turn ``user_context``, or ``None`` when
    disabled.

    Enable/disable:
    - ``user_context["repeat_tool_reminder"]["enabled"]`` — explicit ``false``
      disables (default on, mirroring dsh loading the plugin).
    - ``ECHO_REPEAT_TOOL_REMINDER=0`` — server-side kill-switch.

    Unlike dsh (which fail-louds at plugin load on deployment-owned config),
    a malformed *client-supplied* config logs a warning and degrades to
    defaults — never crashes the turn, never silently disables the guard.
    """

    raw = (user_context or {}).get("repeat_tool_reminder")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        _logger.warning("repeat_tool_reminder config ignored: not an object")
        return None
    if str(raw.get("enabled", True)).strip().lower() in {"0", "false", "off", "no"}:
        return None
    env = os.environ.get("ECHO_REPEAT_TOOL_REMINDER", "1").strip().lower()
    if env in {"0", "false", "off", "no"}:
        return None
    try:
        config = RepeatToolReminderConfig.from_mapping(raw)
    except (TypeError, ValueError) as exc:
        _logger.warning("repeat_tool_reminder config invalid (%s); using defaults", exc)
        config = RepeatToolReminderConfig()
    return RepeatToolReminderGuard(config)
