"""Tests for ``runtime.safety.organization.decision_visibility`` (P1 · 协作可见性控制).

Covers:
  1. 可见性矩阵:scope × 查看者角色 → 层级(含 Agent 自身 / 组织外 / viewer 降级)
  2. 敏感决策点整体降级
  3. apply_level 脱敏:summary 无 basis、conclusion 无 details、hidden 过滤
  4. 与 ``_visibility_trace.export()`` 的衔接(from_trace_entry / decision_records_from_trace)
  5. DecisionAccessAudit:记录/查询/导出/校验 + 篡改检测
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.core.cerebrum._visibility_trace import (
    new_trace,
    record_visibility,
    reset_active_trace,
    set_active_trace,
)
from runtime.safety.organization import decision_visibility as dv
from runtime.safety.organization.decision_visibility import (
    DECISION_ACCESS_EVENT_TYPES,
    DecisionAccessAudit,
    DecisionAccessLevel,
    DecisionRecord,
    DecisionScope,
    ViewerContext,
    apply_level,
    decision_records_from_trace,
    filter_decisions,
    resolve_access,
)

SECRET = "00" * 32


def _record(
    *,
    agent_id: str = "agent-1",
    scope: DecisionScope = DecisionScope.TEAM,
    team_id: str = "room-a",
    decision_point: str = "capability_router.activate",
    conclusion: str = "激活 3 项能力",
    basis: str = "目标匹配度 TF-IDF 前 3",
) -> DecisionRecord:
    return DecisionRecord(
        agent_id=agent_id,
        decision_point=decision_point,
        conclusion=conclusion,
        basis=basis,
        scope=scope,
        team_id=team_id,
        ts=1.0,
        details={"matched": ["A", "B", "C"]},
    )


def _viewer(
    *,
    member_id: str = "member-1",
    kind: str = "human",
    org_role: str | None = "member",
    team_ids: frozenset[str] = frozenset({"room-a"}),
) -> ViewerContext:
    return ViewerContext(
        member_id=member_id,
        kind=kind,
        org_role=org_role,
        team_ids=team_ids,
    )


# ── 可见性矩阵 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("scope", "viewer", "expected"),
    [
        # private
        (
            DecisionScope.PRIVATE,
            _viewer(member_id="agent-1", kind="agent", org_role="member"),
            DecisionAccessLevel.FULL,
        ),
        (DecisionScope.PRIVATE, _viewer(org_role="member"), DecisionAccessLevel.SUMMARY),
        (
            DecisionScope.PRIVATE,
            _viewer(org_role="owner", team_ids=frozenset({"room-b"})),
            DecisionAccessLevel.CONCLUSION,
        ),
        (
            DecisionScope.PRIVATE,
            _viewer(org_role="admin", team_ids=frozenset({"room-b"})),
            DecisionAccessLevel.CONCLUSION,
        ),
        (
            DecisionScope.PRIVATE,
            _viewer(org_role="member", team_ids=frozenset({"room-b"})),
            DecisionAccessLevel.HIDDEN,
        ),
        (
            DecisionScope.PRIVATE,
            _viewer(org_role="viewer", team_ids=frozenset({"room-b"})),
            DecisionAccessLevel.HIDDEN,
        ),
        (DecisionScope.PRIVATE, _viewer(org_role=None), DecisionAccessLevel.HIDDEN),
        # team
        (DecisionScope.TEAM, _viewer(member_id="agent-1", kind="agent"), DecisionAccessLevel.FULL),
        (DecisionScope.TEAM, _viewer(org_role="member"), DecisionAccessLevel.FULL),
        (
            DecisionScope.TEAM,
            _viewer(org_role="owner", team_ids=frozenset({"room-b"})),
            DecisionAccessLevel.FULL,
        ),
        (
            DecisionScope.TEAM,
            _viewer(org_role="member", team_ids=frozenset({"room-b"})),
            DecisionAccessLevel.SUMMARY,
        ),
        (
            DecisionScope.TEAM,
            _viewer(org_role="viewer", team_ids=frozenset({"room-b"})),
            DecisionAccessLevel.CONCLUSION,
        ),
        (DecisionScope.TEAM, _viewer(org_role=None), DecisionAccessLevel.HIDDEN),
        # org
        (DecisionScope.ORG, _viewer(member_id="agent-1", kind="agent"), DecisionAccessLevel.FULL),
        (DecisionScope.ORG, _viewer(org_role="member"), DecisionAccessLevel.FULL),
        (DecisionScope.ORG, _viewer(org_role="viewer"), DecisionAccessLevel.CONCLUSION),
        (DecisionScope.ORG, _viewer(org_role=None), DecisionAccessLevel.HIDDEN),
    ],
)
def test_resolve_access_matrix(
    scope: DecisionScope, viewer: ViewerContext, expected: DecisionAccessLevel
) -> None:
    record = _record(scope=scope)
    assert resolve_access(record=record, viewer=viewer) == expected


def test_private_admin_in_team_still_gets_conclusion() -> None:
    """PRIVATE 记录的可见性矩阵：组织管理员即使恰好在记录所属团队内，
    也应拿到 conclusion（审计视图），而不是被同团队分支降为 summary。

    回归：in_team 分支原先排在 admin 之前，团队内管理员反而比团队外
    管理员看得少（summary < conclusion），与 docstring 及矩阵意图矛盾。
    """
    record = _record(scope=DecisionScope.PRIVATE, team_id="room-a")
    # 管理员默认在 room-a（即记录所属团队）→ 期望 conclusion
    admin_in_team = _viewer(org_role="admin")
    assert admin_in_team.team_ids == frozenset({"room-a"}), "fixture 前提"
    assert resolve_access(record=record, viewer=admin_in_team) == DecisionAccessLevel.CONCLUSION
    # 对照：团队外管理员也是 conclusion（原有行为，保持不变）
    admin_outside_team = _viewer(org_role="admin", team_ids=frozenset({"room-b"}))
    assert (
        resolve_access(record=record, viewer=admin_outside_team) == DecisionAccessLevel.CONCLUSION
    )
    # 对照：非管理员的团队成员仍只有 summary
    member_in_team = _viewer(org_role="member")
    assert resolve_access(record=record, viewer=member_in_team) == DecisionAccessLevel.SUMMARY


def test_sensitive_decision_point_downgrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dv, "SENSITIVE_DECISION_POINTS", frozenset({"tool.call"}))
    record = _record(decision_point="tool.call")
    # 同团队 member:full → conclusion
    assert (
        resolve_access(record=record, viewer=_viewer(org_role="member"))
        == DecisionAccessLevel.CONCLUSION
    )
    # 组织 admin 不受敏感降级影响
    assert (
        resolve_access(
            record=record, viewer=_viewer(org_role="admin", team_ids=frozenset({"room-b"}))
        )
        == DecisionAccessLevel.FULL
    )
    # 非敏感决策点不受影响
    other = _record()
    assert (
        resolve_access(record=other, viewer=_viewer(org_role="member")) == DecisionAccessLevel.FULL
    )


def test_agent_self_always_full_even_private() -> None:
    record = _record(scope=DecisionScope.PRIVATE)
    assert (
        resolve_access(record=record, viewer=_viewer(member_id="agent-1", kind="agent"))
        == DecisionAccessLevel.FULL
    )


def test_unknown_scope_coerces_to_private() -> None:
    record = _record(scope=DecisionScope.PRIVATE)
    # scope 落在 org 外成员 → private 下为 hidden(最小暴露默认)
    assert (
        resolve_access(
            record=record, viewer=_viewer(org_role="member", team_ids=frozenset({"room-b"}))
        )
        == DecisionAccessLevel.HIDDEN
    )


# ── 脱敏 ────────────────────────────────────────────────────────────────────


def test_apply_level_redacts_by_level() -> None:
    record = _record()
    full = apply_level(record, DecisionAccessLevel.FULL)
    assert (
        full is not None
        and full["basis"] == record.basis
        and full["details"]["matched"] == ["A", "B", "C"]
    )

    conclusion = apply_level(record, DecisionAccessLevel.CONCLUSION)
    assert conclusion is not None
    assert conclusion["conclusion"] == record.conclusion
    assert "basis" not in conclusion and "details" not in conclusion

    summary = apply_level(record, DecisionAccessLevel.SUMMARY)
    assert summary is not None
    assert "decision_point" in summary and "summary" in summary
    assert "basis" not in summary and "details" not in summary

    assert apply_level(record, DecisionAccessLevel.HIDDEN) is None


def test_summary_truncates_long_conclusion() -> None:
    record = _record(conclusion="x" * 500)
    summary = apply_level(record, DecisionAccessLevel.SUMMARY)
    assert summary is not None and len(summary["summary"]) <= 80


def test_filter_decisions_drops_hidden_and_annotates_level() -> None:
    records = [
        _record(scope=DecisionScope.PRIVATE),  # 组织外成员 → hidden
        _record(scope=DecisionScope.TEAM),  # 同团队 → full
    ]
    viewer = _viewer(org_role=None)
    result = filter_decisions(records, viewer)
    assert len(result) == 0

    # 组织成员但不在 room-a → private hidden、team 降为 summary
    viewer = _viewer(org_role="member", team_ids=frozenset({"room-b"}))
    result = filter_decisions(records, viewer)
    assert len(result) == 1
    assert result[0]["decision_point"] == "capability_router.activate"
    assert result[0]["access_level"] == str(DecisionAccessLevel.SUMMARY)


# ── 与 visibility trace 衔接 ─────────────────────────────────────────────────


def test_from_trace_entry_bridges_visibility_trace_export() -> None:
    trace = new_trace()
    token = set_active_trace(trace)
    try:
        record_visibility(
            "context.skill_catalog",
            "截断技能目录",
            "目录 120 项,保留 100 项",
            total=120,
            kept=100,
        )
    finally:
        reset_active_trace(token)
    exported = trace.export()
    assert len(exported) == 1
    record = DecisionRecord.from_trace_entry(
        exported[0],
        agent_id="agent-1",
        scope=DecisionScope.ORG,
    )
    assert record.agent_id == "agent-1"
    assert record.decision_point == "context.skill_catalog"
    assert record.basis == "目录 120 项,保留 100 项"
    assert record.scope == DecisionScope.ORG
    assert record.details == {"total": 120, "kept": 100}


def test_decision_records_from_trace_skips_junk_entries() -> None:
    trace = new_trace()
    token = set_active_trace(trace)
    try:
        record_visibility("capability_router.activate", "激活", "依据")
    finally:
        reset_active_trace(token)
    records = decision_records_from_trace(
        [*trace.export(), {"decision_point": ""}, "junk", None, {}],
        agent_id="agent-1",
    )
    assert len(records) == 1
    assert records[0].decision_point == "capability_router.activate"


def test_viewer_context_from_dict() -> None:
    viewer = ViewerContext.from_dict(
        {"member_id": "m1", "kind": "human", "org_role": "admin", "team_ids": ["a", "b"]}
    )
    assert viewer.team_ids == frozenset({"a", "b"})
    assert viewer.org_role == "admin"


# ── 决策查看审计 ─────────────────────────────────────────────────────────────


def test_audit_records_view_and_export(tmp_path: Path) -> None:
    audit = DecisionAccessAudit(chain_path=tmp_path / "audit.jsonl", secret=SECRET)
    view = audit.record_view(
        actor="alice",
        agent_id="agent-1",
        decision_point="capability_router.activate",
        granted="full",
        scope=DecisionScope.TEAM,
        team_id="room-a",
    )
    export = audit.record_export(
        actor="alice",
        agent_id="agent-1",
        decision_point="context.skill_catalog",
        granted="conclusion",
        scope=DecisionScope.ORG,
        channel_id="chan-1",
    )
    assert view["event_type"] == "decision_view"
    assert view["actor"] == "alice" and view["agent_id"] == "agent-1"
    assert export["event_type"] == "decision_export"
    assert export["channel_id"] == "chan-1"
    assert audit.verify()["ok"] is True


def test_audit_recent_filters(tmp_path: Path) -> None:
    audit = DecisionAccessAudit(chain_path=tmp_path / "audit.jsonl", secret=SECRET)
    for i in range(3):
        audit.record_view(
            actor="alice", agent_id=f"agent-{i}", decision_point="d", granted="summary"
        )
    audit.record_view(actor="bob", agent_id="agent-9", decision_point="d", granted="full")
    assert len(audit.recent()) == 4
    assert len(audit.recent(actor="alice")) == 3
    assert len(audit.recent(agent_id="agent-9")) == 1
    # 有界扫描:limit 仅影响尾部扫描范围
    assert len(audit.recent(limit=2)) == 2


def test_audit_tamper_detection(tmp_path: Path) -> None:
    audit = DecisionAccessAudit(chain_path=tmp_path / "audit.jsonl", secret=SECRET)
    audit.record_view(actor="alice", agent_id="agent-1", decision_point="d", granted="full")
    audit.record_view(actor="bob", agent_id="agent-2", decision_point="d", granted="full")
    assert audit.verify()["ok"] is True
    # 篡改第一条记录 → 整条链校验失败
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"actor": "alice"', '"actor": "mallory"')
    (tmp_path / "audit.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = audit.verify()
    assert report["ok"] is False
    assert report["broken_at"] == 0


def test_audit_export_bundle(tmp_path: Path) -> None:
    audit = DecisionAccessAudit(chain_path=tmp_path / "audit.jsonl", secret=SECRET)
    audit.record_view(actor="alice", agent_id="agent-1", decision_point="d", granted="full")
    bundle = audit.export_bundle()
    assert bundle["chain"]["line_count"] == 1
    assert bundle["integrity"]["ok"] is True
    assert bundle["chain_sha256"]


def test_audit_rejects_unknown_event_type(tmp_path: Path) -> None:
    audit = DecisionAccessAudit(chain_path=tmp_path / "audit.jsonl", secret=SECRET)
    with pytest.raises(ValueError):
        audit.record(
            event_type="hack", actor="alice", agent_id="agent-1", decision_point="d", granted="full"
        )


def test_audit_event_types_surface() -> None:
    assert {"decision_view", "decision_export"} == DECISION_ACCESS_EVENT_TYPES

