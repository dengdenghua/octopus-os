"""Auto-retrieval of relevant source chunks for planner grounding."""

from __future__ import annotations

from pathlib import Path

from runtime.memory.hemolymph import code_index
from runtime.memory.hemolymph.code_index import (
    _salient_identifiers,
    reciprocal_rank_fusion,
    retrieve_code_context,
)


def _make_src(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def test_retrieves_relevant_source_chunk(tmp_path: Path) -> None:
    _make_src(
        tmp_path,
        {
            "runtime/planner.py": "def plan_tool_calls():\n    # planner builds react steps\n    return step()\n",
            "runtime/browser.py": "def open_browser():\n    return playwright_launch()\n",
        },
    )
    out = retrieve_code_context("how does plan_tool_calls work", root=tmp_path)
    assert out is not None
    assert "runtime/planner.py:1" in out
    assert "plan_tool_calls" in out
    assert "browser" not in out  # no overlap → not selected


def test_skips_noise_dirs(tmp_path: Path) -> None:
    _make_src(
        tmp_path,
        {
            "node_modules/pkg/index.py": "def widget_factory(): return alpha()\n",
            "tests/test_x.py": "def widget_factory(): pass\n",
            "src/real.py": "def widget_factory():\n    return real_impl()\n",
        },
    )
    out = retrieve_code_context("widget factory", root=tmp_path)
    assert out is not None
    assert "src/real.py" in out
    assert "node_modules" not in out
    assert "tests/" not in out


def test_no_source_returns_none(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("not code", encoding="utf-8")
    assert retrieve_code_context("anything", root=tmp_path) is None


def test_agent_source_packs_are_never_auto_grounded(tmp_path: Path) -> None:
    _make_src(
        tmp_path,
        {
            "agents/vibe_selling/private.py": "def dream_dive_campaign(): return 'private'\n",
            "runtime/growth.py": "def campaign_metrics(): return 'project'\n",
        },
    )
    out = retrieve_code_context("campaign", root=tmp_path)
    assert out is not None
    assert "runtime/growth.py" in out
    assert "agents/vibe_selling/private.py" not in out


def test_no_overlap_returns_none(tmp_path: Path) -> None:
    _make_src(tmp_path, {"a.py": "def alpha(): return beta()\n"})
    assert retrieve_code_context("quantum chromodynamics lattice", root=tmp_path) is None


def test_word_query_matches_camelcase_symbol(tmp_path: Path) -> None:
    _make_src(
        tmp_path, {"a.py": "class ToolEngine:\n    def execute(self):\n        return run()\n"}
    )
    out = retrieve_code_context("tool engine execute", root=tmp_path)
    assert out is not None and "ToolEngine" in out


def test_empty_or_stopword_query_returns_none(tmp_path: Path) -> None:
    _make_src(tmp_path, {"a.py": "def x(): pass\n"})
    assert retrieve_code_context("", root=tmp_path) is None
    assert retrieve_code_context("the and for", root=tmp_path) is None


def test_cache_respects_ttl(tmp_path: Path) -> None:
    _make_src(tmp_path, {"a.py": "def alpha_one(): pass\n"})
    assert "alpha_one" in (retrieve_code_context("alpha one", root=tmp_path) or "")
    (tmp_path / "a.py").write_text("def alpha_two(): pass\n", encoding="utf-8")
    # ttl=0 forces a rebuild → the new symbol is seen
    assert "alpha_two" in (retrieve_code_context("alpha two", root=tmp_path, ttl=0) or "")


def test_real_repo_smoke() -> None:
    out = retrieve_code_context(
        "compose context segments token budget",
        root="runtime",
        max_chunks=2,
    )
    assert out is None or "RELEVANT SOURCE" in out


def test_sink_captures_chosen_chunks_faithfully(tmp_path: Path) -> None:
    _make_src(
        tmp_path,
        {
            "runtime/planner.py": "def plan_tool_calls():\n    # planner builds react steps\n    return step()\n",
            "runtime/browser.py": "def open_browser():\n    return playwright_launch()\n",
        },
    )
    sink: list[dict[str, str]] = []
    out = retrieve_code_context("how does plan_tool_calls work", root=tmp_path, _sink=sink)
    assert out is not None
    # the sink lists EXACTLY the chunk(s) folded into the prompt — same scoring,
    # so a UI chip can't drift from what was actually injected.
    assert sink == [
        {"kind": "source", "title": "planner.py", "path": "runtime/planner.py:1"},
    ]
    assert sink[0]["path"] in out  # faithful: the cited path is in the prompt


def test_explicit_cross_stack_paths_are_always_grounded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ECHO_GROUNDING_HOPS", "0")
    _make_src(
        tmp_path,
        {
            "runtime/protocol/items.py": (
                "class Item:\n"
                "    phase_id: str | None\n"
                "    parent_item_id: str | None\n"
                "    progress_sequence: int | None\n"
            ),
            "frontend/src/core/realtime/items.ts": (
                "export type Item = {\n"
                "  phaseId?: string;\n"
                "  parentItemId?: string;\n"
                "  progressSequence?: number;\n"
                "};\n"
            ),
            "runtime/unrelated.py": "def phase_id_parent_item_id_progress_sequence(): pass\n",
        },
    )
    sink: list[dict[str, str]] = []

    out = retrieve_code_context(
        "只读比较 runtime/protocol/items.py 与 frontend/src/core/realtime/items.ts，用一句话回答。",
        root=tmp_path,
        max_chunks=2,
        _sink=sink,
    )

    assert out is not None
    assert {entry["path"].split(":", 1)[0] for entry in sink} == {
        "runtime/protocol/items.py",
        "frontend/src/core/realtime/items.ts",
    }
    assert "phase_id" in out
    assert "phaseId" in out


def test_strict_explicit_grounding_excludes_ranked_and_dependency_files(
    tmp_path: Path,
) -> None:
    requested = {
        f"src/file-{index}.ts": f"export const requested_{index} = SharedType;\n"
        for index in range(4)
    }
    _make_src(
        tmp_path,
        {
            **requested,
            "src/shared.ts": "export type SharedType = string;\n",
            "src/file-0.test.ts": "test('requested', () => requested_0);\n",
        },
    )
    sink: list[dict[str, str]] = []
    goal = "只读比较 " + "、".join(requested) + "，不要读取其他文件。"

    out = retrieve_code_context(
        goal,
        root=tmp_path,
        strict_explicit_paths=True,
        _sink=sink,
    )

    assert out is not None
    assert "EXPLICITLY REQUESTED SOURCE" in out
    assert {entry["path"].split(":", 1)[0] for entry in sink} == set(requested)
    assert "shared.ts" not in out
    assert "file-0.test.ts" not in out


# ── semantic fusion (reuses the persisted KB index, gated to the workspace) ──


def test_rrf_blends_two_rankings() -> None:
    # "b" sits high in BOTH lists → wins; union is preserved.
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "d"]])
    assert fused[0] == "b"
    assert set(fused) == {"a", "b", "c", "d"}


def test_rrf_handles_empty() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_explicit_root_skips_semantic(tmp_path: Path, monkeypatch) -> None:
    # A retrieval over an explicit (non-cwd) root must NOT consult the global
    # persisted index — it's built for the cwd workspace and would be incoherent.
    _make_src(tmp_path, {"a.py": "def alpha_token(): pass\n"})
    calls = {"n": 0}

    def spy(_q, **_kw):
        calls["n"] += 1
        return [{"path": "x.py", "snippet": "y", "score": 1.0}]

    monkeypatch.setattr(code_index, "search_persisted", spy)
    out = retrieve_code_context("alpha token", root=tmp_path)
    assert calls["n"] == 0
    assert out and "a.py:1" in out


def test_no_semantic_is_pure_bm25(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _make_src(tmp_path, {"a.py": "def alpha_token(): pass\n"})
    monkeypatch.setattr(code_index, "search_persisted", lambda _q, **_kw: None)
    out = retrieve_code_context("alpha token")  # root=None → cwd, semantic eligible
    assert out and "a.py:1" in out  # but None semantic → identical BM25 output


def test_semantic_fuses_in_a_token_missed_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _make_src(
        tmp_path,
        {
            "hit/bm.py": "def alpha_token():\n    return alpha_token()\n",
            "miss/sem.py": "def unrelated_symbol():\n    return other()\n",
        },
    )
    # The embedder surfaces a file with NO token overlap with the query — the
    # exact synonym-bridging gap BM25 can't close. It must be fused in.
    monkeypatch.setattr(
        code_index,
        "search_persisted",
        lambda _q, **_kw: [
            {
                "path": "miss/sem.py",
                "snippet": "# miss/sem.py\ndef unrelated_symbol(): ...",
                "score": 0.9,
            }
        ],
    )
    out = retrieve_code_context("alpha token", max_chunks=3)
    assert out
    assert "hit/bm.py" in out  # BM25 exact-token hit
    assert "miss/sem.py" in out  # fused in by semantic despite zero token overlap


# ── multi-hop grounding (retrieve → follow symbols → retrieve again) ──


def test_salient_identifiers_picks_structural_symbols() -> None:
    text = "def validate_shipping_address(): return Foobar().run_widget()  # the data item"
    out = _salient_identifiers(text)
    assert "validate_shipping_address" in out  # snake_case
    assert "Foobar" in out  # CamelCase
    assert "run_widget" in out
    assert "data" not in out and "item" not in out  # stopwords dropped
    assert "return" not in out


def test_hop_pulls_in_a_referenced_definition(tmp_path: Path) -> None:
    # a.py matches the goal and references Foobar/run_widget, whose definition
    # lives in b.py — which shares NO token with the goal, so round-0 misses it.
    _make_src(
        tmp_path,
        {
            "a.py": "def validate_shipping_address():\n    return Foobar().run_widget()\n",
            "b.py": "class Foobar:\n    def run_widget(self):\n        return helper_zap()\n",
        },
    )
    out = retrieve_code_context("validate the shipping address", root=tmp_path, max_chunks=3)
    assert out
    assert "a.py" in out  # round-0 direct hit
    assert "b.py" in out  # pulled in by the symbol hop
    assert "(dependency)" in out  # tagged as a dependency


def test_hops_zero_disables_expansion(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ECHO_GROUNDING_HOPS", "0")
    _make_src(
        tmp_path,
        {
            "a.py": "def validate_shipping_address():\n    return Foobar().run_widget()\n",
            "b.py": "class Foobar:\n    def run_widget(self):\n        return helper_zap()\n",
        },
    )
    out = retrieve_code_context("validate the shipping address", root=tmp_path, max_chunks=3)
    assert out
    assert "a.py" in out
    assert "b.py" not in out  # one-shot: no graph follow
    assert "(dependency)" not in out


# ── AST-aware chunking (function/class boundaries, real line numbers) ──


def test_ast_chunks_at_function_boundaries(tmp_path: Path) -> None:
    # imports + two functions at different lines → each function is its OWN chunk
    # at its real start line, not folded into a single line-1 window.
    src = (
        "import os\n"  # 1
        "\n"  # 2
        "def alpha_one():\n"  # 3
        "    return 1\n"  # 4
        "\n"  # 5
        "def beta_two():\n"  # 6
        "    return 2\n"  # 7
    )
    _make_src(tmp_path, {"m.py": src})
    out = retrieve_code_context("beta two", root=tmp_path, max_chunks=5)
    assert out
    assert "m.py:6" in out  # beta_two chunk starts at its REAL line (6), not 1


def test_ast_falls_back_to_windows_on_syntax_error(tmp_path: Path) -> None:
    _make_src(tmp_path, {"bad.py": "def broken(:\n    oops_here\n"})  # unparseable
    out = retrieve_code_context("broken oops_here", root=tmp_path)
    assert out is None or "bad.py" in out  # no crash; window fallback indexes it

