from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from runtime.platform.plugins._secure_fetch import fetch_public_https_bytes

_MAX_REMOTE_REGISTRY_BYTES = 8 * 1024 * 1024


class SkillMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    version: str = "0.1.0"
    author: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    min_echo_version: str = ""
    requires: list[str] = Field(default_factory=list)


class SearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    version: str = ""
    installed: bool = False


class InstallResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    path: str
    status: str  # "installed" | "updated" | "already_installed" | "failed"
    message: str = ""


class SkillMarket:
    # Skill store with an optional cloud registry. The original remote registry
    # (an unpublished ``echo-agent/skill-hub`` repo) was dead plumbing and the
    # network path was removed. We now point at the published WorkBuddy expert
    # mall (GitHub Pages) ``skill-registry.json`` so remote skills show up in
    # search/info, merged with the local registry. Local install still happens
    # by dropping a folder under ~/.echo/skills; the built-in catalog ships
    # under skills/public (auto-registered, no install step). DEFAULT_REPO is
    # kept only as a publish-instructions hint.
    DEFAULT_REPO = "echo-agent/skill-hub"
    REMOTE_REGISTRY_URL = os.environ.get(
        "ECHO_SKILL_REGISTRY_URL",
        "https://raw.githubusercontent.com/dengdenghua/workbuddy-expert-market/gh-pages/data/skill-registry.json",
    )

    def __init__(
        self,
        skills_dir: str | Path | None = None,
        *,
        remote_registry_url: str | None = None,
        use_remote: bool = True,
    ) -> None:
        if skills_dir is None:
            skills_dir = Path(os.path.expanduser("~/.echo/skills"))
        self._dir = Path(skills_dir)
        self._remote_url = remote_registry_url or self.REMOTE_REGISTRY_URL
        self._use_remote = use_remote

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        registry = self._fetch_registry()
        if registry is None:
            return []

        query_lower = query.lower()
        results: list[SearchResult] = []

        for entry in registry:
            name = entry.get("name", "")
            desc = entry.get("description", "")
            tags = entry.get("tags", [])
            searchable = f"{name} {desc} {' '.join(tags)}".lower()

            if query_lower in searchable:
                results.append(
                    SearchResult(
                        name=name,
                        description=desc,
                        author=entry.get("author", ""),
                        tags=tags,
                        version=entry.get("version", "0.1.0"),
                        installed=self._is_installed(name),
                    )
                )

            if len(results) >= limit:
                break

        return results

    def install(self, name: str, version: str | None = None) -> InstallResult:
        dest = self._dir / name
        if dest.exists():
            return InstallResult(
                name=name,
                version=version or "0.1.0",
                path=str(dest),
                status="already_installed",
                message=f"Skill '{name}' already installed at {dest}",
            )

        # Remote fetch removed — there's no registry to pull from. Local skills
        # are added by dropping a folder under ~/.echo/skills, and the built-in
        # catalog under skills/public auto-registers without an install step.
        return InstallResult(
            name=name,
            version=version or "0.1.0",
            path="",
            status="failed",
            message=(
                f"Remote skill market is disabled. Add '{name}' locally under "
                f"{dest}/ (SKILL.md + meta.json), or use the built-in skills/public catalog."
            ),
        )

    def uninstall(self, name: str) -> bool:
        dest = self._dir / name
        if not dest.exists():
            return False
        shutil.rmtree(dest, ignore_errors=True)
        return True

    def publish(self, skill_path: str | Path) -> dict[str, Any]:
        skill_path = Path(skill_path)
        if not skill_path.exists():
            return {"status": "error", "message": f"Path not found: {skill_path}"}

        skill_md = skill_path / "SKILL.md"
        meta_file = skill_path / "meta.json"

        if not skill_md.exists():
            return {"status": "error", "message": "SKILL.md not found"}

        meta: dict[str, Any] = {}
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {"status": "error", "message": "meta.json is invalid JSON"}

        name = meta.get("name", skill_path.name)
        meta.setdefault("name", name)

        return {
            "status": "ready",
            "message": (
                f"Skill '{name}' is ready to publish.\n"
                f"To publish, create a PR to {self.DEFAULT_REPO}:\n"
                f"  1. Fork {self.DEFAULT_REPO}\n"
                f"  2. Copy your skill to skills/{name}/\n"
                f"  3. Add entry to registry.json\n"
                f"  4. Submit PR"
            ),
            "skill_name": name,
            "meta": meta,
        }

    def list_installed(self) -> list[SkillMeta]:
        if not self._dir.exists():
            return []

        results: list[SkillMeta] = []
        for item in sorted(self._dir.iterdir()):
            if not item.is_dir():
                continue
            meta_file = item / "meta.json"
            if meta_file.exists():
                try:
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                    results.append(SkillMeta(**data))
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    results.append(SkillMeta(name=item.name))
            else:
                results.append(SkillMeta(name=item.name))

        return results

    def info(self, name: str) -> dict[str, Any] | None:
        local = self._dir / name
        if local.exists():
            meta_file = local / "meta.json"
            skill_md = local / "SKILL.md"
            result: dict[str, Any] = {"name": name, "installed": True}

            if meta_file.exists():
                with contextlib.suppress(json.JSONDecodeError, OSError, ValueError, TypeError):
                    result["meta"] = json.loads(meta_file.read_text(encoding="utf-8"))
            if skill_md.exists():
                result["skill_md"] = skill_md.read_text(encoding="utf-8")[:2000]

            return result

        registry = self._fetch_registry()
        if registry is None:
            return None

        for entry in registry:
            if entry.get("name") == name:
                return {**entry, "installed": False}

        return None

    def _is_installed(self, name: str) -> bool:
        return (self._dir / name).exists()

    # NOTE: the former _fetch_registry()/_fetch_skill_content() GitHub plumbing
    # (raw.githubusercontent.com/echo-agent/skill-hub) was removed — it pointed
    # at an unpublished repo and always 404'd. The market is now local-only.

    def _fetch_registry(self) -> list[dict[str, Any]] | None:
        local = self._local_registry() or []
        if not self._use_remote:
            return local or None
        remote = self._remote_registry()
        if not remote:
            return local or None
        # 按 name 合并:本地优先,远程补缺(远程条目标记 source=remote)
        by_name: dict[str, dict[str, Any]] = {}
        for entry in local:
            by_name[entry.get("name", "")] = {**entry, "local": True}
        for entry in remote:
            name = entry.get("name", "")
            if name and name not in by_name:
                by_name[name] = {**entry, "local": False, "source": "workbuddy-cloud"}
        return list(by_name.values())

    def _local_registry(self) -> list[dict[str, Any]] | None:
        local_reg = self._dir / "registry.json"
        if local_reg.exists():
            try:
                return json.loads(local_reg.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):  # noqa: BLE001 — local registry corrupt; return None for upstream fallback
                pass
        return None

    def _remote_registry(self) -> list[dict[str, Any]]:
        """拉取云端 skill-registry.json;失败返回 []。"""
        try:
            body = fetch_public_https_bytes(
                self._remote_url,
                timeout=15,
                max_bytes=_MAX_REMOTE_REGISTRY_BYTES,
            )
            data = json.loads(body.decode("utf-8"))
            skills = data.get("skills") if isinstance(data, dict) else data
            if isinstance(skills, list):
                return skills
        except Exception:  # noqa: BLE001 — 网络/解析失败静默回退本地
            pass
        return []
