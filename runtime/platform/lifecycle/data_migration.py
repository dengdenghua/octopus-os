from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

CURRENT_DATA_VERSION = 2
# One-shot utility: invoked explicitly during upgrades, never at startup.
COMPATIBILITY_STATUS = "legacy-one-shot"

_MIGRATIONS: dict[int, dict[str, Any]] = {
    1: {
        "description": "add version field to all JSON data files",
        "transforms": {
            "agents.json": lambda d: {**d, "version": 1},
            "skills.json": lambda d: {**d, "version": 1},
            "config.json": lambda d: {**d, "version": 1},
        },
    },
    2: {
        "description": "add created_at/updated_at timestamps",
        "transforms": {
            "agents.json": lambda d: {**d, "updated_at": datetime.now().isoformat()},
            "skills.json": lambda d: {**d, "updated_at": datetime.now().isoformat()},
        },
    },
}


class DataMigrator:
    def __init__(self, data_dir: str | Path = "data") -> None:
        self._dir = Path(data_dir)

    def current_version(self, filename: str = "agents.json") -> int:
        path = self._dir / filename
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("version", 0)
        except (json.JSONDecodeError, OSError):
            return 0

    def needs_migration(self) -> bool:
        for json_file in self._dir.glob("*.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                if data.get("version", 0) < CURRENT_DATA_VERSION:
                    return True
            except (json.JSONDecodeError, OSError):
                continue
        return False

    def migrate(self, *, backup: bool = True) -> list[str]:
        migrated: list[str] = []

        if backup:
            self._backup()

        for json_file in self._dir.glob("*.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                _LOG.warning("skipping %s: %s", json_file.name, exc)
                continue

            version = data.get("version", 0)
            if version >= CURRENT_DATA_VERSION:
                continue

            for v in range(version + 1, CURRENT_DATA_VERSION + 1):
                migration = _MIGRATIONS.get(v)
                if migration is None:
                    continue
                transform = migration.get("transforms", {}).get(json_file.name)
                if transform is not None:
                    data = transform(data)

            data["version"] = CURRENT_DATA_VERSION
            json_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            migrated.append(json_file.name)

        if migrated:
            _LOG.info("migrated %d file(s) to v%d", len(migrated), CURRENT_DATA_VERSION)
        return migrated

    def _backup(self) -> Path | None:
        if not self._dir.exists():
            return None
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self._dir.parent / f"{self._dir.name}_backup_{ts}"
        try:
            shutil.copytree(self._dir, backup_dir)
            _LOG.info("data backup: %s", backup_dir)
            return backup_dir
        except OSError as exc:
            _LOG.warning("backup failed: %s", exc)
            return None


__all__ = ["DataMigrator", "CURRENT_DATA_VERSION"]
