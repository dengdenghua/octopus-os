"""Dense coverage for the wiki compiler pipeline (audit Q-05)."""

from __future__ import annotations

from types import SimpleNamespace

from runtime.memory.diagnostics.wiki_compiler import WikiCompiler


def _compiler(tmp_path) -> WikiCompiler:
    return WikiCompiler(output_dir=str(tmp_path))


def _rules_report():
    return SimpleNamespace(
        rules_produced=[
            SimpleNamespace(rule_id="r1", pattern="p1", action="a1", confidence=0.9, hit_count=3),
        ]
    )


def _memories_report():
    return SimpleNamespace(
        memories_produced=[
            SimpleNamespace(
                arm_id="arm1",
                strategy_id="s1",
                success_rate=0.8,
                avg_steps=3,
                avg_cost_usd=0.01,
                tier="silver",
            ),
        ]
    )


def _workflow_report():
    return SimpleNamespace(
        proposals=[
            SimpleNamespace(kind="split", severity="high", confidence=0.5, reason="too big"),
        ]
    )


def _recipe_report():
    return SimpleNamespace(
        recipes_found=2,
        best=SimpleNamespace(
            recipe_id="x1", uses=3, success_rate=0.9, avg_cost_usd=0.02, verdict="keep"
        ),
    )


class _Kg:
    def query(self):
        return [
            SimpleNamespace(subject="a", predicate="p", object="b"),
            SimpleNamespace(subject="c", predicate="q", object="d"),
        ]


def test_compile_from_reflect_all_sections(tmp_path) -> None:
    comp = _compiler(tmp_path)
    index = comp.compile_from_reflect(
        {
            "rules": _rules_report(),
            "memories": _memories_report(),
            "kg_graph": _Kg(),
            "workflow": _workflow_report(),
            "recipe": _recipe_report(),
        }
    )
    assert index.total_pages == 5
    assert set(index.pages) == {
        "Learned Rules",
        "Arm Memories",
        "Knowledge Graph",
        "Workflow Proposals",
        "Recipe Assessment",
    }
    assert (tmp_path / "learned-rules.md").exists()
    assert (tmp_path / "arm-memories.md").exists()
    assert (tmp_path / "knowledge-graph.md").exists()
    assert (tmp_path / "workflow-proposals.md").exists()
    assert (tmp_path / "recipe-assessment.md").exists()
    assert (tmp_path / "INDEX.md").exists()
    assert "Rule 1: r1" in (tmp_path / "learned-rules.md").read_text()
    assert "| a | p | b |" in (tmp_path / "knowledge-graph.md").read_text()


def test_compile_empty_results(tmp_path) -> None:
    comp = _compiler(tmp_path)
    index = comp.compile_from_reflect({})
    assert index.total_pages == 0
    assert index.pages == []
    assert (tmp_path / "INDEX.md").exists()


def test_compile_empty_sections(tmp_path) -> None:
    comp = _compiler(tmp_path)
    index = comp.compile_from_reflect(
        {
            "rules": SimpleNamespace(rules_produced=[]),
            "memories": SimpleNamespace(memories_produced=[]),
            "kg_graph": SimpleNamespace(query=lambda: []),
            "workflow": SimpleNamespace(proposals=[]),
            "recipe": SimpleNamespace(recipes_found=0, best=None),
        }
    )
    assert index.total_pages == 5
    assert "No learned rules yet." in (tmp_path / "learned-rules.md").read_text()
    assert "No triples yet." in (tmp_path / "knowledge-graph.md").read_text()
    assert "No arm memories yet." in (tmp_path / "arm-memories.md").read_text()


def test_compile_kg_failure(tmp_path) -> None:
    class _BrokenKg:
        def query(self):
            raise ValueError("no kg backend")

    comp = _compiler(tmp_path)
    index = comp.compile_from_reflect({"kg_graph": _BrokenKg()})
    assert index.total_pages == 1
    assert "Knowledge graph unavailable." in (tmp_path / "knowledge-graph.md").read_text()


def test_compile_from_journal_degrades(monkeypatch, tmp_path) -> None:
    import runtime.safety.recovery as rec

    class _Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("recovery subsystem down")

    for name in (
        "RuleExtractor",
        "MemoryConsolidator",
        "KGUpdater",
        "WorkflowRewriter",
        "RecipeEvaluator",
    ):
        monkeypatch.setattr(rec, name, _Boom)
    comp = _compiler(tmp_path)
    index = comp.compile_from_journal(journal=object())
    assert index.total_pages == 0
    assert (tmp_path / "INDEX.md").exists()


def test_compile_from_journal_with_stubs(monkeypatch, tmp_path) -> None:
    class _RuleExtractor:
        def __init__(self, journal):
            self.journal = journal

        def extract(self):
            return _rules_report()

    class _MemoryConsolidator:
        def __init__(self, journal):
            self.journal = journal

        def consolidate(self):
            return _memories_report()

    class _KGUpdater:
        def __init__(self, journal, kg):
            self.kg = kg

        def update(self):
            return None

    class _WorkflowRewriter:
        def __init__(self, journal):
            self.journal = journal

        def analyze(self):
            return _workflow_report()

    class _RecipeEvaluator:
        def __init__(self, journal):
            self.journal = journal

        def evaluate(self):
            return _recipe_report()

    import runtime.memory.knowledge_graph as kg_mod
    import runtime.safety.recovery as rec

    monkeypatch.setattr(rec, "RuleExtractor", _RuleExtractor)
    monkeypatch.setattr(rec, "MemoryConsolidator", _MemoryConsolidator)
    monkeypatch.setattr(rec, "KGUpdater", _KGUpdater)
    monkeypatch.setattr(rec, "WorkflowRewriter", _WorkflowRewriter)
    monkeypatch.setattr(rec, "RecipeEvaluator", _RecipeEvaluator)

    class _FakeKg:
        def query(self):
            return []

    monkeypatch.setattr(kg_mod, "KnowledgeGraph", lambda: _FakeKg())

    comp = _compiler(tmp_path)
    index = comp.compile_from_journal(journal=object())
    assert index.total_pages == 5
    assert (tmp_path / "recipe-assessment.md").exists()

