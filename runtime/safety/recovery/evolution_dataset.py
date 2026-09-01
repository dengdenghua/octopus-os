"""Unified dataset builder for regeneration and prompt evolution.

This module centralizes the few small dataset sources Echo already has
into one place so callers do not need to know whether examples came from
Journal failures, ProposalLedger turn failures, or synthetic augmentation.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from runtime.safety.auth.scope import TenantScope
from runtime.safety.evolution.proposal_ledger import ProposalLedger
from runtime.safety.recovery.tenant_scope import (
    is_legacy_unscoped_event,
    read_learning_events,
)


@dataclass(frozen=True)
class EvolutionExample:
    """One normalized example for evolution/eval."""

    task_input: str
    expected_behavior: str
    source: str = "failure"
    difficulty: str = "medium"
    category: str = "general"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvolutionDataset:
    """Split dataset used by prompt evolution and downstream evals."""

    train: list[EvolutionExample] = field(default_factory=list)
    val: list[EvolutionExample] = field(default_factory=list)
    holdout: list[EvolutionExample] = field(default_factory=list)

    @property
    def all_examples(self) -> list[EvolutionExample]:
        return self.train + self.val + self.holdout

    def save_jsonl(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as fh:
            for example in self.all_examples:
                fh.write(json.dumps(asdict(example), ensure_ascii=False) + "\n")
        return target

    @classmethod
    def load_jsonl(cls, path: str | Path) -> EvolutionDataset:
        examples: list[EvolutionExample] = []
        with Path(path).open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                data = json.loads(line)
                examples.append(
                    EvolutionExample(
                        task_input=str(data.get("task_input") or ""),
                        expected_behavior=str(data.get("expected_behavior") or ""),
                        source=str(data.get("source") or "golden"),
                        difficulty=str(data.get("difficulty") or "medium"),
                        category=str(data.get("category") or "general"),
                        metadata=data.get("metadata")
                        if isinstance(data.get("metadata"), dict)
                        else {},
                    )
                )
        return EvolutionDatasetBuilder()._split(
            [ex for ex in examples if ex.task_input.strip() and ex.expected_behavior.strip()]
        )


@dataclass(frozen=True)
class FailureCluster:
    """Repeated failure pattern used to prioritize reflection."""

    key: str
    count: int
    category: str
    representative_goal: str
    representative_error: str
    sample_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class EvolutionDatasetBuilder:
    """Build datasets from the failure samples Echo already collects."""

    train_ratio: float = 0.5
    val_ratio: float = 0.25
    holdout_ratio: float = 0.25
    synthetic_variants_per_failure: int = 1

    def build_from_failure_samples(
        self,
        failures: list[dict[str, Any]],
        *,
        source_name: str = "failure",
        include_synthetic: bool = True,
    ) -> EvolutionDataset:
        examples: list[EvolutionExample] = []
        for failure in failures:
            normalized = self._normalize_failure(failure, source_name=source_name)
            if normalized is None:
                continue
            examples.append(normalized)
            if include_synthetic:
                examples.extend(self._synthetic_variants(normalized))
        return self._split(examples)

    def build_from_sources(
        self,
        *,
        journal_failures: list[dict[str, Any]] | None = None,
        ledger_failures: list[dict[str, Any]] | None = None,
        include_synthetic: bool = True,
    ) -> EvolutionDataset:
        failures: list[dict[str, Any]] = []
        failures.extend(journal_failures or [])
        failures.extend(ledger_failures or [])
        return self.build_from_failure_samples(
            failures,
            source_name="merged_failure",
            include_synthetic=include_synthetic,
        )

    def build_from_golden_jsonl(self, path: str | Path) -> EvolutionDataset:
        return EvolutionDataset.load_jsonl(path)

    def build_from_journal_successes(
        self,
        journal: Any,
        *,
        recipe_id: str | None = None,
        limit: int = 50,
        scope: TenantScope | None = None,
    ) -> EvolutionDataset:
        """Mine successful tool-chain templates from Journal trajectories.

        Trajectory rows do not currently persist the original user goal.
        Until that schema is widened, these examples preserve successful
        action sequences and recipe scopes rather than user wording.
        """
        try:
            events = read_learning_events(journal, "trajectory", scope=scope)
        except (OSError, TypeError, ValueError, AttributeError):
            return EvolutionDataset()
        examples: list[EvolutionExample] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for event in reversed(events):
            trajectory = getattr(event, "trajectory", None)
            if trajectory is None:
                continue
            outcome = getattr(trajectory, "outcome", None)
            if not bool(getattr(outcome, "success", False)) or bool(
                getattr(outcome, "degraded", False)
            ):
                continue
            rid = str(getattr(trajectory, "recipe_id", None) or "").strip()
            if recipe_id and rid != recipe_id:
                continue
            actions = tuple(
                str(getattr(getattr(step, "action", None), "sucker_id", "") or "").strip()
                for step in (getattr(trajectory, "steps", None) or [])
            )
            actions = tuple(action for action in actions if action)
            if not actions:
                continue
            signature = (rid, actions)
            if signature in seen:
                continue
            seen.add(signature)
            action_chain = " -> ".join(actions)
            recipe_scope = rid or "__unscoped__"
            examples.append(
                EvolutionExample(
                    task_input=f"Preserve successful recipe {recipe_scope}: {action_chain}",
                    expected_behavior=(
                        "Keep this verified tool-chain behavior available while improving "
                        "failure handling. Avoid regressions in the successful path."
                    ),
                    source="journal_success",
                    difficulty="medium",
                    category="successful_tool_chain",
                    metadata={
                        "recipe_id": rid or None,
                        "action_chain": list(actions),
                        "trajectory_id": str(getattr(trajectory, "trajectory_id", "") or ""),
                        "goal_available": False,
                    },
                )
            )
            if len(examples) >= max(1, int(limit)):
                break
        return self._split(examples)

    def build_from_ledger_successes(
        self,
        *,
        ledger_path: Any = "data/proposal_ledger.jsonl",
        limit: int = 50,
        scope: TenantScope | None = None,
    ) -> EvolutionDataset:
        try:
            records = ProposalLedger(ledger_path).query(
                kind="turn_success",
                limit=max(1, int(limit)),
                scope=scope,
            )
        except (OSError, TypeError, ValueError, AttributeError):
            return EvolutionDataset()
        examples: list[EvolutionExample] = []
        seen: set[str] = set()
        for record in reversed(records):
            if scope is None and not is_legacy_unscoped_event(record):
                continue
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            if not _ledger_record_is_clean_success(metadata):
                continue
            goal = str(metadata.get("goal") or "").strip()
            if not goal or goal in seen:
                continue
            seen.add(goal)
            item_counts = metadata.get("item_counts")
            counts = item_counts if isinstance(item_counts, dict) else {}
            examples.append(
                EvolutionExample(
                    task_input=goal,
                    expected_behavior=(
                        "Preserve the behavior that made this turn complete successfully. "
                        "Use it as a positive example while evolving failure handling."
                    ),
                    source="ledger_success",
                    difficulty="medium",
                    category="successful_turn",
                    metadata={
                        "turn_id": metadata.get("turn_id"),
                        "thread_id": metadata.get("thread_id"),
                        "item_counts": counts,
                        "code_change_paths": metadata.get("code_change_paths") or [],
                        "verification_count": metadata.get("verification_count") or 0,
                        "proposal_id": record.proposal_id,
                    },
                )
            )
        return self._split(examples)

    def build_positive_examples(
        self,
        *,
        journal: Any | None = None,
        ledger_path: Any = "data/proposal_ledger.jsonl",
        recipe_id: str | None = None,
        limit: int = 50,
        scope: TenantScope | None = None,
    ) -> EvolutionDataset:
        """Merge real-goal successes with legacy tool-chain successes."""
        ledger_dataset = self.build_from_ledger_successes(
            ledger_path=ledger_path,
            limit=limit,
            scope=scope,
        )
        journal_dataset = (
            self.build_from_journal_successes(
                journal,
                recipe_id=recipe_id,
                limit=limit,
                scope=scope,
            )
            if journal is not None
            else EvolutionDataset()
        )
        examples: list[EvolutionExample] = []
        seen: set[tuple[str, str]] = set()
        for example in [*ledger_dataset.all_examples, *journal_dataset.all_examples]:
            signature = (example.source, example.task_input)
            if signature in seen:
                continue
            seen.add(signature)
            examples.append(example)
            if len(examples) >= max(1, int(limit)):
                break
        return self._split(examples)

    def cluster_failures(
        self,
        failures: list[dict[str, Any]],
    ) -> list[FailureCluster]:
        """Group repeated failure modes using stable, cheap heuristics."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for failure in failures:
            goal = str(failure.get("goal") or "").strip()
            if not goal:
                continue
            category = _failure_repair_category(failure)
            error = str(failure.get("last_error") or failure.get("error") or "").strip()
            error_key = _normalize_cluster_text(error)[:120] or "no_error"
            key = f"{category}:{error_key}"
            grouped.setdefault(key, []).append(failure)

        clusters: list[FailureCluster] = []
        for key, samples in grouped.items():
            first = samples[0]
            ids = tuple(
                str(sample.get("turn_id") or sample.get("proposal_id") or "").strip()
                for sample in samples
                if str(sample.get("turn_id") or sample.get("proposal_id") or "").strip()
            )
            clusters.append(
                FailureCluster(
                    key=key,
                    count=len(samples),
                    category=key.split(":", 1)[0],
                    representative_goal=str(first.get("goal") or "").strip(),
                    representative_error=str(
                        first.get("last_error") or first.get("error") or ""
                    ).strip(),
                    sample_ids=ids,
                )
            )
        return sorted(clusters, key=lambda cluster: (-cluster.count, cluster.key))

    def annotate_failure_clusters(
        self,
        failures: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Attach cluster metadata without mutating caller-owned samples."""
        clusters = self.cluster_failures(failures)
        counts = Counter(cluster.key for cluster in clusters for _ in range(cluster.count))
        annotated: list[dict[str, Any]] = []
        for failure in failures:
            category = _failure_repair_category(failure)
            error = str(failure.get("last_error") or failure.get("error") or "").strip()
            key = f"{category}:{_normalize_cluster_text(error)[:120] or 'no_error'}"
            annotated.append(
                {
                    **failure,
                    "failure_cluster": key,
                    "failure_cluster_count": counts.get(key, 1),
                }
            )
        return annotated

    def _normalize_failure(
        self,
        failure: dict[str, Any],
        *,
        source_name: str,
    ) -> EvolutionExample | None:
        goal = str(failure.get("goal") or "").strip()
        if not goal:
            return None
        error = str(failure.get("last_error") or failure.get("error") or "").strip()
        step_count = int(failure.get("step_count") or 0 or 0)
        source = str(failure.get("source") or source_name or "failure").strip() or "failure"
        category = _failure_repair_category(failure) or source or "general"
        difficulty = "hard" if step_count >= 5 or error else "medium"
        expected = error or "Preserve the original intent and address the observed failure."
        repair_route = str(failure.get("primary_repair_route") or "").strip()
        repair_hint = f" Use repair route `{repair_route}`." if repair_route else ""
        expected_behavior = (
            "Produce a corrected plan/prompt that directly addresses the failure. "
            f"Observed issue: {expected}.{repair_hint}"
        )
        metadata = {
            "goal": goal,
            "last_error": error,
            "step_count": step_count,
            "recipe_id": failure.get("recipe_id"),
            "source": source,
            "failure_source": failure.get("failure_source"),
            "failure_cluster": failure.get("failure_cluster"),
            "failure_cluster_count": int(failure.get("failure_cluster_count") or 1),
            "turn_id": failure.get("turn_id"),
            "thread_id": failure.get("thread_id"),
            "proposal_id": failure.get("proposal_id"),
            "code_change_paths": failure.get("code_change_paths") or [],
            "primary_repair_route": failure.get("primary_repair_route") or "",
            "repair_routes": failure.get("repair_routes") or [],
        }
        return EvolutionExample(
            task_input=goal,
            expected_behavior=expected_behavior,
            source=source,
            difficulty=difficulty,
            category=category,
            metadata=metadata,
        )

    def _synthetic_variants(self, example: EvolutionExample) -> list[EvolutionExample]:
        variants: list[EvolutionExample] = []
        count = max(0, int(self.synthetic_variants_per_failure))
        for idx in range(count):
            variants.append(
                EvolutionExample(
                    task_input=f"{example.task_input} (repair variant {idx + 1})",
                    expected_behavior=(
                        "Explain the minimal corrective change, preserve intent, and avoid "
                        "repeating the original failure mode."
                    ),
                    source=f"{example.source}:synthetic",
                    difficulty=example.difficulty,
                    category=example.category,
                    metadata={**example.metadata, "synthetic_variant": idx + 1},
                )
            )
        return variants

    def _split(self, examples: list[EvolutionExample]) -> EvolutionDataset:
        if not examples:
            return EvolutionDataset()
        train_end = max(1, int(len(examples) * self.train_ratio))
        val_end = train_end + max(1, int(len(examples) * self.val_ratio))
        return EvolutionDataset(
            train=examples[:train_end],
            val=examples[train_end:val_end],
            holdout=examples[val_end:],
        )


def _ledger_record_is_clean_success(metadata: dict[str, Any]) -> bool:
    """Reject warning/degraded completions from the positive corpus."""

    if bool(metadata.get("degraded")):
        return False
    outcome = str(metadata.get("outcome") or metadata.get("result") or "").strip().lower()
    if outcome in {"pass_degraded", "degraded", "completed_with_warning", "partial"}:
        return False
    disposition = str(metadata.get("disposition") or "").strip().lower()
    return disposition not in {"completed_with_warning", "partial"}


def _normalize_cluster_text(text: str) -> str:
    normalized = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", text.lower())
    normalized = re.sub(r"\b\d+\b", "<n>", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _failure_repair_category(failure: dict[str, Any]) -> str:
    route = str(failure.get("primary_repair_route") or "").strip().lower()
    source = str(failure.get("failure_source") or "").strip().lower()
    if route and source in {"verification_failed", "verification_required", ""}:
        return route
    category = str(failure.get("category") or "").strip().lower()
    return source or category or "unclassified"


__all__ = [
    "EvolutionDataset",
    "EvolutionDatasetBuilder",
    "EvolutionExample",
    "FailureCluster",
]
