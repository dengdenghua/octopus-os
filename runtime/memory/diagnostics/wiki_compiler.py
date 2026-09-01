from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WikiPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    updated_at: str = ""


class WikiIndex(BaseModel):
    model_config = ConfigDict(frozen=True)

    pages: list[str] = Field(default_factory=list)
    total_pages: int = 0
    last_compiled: str = ""


class WikiCompiler:
    def __init__(self, output_dir: str = "~/.echo/wiki") -> None:
        self._dir = Path(os.path.expanduser(output_dir))

    def compile_from_journal(self, journal: Any) -> WikiIndex:
        from runtime.safety.recovery import (
            KGUpdater,
            MemoryConsolidator,
            RecipeEvaluator,
            RuleExtractor,
            WorkflowRewriter,
        )

        results: dict[str, Any] = {}

        try:
            results["rules"] = RuleExtractor(journal).extract()
        except Exception:
            results["rules"] = None

        try:
            results["memories"] = MemoryConsolidator(journal).consolidate()
        except Exception:
            results["memories"] = None

        try:
            from runtime.memory.knowledge_graph import KnowledgeGraph

            kg = KnowledgeGraph()
            results["kg"] = KGUpdater(journal, kg).update()
            results["kg_graph"] = kg
        except Exception:
            results["kg"] = None
            results["kg_graph"] = None

        try:
            results["workflow"] = WorkflowRewriter(journal).analyze()
        except Exception:
            results["workflow"] = None

        try:
            results["recipe"] = RecipeEvaluator(journal).evaluate()
        except Exception:
            results["recipe"] = None

        return self.compile_from_reflect(results)

    def compile_from_reflect(self, results: dict[str, Any]) -> WikiIndex:
        self._dir.mkdir(parents=True, exist_ok=True)
        pages: list[str] = []
        now = datetime.now(UTC).isoformat()

        if results.get("rules"):
            pages.append(self._compile_rules(results["rules"], now))

        if results.get("memories"):
            pages.append(self._compile_memories(results["memories"], now))

        if results.get("kg_graph"):
            pages.append(self._compile_kg(results["kg_graph"], now))

        if results.get("workflow"):
            pages.append(self._compile_workflow(results["workflow"], now))

        if results.get("recipe"):
            pages.append(self._compile_recipe(results["recipe"], now))

        self._write_index(pages, now)

        return WikiIndex(
            pages=pages,
            total_pages=len(pages),
            last_compiled=now,
        )

    def _compile_rules(self, report: Any, now: str) -> str:
        title = "Learned Rules"
        lines = [f"# {title}\n"]
        lines.append(f"_Auto-compiled from RuleExtractor · {now}_\n")

        rules = getattr(report, "rules_produced", [])
        if not rules:
            lines.append("No learned rules yet.\n")
        else:
            for i, rule in enumerate(rules, 1):
                lines.append(f"## Rule {i}: {getattr(rule, 'rule_id', f'rule_{i}')}\n")
                lines.append(f"- **Pattern**: {getattr(rule, 'pattern', 'N/A')}")
                lines.append(f"- **Action**: {getattr(rule, 'action', 'N/A')}")
                lines.append(f"- **Confidence**: {getattr(rule, 'confidence', 'N/A')}")
                lines.append(f"- **Hits**: {getattr(rule, 'hit_count', 0)}")
                lines.append("")

        self._write_page("learned-rules", "\n".join(lines), now)
        return title

    def _compile_memories(self, report: Any, now: str) -> str:
        title = "Arm Memories"
        lines = [f"# {title}\n"]
        lines.append(f"_Auto-compiled from MemoryConsolidator · {now}_\n")

        memories = getattr(report, "memories_produced", [])
        if not memories:
            lines.append("No arm memories yet.\n")
        else:
            for mem in memories:
                arm_id = getattr(mem, "arm_id", "unknown")
                strategy = getattr(mem, "strategy_id", "unknown")
                lines.append(f"## {arm_id} / {strategy}\n")
                lines.append(f"- **Success Rate**: {getattr(mem, 'success_rate', 'N/A')}")
                lines.append(f"- **Avg Steps**: {getattr(mem, 'avg_steps', 'N/A')}")
                lines.append(f"- **Avg Cost**: ${getattr(mem, 'avg_cost_usd', 0):.4f}")
                lines.append(f"- **Tier**: {getattr(mem, 'tier', 'N/A')}")
                lines.append("")

        self._write_page("arm-memories", "\n".join(lines), now)
        return title

    def _compile_kg(self, kg: Any, now: str) -> str:
        title = "Knowledge Graph"
        lines = [f"# {title}\n"]
        lines.append(f"_Auto-compiled from KGUpdater · {now}_\n")

        try:
            triples = kg.query()
            if not triples:
                lines.append("No triples yet.\n")
            else:
                lines.append(f"**Total triples**: {len(triples)}\n")
                lines.append("| Subject | Predicate | Object |")
                lines.append("|---------|-----------|--------|")
                for t in triples[:50]:
                    s = getattr(t, "subject", "?")
                    p = getattr(t, "predicate", "?")
                    o = getattr(t, "object", "?")
                    lines.append(f"| {s} | {p} | {o} |")
                if len(triples) > 50:
                    lines.append(f"\n_Showing 50 of {len(triples)} triples_")
        except (ImportError, AttributeError, TypeError, ValueError):
            lines.append("Knowledge graph unavailable.\n")

        self._write_page("knowledge-graph", "\n".join(lines), now)
        return title

    def _compile_workflow(self, report: Any, now: str) -> str:
        title = "Workflow Proposals"
        lines = [f"# {title}\n"]
        lines.append(f"_Auto-compiled from WorkflowRewriter · {now}_\n")

        proposals = getattr(report, "proposals", [])
        if not proposals:
            lines.append("No workflow rewrite proposals yet.\n")
        else:
            for i, prop in enumerate(proposals, 1):
                lines.append(f"## Proposal {i}\n")
                lines.append(f"- **Kind**: {getattr(prop, 'kind', 'N/A')}")
                lines.append(f"- **Severity**: {getattr(prop, 'severity', 'N/A')}")
                lines.append(f"- **Confidence**: {getattr(prop, 'confidence', 'N/A')}")
                reason = getattr(prop, "reason", "")
                if reason:
                    lines.append(f"- **Reason**: {reason}")
                lines.append("")

        self._write_page("workflow-proposals", "\n".join(lines), now)
        return title

    def _compile_recipe(self, report: Any, now: str) -> str:
        title = "Recipe Assessment"
        lines = [f"# {title}\n"]
        lines.append(f"_Auto-compiled from RecipeEvaluator · {now}_\n")

        recipes_found = getattr(report, "recipes_found", 0)
        lines.append(f"**Recipes found**: {recipes_found}\n")

        best = getattr(report, "best", None)
        if best:
            lines.append("## Best Recipe\n")
            lines.append(f"- **ID**: {getattr(best, 'recipe_id', 'N/A')}")
            lines.append(f"- **Uses**: {getattr(best, 'uses', 0)}")
            lines.append(f"- **Success Rate**: {getattr(best, 'success_rate', 'N/A')}")
            lines.append(f"- **Avg Cost**: ${getattr(best, 'avg_cost_usd', 0):.4f}")
            lines.append(f"- **Verdict**: {getattr(best, 'verdict', 'N/A')}")

        self._write_page("recipe-assessment", "\n".join(lines), now)
        return title

    def _write_page(self, name: str, content: str, now: str) -> None:
        path = self._dir / f"{name}.md"
        path.write_text(content, encoding="utf-8")

    def _write_index(self, pages: list[str], now: str) -> None:
        lines = ["# Echo Agent Wiki\n"]
        lines.append(f"_Last compiled: {now}_\n")
        lines.append("## Pages\n")
        page_files = sorted(self._dir.glob("*.md"))
        for pf in page_files:
            if pf.name == "INDEX.md":
                continue
            title = pf.stem.replace("-", " ").title()
            lines.append(f"- [{title}]({pf.name})")
        lines.append("")

        index_path = self._dir / "INDEX.md"
        index_path.write_text("\n".join(lines), encoding="utf-8")
