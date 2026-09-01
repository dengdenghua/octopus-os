"""Colloquial repair imperatives must gate on write evidence.

``_code_mode_missing_write_guard`` already refuses to finish an implementation
task with no successful write, but it is gated on
``_goal_requests_code_mutation``, whose vocabulary only held formal verbs. So
"干活啊" and "开始优化" returned False and the guard stayed silent through turns
that produced zero file changes (trn_3348dff0b9e54a99, trn_7e2403ef8c5a42db).
Widening the positive vocabulary requires widening the negation handling with
it, or "别改吧" becomes an implementation mandate.
"""

from __future__ import annotations

import pytest

from runtime.core.cerebrum.react_code_mode_guards import _code_mode_missing_write_guard
from runtime.core.cerebrum.react_goal_analysis import _goal_requests_code_mutation
from runtime.core.cerebrum.react_types import ReActStep


def _step(**kw: object) -> ReActStep:
    kw.setdefault("iteration", 1)
    kw.setdefault("thought", "t")
    return ReActStep(**kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "goal",
    ["继续修复", "干活啊", "我让你修复问题", "开始优化", "改吧", "全修", "动手", "落地这个改动"],
)
def test_colloquial_imperatives_request_mutation(goal: str) -> None:
    assert _goal_requests_code_mutation(goal) is True


@pytest.mark.parametrize(
    "goal",
    ["审计项目", "这段代码可以优化", "继续", "这些问题严重吗", "review the code", "帮我看看"],
)
def test_read_only_goals_do_not_request_mutation(goal: str) -> None:
    assert _goal_requests_code_mutation(goal) is False


@pytest.mark.parametrize(
    "goal",
    [
        (
            "只执行以下两步后回答“Journal 回归通过”：1. 用 todo_write "
            "创建一项已完成清单；2. 调用 list_cwd 查看当前项目根目录。"
            "不要调用其他工具。"
        ),
        "Use todo_write to create a completed checklist, then call list_cwd.",
        "添加一个待办事项清单并查看项目目录。",
    ],
)
def test_todo_bookkeeping_is_not_workspace_mutation(goal: str) -> None:
    assert _goal_requests_code_mutation(goal) is False


@pytest.mark.parametrize(
    "goal",
    [
        "用 todo_write 创建清单，然后创建 README.md 并写入结果。",
        "创建任务清单组件并运行测试。",
        "Create a checklist file and add it to the repository.",
    ],
)
def test_todo_words_do_not_hide_real_workspace_mutation(goal: str) -> None:
    assert _goal_requests_code_mutation(goal) is True


@pytest.mark.parametrize(
    "goal",
    ["不要动手改代码", "别改吧", "别动手", "不要修一下", "禁止修改", "不要修复任何东西"],
)
def test_prohibitions_survive_the_wider_vocabulary(goal: str) -> None:
    """Adding positive markers must not defeat negation."""
    assert _goal_requests_code_mutation(goal) is False


def test_missing_write_guard_now_fires_on_colloquial_authorization() -> None:
    """The end-to-end effect: read-only trajectory + "干活啊" is rejected."""
    reads = [
        _step(action='read_file({"path": "a.py"})', observation="(real tool execution succeeded)"),
        _step(action='grep_text({"pattern": "x"})', observation="(real tool execution succeeded)"),
    ]
    message = _code_mode_missing_write_guard(reads, "我已经分析完了，问题在这几处。", goal="干活啊")
    assert message is not None
    assert "no successful" in message.lower()


def test_missing_write_guard_stays_silent_for_review_goals() -> None:
    reads = [
        _step(action='read_file({"path": "a.py"})', observation="(real tool execution succeeded)")
    ]
    assert _code_mode_missing_write_guard(reads, "审计结论如下。", goal="审计项目") is None


def test_missing_write_guard_stays_silent_after_requested_todo_and_listing() -> None:
    steps = [
        _step(
            action='todo_write({"todos":[{"title":"Journal 回归","status":"completed"}]})',
            observation='{"ok":true}',
            action_results=[
                {
                    "tool_name": "todo_write",
                    "ok": True,
                    "observation": "todo updated",
                }
            ],
        ),
        _step(
            iteration=2,
            action="list_cwd({})",
            observation='{"entries":["runtime","tests"]}',
            action_results=[
                {
                    "tool_name": "list_cwd",
                    "ok": True,
                    "observation": "listed cwd",
                }
            ],
        ),
    ]
    goal = "用 todo_write 创建一项已完成清单；调用 list_cwd 查看当前项目根目录。不要调用其他工具。"

    assert _code_mode_missing_write_guard(steps, "Journal 回归通过", goal=goal) is None

