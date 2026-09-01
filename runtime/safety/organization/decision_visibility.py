"""协作决策可见性控制：谁对哪个 Agent 的哪个决策可见、可审计。

对标明略 Octo 的 Agent OS 协作模型。仓库已有三层基础，本模块把它们
串成一条"决策可见性"链路：

* 组织 / 频道 ACL（``runtime/workspace/org.py`` + ``org_store.py``）——
  谁能进入哪个空间；
* 决策 trace（``runtime/core/cerebrum/_visibility_trace.py``）——
  Agent 内部决策的 why 链（能力激活、委派可见性、技能目录截断）；
* HMAC 审计链（``runtime/safety/audit/audit_chain.py``）—— 防篡改的
  追加式日志（``org_audit.py`` 已用于权限变更审计）。

本模块补上缺失的一环：**决策记录的可见性策略与查看审计**。

* 决策产生时按 ``DecisionScope`` 标记可见范围（private / team / org）；
* 查看时按查看者身份（``ViewerContext``：组织角色 + 团队归属）解析出
  ``DecisionAccessLevel``（hidden / summary / conclusion / full），再按
  层级脱敏——``basis`` / ``details`` 这类决策依据不会泄露给无权者；
* 每次查看 / 导出都追加到独立的 HMAC 审计链（``DecisionAccessAudit``），
  回答"谁在什么时候看了哪个 Agent 的哪个决策、拿到了哪一层"。

纯函数判定 + 显式存储，全部可单测；不修改既有决策路径（trace 采集
保持零侵入）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from runtime.safety.audit.audit_chain import AuditChain

_LOG = logging.getLogger("echo.safety.organization.decision_visibility")

_DECISION_AUDIT_CHAIN_KEY_ID = "decision-access-local-v1"
_DECISION_AUDIT_CHAIN_SECRET_ENV = "ECHO_DECISION_AUDIT_SECRET"

# 审计链识别的行为事件类型（查看 / 导出共享案例）。
DECISION_ACCESS_EVENT_TYPES = frozenset({"decision_view", "decision_export"})

# 敏感决策点：即使授权成立，也按矩阵整体降一级，防止工具细节外泄。
# 默认空集——当前采集的决策点（能力激活/委派/技能目录截断）均非敏感；
# 未来接入含工具调用细节的决策点时在此登记即可。
SENSITIVE_DECISION_POINTS: frozenset[str] = frozenset()

# 组织管理员角色（owner / admin）可跨团队查看 team 与 org 级决策。
_ORG_ADMIN_ROLES = frozenset({"owner", "admin"})

# summary 层级的结论摘要截断长度。
_SUMMARY_LIMIT = 80


class DecisionScope(StrEnum):
    """决策产生时的可见范围标记。"""

    PRIVATE = "private"  # 仅决策 Agent 自身（默认，最小暴露）
    TEAM = "team"  # 同协作团队（room/team）成员可见
    ORG = "org"  # 组织内任意成员可见


class DecisionAccessLevel(StrEnum):
    """查看者可获的内容层级（由高到低）。"""

    FULL = "full"  # 决策点 + 结论 + 依据 + 细节
    CONCLUSION = "conclusion"  # 决策点 + 结论（无依据/细节）
    SUMMARY = "summary"  # 仅决策点 + 结论摘要
    HIDDEN = "hidden"  # 不可见


_LEVEL_ORDER = (
    DecisionAccessLevel.HIDDEN,
    DecisionAccessLevel.SUMMARY,
    DecisionAccessLevel.CONCLUSION,
    DecisionAccessLevel.FULL,
)


@dataclass(frozen=True)
class DecisionRecord:
    """一条可受控可见的 Agent 决策记录。"""

    agent_id: str
    decision_point: str
    conclusion: str
    basis: str = ""
    scope: DecisionScope = DecisionScope.PRIVATE
    team_id: str = ""  # scope=team 时的可见团队（room/team id）
    ts: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_trace_entry(
        cls,
        entry: dict[str, Any],
        *,
        agent_id: str,
        scope: DecisionScope | str = DecisionScope.PRIVATE,
        team_id: str = "",
    ) -> DecisionRecord:
        """从 ``_visibility_trace`` 的 ``export()`` 单项构造。

        trace 采集保持零侵入：entry 本身不含 agent/scope 元数据，由调用方
        （知道 Agent 上下文的工作台 / 共享案例层）补充。
        """
        return cls(
            agent_id=str(agent_id or ""),
            decision_point=str(entry.get("decision_point") or ""),
            conclusion=str(entry.get("conclusion") or ""),
            basis=str(entry.get("basis") or ""),
            scope=_coerce_scope(scope),
            team_id=str(team_id or ""),
            ts=float(entry.get("ts") or 0.0),
            details=dict(entry.get("details") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "decision_point": self.decision_point,
            "conclusion": self.conclusion,
            "basis": self.basis,
            "scope": str(self.scope),
            "team_id": self.team_id,
            "ts": self.ts,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ViewerContext:
    """查看者身份：组织角色 + 团队归属 + 是否为决策 Agent 自身。"""

    member_id: str
    kind: str = "human"  # human / agent —— Agent 看自己 Agent 的决策也视为 self
    org_role: str | None = None  # owner/admin/member/viewer；None = 组织外
    team_ids: frozenset[str] = frozenset()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ViewerContext:
        return cls(
            member_id=str(raw.get("member_id") or ""),
            kind=str(raw.get("kind") or "human"),
            org_role=raw.get("org_role"),
            team_ids=frozenset(str(t) for t in (raw.get("team_ids") or []) if t),
        )


def resolve_access(
    *,
    record: DecisionRecord,
    viewer: ViewerContext,
) -> DecisionAccessLevel:
    """解析查看者对一条决策记录可见性矩阵的结果（纯函数）。

    规则：

    * 决策 Agent 自身（同一 identity 的 agent 成员）——总是 ``full``；
    * 组织外成员——总是 ``hidden``；
    * 其余按 ``DecisionScope`` 矩阵：
      ``private`` 仅同团队拿 ``summary``、组织管理员拿 ``conclusion``
      （管理员结论视图不受团队归属影响）；
      ``team`` 同团队与管理拿 ``full``、组织成员拿 ``summary``、viewer 拿
      ``conclusion``；
      ``org`` 组织成员拿 ``full``、viewer 只拿 ``conclusion``；
    * 敏感决策点（见 ``SENSITIVE_DECISION_POINTS``）对非自身、非管理员的
      授权整体降一级。
    """
    if viewer.kind == "agent" and viewer.member_id and viewer.member_id == record.agent_id:
        return DecisionAccessLevel.FULL
    if not viewer.org_role:
        return DecisionAccessLevel.HIDDEN
    in_team = bool(record.team_id) and record.team_id in viewer.team_ids
    admin = viewer.org_role in _ORG_ADMIN_ROLES

    if record.scope == DecisionScope.PRIVATE:
        # Admin privilege is independent of team membership: an org admin
        # who happens to be in the record's team still gets the conclusion
        # (audit) view, not the lower same-team summary.
        if admin:
            base = DecisionAccessLevel.CONCLUSION
        elif in_team:
            base = DecisionAccessLevel.SUMMARY
        else:
            return DecisionAccessLevel.HIDDEN
    elif record.scope == DecisionScope.TEAM:
        if in_team or admin:
            base = DecisionAccessLevel.FULL
        elif viewer.org_role == "viewer":
            base = DecisionAccessLevel.CONCLUSION
        else:
            base = DecisionAccessLevel.SUMMARY
    else:  # DecisionScope.ORG
        if viewer.org_role == "viewer":
            base = DecisionAccessLevel.CONCLUSION
        else:
            base = DecisionAccessLevel.FULL

    if record.decision_point in SENSITIVE_DECISION_POINTS and not admin:
        return _downgrade(base)
    return base


def apply_level(record: DecisionRecord, level: DecisionAccessLevel) -> dict[str, Any] | None:
    """按层级脱敏输出；``hidden`` 返回 None（调用方应过滤掉）。"""
    if level == DecisionAccessLevel.HIDDEN:
        return None
    common = {
        "agent_id": record.agent_id,
        "decision_point": record.decision_point,
        "scope": str(record.scope),
    }
    if level == DecisionAccessLevel.SUMMARY:
        common["summary"] = _truncate(record.conclusion, _SUMMARY_LIMIT)
        return common
    if level == DecisionAccessLevel.CONCLUSION:
        common["conclusion"] = record.conclusion
        return common
    # FULL
    return record.to_dict()


def filter_decisions(
    records: list[DecisionRecord],
    viewer: ViewerContext,
) -> list[dict[str, Any]]:
    """批量过滤 + 脱敏：返回查看者实际可见的决策列表（已按层级脱敏）。"""
    out: list[dict[str, Any]] = []
    for record in records:
        level = resolve_access(record=record, viewer=viewer)
        rendered = apply_level(record, level)
        if rendered is not None:
            rendered["access_level"] = str(level)
            out.append(rendered)
    return out


def decision_records_from_trace(
    trace_export: list[dict[str, Any]],
    *,
    agent_id: str,
    scope: DecisionScope | str = DecisionScope.PRIVATE,
    team_id: str = "",
) -> list[DecisionRecord]:
    """把 ``VisibilityTrace.export()`` 的整份输出投影为 ``DecisionRecord`` 列表。"""
    return [
        DecisionRecord.from_trace_entry(
            entry,
            agent_id=agent_id,
            scope=scope,
            team_id=team_id,
        )
        for entry in trace_export
        if isinstance(entry, dict) and entry.get("decision_point")
    ]


class DecisionAccessAudit:
    """决策查看 / 导出行为的防篡改审计链。

    每次查看（``record_view``）或导出共享案例（``record_export``）都追加到
    独立的 HMAC 链：``actor``（谁）、``agent_id``（哪个 Agent）、
    ``decision_point``（哪个决策）、``granted``（拿到哪一层）。
    复用 ``runtime/safety/audit/audit_chain.py``，与 ``org_audit`` 同构。
    """

    def __init__(
        self,
        *,
        chain_path: str | Path | None = None,
        secret: str | bytes | None = None,
    ) -> None:
        path = Path(chain_path) if chain_path else _default_chain_path()
        secret_bytes = _chain_secret(path=path, secret=secret)
        self._chain = AuditChain(
            path=path,
            keys={_DECISION_AUDIT_CHAIN_KEY_ID: secret_bytes},
            active_key_id=_DECISION_AUDIT_CHAIN_KEY_ID,
        )

    @property
    def path(self) -> Path:
        return self._chain.path

    def record(
        self,
        *,
        event_type: str,
        actor: str,
        agent_id: str,
        decision_point: str,
        granted: str,
        scope: DecisionScope | str = DecisionScope.PRIVATE,
        team_id: str = "",
        channel_id: str = "",
    ) -> dict[str, Any]:
        """追加一条决策访问审计记录，返回带 ``audit_chain`` 溯源的结果。"""
        event = str(event_type or "")
        if event not in DECISION_ACCESS_EVENT_TYPES:
            raise ValueError(f"unknown decision audit event_type {event!r}")
        payload = {
            "event_type": event,
            "actor": _clean(actor, limit=120),
            "agent_id": _clean(agent_id, limit=80),
            "decision_point": _clean(decision_point, limit=120),
            "scope": str(_coerce_scope(scope)),
            "team_id": _clean(team_id, limit=80),
            "channel_id": _clean(channel_id, limit=80),
            "granted": _clean(granted, limit=32),
            "ts": _iso(),
        }
        entry = self._chain.append(kind=event, payload=payload)
        payload["audit_chain"] = {
            "path": str(self._chain.path),
            "seq": entry.seq,
            "mac": entry.mac,
        }
        return payload

    def record_view(self, **kwargs: Any) -> dict[str, Any]:
        """记录一次决策查看。"""
        return self.record(event_type="decision_view", **kwargs)

    def record_export(self, **kwargs: Any) -> dict[str, Any]:
        """记录一次决策导出（共享案例场景）。"""
        return self.record(event_type="decision_export", **kwargs)

    def recent(
        self,
        *,
        limit: int = 50,
        actor: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """最近的行为记录，支持按 actor / agent 过滤；从尾部有界扫描。"""
        entries = self._chain.tail(max(1, min(limit, 2000)))
        result: list[dict[str, Any]] = []
        for entry in reversed(entries):
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            if actor and payload.get("actor") != actor:
                continue
            if agent_id and payload.get("agent_id") != agent_id:
                continue
            result.append(payload)
        return result

    def verify(self, *, limit: int | None = None) -> dict[str, Any]:
        """重新校验整条 MAC 链的完整性。"""
        report = self._chain.verify(limit=limit)
        return {
            "schema": "echo.decision_access_audit.v1",
            "path": str(self._chain.path),
            "ok": report.ok,
            "entries_checked": report.entries_checked,
            "broken_at": report.broken_at,
            "error": report.error,
        }

    def export_bundle(self) -> dict[str, Any]:
        """导出完整的可交付审计包（每条链行 + 完整性报告）。"""
        chain_text = (
            self._chain.path.read_text(encoding="utf-8") if self._chain.path.exists() else ""
        )
        chain_lines = [line for line in chain_text.splitlines() if line.strip()]
        integrity = self.verify()
        return {
            "schema": "echo.decision_access_export.v1",
            "chain_path": str(self._chain.path),
            "chain_sha256": hashlib.sha256(chain_text.encode("utf-8")).hexdigest(),
            "integrity": integrity,
            "chain": {"format": "jsonl", "line_count": len(chain_lines), "lines": chain_lines},
        }


# ── internals ────────────────────────────────────────────────────────────


def _downgrade(level: DecisionAccessLevel) -> DecisionAccessLevel:
    """把授权层级降一级（敏感决策点适用）。"""
    idx = _LEVEL_ORDER.index(level)
    return _LEVEL_ORDER[idx - 1] if idx > 0 else level


def _truncate(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit].rstrip()


def _coerce_scope(scope: DecisionScope | str) -> DecisionScope:
    if isinstance(scope, DecisionScope):
        return scope
    try:
        return DecisionScope(str(scope or ""))
    except ValueError:
        return DecisionScope.PRIVATE


def _clean(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()


def _iso(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def _default_chain_path() -> Path:
    from runtime.platform.process.paths import app_paths

    return app_paths().decision_audit_chain_path


def _chain_secret(*, path: Path, secret: str | bytes | None) -> bytes:
    if isinstance(secret, bytes) and secret:
        return secret
    if isinstance(secret, str) and secret.strip():
        return _secret_text_to_bytes(secret)
    env_secret = os.environ.get(_DECISION_AUDIT_CHAIN_SECRET_ENV)
    if env_secret:
        return _secret_text_to_bytes(env_secret)
    secret_path = path.with_suffix(path.suffix + ".secret")
    return _read_or_create_secret(secret_path)


def _read_or_create_secret(path: Path) -> bytes:
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return _secret_text_to_bytes(text)
    except FileNotFoundError:  # expected · no secret yet, mint one below
        pass
    generated = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        text = path.read_text(encoding="utf-8").strip()
        return _secret_text_to_bytes(text)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(generated)
        handle.write("\n")
    return bytes.fromhex(generated)


def _secret_text_to_bytes(value: str) -> bytes:
    text = value.strip()
    if not text:
        raise ValueError("decision audit secret cannot be empty")
    with_hex_prefix = text[2:] if text.startswith("0x") else text
    try:
        return bytes.fromhex(with_hex_prefix)
    except ValueError:
        return text.encode("utf-8")


__all__ = [
    "DECISION_ACCESS_EVENT_TYPES",
    "SENSITIVE_DECISION_POINTS",
    "DecisionAccessAudit",
    "DecisionAccessLevel",
    "DecisionRecord",
    "DecisionScope",
    "ViewerContext",
    "apply_level",
    "decision_records_from_trace",
    "filter_decisions",
    "resolve_access",
]
