"""Implementation note."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from runtime.sensing.server import SubprocessBackend

# Implementation note.
pytestmark = pytest.mark.slow


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBasicRunSkill:
    def test_list_cwd_in_subprocess(self, tmp_path: Path):
        backend = SubprocessBackend(timeout_seconds=10.0)
        with backend.sandbox(arm_id="test") as box:
            result = box.run_skill("list_cwd", {"path": str(tmp_path)})
        assert "error" not in result
        assert "items" in result
        assert result["count"] == 0

    def test_count_words_subprocess(self):
        backend = SubprocessBackend(timeout_seconds=10.0)
        with backend.sandbox(arm_id="test") as box:
            result = box.run_skill("count_words", {"text": "one two three four"})
        assert "error" not in result
        assert result["words"] == 4

    def test_subprocess_call_count_tracked(self):
        backend = SubprocessBackend(timeout_seconds=10.0)
        with backend.sandbox(arm_id="test") as box:
            box.run_skill("count_words", {"text": "a b"})
            box.run_skill("count_words", {"text": "c d"})
            assert box.subprocess_call_count == 2
            assert box.timeouts == 0


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestTimeout:
    def test_timeout_returns_error_dict_not_exception(self):
        """Implementation note."""
        backend = SubprocessBackend(timeout_seconds=0.01)
        with backend.sandbox(arm_id="test") as box:
            result = box.run_skill("list_cwd", {"path": "."})
        # Implementation note.
        assert result["timed_out"] is True
        assert "timeout" in result["error"]
        assert box.timeouts == 1


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestErrorHandling:
    def test_unknown_skill_nonzero_exit(self):
        """Implementation note."""
        backend = SubprocessBackend(timeout_seconds=10.0)
        with backend.sandbox(arm_id="test") as box:
            result = box.run_skill("no_such_skill_xyz", {})
        assert "error" in result
        assert result.get("exit_code") == 3
        assert "stderr" in result
        assert "SkillNotFound" in result["stderr"] or "no_such" in result["stderr"]

    def test_bad_args_caught(self):
        """Implementation note."""
        backend = SubprocessBackend(timeout_seconds=10.0)
        with backend.sandbox(arm_id="test") as box:
            # Implementation note.
            # Implementation note.
            # Implementation note.
            result = box.run_skill("read_file", {"path": "/definitely/no/such"})
        # Implementation note.
        assert "error" in result


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestArgsPassing:
    def test_json_roundtrip_preserves_nested(self, tmp_path: Path):
        # Implementation note.
        p = tmp_path / "hello.txt"
        p.write_text("hello world", encoding="utf-8")

        backend = SubprocessBackend(timeout_seconds=10.0)
        with backend.sandbox(arm_id="test") as box:
            result = box.run_skill("read_file", {"path": str(p)})
        assert "content" in result
        assert "hello world" in result["content"]


class TestPathAudit:
    def test_allowed_read_roots_are_enforced_in_child_process(self, tmp_path: Path):
        allowed = tmp_path / "allowed"
        denied = tmp_path / "denied"
        allowed.mkdir()
        denied.mkdir()
        inside = allowed / "inside.txt"
        outside = denied / "outside.txt"
        inside.write_text("inside", encoding="utf-8")
        outside.write_text("outside", encoding="utf-8")

        backend = SubprocessBackend(
            timeout_seconds=10.0,
            allowed_read_roots=[allowed],
        )
        with backend.sandbox(arm_id="test") as box:
            ok = box.run_skill("read_file", {"path": str(inside)})
            blocked = box.run_skill("read_file", {"path": str(outside)})

        assert ok["content"] == "inside"
        assert blocked["exit_code"] == 3
        assert "read outside allowed roots" in blocked["stderr"]


# ═══════════════════════════════════════════════════════════
# Resource limits (Unix only)
# ═══════════════════════════════════════════════════════════


@pytest.mark.skipif(sys.platform == "win32", reason="resource limits unix only")
class TestResourceLimitsUnix:
    def test_memory_limit_configured_no_crash_under_cap(self, tmp_path: Path):
        """Implementation note."""
        backend = SubprocessBackend(
            timeout_seconds=10.0,
            max_memory_mb=512,
        )
        with backend.sandbox(arm_id="test") as box:
            result = box.run_skill("list_cwd", {"path": str(tmp_path)})
        assert "error" not in result

    def test_preexec_returns_callable_when_limits_set(self):
        backend = SubprocessBackend(max_memory_mb=256)
        with backend.sandbox(arm_id="test") as box:
            preexec = box._build_preexec()
        assert callable(preexec)

    def test_preexec_none_without_limits(self):
        backend = SubprocessBackend()  # no limits
        with backend.sandbox(arm_id="test") as box:
            preexec = box._build_preexec()
        assert preexec is None


@pytest.mark.skipif(sys.platform != "win32", reason="windows-only no-op check")
class TestResourceLimitsWindows:
    def test_preexec_always_none(self):
        """Implementation note."""
        backend = SubprocessBackend(max_memory_mb=256, max_cpu_seconds=5)
        with backend.sandbox(arm_id="test") as box:
            preexec = box._build_preexec()
        assert preexec is None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBackwardCompat:
    def test_subprocess_mantle_has_local_api(self, tmp_path: Path):
        """Implementation note."""
        backend = SubprocessBackend(
            timeout_seconds=10.0,
            allowed_read_roots=[tmp_path],
        )
        assert backend.allows_read(tmp_path / "a.txt") is True
        assert backend.allows_read(Path("/etc/passwd")) is False
