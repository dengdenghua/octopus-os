"""Genome Registry — versioned JSON snapshot store for system configuration.

This module implements a simple versioned key-value store backed by git commits.
Each "genome" is a JSON dict representing a system configuration snapshot;
the registry tracks versions, supports rollback, and trims old entries.

NOTE — this module does NOT contain mutation / crossover / selection logic.
The actual evolution algorithms (mutation, crossover, pareto frontier) live in
``runtime.safety.experiments.prompt_evolver`` and operate on prompt variants.
The genome registry stores the configuration that evolution may modify, but the
evolution engine itself is a separate module.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.regeneration.genome_registry")


@dataclass
class GenomeRegistryConfig:
    genome_dir: str = "data/genome"
    max_versions: int = 50


class GenomeRegistry:
    def __init__(self, config: GenomeRegistryConfig | None = None) -> None:
        self.config = config or GenomeRegistryConfig()
        self._dir = Path(self.config.genome_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._init_git()

    def _init_git(self) -> None:
        if not (self._dir / ".git").exists():
            subprocess.run(
                ["git", "init"],
                cwd=str(self._dir),
                capture_output=True,
                timeout=10,
            )
            _LOG.info("initialized git repo at %s", self._dir)

    def commit(self, genome: dict[str, Any], message: str) -> int:
        version = self._next_version()
        path = self._dir / f"v{version}.json"
        path.write_text(
            json.dumps(genome, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        self._git_add_and_commit(f"v{version}.json", message or f"genome v{version}")
        self._trim_old_versions()
        return version

    def rollback(self, version: int) -> dict[str, Any] | None:
        path = self._dir / f"v{version}.json"
        if not path.exists():
            _LOG.warning("genome v%d not found", version)
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def diff(self, v1: int, v2: int) -> dict[str, Any]:
        g1 = self.rollback(v1) or {}
        g2 = self.rollback(v2) or {}
        added = {k: v2[k] for k in g2 if k not in g1}
        removed = {k: g1[k] for k in g1 if k not in g2}
        changed = {k: {"from": g1[k], "to": g2[k]} for k in g1 if k in g2 and g1[k] != g2[k]}
        return {"added": added, "removed": removed, "changed": changed}

    def latest_version(self) -> int:
        versions = self._list_versions()
        return max(versions) if versions else 0

    def latest(self) -> dict[str, Any] | None:
        v = self.latest_version()
        return self.rollback(v) if v > 0 else None

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        versions = self._list_versions()
        entries = []
        for v in reversed(versions[-limit:]):
            path = self._dir / f"v{v}.json"
            stat = path.stat()
            entries.append(
                {
                    "version": v,
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )
        return entries

    def _next_version(self) -> int:
        return self.latest_version() + 1

    def _list_versions(self) -> list[int]:
        versions = []
        for p in self._dir.glob("v*.json"):
            try:  # noqa: SIM105
                versions.append(int(p.stem[1:]))
            except ValueError:  # noqa: BLE001 — version stem parse fallthrough
                pass
        return sorted(versions)

    def _git_add_and_commit(self, filename: str, message: str) -> None:
        try:
            subprocess.run(
                ["git", "add", filename],
                cwd=str(self._dir),
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.email=echo@agent",
                    "-c",
                    "user.name=echo-agent",
                    "commit",
                    "-m",
                    message,
                ],
                cwd=str(self._dir),
                capture_output=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            _LOG.warning("git commit failed: %s", exc)

    def _trim_old_versions(self) -> None:
        versions = self._list_versions()
        if len(versions) <= self.config.max_versions:
            return
        for v in versions[: len(versions) - self.config.max_versions]:
            path = self._dir / f"v{v}.json"
            path.unlink(missing_ok=True)
