from runtime.core.cerebrum.react_convergence import (
    build_direct_answer_directive,
    build_evidence_digest,
    constrain_explicit_read_scope,
    evidence_answer_conflicts_with_goal,
    ordered_explicit_read_groups,
    read_only_evidence_convergence,
)
from runtime.core.cerebrum.react_types import ReActStep


def _step(action: str, *, ok: bool = True) -> ReActStep:
    return ReActStep(
        iteration=1,
        action=action,
        actions=[action],
        observation="evidence" if ok else "(工具失败) missing",
        action_results=[{"ok": ok, "observation": "evidence"}],
    )


def test_converges_after_every_explicit_file_is_read() -> None:
    decision = read_only_evidence_convergence(
        goal="只读比较 src/a.py 和 src/b.tsx，用两点回答，不要修改文件。",
        read_only=True,
        steps=[
            _step('read_file({"path":"src/a.py"})'),
            _step('read_file({"path":"src/b.tsx"})'),
        ],
    )

    assert decision is not None
    assert decision.covered == ("src/a.py", "src/b.tsx")


def test_does_not_converge_while_an_explicit_file_is_missing() -> None:
    decision = read_only_evidence_convergence(
        goal="只读比较 src/a.py 和 src/b.ts，不要修改文件。",
        read_only=True,
        steps=[_step('read_file({"path":"src/a.py"})')],
    )

    assert decision is None


def test_incomplete_explicit_scope_filters_duplicate_and_unrequested_reads() -> None:
    goal = "只读比较 src/a.py、src/b.ts 与 src/c.tsx，不要修改文件。"
    constraint = constrain_explicit_read_scope(
        goal=goal,
        read_only=True,
        steps=[_step('read_file({"path":"src/a.py"})')],
        actions=[
            'read_file({"path":"src/a.py"})',
            'grep_text({"pattern":"Thing", "path":"src/a.py"})',
            'read_file({"path":"src/b.ts"})',
            'read_file({"path":"src/related.test.ts"})',
            'echo({"text":"keep non-read actions untouched"})',
        ],
    )

    assert constraint is not None
    assert constraint.actions == (
        'read_file({"path":"src/b.ts"})',
        'echo({"text":"keep non-read actions untouched"})',
    )
    assert constraint.missing == ("src/b.ts", "src/c.tsx")
    assert constraint.skipped == ("src/a.py", "src/related.test.ts")
    assert "src/b.ts, src/c.tsx" in constraint.observation_note()


def test_explicit_scope_waits_for_first_successful_requested_read() -> None:
    constraint = constrain_explicit_read_scope(
        goal="只读比较 src/a.py 与 src/b.ts。",
        read_only=True,
        steps=[],
        actions=['read_file({"path":"README.md"})'],
    )

    assert constraint is None


def test_ordered_read_groups_preserve_parallel_batches() -> None:
    goal = (
        "按证据顺序：先并行读取 src/a.py 与 src/b.ts；"
        "再并行读取 src/c.tsx 与 src/d.py；最后读取 src/e.ts。"
    )

    assert ordered_explicit_read_groups(goal) == (
        ("src/a.py", "src/b.ts"),
        ("src/c.tsx", "src/d.py"),
        ("src/e.ts",),
    )


def test_ordered_scope_rejects_later_batch_before_first_evidence() -> None:
    constraint = constrain_explicit_read_scope(
        goal="先并行读取 src/a.py 与 src/b.ts；再并行读取 src/c.ts 与 src/d.py。",
        read_only=True,
        enforce_order=True,
        steps=[],
        actions=[
            'read_file({"path":"src/c.ts"})',
            'read_file({"path":"src/d.py"})',
        ],
    )

    assert constraint is not None
    assert constraint.actions == ()
    assert constraint.missing == ("src/a.py", "src/b.ts")
    assert constraint.skipped == ("src/c.ts", "src/d.py")


def test_failed_read_is_not_evidence() -> None:
    decision = read_only_evidence_convergence(
        goal="只读读取 package.json，用一句话回答。",
        read_only=True,
        steps=[_step('read_file({"path":"package.json"})', ok=False)],
    )

    assert decision is None


def test_basename_read_can_resolve_a_user_named_file() -> None:
    decision = read_only_evidence_convergence(
        goal="只读读取当前项目的 package.json，只用一句话告诉我项目名称。",
        read_only=True,
        steps=[_step('read_file({"path":"frontend/package.json"})')],
    )

    assert decision is not None
    assert decision.covered == ("package.json",)


def test_converges_after_an_explicit_url_is_fetched() -> None:
    url = "https://example.com/reference"
    decision = read_only_evidence_convergence(
        goal=f"只读打开 {url}，用一句话概括。",
        read_only=True,
        steps=[_step(f'fetch_url({{"url":"{url}"}})')],
    )

    assert decision is not None
    assert decision.covered == (url,)


def test_bounded_answer_can_converge_on_substantive_search_evidence() -> None:
    decision = read_only_evidence_convergence(
        goal="只读查清这个术语，只用一句话回答。",
        read_only=True,
        steps=[_step('web_search({"query":"term"})')],
    )

    assert decision is not None


def test_open_ended_research_keeps_exploring_without_explicit_coverage() -> None:
    decision = read_only_evidence_convergence(
        goal="只读调研当前项目的整体架构、风险和未来演进方向，不要修改文件。",
        read_only=True,
        steps=[
            _step('read_file({"path":"README.md"})'),
            _step('grep_text({"query":"architecture"})'),
        ],
    )

    assert decision is None


def test_mutating_turn_never_uses_read_only_convergence() -> None:
    decision = read_only_evidence_convergence(
        goal="读取 src/a.py 后修复它。",
        read_only=False,
        steps=[_step('read_file({"path":"src/a.py"})')],
    )

    assert decision is None


def test_digest_preserves_each_parallel_file_with_bounded_excerpts() -> None:
    first = "first-head\n" + ("a" * 3000) + "\nfirst-tail"
    second = "second-head\n" + ("b" * 3000) + "\nsecond-tail"
    step = ReActStep(
        iteration=1,
        action=('read_file({"path":"src/a.py"}); read_file({"path":"src/b.tsx"})'),
        actions=[
            'read_file({"path":"src/a.py"})',
            'read_file({"path":"src/b.tsx"})',
        ],
        observation="merged",
        action_results=[
            {"ok": True, "observation": first},
            {"ok": True, "observation": second},
        ],
    )
    decision = read_only_evidence_convergence(
        goal="只读比较 src/a.py 和 src/b.tsx，不要修改文件。",
        read_only=True,
        steps=[step],
    )

    assert decision is not None
    digest = build_evidence_digest(decision, [step], max_chars_per_target=120)
    assert "--- src/a.py ---" in digest
    assert "--- src/b.tsx ---" in digest
    assert "first-head" in digest and "first-tail" in digest
    assert "second-head" in digest and "second-tail" in digest
    assert len(digest) < 700


def test_direct_answer_directive_keeps_original_request_after_evidence() -> None:
    step = _step('read_file({"path":"package.json"})')
    decision = read_only_evidence_convergence(
        goal="只读读取 package.json，只用一句话告诉我项目名称。",
        read_only=True,
        steps=[step],
    )

    assert decision is not None
    directive = build_direct_answer_directive(
        goal="只读读取 package.json，只用一句话告诉我项目名称。",
        decision=decision,
        steps=[step],
    )
    assert directive.index("[bounded-read-evidence]") < directive.index("[original-user-request]")
    assert "or expand scope." in directive
    assert "conversational tone" in directive
    assert "告诉我项目名称" in directive


def test_evidence_answer_rejects_lost_task_greeting_but_not_requested_status() -> None:
    idle_answer = (
        "这一轮没有正在进行的任务，也没有工具结果需要收尾。如果你有具体需求，直接说一句，我就开工。"
    )

    assert evidence_answer_conflicts_with_goal(
        goal="只读读取 package.json，只用一句话告诉我项目名称。",
        answer=idle_answer,
    )
    assert evidence_answer_conflicts_with_goal(
        goal="只读读取 package.json，只用一句话告诉我项目名称。",
        answer="这里没有可继续的工作。你要我做什么，直接说一句就行。",
    )
    assert not evidence_answer_conflicts_with_goal(
        goal="请检查当前是否有正在进行的任务。",
        answer="没有正在进行的任务。",
    )

