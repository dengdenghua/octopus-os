"""Tests for the citation-ready context builder + prompt integration."""

from __future__ import annotations

from datetime import date

from runtime.research.citations import (
    SourceEntry,
    build_citation_context,
    render_citation_prompt,
    resolve_citations,
)

# ═══════════════════════════════════════════════════════════
# build_citation_context
# ═══════════════════════════════════════════════════════════


class TestBuildCitationContext:
    def test_numbers_sources_from_one(self):
        sources = [
            SourceEntry(url="https://a.example/1", title="A", snippet="first"),
            SourceEntry(url="https://b.example/2", title="B", snippet="second"),
        ]
        block, norm = build_citation_context(sources)
        assert len(norm) == 2
        assert "[1] A" in block
        assert "[2] B" in block
        assert "https://a.example/1" in block
        assert "https://b.example/2" in block

    def test_accepts_dict_input(self):
        sources = [
            {
                "url": "https://x.example/",
                "title": "X",
                "content": "body text",
                "metadata": {"date": "2026-01-01"},
            },
        ]
        block, norm = build_citation_context(sources)
        assert len(norm) == 1
        assert norm[0].title == "X"
        assert norm[0].published == "2026-01-01"
        assert "published: 2026-01-01" in block

    def test_dedupes_by_url(self):
        sources = [
            SourceEntry(url="https://dup.example/", title="First", snippet="a"),
            SourceEntry(url="https://dup.example/", title="Again", snippet="b"),
        ]
        _, norm = build_citation_context(sources)
        assert len(norm) == 1
        assert norm[0].title == "First"

    def test_caps_body_length(self):
        big = "x" * 5000
        sources = [SourceEntry(url="https://big.example/", title="Big", content=big)]
        block, _ = build_citation_context(sources, max_chars_per_source=100)
        # body should be truncated to ~100 chars including ellipsis
        assert "x" * 5000 not in block
        # count x's inside the block
        assert block.count("x") <= 100

    def test_caps_number_of_sources(self):
        sources = [
            SourceEntry(url=f"https://s.example/{i}", title=f"T{i}", snippet="s") for i in range(20)
        ]
        _, norm = build_citation_context(sources, max_sources=5)
        assert len(norm) == 5

    def test_skips_empty_sources(self):
        sources = [
            SourceEntry(url="https://empty.example/"),  # no title/snippet/content
            SourceEntry(url="https://good.example/", title="Good"),
        ]
        _, norm = build_citation_context(sources)
        assert len(norm) == 1
        assert norm[0].url == "https://good.example/"

    def test_no_sources_returns_placeholder(self):
        block, norm = build_citation_context([])
        assert norm == []
        assert "no sources" in block.lower()


# ═══════════════════════════════════════════════════════════
# render_citation_prompt
# ═══════════════════════════════════════════════════════════


class TestRenderCitationPrompt:
    def test_fills_question_sources_and_date(self):
        sources = [
            SourceEntry(url="https://a.example/", title="Alpha", snippet="foo"),
        ]
        prompt, norm = render_citation_prompt("What is foo?", sources, today=date(2026, 5, 9))
        assert "What is foo?" in prompt
        assert "[1] Alpha" in prompt
        assert "2026-05-09" in prompt
        assert len(norm) == 1

    def test_contains_core_instructions(self):
        prompt, _ = render_citation_prompt(
            "Q",
            [SourceEntry(url="https://x/", title="X", snippet="y")],
        )
        # core policies from the template
        assert "[n]" in prompt
        assert "don't cover this" in prompt.lower() or "do not cover" in prompt.lower()

    def test_no_sources_still_produces_prompt(self):
        prompt, norm = render_citation_prompt("Q?", [])
        assert "Q?" in prompt
        assert norm == []


# ═══════════════════════════════════════════════════════════
# resolve_citations
# ═══════════════════════════════════════════════════════════


class TestResolveCitations:
    def _srcs(self, n: int) -> list[SourceEntry]:
        return [SourceEntry(url=f"https://s{i}.example/", title=f"S{i}") for i in range(1, n + 1)]

    def test_extracts_used_indices_in_order(self):
        srcs = self._srcs(3)
        answer = "Foo happened [2]. Bar too [1][3]. Baz again [2]."
        r = resolve_citations(answer, srcs)
        assert r.used_indices == [2, 1, 3]
        assert [s.title for s in r.used_sources] == ["S2", "S1", "S3"]

    def test_flags_out_of_range_indices(self):
        srcs = self._srcs(2)
        r = resolve_citations("Claim [5]. And [1].", srcs)
        assert r.used_indices == [1]
        assert r.invalid_indices == [5]

    def test_no_citations(self):
        r = resolve_citations("No citations here.", self._srcs(3))
        assert r.used_indices == []
        assert r.used_sources == []
        assert r.invalid_indices == []

    def test_dedupes_used_indices(self):
        srcs = self._srcs(2)
        r = resolve_citations("[1] and again [1] and [2] and [1].", srcs)
        assert r.used_indices == [1, 2]
