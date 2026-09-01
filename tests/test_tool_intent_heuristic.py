"""Implementation note."""

from __future__ import annotations

import pytest
from runtime.sensing.gateway.realtime_turn_routing import (
    looks_like_contextual_tool_followup,
    looks_like_plain_chat,
    looks_like_tool_intent,
)

# ─── Bug regression · the exact user message that broke ──────


def test_the_original_bug_message_matches() -> None:
    """The precise phrasing that triggered the hallucination must
    now route to agentic. This is the lockdown test; do not relax."""
    assert looks_like_tool_intent("帮我看一下 Makefile 前 20 行的内容")


def test_contextual_research_followups_match() -> None:
    history = [
        {
            "role": "assistant",
            "content": "你倾向哪个方向？选定后我可以直接启动 deep research。",
        },
    ]

    assert looks_like_contextual_tool_followup(
        "AI 家庭机器人（扫地/陪伴/安防）",
        history,
    )
    assert looks_like_contextual_tool_followup("需要", history)
    assert looks_like_contextual_tool_followup("好", history)


def test_contextual_research_topic_after_clarification_offer_matches() -> None:
    history = [
        {
            "role": "assistant",
            "content": (
                "这个方向很宽泛，我需要一个聚焦点才能给出有价值的调研。\n\n"
                "给我一个大致方向，我马上开始调研。"
            ),
        },
    ]

    assert looks_like_contextual_tool_followup("AI应用", history)


def test_tool_meta_followup_matches_without_history() -> None:
    assert looks_like_contextual_tool_followup("你不能调用工具么")
    assert looks_like_contextual_tool_followup("为什么看不到过程 进度")


def test_plain_chat_stays_plain() -> None:
    assert looks_like_plain_chat("2+2等于几？")
    assert not looks_like_plain_chat("AI 家庭机器人（扫地/陪伴/安防）")


# ─── Extensionless project filenames ─────────────────────────


@pytest.mark.parametrize(
    "goal",
    [
        "打开 Makefile 看一眼",
        "Dockerfile 里写了什么？",
        "Containerfile 和 Dockerfile 区别",
        "Justfile 有哪些 target",
        "Procfile 的 web 进程怎么配",
        "Rakefile 里的默认任务是什么",
        "Gemfile.lock 需不需要提交",
        "Brewfile 有啥用",
        "CMakeLists.txt 的入口在哪",
        "README 里写了啥",
        "LICENSE 是 MIT 还是 Apache",
        "CHANGELOG 最新一项是什么",
    ],
)
def test_extensionless_filenames_match(goal: str) -> None:
    assert looks_like_tool_intent(goal), (
        f"extensionless filename in {goal!r} should route to agentic"
    )


# ─── Explicit line-range phrasing ────────────────────────────


@pytest.mark.parametrize(
    "goal",
    [
        "给我看这个文件的前 20 行",
        "把头 5 行贴出来",
        "最后 10 行是啥",
        "read the first 30 lines",
        "show me the last 15 lines",
    ],
)
def test_line_range_phrasing_matches(goal: str) -> None:
    assert looks_like_tool_intent(goal)


# ─── Windows drive-letter paths ──────────────────────────────


@pytest.mark.parametrize(
    "goal",
    [
        r"看看 F:\echo-agent\Makefile",
        r"C:\Users\me\Desktop\notes.txt 里有啥",
        "路径是 D:/projects/app/main.py",
    ],
)
def test_windows_paths_match(goal: str) -> None:
    assert looks_like_tool_intent(goal)


# ─── Pre-existing coverage (still green) ─────────────────────
#
# Quick spot-check that the changes didn't accidentally break
# long-standing matches. Not exhaustive — those are covered end-to-
# end by the live router integration tests.


@pytest.mark.parametrize(
    "goal",
    [
        "搜一下 Python 3.12 的新特性",
        "列出当前目录",
        "帮我委派给 architect 评估一下",
        "记下这个偏好",
        "/usr/local/bin 下有啥",
        "https://example.com 是啥",
        "试试 config.yaml 能不能解析",
        "做一个nas调研",
        "生成一份行业报告",
    ],
)
def test_preexisting_triggers_still_match(goal: str) -> None:
    assert looks_like_tool_intent(goal)


# ─── Non-tool chitchat must NOT match ────────────────────────


@pytest.mark.parametrize(
    "goal",
    [
        "你好",
        "今天天气怎么样",
        "给我讲个笑话",
        "1+1 等于几",
        "Python 装饰器是啥",  # concept question · should direct_llm
        "回文函数的时间复杂度",  # concept follow-up · should direct_llm
    ],
)
def test_casual_chitchat_does_not_match(goal: str) -> None:
    assert not looks_like_tool_intent(goal), (
        f"{goal!r} is casual chitchat but got routed to agentic · "
        f"false-positive rate is rising, check recent regex additions"
    )


# ─── Edge cases ──────────────────────────────────────────────


def test_empty_and_none_do_not_match() -> None:
    assert not looks_like_tool_intent("")
    assert not looks_like_tool_intent(None)  # type: ignore[arg-type]


def test_whitespace_only_does_not_match() -> None:
    assert not looks_like_tool_intent("   \n\t  ")
