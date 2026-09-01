"""Tool-result spill — dsh session-scoped spill store + best-effort policy."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.execution.tool_engine.tool_output_pruner import PRUNE_MARKER
from runtime.execution.tool_engine.tool_output_spill import (
    SpillRef,
    encode_segment,
    head_tail_preview,
    maybe_spill_text,
    save_text_spill,
    session_spill_dir,
    spill_notice,
)
from runtime.execution.tool_engine.tool_protocol import (
    normalize_step_tool_result,
    normalize_tool_result,
    render_tool_output,
)
from runtime.platform.process.session import Session, session_scope
from runtime.safety.auth import TrustEngine
from runtime.sensing.gateway import _tool_bridge_exec as _tbe

SPILL_SESSION = "test-spill-session"


# ═══════════════════════════════════════════════════════════
# encode_segment
# ═══════════════════════════════════════════════════════════


def test_encode_segment_safe_chars_pass_through() -> None:
    assert encode_segment("web_fetch.txt") == "web_fetch.txt"
    assert encode_segment("tool.result-1") == "tool.result-1"


def test_encode_segment_neutralizes_traversal() -> None:
    assert "/" not in encode_segment("../evil.txt")
    assert "\\" not in encode_segment("..\\evil")
    assert encode_segment("..") == "~002E~002E"
    assert encode_segment(".") == "~002E"
    assert encode_segment("") == "~"
    assert not encode_segment("a/b").startswith("a/")
    assert "\x00" not in encode_segment("a\x00b")


def test_encode_segment_injective_and_reversible() -> None:
    inputs = ["", ".", "..", "~", "a~b", "中文.txt", "sp ace", "a/b", "a\\b", "tool.txt"]
    encoded = [encode_segment(i) for i in inputs]
    assert len(set(encoded)) == len(inputs)
    assert encode_segment("a~b") == "a~007Eb"


def test_encode_segment_unicode_escaped() -> None:
    out = encode_segment("日志.txt")
    assert out == "~65E5~5FD7.txt"


# ═══════════════════════════════════════════════════════════
# save_text_spill / storage
# ═══════════════════════════════════════════════════════════


def test_save_text_spill_writes_private_session_scoped_file(tmp_path) -> None:
    ref = save_text_spill(
        session_key=SPILL_SESSION,
        content="hello world",
        suggested_name="web_fetch.txt",
        root=str(tmp_path),
    )
    assert isinstance(ref, SpillRef)
    assert ref.bytes == 11
    assert "read_file with offset/limit" in ref.retrieval_hint
    path = ref.locator
    assert os.path.exists(path)
    assert Path(path).read_text(encoding="utf-8") == "hello world"
    # session-scoped dir: <root>/session-<sha256 prefix>
    assert os.path.dirname(path).startswith(f"{tmp_path}/session-")
    assert str(session_spill_dir(tmp_path, SPILL_SESSION)) == os.path.dirname(path)
    # random prefix + safe name
    assert os.path.basename(path).endswith("-web_fetch.txt")
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode & 0o077 == 0


def test_save_text_spill_sanitizes_suggested_name(tmp_path) -> None:
    ref = save_text_spill(
        session_key="s2",
        content="x",
        suggested_name="../../evil.txt",
        root=str(tmp_path),
    )
    basename = os.path.basename(ref.locator)
    assert "/" not in basename
    assert basename.split("-", 1)[1] == "..~002F..~002Fevil.txt"


def test_save_text_spill_writes_distinct_files(tmp_path) -> None:
    a = save_text_spill(session_key="s", content="a", suggested_name="x.txt", root=str(tmp_path))
    b = save_text_spill(session_key="s", content="b", suggested_name="x.txt", root=str(tmp_path))
    assert a.locator != b.locator
    assert Path(a.locator).read_text(encoding="utf-8") == "a"
    assert Path(b.locator).read_text(encoding="utf-8") == "b"


def test_save_text_spill_raises_on_storage_failure(tmp_path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file")
    with pytest.raises(OSError):
        save_text_spill(
            session_key="s",
            content="x",
            suggested_name="x.txt",
            root=str(blocker),
        )


# ═══════════════════════════════════════════════════════════
# head_tail_preview
# ═══════════════════════════════════════════════════════════


def test_head_tail_preview_within_budget() -> None:
    assert head_tail_preview("short", 100) == ("short", 0)


def test_head_tail_preview_splits_budget_across_ends() -> None:
    text = "a" * 1000
    preview, omitted = head_tail_preview(text, 100)
    assert omitted == 900
    assert len(preview.encode("utf-8")) == 100
    assert preview.startswith("a" * 50)
    assert preview.endswith("a" * 50)


def test_head_tail_preview_preserves_utf8_boundaries() -> None:
    # 100 emoji = 400 bytes; a byte split lands mid-codepoint without care.
    text = "🧪" * 100
    preview, omitted = head_tail_preview(text, 100)
    assert "\ufffd" not in preview
    assert len(preview.encode("utf-8")) <= 100
    # head keeps whole codepoints (ceil half → 12 codepoints = 48 bytes),
    # tail keeps whole codepoints (floor half → 12 codepoints = 48 bytes).
    assert preview == "🧪" * 24
    assert omitted == 400 - 96


def test_head_tail_preview_zero_budget() -> None:
    preview, omitted = head_tail_preview("abc", 0)
    assert preview == ""
    assert omitted == 3


# ═══════════════════════════════════════════════════════════
# maybe_spill_text
# ═══════════════════════════════════════════════════════════


def test_maybe_spill_text_within_cap_keeps_inline() -> None:
    assert (
        maybe_spill_text(
            "small",
            session_key=SPILL_SESSION,
            tool_name="exec_shell",
            enabled=True,
        )
        is None
    )


def test_maybe_spill_text_disabled() -> None:
    assert maybe_spill_text("x" * 10000, session_key=SPILL_SESSION, enabled=False) is None


def test_maybe_spill_text_skips_read_file() -> None:
    assert (
        maybe_spill_text(
            "x" * 10000,
            session_key=SPILL_SESSION,
            tool_name="read_file",
            enabled=True,
        )
        is None
    )


def test_maybe_spill_text_no_session_keeps_inline(caplog) -> None:
    assert maybe_spill_text("x" * 10000, tool_name="exec_shell", enabled=True) is None
    assert any("no session owner" in r.message for r in caplog.records)


def test_maybe_spill_text_spills_full_text_and_preview(tmp_path) -> None:
    text = "HEAD-" + ("中" * 9000) + "-TAIL"
    replaced = maybe_spill_text(
        text,
        session_key=SPILL_SESSION,
        tool_name="exec_shell",
        root=str(tmp_path),
        enabled=True,
        max_inline_bytes=8192,
    )
    assert replaced is not None
    # the full text lives in the spill file
    locator = replaced.split("stored at: ", 1)[1].split(". ", 1)[0]
    assert Path(locator).read_text(encoding="utf-8") == text
    # the replacement is bounded by the cap and carries the notice
    assert len(replaced.encode("utf-8")) <= 8192
    assert "Omitted" in replaced
    assert "Full formatted result stored at:" in replaced
    assert "read_file with offset/limit" in replaced
    # both ends of the original survive the preview
    assert replaced.startswith("HEAD-")
    assert replaced.endswith(")")


def test_maybe_spill_text_best_effort_on_storage_failure(tmp_path, caplog) -> None:
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("file")
    replaced = maybe_spill_text(
        "x" * 10000,
        session_key=SPILL_SESSION,
        tool_name="exec_shell",
        root=str(blocker),
        enabled=True,
    )
    assert replaced is None
    assert any("saveText failed" in r.message for r in caplog.records)


def test_maybe_spill_text_notice_only_when_budget_tight(tmp_path) -> None:
    text = "x" * 10000
    ref = save_text_spill(
        session_key=SPILL_SESSION,
        content=text,
        suggested_name="exec_shell.txt",
        root=str(tmp_path),
    )
    notice_only_bytes = len(spill_notice(10000, ref).encode("utf-8")) + 2
    replaced = maybe_spill_text(
        text,
        session_key=SPILL_SESSION,
        tool_name="exec_shell",
        root=str(tmp_path),
        max_inline_bytes=notice_only_bytes,
        enabled=True,
    )
    assert replaced is not None
    assert len(replaced.encode("utf-8")) <= notice_only_bytes
    assert replaced.startswith("(Omitted")


def test_maybe_spill_text_keeps_inline_when_notice_cannot_fit(tmp_path) -> None:
    text = "x" * 10000
    replaced = maybe_spill_text(
        text,
        session_key=SPILL_SESSION,
        tool_name="exec_shell",
        root=str(tmp_path),
        max_inline_bytes=1,
        enabled=True,
    )
    assert replaced is None


# ═══════════════════════════════════════════════════════════
# render/normalize integration
# ═══════════════════════════════════════════════════════════


def test_render_tool_output_spills_before_prune(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("runtime.execution.tool_engine.tool_output_spill.SPILL_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "runtime.execution.tool_engine.tool_output_spill.DEFAULT_SPILL_MAX_INLINE_BYTES",
        8192,
    )
    long_output = "x" * 20000
    with session_scope(Session(thread_id="t-render")):
        spilled = render_tool_output(
            long_output,
            max_chars=16000,
            prune_middle=True,
            spill_oversized=True,
            tool_name="exec_shell",
        )
        pruned = render_tool_output(
            long_output,
            max_chars=16000,
            prune_middle=True,
        )
    assert "Full formatted result stored at:" in spilled
    assert PRUNE_MARKER not in spilled
    assert PRUNE_MARKER in pruned
    assert len(spilled.encode("utf-8")) <= 8192


def test_normalize_result_passes_spill_through(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("runtime.execution.tool_engine.tool_output_spill.SPILL_ROOT", str(tmp_path))
    call = {"id": f"tool-{uuid4().hex[:8]}", "name": "exec_shell", "input": {}}
    with session_scope(Session(thread_id="t-norm")):
        result = normalize_tool_result(
            call,
            "x" * 20000,
            origin="native",
            max_chars=16000,
            prune_middle=True,
            spill_oversized=True,
            tool_name="exec_shell",
        )
    assert "Full formatted result stored at:" in result.rendered
    assert result.is_error is False

    step = SimpleNamespace(
        action=call,
        result=SimpleNamespace(output="x" * 20000, status="success", error_type=None),
    )
    with session_scope(Session(thread_id="t-norm-step")):
        step_result = normalize_step_tool_result(
            step,
            origin="native",
            max_chars=16000,
            prune_middle=True,
            spill_oversized=True,
            tool_name="exec_shell",
        )
    assert "Full formatted result stored at:" in step_result.rendered


# ═══════════════════════════════════════════════════════════
# call-site wiring
# ═══════════════════════════════════════════════════════════


def _stack_with_long_output(skill_name: str = "long_tool", size: int = 20000) -> Any:
    reg = SkillRegistry()
    reg.register(
        Skill(
            name=skill_name,
            description="Long output.",
            trusted_source=f"skill://public/{skill_name}",
            handler=lambda **_kwargs: "x" * size,
        ),
        verify_tests=False,
    )
    stack = SimpleNamespace()
    stack.executor = ToolExecutor(reg, TrustEngine())
    return stack


def test_bridge_output_spills_when_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_tbe, "TOOL_RESULT_SPILL_ENABLED", True)
    monkeypatch.setattr("runtime.execution.tool_engine.tool_output_spill.SPILL_ROOT", str(tmp_path))
    call = {"id": f"tool-{uuid4().hex[:8]}", "name": "long_tool", "input": {}}
    with session_scope(Session(thread_id="t-bridge")):
        output, is_error = _tbe._execute_tool_call(_stack_with_long_output(), call)
    assert is_error is False
    assert "Full formatted result stored at:" in output
    assert PRUNE_MARKER not in output


def test_bridge_read_file_never_spilled(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_tbe, "TOOL_RESULT_SPILL_ENABLED", True)
    monkeypatch.setattr("runtime.execution.tool_engine.tool_output_spill.SPILL_ROOT", str(tmp_path))
    call = {"id": f"tool-{uuid4().hex[:8]}", "name": "read_file", "input": {}}
    with session_scope(Session(thread_id="t-bridge-read")):
        output, is_error = _tbe._execute_tool_call(_stack_with_long_output("read_file"), call)
    assert is_error is False
    assert "Full formatted result stored at:" not in output
    assert PRUNE_MARKER in output


def test_react_path_spills_when_enabled(tmp_path, monkeypatch) -> None:
    from runtime.core.cerebrum import _react_execution_dispatch as _dispatch
    from runtime.core.cerebrum.react_execution import (
        TOOL_OBSERVATION_MAX_CHARS,
        _execute_action_via_beak,
    )
    from runtime.platform.models import TaskId

    monkeypatch.setattr(_dispatch, "TOOL_RESULT_SPILL_ENABLED", True)
    monkeypatch.setattr("runtime.execution.tool_engine.tool_output_spill.SPILL_ROOT", str(tmp_path))

    reg = SkillRegistry()
    reg.register(
        Skill(
            name="large_output",
            description="Return a large payload.",
            trusted_source="skill://public/large_output",
            handler=lambda **_kwargs: "x" * (TOOL_OBSERVATION_MAX_CHARS + 7),
        ),
        verify_tests=False,
    )
    from tests.test_react_loop import _FakeStack  # type: ignore[attr-defined]

    stack = _FakeStack(None)
    stack.executor = ToolExecutor(reg, TrustEngine())

    with session_scope(Session(thread_id="t-react")):
        observation, step = _execute_action_via_beak(
            stack,
            "large_output({})",
            react_task_id=TaskId(uuid4()),
            react_step_counter=1,
        )
    assert step is not None
    assert observation is not None
    assert "(real tool execution succeeded) large_output" in observation
    assert "Full formatted result stored at:" in observation
    assert PRUNE_MARKER not in observation
    assert "x" * (TOOL_OBSERVATION_MAX_CHARS + 7) not in observation


def test_react_path_falls_back_to_prune_when_spill_disabled(monkeypatch) -> None:
    from runtime.core.cerebrum import _react_execution_dispatch as _dispatch
    from runtime.core.cerebrum.react_execution import (
        TOOL_OBSERVATION_MAX_CHARS,
        _execute_action_via_beak,
    )
    from runtime.platform.models import TaskId

    monkeypatch.setattr(_dispatch, "TOOL_RESULT_SPILL_ENABLED", False)
    reg = SkillRegistry()
    reg.register(
        Skill(
            name="large_output",
            description="Return a large payload.",
            trusted_source="skill://public/large_output",
            handler=lambda **_kwargs: "x" * (TOOL_OBSERVATION_MAX_CHARS + 7),
        ),
        verify_tests=False,
    )
    from tests.test_react_loop import _FakeStack  # type: ignore[attr-defined]

    stack = _FakeStack(None)
    stack.executor = ToolExecutor(reg, TrustEngine())
    with session_scope(Session(thread_id="t-react-prune")):
        observation, step = _execute_action_via_beak(
            stack,
            "large_output({})",
            react_task_id=TaskId(uuid4()),
            react_step_counter=1,
        )
    assert step is not None
    assert observation is not None
    assert PRUNE_MARKER in observation


def test_spill_master_switch_reads_env_at_import() -> None:
    from runtime.execution.tool_engine import tool_output_spill

    assert (os.environ.get("ECHO_TOOL_SPILL", "1") != "0") == (
        tool_output_spill.TOOL_RESULT_SPILL_ENABLED
    )

