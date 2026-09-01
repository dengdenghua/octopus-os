"""Local, upstream-independent conversation grouping for the MX viewer."""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

MAX_GROUPS = 30
MAX_GROUP_NAME = 24
MAX_ASSIGNMENTS = 5000
_ROOM_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


class ConversationGroupStore:
    """Persist groups and one-group-per-room assignments as a small JSON file."""

    def __init__(self, data_dir: str | Path = "~/.echo/data/mx2025_viewer") -> None:
        self.data_dir = Path(data_dir).expanduser()
        self._lock = threading.RLock()
        self._groups: list[dict[str, str]] = []
        self._assignments: dict[str, str] = {}
        self._load()

    @property
    def file(self) -> Path:
        return self.data_dir / "groups.json"

    def _load(self) -> None:
        try:
            if not self.file.exists():
                return
            payload = json.loads(self.file.read_text(encoding="utf-8"))
            groups = payload.get("groups") if isinstance(payload, dict) else []
            assignments = payload.get("assignments") if isinstance(payload, dict) else {}
            if isinstance(groups, list):
                for item in groups[:MAX_GROUPS]:
                    if not isinstance(item, dict):
                        continue
                    group_id = str(item.get("id") or "")
                    name = str(item.get("name") or "").strip()
                    if _ROOM_ID_RE.fullmatch(group_id) and name:
                        self._groups.append({"id": group_id, "name": name[:MAX_GROUP_NAME]})
            valid_ids = {item["id"] for item in self._groups}
            if isinstance(assignments, dict):
                self._assignments = {
                    str(room_id): str(group_id)
                    for room_id, group_id in list(assignments.items())[:MAX_ASSIGNMENTS]
                    if _ROOM_ID_RE.fullmatch(str(room_id)) and str(group_id) in valid_ids
                }
        except Exception as exc:  # noqa: BLE001 - corrupt preferences must not break plugin load
            _logger.warning("mx2025_viewer groups could not be loaded (%s)", type(exc).__name__)
            self._groups = []
            self._assignments = {}

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"groups": self._groups, "assignments": self._assignments}
        temporary = self.file.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.file)

    @staticmethod
    def _name(value: Any) -> str:
        return str(value or "").strip()[:MAX_GROUP_NAME]

    @staticmethod
    def valid_room_id(value: Any) -> str | None:
        room_id = str(value or "").strip()
        return room_id if _ROOM_ID_RE.fullmatch(room_id) else None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "groups": [dict(item) for item in self._groups],
                "assignments": dict(self._assignments),
            }

    def create(self, name: Any) -> dict[str, Any]:
        clean_name = self._name(name)
        if not clean_name:
            raise ValueError("分组名不能为空")
        with self._lock:
            if len(self._groups) >= MAX_GROUPS:
                raise ValueError(f"最多创建 {MAX_GROUPS} 个分组")
            group = {"id": uuid.uuid4().hex[:12], "name": clean_name}
            self._groups.append(group)
            self._save()
            return dict(group)

    def rename(self, group_id: str, name: Any) -> dict[str, Any] | None:
        clean_name = self._name(name)
        if not clean_name:
            raise ValueError("分组名不能为空")
        with self._lock:
            group = next((item for item in self._groups if item["id"] == group_id), None)
            if group is None:
                return None
            group["name"] = clean_name
            self._save()
            return dict(group)

    def delete(self, group_id: str) -> bool:
        with self._lock:
            original_length = len(self._groups)
            self._groups = [item for item in self._groups if item["id"] != group_id]
            if len(self._groups) == original_length:
                return False
            self._assignments = {
                room_id: assigned
                for room_id, assigned in self._assignments.items()
                if assigned != group_id
            }
            self._save()
            return True

    def assign(self, room_id: Any, group_id: Any) -> dict[str, Any]:
        valid_room_id = self.valid_room_id(room_id)
        if valid_room_id is None:
            raise ValueError("对话 ID 无效")
        with self._lock:
            clean_group_id = str(group_id or "").strip()
            if clean_group_id:
                if not any(item["id"] == clean_group_id for item in self._groups):
                    raise KeyError("分组不存在")
                if (
                    valid_room_id not in self._assignments
                    and len(self._assignments) >= MAX_ASSIGNMENTS
                ):
                    raise ValueError(f"最多保存 {MAX_ASSIGNMENTS} 条分组关系")
                self._assignments[valid_room_id] = clean_group_id
            else:
                self._assignments.pop(valid_room_id, None)
            self._save()
            return {"room_id": valid_room_id, "group_id": clean_group_id or None}


__all__ = ["ConversationGroupStore", "MAX_GROUPS"]
