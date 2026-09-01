from runtime.platform.models import ParsedIntent
from runtime.sensing.gateway._tool_bridge_policy import _is_evidence_task


def _intent(goal: str, **context):
    return ParsedIntent(
        raw=goal,
        intent_type="task",
        normalized_goal=goal,
        user_context=context,
    )


def test_review_and_analysis_tasks_require_workspace_evidence():
    assert _is_evidence_task(_intent("评价这个项目的前端 UI/UX"))
    assert _is_evidence_task(_intent("走查一下当前实现，找出问题"))
    assert _is_evidence_task(_intent("analyze the frontend implementation"))


def test_plain_conversation_and_explicit_opt_out_are_not_evidence_tasks():
    assert not _is_evidence_task(_intent("你好，介绍一下你自己"))
    assert not _is_evidence_task(_intent("分析这个想法", no_evidence_required=True))


def test_declared_audit_ux_and_code_modes_require_evidence():
    assert _is_evidence_task(_intent("给我结论", mode="audit"))
    assert _is_evidence_task(_intent("给我结论", mode="ux"))
    assert _is_evidence_task(_intent("读代码后评价", mode="code"))


def test_declared_build_and_research_modes_require_evidence():
    assert _is_evidence_task(_intent("构建这个功能", mode="build"))
    assert _is_evidence_task(_intent("研究这个项目", mode="research"))

