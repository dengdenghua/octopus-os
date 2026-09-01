from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PromptBudgetConfig:
    # RuleExtractor → learned_rules_section
    learned_rules_max_chars: int = 2000

    # MemoryConsolidator → learned_memories_section
    learned_memories_max_chars: int = 1500
    learned_memories_only_hot: bool = False

    # KnowledgeGraph → kg_section
    kg_max_chars: int = 1500
    kg_max_triples: int = 15
    kg_min_confidence: float = 0.5

    # ContextComposer
    compress_order: tuple[str, ...] = ("history", "memory", "suckers", "system")

    @classmethod
    def from_dict(cls, data: dict) -> PromptBudgetConfig:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


DEFAULT_BUDGET = PromptBudgetConfig()
