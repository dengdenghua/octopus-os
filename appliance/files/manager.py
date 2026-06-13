"""NAS 文件管理器核心逻辑(无 HTTP,便于单测)。

路径安全是第一要务:所有相对路径解析后必须落在 root 内,拒绝 `..` 越权与
符号链接逃逸。删除走回收站语义,物理删除仅 empty_trash。
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TRASH_DIRNAME = ".octopus-trash"
_MANIFEST = "manifest.json"


class PathEscape(ValueError):
    """相对路径解析后逃出了 root。"""


@dataclass
class FileEntry:
    name: str
    path: str  # root 相对路径(posix 风格)
    kind: str  # "dir" | "file"
    size: int
    mtime: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "size": self.size,
            "mtime": self.mtime,
        }


class FileManager:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ── 路径安全 ────────────────────────────────────────────────
    def _resolve(self, rel: str) -> Path:
        # 归一化:去掉前导斜杠,空/"."视为 root。
        rel = (rel or "").strip().lstrip("/")
        target = (self.root / rel).resolve()
        if target != self.root and self.root not in target.parents:
            raise PathEscape(f"path escapes root: {rel!r}")
        return target

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    @property
    def _trash_dir(self) -> Path:
        d = self.root / TRASH_DIRNAME
        d.mkdir(exist_ok=True)
        return d

    def _read_manifest(self) -> list[dict[str, Any]]:
        f = self._trash_dir / _MANIFEST
        try:
            return json.loads(f.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _write_manifest(self, entries: list[dict[str, Any]]) -> None:
        (self._trash_dir / _MANIFEST).write_text(json.dumps(entries, indent=2))

    # ── 浏览 ────────────────────────────────────────────────────
    def list_dir(self, rel: str = "") -> list[FileEntry]:
        target = self._resolve(rel)
        if not target.is_dir():
            raise NotADirectoryError(rel)
        entries: list[FileEntry] = []
        for child in target.iterdir():
            # 隐藏回收站目录本身(仅在 root 层)。
            if child.parent == self.root and child.name == TRASH_DIRNAME:
                continue
            try:
                st = child.stat()
            except OSError:
                continue
            entries.append(
                FileEntry(
                    name=child.name,
                    path=self._rel(child),
                    kind="dir" if child.is_dir() else "file",
                    size=st.st_size,
                    mtime=st.st_mtime,
                )
            )
        entries.sort(key=lambda e: (e.kind != "dir", e.name.lower()))
        return entries

    def mkdir(self, rel: str) -> FileEntry:
        target = self._resolve(rel)
        if target == self.root:
            raise ValueError("cannot mkdir root")
        target.mkdir(parents=True, exist_ok=False)
        return self._entry(target)

    def move(self, src_rel: str, dst_rel: str) -> FileEntry:
        src = self._resolve(src_rel)
        dst = self._resolve(dst_rel)
        if src == self.root:
            raise ValueError("cannot move root")
        if not src.exists():
            raise FileNotFoundError(src_rel)
        # dst 是已存在目录 → 移动进该目录;否则视为重命名目标。
        if dst.is_dir():
            dst = dst / src.name
        if dst.exists():
            raise FileExistsError(self._rel(dst))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return self._entry(dst)

    # ── 回收站语义 ──────────────────────────────────────────────
    def trash(self, rel: str) -> dict[str, Any]:
        """移入回收站(非物理删除)。"""
        src = self._resolve(rel)
        if src == self.root:
            raise ValueError("cannot trash root")
        if not src.exists():
            raise FileNotFoundError(rel)
        entry_id = uuid.uuid4().hex
        dest = self._trash_dir / entry_id
        shutil.move(str(src), str(dest))
        record = {
            "id": entry_id,
            "name": src.name,
            "original": self._rel(src),
            "kind": "dir" if dest.is_dir() else "file",
            "trashed_at": time.time(),
        }
        manifest = self._read_manifest()
        manifest.append(record)
        self._write_manifest(manifest)
        return record

    def list_trash(self) -> list[dict[str, Any]]:
        return sorted(
            self._read_manifest(),
            key=lambda r: r.get("trashed_at", 0),
            reverse=True,
        )

    def restore(self, entry_id: str) -> FileEntry:
        manifest = self._read_manifest()
        record = next((r for r in manifest if r["id"] == entry_id), None)
        if record is None:
            raise FileNotFoundError(entry_id)
        stored = self._trash_dir / entry_id
        if not stored.exists():
            # 清单与磁盘不一致:剔除该条。
            self._write_manifest([r for r in manifest if r["id"] != entry_id])
            raise FileNotFoundError(entry_id)
        dest = self._resolve(record["original"])
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}-restored-{entry_id[:6]}{dest.suffix}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stored), str(dest))
        self._write_manifest([r for r in manifest if r["id"] != entry_id])
        return self._entry(dest)

    def empty_trash(self) -> int:
        """物理删除回收站全部内容(唯一不可逆路径)。返回清空条数。"""
        manifest = self._read_manifest()
        count = len(manifest)
        for record in manifest:
            stored = self._trash_dir / record["id"]
            if stored.is_dir():
                shutil.rmtree(stored, ignore_errors=True)
            elif stored.exists():
                stored.unlink(missing_ok=True)
        self._write_manifest([])
        return count

    # ── helpers ─────────────────────────────────────────────────
    def _entry(self, path: Path) -> FileEntry:
        st = path.stat()
        return FileEntry(
            name=path.name,
            path=self._rel(path),
            kind="dir" if path.is_dir() else "file",
            size=st.st_size,
            mtime=st.st_mtime,
        )
