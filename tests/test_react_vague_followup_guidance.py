"""Vague interjection follow-up (``？``) steering after a broken image turn.

Regression for thread txhjBkLKtmrjdfdJp0FQhN: after a user image silently
failed to reach the model, the user typed ``？`` and the model answered with a
generic "请说明您需要我处理的具体内容" template — ignoring that the user was
pushing back on the image failure. The assembly now detects the bare
interjection + a recent not-received image/attachment marker and injects
context-aware steering.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from runtime.core.cerebrum._react_prompt_assembly_state import (
    _assemble_messages,
    _AssemblyState,
    _recent_attachment_issue,
    _vague_user_goal,
)


def _state(goal: str = "", *, conversation_messages: Any = None) -> Any:
    return _AssemblyState(
        intent=SimpleNamespace(normalized_goal=goal, raw=goal),
        agent=None,
        stack=None,
        executor=None,
        approval_provider=None,
        resume_task_id=None,
        planning_mode=False,
        tools_active=True,
        native_mode=True,
        no_tool_turn=False,
        strict_explicit_reads=False,
        camouflage_suffix="",
        max_iterations=10,
        max_tokens_budget=None,
        max_usd_budget=None,
        user_context={
            "attachments": [],
            "conversation_messages": conversation_messages or [],
        },
    )


def _rendered_messages(state: Any) -> list[str]:
    _assemble_messages(state)
    return [str(m.content) for m in state.messages]


def test_vague_goal_detection() -> None:
    for vague in ("？", "?", "？？", "？？？", "。。", "...", "嗯？", "啥", "什么？", "咋了"):
        assert _vague_user_goal(vague), vague
    for normal in ("继续", "ok", "好", "谢谢", "嗯", "帮我查一下", "再来", "跑一下", ""):
        assert not _vague_user_goal(normal), normal


def test_recent_attachment_issue_detection() -> None:
    broken = [
        {"role": "user", "content": "你帮我看看这张图片"},
        {"role": "assistant", "content": "没有收到图片附件。你重新发一次图，我就能看了。"},
    ]
    assert _recent_attachment_issue(broken)
    healthy = [
        {"role": "user", "content": "报告写得不错"},
        {"role": "assistant", "content": "谢谢，需要我调整排版吗？"},
    ]
    assert not _recent_attachment_issue(healthy)
    assert not _recent_attachment_issue([])
    assert not _recent_attachment_issue(None)


def test_vague_goal_after_image_failure_injects_guidance() -> None:
    history = [
        {"role": "user", "content": "你帮我看看这张图片"},
        {"role": "assistant", "content": "没有收到图片附件。你重新发一次图，我就能看了。"},
    ]
    rendered = _rendered_messages(_state("？", conversation_messages=history))
    guidance = [m for m in rendered if "<vague-user-followup>" in m]
    assert guidance, "guidance must be injected"
    assert "重新上传" in guidance[0]
    assert "图片" in guidance[0]


def test_non_vague_goal_skips_guidance() -> None:
    history = [
        {"role": "user", "content": "你帮我看看这张图片"},
        {"role": "assistant", "content": "没有收到图片附件。你重新发一次图，我就能看了。"},
    ]
    rendered = _rendered_messages(_state("帮我改一下报告", conversation_messages=history))
    assert not any("<vague-user-followup>" in m for m in rendered)


def test_vague_goal_without_image_issue_skips_guidance() -> None:
    history = [
        {"role": "user", "content": "报告写得不错"},
        {"role": "assistant", "content": "谢谢，需要我调整排版吗？"},
    ]
    rendered = _rendered_messages(_state("？", conversation_messages=history))
    assert not any("<vague-user-followup>" in m for m in rendered)

