"""research-lookup guard — non-code turns that announce a lookup must actually
run a search/fetch tool.

The code-mode "did you actually inspect the project" guard
(``_code_mode_missing_inspection_tool_guard``) was gated on ``is_code_mode``;
research/chat turns had no mirror, so a turn could announce "我来查一下最新
数据" and complete with zero tool calls. This guard is the non-code branch of
the *same* contract, keyed to lookup vocabulary and gated on tools being
present (pure chat has none, so it is skipped rather than wedging the loop).
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    GuardContext,
    _goal_requests_research_lookup,
    _invoke_missing_inspection,
    _research_low_quality_evidence_guard,
    _research_missing_lookup_guard,
    evaluate_guards,
)
from runtime.core.cerebrum.react_types import ReActStep


def _step(action: str, observation: str = "") -> ReActStep:
    return ReActStep(iteration=1, action=action, observation=observation)


# ── classifier ──


def test_classifier_flags_explicit_lookup_verbs() -> None:
    for goal in (
        "查一下今年最新的 GDP 数据",
        "搜索一下 React 19 的新特性",
        "核实这个说法的出处",
        "调研一下这个市场规模",
        "上网查最新汇率",
        "search for the latest docs",
        "look up the error code",
        "verify whether this is true",
    ):
        assert _goal_requests_research_lookup(goal), goal


def test_classifier_ignores_knowledge_questions() -> None:
    for goal in (
        "什么是类型注解",
        "解释一下 Python 装饰器",
        "介绍一下你自己",
        "翻译这句话",
        "定义一下递归",
        "讲讲设计模式",
    ):
        assert not _goal_requests_research_lookup(goal), goal


# ── guard ──


def test_fires_when_lookup_announced_but_no_tools_ran() -> None:
    msg = _research_missing_lookup_guard(
        [], "我查了最新的数据，结论是……", goal="查一下今年最新的 GDP 数据", tools_active=True
    )
    assert msg is not None
    assert "never" in msg


def test_accepts_successful_search_observation() -> None:
    steps = [_step('web_search({"q": "2026 GDP"})', "GDP grew 4.5% in 2026.")]
    assert (
        _research_missing_lookup_guard(
            steps,
            "2026 年 GDP 增长 4.5%。",
            goal="查一下今年最新的 GDP 数据",
            tools_active=True,
        )
        is None
    )


def test_accepts_knowledge_question_with_no_tools() -> None:
    assert (
        _research_missing_lookup_guard(
            [], "类型注解是给变量标注类型的方式。", goal="什么是类型注解", tools_active=True
        )
        is None
    )


def test_accepts_when_no_tools_available() -> None:
    # Pure chat has no tools; demanding a lookup there would wedge the loop.
    assert (
        _research_missing_lookup_guard(
            [], "我先查一下再说。", goal="查一下最新数据", tools_active=False
        )
        is None
    )


def test_accepts_user_help_handoff() -> None:
    assert (
        _research_missing_lookup_guard(
            [],
            "需要你提供 API 凭证才能查询。",
            goal="查一下最新数据",
            tools_active=True,
        )
        is None
    )


def test_low_quality_search_cannot_complete_research() -> None:
    steps = [
        _step(
            'web_search({"query": "Eight Sleep patent lawsuit"})',
            '{"results": [], "result_count": 0, "quality_warning": "low_relevance"}',
        )
    ]
    msg = _research_low_quality_evidence_guard(
        steps,
        "这个搜索引擎不支持该问题。",
        goal="调研一下 Eight Sleep 的专利诉讼",
    )
    assert msg is not None
    assert "low_relevance" in msg


def test_verified_page_clears_low_quality_search_guard() -> None:
    steps = [
        _step(
            'web_search({"query": "Eight Sleep patent lawsuit"})',
            '{"results": [], "result_count": 0, "quality_warning": "low_relevance"}',
        ),
        _step(
            'web_fetch({"url": "https://courtlistener.com/docket/1"})',
            '{"answer": "The complaint alleges patent infringement."}',
        ),
    ]
    assert (
        _research_low_quality_evidence_guard(
            steps,
            "法院材料显示该案涉及专利侵权。",
            goal="调研一下 Eight Sleep 的专利诉讼",
        )
        is None
    )


# ── mode dispatch ──


def test_dispatch_code_mode_still_uses_project_inspection() -> None:
    ctx = GuardContext(
        steps=[],
        final_answer="评价完成。",
        is_code_mode=True,
        file_inspection_tools_visible=True,
        goal="评价这个项目的架构",
    )
    assert _invoke_missing_inspection(ctx) is not None


def test_dispatch_research_mode_fires_on_announce() -> None:
    ctx = GuardContext(
        steps=[],
        final_answer="我查了最新数据。",
        is_code_mode=False,
        tools_active=True,
        goal="查一下今年最新的 GDP 数据",
    )
    assert _invoke_missing_inspection(ctx) is not None


def test_dispatch_browser_mode_skips_lookup() -> None:
    ctx = GuardContext(
        steps=[],
        final_answer="页面已检查。",
        is_code_mode=False,
        browser_operation_mode=True,
        tools_active=True,
        goal="查一下最新数据",
    )
    assert _invoke_missing_inspection(ctx) is None


def test_dispatch_pure_chat_skips_lookup() -> None:
    ctx = GuardContext(
        steps=[],
        final_answer="我查了最新数据。",
        is_code_mode=False,
        tools_active=False,
        goal="查一下今年最新的 GDP 数据",
    )
    assert _invoke_missing_inspection(ctx) is None


def test_registry_fires_inspection_evidence_guard_in_research_mode() -> None:
    ctx = GuardContext(
        steps=[],
        final_answer="我查了最新数据。",
        is_code_mode=False,
        tools_active=True,
        goal="查一下今年最新的 GDP 数据",
    )
    result = evaluate_guards(ctx)
    assert result is not None
    assert result[0] == "inspection-evidence guard"

