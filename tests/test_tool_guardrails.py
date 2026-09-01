"""Unit tests for tool guardrails and file safety."""

from __future__ import annotations

import os

from runtime.safety.auth.file_safety import (
    FileWriteVerdict,
    check_file_write,
    denied_basenames,
    denied_home_subdirs,
    is_safe_write,
)
from runtime.safety.auth.tool_guardrails import (
    GuardrailConfig,
    GuardrailDecision,
    ToolCallGuardrailController,
    ToolCallSignature,
    classify_tool,
    classify_tool_failure,
)

# ═══════════════════════════════════════════════════════════
# Tool Classification
# ═══════════════════════════════════════════════════════════


class TestClassifyTool:
    def test_idempotent_tools(self):
        for name in ["read_file", "search_files", "web_search", "browser_snapshot"]:
            assert classify_tool(name) == "idempotent"

    def test_mutating_tools(self):
        for name in ["exec_shell", "write_text_file", "edit_code", "delete_file", "git_commit"]:
            assert classify_tool(name) == "mutating"

    def test_dangerous_tools(self):
        assert classify_tool("exec_shell") in ("mutating", "dangerous")

    def test_unknown_tool(self):
        assert classify_tool("custom_tool_xyz") == "unknown"


# ═══════════════════════════════════════════════════════════
# ToolCallSignature
# ═══════════════════════════════════════════════════════════


class TestToolCallSignature:
    def test_same_args_same_hash(self):
        sig1 = ToolCallSignature.from_call("read_file", {"path": "/tmp/a.txt"})
        sig2 = ToolCallSignature.from_call("read_file", {"path": "/tmp/a.txt"})
        assert sig1.args_hash == sig2.args_hash

    def test_different_args_different_hash(self):
        sig1 = ToolCallSignature.from_call("read_file", {"path": "/tmp/a.txt"})
        sig2 = ToolCallSignature.from_call("read_file", {"path": "/tmp/b.txt"})
        assert sig1.args_hash != sig2.args_hash

    def test_key_order_irrelevant(self):
        sig1 = ToolCallSignature.from_call("tool", {"a": 1, "b": 2})
        sig2 = ToolCallSignature.from_call("tool", {"b": 2, "a": 1})
        assert sig1.args_hash == sig2.args_hash


# ═══════════════════════════════════════════════════════════
# GuardrailDecision
# ═══════════════════════════════════════════════════════════


class TestGuardrailDecision:
    def test_allow_allows_execution(self):
        d = GuardrailDecision(action="allow")
        assert d.allows_execution is True
        assert d.should_halt is False

    def test_warn_allows_execution(self):
        d = GuardrailDecision(action="warn")
        assert d.allows_execution is True
        assert d.should_halt is False

    def test_block_halts(self):
        d = GuardrailDecision(action="block")
        assert d.allows_execution is False
        assert d.should_halt is True

    def test_halt_halts(self):
        d = GuardrailDecision(action="halt")
        assert d.allows_execution is False
        assert d.should_halt is True


# ═══════════════════════════════════════════════════════════
# ToolCallGuardrailController
# ═══════════════════════════════════════════════════════════


class TestGuardrailController:
    def test_first_call_allows(self):
        ctrl = ToolCallGuardrailController()
        d = ctrl.observe("read_file", {"path": "/tmp/a.txt"})
        assert d.action == "allow"

    def test_warning_after_repeated_failures(self):
        ctrl = ToolCallGuardrailController()
        for i in range(4):
            d = ctrl.observe("exec_shell", {"cmd": f"cmd_{i}"}, failed=True)
        assert d.action == "warn"
        assert "same_tool" in d.code or "warn" in d.code

    def test_exact_failure_warn(self):
        ctrl = ToolCallGuardrailController()
        args = {"cmd": "bad_command"}
        for _i in range(3):
            d = ctrl.observe("exec_shell", args, failed=True)
        assert d.action == "warn"
        assert "exact" in d.code or "same" in d.code

    def test_block_after_exact_failure_threshold(self):
        ctrl = ToolCallGuardrailController(GuardrailConfig(hard_stop_enabled=True))
        args = {"cmd": "bad_command"}
        for _i in range(6):
            d = ctrl.observe("exec_shell", args, failed=True)
        assert d.should_halt is True

    def test_halt_after_same_tool_threshold(self):
        ctrl = ToolCallGuardrailController(GuardrailConfig(hard_stop_enabled=True))
        for i in range(9):
            d = ctrl.observe("exec_shell", {"cmd": f"cmd_{i}"}, failed=True)
        assert d.should_halt is True

    def test_no_progress_warning_for_idempotent(self):
        ctrl = ToolCallGuardrailController()
        args = {"path": "/tmp/same.txt"}
        for _i in range(3):
            d = ctrl.observe("read_file", args, result="same content")
        assert d.action == "warn"
        assert "no_progress" in d.code

    def test_no_progress_block_with_hard_stop(self):
        ctrl = ToolCallGuardrailController(GuardrailConfig(hard_stop_enabled=True))
        args = {"path": "/tmp/same.txt"}
        for _i in range(6):
            d = ctrl.observe("read_file", args, result="same content")
        assert d.should_halt is True

    def test_successful_mutating_tool_allows(self):
        ctrl = ToolCallGuardrailController()
        d = ctrl.observe("write_text_file", {"path": "/tmp/out.txt"}, result="OK")
        assert d.action == "allow"

    def test_reset_clears_state(self):
        ctrl = ToolCallGuardrailController()
        for _i in range(5):
            ctrl.observe("exec_shell", {"cmd": "ls"}, failed=True)
        ctrl.reset()
        d = ctrl.observe("exec_shell", {"cmd": "ls"}, failed=True)
        assert d.action == "allow"

    def test_total_calls_tracked(self):
        ctrl = ToolCallGuardrailController()
        for i in range(10):
            ctrl.observe("read_file", {"path": f"/tmp/{i}.txt"})
        assert ctrl.total_calls == 10


# ═══════════════════════════════════════════════════════════
# Failure Classification
# ═══════════════════════════════════════════════════════════


class TestClassifyToolFailure:
    def test_none_result_not_failure(self):
        assert classify_tool_failure("tool", None) == (False, "")

    def test_shell_nonzero_exit(self):
        result = '{"exit_code": 1, "stdout": "", "stderr": "error"}'
        failed, reason = classify_tool_failure("exec_shell", result)
        assert failed is True
        assert "exit_code" in reason

    def test_shell_zero_exit_not_failure(self):
        result = '{"exit_code": 0, "stdout": "ok"}'
        failed, _ = classify_tool_failure("exec_shell", result)
        assert failed is False

    def test_error_in_result(self):
        failed, _ = classify_tool_failure("tool", '{"error": "something broke"}')
        assert failed is True

    def test_exception_prefix(self):
        failed, _ = classify_tool_failure("tool", "Exception: timeout")
        assert failed is True

    def test_normal_result_not_failure(self):
        failed, _ = classify_tool_failure("tool", "Operation completed successfully")
        assert failed is False


# ═══════════════════════════════════════════════════════════
# File Safety
# ═══════════════════════════════════════════════════════════


class TestFileWriteSafety:
    def test_empty_path_denied(self):
        v = check_file_write("")
        assert v.allow is False
        assert "empty" in v.reason

    def test_env_file_denied(self):
        v = check_file_write(".env")
        assert v.allow is False
        assert "denied_basename" in v.reason

    def test_bashrc_denied(self):
        v = check_file_write("~/.bashrc")
        assert v.allow is False

    def test_ssh_key_denied(self):
        v = check_file_write("~/.ssh/id_rsa")
        assert v.allow is False
        assert "denied_home_subdir" in v.reason or "denied_basename" in v.reason

    def test_normal_file_allowed(self):
        v = check_file_write("/tmp/myfile.txt")
        assert v.allow is True

    def test_project_file_allowed(self):
        v = check_file_write("F:/projects/myapp/config.yaml")
        assert v.allow is True

    def test_etc_passwd_denied(self):
        import platform

        if platform.system() == "Windows":
            v = check_file_write("C:/etc/passwd")
            if not v.allow:
                assert "denied_abs" in v.reason or "denied_basename" in v.reason
            else:
                pass  # Windows has no /etc/passwd
        else:
            v = check_file_write("/etc/passwd")
            assert v.allow is False
            assert "denied_abs" in v.reason

    def test_aws_dir_denied(self):
        home = os.path.expanduser("~")
        aws_path = os.path.join(home, ".aws", "credentials")
        v = check_file_write(aws_path)
        assert v.allow is False

    def test_is_safe_write_helper(self):
        assert is_safe_write("/tmp/safe.txt") is True
        assert is_safe_write(".env") is False

    def test_verdict_bool(self):
        assert bool(FileWriteVerdict(True, "ok")) is True
        assert bool(FileWriteVerdict(False, "bad", "reason")) is False

    def test_verdict_repr(self):
        v = FileWriteVerdict(True, "ok")
        assert "ALLOW" in repr(v)
        v2 = FileWriteVerdict(False, "bad", "test")
        assert "DENY" in repr(v2)

    def test_denied_basenames_not_empty(self):
        assert len(denied_basenames()) > 0

    def test_denied_home_subdirs_not_empty(self):
        assert len(denied_home_subdirs()) > 0
