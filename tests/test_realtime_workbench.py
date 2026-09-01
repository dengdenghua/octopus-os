"""Tests for the workbench-snapshot helpers in realtime_workbench.py.

Focuses on the ``phase_kind`` business-phase mapping: every todo title the
backend surfaces via ``AgentPhaseSnapshot`` must carry a coarse
planning/exploring/implementing/testing/deploying/other tag so the frontend
can render a localized label instead of the raw technical wording.

The keyword sets and precedence mirror the frontend
``businessAgentPhaseKey`` mapping in
``frontend/src/components/workspace/agent-phases.ts`` — when a title matches
multiple categories, deploying wins over testing, testing over implementing,
implementing over planning, planning over exploring.
"""

from __future__ import annotations

from runtime.protocol import (
    AgentPhaseSnapshot,
    CommandExecutionItem,
    GroundingSource,
    ItemStatus,
    WorkspaceFocus,
)
from runtime.sensing.gateway.realtime_workbench import (
    _grounding_evidence,
    _phase_kind,
    _phases_from_plan_md,
    _phases_from_todo_preview,
    _tool_evidence,
    _workbench_snapshot,
)

# ── _phase_kind: the five business phases ──────────────────────────


def test_phase_kind_deploying_matches_english_keywords() -> None:
    assert _phase_kind("Deploy to production") == "deploying"
    assert _phase_kind("Release the new build") == "deploying"
    assert _phase_kind("Publish package to registry") == "deploying"
    assert _phase_kind("Ship the feature") == "deploying"


def test_phase_kind_deploying_matches_chinese_keywords() -> None:
    assert _phase_kind("部署到生产环境") == "deploying"
    assert _phase_kind("上线新版本") == "deploying"
    assert _phase_kind("发布服务") == "deploying"


def test_phase_kind_testing_matches_english_keywords() -> None:
    assert _phase_kind("Run unit tests") == "testing"
    assert _phase_kind("Verify the fix") == "testing"
    assert _phase_kind("Validate inputs") == "testing"
    assert _phase_kind("Check lint output") == "testing"
    assert _phase_kind("Build the project") == "testing"


def test_phase_kind_testing_matches_chinese_keywords() -> None:
    assert _phase_kind("运行测试") == "testing"
    assert _phase_kind("验证行为") == "testing"
    assert _phase_kind("构建产物") == "testing"
    assert _phase_kind("打包步骤") == "testing"


def test_phase_kind_implementing_matches_english_keywords() -> None:
    assert _phase_kind("Implement the reducer") == "implementing"
    assert _phase_kind("Edit the config file") == "implementing"
    assert _phase_kind("Fix the bug") == "implementing"
    assert _phase_kind("Refactor the module") == "implementing"
    assert _phase_kind("Create a new helper") == "implementing"
    assert _phase_kind("Patch the hole") == "implementing"


def test_phase_kind_implementing_matches_chinese_keywords() -> None:
    assert _phase_kind("实现接口") == "implementing"
    assert _phase_kind("修改逻辑") == "implementing"
    assert _phase_kind("修复缺陷") == "implementing"
    assert _phase_kind("重构代码") == "implementing"
    assert _phase_kind("新增字段") == "implementing"


def test_phase_kind_planning_matches_english_keywords() -> None:
    assert _phase_kind("Plan the work") == "planning"
    assert _phase_kind("Design the schema") == "planning"
    assert _phase_kind("Scope the project") == "planning"
    assert _phase_kind("Spec the requirements") == "planning"


def test_phase_kind_planning_matches_chinese_keywords() -> None:
    assert _phase_kind("规划任务") == "planning"
    assert _phase_kind("设计方案") == "planning"
    assert _phase_kind("计划迭代") == "planning"


def test_phase_kind_exploring_matches_english_keywords() -> None:
    assert _phase_kind("Explore the repository") == "exploring"
    assert _phase_kind("Read the README") == "exploring"
    assert _phase_kind("Analyze the failure") == "exploring"
    assert _phase_kind("Investigate the logs") == "exploring"
    assert _phase_kind("Research alternatives") == "exploring"
    assert _phase_kind("Review the diff") == "exploring"


def test_phase_kind_exploring_matches_chinese_keywords() -> None:
    assert _phase_kind("浏览代码") == "exploring"
    assert _phase_kind("阅读文档") == "exploring"
    assert _phase_kind("分析问题") == "exploring"
    assert _phase_kind("调研现状") == "exploring"
    assert _phase_kind("排查故障") == "exploring"


# ── _phase_kind: fallback + precedence ─────────────────────────────


def test_phase_kind_other_when_no_keyword_matches() -> None:
    assert _phase_kind("Wrap up") == "other"
    assert _phase_kind("收尾") == "other"
    assert _phase_kind("Something unrelated") == "other"


def test_phase_kind_is_case_insensitive() -> None:
    assert _phase_kind("DEPLOY NOW") == "deploying"
    assert _phase_kind("TestTheThing") == "testing"
    assert _phase_kind("IMPLEMENT") == "implementing"


def test_phase_kind_deploying_takes_precedence_over_testing() -> None:
    """A title mentioning both deploy and test classifies as deploying."""
    assert _phase_kind("Test the deploy script") == "deploying"
    assert _phase_kind("部署测试环境") == "deploying"


def test_phase_kind_testing_takes_precedence_over_implementing() -> None:
    assert _phase_kind("Build the implementation") == "testing"


def test_phase_kind_implementing_takes_precedence_over_planning() -> None:
    assert _phase_kind("Write the design doc") == "implementing"


def test_phase_kind_planning_takes_precedence_over_exploring() -> None:
    assert _phase_kind("Review the plan") == "planning"


# ── _phases_from_todo_preview: phase_kind wiring ───────────────────


def test_phases_from_todo_preview_populates_phase_kind_for_each_entry() -> None:
    preview = {
        "items": [
            {"content": "Plan the work", "status": "completed"},
            {"content": "Investigate the issue", "status": "in_progress"},
            {"content": "Implement the reducer", "status": "pending"},
            {"content": "Run the test suite", "status": "pending"},
            {"content": "Deploy to staging", "status": "pending"},
        ]
    }
    phases = _phases_from_todo_preview(preview)
    assert phases is not None
    assert [p.phase_kind for p in phases] == [
        "planning",
        "exploring",
        "implementing",
        "testing",
        "deploying",
    ]


def test_phases_from_todo_preview_defaults_to_other_when_no_match() -> None:
    preview = {
        "items": [
            {"content": "Wrap up", "status": "completed"},
            {"content": "Something unrelated", "status": "in_progress"},
        ]
    }
    phases = _phases_from_todo_preview(preview)
    assert phases is not None
    assert [p.phase_kind for p in phases] == ["other", "other"]


def test_phases_from_todo_preview_phase_kind_survives_machine_prefix() -> None:
    """The machine prefix (``Phase 1:``) is stripped from the displayed
    title but must not interfere with keyword matching — the business
    phase is derived from the raw todo content."""
    preview = {
        "items": [
            {"content": "Phase 1: Deploy the service", "status": "completed"},
            {"content": "步骤 2：验证行为", "status": "in_progress"},
        ]
    }
    phases = _phases_from_todo_preview(preview)
    assert phases is not None
    assert phases[0].phase_kind == "deploying"
    assert phases[0].title == "Deploy the service"
    assert phases[1].phase_kind == "testing"
    assert phases[1].title == "验证行为"


# ── _phases_from_plan_md: plan.md → phases ─────────────────────────


def test_phases_from_plan_md_checkbox_list_marks_first_incomplete_running() -> None:
    preview = {
        "path": "plan.md",
        "content": "- [x] Investigate the issue\n- [ ] Implement the fix\n- [ ] Run the tests",
    }
    phases = _phases_from_plan_md(preview, active_item_id="tool-1")
    assert phases is not None
    assert [p.title for p in phases] == [
        "Investigate the issue",
        "Implement the fix",
        "Run the tests",
    ]
    assert [p.status for p in phases] == ["done", "running", "pending"]
    assert [p.active_item_id for p in phases] == [None, "tool-1", None]
    assert [p.phase_kind for p in phases] == ["exploring", "implementing", "testing"]


def test_phases_from_plan_md_ordered_list() -> None:
    preview = {
        "path": "PLAN.md",
        "content": "1. Collect evidence\n2. Cross-check sources\n3. Write the report",
    }
    phases = _phases_from_plan_md(preview)
    assert phases is not None
    assert [p.title for p in phases] == [
        "Collect evidence",
        "Cross-check sources",
        "Write the report",
    ]
    # First incomplete item is the current step; the rest stay pending.
    assert [p.status for p in phases] == ["running", "pending", "pending"]


def test_phases_from_plan_md_unordered_list() -> None:
    preview = {
        "path": "plan_final.md",
        "content": "- Gather market data\n- Analyze competitors\n- Draft findings",
    }
    phases = _phases_from_plan_md(preview)
    assert phases is not None
    assert [p.title for p in phases] == [
        "Gather market data",
        "Analyze competitors",
        "Draft findings",
    ]


def test_phases_from_plan_md_heading_fallback_drops_section_words() -> None:
    preview = {
        "path": "plan.md",
        "content": ("## Steps\n## Investigate root cause\n## Deliverable\n## Verify the fix\n"),
    }
    phases = _phases_from_plan_md(preview)
    assert phases is not None
    # "Steps" and "Deliverable" are organizational section words, dropped.
    assert [p.title for p in phases] == ["Investigate root cause", "Verify the fix"]


def test_phases_from_plan_md_ignores_non_plan_file() -> None:
    preview = {
        "path": "notes.md",
        "content": "- [ ] Collect evidence\n- [ ] Write report",
    }
    assert _phases_from_plan_md(preview) is None


def test_phases_from_plan_md_requires_two_items() -> None:
    preview = {"path": "plan.md", "content": "- [ ] Only one step"}
    assert _phases_from_plan_md(preview) is None


def test_phases_from_plan_md_skips_code_fences() -> None:
    preview = {
        "path": "plan.md",
        "content": ("- [ ] First step\n```\n- [ ] Not a real step\n```\n- [ ] Second step\n"),
    }
    phases = _phases_from_plan_md(preview)
    assert phases is not None
    assert [p.title for p in phases] == ["First step", "Second step"]


def test_phases_from_plan_md_returns_none_for_missing_content() -> None:
    assert _phases_from_plan_md({"path": "plan.md"}) is None
    assert _phases_from_plan_md({"path": "plan.md", "content": ""}) is None


def test_grounding_sources_become_replayable_workbench_evidence() -> None:
    evidence = _grounding_evidence(
        [GroundingSource(kind="source", title="Gateway", path="runtime/gateway.py:12")]
    )

    assert len(evidence) == 1
    assert evidence[0].uri == "runtime/gateway.py:12"
    assert evidence[0].origin == "grounding"


def test_successful_file_search_records_only_confirmed_result_paths() -> None:
    item = CommandExecutionItem(
        id="cmd-search",
        command="grep_text",
        input_preview={"pattern": "EvidenceReference", "glob": "**/*.py"},
        aggregated_output=(
            '{"matches":[{"path":"runtime/protocol/items.py","line":201}],"count":1}'
        ),
        status=ItemStatus.COMPLETED,
    )

    evidence = _tool_evidence(item, phase_id="todo-phase:1")

    assert [entry.uri for entry in evidence] == ["runtime/protocol/items.py"]
    assert evidence[0].source_item_id == "cmd-search"
    assert evidence[0].phase_id == "todo-phase:1"


def test_failed_tool_never_manufactures_workbench_evidence() -> None:
    item = CommandExecutionItem(
        command="read_file",
        input_preview={"path": "runtime/missing.py"},
        status=ItemStatus.FAILED,
    )

    assert _tool_evidence(item) == []


def test_logical_read_error_never_manufactures_workbench_evidence() -> None:
    item = CommandExecutionItem(
        command="read_file",
        input_preview={"path": "runtime/missing.py"},
        aggregated_output='{"error":"not found: runtime/missing.py"}',
        status=ItemStatus.COMPLETED,
    )

    assert _tool_evidence(item) == []


def test_terminal_pending_phase_does_not_retain_stale_current_tool() -> None:
    snapshot = _workbench_snapshot(
        version=3,
        phases=[
            AgentPhaseSnapshot(id="phase-1", index=1, total=2, title="Implement", status="done"),
            AgentPhaseSnapshot(
                id="phase-2", index=2, total=2, title="Optional follow-up", status="pending"
            ),
        ],
        workspace_focus=WorkspaceFocus(
            itemId="tool-finished",
            view="terminal",
            title="Finished command",
        ),
    )

    assert snapshot.current_phase_id == "phase-2"
    assert snapshot.current_item_id is None
    # Preserve the last useful surface for inspection without claiming that
    # its finished tool is still the live work item.
    assert snapshot.workspace_focus is not None
    assert snapshot.workspace_focus.item_id == "tool-finished"


def test_running_phase_keeps_current_tool_focus() -> None:
    snapshot = _workbench_snapshot(
        version=1,
        phases=[
            AgentPhaseSnapshot(
                id="phase-1",
                index=1,
                total=1,
                title="Verify",
                status="running",
            )
        ],
        workspace_focus=WorkspaceFocus(
            itemId="tool-running",
            view="terminal",
            title="Running command",
        ),
    )

    assert snapshot.current_item_id == "tool-running"

