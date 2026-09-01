"""Cache-stable prompt builder.

The goal is to keep the system prompt **prefix-cacheable**: the same
turn shape (mode, workspace, agent) produces a byte-equal system
prompt across turns, so the LLM provider's prompt cache can hit
on every turn after the first.

Two levers:

1. **Stable parts** are conditional but byte-stable: they depend on
   slow-moving inputs (mode, workspace path, project rules file
   content). Two consecutive turns in the same context produce the
   same stable prompt.

2. **Volatile parts** depend on per-turn inputs (current date,
   user's output_style choice, camouflage A/B suffix, memory recall
   results, thinking_guidance). Adding these to the system prompt
   would break the cache prefix on EVERY turn — so we route them
   to a synthetic prepended user message instead, where they don't
   poison the cache.

The contract:

    builder = StablePromptBuilder()
    builder.add_stable("base", REACT_SYSTEM_PROMPT_BASE)
    builder.add_stable("workspace", workspace_block)
    builder.add_volatile("date", today_block)
    builder.add_volatile("output_style", style_block)

    system_text, prepended_user_text = builder.compose()
    messages = [
        Message(role="system", content=system_text),
        # Prepended user message — only when there's volatile content
        Message(role="user", content=prepended_user_text),
        # ... rest of conversation
    ]

The provider's prompt cache then hits on ``system_text`` because
that block is byte-equal across turns of the same shape. The
volatile bits cost their full input tokens once per turn, which is
correct — they're per-turn signals.

A debug helper (``compute_stable_hash``) returns a hash of the
stable section so tests can pin the byte-stability invariant.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class StablePromptBuilder:
    """Compose a system prompt with explicit cache-stability separation.

    Each ``add_stable`` / ``add_volatile`` call records a labelled
    section. ``compose`` returns ``(system_text, volatile_text)``:

      * ``system_text`` — newline-joined stable sections in
        insertion order. Pure function of the inputs declared
        stable.
      * ``volatile_text`` — newline-joined volatile sections.
        Empty string when no volatile sections were added.

    The label argument is for diagnostics only; ordering follows
    insertion order so two callers with the same inputs produce the
    same output.
    """

    _stable: list[tuple[str, str]] = field(default_factory=list)
    _volatile: list[tuple[str, str]] = field(default_factory=list)

    def add_stable(self, label: str, text: str) -> None:
        """Append a section that's byte-equal across turns of the
        same shape (mode + workspace + agent + project_rules).
        """
        if not isinstance(text, str) or not text.strip():
            return
        self._stable.append((label, text))

    def add_volatile(self, label: str, text: str) -> None:
        """Append a section that varies per-turn (date, recall,
        camouflage, output_style, thinking).

        Routed to a prepended user message so it doesn't poison
        the cache prefix.
        """
        if not isinstance(text, str) or not text.strip():
            return
        self._volatile.append((label, text))

    def insert_stable_first(self, label: str, text: str) -> None:
        """Insert at the front of the stable section.

        Used by the SOUL injection path which historically prepended.
        Kept as a separate method so callers can be explicit about
        ordering — random `insert(0, ...)` calls would break the
        cache invariant.
        """
        if not isinstance(text, str) or not text.strip():
            return
        self._stable.insert(0, (label, text))

    def stable_labels(self) -> list[str]:
        return [label for label, _ in self._stable]

    def volatile_labels(self) -> list[str]:
        return [label for label, _ in self._volatile]

    def compose(self) -> tuple[str, str]:
        """Return ``(system_text, volatile_text)``.

        ``volatile_text`` is the empty string when no volatile
        sections were added — caller can skip the prepended user
        message in that case.
        """
        system_text = "\n\n".join(text for _, text in self._stable)
        volatile_text = "\n\n".join(text for _, text in self._volatile)
        return system_text, volatile_text

    def compute_stable_hash(self) -> str:
        """SHA-256 of the stable section's bytes (UTF-8).

        Tests assert that two builders with the same stable inputs
        produce the same hash — that's the byte-stability invariant
        the cache depends on.
        """
        system_text, _ = self.compose()
        return hashlib.sha256(system_text.encode("utf-8")).hexdigest()


def render_volatile_as_user_message(volatile_text: str) -> str:
    """Wrap volatile content for injection as a synthetic user
    message before the real conversation starts.

    The wrapping is fixed text (also stable) so the only varying
    bytes are the volatile content itself — and that lives in a
    user message which doesn't sit inside the cached prefix.
    """
    if not volatile_text or not volatile_text.strip():
        return ""
    return (
        "<turn-context>\n"
        "These are per-turn signals (date, user preferences, recall, "
        "etc.). They are NOT part of your stable system prompt; they "
        "change between turns. Use them as additional context for "
        "this turn only.\n\n"
        f"{volatile_text}\n"
        "</turn-context>"
    )


__all__ = [
    "StablePromptBuilder",
    "render_volatile_as_user_message",
]
