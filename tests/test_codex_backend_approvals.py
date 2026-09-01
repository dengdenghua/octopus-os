from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from runtime.execution.codex_backend.approvals import CodexApprovalBroker
from runtime.execution.codex_backend.types import ApprovalRequest
from runtime.safety.approval.approval_gate import ApprovalDecision, ApprovalProvider

_CODEX_0_149_APPS_APPROVAL = (
    Path(__file__).with_name("fixtures")
    / "codex_app_server_0_149"
    / "mcp_apps_approval_request.json"
)


def _apps_approval_request(*, request_id: str = "mcp-approval-149") -> ApprovalRequest:
    payload = json.loads(_CODEX_0_149_APPS_APPROVAL.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    params = payload["params"]
    assert isinstance(params, dict)
    return ApprovalRequest(
        request_id=request_id,
        method="mcpServer/elicitation/request",
        params=copy.deepcopy(params),
    )


class _Provider(ApprovalProvider):
    def __init__(self, approved: bool = True) -> None:
        self.approved = approved
        self.requests = []

    def request(self, req, *, timeout: float = 120.0):
        self.requests.append((req, timeout))
        return ApprovalDecision(approved=self.approved, reason="test")


def _broker(
    tmp_path: Path,
    provider: _Provider,
    interrupted=lambda: False,
    *,
    selected_app_ids: tuple[str, ...] = ("calendar",),
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    broker = CodexApprovalBroker(
        provider,
        outer_thread_id="outer-thread",
        outer_turn_id="outer-turn",
        workspace=workspace,
        selected_app_ids=selected_app_ids,
        is_interrupted=interrupted,
    )
    broker.bind_inner_scope(thread_id="inner-thread", turn_id="inner-turn")
    return broker, workspace


def test_command_approval_uses_outer_provider_without_session_grant(tmp_path: Path) -> None:
    provider = _Provider(approved=True)
    broker, workspace = _broker(tmp_path, provider)

    result = asyncio.run(
        broker(
            ApprovalRequest(
                request_id=7,
                method="item/commandExecution/requestApproval",
                params={
                    "threadId": "inner-thread",
                    "turnId": "inner-turn",
                    "itemId": "command-1",
                    "command": "pytest -q",
                    "cwd": str(workspace),
                },
            )
        )
    )

    assert result == {"decision": "accept"}
    request, timeout = provider.requests[0]
    assert request.thread_id == "outer-thread"
    assert request.tool_name == "exec_shell"
    assert request.tool_call_id == "command-1"
    assert "pytest -q" in request.args_preview
    assert timeout == 120.0


def test_cross_turn_request_and_outside_grant_root_fail_closed(tmp_path: Path) -> None:
    provider = _Provider(approved=True)
    broker, _workspace = _broker(tmp_path, provider)

    cross_turn = asyncio.run(
        broker(
            ApprovalRequest(
                1,
                "item/commandExecution/requestApproval",
                {"threadId": "inner-thread", "turnId": "other", "itemId": "cmd"},
            )
        )
    )
    outside = asyncio.run(
        broker(
            ApprovalRequest(
                2,
                "item/fileChange/requestApproval",
                {
                    "threadId": "inner-thread",
                    "turnId": "inner-turn",
                    "itemId": "patch",
                    "grantRoot": "/etc",
                },
            )
        )
    )

    assert cross_turn == {"decision": "decline"}
    assert outside == {"decision": "decline"}
    assert provider.requests == []


def test_permission_expansion_is_never_forwarded_to_boolean_approval(tmp_path: Path) -> None:
    provider = _Provider(approved=True)
    broker, workspace = _broker(tmp_path, provider)

    result = asyncio.run(
        broker(
            ApprovalRequest(
                3,
                "item/permissions/requestApproval",
                {
                    "threadId": "inner-thread",
                    "turnId": "inner-turn",
                    "itemId": "permissions",
                    "cwd": str(workspace),
                    "permissions": {"network": {"enabled": True}},
                },
            )
        )
    )

    assert result == {"permissions": {}, "scope": "turn", "strictAutoReview": True}
    assert provider.requests == []


def test_request_user_input_confirmation_never_uses_approval_provider(tmp_path: Path) -> None:
    provider = _Provider(approved=True)
    broker, _workspace = _broker(tmp_path, provider)

    result = asyncio.run(
        broker(
            ApprovalRequest(
                5,
                "item/tool/requestUserInput",
                {
                    "threadId": "inner-thread",
                    "turnId": "inner-turn",
                    "itemId": "app-call-1",
                    "isBlocking": True,
                    "questions": [
                        {
                            "id": "approval",
                            "header": "Google Drive",
                            "question": "Create this file?",
                            "options": [
                                {"label": "Accept", "description": "Create it"},
                                {"label": "Decline", "description": "Do not create it"},
                                {"label": "Cancel", "description": "Stop"},
                            ],
                        }
                    ],
                },
            )
        )
    )

    assert result == {"answers": {}}
    assert provider.requests == []


def test_arbitrary_app_questionnaire_fails_closed(tmp_path: Path) -> None:
    provider = _Provider(approved=True)
    broker, _workspace = _broker(tmp_path, provider)

    result = asyncio.run(
        broker(
            ApprovalRequest(
                6,
                "item/tool/requestUserInput",
                {
                    "threadId": "inner-thread",
                    "turnId": "inner-turn",
                    "itemId": "app-call-2",
                    "questions": [
                        {
                            "id": "name",
                            "header": "Name",
                            "question": "What should it be called?",
                            "options": None,
                        }
                    ],
                },
            )
        )
    )

    assert result == {"answers": {}}
    assert provider.requests == []


@pytest.mark.parametrize(
    ("approved", "expected"),
    [
        (True, {"action": "accept", "content": {}}),
        (False, {"action": "decline", "content": None}),
    ],
)
def test_codex_apps_elicitation_maps_to_single_echo_approval(
    tmp_path: Path,
    approved: bool,
    expected: dict[str, Any],
) -> None:
    provider = _Provider(approved=approved)
    broker, _workspace = _broker(tmp_path, provider)

    result = asyncio.run(broker(_apps_approval_request()))

    assert result == expected
    request, timeout = provider.requests[0]
    assert request.thread_id == "outer-thread"
    assert request.tool_name == "codex_app"
    assert request.tool_call_id == "mcp_elicitation:codex_apps:mcp-approval-149"
    assert "calendar_confirm_action" in request.args_preview
    assert "request_nonce" in request.args_preview
    assert request.detail == "Strict automated review #0"
    assert timeout == 120.0


def test_codex_apps_core_generated_approval_without_tool_name_is_supported(
    tmp_path: Path,
) -> None:
    provider = _Provider(approved=True)
    broker, _workspace = _broker(tmp_path, provider)
    request = _apps_approval_request(request_id="core-approval")
    request.params["_meta"] = {
        "codex_approval_kind": "mcp_tool_call",
        "persist": ["session", "always"],
        "source": "connector",
        "connector_id": "calendar",
        "connector_name": "Calendar",
        "connector_description": "Manage events and schedules.",
        "tool_title": "Create Event",
        "tool_description": "Create a calendar event.",
        "tool_params": {"calendar_id": "primary", "title": "Roadmap review"},
        "tool_params_display": [
            {"name": "calendar_id", "value": "primary", "display_name": "Calendar"},
        ],
    }
    request.params["message"] = "Allow Calendar to create an event?"

    result = asyncio.run(broker(request))

    assert result == {"action": "accept", "content": {}}
    outer_request, _timeout = provider.requests[0]
    assert '"tool": "Create Event"' in outer_request.args_preview
    assert '"title": "Roadmap review"' in outer_request.args_preview


def test_codex_apps_elicitation_before_inner_scope_binding_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = _Provider(approved=True)
    broker = CodexApprovalBroker(
        provider,
        outer_thread_id="outer-thread",
        outer_turn_id="outer-turn",
        workspace=workspace,
        selected_app_ids=("calendar",),
        is_interrupted=lambda: False,
    )

    result = asyncio.run(broker(_apps_approval_request()))

    assert result == {"action": "decline", "content": None}
    assert provider.requests == []


def test_codex_apps_elicitation_with_empty_selected_app_set_fails_closed(
    tmp_path: Path,
) -> None:
    provider = _Provider(approved=True)
    broker, _workspace = _broker(tmp_path, provider, selected_app_ids=())

    result = asyncio.run(broker(_apps_approval_request()))

    assert result == {"action": "decline", "content": None}
    assert provider.requests == []


@pytest.mark.parametrize(
    "case",
    [
        "wrong-thread",
        "wrong-turn",
        "null-turn",
        "wrong-server",
        "url-mode",
        "openai-form",
        "ordinary-form",
        "missing-connector",
        "empty-connector",
        "unselected-app",
        "non-empty-schema",
        "extended-empty-schema",
        "tool-suggestion",
        "invalid-persist",
        "auth-meta",
        "unknown-meta",
        "unknown-param",
        "non-object-tool-params",
    ],
)
def test_codex_apps_elicitation_rejection_matrix(tmp_path: Path, case: str) -> None:
    provider = _Provider(approved=True)
    broker, _workspace = _broker(tmp_path, provider)
    request = _apps_approval_request()
    params = request.params
    meta = params["_meta"]
    assert isinstance(meta, dict)

    if case == "wrong-thread":
        params["threadId"] = "other-thread"
    elif case == "wrong-turn":
        params["turnId"] = "other-turn"
    elif case == "null-turn":
        params["turnId"] = None
    elif case == "wrong-server":
        params["serverName"] = "untrusted_server"
    elif case == "url-mode":
        params["mode"] = "url"
        params["url"] = "https://example.com/oauth"
        params["elicitationId"] = "codex_apps_auth_call-1"
        params.pop("requestedSchema")
    elif case == "openai-form":
        params["mode"] = "openai/form"
    elif case == "ordinary-form":
        params["_meta"] = None
    elif case == "missing-connector":
        meta.pop("connector_id")
    elif case == "empty-connector":
        meta["connector_id"] = ""
    elif case == "unselected-app":
        meta["connector_id"] = "google_drive"
    elif case == "non-empty-schema":
        params["requestedSchema"] = {
            "type": "object",
            "properties": {"confirmed": {"type": "boolean"}},
        }
    elif case == "extended-empty-schema":
        params["requestedSchema"]["required"] = []
    elif case == "tool-suggestion":
        meta["codex_approval_kind"] = "tool_suggestion"
    elif case == "invalid-persist":
        meta["persist"] = ["session", "forever"]
    elif case == "auth-meta":
        meta["_codex_apps"] = {
            "connector_auth_failure": {
                "is_auth_failure": True,
                "install_url": "https://example.com/oauth",
            }
        }
    elif case == "unknown-meta":
        meta["future_privileged_mode"] = True
    elif case == "unknown-param":
        params["futureSchema"] = {}
    elif case == "non-object-tool-params":
        meta["tool_params"] = ["unexpected"]
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(case)

    result = asyncio.run(broker(request))

    assert result == {"action": "decline", "content": None}
    assert provider.requests == []


def test_codex_apps_elicitation_interrupt_after_accept_returns_cancel(tmp_path: Path) -> None:
    interrupted = {"value": False}

    class _InterruptingProvider(_Provider):
        def request(self, req, *, timeout: float = 120.0):
            interrupted["value"] = True
            return super().request(req, timeout=timeout)

    provider = _InterruptingProvider(approved=True)
    broker, _workspace = _broker(tmp_path, provider, lambda: interrupted["value"])

    result = asyncio.run(broker(_apps_approval_request()))

    assert result == {"action": "cancel", "content": None}


def test_interrupt_after_user_accept_translates_to_cancel(tmp_path: Path) -> None:
    provider = _Provider(approved=True)
    interrupted = {"value": False}

    class _InterruptingProvider(_Provider):
        def request(self, req, *, timeout: float = 120.0):
            interrupted["value"] = True
            return super().request(req, timeout=timeout)

    provider = _InterruptingProvider()
    broker, _workspace = _broker(tmp_path, provider, lambda: interrupted["value"])
    result = asyncio.run(
        broker(
            ApprovalRequest(
                4,
                "item/fileChange/requestApproval",
                {
                    "threadId": "inner-thread",
                    "turnId": "inner-turn",
                    "itemId": "patch",
                },
            )
        )
    )

    assert result == {"decision": "cancel"}


