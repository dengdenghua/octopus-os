"""Versioned UTF-8 editing for an explicitly authorized Storage root.

All protocol writers must share this store (one service process per root).
Uncooperative filesystem writers cannot participate in its compare-and-swap lock.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import stat
import threading
from pathlib import Path

from fastapi import HTTPException

from appliance.files.manager import FileManager
from echo_runtime.resource_identity import parse_storage_file_resource_id, photo_library_id

MAX_TEXT_BYTES = 1_000_000
TEXT_EDIT_CAPABILITY = "text-edit.v1"


class TextDocuments:
    def __init__(self, manager: FileManager):
        self.manager = manager
        self.source_id = photo_library_id(manager.root)
        self.lock = threading.RLock()

    def target(self, resource_id: str) -> Path:
        parsed = parse_storage_file_resource_id(resource_id)
        if parsed is None or parsed[0] != self.source_id:
            raise HTTPException(404, "文件不属于当前 Storage 数据源")
        relative = parsed[1].lstrip("/")
        # Reject aliases, including links inside the root and Windows ADS.
        candidate = self.manager.root
        for part in relative.split("/"):
            if ":" in part or part.endswith((".", " ")):
                raise HTTPException(400, "文件路径无效")
            candidate = candidate / part
            if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
                raise HTTPException(400, "不支持通过链接编辑文件")
        target = self.manager.file_for_download(relative)
        if target != candidate:
            raise HTTPException(400, "不支持通过路径别名编辑文件")
        if not stat.S_ISREG(target.stat().st_mode):
            raise HTTPException(415, "仅支持普通文本文件")
        return target

    def read(self, resource_id: str) -> dict:
        with self.lock:
            target = self.target(resource_id)
            with target.open("rb") as stream:
                content = stream.read(MAX_TEXT_BYTES + 1)
            if len(content) > MAX_TEXT_BYTES:
                raise HTTPException(413, "文本超过 1 MB，无法完整编辑")
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise HTTPException(415, "仅支持完整 UTF-8 文本") from exc
            if "\x00" in text:
                raise HTTPException(415, "不支持编辑二进制文件")
            return {
                "resource_id": resource_id,
                "text": text,
                "revision": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "encoding": "utf-8",
                "complete": True,
            }

    @staticmethod
    def encode(text: str) -> bytes:
        try:
            content = text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise HTTPException(415, "文本包含无效 Unicode 字符") from exc
        if len(content) > MAX_TEXT_BYTES:
            raise HTTPException(413, "文本超过 1 MB")
        if b"\x00" in content:
            raise HTTPException(415, "不支持编辑二进制文件")
        return content

    def check(self, resource_id: str, revision: str) -> dict:
        current = self.read(resource_id)
        if current["revision"] != revision:
            raise HTTPException(
                409,
                {
                    "code": "revision_conflict",
                    "message": "文件已被其他窗口修改，草稿已保留，请重新读取后合并",
                    "current_revision": current["revision"],
                },
            )
        return current

    def save(self, resource_id: str, text: str, revision: str) -> dict:
        content = self.encode(text)
        with self.lock:
            self.check(resource_id, revision)
            target = self.target(resource_id)
            relative = target.relative_to(self.manager.root)
            temp, destination = self.manager.prepare_upload(
                relative.parent.as_posix(),
                relative.name,
                overwrite=True,
                expected_bytes=len(content),
            )
            try:
                with temp.open("xb") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(temp, stat.S_IMODE(target.stat().st_mode))
                # Catch changes made while staging. This lock serializes protocol
                # clients; external editors must still coordinate at deployment.
                self.check(resource_id, revision)
                if self.target(resource_id) != destination:
                    raise HTTPException(409, "文件位置已变化，请重新读取")
                self.manager.finalize_upload(temp, destination, overwrite=True)
            finally:
                self.manager.discard_upload(temp)
            return {
                "resource_id": resource_id,
                "text": text,
                "revision": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "encoding": "utf-8",
                "complete": True,
            }

    def diff(self, resource_id: str, text: str, revision: str) -> dict:
        self.encode(text)
        before = self.check(resource_id, revision)["text"]
        left, right = before.splitlines(keepends=True), text.splitlines(keepends=True)
        # SequenceMatcher can be quadratic on adversarial text. Bound its input
        # and explicitly decline an expensive preview; never truncate a save.
        if len(left) + len(right) > 4000:
            raise HTTPException(413, "行数超过差异预览上限，请使用本地编辑器比较")
        lines = list(difflib.unified_diff(left, right, fromfile="已保存", tofile="草稿", n=3))
        return {
            "revision": revision,
            "patch": "".join(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                for line in lines
            ),
            "complete": True,
            "changed": before != text,
        }
