"""Export canonical mobile SKILL.md files into Echo Mobile assets.

Canonical source:
    runtime/tentacle/mobile/skills/<skill-name>/SKILL.md

Android asset target:
    ../echo-mobile/app/src/main/assets/skills/mobile/<skill.name>.md

The runtime keeps rich directory-based SKILL.md files for MCP/LLM use. The
Android app keeps compact flat files because ``SkillManifest.kt`` scans
``assets/skills/mobile/*.md`` and prefers one-line JSON Schema parameters.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from runtime.tentacle.llm.skill_manifest import SkillManifestLoader, SkillSpec

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _default_source_dir() -> Path:
    return _project_root() / "runtime" / "tentacle" / "mobile" / "skills"


def _default_target_dir() -> Path:
    return (
        _project_root().parent
        / "echo-mobile"
        / "app"
        / "src"
        / "main"
        / "assets"
        / "skills"
        / "mobile"
    )


def _asset_filename(skill_name: str) -> str:
    prefix = "android."
    stem = skill_name[len(prefix) :] if skill_name.startswith(prefix) else skill_name
    return f"{stem}.md"


def _compact_description(description: str) -> str:
    return " ".join(description.split())


def _risk_for(skill: SkillSpec) -> str:
    name = skill.name
    if name in {
        "android.install_app",
        "android.write_file",
        "android.browser.install_extension",
    }:
        return "high"
    if name in {
        "android.input_text",
        "android.set_clipboard",
        "android.open_app",
        "android.system_key",
        "android.browser.evaluate",
    }:
        return "medium"
    return "low"


def _timeout_for(skill: SkillSpec) -> int:
    name = skill.name
    if name.startswith("android.browser."):
        return 30_000
    if name in {"android.install_app", "android.open_app"}:
        return 30_000
    return 15_000


def render_asset_skill(skill: SkillSpec) -> str:
    parameters = json.dumps(skill.parameters, ensure_ascii=False)
    description = _compact_description(skill.description)
    return (
        "---\n"
        f"name: {skill.name}\n"
        f"description: {description}\n"
        f"risk: {_risk_for(skill)}\n"
        f"timeout_ms: {_timeout_for(skill)}\n"
        f"parameters: {parameters}\n"
        "---\n"
    )


def export_all(
    target_dir: Path | None = None,
    *,
    source_dir: Path | None = None,
    prune_stale: bool = True,
) -> list[Path]:
    """Export all canonical mobile skills into Android's flat assets folder."""
    source = source_dir or _default_source_dir()
    target = target_dir or _default_target_dir()
    target.mkdir(parents=True, exist_ok=True)

    loader = SkillManifestLoader()
    skills = loader.load_directory(source)
    if not skills:
        raise RuntimeError(f"no mobile skills loaded from {source}")

    written: list[Path] = []
    expected_files: set[str] = set()
    for skill in sorted(skills, key=lambda item: item.name):
        filename = _asset_filename(skill.name)
        expected_files.add(filename)
        path = target / filename
        path.write_text(render_asset_skill(skill), encoding="utf-8")
        written.append(path)

    if prune_stale:
        for path in target.glob("*.md"):
            if path.name.lower() in {"readme.md", "skill.md"}:
                continue
            if path.name not in expected_files:
                path.unlink()

    logger.info("exported %d mobile skills from %s to %s", len(written), source, target)
    return written


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    export_all()
