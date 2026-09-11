"""
Regression tests for ``_extract_edges`` — the three-signal edge
builder that replaced the unconditional linear chain in LLMPlanner.

Context (review claim ③)
------------------------

Pre-2026-04 the planner unconditionally wrote
``edges = [n0→n1, n1→n2, ...]`` regardless of the LLM's plan shape.
A swarm using ``split_strategy="topo_layers"`` would therefore see
a linear chain and execute serially — killing all parallelism the
LLM could have expressed.

Fix: three signals, priority order:

    1. Explicit ``depends_on`` on the plan node (most expressive).
    2. Template-reference scan of ``{nX.key}`` patterns in args
       (bridges existing prompts that already use template syntax).
    3. Linear fallback for orphan nodes so a valid serial plan is
       always produced (preserves safe default).

These tests pin each signal + interactions + cycle detection.
"""

from __future__ import annotations

import pytest

from runtime.core.cerebrum.llm_planner import (
    _extract_edges,
    _has_cycle,
    _normalize_node_ref,
)
from runtime.core.cerebrum.planner import PlannerError


def _edge_pairs(edges):
    return [(e.from_node, e.to_node) for e in edges]


# ═══════════════════════════════════════════════════════════
# Helpers (pure functions)
# ═══════════════════════════════════════════════════════════


class TestNormalizeNodeRef:
    def test_int_zero(self):
        assert _normalize_node_ref(0) == "n0"

    def test_int_positive(self):
        assert _normalize_node_ref(3) == "n3"

    def test_int_negative_rejected(self):
        assert _normalize_node_ref(-1) is None

    def test_string_nN(self):  # noqa: N802 — covers both 'n' and 'N' prefixes
        assert _normalize_node_ref("n2") == "n2"

    def test_string_uppercase_N(self):  # noqa: N802 — covers uppercase-N prefix
        assert _normalize_node_ref("N2") == "n2"

    def test_string_just_digit(self):
        assert _normalize_node_ref("2") == "n2"

    def test_unknown_form_rejected(self):
        assert _normalize_node_ref("foo") is None
        assert _normalize_node_ref("n_0") is None
        assert _normalize_node_ref(None) is None


class TestHasCycle:
    def test_empty(self):
        assert not _has_cycle(0, set())

    def test_linear_no_cycle(self):
        assert not _has_cycle(3, {("n0", "n1"), ("n1", "n2")})

    def test_diamond_no_cycle(self):
        assert not _has_cycle(
            4,
            {("n0", "n1"), ("n0", "n2"), ("n1", "n3"), ("n2", "n3")},
        )

    def test_self_loop(self):
        assert _has_cycle(2, {("n0", "n0")})

    def test_two_node_cycle(self):
        assert _has_cycle(2, {("n0", "n1"), ("n1", "n0")})

    def test_three_node_cycle(self):
        assert _has_cycle(3, {("n0", "n1"), ("n1", "n2"), ("n2", "n0")})


# ═══════════════════════════════════════════════════════════
# _extract_edges · full behavior
# ═══════════════════════════════════════════════════════════


class TestExtractEdgesExplicit:
    def test_depends_on_int_indices(self):
        nodes = [
            {"skill": "a"},
            {"skill": "b", "depends_on": [0]},
            {"skill": "c", "depends_on": [0, 1]},
        ]
        pairs = _edge_pairs(_extract_edges(nodes, 3))
        assert ("n0", "n1") in pairs
        assert ("n0", "n2") in pairs
        assert ("n1", "n2") in pairs

    def test_depends_on_string_refs(self):
        nodes = [
            {"skill": "a"},
            {"skill": "b", "depends_on": ["n0"]},
        ]
        pairs = _edge_pairs(_extract_edges(nodes, 2))
        assert pairs == [("n0", "n1")]

    def test_parallel_siblings(self):
        """Key case: n1 and n2 both depend on n0 and nothing else.
        Under the old linear-edges code, n2 would wait on n1 despite
        having no data dependency on it — killing parallelism."""
        nodes = [
            {"skill": "a"},
            {"skill": "b", "depends_on": [0]},
            {"skill": "c", "depends_on": [0]},
        ]
        pairs = _edge_pairs(_extract_edges(nodes, 3))
        assert ("n0", "n1") in pairs
        assert ("n0", "n2") in pairs
        # Critically, NO n1→n2 edge — that was the pre-fix regression.
        assert ("n1", "n2") not in pairs

    def test_invalid_depends_on_dropped(self):
        """LLM typo ("n99", self-ref, string "foo") must not crash
        the plan. Invalid entries are silently dropped and the node
        falls through to pass 3 (linear fallback)."""
        nodes = [
            {"skill": "a"},
            {"skill": "b", "depends_on": [1, "n99", "self", -1]},
        ]
        pairs = _edge_pairs(_extract_edges(nodes, 2))
        # All invalid → linear fallback kicks in.
        assert pairs == [("n0", "n1")]


class TestExtractEdgesTemplateInference:
    def test_template_ref_in_arg_creates_edge(self):
        nodes = [
            {"skill": "read", "args": {"path": "foo"}},
            {"skill": "parse", "args": {"content": "{n0.content}"}},
        ]
        pairs = _edge_pairs(_extract_edges(nodes, 2))
        assert pairs == [("n0", "n1")]

    def test_multi_template_refs_multi_edges(self):
        """Template scan creates edges n0→n2 AND n1→n2. n1 has no
        ``depends_on`` field at all (not even empty) and no template
        pointing AT it · fallback treats it as orphan and adds
        n0→n1 for safety. To opt out of this fallback, the LLM
        emits ``depends_on: []`` explicitly · see
        ``test_explicit_empty_depends_on_for_parallelism``."""
        nodes = [
            {"skill": "read_a"},
            {"skill": "read_b"},
            {
                "skill": "merge",
                "args": {"a": "{n0.data}", "b": "{n1.data}"},
            },
        ]
        pairs = _edge_pairs(_extract_edges(nodes, 3))
        assert ("n0", "n2") in pairs
        assert ("n1", "n2") in pairs
        # n1 has no signal of any kind → linear fallback kicks in
        # to preserve the "always produces a runnable plan" safety
        # property.
        assert ("n0", "n1") in pairs

    def test_explicit_empty_depends_on_for_parallelism(self):
        """``depends_on: []`` is the explicit "no dependencies"
        signal · the linear fallback must respect it and NOT chain
        the node to its previous sibling. Fixed in second-pass
        review after this test initially documented the buggy
        squash-back-to-linear behavior.
        """
        nodes = [
            {"skill": "read_a", "depends_on": []},
            {"skill": "read_b", "depends_on": []},
            {
                "skill": "merge",
                "args": {"a": "{n0.data}", "b": "{n1.data}"},
            },
        ]
        pairs = _edge_pairs(_extract_edges(nodes, 3))
        # Pass 2 (template scan) fans n0+n1 into n2.
        assert ("n0", "n2") in pairs
        assert ("n1", "n2") in pairs
        # Critically: n1 has depends_on=[] → fallback MUST skip it.
        # This was the bug: pre-fix the linear fallback added
        # (n0, n1) anyway because n1's in_degree was 0.
        assert ("n0", "n1") not in pairs
        assert ("n1", "n0") not in pairs

    def test_parallelism_via_diamond_dag(self):
        """The actual way to express `n1 ⫫ n2` is the diamond
        pattern: n0 fans out, n1 + n2 both depend on n0 only, n3
        joins them. Pass 1 catches n0→n1 and n0→n2; pass 3 has no
        orphans because both n1 and n2 now have incoming edges."""
        nodes = [
            {"skill": "split"},
            {"skill": "branch_a", "depends_on": [0]},
            {"skill": "branch_b", "depends_on": [0]},
            {
                "skill": "join",
                "depends_on": [1, 2],
            },
        ]
        pairs = _edge_pairs(_extract_edges(nodes, 4))
        assert ("n0", "n1") in pairs
        assert ("n0", "n2") in pairs
        assert ("n1", "n3") in pairs
        assert ("n2", "n3") in pairs
        # Parallel siblings verified: no n1→n2 edge.
        assert ("n1", "n2") not in pairs
        assert ("n2", "n1") not in pairs

    def test_inline_interpolation_detected(self):
        """``"prefix-{n0.id}-suffix"`` counts as a ref even though
        the template is embedded in a literal string."""
        nodes = [
            {"skill": "src"},
            {"skill": "use", "args": {"path": "/tmp/file-{n0.id}.txt"}},
        ]
        pairs = _edge_pairs(_extract_edges(nodes, 2))
        assert pairs == [("n0", "n1")]


class TestExtractEdgesLinearFallback:
    def test_two_nodes_no_signal(self):
        """Old behavior preserved when plan gives no edge signals."""
        nodes = [{"skill": "a"}, {"skill": "b"}]
        pairs = _edge_pairs(_extract_edges(nodes, 2))
        assert pairs == [("n0", "n1")]

    def test_three_nodes_no_signal(self):
        nodes = [{"skill": "a"}, {"skill": "b"}, {"skill": "c"}]
        pairs = _edge_pairs(_extract_edges(nodes, 3))
        assert pairs == [("n0", "n1"), ("n1", "n2")]

    def test_single_node_no_edges(self):
        nodes = [{"skill": "solo"}]
        assert _extract_edges(nodes, 1) == []

    def test_zero_nodes_no_edges(self):
        assert _extract_edges([], 0) == []


class TestExtractEdgesMixed:
    def test_explicit_plus_template_merged_deduplicated(self):
        """depends_on=[0] + {n0.field} → still just one edge, not two."""
        nodes = [
            {"skill": "src"},
            {
                "skill": "use",
                "args": {"x": "{n0.v}"},
                "depends_on": [0],
            },
        ]
        pairs = _edge_pairs(_extract_edges(nodes, 2))
        assert pairs == [("n0", "n1")]

    def test_explicit_wins_fallback_skipped(self):
        """n2 has explicit depends_on=[0] → should NOT get a fallback
        edge from n1. Linear fallback only applies to orphans."""
        nodes = [
            {"skill": "a"},
            {"skill": "b"},
            {"skill": "c", "depends_on": [0]},
        ]
        pairs = _edge_pairs(_extract_edges(nodes, 3))
        # n1 gets a fallback to n0 (orphan)
        assert ("n0", "n1") in pairs
        # n2 uses its explicit dep only
        assert ("n0", "n2") in pairs
        # no linear junk edge
        assert ("n1", "n2") not in pairs


class TestExtractEdgesCycleDetection:
    def test_explicit_cycle_raises(self):
        nodes = [
            {"skill": "a", "depends_on": [1]},
            {"skill": "b", "depends_on": [0]},
        ]
        with pytest.raises(PlannerError, match="cyclic"):
            _extract_edges(nodes, 2)

    def test_template_cycle_raises(self):
        """Arg template referencing forward (future) node also a
        cycle — a node can't depend on one that follows it if the
        follow-up depends back via ``depends_on``."""
        # This particular arrangement: n1→n0 (template) + n1→n0 via
        # explicit depends_on. No cycle · just "n1 must precede n0".
        # Fallback will not add n0→n1 since n1 has no incoming.
        # Adjusted check: construct a real cycle.
        with pytest.raises(PlannerError, match="cyclic"):
            _extract_edges(
                [
                    {
                        "skill": "a",
                        "args": {"x": "{n2.v}"},  # n2 → n0
                    },
                    {"skill": "b", "depends_on": [0]},  # n0 → n1
                    {"skill": "c", "depends_on": [1]},  # n1 → n2
                ],
                3,
            )
