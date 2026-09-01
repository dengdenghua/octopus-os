"""Tests for runtime.memory.skills_lib.meta_skill — Meta-Skill (能力包) orchestration."""

from pathlib import Path

import pytest
from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal
from runtime.memory.skills_lib.meta_skill import (
    MetaEdge,
    MetaSkill,
    MetaStep,
    _tokenize,
    compile_to_task_graph,
    display_name_for_kind,
    list_meta_skills,
    load_meta_skill,
    match_meta_skill,
    meta_skill_from_dict,
    meta_skill_from_yaml_text,
    meta_skill_to_mermaid,
    save_meta_skill,
)
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
)
from runtime.safety.auth import TrustEngine

# ── Validators ───────────────────────────────────────────


class TestMetaStepValidation:
    def test_valid_node_id(self):
        MetaStep(node_id="research", skill_ref="deep_research")

    def test_invalid_node_id_uppercase_rejected(self):
        with pytest.raises(ValueError, match="node_id must match"):
            MetaStep(node_id="Research", skill_ref="deep_research")

    def test_invalid_node_id_starts_with_digit(self):
        with pytest.raises(ValueError, match="node_id must match"):
            MetaStep(node_id="1_research", skill_ref="deep_research")

    def test_empty_skill_ref_rejected(self):
        with pytest.raises(ValueError, match="empty skill_ref"):
            MetaStep(node_id="research", skill_ref="")

    def test_node_id_too_long(self):
        with pytest.raises(ValueError, match="node_id must match"):
            MetaStep(node_id="x" * 33, skill_ref="foo")


class TestMetaSkillValidation:
    def test_minimal_skill(self):
        s = MetaSkill(
            name="test",
            steps=(MetaStep(node_id="a", skill_ref="x"),),
        )
        assert s.name == "test"
        assert len(s.steps) == 1

    def test_duplicate_step_ids_rejected(self):
        with pytest.raises(ValueError, match="duplicate step ids"):
            MetaSkill(
                name="test",
                steps=(
                    MetaStep(node_id="a", skill_ref="x"),
                    MetaStep(node_id="a", skill_ref="y"),
                ),
            )

    def test_invalid_name_rejected(self):
        with pytest.raises(ValueError, match="name must match"):
            MetaSkill(name="Bad Name", steps=(MetaStep(node_id="a", skill_ref="x"),))

    def test_no_steps_rejected(self):
        with pytest.raises(ValueError, match="no steps"):
            MetaSkill(name="test", steps=())

    def test_self_dependency_rejected(self):
        with pytest.raises(ValueError, match="cannot depend on itself"):
            MetaSkill(
                name="test",
                steps=(MetaStep(node_id="a", skill_ref="x", depends_on=("a",)),),
            )

    def test_unknown_dependency_rejected(self):
        with pytest.raises(ValueError, match="depends_on unknown"):
            MetaSkill(
                name="test",
                steps=(MetaStep(node_id="a", skill_ref="x", depends_on=("ghost",)),),
            )

    def test_edge_unknown_node_rejected(self):
        with pytest.raises(ValueError, match="from_node not in steps"):
            MetaSkill(
                name="test",
                steps=(MetaStep(node_id="a", skill_ref="x"),),
                edges=(MetaEdge(from_node="ghost", to_node="a"),),
            )


# ── YAML / dict loader ────────────────────────────────────


class TestLoader:
    def test_minimal_dict(self):
        data = {
            "name": "x",
            "steps": [{"node_id": "a", "skill": "y"}],
        }
        meta = meta_skill_from_dict(data)
        assert meta.name == "x"
        assert meta.steps[0].node_id == "a"

    def test_yaml_minimal(self):
        yaml_text = """
name: simple
description: A simple workflow
when_to_use: user asks
steps:
  - node_id: a
    skill: foo
  - node_id: b
    skill: bar
    depends_on: [a]
"""
        meta = meta_skill_from_yaml_text(yaml_text)
        assert meta.name == "simple"
        assert len(meta.steps) == 2
        assert meta.steps[1].depends_on == ("a",)

    def test_yaml_with_budget(self):
        yaml_text = """
name: expensive
budget:
  tokens: 100000
  usd: 3.5
  latency_ms: 600000
steps:
  - node_id: a
    skill: foo
"""
        meta = meta_skill_from_yaml_text(yaml_text)
        assert meta.budget_tokens == 100_000
        assert meta.budget_usd == 3.5
        assert meta.budget_latency_ms == 600_000

    def test_yaml_with_explicit_edges(self):
        yaml_text = """
name: branched
steps:
  - {node_id: a, skill: foo}
  - {node_id: b, skill: bar}
  - {node_id: c, skill: baz, depends_on: [a, b]}
edges:
  - {from: a, to: c}
  - {from: b, to: c, kind: branch}
"""
        meta = meta_skill_from_yaml_text(yaml_text)
        assert len(meta.edges) == 2
        assert meta.edges[1].kind == "branch"

    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="missing required 'name'"):
            meta_skill_from_dict({"steps": [{"node_id": "a", "skill": "x"}]})

    def test_empty_steps_raises(self):
        with pytest.raises(ValueError, match="non-empty list"):
            meta_skill_from_dict({"name": "x", "steps": []})


# ── Compile to TaskGraph ─────────────────────────────────


class TestCompile:
    def test_compile_simple_chain(self):
        meta = MetaSkill(
            name="simple",
            steps=(
                MetaStep(node_id="a", skill_ref="foo"),
                MetaStep(node_id="b", skill_ref="bar", depends_on=("a",)),
            ),
        )
        graph = compile_to_task_graph(meta)
        assert len(graph.nodes) == 2
        assert len(graph.edges) == 1
        assert graph.task_type == "meta_skill:simple"
        assert graph.budget.tokens == meta.budget_tokens

    def test_compile_rewrites_friendly_aliases(self):
        """``{a.output}`` in args stays ``{a.output}`` (no prefix),
        but a separate ``alias_to_id`` mapping is honoured.
        """
        meta = MetaSkill(
            name="aliased",
            steps=(
                MetaStep(node_id="a", skill_ref="foo"),
                MetaStep(
                    node_id="b",
                    skill_ref="bar",
                    args_template={"input": "{a.output}"},
                    depends_on=("a",),
                ),
            ),
        )
        graph = compile_to_task_graph(meta)
        b = next(n for n in graph.nodes if n.node_id == "b")
        # The friendly name ``a`` IS the step's node_id, so rewrite
        # is a no-op: template stays as ``{a.output}``.
        assert b.args_template["input"] == "{a.output}"

    def test_compile_rewrites_via_alias_to_id(self):
        """When the placeholder name differs from the node_id, the
        compile rewrites it to the canonical node_id.
        """
        meta = MetaSkill(
            name="remapped",
            steps=(
                MetaStep(node_id="research", skill_ref="foo"),
                MetaStep(
                    node_id="paper",
                    skill_ref="bar",
                    args_template={"src": "{research.output}"},
                    depends_on=("research",),
                ),
            ),
        )
        # No alias_to_id here (default identity); since the alias
        # ``research`` IS the node_id, no rewrite happens.
        graph = compile_to_task_graph(meta)
        paper = next(n for n in graph.nodes if n.node_id == "paper")
        assert paper.args_template["src"] == "{research.output}"

    def test_compile_injects_user_input(self):
        meta = MetaSkill(
            name="with-input",
            steps=(
                MetaStep(
                    node_id="a",
                    skill_ref="foo",
                    args_template={"topic": "{user_input.topic}"},
                ),
            ),
        )
        graph = compile_to_task_graph(meta, user_input={"topic": "RAG systems"})
        a = graph.nodes[0]
        # user_input bindings are present (default key path)
        assert "user_input.topic" in a.args_template

    def test_compile_explicit_edges(self):
        meta = MetaSkill(
            name="explicit-edges",
            steps=(
                MetaStep(node_id="a", skill_ref="foo"),
                MetaStep(node_id="b", skill_ref="bar"),
            ),
            edges=(MetaEdge(from_node="a", to_node="b"),),
        )
        graph = compile_to_task_graph(meta)
        assert len(graph.edges) == 1

    def test_compile_diamond_dag(self):
        """diamond: a -> b, a -> c, b -> d, c -> d"""
        meta = MetaSkill(
            name="diamond",
            steps=(
                MetaStep(node_id="a", skill_ref="root"),
                MetaStep(node_id="b", skill_ref="left", depends_on=("a",)),
                MetaStep(node_id="c", skill_ref="right", depends_on=("a",)),
                MetaStep(
                    node_id="d",
                    skill_ref="merge",
                    depends_on=("b", "c"),
                ),
            ),
        )
        graph = compile_to_task_graph(meta)
        # 4 nodes, 4 edges (a->b, a->c, b->d, c->d)
        assert len(graph.nodes) == 4
        assert len(graph.edges) == 4

    def test_compile_budget_passthrough(self):
        meta = MetaSkill(
            name="expensive",
            steps=(MetaStep(node_id="a", skill_ref="foo"),),
            budget_tokens=50_000,
            budget_usd=2.5,
            budget_latency_ms=900_000,
        )
        graph = compile_to_task_graph(meta)
        assert graph.budget.tokens == 50_000
        assert graph.budget.usd == 2.5
        assert graph.budget.latency_ms == 900_000


# ── Save / Load round-trip ───────────────────────────────


class TestRoundTrip:
    def test_save_and_load_minimal(self, tmp_path, monkeypatch):
        # Override project root to a tmp dir
        monkeypatch.setattr(
            "runtime.platform.process.paths.project_root",
            lambda: tmp_path,
        )
        meta = MetaSkill(
            name="roundtrip",
            description="test",
            when_to_use="user says X",
            steps=(MetaStep(node_id="a", skill_ref="foo"),),
        )
        path = save_meta_skill(meta)
        assert path.exists()

        loaded = load_meta_skill("roundtrip")
        assert loaded is not None
        assert loaded.name == "roundtrip"
        assert loaded.steps[0].skill_ref == "foo"

    def test_save_creates_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "runtime.platform.process.paths.project_root",
            lambda: tmp_path,
        )
        meta = MetaSkill(
            name="autocreate",
            steps=(MetaStep(node_id="a", skill_ref="foo"),),
        )
        save_meta_skill(meta)
        assert (tmp_path / "meta_skills").exists()
        assert (tmp_path / "meta_skills" / "autocreate.yaml").exists()

    def test_list_meta_skills(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "runtime.platform.process.paths.project_root",
            lambda: tmp_path,
        )
        for name in ("alpha", "beta", "gamma"):
            save_meta_skill(
                MetaSkill(
                    name=name,
                    description=f"desc-{name}",
                    steps=(MetaStep(node_id="a", skill_ref="foo"),),
                )
            )

        results = list_meta_skills()
        names = [r["name"] for r in results]
        expected = {"alpha", "beta", "gamma"}
        assert expected <= set(names)
        for r in results:
            if r["name"] not in expected:
                continue
            assert r["steps"] == ["a"]


# ── Built-in examples ────────────────────────────────────


class TestBuiltinExamples:
    """The 3 hand-authored YAMLs we ship with the project."""

    @pytest.fixture(autouse=True)
    def _override_paths(self, monkeypatch):

        import runtime.platform.process.paths as paths_mod

        project_root = Path(__file__).resolve().parents[1]
        monkeypatch.setattr(paths_mod, "project_root", lambda: project_root)

    def test_paper_write_loads(self):
        meta = load_meta_skill("paper-write")
        assert meta is not None
        assert meta.name == "paper-write"
        node_ids = [s.node_id for s in meta.steps]
        assert "research" in node_ids
        assert "outline" in node_ids
        assert "pdf" in node_ids  # final compile

    def test_code_review_loads(self):
        meta = load_meta_skill("code-review")
        assert meta is not None
        node_ids = [s.node_id for s in meta.steps]
        assert "security" in node_ids
        assert "performance" in node_ids

    def test_daily_brief_loads(self):
        meta = load_meta_skill("daily-brief")
        assert meta is not None
        node_ids = [s.node_id for s in meta.steps]
        assert "weather" in node_ids
        assert "compose" in node_ids

    def test_paper_write_compiles_to_valid_graph(self):
        meta = load_meta_skill("paper-write")
        assert meta is not None
        graph = compile_to_task_graph(
            meta,
            user_input={"topic": "RAG systems"},
        )
        # 10 steps, plus 1 edge per depends_on entry
        assert len(graph.nodes) == 10
        # GraphRuntime's validator should accept the graph
        # (TaskGraph has a no-cycles validator built in)
        assert graph.task_type == "meta_skill:paper-write"


# ── Matcher (request → meta-skill) ───────────────────────


class TestMatchMetaSkill:
    def test_match_by_english_keywords(self):
        meta = MetaSkill(
            name="paper",
            when_to_use="user asks to write a research paper",
            steps=(MetaStep(node_id="a", skill_ref="x"),),
        )
        m = match_meta_skill("I need to write a research paper", available=[meta])
        assert m is not None
        assert m.name == "paper"

    def test_match_by_chinese_chars(self):
        meta = MetaSkill(
            name="paper-cn",
            when_to_use="用户写论文",
            steps=(MetaStep(node_id="a", skill_ref="x"),),
        )
        m = match_meta_skill("帮我写论文", available=[meta])
        assert m is not None
        assert m.name == "paper-cn"

    def test_no_match_when_unrelated(self):
        meta = MetaSkill(
            name="paper",
            when_to_use="user asks to write a research paper",
            steps=(MetaStep(node_id="a", skill_ref="x"),),
        )
        m = match_meta_skill("play some music", available=[meta])
        assert m is None

    def test_no_match_below_threshold(self):
        """Need ≥2 shared tokens; 1 is not enough."""
        meta = MetaSkill(
            name="paper",
            when_to_use="user asks to write",
            steps=(MetaStep(node_id="a", skill_ref="x"),),
        )
        m = match_meta_skill("write something", available=[meta])
        # Only "write" overlaps (1 token) → no match
        assert m is None

    def test_empty_request_returns_none(self):
        meta = MetaSkill(
            name="paper",
            when_to_use="user asks",
            steps=(MetaStep(node_id="a", skill_ref="x"),),
        )
        assert match_meta_skill("", available=[meta]) is None
        assert match_meta_skill("   ", available=[meta]) is None

    def test_picks_best_match_when_multiple(self):
        a = MetaSkill(
            name="a",
            when_to_use="write a paper",
            steps=(MetaStep(node_id="a", skill_ref="x"),),
        )
        b = MetaSkill(
            name="b",
            when_to_use="write a research paper for me",
            steps=(MetaStep(node_id="a", skill_ref="x"),),
        )
        m = match_meta_skill("write a research paper for me", available=[a, b])
        assert m is not None
        assert m.name == "b"  # higher overlap


class TestTokenizer:
    def test_basic_latin(self):
        assert "hello" in _tokenize("Hello World")

    def test_cjk_chars_individual(self):
        tokens = _tokenize("帮我写论文")
        # Each CJK char becomes its own token
        assert "帮" in tokens
        assert "我" in tokens
        assert "写" in tokens
        assert "论" in tokens
        assert "文" in tokens

    def test_short_tokens_dropped(self):
        # 1-char Latin tokens are dropped
        assert "a" not in _tokenize("a test")
        # 2+ char tokens are kept
        assert "test" in _tokenize("a test")


# ── Kind / display name (能力包) ──────────────────────────


class TestKind:
    """The MetaSkill class is internally still ``MetaSkill`` but
    the user-facing label is **能力包**. We carry the ``kind`` field
    through the loader, the in-memory dataclass, the YAML serializer,
    and ``list_meta_skills()`` so the UI can render the right label.
    """

    def test_default_kind_is_skill_cluster(self):
        m = MetaSkill(
            name="x",
            steps=(MetaStep(node_id="a", skill_ref="foo"),),
        )
        assert m.kind == "skill_cluster"

    def test_explicit_kind_preserved(self):
        m = MetaSkill(
            name="x",
            kind="recipe",
            steps=(MetaStep(node_id="a", skill_ref="foo"),),
        )
        assert m.kind == "recipe"

    def test_loader_picks_kind_from_yaml(self):
        data = {
            "name": "x",
            "kind": "skill_cluster",
            "steps": [{"node_id": "a", "skill": "y"}],
        }
        meta = meta_skill_from_dict(data)
        assert meta.kind == "skill_cluster"

    def test_loader_defaults_when_kind_missing(self):
        data = {
            "name": "x",
            "steps": [{"node_id": "a", "skill": "y"}],
        }
        meta = meta_skill_from_dict(data)
        # Missing kind → default to skill_cluster (UI label "能力包")
        assert meta.kind == "skill_cluster"

    def test_yaml_serializer_writes_kind(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "runtime.platform.process.paths.project_root",
            lambda: tmp_path,
        )
        m = MetaSkill(
            name="kind-test",
            kind="skill_cluster",
            steps=(MetaStep(node_id="a", skill_ref="foo"),),
        )
        save_meta_skill(m)
        text = (tmp_path / "meta_skills" / "kind-test.yaml").read_text(encoding="utf-8")
        assert "kind: skill_cluster" in text

    def test_round_trip_preserves_kind(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "runtime.platform.process.paths.project_root",
            lambda: tmp_path,
        )
        m = MetaSkill(
            name="rt-kind",
            kind="skill_cluster",
            steps=(MetaStep(node_id="a", skill_ref="foo"),),
        )
        save_meta_skill(m)
        loaded = load_meta_skill("rt-kind")
        assert loaded is not None
        assert loaded.kind == "skill_cluster"

    def test_list_returns_kind_and_display_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "runtime.platform.process.paths.project_root",
            lambda: tmp_path,
        )
        save_meta_skill(
            MetaSkill(
                name="show-name",
                steps=(MetaStep(node_id="a", skill_ref="foo"),),
            )
        )
        results = list_meta_skills()
        entry = next(result for result in results if result["name"] == "show-name")
        assert entry["kind"] == "skill_cluster"
        # UI-facing label in Chinese
        assert entry["display_name"] == "能力包"

    def test_display_name_helper(self):
        assert display_name_for_kind("skill_cluster") == "能力包"
        assert display_name_for_kind("recipe") == "配方"
        assert display_name_for_kind("macro") == "宏"
        # Unknown kind → fallback
        assert display_name_for_kind("made_up_kind") == "Made Up Kind"


# ── Mermaid renderer (能力包 → flowchart) ─────────────────────


class TestMermaidRenderer:
    """``meta_skill_to_mermaid`` produces a valid Mermaid ``flowchart``
    string that any modern renderer (GitHub, mermaid.live, Notion)
    can consume.

    The tests assert on the *structure* of the string, not its exact
    bytes — so a future tweak to label formatting does not invalidate
    the whole suite.
    """

    def _chain_meta(self) -> MetaSkill:
        return MetaSkill(
            name="e2e-chain",
            affinity=("finance", "writing"),
            budget_tokens=60_000,
            budget_usd=1.5,
            budget_latency_ms=1_800_000,
            steps=(
                MetaStep(node_id="s1", skill_ref="echo", args_template={"value": 42}),
                MetaStep(
                    node_id="s2",
                    skill_ref="add",
                    args_template={"a": "{s1.output.echoed}", "b": 8},
                    depends_on=("s1",),
                ),
                MetaStep(
                    node_id="s3",
                    skill_ref="final",
                    args_template={"trigger": "{s2.output.sum}"},
                    depends_on=("s2",),
                ),
            ),
        )

    def _diamond_meta(self) -> MetaSkill:
        return MetaSkill(
            name="e2e-diamond",
            steps=(
                MetaStep(node_id="left", skill_ref="echo", args_template={"value": "L"}),
                MetaStep(node_id="right", skill_ref="add", args_template={"a": 1, "b": 2}),
                MetaStep(
                    node_id="merge",
                    skill_ref="join",
                    args_template={"x": "{left.output.echoed}", "y": "{right.output.sum}"},
                    depends_on=("left", "right"),
                ),
                MetaStep(
                    node_id="done",
                    skill_ref="final",
                    args_template={"trigger": "{merge.output.joined}"},
                    depends_on=("merge",),
                ),
            ),
        )

    def test_basic_chain_structure(self):
        mm = meta_skill_to_mermaid(self._chain_meta())
        # Header + 3 classDefs
        assert mm.startswith("flowchart LR\n")
        assert "classDef root " in mm
        assert "classDef sink " in mm
        assert "classDef bridge " in mm
        # 3 node declarations
        for nid in ("s1", "s2", "s3"):
            assert f'{nid}["' in mm
        # Edges (s1→s2, s2→s3)
        assert "s1 --> s2" in mm
        assert "s2 --> s3" in mm
        # Subgraph with kind/affinity
        assert "能力包 e2e-chain" in mm
        assert "affinity: finance, writing" in mm
        assert "kind=skill_cluster" in mm
        # Budget comment
        assert "%% budget: 60,000 tokens" in mm
        assert "$1.50" in mm

    def test_root_and_sink_styles(self):
        """Root (no parents) gets ::root; sink (no children) gets ::sink."""
        mm = meta_skill_to_mermaid(self._chain_meta())
        # s1 has no parents → root
        assert ":::root" in mm and 's1["' in mm
        # s2 has a parent (s1) and a child (s3) → bridge
        assert "s2[..." in mm or 's2["' in mm
        # s3 has no children → sink
        assert ":::sink" in mm
        # Verify the exact role assignments
        s1_line = next(ln for ln in mm.splitlines() if ln.lstrip().startswith('s1["'))
        assert ":::root" in s1_line
        s3_line = next(ln for ln in mm.splitlines() if ln.lstrip().startswith('s3["'))
        assert ":::sink" in s3_line

    def test_diamond_layout_marks_bridge_node(self):
        """In a diamond, ``merge`` has 2 parents and 1 child → bridge.
        The two parallel nodes are roots; ``done`` is a sink.
        """
        mm = meta_skill_to_mermaid(self._diamond_meta())
        # 4 edges (left→merge, right→merge, merge→done; left/right have no parent edge)
        assert "left --> merge" in mm
        assert "right --> merge" in mm
        assert "merge --> done" in mm
        # left and right are roots (no parents)
        left_line = next(ln for ln in mm.splitlines() if ln.lstrip().startswith('left["'))
        right_line = next(ln for ln in mm.splitlines() if ln.lstrip().startswith('right["'))
        assert ":::root" in left_line
        assert ":::root" in right_line
        # merge is bridge
        merge_line = next(ln for ln in mm.splitlines() if ln.lstrip().startswith('merge["'))
        assert ":::bridge" in merge_line
        # done is sink
        done_line = next(ln for ln in mm.splitlines() if ln.lstrip().startswith('done["'))
        assert ":::sink" in done_line

    def test_parallel_eligible_steps_grouped_in_subgraph(self):
        """Steps that share the same dependency depth and have no
        edge between each other should land in a `subgraph` block so
        Mermaid lays them out side-by-side. This is the visual cue
        for "these run in parallel" — without it, two independent
        roots in a diamond render stacked, hiding the parallelism.
        """
        mm = meta_skill_to_mermaid(self._diamond_meta())
        # Diamond depth-0 has TWO roots (left, right) — they should
        # land in the same parallel-level subgraph.
        assert "subgraph par_lvl_0" in mm
        # Subgraph title surfaces the parallelism count for reviewers.
        assert "parallel · 2 tasks" in mm
        # Inside the subgraph: both root node ids appear bare (no
        # bracket label — those declarations were emitted earlier).
        sg_start = mm.index("subgraph par_lvl_0")
        sg_end = mm.index("end", sg_start)
        sg_body = mm[sg_start:sg_end]
        assert "left" in sg_body
        assert "right" in sg_body
        # Stylign is applied so the box reads as a hint not a hard wall.
        assert "stroke-dasharray" in mm

    def test_chain_does_not_create_parallel_subgraph(self):
        """A pure A→B→C chain has every level holding a single node;
        the renderer must NOT emit a subgraph for a 1-node level
        (would read as visual noise).
        """
        mm = meta_skill_to_mermaid(self._chain_meta())
        assert "subgraph par_lvl_" not in mm
        assert "parallel · " not in mm

    def test_three_way_parallel_collapses_into_one_subgraph(self):
        """Three independent roots feeding a single sink (the bug-hunt
        / probe-pattern shape) should produce ONE 3-task subgraph, not
        three single-node subgraphs.
        """
        meta = MetaSkill(
            name="three-way",
            steps=(
                MetaStep(node_id="probe", skill_ref="echo", args_template={"value": "p"}),
                MetaStep(node_id="audit", skill_ref="echo", args_template={"value": "a"}),
                MetaStep(node_id="fuzz", skill_ref="echo", args_template={"value": "f"}),
                MetaStep(
                    node_id="merge",
                    skill_ref="join",
                    args_template={
                        "p": "{probe.output.echoed}",
                        "a": "{audit.output.echoed}",
                        "f": "{fuzz.output.echoed}",
                    },
                    depends_on=("probe", "audit", "fuzz"),
                ),
            ),
        )
        mm = meta_skill_to_mermaid(meta)
        # One subgraph at depth 0 with all three roots.
        assert mm.count("subgraph par_lvl_0") == 1
        assert "parallel · 3 tasks" in mm
        # No subgraph for the single-node level containing merge.
        assert "subgraph par_lvl_1" not in mm

    def test_arg_summary_truncates_long_values(self):
        """Args longer than 60 chars get an ellipsis suffix."""
        long_str = "x" * 200
        meta = MetaSkill(
            name="longy",
            steps=(MetaStep(node_id="a", skill_ref="echo", args_template={"huge": long_str}),),
        )
        mm = meta_skill_to_mermaid(meta)
        # The arg label is "huge=xxx…" with exactly 60 chars of "x" + "…"
        assert "huge=xxx" in mm
        assert "…" in mm
        # The full 200-char string must NOT appear (would balloon the diagram).
        assert long_str not in mm

    def test_arg_summary_limits_to_two_args(self):
        """Only the first 2 args are shown; the rest get a ``+N`` marker."""
        meta = MetaSkill(
            name="manyargs",
            steps=(
                MetaStep(
                    node_id="a", skill_ref="echo", args_template={"a": 1, "b": 2, "c": 3, "d": 4}
                ),
            ),
        )
        mm = meta_skill_to_mermaid(meta)
        # +2 marker for the omitted 2 args
        assert "+2" in mm
        # a and b are shown
        assert "a=1" in mm
        assert "b=2" in mm
        # c and d are NOT shown
        assert "c=3" not in mm
        assert "d=4" not in mm

    def test_template_refs_preserved_in_labels(self):
        """``{step.output.field}`` refs in args stay visible in the
        label so reviewers can see the data-flow wiring at a glance.
        """
        meta = self._chain_meta()
        mm = meta_skill_to_mermaid(meta)
        # The s2 label should mention ``{s1.output.echoed}`` so
        # reviewers can see s1 → s2 wiring.
        assert "{s1.output.echoed}" in mm
        # And s3's label mentions s2's output.
        assert "{s2.output.sum}" in mm

    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError, match="invalid direction"):
            meta_skill_to_mermaid(self._chain_meta(), direction="DIAGONAL")

    def test_include_budget_false_omits_footer(self):
        mm = meta_skill_to_mermaid(
            self._chain_meta(),
            include_budget=False,
        )
        assert "%% budget:" not in mm
        # classDefs still present
        assert "classDef root " in mm

    def test_top_down_direction(self):
        mm = meta_skill_to_mermaid(self._chain_meta(), direction="TD")
        assert mm.startswith("flowchart TD\n")

    def test_built_in_meta_skill_renders(self):
        """Spot-check: every shipped 能力包 renders without error
        and contains at least one node + one edge.
        """
        for name, *_ in TestCatalogSnapshot.EXPECTED:
            meta = load_meta_skill(name)
            assert meta is not None
            mm = meta_skill_to_mermaid(meta)
            assert mm.startswith("flowchart LR\n"), f"{name} did not start with flowchart LR"
            # At least one edge
            assert "-->" in mm, f"{name} had no edges"
            # And the subgraph for affinity/kind
            assert f"能力包 {name}" in mm

    def test_empty_step_args_omits_third_line(self):
        """A step with no args should have only 2 lines in its label
        (id + skill), not 3.
        """
        meta = MetaSkill(
            name="noargs",
            steps=(MetaStep(node_id="a", skill_ref="echo"),),
        )
        mm = meta_skill_to_mermaid(meta)
        # The node label is ``a<br/>echo`` — no trailing ``<br/>``.
        a_line = next(ln for ln in mm.splitlines() if ln.lstrip().startswith('a["'))
        # Count of <br/> in the label section only
        label = a_line.split('"')[1]
        assert label.count("<br/>") == 1, (
            f"expected 1 <br/>, got {label.count('<br/>')}: {a_line!r}"
        )


# ── End-to-end: MetaSkill → TaskGraph → GraphRuntime.run ──────────────


@pytest.fixture
def e2e_stack():
    """A real GraphRuntime + 4 stub skills for end-to-end MetaSkill tests.

    Stub skills are deterministic, capture calls, and return dicts so
    downstream ``{nX.output.field}`` refs can be verified.
    """
    calls: list[tuple[str, dict]] = []

    def make_handler(name: str):
        def handler(**kwargs):
            calls.append((name, dict(kwargs)))
            if name == "echo":
                return {"echoed": kwargs.get("value")}
            if name == "add":
                return {
                    "sum": (kwargs.get("a", 0) or 0) + (kwargs.get("b", 0) or 0),
                }
            if name == "join":
                return {"joined": f"{kwargs.get('x')}::{kwargs.get('y')}"}
            if name == "boom":
                raise RuntimeError("simulated skill failure")
            if name == "final":
                return {"ok": True, "trigger": kwargs.get("trigger")}
            return {"name": name, "args": kwargs}

        return handler

    registry = SkillRegistry()
    for name in ("echo", "add", "join", "boom", "final"):
        registry.register(
            Skill(
                name=name,
                trusted_source=f"skill://test/{name}",
                handler=make_handler(name),
            ),
            verify_tests=False,
        )
    journal = InMemoryJournal()
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://test/*"]),
        journal=journal,
    )
    runtime = GraphRuntime(executor=executor, journal=journal)
    return {"runtime": runtime, "calls": calls, "registry": registry, "journal": journal}


def _run_meta_skill(stack, meta, *, user_input=None) -> object:
    """Compile a MetaSkill to TaskGraph and run it through GraphRuntime.

    Returns the Trajectory. Used by all TestEndToEnd methods to keep
    each test focused on (a) the MetaSkill definition and (b) the
    assertions on the resulting trajectory.
    """
    graph = compile_to_task_graph(meta, user_input=user_input)
    budget = Budget(
        task_id=graph.task_id,
        limits=BudgetLimits(tokens=10_000, usd=0.10, latency_ms=60_000),
    )
    return stack["runtime"].run(
        graph,
        budget=budget,
        caller="arms/test",
        arm_id=ArmId("test_arm"),
    )


class TestEndToEnd:
    """MetaSkill → TaskGraph → GraphRuntime.run end-to-end.

    These tests use a real (in-process) GraphRuntime with stub skills.
    They prove that the whole compile-and-run pipeline actually wires
    up — not just that compile_to_task_graph returns a Pydantic model
    with the right shape.
    """

    def test_simple_chain_3_steps(self, e2e_stack):
        """echo(42) → add(a=echoed, b=8) → final(trigger=sum)."""
        meta = MetaSkill(
            name="e2e-chain",
            steps=(
                MetaStep(node_id="s1", skill_ref="echo", args_template={"value": 42}),
                MetaStep(
                    node_id="s2",
                    skill_ref="add",
                    args_template={"a": "{s1.output.echoed}", "b": 8},
                    depends_on=("s1",),
                ),
                MetaStep(
                    node_id="s3",
                    skill_ref="final",
                    args_template={"trigger": "{s2.output.sum}"},
                    depends_on=("s2",),
                ),
            ),
        )
        traj = _run_meta_skill(e2e_stack, meta)

        assert traj.outcome.success
        assert len(traj.steps) == 3
        # s2 was called with a=42 (resolved from s1.echoed), b=8
        s2_call = e2e_stack["calls"][1]
        assert s2_call[0] == "add"
        assert s2_call[1]["a"] == 42
        assert s2_call[1]["b"] == 8
        # s3 was called with trigger=50
        s3_call = e2e_stack["calls"][2]
        assert s3_call[0] == "final"
        assert s3_call[1]["trigger"] == 50

    def test_diamond_dag_4_steps(self, e2e_stack):
        """Diamond:  echo + add run in parallel, then join, then final."""
        meta = MetaSkill(
            name="e2e-diamond",
            steps=(
                MetaStep(node_id="left", skill_ref="echo", args_template={"value": "L"}),
                MetaStep(node_id="right", skill_ref="add", args_template={"a": 1, "b": 2}),
                MetaStep(
                    node_id="merge",
                    skill_ref="join",
                    args_template={
                        "x": "{left.output.echoed}",
                        "y": "{right.output.sum}",
                    },
                    depends_on=("left", "right"),
                ),
                MetaStep(
                    node_id="done",
                    skill_ref="final",
                    args_template={"trigger": "{merge.output.joined}"},
                    depends_on=("merge",),
                ),
            ),
        )
        traj = _run_meta_skill(e2e_stack, meta)

        assert traj.outcome.success
        assert len(traj.steps) == 4
        # Both parents ran with the right inputs
        left_call = next(c for c in e2e_stack["calls"] if c[0] == "echo")
        assert left_call[1]["value"] == "L"
        right_call = next(c for c in e2e_stack["calls"] if c[0] == "add")
        assert right_call[1]["a"] == 1
        # merge received x="L", y=3 (1+2)
        merge_call = next(c for c in e2e_stack["calls"] if c[0] == "join")
        assert merge_call[1]["x"] == "L"
        assert merge_call[1]["y"] == 3
        # final received the joined string
        final_call = next(c for c in e2e_stack["calls"] if c[0] == "final")
        assert final_call[1]["trigger"] == "L::3"

    def test_yaml_defined_meta_skill_runs(self, e2e_stack):
        """A yaml-defined MetaSkill compiles and runs end-to-end."""
        yaml_text = """
name: tiny-pipe
kind: skill_cluster
steps:
  - node_id: a
    skill: echo
    args: { value: "hello" }
  - node_id: b
    skill: final
    args: { trigger: "{a.output.echoed}" }
    depends_on: [a]
"""
        meta = meta_skill_from_yaml_text(yaml_text)
        traj = _run_meta_skill(e2e_stack, meta)

        assert traj.outcome.success
        assert len(traj.steps) == 2
        b_call = e2e_stack["calls"][1]
        assert b_call[0] == "final"
        assert b_call[1]["trigger"] == "hello"

    def test_step_failure_aborts_pipeline(self, e2e_stack):
        """A failing step must NOT silently succeed; the trajectory
        must report failure and downstream steps must not run.
        """
        meta = MetaSkill(
            name="e2e-failure",
            steps=(
                MetaStep(node_id="a", skill_ref="boom", args_template={"reason": "test"}),
                MetaStep(
                    node_id="b",
                    skill_ref="final",
                    args_template={"trigger": "should not run"},
                    depends_on=("a",),
                ),
            ),
        )
        traj = _run_meta_skill(e2e_stack, meta)

        assert not traj.outcome.success
        # Only the failed step is recorded; downstream steps are
        # not attempted by the runtime (no Step record created).
        assert len(traj.steps) == 1
        # The failed step is node "a" with a RuntimeError
        failed = traj.steps[0]
        assert failed.node_id == "a"
        assert failed.result is not None
        assert failed.result.status == "failed"
        assert failed.result.error_type == "RuntimeError"
        # boom was called and raised
        boom_call = e2e_stack["calls"][0]
        assert boom_call[0] == "boom"
        # final was NEVER called (pipeline stopped at the failure)
        final_calls = [c for c in e2e_stack["calls"] if c[0] == "final"]
        assert final_calls == []

    def test_built_in_meta_skill_compiles_to_valid_task_graph(self, e2e_stack):
        """All 15 shipped 能力包 must produce a valid TaskGraph
        that GraphRuntime's model validators accept (no cycles,
        edge refs resolve). We don't actually run them because the
        real skills (deep-research, docx, slack_post, ...) aren't
        registered in the test registry — but compile-time checks
        catch a huge class of bugs (missing skill_ref, dangling
        depends_on, broken templates, etc.).
        """
        for name, *_ in TestCatalogSnapshot.EXPECTED:
            meta = load_meta_skill(name)
            assert meta is not None
            graph = compile_to_task_graph(meta)
            # TaskGraph.model_validator ran during construction and
            # accepted the graph (no cycles, edges resolve). If we
            # get here, the graph is structurally sound.
            assert graph.task_type == f"meta_skill:{name}"
            assert len(graph.nodes) == len(meta.steps)
            assert graph.budget.tokens > 0
            assert graph.budget.usd > 0


# ── Trigger phrase coverage for the 15 built-in 能力包 ─────────────────────


class TestBuiltinTriggers:
    """Each shipped 能力包 must be reachable from a realistic user
    request via ``match_meta_skill``. The 15 cases below lock the
    ``when_to_use`` triggers so a well-meaning tweak cannot silently
    break routing.

    Each query is hand-crafted to share ≥2 tokens with the target
    trigger AND 0 tokens with every other trigger · that way the
    best-match tie-breaker has nothing to do.
    """

    # (capability-package-name, query-that-should-route-here)
    CASES = [
        # ── 3 hand-authored baseline capability packages ──
        ("paper-write", "帮我写一篇 arxiv 论文"),
        ("code-review", "review my PR diff"),
        ("daily-brief", "今天的每日简报"),
        # ── 12 added in the catalog roll-out ──
        ("earnings-deep-dive", "本周财报业绩点评"),
        ("morning-trader", "今日盘前操盘手早报"),
        ("incident-postmortem", "昨晚故障 RCA 复盘"),
        ("lbo-memo", "LBO 杠杆收购 备忘录"),
        ("data-to-deck", "月度数据汇报 PPT"),
        ("idea-to-launch", "创业 0 到 1 上线"),
        ("customer-outreach", "客户销售 BD 触达"),
        ("ma-deal-package", "M&A 收并购 merger"),
        ("fixed-income-monitor", "国债利率久期日报"),
        ("fund-pitch-deck", "一级市场融资路演 pitch"),
        ("bug-hunt", "安全审计 pentest 漏洞"),
        ("compliance-pack", "KYC 合规合同审"),
        # ── 2 mobile capability packages ──
        ("mobile-automate", "帮我用手机自动操作 app 点击按钮"),
        ("mobile-browser", "在安卓浏览器上抓取网页 反爬"),
    ]

    @pytest.mark.parametrize("name,query", CASES)
    def test_query_routes_to_correct_meta_skill(self, name, query):
        """Real user query must hit the intended 能力包 (best-match)."""
        meta = match_meta_skill(query)
        assert meta is not None, f"no match for {query!r}"
        assert meta.name == name, f"{query!r} routed to {meta.name!r}, expected {name!r}"

    def test_all_packs_are_reachable(self):
        """Sanity: every EXPECTED row in TestCatalogSnapshot must
        appear at least once in TestBuiltinTriggers.CASES.
        """
        from tests.test_meta_skill import TestCatalogSnapshot  # noqa: PLC0415

        expected = {name for name, _, _ in TestCatalogSnapshot.EXPECTED}
        routed = {name for name, _ in self.CASES}
        missing = expected - routed
        assert not missing, f"these shipped packs have no trigger test: {sorted(missing)}"

    def test_unrelated_query_returns_none(self):
        """Things that share no tokens with any trigger should NOT
        be force-routed to the most-generic pack.
        """
        # "play some music and dance" → 0 overlap with any trigger
        assert match_meta_skill("play some music and dance") is None
        # Pure punctuation / whitespace
        assert match_meta_skill("!@#$%") is None
        assert match_meta_skill("---") is None

    def test_no_double_routing_for_disambiguating_queries(self):
        """When two packs could match, the more-specific one wins.

        Both ``bug-hunt`` and ``code-review`` have security-adjacent
        triggers. A pentest-style query should land on bug-hunt,
        not code-review.
        """
        meta = match_meta_skill("安全 pentest 漏洞扫描")
        assert meta is not None
        assert meta.name == "bug-hunt"

    def test_builtin_examples_have_kind(self):
        """The 15 hand-authored YAMLs (3 baseline + 12 added in the
        catalog roll-out) should all advertise themselves as 能力包
        (skill_cluster) so the UI shows the right label.
        """
        expected = {
            "paper-write",
            "code-review",
            "daily-brief",
            "earnings-deep-dive",
            "morning-trader",
            "incident-postmortem",
            "lbo-memo",
            "data-to-deck",
            "idea-to-launch",
            "customer-outreach",
            "ma-deal-package",
            "fixed-income-monitor",
            "fund-pitch-deck",
            "bug-hunt",
            "compliance-pack",
        }
        for name in expected:
            meta = load_meta_skill(name)
            assert meta is not None, f"{name} not found in meta_skills/"
            assert meta.kind == "skill_cluster", f"{name} should declare kind: skill_cluster"


# ── Built-in catalog snapshot (能力包 index) ─────────────────────


class TestCatalogSnapshot:
    """Lock the on-disk 能力包 catalog so a YAML refactor / rename
    cannot silently drop or duplicate a workflow template.

    The snapshot is a *whitelist* — every name we ship is asserted
    to (a) exist, (b) be valid YAML, (c) carry kind=skill_cluster,
    (d) have ≥2 steps, and (e) be reachable through ``list_meta_skills``.
    """

    EXPECTED = (
        # ── 3 hand-authored baseline capability packages ──
        ("paper-write", 8, ["research", "writing", "latex"]),
        ("code-review", 5, ["engineering", "code", "security"]),
        ("daily-brief", 5, ["productivity", "writing", "briefing"]),
        # ── 12 added in the catalog roll-out ──
        ("earnings-deep-dive", 5, ["finance", "research", "writing"]),
        ("morning-trader", 8, ["finance", "trading", "briefing"]),
        ("incident-postmortem", 5, ["sre", "devops", "writing"]),
        ("lbo-memo", 5, ["finance", "modeling", "memo"]),
        ("data-to-deck", 4, ["data", "presentation", "productivity"]),
        ("idea-to-launch", 4, ["product", "design", "growth"]),
        ("customer-outreach", 4, ["sales", "crm", "writing"]),
        ("ma-deal-package", 4, ["finance", "modeling", "deal"]),
        ("fixed-income-monitor", 5, ["finance", "fixed_income", "briefing"]),
        ("fund-pitch-deck", 4, ["finance", "fundraising", "presentation"]),
        ("bug-hunt", 4, ["security", "code", "audit"]),
        ("compliance-pack", 4, ["legal", "compliance", "risk"]),
        # ── 2 mobile capability packages ──
        ("mobile-automate", 4, ["mobile", "android", "automation"]),
        ("mobile-browser", 4, ["mobile", "android", "browser"]),
    )

    def test_catalog_count(self):
        listed = {e["name"] for e in list_meta_skills()}
        expected = {name for name, _, _ in self.EXPECTED}
        missing = expected - listed
        extra = listed - expected
        assert not missing, f"missing from meta_skills/: {sorted(missing)}"
        assert not extra, f"unexpected meta_skills/ entries: {sorted(extra)}"

    def test_each_loaded_meta_skill_is_well_formed(self):
        for name, min_steps, expected_affinity in self.EXPECTED:
            meta = load_meta_skill(name)
            assert meta is not None, f"{name} failed to load"
            assert meta.kind == "skill_cluster", f"{name} should be a 能力包 (skill_cluster)"
            assert len(meta.steps) >= min_steps, (
                f"{name} should have ≥{min_steps} steps, got {len(meta.steps)}"
            )
            # affinity should overlap (loose check: ≥1 common tag)
            assert set(meta.affinity) & set(expected_affinity), (
                f"{name} affinity {list(meta.affinity)} should overlap with {expected_affinity}"
            )

    def test_list_meta_skills_shape(self):
        for entry in list_meta_skills():
            assert set(entry) >= {
                "name",
                "file",
                "description",
                "steps",
                "kind",
                "display_name",
            }, f"malformed list entry: {entry}"
            assert entry["kind"] == "skill_cluster"
            assert entry["display_name"] == "能力包"

    def test_display_name_helper(self):
        assert display_name_for_kind("skill_cluster") == "能力包"
        assert display_name_for_kind("recipe") == "配方"
        assert display_name_for_kind("macro") == "宏"
        # Unknown kind → fallback
        assert display_name_for_kind("made_up_kind") == "Made Up Kind"
