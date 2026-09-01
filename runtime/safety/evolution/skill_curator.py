from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

_LOG = logging.getLogger("echo.evolution.skill_curator")


class SkillState(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"
    PINNED = "pinned"


class CuratorAction(StrEnum):
    MERGE = "merge"
    ARCHIVE = "archive"
    UPGRADE = "upgrade"
    SPLIT = "split"
    KEEP = "keep"
    DEMOTE = "demote"


@dataclass
class SkillInfo:
    name: str
    description: str
    state: SkillState
    use_count: int
    created_at: str
    last_activity_at: str | None
    content_hash: str
    tags: list[str] = field(default_factory=list)
    category: str | None = None


@dataclass
class CuratorProposal:
    action: CuratorAction
    target: str
    umbrella: str | None = None
    reason: str = ""
    confidence: str = "medium"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CuratorConfig:
    stale_after_days: int = 30
    archive_after_days: int = 90
    similarity_threshold: float = 0.6
    min_cluster_size: int = 2
    dry_run: bool = True


@dataclass
class CuratorReport:
    ts: str
    total_skills: int
    proposals: list[CuratorProposal]
    clusters_found: int
    actions_summary: dict[str, int]


class SkillCurator:
    def __init__(self, config: CuratorConfig | None = None) -> None:
        self.config = config or CuratorConfig()

    def analyze(self, skills: list[SkillInfo]) -> CuratorReport:
        proposals: list[CuratorProposal] = []
        active = [s for s in skills if s.state != SkillState.ARCHIVED]

        state_proposals = self._apply_state_transitions(active)
        proposals.extend(state_proposals)

        clusters = self._find_clusters(active)
        for cluster_name, members in clusters.items():
            if len(members) < self.config.min_cluster_size:
                continue
            cluster_proposals = self._consolidate_cluster(cluster_name, members)
            proposals.extend(cluster_proposals)

        upgrade_proposals = self._find_upgrade_candidates(active)
        proposals.extend(upgrade_proposals)

        actions_summary: dict[str, int] = {}
        for p in proposals:
            key = p.action.value
            actions_summary[key] = actions_summary.get(key, 0) + 1

        return CuratorReport(
            ts=datetime.now().isoformat(timespec="seconds"),
            total_skills=len(skills),
            proposals=proposals,
            clusters_found=len(clusters),
            actions_summary=actions_summary,
        )

    def _apply_state_transitions(self, skills: list[SkillInfo]) -> list[CuratorProposal]:
        from datetime import datetime as _dt

        proposals: list[CuratorProposal] = []
        now = _dt.now(UTC)
        stale_cutoff = now - __import__("datetime").timedelta(days=self.config.stale_after_days)
        archive_cutoff = now - __import__("datetime").timedelta(days=self.config.archive_after_days)

        for s in skills:
            if s.state == SkillState.PINNED:
                continue
            last = s.last_activity_at or s.created_at
            try:
                anchor = _dt.fromisoformat(last.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=UTC)

            if anchor <= archive_cutoff and s.state != SkillState.ARCHIVED:
                proposals.append(
                    CuratorProposal(
                        action=CuratorAction.ARCHIVE,
                        target=s.name,
                        reason=f"no activity for {self.config.archive_after_days}+ days",
                        confidence="high",
                    )
                )
            elif anchor <= stale_cutoff and s.state == SkillState.ACTIVE:
                proposals.append(
                    CuratorProposal(
                        action=CuratorAction.DEMOTE,
                        target=s.name,
                        reason=f"no activity for {self.config.stale_after_days}+ days · mark stale",
                        confidence="high",
                    )
                )

        return proposals

    def _find_clusters(self, skills: list[SkillInfo]) -> dict[str, list[SkillInfo]]:
        clusters: dict[str, list[SkillInfo]] = {}
        for s in skills:
            if s.state == SkillState.PINNED:
                continue
            prefix = self._extract_prefix(s.name)
            if prefix:
                clusters.setdefault(prefix, []).append(s)
            if s.category:
                cat_key = f"cat:{s.category}"
                clusters.setdefault(cat_key, []).append(s)
        return {k: v for k, v in clusters.items() if len(v) >= self.config.min_cluster_size}

    def _consolidate_cluster(
        self,
        cluster_name: str,
        members: list[SkillInfo],
    ) -> list[CuratorProposal]:
        proposals: list[CuratorProposal] = []
        if len(members) < 2:
            return proposals

        sorted_members = sorted(members, key=lambda s: s.use_count, reverse=True)
        umbrella = sorted_members[0]
        siblings = sorted_members[1:]

        for sibling in siblings:
            similarity = self._compute_similarity(umbrella, sibling)
            if similarity >= self.config.similarity_threshold:
                proposals.append(
                    CuratorProposal(
                        action=CuratorAction.MERGE,
                        target=sibling.name,
                        umbrella=umbrella.name,
                        reason=(
                            f"cluster '{cluster_name}': similarity={similarity:.2f} "
                            f"with umbrella '{umbrella.name}' (use_count={umbrella.use_count})"
                        ),
                        confidence="medium" if similarity < 0.8 else "high",
                        metadata={"similarity": round(similarity, 3)},
                    )
                )
            else:
                proposals.append(
                    CuratorProposal(
                        action=CuratorAction.KEEP,
                        target=sibling.name,
                        reason=f"cluster '{cluster_name}' but low similarity ({similarity:.2f}) to umbrella",
                        confidence="medium",
                        metadata={"similarity": round(similarity, 3)},
                    )
                )

        return proposals

    def _find_upgrade_candidates(self, skills: list[SkillInfo]) -> list[CuratorProposal]:
        proposals: list[CuratorProposal] = []
        for s in skills:
            if s.state != SkillState.ACTIVE:
                continue
            if s.use_count > 10 and len(s.tags) < 2:
                proposals.append(
                    CuratorProposal(
                        action=CuratorAction.UPGRADE,
                        target=s.name,
                        reason=f"high-use skill (count={s.use_count}) with few tags · add richer metadata",
                        confidence="low",
                    )
                )
            name_lower = s.name.lower()
            narrow_patterns = ["audit-", "diagnosis-", "salvage-", "fix-", "debug-"]
            for pat in narrow_patterns:
                if name_lower.startswith(pat):
                    proposals.append(
                        CuratorProposal(
                            action=CuratorAction.SPLIT,
                            target=s.name,
                            reason=f"narrow session-specific name '{s.name}' · belongs under a class-level umbrella",
                            confidence="medium",
                        )
                    )
                    break
        return proposals

    @staticmethod
    def _extract_prefix(name: str) -> str | None:
        sep = "-"
        if "_" in name and "-" not in name:
            sep = "_"
        parts = name.split(sep)
        if len(parts) >= 2:
            return parts[0]
        return None

    @staticmethod
    def _compute_similarity(a: SkillInfo, b: SkillInfo) -> float:
        score = 0.0
        if a.category and b.category and a.category == b.category:
            score += 0.3
        common_tags = set(a.tags) & set(b.tags)
        all_tags = set(a.tags) | set(b.tags)
        if all_tags:
            score += 0.3 * (len(common_tags) / len(all_tags))
        prefix_a = SkillCurator._extract_prefix(a.name)
        prefix_b = SkillCurator._extract_prefix(b.name)
        if prefix_a and prefix_b and prefix_a == prefix_b:
            score += 0.2
        words_a = set(a.description.lower().split())
        words_b = set(b.description.lower().split())
        if words_a and words_b:
            overlap = len(words_a & words_b) / max(len(words_a), len(words_b))
            score += 0.2 * overlap
        return min(score, 1.0)


__all__ = [
    "CuratorAction",
    "CuratorConfig",
    "CuratorProposal",
    "CuratorReport",
    "SkillCurator",
    "SkillInfo",
    "SkillState",
]
