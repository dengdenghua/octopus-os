"""Parse @plugin/@skill/@agent and runtime surface mentions from prompts.

The chat input box's mention autocomplete (frontend/src/components/
workspace/mention-autocomplete.tsx) inserts tokens like::

    @plugin:web-search
    @skill:deep-research
    @agent:researcher_v1
    @Browser

These survive the wire round-trip into ReAct's user goal text. Rather
than relying on the model to pick them up by accident, this module
extracts them up front so the runtime can:

1.  Boost the matching skill / capability into the priority list.
2.  Pre-resolve the agent for delegation tools.
3.  Pre-load the plugin if it's not yet active.

The parser is deliberately permissive: malformed tokens are ignored,
and the original text is preserved for the model so it still sees the
human-readable phrasing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Token shape: @<type>:<id>  where id may contain letters, digits,
# hyphens, underscores, slashes, dots. The trailing boundary is any
# whitespace, punctuation, or end-of-string.
_MENTION_RE = re.compile(
    r"@(?P<type>plugin|skill|agent|pack):(?P<id>[A-Za-z0-9][A-Za-z0-9._/\-]*)",
)

# Codex / connector mentions can arrive as Markdown links such as
# ``[@product-design](plugin://product-design@openai-curated-remote)``.
# Treat the URI as the same strong routing signal as ``@plugin:...``.
_PLUGIN_URI_RE = re.compile(
    r"plugin://(?P<id>[A-Za-z0-9][A-Za-z0-9._/\-]*?)"
    r"(?:@[A-Za-z0-9][A-Za-z0-9._/\-]*)?"
    r"(?=$|[\s)\]}>.,;:!?\"'])",
)

_SURFACE_RE = re.compile(
    r"@(?P<id>browser|chrome|computer)\b",
    re.IGNORECASE,
)

_VALID_TYPES = ("plugin", "skill", "agent", "pack")
_VALID_SURFACES = {
    "browser": "browser",
    "chrome": "chrome",
    "computer": "computer",
}


@dataclass(frozen=True)
class InputMention:
    """A single @-mention extracted from user input."""

    type: str  # one of "plugin" | "skill" | "agent" | "pack" | "surface"
    id: str
    raw: str  # the matched token, e.g. "@skill:deep-research"
    span: tuple[int, int]  # (start, end) char offsets in the source text


@dataclass(frozen=True)
class InputMentions:
    """Container holding the mentions found in a single prompt."""

    plugins: tuple[str, ...]
    skills: tuple[str, ...]
    agents: tuple[str, ...]
    packs: tuple[str, ...]
    raw_mentions: tuple[InputMention, ...]
    surfaces: tuple[str, ...] = ()

    @property
    def has_any(self) -> bool:
        return bool(
            self.plugins or self.skills or self.agents or self.packs or self.surfaces,
        )

    def render_hint(self) -> str:
        """Render a system-prompt fragment describing the mentions.

        The fragment is wrapped in a sentinel tag so the model can
        recognize it as a routing hint, not free-form chat.
        """
        if not self.has_any:
            return ""
        lines: list[str] = ["<input-mentions>"]
        if self.skills:
            lines.append(
                "User pinned these skills via @skill: "
                + ", ".join(f"`{name}`" for name in self.skills)
                + ". Prefer them when they match the next concrete action.",
            )
        if self.packs:
            lines.append(
                "User pinned these skill packs via @pack: "
                + ", ".join(f"`{name}`" for name in self.packs)
                + ". Treat them as a bundle — when the next step needs any "
                "of the pack's contents, prefer that pack as a whole.",
            )
        if self.plugins:
            lines.append(
                "User pinned these plugins via @plugin: "
                + ", ".join(f"`{name}`" for name in self.plugins)
                + ". Treat this as an explicit routing request: use "
                "`query_capability` / `use_capability` for the pinned plugin "
                "before lower-level tools unless it is unavailable or clearly "
                "irrelevant.",
            )
        if self.agents:
            lines.append(
                "User pinned these teammates via @agent: "
                + ", ".join(f"`{name}`" for name in self.agents)
                + ". When delegation is appropriate, route to these "
                "agents via `call_agent` / `call_agent_parallel` first.",
            )
        if self.surfaces:
            lines.append(
                "User invoked these runtime surfaces via @Surface: "
                + ", ".join(f"`{name}`" for name in self.surfaces)
                + ". Treat this as an explicit request to use that surface "
                "when it fits the next action.",
            )
        lines.append(
            "These pins are strong routing preferences. If a pinned "
            "capability cannot be used, say why before falling back.",
        )
        lines.append("</input-mentions>")
        return "\n".join(lines)


def parse_input_mentions(text: str) -> InputMentions:
    """Extract typed mentions and known runtime surface mentions.

    Duplicates within a single bucket are removed while preserving
    first-seen order. Mentions in code fences or inline code spans are
    NOT excluded — those formatting rules belong to markdown, not user
    intent. If a user pastes ``@skill:foo`` inside backticks they
    probably still want it to count.

    Returns an empty `InputMentions` when no mentions are found.
    """
    if not text:
        return InputMentions((), (), (), (), ())

    raw: list[InputMention] = []
    plugins: list[str] = []
    skills: list[str] = []
    agents: list[str] = []
    packs: list[str] = []
    surfaces: list[str] = []
    seen_per_bucket: dict[str, set[str]] = {
        "plugin": set(),
        "skill": set(),
        "agent": set(),
        "pack": set(),
    }

    def record(kind: str, ident: str, raw_text: str, span: tuple[int, int]) -> bool:
        if kind not in _VALID_TYPES:
            return False
        if not ident or ident in seen_per_bucket[kind]:
            return False
        seen_per_bucket[kind].add(ident)
        raw.append(
            InputMention(
                type=kind,
                id=ident,
                raw=raw_text,
                span=span,
            ),
        )
        if kind == "plugin":
            plugins.append(ident)
        elif kind == "skill":
            skills.append(ident)
        elif kind == "pack":
            packs.append(ident)
        else:
            agents.append(ident)
        return True

    for match in _MENTION_RE.finditer(text):
        kind = match.group("type")
        ident = match.group("id")
        record(kind, ident, match.group(0), (match.start(), match.end()))

    for match in _PLUGIN_URI_RE.finditer(text):
        record(
            "plugin",
            match.group("id"),
            match.group(0),
            (match.start(), match.end()),
        )

    seen_surfaces: set[str] = set()
    occupied_spans = [mention.span for mention in raw]
    for match in _SURFACE_RE.finditer(text):
        if any(start <= match.start() < end for start, end in occupied_spans):
            continue
        normalized = _VALID_SURFACES.get(match.group("id").lower())
        if not normalized or normalized in seen_surfaces:
            continue
        seen_surfaces.add(normalized)
        surfaces.append(normalized)
        raw.append(
            InputMention(
                type="surface",
                id=normalized,
                raw=match.group(0),
                span=(match.start(), match.end()),
            ),
        )

    return InputMentions(
        plugins=tuple(plugins),
        skills=tuple(skills),
        agents=tuple(agents),
        packs=tuple(packs),
        raw_mentions=tuple(raw),
        surfaces=tuple(surfaces),
    )


__all__ = [
    "InputMention",
    "InputMentions",
    "parse_input_mentions",
]
