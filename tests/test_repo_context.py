"""Auto-retrieval of project-wiki context for planner grounding."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from runtime.memory.hemolymph.repo_context import (
    _flatten,
    _split_frontmatter,
    _tokenize,
    build_codebase_context,
    collect_codebase_sources,
    retrieve_repo_context,
)


def _page_with_fm(
    title: str,
    body: str,
    *,
    tier: str = "standard",
    description: str = "",
    tags: list[str] | None = None,
) -> str:
    """A page body prefixed with OKF frontmatter (JSON-literal values, matching
    gen_wiki's emitter) for exercising the retriever's frontmatter handling."""
    fm = [
        "---",
        'type: "Doc"',
        f"title: {json.dumps(title)}",
        f"description: {json.dumps(description)}",
        f"tags: {json.dumps(tags or [])}",
        f"tier: {json.dumps(tier)}",
        "---",
        "",
    ]
    return "\n".join(fm) + body


def _make_wiki(
    root: Path,
    pages: list[tuple[str, str, str]],
    edges: list[dict[str, str]] | None = None,
) -> Path:
    auto = root / "docs" / "auto"
    auto.mkdir(parents=True, exist_ok=True)
    tree: list[dict[str, Any]] = []
    for title, rel, body in pages:
        (auto / rel).parent.mkdir(parents=True, exist_ok=True)
        (auto / rel).write_text(body, encoding="utf-8")
        tree.append({"type": "doc", "title": title, "path": rel})
    manifest: dict[str, Any] = {"version": 2, "tree": tree}
    if edges is not None:
        manifest["edges"] = edges
    (auto / "index.json").write_text(json.dumps(manifest), encoding="utf-8")
    return auto


def test_retrieves_most_relevant_page(tmp_path: Path) -> None:
    auto = _make_wiki(
        tmp_path,
        [
            (
                "Cerebrum planning",
                "cerebrum.md",
                "The planner builds a ReAct loop with tool calls.",
            ),
            ("Browser automation", "browser.md", "Playwright drives chromium via skills."),
        ],
    )
    out = retrieve_repo_context("how does the planner cerebrum work", wiki_dir=auto)
    assert out is not None
    assert "Cerebrum planning" in out and "ReAct loop" in out
    assert "Browser automation" not in out  # no overlap → not selected


def test_nested_tree_is_flattened() -> None:
    tree = [
        {"type": "doc", "title": "A", "path": "a.md"},
        {
            "type": "dir",
            "title": "D",
            "children": [
                {"type": "doc", "title": "B", "path": "d/b.md"},
            ],
        },
    ]
    assert _flatten(tree) == [("A", "a.md"), ("B", "d/b.md")]


def test_no_wiki_returns_none(tmp_path: Path) -> None:
    assert retrieve_repo_context("anything", wiki_dir=tmp_path / "nope") is None


def test_no_overlap_returns_none(tmp_path: Path) -> None:
    auto = _make_wiki(tmp_path, [("Browser", "b.md", "playwright chromium")])
    assert retrieve_repo_context("quantum entanglement theory", wiki_dir=auto) is None


def test_agent_profile_wiki_pages_are_never_auto_grounded(tmp_path: Path) -> None:
    """A teammate's generated SOUL page must not enter another agent's prompt."""
    auto = _make_wiki(
        tmp_path,
        [
            ("Luna", "20-backend/26-agents/vibe_selling.md", "growth campaign dream dive"),
            ("Project growth", "product/growth.md", "growth campaign metrics"),
        ],
    )
    sink: list[dict[str, str]] = []
    out = retrieve_repo_context("growth campaign", wiki_dir=auto, _sink=sink)
    assert out is not None
    assert "Project growth" in out
    assert "Luna" not in out
    assert sink == [{"kind": "doc", "title": "Project growth", "path": "product/growth.md"}]


def test_agent_type_wiki_pages_are_never_auto_grounded(tmp_path: Path) -> None:
    auto = _make_wiki(
        tmp_path,
        [
            ("Teammate", "profiles/teammate.md", _page_with_fm("Teammate", "growth campaign")),
            ("Project growth", "product/growth.md", "growth campaign metrics"),
        ],
    )
    # Keep the path neutral: the frontmatter type alone is enough to enforce
    # the boundary for future wiki layouts.
    teammate = auto / "profiles" / "teammate.md"
    teammate.write_text(
        teammate.read_text(encoding="utf-8").replace('type: "Doc"', 'type: "Agent"'),
        encoding="utf-8",
    )
    out = retrieve_repo_context("growth campaign", wiki_dir=auto)
    assert out is not None
    assert "Project growth" in out
    assert "Teammate" not in out


def test_empty_or_stopword_query_returns_none(tmp_path: Path) -> None:
    auto = _make_wiki(tmp_path, [("Topic", "x.md", "content")])
    assert retrieve_repo_context("", wiki_dir=auto) is None
    assert retrieve_repo_context("the and for you", wiki_dir=auto) is None


def test_budget_truncates_long_pages(tmp_path: Path) -> None:
    big = "alpha\n" + ("word " * 5000)
    auto = _make_wiki(tmp_path, [("Alpha topic", "a.md", big)])
    out = retrieve_repo_context("alpha", wiki_dir=auto, budget_tokens=100, max_pages=1)
    assert out is not None
    assert "(truncated)" in out
    assert len(out) < 1200


def test_cache_refreshes_on_mtime(tmp_path: Path) -> None:
    auto = _make_wiki(tmp_path, [("Alpha topic", "a.md", "alpha content one")])
    assert "content one" in (retrieve_repo_context("alpha", wiki_dir=auto) or "")
    # rewrite the page and bump index.json mtime → cache must invalidate
    (auto / "a.md").write_text("alpha content two", encoding="utf-8")
    idx = auto / "index.json"
    idx.write_text(idx.read_text())
    future = time.time() + 10
    os.utime(idx, (future, future))
    assert "content two" in (retrieve_repo_context("alpha", wiki_dir=auto) or "")


def test_identifier_tokenization_splits_camel_and_snake() -> None:
    assert set(_tokenize("ToolEngine")) >= {"tool", "engine"}
    assert set(_tokenize("tool_engine executor")) >= {"tool", "engine", "executor"}
    assert set(_tokenize("HTTPServer")) >= {"http", "server"}
    assert "规划" in _tokenize("cerebrum 规划")


def test_cjk_run_emits_bigrams_and_whole_run() -> None:
    # A CJK run yields its adjacent bigrams (partial-overlap signal) AND the
    # whole run (exact-match signal). ADR-009 Phase 0: whole-run-only made BM25
    # weak on Chinese — a CN goal shares bigrams, not whole runs, with a CN doc.
    toks = set(_tokenize("简历优化"))
    assert {"简历", "历优", "优化"} <= toks  # bigrams
    assert "简历优化" in toks  # whole run still present
    # 2-char run: its only bigram is itself, so single domain words still surface
    assert "规划" in _tokenize("规划")


def test_retrieves_chinese_page_by_bigram_overlap(tmp_path: Path) -> None:
    # No shared *whole* CJK run and no shared English token between goal and the
    # target page — only shared bigrams (简历 / 关键 / 键词). Whole-run-only
    # tokenization missed this; bigrams retrieve it.
    auto = _make_wiki(
        tmp_path,
        [
            ("简历助手", "resume.md", "简历优化与关键词匹配分析"),
            ("浏览器", "b.md", "playwright chromium 自动化测试"),
        ],
    )
    out = retrieve_repo_context("帮我改简历做关键词", wiki_dir=auto)
    assert out is not None
    assert "简历助手" in out
    assert "浏览器" not in out


def test_word_goal_matches_camelcase_identifier(tmp_path: Path) -> None:
    auto = _make_wiki(
        tmp_path,
        [
            ("Engine", "e.md", "The ToolEngine executes skills via execute_token."),
            ("Other", "o.md", "unrelated browser playwright content"),
        ],
    )
    out = retrieve_repo_context("how does the tool engine execute", wiki_dir=auto)
    assert out is not None and "ToolEngine" in out


def test_bm25_length_normalization_beats_long_page(tmp_path: Path) -> None:
    # Both pages contain the query terms, but the catalog page buries them in
    # 1800 unrelated tokens. Plain overlap would tie (or favour the long page);
    # BM25 length-normalizes so the short on-topic page wins.
    short = "Cerebrum planner: plans tool calls."
    longp = "cerebrum planner " + ("catalog skill registry module export summary " * 300)
    auto = _make_wiki(
        tmp_path,
        [
            ("Cerebrum planning", "cere.md", short),
            ("Skills catalog", "cat.md", longp),
        ],
    )
    out = retrieve_repo_context("cerebrum planner", wiki_dir=auto, max_pages=1)
    assert out is not None
    assert "Cerebrum planning" in out
    assert "Skills catalog" not in out


def test_graph_boost_reranks_dependency_neighbor(tmp_path: Path) -> None:
    # a.md is the strong hit. z_tool.md and m_other.md TIE on base BM25 (same
    # body), so without the graph they sort by path → m_other before z_tool.
    # An import edge a.md↔z_tool.md lifts z_tool above m_other: a page's
    # dependency context outranks an equally-lexical but unconnected page.
    auto = _make_wiki(
        tmp_path,
        [
            ("A", "a.md", "cerebrum planner cerebrum planner"),
            ("Tool", "z_tool.md", "planner"),
            ("Other", "m_other.md", "planner"),
        ],
        edges=[{"from": "a.md", "to": "z_tool.md", "type": "imports"}],
    )
    sink: list[dict[str, str]] = []
    retrieve_repo_context("cerebrum planner", wiki_dir=auto, max_pages=2, _sink=sink)
    assert [s["path"] for s in sink] == ["a.md", "z_tool.md"]


def test_graph_boost_disabled_by_env(tmp_path: Path, monkeypatch) -> None:
    # Same wiki; with the graph disabled the tie falls back to path order, so
    # the unconnected m_other (m < z) takes second — proving the boost, not
    # something else, produced the re-rank above.
    monkeypatch.setenv("ECHO_CODEBASE_GRAPH", "0")
    auto = _make_wiki(
        tmp_path,
        [
            ("A", "a.md", "cerebrum planner cerebrum planner"),
            ("Tool", "z_tool.md", "planner"),
            ("Other", "m_other.md", "planner"),
        ],
        edges=[{"from": "a.md", "to": "z_tool.md", "type": "imports"}],
    )
    sink: list[dict[str, str]] = []
    retrieve_repo_context("cerebrum planner", wiki_dir=auto, max_pages=2, _sink=sink)
    assert [s["path"] for s in sink] == ["a.md", "m_other.md"]


def test_split_frontmatter_parses_json_values_and_passes_through() -> None:
    meta, body = _split_frontmatter('---\ntype: "Doc"\ntags: ["a", "b"]\ntier: "core"\n---\nbody')
    assert meta == {"type": "Doc", "tags": ["a", "b"], "tier": "core"}
    assert body == "body"
    assert _split_frontmatter("# plain markdown") == ({}, "# plain markdown")
    # A doc that merely opens with a `---` rule (no OKF `type`) is NOT
    # frontmatter — even when the block contains a colon — so its body is
    # returned intact rather than truncated at the second `---`.
    rule_doc = "---\nNote: not frontmatter\n---\nreal body"
    assert _split_frontmatter(rule_doc) == ({}, rule_doc)


def test_frontmatter_stripped_from_prompt_and_description_indexed(tmp_path: Path) -> None:
    # The match term lives only in the OKF description (not the body), so a hit
    # proves the description is indexed. And the rendered prompt must not leak
    # the raw YAML keys — the body injected verbatim is frontmatter-stripped.
    auto = _make_wiki(
        tmp_path,
        [
            (
                "Topic",
                "t.md",
                _page_with_fm(
                    "Topic",
                    "# Topic\nbody about widgets",
                    description="quantum entanglement theory",
                ),
            ),
            ("Other", "o.md", _page_with_fm("Other", "# Other\nbody about gadgets")),
        ],
    )
    out = retrieve_repo_context("quantum entanglement", wiki_dir=auto)
    assert out is not None
    assert "Topic" in out and "Other" not in out
    assert "type:" not in out and "tier:" not in out  # frontmatter not leaked


def test_tier_boosts_core_over_standard(tmp_path: Path) -> None:
    # Both pages tie on BM25 for "planner" (same body); the core-tier page wins
    # despite sorting after a_std alphabetically, proving the tier multiplier.
    auto = _make_wiki(
        tmp_path,
        [
            ("Core", "z_core.md", _page_with_fm("Core", "planner", tier="core")),
            ("Std", "a_std.md", _page_with_fm("Std", "planner", tier="standard")),
        ],
    )
    sink: list[dict[str, str]] = []
    retrieve_repo_context("planner", wiki_dir=auto, max_pages=2, _sink=sink)
    assert sink[0]["path"] == "z_core.md"


def test_semantic_lane_fuses_when_embedder_present(tmp_path: Path, monkeypatch) -> None:
    # Three pages tie on BM25 (same body) → lexical order is path-sorted
    # [a, b, c]. A configured embedder makes the query most similar to c, then a,
    # then b; RRF fusion of [a,b,c] + [c,a,b] pulls c above b. Proves the cached
    # semantic lane participates in fusion.
    import runtime.memory.hemolymph.embedding_backend as eb

    auto = _make_wiki(
        tmp_path,
        [("Alpha", "a.md", "planner"), ("Beta", "b.md", "planner"), ("Gamma", "c.md", "planner")],
    )
    # Page vectors (a, b, c) plus the query vector — chosen so cosine ranks
    # c > a > b. embed_texts is called once for the 3-page corpus, once for the
    # 1-text query.
    vecs = {
        "Alpha\nplanner": [1.0, 0.0],
        "Beta\nplanner": [0.0, 1.0],
        "Gamma\nplanner": [1.0, 1.0],
    }
    monkeypatch.setattr(eb, "available", lambda: True)
    monkeypatch.setattr(eb, "embed_texts", lambda texts: [vecs.get(t, [1.0, 0.9]) for t in texts])
    sink: list[dict[str, str]] = []
    retrieve_repo_context("planner", wiki_dir=auto, max_pages=3, _sink=sink)
    paths = [s["path"] for s in sink]
    assert paths.index("c.md") < paths.index("b.md")  # semantic lifted c over b


def test_semantic_lane_off_without_embedder(tmp_path: Path, monkeypatch) -> None:
    # No embedder → semantic lane skipped → pure lexical path-tiebreak order.
    import runtime.memory.hemolymph.embedding_backend as eb

    monkeypatch.setattr(eb, "available", lambda: False)
    auto = _make_wiki(
        tmp_path,
        [("Alpha", "a.md", "planner"), ("Beta", "b.md", "planner"), ("Gamma", "c.md", "planner")],
    )
    sink: list[dict[str, str]] = []
    retrieve_repo_context("planner", wiki_dir=auto, max_pages=3, _sink=sink)
    assert [s["path"] for s in sink] == ["a.md", "b.md", "c.md"]


def test_reranker_reorders_when_cohere_configured(tmp_path: Path, monkeypatch) -> None:
    # Three pages tie on BM25 → fused order is path-sorted [a, b, c]. With a real
    # reranker configured (COHERE_API_KEY), the cross-encoder reorders them
    # [c, a, b] and that becomes the final order.
    import importlib

    _rr_mod = importlib.import_module("runtime.research.rerank")  # the module, not the re-export
    auto = _make_wiki(
        tmp_path,
        [("Alpha", "a.md", "planner"), ("Beta", "b.md", "planner"), ("Gamma", "c.md", "planner")],
    )
    monkeypatch.setenv("COHERE_API_KEY", "test-key")

    class _Src:
        def __init__(self, url: str) -> None:
            self.url = url

    class _Hit:
        def __init__(self, url: str) -> None:
            self.source = _Src(url)

    class _Res:
        backend = "cohere"
        hits = [_Hit("c.md"), _Hit("a.md"), _Hit("b.md")]

    monkeypatch.setattr(_rr_mod, "rerank", lambda query, sources, **k: _Res())
    sink: list[dict[str, str]] = []
    retrieve_repo_context("planner", wiki_dir=auto, max_pages=3, _sink=sink)
    assert [s["path"] for s in sink] == ["c.md", "a.md", "b.md"]


def test_reranker_skipped_without_cohere(tmp_path: Path, monkeypatch) -> None:
    # No COHERE_API_KEY → reranker dormant → plain fused (lexical) order.
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    auto = _make_wiki(tmp_path, [("Alpha", "a.md", "planner"), ("Beta", "b.md", "planner")])
    sink: list[dict[str, str]] = []
    retrieve_repo_context("planner", wiki_dir=auto, max_pages=2, _sink=sink)
    assert [s["path"] for s in sink] == ["a.md", "b.md"]


def test_real_wiki_smoke() -> None:
    # the repo ships a generated wiki under docs/auto
    out = retrieve_repo_context("cerebrum planner tool engine skills", wiki_dir="docs/auto")
    assert out is None or "CODEBASE DOCS" in out


# ── render_codebase_context: shared wiki+code grounding (planner + chat) ──


def test_render_codebase_context_combines_wiki_and_code(monkeypatch) -> None:
    import runtime.memory.hemolymph.code_index as ci
    import runtime.memory.hemolymph.repo_context as rc

    monkeypatch.delenv("ECHO_CODEBASE_CONTEXT", raising=False)
    monkeypatch.setattr(rc, "retrieve_repo_context", lambda goal, **k: "WIKI-PART")
    monkeypatch.setattr(ci, "retrieve_code_context", lambda goal, **k: "CODE-PART")
    out = rc.render_codebase_context("fix the planner")
    assert "WIKI-PART" in out and "CODE-PART" in out


def test_render_codebase_context_empty_goal_and_env_off(monkeypatch) -> None:
    import runtime.memory.hemolymph.repo_context as rc

    monkeypatch.delenv("ECHO_CODEBASE_CONTEXT", raising=False)
    assert rc.render_codebase_context("") == ""
    assert rc.render_codebase_context("   ") == ""
    monkeypatch.setenv("ECHO_CODEBASE_CONTEXT", "0")
    assert rc.render_codebase_context("anything") == ""


# ── grounding sources: faithful to what render_codebase_context injects ──


def test_sink_captures_chosen_doc_faithfully(tmp_path: Path) -> None:
    auto = _make_wiki(
        tmp_path,
        [
            ("Cerebrum planning", "cerebrum.md", "The planner builds a ReAct loop."),
            ("Browser automation", "browser.md", "Playwright drives chromium."),
        ],
    )
    sink: list[dict[str, str]] = []
    out = retrieve_repo_context("how does the planner cerebrum work", wiki_dir=auto, _sink=sink)
    assert out is not None
    assert sink == [{"kind": "doc", "title": "Cerebrum planning", "path": "cerebrum.md"}]
    assert all(s["path"] in out for s in sink)  # every cited path is in the prompt


def test_build_codebase_context_returns_text_and_sources(monkeypatch) -> None:
    import runtime.memory.hemolymph.code_index as ci
    import runtime.memory.hemolymph.repo_context as rc

    monkeypatch.delenv("ECHO_CODEBASE_CONTEXT", raising=False)

    def _fake_wiki(goal: str, **k: Any) -> str:
        sink = k.get("_sink")
        if sink is not None:
            sink.append({"kind": "doc", "title": "Cerebrum", "path": "cerebrum.md"})
        return "WIKI-PART"

    def _fake_code(goal: str, **k: Any) -> str:
        sink = k.get("_sink")
        if sink is not None:
            sink.append({"kind": "source", "title": "planner.py", "path": "p.py:1"})
        return "CODE-PART"

    monkeypatch.setattr(rc, "retrieve_repo_context", _fake_wiki)
    monkeypatch.setattr(ci, "retrieve_code_context", _fake_code)

    text, sources = build_codebase_context("fix the planner")
    assert "WIKI-PART" in text and "CODE-PART" in text
    assert sources == [
        {"kind": "doc", "title": "Cerebrum", "path": "cerebrum.md"},
        {"kind": "source", "title": "planner.py", "path": "p.py:1"},
    ]
    # collect_codebase_sources is just the sources half
    assert collect_codebase_sources("fix the planner") == sources


def test_strict_explicit_codebase_context_skips_wiki_and_forwards_scope(monkeypatch) -> None:
    import runtime.memory.hemolymph.code_index as ci
    import runtime.memory.hemolymph.repo_context as rc

    wiki_calls = {"count": 0}
    code_flags: list[bool] = []

    def _fake_wiki(_goal: str, **_kwargs: Any) -> str:
        wiki_calls["count"] += 1
        return "WIKI-PART"

    def _fake_code(_goal: str, **kwargs: Any) -> str:
        code_flags.append(bool(kwargs.get("strict_explicit_paths")))
        sink = kwargs.get("_sink")
        if sink is not None:
            sink.append({"kind": "source", "title": "a.py", "path": "a.py:1"})
        return "STRICT-CODE-PART"

    monkeypatch.setattr(rc, "retrieve_repo_context", _fake_wiki)
    monkeypatch.setattr(ci, "retrieve_code_context", _fake_code)

    text, sources = build_codebase_context(
        "只读读取 a.py，不要读取其他文件。",
        strict_explicit_scope=True,
    )

    assert text == "STRICT-CODE-PART"
    assert sources == [{"kind": "source", "title": "a.py", "path": "a.py:1"}]
    assert wiki_calls["count"] == 0
    assert code_flags == [True]


def test_collect_codebase_sources_empty_when_off(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_CODEBASE_CONTEXT", "0")
    assert collect_codebase_sources("anything") == []
    monkeypatch.delenv("ECHO_CODEBASE_CONTEXT", raising=False)
    assert collect_codebase_sources("") == []

