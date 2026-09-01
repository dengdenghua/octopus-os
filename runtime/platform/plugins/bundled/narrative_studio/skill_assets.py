"""Load the bounded, first-party SKILL.md assets owned by Narrative Studio.

The loader is deliberately whitelist based.  A plugin update may add a new
asset only by adding it to :data:`PACKAGED_SKILL_SLUGS`; arbitrary files under
the plugin directory never become executable runtime skills.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from runtime.execution.suckers.registry import Skill

CANON_POLICY = "candidate_only"
PACKAGED_SKILL_SLUGS: tuple[str, ...] = (
    "narrative-authoring",
    "continuity",
    "canon-review",
)
_RUNTIME_NAMES = {
    # Keep governance authority out of the callable skill id.  The packaged
    # asset reviews readiness, but the runtime capability is editorial only.
    "canon-review": "narrative_studio.editorial_readiness",
}


@dataclass(frozen=True)
class PackagedSkillAsset:
    """Validated metadata and instructions from one plugin-owned SKILL.md."""

    slug: str
    description: str
    summary: str
    affinity: tuple[str, ...]
    cost_profile: str
    instructions: str
    path: Path

    @property
    def runtime_name(self) -> str:
        return _RUNTIME_NAMES.get(
            self.slug,
            f"narrative_studio.{self.slug.replace('-', '_')}",
        )

    def catalog_entry(self) -> dict[str, str]:
        return {
            "name": self.runtime_name,
            "description": self.description,
        }

    def as_runtime_skill(self) -> Skill:
        asset = self

        def load_instructions(**_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "skill": asset.runtime_name,
                "canon_policy": CANON_POLICY,
                "instructions": asset.instructions,
            }

        return Skill(
            name=self.runtime_name,
            description=f"{self.description}\n\n{self.instructions}",
            summary=self.summary,
            affinity=list(self.affinity),
            cost_profile=self.cost_profile,  # type: ignore[arg-type]
            trusted_source=f"plugin://narrative_studio/skills/{self.slug}",
            handler=load_instructions,
        )


def _split_skill_document(text: str, path: Path) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"packaged skill is missing YAML frontmatter: {path}")
    try:
        closing = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError(f"packaged skill has unterminated YAML frontmatter: {path}") from exc
    raw = yaml.safe_load("\n".join(lines[1:closing])) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"packaged skill frontmatter must be a mapping: {path}")
    body = "\n".join(lines[closing + 1 :]).strip()
    if not body:
        raise ValueError(f"packaged skill instructions are empty: {path}")
    return raw, body


def _load_one(root: Path, slug: str) -> PackagedSkillAsset:
    path = root / "skills" / slug / "SKILL.md"
    if not path.is_file():
        raise ValueError(f"required packaged skill is missing: {path}")
    frontmatter, body = _split_skill_document(path.read_text(encoding="utf-8"), path)
    if frontmatter.get("name") != slug:
        raise ValueError(f"packaged skill name must equal its directory name: {path}")
    description = str(frontmatter.get("description") or "").strip()
    if not description:
        raise ValueError(f"packaged skill description is required: {path}")
    metadata = frontmatter.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"packaged skill metadata must be a mapping: {path}")
    if metadata.get("canon_policy") != CANON_POLICY:
        raise ValueError(f"packaged skill must declare canon_policy=candidate_only: {path}")
    summary = str(metadata.get("summary") or description).strip()
    raw_affinity = metadata.get("affinity") or ["narrative", "candidate"]
    if not isinstance(raw_affinity, list) or not all(
        isinstance(value, str) and value.strip() for value in raw_affinity
    ):
        raise ValueError(f"packaged skill affinity must be a list of strings: {path}")
    cost_profile = str(metadata.get("cost_profile") or "low").strip()
    if cost_profile not in {"low", "mid", "high"}:
        raise ValueError(f"invalid packaged skill cost_profile: {path}")
    return PackagedSkillAsset(
        slug=slug,
        description=description,
        summary=summary,
        affinity=tuple(value.strip() for value in raw_affinity),
        cost_profile=cost_profile,
        instructions=body,
        path=path,
    )


def load_packaged_skill_assets(plugin_dir: str | Path) -> list[PackagedSkillAsset]:
    """Load exactly the plugin's reviewed first-party skill assets."""

    root = Path(plugin_dir).expanduser().resolve()
    return [_load_one(root, slug) for slug in PACKAGED_SKILL_SLUGS]


__all__ = [
    "PACKAGED_SKILL_SLUGS",
    "PackagedSkillAsset",
    "load_packaged_skill_assets",
]
