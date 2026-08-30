"""
Regression · ``LLMPlanner.plan()`` must preserve explicit ``depends_on``
from LLM output through ``_validate_nodes()`` into ``_extract_edges()``.

Pre-fix: ``_validate_nodes()`` returned ``{"skill", "args"}`` only,
silently stripping every other field. That meant an LLM plan like::

    [
      {"skill": "read_file",  "args": {...}},
      {"skill": "read_file",  "args": {...}},
      {"skill": "count_words","args": {...}, "depends_on": [0, 1]}
    ]

...reached ``_extract_edges()`` as three depless dicts · edges fell
back to linear inference · swarm ``split_strategy="topo_layers"``
degenerated to "single" · all parallelism the LLM expressed was
lost.

Post-fix: ``depends_on`` survives validation (with light normalization
· accept ints or ``"nN"``-style ids · drop stray types rather than
crashing on slightly-off LLM output).
"""

from __future__ import annotations

import json


def _make_planner(reg, response_text):
    """Shared helper · LLMPlanner needs a ContextComposer parameter."""
    from runtime.core.cerebrum.llm_planner import LLMPlanner
    from runtime.memory.hemolymph import ContextComposer
    from runtime.memory.journal import InMemoryJournal
    from runtime.sensing.model_router import MockModelRouter

    composer = ContextComposer(journal=InMemoryJournal(), registry=reg)
    return LLMPlanner(
        router=MockModelRouter(response=response_text),
        registry=reg,
        composer=composer,
    )


# ═══════════════════════════════════════════════════════════
# _validate_nodes preserves depends_on
# ═══════════════════════════════════════════════════════════


class TestValidateNodes:
    def test_depends_on_survives_validation(self):
        from runtime.execution.all_skills import register_base
        from runtime.execution.suckers import SkillRegistry

        reg = SkillRegistry()
        register_base(reg)
        planner = _make_planner(reg, "{}")

        raw = [
            {"skill": "read_file", "args": {}},
            {"skill": "read_file", "args": {}},
            {"skill": "count_words", "args": {}, "depends_on": [0, 1]},
        ]
        out = planner._validate_nodes(raw)

        assert len(out) == 3
        assert "depends_on" not in out[0]
        assert "depends_on" not in out[1]
        assert out[2]["depends_on"] == [0, 1]

    def test_empty_depends_on_preserved_as_explicit_signal(self):
        """``depends_on: []`` is the explicit "no deps" signal ·
        must survive validation so ``_extract_edges`` can tell it
        apart from "field absent"."""
        from runtime.execution.all_skills import register_base
        from runtime.execution.suckers import SkillRegistry

        reg = SkillRegistry()
        register_base(reg)
        planner = _make_planner(reg, "{}")

        raw = [
            {"skill": "read_file", "args": {}},
            {"skill": "count_words", "args": {}, "depends_on": []},
        ]
        out = planner._validate_nodes(raw)
        assert "depends_on" in out[1]
        assert out[1]["depends_on"] == []

    def test_none_depends_on_dropped(self):
        """``depends_on: null`` from a malformed LLM reply is dropped
        rather than treated as explicit empty."""
        from runtime.execution.all_skills import register_base
        from runtime.execution.suckers import SkillRegistry

        reg = SkillRegistry()
        register_base(reg)
        planner = _make_planner(reg, "{}")

        raw = [
            {"skill": "read_file", "args": {}, "depends_on": None},
        ]
        out = planner._validate_nodes(raw)
        assert "depends_on" not in out[0]

    def test_non_list_depends_on_ignored(self):
        from runtime.execution.all_skills import register_base
        from runtime.execution.suckers import SkillRegistry

        reg = SkillRegistry()
        register_base(reg)
        planner = _make_planner(reg, "{}")

        raw = [{"skill": "read_file", "args": {}, "depends_on": "0"}]
        out = planner._validate_nodes(raw)
        assert "depends_on" not in out[0]

    def test_string_node_ids_accepted(self):
        """LLM sometimes emits ``"n0"``-style ids · _extract_edges
        handles both forms · validation must not strip them."""
        from runtime.execution.all_skills import register_base
        from runtime.execution.suckers import SkillRegistry

        reg = SkillRegistry()
        register_base(reg)
        planner = _make_planner(reg, "{}")

        raw = [
            {"skill": "read_file", "args": {}},
            {"skill": "count_words", "args": {}, "depends_on": ["n0"]},
        ]
        out = planner._validate_nodes(raw)
        assert out[1]["depends_on"] == ["n0"]


# ═══════════════════════════════════════════════════════════
# End-to-end · real plan() path builds correct edges
# ═══════════════════════════════════════════════════════════


class TestPlanEndToEnd:
    def test_explicit_dag_from_plan(self):
        """The whole round-trip · LLM reply → plan() → TaskGraph
        edges · must honor ``depends_on``. Without the fix this
        test falls through to linear-fallback edges (0→1→2)."""
        from runtime.execution.all_skills import register_base
        from runtime.execution.suckers import SkillRegistry
        from runtime.platform.models import ParsedIntent

        reg = SkillRegistry()
        register_base(reg)

        # Two leaves feed into one sink · expected edges: 0→2, 1→2
        # (NOT linear 0→1→2 which fallback would produce).
        # All three nodes declare depends_on explicitly · that's the
        # protocol a DAG-aware LLM uses. Leaf nodes pass depends_on=[]
        # to signal "no parents" · without that, Pass 3's linear
        # fallback would still add 0→1.
        llm_plan = {
            "reasoning": "parallel read then count",
            "nodes": [
                {"skill": "read_file", "args": {"path": "/a"}, "depends_on": []},
                {"skill": "read_file", "args": {"path": "/b"}, "depends_on": []},
                {"skill": "count_words", "args": {}, "depends_on": [0, 1]},
            ],
        }
        planner = _make_planner(reg, json.dumps(llm_plan))

        graph = planner.plan(
            ParsedIntent(
                raw="read two files and count words",
                intent_type="command",
                normalized_goal="read two files and count words",
            )
        )

        # Materialize edge pairs (source_index → dest_index)
        node_ids = [n.node_id for n in graph.nodes]
        assert node_ids == ["n0", "n1", "n2"]
        edge_pairs = {(node_ids.index(e.from_node), node_ids.index(e.to_node)) for e in graph.edges}
        # Must include 0→2 AND 1→2; must NOT include 0→1 (linear
        # fallback signature).
        assert (0, 2) in edge_pairs
        assert (1, 2) in edge_pairs
        assert (0, 1) not in edge_pairs
