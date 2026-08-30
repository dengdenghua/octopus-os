"""Canonical aliases for bundled prompt skills.

These aliases fold duplicate all_skills packages without deleting the
compatibility entry points. The canonical skill remains visible in catalogs;
the alias can still resolve for older prompts, configs, or user mentions.
"""

from __future__ import annotations

from collections import defaultdict

SKILL_ALIAS_TO_CANONICAL: dict[str, str] = {
    # Same script/assets, renamed or translated.
    "ad-copywriter": "ad-creative",
    "api-doc-gen": "route-to-openapi",
    "auto-hypothesis-test": "auto-stat-test",
    "chart-gen": "chart-image",
    "code-safety-audit": "code-vuln-audit",
    "data-viz-gen": "data-viz-renderer",
    "database-inspector": "database-scout",
    "edge-tts": "speech-synthesis",
    "git-repo-audit": "repo-audit",
    "gitlab-cli-guide": "gitlab-cli-skills",
    "http-load-profiler": "http-load-tester",
    "imap-smtp-email": "email-manager",
    "incident-review-guide": "incident-retrospective",
    "k8s-cluster-ops": "kubectl",
    "log-error-digest": "log-diagnostic",
    "playwright-scraper-skill": "smart-web-scraper",
    "sql-tutor": "sql-insight",
    "sun-path": "sunlight-analysis",
    # Same intent, different language/name.
    "cv-tailor": "resume-craft",
    "design-system-builder": "ui-blueprint",
    "idea-to-prd": "product-spec-writer",
    "mock-interview-drill": "interview-simulator",
    "seo-analyzer": "seo-audit",
    "tdd-coach": "test-driven-dev",
}


_CANONICAL_TO_ALIASES: dict[str, tuple[str, ...]] = {}
_tmp: dict[str, list[str]] = defaultdict(list)
for alias, canonical in SKILL_ALIAS_TO_CANONICAL.items():
    _tmp[canonical].append(alias)
_CANONICAL_TO_ALIASES = {canonical: tuple(sorted(aliases)) for canonical, aliases in _tmp.items()}


def canonical_skill_id(name: str) -> str:
    """Return the canonical skill id for an alias, or ``name`` unchanged."""
    return SKILL_ALIAS_TO_CANONICAL.get(name, name)


def aliases_for_canonical(name: str) -> tuple[str, ...]:
    """Return compatibility aliases that should resolve to ``name``."""
    return _CANONICAL_TO_ALIASES.get(name, ())


def is_shadow_skill_id(name: str) -> bool:
    """Whether ``name`` is a compatibility alias, not a primary entry."""
    return name in SKILL_ALIAS_TO_CANONICAL


def is_alias_trusted_source(trusted_source: str) -> bool:
    """Whether a registered skill came from an alias entry."""
    return "#alias" in str(trusted_source or "")


__all__ = [
    "SKILL_ALIAS_TO_CANONICAL",
    "aliases_for_canonical",
    "canonical_skill_id",
    "is_alias_trusted_source",
    "is_shadow_skill_id",
]
