"""黑板证据持久化桥：turn-scoped 黑板的证据落盘与跨轮复用。

为什么存在
----------

``runtime/memory/runtime_state/blackboard.py`` 给并行子代理提供 turn-scoped
共享键值（stigmergic 协调），但默认实现是**内存**的：turn 结束即失效，
重启/断线/多窗口都拿不回上一轮并行子代理写在黑板上的中间证据。而线程
事件日志（``event_log.py``）是持久主干——并行瓦片（SubagentItem）已能
从 JSONL 回放，唯独黑板的键值证据没有出口。

本模块补上这一环：

* ``save_turn_blackboard`` —— turn 收尾时把黑板 snapshot（键值、尺寸
  截断、JSON-safe）append 到 ``<threads>/{thread_id}.board.jsonl``；
* ``load_board_evidence`` / ``build_board_evidence_summary`` —— 从 JSONL
  重建该线程最近几轮的黑板证据，投影为 ``<board-evidence>`` 块，供下一轮
  的并行拆解（``_react_prompt_assembly_bootstrap`` PHASE 4.6）当背景上下文
  喂给子代理——"上一轮并行探索出的证据"不必等模型重新回忆。

约定（与 EventLog 一致）：纯 best-effort、绝不抛出、绝不阻塞决策路径。
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.memory.threads._event_log_helpers import validate_thread_id

_log = logging.getLogger("echo.memory.threads.board_evidence")

_VALUE_LIMIT = 200  # 单个黑板值的最大字符数（黑板会进 LLM 上下文）
_MAX_RECORD_BYTES = 8_192  # 单条证据记录的最大字节数
_DEFAULT_EVIDENCE_LIMIT = 4  # 跨轮复用最多回看几轮
_DEFAULT_EVIDENCE_CHARS = 800  # <board-evidence> 块的最大字符数


def _threads_root(logs_root: str | Path | None) -> Path:
    if logs_root is not None:
        return Path(logs_root)
    from runtime.platform.process.paths import app_paths

    return app_paths().data_dir / "threads"


def _evidence_path(thread_id: str, logs_root: str | Path | None) -> Path:
    safe = validate_thread_id(thread_id)
    return _threads_root(logs_root) / f"{safe}.board.jsonl"


def _json_safe_value(value: Any) -> str:
    """把黑板值投影为单行文本：字符串保持原文，结构化值 JSON 化，长度截断。"""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) > _VALUE_LIMIT:
        text = text[: _VALUE_LIMIT - 3].rstrip() + "..."
    return text


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ── 写入 ───────────────────────────────────────────────────────────────────


def save_turn_blackboard(
    thread_id: str,
    turn_id: str,
    board: Any,
    *,
    logs_root: str | Path | None = None,
) -> dict[str, Any] | None:
    """把一轮黑板的 snapshot 持久化到该线程的证据日志。

    ``board`` 可以是 ``Blackboard`` 实例（取 ``snapshot()``）或任意可
    ``dict()`` 的对象。没有可用的 turn_id / 空黑板时返回 None（no-op）。
    绝不抛出——证据持久化不能影响决策路径。
    """
    if not thread_id or not turn_id:
        return None
    try:
        snapshot = board.snapshot() if hasattr(board, "snapshot") else dict(board)
        if not isinstance(snapshot, dict):
            return None
        keys: dict[str, str] = {}
        for key, value in snapshot.items():
            if key is None:
                continue
            rendered = _json_safe_value(value)
            if rendered:
                keys[str(key)] = rendered
        if not keys:
            return None
        record = {
            "turn_id": str(turn_id),
            "ts": _now_iso(),
            "keys": keys,
        }
        blob = json.dumps(record, ensure_ascii=False)
        if len(blob.encode("utf-8")) > _MAX_RECORD_BYTES:
            # 超长记录按 key 截断而不是丢弃整轮：截到放得下为止。
            record["keys"] = _trim_keys_for_budget(keys, _MAX_RECORD_BYTES)
            blob = json.dumps(record, ensure_ascii=False)
        path = _evidence_path(thread_id, logs_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        _append_line(path, blob)
        return record
    except Exception:  # noqa: BLE001 — evidence persistence must never break the turn
        _log.debug(
            "board evidence save failed · thread=%s turn=%s", thread_id, turn_id, exc_info=True
        )
        return None


def _append_line(path: Path, blob: str) -> None:
    with _THREAD_SAVE_LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(blob)
        handle.write("\n")


def _trim_keys_for_budget(keys: dict[str, str], budget: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in keys.items():
        if len(json.dumps({**out, key: value}, ensure_ascii=False).encode("utf-8")) > budget:
            continue
        out[key] = value
        if len(json.dumps(out, ensure_ascii=False).encode("utf-8")) >= budget - 128:
            break
    return out


# ── 读取与投影 ─────────────────────────────────────────────────────────────


def list_board_evidence(
    thread_id: str,
    *,
    limit: int = 20,
    logs_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """该线程最近的 N 轮黑板证据（原始记录，新→旧）。不存在则空列表。"""
    path = _evidence_path(thread_id, logs_root)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    records: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict) and raw.get("turn_id"):
            records.append(raw)
    return list(reversed(records))


def build_board_evidence_summary(
    records: list[dict[str, Any]],
    *,
    limit: int = _DEFAULT_EVIDENCE_LIMIT,
    max_chars: int = _DEFAULT_EVIDENCE_CHARS,
) -> str:
    """把黑板证据记录投影为 ``<board-evidence>`` 块（纯函数）。

    每轮一行 ``turn <id>: key = value``；超长按字符上限截断。空输入
    返回空串。绝不抛出。
    """
    if not records:
        return ""
    lines: list[str] = ["<board-evidence>"]
    total = len(lines[0]) + 1
    emitted = 0
    exhausted = False
    for record in records[-max(1, limit) :]:
        if not isinstance(record, dict):
            continue
        turn_id = str(record.get("turn_id") or "")
        keys = record.get("keys")
        if not isinstance(keys, dict):
            continue
        for key, value in keys.items():
            line = f"turn {turn_id}: {key} = {value}"
            remaining = max_chars - total
            if remaining <= 0:
                exhausted = True
                break
            if len(line) > remaining:
                line = line[: max(0, remaining - 3)].rstrip() + "..."
            lines.append(line)
            total += len(line) + 1
            emitted += 1
        if exhausted:
            break
    if not emitted:
        return ""
    lines.append("</board-evidence>")
    return "\n".join(lines)


def load_board_evidence(
    thread_id: str,
    *,
    limit: int = _DEFAULT_EVIDENCE_LIMIT,
    max_chars: int = _DEFAULT_EVIDENCE_CHARS,
    logs_root: str | Path | None = None,
) -> str:
    """读取该线程最近几轮黑板证据并投影为 ``<board-evidence>`` 块。

    无证据或出错时返回空串（调用方视为 no-op 背景上下文）。best-effort，
    绝不抛出。
    """
    try:
        records = list_board_evidence(thread_id, limit=limit, logs_root=logs_root)
        return build_board_evidence_summary(records, limit=limit, max_chars=max_chars)
    except Exception:  # noqa: BLE001 — evidence read must never break the turn
        return ""


_THREAD_SAVE_LOCK = threading.Lock()


__all__ = [
    "build_board_evidence_summary",
    "list_board_evidence",
    "load_board_evidence",
    "save_turn_blackboard",
]
