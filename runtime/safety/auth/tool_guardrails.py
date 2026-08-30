from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_LOG = logging.getLogger("echo.safety.tool_guardrails")


def _publish_tool_blocked_event(tool_name: str, reason: str) -> None:
    from runtime.platform.process.eventbus import ToolCallBlocked, publish_event

    publish_event(
        ToolCallBlocked(
            event_type="tool_call.blocked",
            tool_name=tool_name,
            reason=reason,
        ),
        logger=_LOG,
    )


IDEMPOTENT_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "search_files",
        "web_search",
        "web_extract",
        "browser_snapshot",
        "browser_console",
        "browser_get_images",
        "list_directory",
        "get_file_info",
        "directory_tree",
        "fs_search",
        "code_intelligence",
        "lsp_diagnostics",
        "lsp_hover",
        "lsp_references",
        "lsp_definitions",
        "verify_skill",
        "skill_library_list",
        "skill_library_view",
        "kg_query",
        "agent_doc_read",
    }
)

MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "exec_shell",
        "write_text_file",
        "append_text_file",
        "edit_text_file",
        "edit_code",
        "delete_file",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_scroll",
        "browser_navigate",
        "git_commit",
        "git_push",
        "git_checkout",
        "git_create_pr",
        "git_merge",
        "git_reset",
        "send_message",
        "http_request",
        "email_send",
        "slack_send",
        "skill_manage",
        "memory_write",
        "plan_mode",
        "delegate_task",
        "cronjob",
    }
)


def classify_tool(tool_name: str) -> str:
    if tool_name in IDEMPOTENT_TOOLS:
        return "idempotent"
    if tool_name in MUTATING_TOOLS:
        return "mutating"
    from runtime.safety.approval.approval_gate import is_dangerous_tool

    if is_dangerous_tool(tool_name):
        return "dangerous"
    return "unknown"


@dataclass(frozen=True)
class ToolCallSignature:
    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Mapping[str, Any] | None) -> ToolCallSignature:
        canonical = _canonical_args(args or {})
        return cls(tool_name=tool_name, args_hash=_sha256(canonical))


@dataclass(frozen=True)
class GuardrailDecision:
    action: str = "allow"
    code: str = "allow"
    message: str = ""
    tool_name: str = ""
    count: int = 0
    signature: ToolCallSignature | None = None

    @property
    def allows_execution(self) -> bool:
        return self.action in {"allow", "warn"}

    @property
    def should_halt(self) -> bool:
        return self.action in {"block", "halt"}


@dataclass
class GuardrailConfig:
    warnings_enabled: bool = True
    hard_stop_enabled: bool = False
    exact_failure_warn_after: int = 2
    exact_failure_block_after: int = 5
    same_tool_failure_warn_after: int = 3
    same_tool_failure_halt_after: int = 8
    no_progress_warn_after: int = 2
    no_progress_block_after: int = 5


class ToolCallGuardrailController:
    def __init__(self, config: GuardrailConfig | None = None) -> None:
        self.config = config or GuardrailConfig()
        self.reset()

    def reset(self) -> None:
        self._exact_failure_counts: dict[ToolCallSignature, int] = {}
        self._same_tool_failure_counts: dict[str, int] = {}
        self._no_progress: dict[ToolCallSignature, tuple[str, int]] = {}
        self._total_calls: int = 0

    def observe(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None = None,
        result: str | None = None,
        failed: bool = False,
    ) -> GuardrailDecision:
        self._total_calls += 1
        sig = ToolCallSignature.from_call(tool_name, args)

        if failed:
            return self._handle_failure(sig, tool_name, result)

        tool_kind = classify_tool(tool_name)
        if tool_kind == "idempotent":
            return self._handle_no_progress(sig, tool_name, result)

        return GuardrailDecision(action="allow", tool_name=tool_name)

    def _handle_failure(
        self,
        sig: ToolCallSignature,
        tool_name: str,
        result: str | None,
    ) -> GuardrailDecision:
        self._exact_failure_counts[sig] = self._exact_failure_counts.get(sig, 0) + 1
        self._same_tool_failure_counts[tool_name] = (
            self._same_tool_failure_counts.get(tool_name, 0) + 1
        )

        exact_count = self._exact_failure_counts[sig]
        same_count = self._same_tool_failure_counts[tool_name]

        if self.config.hard_stop_enabled and exact_count >= self.config.exact_failure_block_after:
            return GuardrailDecision(
                action="block",
                code="exact_failure_block",
                message=f"Exact same call failed {exact_count}x · blocking to prevent loop",
                tool_name=tool_name,
                count=exact_count,
                signature=sig,
            )

        if self.config.hard_stop_enabled and same_count >= self.config.same_tool_failure_halt_after:
            return GuardrailDecision(
                action="halt",
                code="same_tool_halt",
                message=f"Tool '{tool_name}' failed {same_count}x · halting turn",
                tool_name=tool_name,
                count=same_count,
                signature=sig,
            )

        if self.config.warnings_enabled and exact_count >= self.config.exact_failure_warn_after:
            return GuardrailDecision(
                action="warn",
                code="exact_failure_warn",
                message=f"Exact same call failed {exact_count}x · possible loop",
                tool_name=tool_name,
                count=exact_count,
                signature=sig,
            )

        if self.config.warnings_enabled and same_count >= self.config.same_tool_failure_warn_after:
            return GuardrailDecision(
                action="warn",
                code="same_tool_warn",
                message=f"Tool '{tool_name}' failed {same_count}x · check approach",
                tool_name=tool_name,
                count=same_count,
                signature=sig,
            )

        return GuardrailDecision(action="allow", tool_name=tool_name, count=exact_count)

    def _handle_no_progress(
        self,
        sig: ToolCallSignature,
        tool_name: str,
        result: str | None,
    ) -> GuardrailDecision:
        prev_result, prev_count = self._no_progress.get(sig, ("", 0))
        new_count = prev_count + 1
        result_str = (result or "")[:200]
        self._no_progress[sig] = (result_str, new_count)

        if new_count < self.config.no_progress_warn_after:
            return GuardrailDecision(action="allow", tool_name=tool_name, count=new_count)

        if self.config.hard_stop_enabled and new_count >= self.config.no_progress_block_after:
            return GuardrailDecision(
                action="block",
                code="no_progress_block",
                message=f"Idempotent tool '{tool_name}' called {new_count}x with same args · no progress",
                tool_name=tool_name,
                count=new_count,
                signature=sig,
            )

        if self.config.warnings_enabled:
            return GuardrailDecision(
                action="warn",
                code="no_progress_warn",
                message=f"Idempotent tool '{tool_name}' called {new_count}x · no progress detected",
                tool_name=tool_name,
                count=new_count,
                signature=sig,
            )

        return GuardrailDecision(action="allow", tool_name=tool_name, count=new_count)

    @property
    def total_calls(self) -> int:
        return self._total_calls


def classify_tool_failure(tool_name: str, result: str | None) -> tuple[bool, str]:
    if result is None:
        return False, ""

    lower = result[:500].lower()
    if tool_name == "exec_shell":
        try:
            data = json.loads(result)
            if isinstance(data, dict):
                exit_code = data.get("exit_code")
                if exit_code is not None and exit_code != 0:
                    return True, f"exit_code={exit_code}"
        except json.JSONDecodeError:  # noqa: BLE001 — guardrail rule JSON malformed; skip
            pass

    error_indicators = ['"error"', '"failed"', "error:", "exception:", "traceback"]
    for indicator in error_indicators:
        if indicator in lower:
            return True, f"error_indicator: {indicator}"

    if result.startswith("Error") or result.startswith("Exception"):
        return True, "error_prefix"

    return False, ""


def _canonical_args(args: Mapping[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "GuardrailConfig",
    "GuardrailDecision",
    "IDEMPOTENT_TOOLS",
    "MUTATING_TOOLS",
    "ToolCallGuardrailController",
    "ToolCallSignature",
    "classify_tool",
    "classify_tool_failure",
]
