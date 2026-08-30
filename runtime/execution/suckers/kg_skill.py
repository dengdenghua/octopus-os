from __future__ import annotations

from typing import Any

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.suckers.testing import SkillExpect, SkillTestCase


def _kg_query(
    subject: str = "",
    predicate: str = "",
    object: str = "",
    entity: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    """Query the Knowledge Graph.

    Two modes:
      * SPO query: pass any combination of subject/predicate/object
        (empty = wildcard). Returns matching triples.
      * Neighbors: pass ``entity`` to get all triples where the
        entity appears as subject OR object (1-hop).
    """
    try:
        from runtime.memory.knowledge_graph import KnowledgeGraph  # noqa: F401
    except ImportError:
        return {"error": "knowledge_graph module not available", "triples": []}

    # Get the singleton KG instance from the app state. The KG is
    # created during ``build_from_config`` and stored on the stack;
    # we access it via the module-level default that ``cli.py`` /
    # ``app.py`` seeds at startup. If no KG exists yet, return empty.
    kg = _get_default_kg()
    if kg is None:
        return {"error": "no KG initialized", "triples": []}

    if entity:
        triples = kg.neighbors(entity, hops=1)
    else:
        triples = kg.query(
            subject=subject or None,
            predicate=predicate or None,
            object=object or None,
        )

    results = [
        {
            "subject": t.subject,
            "predicate": t.predicate,
            "object": t.object,
            "confidence": t.confidence,
        }
        for t in triples[:20]
    ]
    return {
        "count": len(triples),
        "triples": results,
        "truncated": len(triples) > 20,
    }


_DEFAULT_KG: Any = None


def set_default_kg(kg: Any) -> None:
    global _DEFAULT_KG
    _DEFAULT_KG = kg


def _get_default_kg() -> Any:
    return _DEFAULT_KG


def register_kg_skill(registry: SkillRegistry) -> int:
    registry.register(
        Skill(
            name="kg_query",
            description=(
                "Query the Knowledge Graph for stored facts. "
                "Pass subject/predicate/object for SPO matching, "
                "or entity for 1-hop neighbors."
            ),
            affinity=["knowledge", "memory"],
            cost_profile="low",
            trusted_source="skill://public/kg_query",
            handler=_kg_query,
            tests=[
                SkillTestCase(
                    name="empty_query_returns_structure",
                    tier="golden",
                    args={},
                    expect=SkillExpect(schema_keys=["count", "triples"]),
                ),
            ],
        )
    )
    return 1
