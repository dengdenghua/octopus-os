"""Tests for AgentPhaseSnapshot.phase_kind business-phase mapping.

Spec source: stream UX synergy optimization, Task 2.

The backend maps a free-form todo title to one of six coarse phase kinds
(planning / exploring / implementing / testing / deploying / other) so
the frontend workbench outline can render a localized label instead of
raw model wording. The mapping mirrors the frontend
``businessAgentPhaseKey`` so both sides agree on classification.

Coverage goals pinned here:

  * each of the 5 named kinds maps for unambiguous English + Chinese keywords
  * precedence: deploy > test > implement > plan > explore (a title like
    "test the deploy script" classifies as deploying)
  * unmatched titles fall back to "other"
  * AgentPhaseSnapshot defaults to "other" when phase_kind is omitted
  * _phases_from_todo_preview threads phase_kind through to each snapshot

Note on keyword ambiguity: the implementing pattern includes broad verbs
(`write`/`add`/`update`/`change`) that overlap with planning/exploring
nouns. Test titles are chosen to be unambiguous — a title like
"Write the spec" classifies as implementing because `write` matches
before `spec`, which is the documented precedence (deploy > test >
implement > plan > explore). Tests here pin the documented behavior,
not an idealized classification.
"""

from __future__ import annotations

import pytest

from runtime.protocol import AgentPhaseSnapshot
from runtime.sensing.gateway.realtime_workbench import (
    _phase_kind,
    _phases_from_todo_preview,
)

# ── _phase_kind: unambiguous English keywords ─────────────────────


@pytest.mark.parametrize(
    "title, expected",
    [
        # planning: avoid "write"/"design" as verb (implementing pattern)
        ("Plan the migration", "planning"),
        ("Todo list for sprint", "planning"),
        ("Spec out the API", "planning"),
        # exploring: avoid "code"/"read"/"inspect" overlap (inspect contains "spec")
        ("Explore the dataset", "exploring"),
        ("Analyze the failure", "exploring"),
        ("Research alternatives", "exploring"),
        ("Review the PR", "exploring"),
        ("Investigate the bug", "exploring"),
        ("Study the docs", "exploring"),
        # implementing
        ("Implement the handler", "implementing"),
        ("Edit the config", "implementing"),
        ("Fix the bug", "implementing"),
        ("Refactor the module", "implementing"),
        ("Add a new file", "implementing"),
        ("Update the docs", "implementing"),
        ("Patch the leak", "implementing"),
        # testing
        ("Run the tests", "testing"),
        ("Verify the output", "testing"),
        ("Validate the input", "testing"),
        ("Check the build", "testing"),
        ("Lint the code", "testing"),
        # deploying
        ("Deploy to staging", "deploying"),
        ("Release v2", "deploying"),
        ("Publish the package", "deploying"),
        ("Ship the fix", "deploying"),
    ],
)
def test_phase_kind_english_keywords(title: str, expected: str) -> None:
    assert _phase_kind(title) == expected


# ── _phase_kind: Chinese keywords (mirror frontend test set) ───────


@pytest.mark.parametrize(
    "title, expected",
    [
        ("分析需求并给出方案", "planning"),
        ("了解代码结构", "exploring"),
        ("修改登录页实现", "implementing"),
        ("运行测试验证修改", "testing"),
        ("部署到预发环境", "deploying"),
        ("规划方案", "planning"),
        ("浏览结构", "exploring"),
        ("排查问题", "exploring"),
        ("重构模块", "implementing"),
        ("新增字段", "implementing"),
        ("检查构建", "testing"),
        ("上线 v2", "deploying"),
    ],
)
def test_phase_kind_chinese_keywords(title: str, expected: str) -> None:
    assert _phase_kind(title) == expected


# ── _phase_kind: precedence (deploy > test > implement > plan > explore) ──


@pytest.mark.parametrize(
    "title, expected",
    [
        # deploy takes precedence over test
        ("test the deploy script", "deploying"),
        # test takes precedence over implement
        ("verify the patch we just wrote", "testing"),
        # implement takes precedence over plan
        ("plan how to fix the bug", "implementing"),
        # plan takes precedence over explore
        ("explore the design space", "planning"),
    ],
)
def test_phase_kind_precedence(title: str, expected: str) -> None:
    assert _phase_kind(title) == expected


# ── _phase_kind: documented keyword-overlap behavior ──────────────
#
# These pin the actual classification for titles whose words span
# multiple patterns. The precedence is deploy > test > implement >
# plan > explore, so the implementing pattern's broad verbs win over
# planning/exploring nouns. Documented here so a future change to the
# precedence order surfaces in CI rather than silently flipping UI.


def test_phase_kind_write_spec_is_implementing_not_planning() -> None:
    """`write` matches implementing before `spec` matches planning."""
    assert _phase_kind("Write the spec") == "implementing"


def test_phase_kind_explore_codebase_is_implementing_not_exploring() -> None:
    """`code` matches implementing before `explore` matches exploring."""
    assert _phase_kind("Explore the codebase") == "implementing"


def test_phase_kind_inspect_matches_planning_via_spec_substring() -> None:
    """`inspect` contains `spec` → planning (substring match, documented trap).

    The planning pattern uses `spec` which matches anywhere in the title;
    `inspect` contains `spec` so it classifies as planning even though the
    user likely meant exploring. Pinned so a future word-boundary fix
    surfaces in CI.
    """
    assert _phase_kind("Inspect the logs") == "planning"


def test_phase_kind_qa_release_is_deploying_via_release_precedence() -> None:
    """`release` matches deploying before `qa` matches testing (precedence)."""
    assert _phase_kind("QA the release") == "deploying"


def test_phase_kind_chinese_qian_yi_is_implementing_via_substring() -> None:
    """`迁移` in the implementing pattern matches before `规划` matches planning.

    A title like "规划迁移方案" (planning a migration) classifies as
    implementing because `迁移` is in the implementing pattern and
    implementing has higher precedence than planning. Pinned so a future
    pattern tightening surfaces in CI.
    """
    assert _phase_kind("规划迁移方案") == "implementing"


# ── _phase_kind: fallback to "other" ───────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "",
        "   ",
        "random text with no keywords",
        ".misc",
        "你好世界",
        "do the thing",
    ],
)
def test_phase_kind_fallback_to_other(title: str) -> None:
    assert _phase_kind(title) == "other"


# ── AgentPhaseSnapshot default ─────────────────────────────────────


def test_agent_phase_snapshot_defaults_to_other() -> None:
    """Omitting phase_kind must yield "other", not None or empty."""
    snap = AgentPhaseSnapshot(
        id="p1",
        index=1,
        total=3,
        title="anything",
        status="pending",
    )
    assert snap.phase_kind == "other"


def test_agent_phase_snapshot_phase_kind_round_trip() -> None:
    """Explicit phase_kind survives serialization."""
    snap = AgentPhaseSnapshot(
        id="p1",
        index=1,
        total=3,
        title="Deploy",
        status="done",
        phase_kind="deploying",
    )
    dumped = snap.model_dump(by_alias=True)
    # phase_kind has no alias, so the wire name is the field name.
    assert dumped["phase_kind"] == "deploying"
    restored = AgentPhaseSnapshot.model_validate(dumped)
    assert restored.phase_kind == "deploying"


# ── _phases_from_todo_preview threads phase_kind through ───────────


def test_phases_from_todo_preview_assigns_phase_kind() -> None:
    """Each AgentPhaseSnapshot built from a todo preview carries a phase_kind
    derived from its title; matched titles classify, unmatched → "other"."""
    preview = {
        "items": [
            {"content": "Analyze the requirements", "status": "completed"},
            {"content": "Implement the handler", "status": "in_progress"},
            {"content": "Random unrelated title", "status": "pending"},
            {"content": "Run the tests", "status": "pending"},
        ]
    }
    phases = _phases_from_todo_preview(preview)
    assert phases is not None
    assert len(phases) == 4
    assert phases[0].phase_kind == "exploring"  # Analyze → exploring
    assert phases[1].phase_kind == "implementing"  # Implement → implementing
    assert phases[2].phase_kind == "other"  # unmatched → other
    assert phases[3].phase_kind == "testing"  # Run the tests → testing


def test_phases_from_todo_preview_empty_returns_none() -> None:
    """Fewer than 2 items is not a real plan → None (existing contract)."""
    assert _phases_from_todo_preview({"items": []}) is None
    assert _phases_from_todo_preview({"items": [{"content": "solo"}]}) is None
    assert _phases_from_todo_preview(None) is None

