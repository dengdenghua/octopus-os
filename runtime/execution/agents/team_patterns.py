"""Declarative collaboration patterns for realtime cowork turns.

The user chooses a product surface (General or Design) and, optionally, a
conversation preference.  They should not have to understand the runtime
topology.  This module turns the server-owned group state plus the current
message into a small, replayable orchestration decision.

Pattern selection is deliberately deterministic and side-effect free.  It is
not another model call, and it never broadens group membership or permissions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

PatternExecution = Literal["presence", "focused", "fanout", "orchestrated"]

_PATTERN_SCHEMA = "octopus.team_pattern_decision.v1"

_PRESENCE_QUERY_RE = re.compile(
    r"^(?:(?:大家|你们|各位|全员|所有人)\s*)?(?:都\s*)?"
    r"(?:在线(?:了)?|在吗|在不在|就位(?:了)?|到齐(?:了)?|准备好(?:了)?)"
    r"(?:吗|么|呢)?[?？!！。\s]*$",
    re.IGNORECASE,
)

# A natural-language group address is an explicit request to involve the
# roster even when the room's stored response preference is ordinary chat.
# Requiring both an audience word and a collaboration verb avoids waking the
# whole team for greetings such as "大家好".
_GROUP_WORK_REQUEST_RE = re.compile(
    r"(?:"
    r"(?:大家|各位|你们|全员|团队).{0,18}"
    r"(?:看看|看下|研究|调研|分析|评审|审查|讨论|给.{0,6}意见|说说|觉得|比较|对比|分工|分别)"
    r"|"
    r"(?:一起|分别|多角度|多个角度).{0,12}"
    r"(?:看看|看下|研究|调研|分析|评审|审查|讨论|回答|验证)"
    r")",
    re.IGNORECASE,
)

_ADVERSARIAL_RE = re.compile(
    r"(?:审查|评审|复核|找(?:出)?(?:问题|漏洞)|风险|反驳|挑战|辩论|互驳|"
    r"权衡|比较|对比|验证|测试|边界|失败路径|安全|回归|"
    r"critique|critic|review|challenge|debate|rebut|verify|risk|edge case)",
    re.IGNORECASE,
)

# Requests that require a durable result must not use the lightweight
# "everyone posts a 1-3 sentence bubble" path. That path deliberately has no
# tools and used to turn requests such as "研究一下 Eight Sleep" into five
# promises to work later, then mark the turn complete. Route concrete work to
# the coordinator/topology so the normal agent loop can use tools and deliver a
# verifiable result before the turn completes.
_DELIVERABLE_WORK_RE = re.compile(
    r"(?:"
    r"研究|调研|检索|搜索|查(?:一?下|找|资料|官网)|"
    r"实现|开发|编写|写(?:代码|脚本|文档|报告)|修改|修复|优化|重构|"
    # A bare mention of ``验收`` is not execution intent: people often ask
    # everyone for one short reply "用于界面验收".  ``测试``/``回归`` still route
    # real verification work through the coordinator.
    r"测试|回归|运行|执行|安装|配置|部署|发布|提交|推送|"
    r"生成|制作|导出|下载|制定|规划|策划|"
    r"做.{0,12}(?:报告|方案|策划|计划|清单|表格)|"
    r"整理(?:成|一份|报告|文档)"
    r")",
    re.IGNORECASE,
)

# A terse reaction is meaningful only in relation to the immediately preceding
# turn. Broadcasting it to every persona makes each member invent a different
# missing context ("???" became interface/CSS/story/finance guesses in a real
# room). Let the coordinator recover the thread and decide whether the original
# work should resume or be re-planned.
_COORDINATOR_FOLLOWUP_RE = re.compile(
    r"^(?:[?？!！.。…\s]{1,12}|"
    r"(?:怎么回事|什么情况|还没好|怎么还没|继续|接着|恢复|上面(?:的)?任务|"
    r"中断(?:的)?任务|上面中断任务|不是队长|你不是(?:\s*tl)?|队长呢|"
    r"让你(?:理解|拆解|分派|转述)).{0,80})$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TeamPatternSpec:
    id: str
    label: str
    execution: PatternExecution
    debate_rounds: int
    roles: tuple[str, ...]
    requires_group_request: bool = False


@dataclass(frozen=True)
class TeamPatternDecision:
    spec: TeamPatternSpec
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _PATTERN_SCHEMA,
            "id": self.spec.id,
            "label": self.spec.label,
            "execution": self.spec.execution,
            "debate_rounds": self.spec.debate_rounds,
            "roles": list(self.spec.roles),
            "reason": self.reason,
        }


TEAM_PATTERNS: dict[str, TeamPatternSpec] = {
    "presence_check": TeamPatternSpec(
        id="presence_check",
        label="成员状态",
        execution="presence",
        debate_rounds=0,
        roles=(),
    ),
    "focused_reply": TeamPatternSpec(
        id="focused_reply",
        label="定向回复",
        execution="focused",
        debate_rounds=1,
        roles=("responder",),
    ),
    "parallel_roundtable": TeamPatternSpec(
        id="parallel_roundtable",
        label="并行圆桌",
        execution="fanout",
        debate_rounds=1,
        roles=("explorer",),
        requires_group_request=True,
    ),
    "adversarial_review": TeamPatternSpec(
        id="adversarial_review",
        label="对抗评审",
        execution="fanout",
        debate_rounds=2,
        roles=("proposer", "critic", "verifier", "alternative"),
        requires_group_request=True,
    ),
    "coordinated_execution": TeamPatternSpec(
        id="coordinated_execution",
        label="协调执行",
        execution="orchestrated",
        debate_rounds=1,
        roles=("orchestrator", "worker", "verifier"),
    ),
}

_ROLE_LABELS = {
    "responder": "回应者",
    "explorer": "探索者",
    "proposer": "候选提出者",
    "critic": "质疑者",
    "verifier": "验证者",
    "alternative": "替代方案探索者",
    "orchestrator": "协调者",
    "worker": "执行者",
}


def is_team_presence_query(message: str) -> bool:
    """Whether the message only asks for current roster availability."""

    return bool(_PRESENCE_QUERY_RE.fullmatch(str(message or "").strip()))


def is_explicit_group_work_request(message: str) -> bool:
    """Whether natural language explicitly asks the group to contribute."""

    return bool(_GROUP_WORK_REQUEST_RE.search(str(message or "").strip()))


def requires_coordinated_execution(message: str) -> bool:
    """Whether the request needs execution/evidence instead of chat bubbles."""

    return bool(_DELIVERABLE_WORK_RE.search(str(message or "").strip()))


def is_coordinator_followup(message: str) -> bool:
    """Whether a short turn should be resolved by the leader with history."""

    return bool(_COORDINATOR_FOLLOWUP_RE.fullmatch(str(message or "").strip()))


def select_team_pattern(
    message: str,
    *,
    mode: str,
    member_count: int,
    addressed_count: int = 0,
) -> TeamPatternDecision:
    """Select the cheapest pattern that satisfies the user's group intent.

    Stored ``cluster``/``swarm`` values remain compatibility preferences.  A
    direct status question always takes the deterministic path.  In ordinary
    chat, only an @broadcast or an explicit natural-language group request may
    wake more than one agent.
    """

    text = str(message or "").strip()
    active_members = max(0, int(member_count or 0))
    addressed = max(0, int(addressed_count or 0))
    normalized_mode = str(mode or "chat").strip().lower()

    if is_team_presence_query(text):
        return TeamPatternDecision(TEAM_PATTERNS["presence_check"], "roster state query")

    if is_coordinator_followup(text):
        return TeamPatternDecision(
            TEAM_PATTERNS["focused_reply"],
            "context-dependent follow-up is recovered by the coordinator",
        )

    if addressed == 1:
        return TeamPatternDecision(
            TEAM_PATTERNS["focused_reply"],
            "one explicit @mention narrows the turn to that member",
        )

    group_requested = addressed > 1 or is_explicit_group_work_request(text)
    if (
        active_members > 1
        and requires_coordinated_execution(text)
        and (normalized_mode in {"cluster", "swarm"} or group_requested)
    ):
        return TeamPatternDecision(
            TEAM_PATTERNS["coordinated_execution"],
            "request requires a completed, verifiable deliverable",
        )

    if active_members > 1 and (normalized_mode == "swarm" or group_requested):
        if _ADVERSARIAL_RE.search(text):
            return TeamPatternDecision(
                TEAM_PATTERNS["adversarial_review"],
                "group request contains review, risk, comparison, or verification intent",
            )
        return TeamPatternDecision(
            TEAM_PATTERNS["parallel_roundtable"],
            "group request benefits from independent parallel viewpoints",
        )

    if active_members > 1 and normalized_mode == "cluster":
        return TeamPatternDecision(
            TEAM_PATTERNS["coordinated_execution"],
            "cluster preference delegates execution through the team coordinator",
        )

    return TeamPatternDecision(
        TEAM_PATTERNS["focused_reply"],
        "no explicit multi-agent work request",
    )


def pattern_member_role(pattern_id: str, index: int) -> str:
    """Assign a stable role without an extra planning/model call."""

    spec = TEAM_PATTERNS.get(str(pattern_id or ""), TEAM_PATTERNS["parallel_roundtable"])
    if not spec.roles:
        return "responder"
    safe_index = max(0, int(index or 0))
    return spec.roles[min(safe_index, len(spec.roles) - 1)]


def pattern_role_label(role: str) -> str:
    return _ROLE_LABELS.get(str(role or ""), str(role or "回应者"))


__all__ = [
    "TEAM_PATTERNS",
    "TeamPatternDecision",
    "TeamPatternSpec",
    "is_explicit_group_work_request",
    "is_coordinator_followup",
    "is_team_presence_query",
    "pattern_member_role",
    "pattern_role_label",
    "requires_coordinated_execution",
    "select_team_pattern",
]


