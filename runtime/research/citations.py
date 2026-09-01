"""Citation-ready context assembly · Perplexity-style.

Turns a list of fetched sources into the numbered `[1] ... [2] ...` block
that `prompts/research_with_citations.yaml` consumes, plus helpers for
rendering the final answer back to the caller.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from runtime.platform.prompts import get_prompt

_CITE_RE = re.compile(r"\[(\d+)\]")


@dataclass(slots=True)
class SourceEntry:
    """A single fetched source, ready to be cited."""

    url: str
    title: str = ""
    snippet: str = ""
    content: str = ""
    published: str = ""
    author: str = ""

    def body(self, max_chars: int) -> str:
        """Best available text for this source, capped."""
        body = self.content or self.snippet or ""
        body = body.strip()
        if len(body) > max_chars:
            body = body[: max_chars - 1].rstrip() + "…"
        return body


def build_citation_context(
    sources: Iterable[SourceEntry | dict],
    *,
    max_chars_per_source: int = 1200,
    max_sources: int = 10,
) -> tuple[str, list[SourceEntry]]:
    """Render sources into a numbered block and return the rendered block
    alongside the (normalized, capped, deduped) list.

    The returned list's index `i` maps to citation marker `[i+1]`.
    """
    normalized: list[SourceEntry] = []
    seen: set[str] = set()
    for raw in sources:
        entry = _coerce(raw)
        key = entry.url.strip()
        if not key or key in seen:
            continue
        if not (entry.title or entry.snippet or entry.content):
            continue
        seen.add(key)
        normalized.append(entry)
        if len(normalized) >= max_sources:
            break

    if not normalized:
        return "(no sources)", []

    lines: list[str] = []
    for i, src in enumerate(normalized, start=1):
        header = f"[{i}] {src.title or src.url}"
        meta_bits = [src.url]
        if src.published:
            meta_bits.append(f"published: {src.published}")
        if src.author:
            meta_bits.append(f"author: {src.author}")
        lines.append(header)
        lines.append("    " + " · ".join(meta_bits))
        body = src.body(max_chars_per_source)
        if body:
            lines.append("    " + body.replace("\n", "\n    "))
        lines.append("")
    return "\n".join(lines).rstrip(), normalized


def render_citation_prompt(
    question: str,
    sources: Iterable[SourceEntry | dict],
    *,
    max_chars_per_source: int = 1200,
    max_sources: int = 10,
    today: date | None = None,
) -> tuple[str, list[SourceEntry]]:
    """Load `research_with_citations` template and fill it in.

    Returns (filled_prompt, normalized_sources) — keep the source list so
    the caller can later resolve `[n]` markers back to URLs.
    """
    block, normalized = build_citation_context(
        sources,
        max_chars_per_source=max_chars_per_source,
        max_sources=max_sources,
    )
    template = get_prompt("research_with_citations")
    today_str = (today or date.today()).isoformat()
    return (
        template.replace("{question}", question)
        .replace("{sources}", block)
        .replace("{date}", today_str),
        normalized,
    )


@dataclass(slots=True)
class CitationResolution:
    """Result of resolving `[n]` markers in a generated answer."""

    answer: str
    used_indices: list[int] = field(default_factory=list)
    used_sources: list[SourceEntry] = field(default_factory=list)
    invalid_indices: list[int] = field(default_factory=list)


def resolve_citations(
    answer: str,
    sources: list[SourceEntry],
) -> CitationResolution:
    """Scan an answer for `[n]` markers and report which sources were cited.

    Indices out of range (e.g. `[99]` when only 5 sources exist) are flagged
    in `invalid_indices` so the caller can decide whether to retry the LLM.
    """
    used: list[int] = []
    invalid: list[int] = []
    for m in _CITE_RE.finditer(answer):
        idx = int(m.group(1))
        if 1 <= idx <= len(sources):
            if idx not in used:
                used.append(idx)
        else:
            if idx not in invalid:
                invalid.append(idx)
    used_sources = [sources[i - 1] for i in used]
    return CitationResolution(
        answer=answer,
        used_indices=used,
        used_sources=used_sources,
        invalid_indices=invalid,
    )


def _coerce(raw: SourceEntry | dict) -> SourceEntry:
    if isinstance(raw, SourceEntry):
        return raw
    meta = raw.get("metadata") or {}
    return SourceEntry(
        url=str(raw.get("url") or ""),
        title=str(raw.get("title") or meta.get("title") or ""),
        snippet=str(raw.get("snippet") or ""),
        content=str(raw.get("content") or ""),
        published=str(raw.get("published") or meta.get("date") or ""),
        author=str(raw.get("author") or meta.get("author") or ""),
    )


__all__ = [
    "SourceEntry",
    "CitationResolution",
    "build_citation_context",
    "render_citation_prompt",
    "resolve_citations",
]
