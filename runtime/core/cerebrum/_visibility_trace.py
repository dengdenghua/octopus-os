"""决策点 → 依据 → 结论 的可见性 trace（visibility trace）记录模块。

capability_router、_react_context_helpers 等 ReAct 决策点用本模块记录
「为什么这样决策」的可解释依据，用于事件推送与持久化。

设计原则：纯增量、零侵入 —— trace 记录的失败/异常一律吞掉，
任何情况下都不得改变原决策结果或拖慢决策路径。
"""

from __future__ import annotations

import contextvars
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

_ACTIVE_TRACE: contextvars.ContextVar[VisibilityTrace | None] = contextvars.ContextVar(
    "cerebrum_visibility_trace",
    default=None,
)


def _json_safe(value: Any) -> Any:
    """Best-effort projection of a trace value onto JSON-safe primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


@dataclass
class VisibilityEntry:
    """单个决策点的可见性记录：决策点 → 结论 → 依据。"""

    decision_point: str
    conclusion: str
    basis: str
    ts: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)


class VisibilityTrace:
    """线程安全的决策 trace 容器。

    实例由调用方（turn 构建期）创建并显式传入决策函数；模块级的
    ContextVar 槽位只是可选的默认通道，未设置时记录为 no-op。
    """

    def __init__(self) -> None:
        self._entries: list[VisibilityEntry] = []
        self._lock = threading.Lock()

    def record(self, entry: VisibilityEntry) -> None:
        """追加一条记录。绝不抛出 —— 纯增量可见性，不影响决策结果。"""
        try:
            with self._lock:
                self._entries.append(entry)
        except Exception:  # noqa: BLE001 - trace must never affect the caller
            pass

    def record_decision(
        self,
        decision_point: str,
        conclusion: str,
        basis: str,
        **details: Any,
    ) -> None:
        """以字符串参数形式记录一条决策。绝不抛出。"""
        with suppress(Exception):  # trace must never affect the caller
            self.record(
                VisibilityEntry(
                    decision_point=decision_point,
                    conclusion=conclusion,
                    basis=basis,
                    details=dict(details),
                )
            )

    def entries(self) -> list[VisibilityEntry]:
        """返回当前记录的浅拷贝快照。"""
        try:
            with self._lock:
                return list(self._entries)
        except Exception:  # noqa: BLE001 - trace must never affect the caller
            return []

    def export(self) -> list[dict[str, Any]]:
        """导出为可 JSON 序列化的 dict 列表（用于事件推送与持久化）。"""
        try:
            with self._lock:
                snapshot = list(self._entries)
        except Exception:  # noqa: BLE001 - trace must never affect the caller
            return []
        return [
            {
                "decision_point": entry.decision_point,
                "conclusion": entry.conclusion,
                "basis": entry.basis,
                "ts": entry.ts,
                "details": _json_safe(entry.details),
            }
            for entry in snapshot
        ]

    def __len__(self) -> int:
        try:
            with self._lock:
                return len(self._entries)
        except Exception:  # noqa: BLE001 - trace must never affect the caller
            return 0

    def empty(self) -> bool:
        return len(self) == 0

    def latest(self, decision_point: str | None = None) -> VisibilityEntry | None:
        """返回最近一条记录；指定决策点名称时返回该决策点的最近一条。"""
        try:
            with self._lock:
                entries = self._entries
                if decision_point is None:
                    return entries[-1] if entries else None
                for entry in reversed(entries):
                    if entry.decision_point == decision_point:
                        return entry
                return None
        except Exception:  # noqa: BLE001 - trace must never affect the caller
            return None


def new_trace() -> VisibilityTrace:
    """创建新的 trace 实例（turn 构建期由调用方持有并显式传入决策函数）。"""
    return VisibilityTrace()


def active_trace() -> VisibilityTrace | None:
    """返回当前上下文中的默认 trace；未设置时返回 None。"""
    try:
        return _ACTIVE_TRACE.get()
    except Exception:  # noqa: BLE001 - trace must never affect the caller
        return None


def set_active_trace(
    trace: VisibilityTrace | None,
) -> contextvars.Token[VisibilityTrace | None] | None:
    """把默认 trace 槽位设到当前上下文；返回可传给 reset_active_trace 的 token。"""
    try:
        return _ACTIVE_TRACE.set(trace)
    except Exception:  # noqa: BLE001 - trace must never affect the caller
        return None


def reset_active_trace(token: contextvars.Token[VisibilityTrace | None] | None) -> None:
    """撤销 set_active_trace 的副作用；token 为 None 时 no-op。绝不抛出。"""
    if token is None:
        return
    with suppress(Exception):  # trace must never affect the caller
        _ACTIVE_TRACE.reset(token)


def record_visibility(
    decision_point: str,
    conclusion: str,
    basis: str,
    **details: Any,
) -> None:
    """经当前上下文的默认槽位记录；未设置时 no-op。绝不抛出。"""
    try:
        trace = _ACTIVE_TRACE.get()
        if trace is None:
            return
        trace.record_decision(decision_point, conclusion, basis, **details)
    except Exception:  # noqa: BLE001 - trace must never affect the caller
        pass
