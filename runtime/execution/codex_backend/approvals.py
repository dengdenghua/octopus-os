"""Fail-closed bridge from Codex App Server approvals to Echo policy.

Codex owns the inner protocol shape; Echo remains the authority that asks
the user and records the decision.  This adapter deliberately grants only the
single command, patch, or marker-bound Codex App tool currently displayed.
Arbitrary elicitation input, session-wide approvals, exec-policy amendments,
network-policy amendments, and permission expansion stay unavailable here.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from runtime.safety.approval.approval_gate import (
    ApprovalProvider,
)
from runtime.safety.approval.approval_gate import (
    ApprovalRequest as EchoApprovalRequest,
)

from ._transport import (
    MCP_ELICITATION_APPROVAL_METHOD,
    TOOL_USER_INPUT_METHOD,
    McpElicitationApproval,
    parse_mcp_elicitation_approval,
)
from .types import ApprovalRequest as CodexApprovalRequest

_COMMAND_APPROVAL = "item/commandExecution/requestApproval"
_FILE_APPROVAL = "item/fileChange/requestApproval"
_PERMISSIONS_APPROVAL = "item/permissions/requestApproval"
_MAX_PREVIEW_CHARS = 8_000


class CodexApprovalBroker:
    """Translate one App Server's server-initiated approval requests.

    Scope is installed only after ``thread/start|resume`` and ``turn/start``
    return.  A request before that point, from another inner turn, after an
    outer interrupt, for an App outside the frozen turn selection, or asking
    to widen filesystem permissions is denied.
    """

    def __init__(
        self,
        provider: ApprovalProvider,
        *,
        outer_thread_id: str,
        outer_turn_id: str,
        workspace: Path,
        selected_app_ids: tuple[str, ...],
        is_interrupted: Any,
        timeout_s: float = 120.0,
    ) -> None:
        if (
            not isinstance(selected_app_ids, tuple)
            or len(selected_app_ids) > 32
            or any(
                not isinstance(app_id, str)
                or app_id != app_id.strip()
                or not app_id
                or len(app_id) > 256
                or any(char in app_id for char in "\x00\r\n")
                for app_id in selected_app_ids
            )
        ):
            raise ValueError("selected_app_ids must be a frozen tuple of safe identifiers")
        self._provider = provider
        self._outer_thread_id = outer_thread_id
        self._outer_turn_id = outer_turn_id
        self._workspace = workspace.resolve(strict=True)
        self._selected_app_ids = frozenset(selected_app_ids)
        self._is_interrupted = is_interrupted
        self._timeout_s = max(1.0, float(timeout_s))
        self._inner_thread_id: str | None = None
        self._inner_turn_id: str | None = None

    def bind_inner_scope(self, *, thread_id: str, turn_id: str) -> None:
        if not thread_id or not turn_id:
            raise ValueError("inner Codex thread and turn ids must be non-empty")
        self._inner_thread_id = thread_id
        self._inner_turn_id = turn_id

    async def __call__(self, request: CodexApprovalRequest) -> dict[str, Any]:
        if self._must_deny(request):
            return self._denial(request.method, cancelled=self._interrupted())
        if request.method == _PERMISSIONS_APPROVAL:
            # An inner request may never enlarge the workspace/network policy
            # selected by the authenticated outer turn.
            return {"permissions": {}, "scope": "turn", "strictAutoReview": True}
        if request.method == MCP_ELICITATION_APPROVAL_METHOD:
            return await self._resolve_mcp_elicitation(request)
        if request.method == _FILE_APPROVAL and not self._safe_grant_root(request.params):
            return self._denial(request.method)

        tool_name = "exec_shell" if request.method == _COMMAND_APPROVAL else "apply_patch"
        item_id = str(request.params.get("itemId") or "").strip()
        if not item_id:
            return self._denial(request.method)
        args_preview = self._args_preview(request)
        detail = str(request.params.get("reason") or "Codex requested approval")[:1000]
        outer_request = EchoApprovalRequest(
            thread_id=self._outer_thread_id,
            tool_name=tool_name,
            tool_call_id=item_id,
            args_preview=args_preview,
            detail=detail,
        )
        try:
            decision = await asyncio.to_thread(
                self._provider.request,
                outer_request,
                timeout=self._timeout_s,
            )
        except (OSError, RuntimeError, TimeoutError):
            return self._denial(request.method)
        if self._interrupted():
            return self._denial(request.method, cancelled=True)
        return {"decision": "accept" if decision.approved else "decline"}

    def _must_deny(self, request: CodexApprovalRequest) -> bool:
        if self._interrupted() or self._inner_thread_id is None or self._inner_turn_id is None:
            return True
        params = request.params
        return (
            params.get("threadId") != self._inner_thread_id
            or params.get("turnId") != self._inner_turn_id
            or request.method
            not in {
                _COMMAND_APPROVAL,
                _FILE_APPROVAL,
                _PERMISSIONS_APPROVAL,
                MCP_ELICITATION_APPROVAL_METHOD,
            }
        )

    def _interrupted(self) -> bool:
        try:
            return bool(self._is_interrupted())
        except Exception:  # noqa: BLE001 - a broken interrupt source fails closed
            return True

    def _safe_grant_root(self, params: dict[str, Any]) -> bool:
        raw = params.get("grantRoot")
        if raw in (None, ""):
            return True
        try:
            candidate = Path(str(raw)).expanduser().resolve(strict=False)
            candidate.relative_to(self._workspace)
        except (OSError, ValueError):
            return False
        return True

    @staticmethod
    def _denial(method: str, *, cancelled: bool = False) -> dict[str, Any]:
        if method == MCP_ELICITATION_APPROVAL_METHOD:
            return {
                "action": "cancel" if cancelled else "decline",
                "content": None,
            }
        if method == TOOL_USER_INPUT_METHOD:
            return {"answers": {}}
        if method == _PERMISSIONS_APPROVAL:
            return {"permissions": {}, "scope": "turn", "strictAutoReview": True}
        return {"decision": "cancel" if cancelled else "decline"}

    async def _resolve_mcp_elicitation(
        self,
        request: CodexApprovalRequest,
    ) -> dict[str, Any]:
        approval = parse_mcp_elicitation_approval(request.params)
        if approval is None or approval.connector_id not in self._selected_app_ids:
            return self._denial(request.method)

        outer_request = EchoApprovalRequest(
            thread_id=self._outer_thread_id,
            tool_name="codex_app",
            tool_call_id=(f"mcp_elicitation:codex_apps:{request.request_id}")[:1_000],
            args_preview=self._mcp_args_preview(approval),
            detail=approval.message[:1_000],
        )
        try:
            decision = await asyncio.to_thread(
                self._provider.request,
                outer_request,
                timeout=self._timeout_s,
            )
        except Exception:  # noqa: BLE001 - an approval transport must fail closed
            return self._denial(request.method)
        if self._interrupted():
            return self._denial(request.method, cancelled=True)
        if not decision.approved:
            return self._denial(request.method)
        return {"action": "accept", "content": {}}

    @staticmethod
    def _mcp_args_preview(approval: McpElicitationApproval) -> str:
        preview = {
            "server": "codex_apps",
            "connector": approval.connector_id,
            "tool": approval.tool_name or approval.tool_title,
            "arguments": approval.tool_params,
        }
        return json.dumps(preview, ensure_ascii=False, sort_keys=True)[:_MAX_PREVIEW_CHARS]

    @staticmethod
    def _args_preview(request: CodexApprovalRequest) -> str:
        params = request.params
        if request.method == _COMMAND_APPROVAL:
            preview: Any = {
                "command": params.get("command"),
                "cwd": params.get("cwd"),
                "actions": params.get("commandActions"),
            }
        else:
            preview = {"grantRoot": params.get("grantRoot")}
        rendered = json.dumps(preview, ensure_ascii=False, sort_keys=True)
        return rendered[:_MAX_PREVIEW_CHARS]


__all__ = ["CodexApprovalBroker"]
