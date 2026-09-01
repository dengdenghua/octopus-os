"""Codex Desktop/App Server adapter for same-task behavioral evaluation.

Unlike :mod:`benchmarks.codex_cli_runner`, this adapter talks to the rich-client
``codex app-server`` protocol used by Codex Desktop.  That preserves the
installed plugins, apps, skills, browser/computer-use surfaces, and multi-agent
runtime instead of reducing the comparison to a non-interactive CLI turn.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from benchmarks.codex_cli_runner import WorkspaceResolver, _is_infrastructure_error

ApprovalResponder = Callable[[str, dict[str, Any]], dict[str, Any]]
InstructionsResolver = str | Callable[[Path], str | None]


@dataclass
class CodexAppServerTrialRunner:
    """Run one isolated turn through the Codex Desktop app-server protocol."""

    executable: str | Path
    workspace: WorkspaceResolver
    model: str | None = None
    sandbox: str = "workspace-write"
    approval_policy: str = "never"
    timeout_seconds: float = 900.0
    reasoning_effort: str | None = None
    developer_instructions: InstructionsResolver | None = None
    approval_responder: ApprovalResponder | None = None

    def __call__(self, prompt: str):
        workspace = self.workspace() if callable(self.workspace) else self.workspace
        root = Path(workspace).resolve()
        if not root.is_dir():
            raise ValueError(f"Codex evaluation workspace does not exist: {root}")
        developer_instructions = (
            self.developer_instructions(root)
            if callable(self.developer_instructions)
            else self.developer_instructions
        )

        process = subprocess.Popen(
            [str(self.executable), "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=_codex_child_environment(),
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        messages: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        stderr_lines: list[str] = []
        stdout_thread = threading.Thread(
            target=_read_json_lines,
            args=(process.stdout, messages),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_read_text_lines,
            args=(process.stderr, stderr_lines),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        deadline = time.monotonic() + self.timeout_seconds
        next_request_id = 1
        pending: list[dict[str, Any]] = []
        emitted_agent_items: set[str] = set()

        def send(message: dict[str, Any]) -> None:
            try:
                process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise RuntimeError(f"Codex app-server pipe closed: {exc}") from exc

        def receive() -> dict[str, Any]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Codex app-server trial exceeded {self.timeout_seconds:.1f}s")
            try:
                message = messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(
                    f"Codex app-server trial exceeded {self.timeout_seconds:.1f}s"
                ) from exc
            if isinstance(message, BaseException):
                raise RuntimeError(f"Codex app-server protocol error: {message}") from message
            if message is None:
                detail = "".join(stderr_lines)[-4000:]
                raise RuntimeError(f"Codex app-server exited before completing the turn: {detail}")
            return message

        def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
            nonlocal next_request_id
            request_id = next_request_id
            next_request_id += 1
            send({"id": request_id, "method": method, "params": params})
            while True:
                message = receive()
                if message.get("id") == request_id and "method" not in message:
                    if "error" in message:
                        raise RuntimeError(
                            f"Codex app-server {method} failed: "
                            f"{json.dumps(message['error'], ensure_ascii=False)}"
                        )
                    result = message.get("result")
                    return result if isinstance(result, dict) else {}
                pending.append(message)

        events: list[dict[str, Any]] = []
        try:
            request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "echo_desktop_eval",
                        "title": "Echo Desktop Evaluation",
                        "version": "1",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            send({"method": "initialized", "params": {}})
            thread_params: dict[str, Any] = {
                "cwd": str(root),
                "approvalPolicy": self.approval_policy,
                "sandbox": self.sandbox,
                "ephemeral": True,
                "threadSource": "echo-desktop-eval",
            }
            if self.model:
                thread_params["model"] = self.model
            if developer_instructions:
                thread_params["developerInstructions"] = developer_instructions
            thread_result = request("thread/start", thread_params)
            thread = thread_result.get("thread")
            if not isinstance(thread, dict) or not thread.get("id"):
                raise RuntimeError("Codex app-server thread/start returned no thread id")
            turn_params: dict[str, Any] = {
                "threadId": str(thread["id"]),
                "input": [{"type": "text", "text": prompt}],
            }
            if self.reasoning_effort:
                turn_params["effort"] = self.reasoning_effort
            request("turn/start", turn_params)

            while True:
                message = pending.pop(0) if pending else receive()
                method = str(message.get("method") or "")
                params = message.get("params")
                params = params if isinstance(params, dict) else {}

                if "id" in message and method:
                    events.append(
                        {
                            "kind": "approval_request"
                            if "requestApproval" in method
                            else "server_request",
                            "method": method,
                            "params": params,
                        }
                    )
                    send(
                        {
                            "id": message["id"],
                            "result": self._server_request_response(method, params),
                        }
                    )
                    continue

                events.extend(
                    _app_server_message_to_eval(
                        message,
                        emitted_agent_items=emitted_agent_items,
                    )
                )
                if method == "turn/completed":
                    turn = params.get("turn")
                    turn = turn if isinstance(turn, dict) else {}
                    if str(turn.get("status") or "") == "failed":
                        detail = turn.get("error") or turn
                        kind = (
                            "infrastructure_error" if _is_infrastructure_error(detail) else "error"
                        )
                        events.append({"kind": kind, "error": detail})
                    break
        except Exception as exc:
            detail = f"{exc}\n{''.join(stderr_lines)[-4000:]}".strip()
            kind = "infrastructure_error" if _is_infrastructure_error(detail) else "error"
            events.append({"kind": kind, "error": {"message": detail}})
        finally:
            _terminate_process(process)
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
        return iter(events)

    def _server_request_response(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if self.approval_responder is not None:
            return self.approval_responder(method, params)
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "applyPatchApproval",
            "execCommandApproval",
        }:
            return {"decision": "decline"}
        if method == "item/tool/requestUserInput":
            return {"answers": {}}
        if method == "item/tool/call":
            return {"success": False, "contentItems": []}
        if method == "mcpServer/elicitation/request":
            return {"action": "decline"}
        if method == "account/chatgptAuthTokens/refresh":
            return {"accessToken": "", "chatgptAccountId": "", "chatgptPlanType": None}
        if method == "attestation/generate":
            return {"token": None}
        if method == "item/permissions/requestApproval":
            return {"permissions": {"fileSystem": [], "network": []}, "scope": "turn"}
        return {}


def codex_desktop_version(executable: str | Path) -> str:
    completed = subprocess.run(
        [str(executable), "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Codex version command failed: {completed.stderr[-1000:]}")
    return f"Codex Desktop App Server ({completed.stdout.strip()})"


def _codex_child_environment() -> dict[str, str]:
    """Return the Codex baseline environment without Echo-owned state.

    The behavioral workflow necessarily holds an Echo API token, an
    optional local-auth password, and the path to the real-provider config.
    Passing ``os.environ`` through verbatim made those values visible to the
    comparison process and any plugin it launched.  Keep the signed-in Codex
    profile and normal non-secret runner environment, but remove every Echo
    namespace variable and every common credential-shaped name so the baseline
    cannot inspect or reuse the system-under-test's provider keys, credentials,
    or configuration.
    """

    sensitive_markers = (
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "API_KEY",
        "ACCESS_KEY",
        "PRIVATE_KEY",
        "CREDENTIAL",
    )
    child: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper.startswith("ECHO_") or any(marker in upper for marker in sensitive_markers):
            continue
        child[key] = value
    child["NO_COLOR"] = "1"
    return child


def _read_json_lines(
    stream: TextIO,
    output: queue.Queue[dict[str, Any] | BaseException | None],
) -> None:
    try:
        for line in stream:
            if not line.strip():
                continue
            message = json.loads(line)
            if isinstance(message, dict):
                output.put(message)
    except BaseException as exc:  # pragma: no cover - exercised by live protocol failures
        output.put(exc)
    finally:
        output.put(None)


def _read_text_lines(stream: TextIO, output: list[str]) -> None:
    for line in stream:
        output.append(line)


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _app_server_message_to_eval(
    message: dict[str, Any],
    *,
    emitted_agent_items: set[str],
) -> list[dict[str, Any]]:
    method = str(message.get("method") or "")
    params = message.get("params")
    params = params if isinstance(params, dict) else {}
    if method == "item/agentMessage/delta":
        delta = str(params.get("delta") or "")
        item_id = str(params.get("itemId") or "")
        if item_id:
            emitted_agent_items.add(item_id)
        return [{"kind": "text_delta", "delta": delta}] if delta else []
    if method in {
        "item/reasoning/summaryTextDelta",
        "item/reasoning/textDelta",
    }:
        delta = str(params.get("delta") or "")
        return [{"kind": "reasoning_delta", "delta": delta}] if delta else []
    if method in {"item/started", "item/completed"}:
        item = params.get("item")
        item = item if isinstance(item, dict) else {}
        item_type = str(item.get("type") or "")
        if item_type == "agentMessage":
            item_id = str(item.get("id") or "")
            text = str(item.get("text") or "")
            if method == "item/completed" and text and item_id not in emitted_agent_items:
                return [{"kind": "text_delta", "delta": text}]
            return []
        if item_type == "reasoning":
            if method != "item/completed":
                return []
            summary = item.get("summary")
            content = item.get("content")
            pieces = [
                str(part)
                for group in (summary, content)
                if isinstance(group, list)
                for part in group
                if part
            ]
            return [{"kind": "reasoning_delta", "delta": "\n".join(pieces)}] if pieces else []
        tool_name = _tool_name_for_item(item)
        if tool_name:
            # App Server reports a sub-agent lifecycle as a completed
            # ``subAgentActivity`` item whose semantic kind is ``started``.
            # Preserve that as a start so cross-client trajectory validators
            # count the actual delegated workers.
            event_kind = "tool_start" if method == "item/started" else "tool_end"
            if item_type == "subAgentActivity" and str(item.get("kind") or "") in {
                "started",
                "spawned",
            }:
                event_kind = "tool_start"
            return [
                {
                    "kind": event_kind,
                    "tool_name": tool_name,
                    "item": item,
                }
            ]
    if method == "error":
        detail = params.get("error") or params
        if _is_transient_reconnect(detail):
            return [
                {
                    "kind": "transport_warning",
                    "message": str(params.get("message") or "transport reconnect"),
                }
            ]
        kind = "infrastructure_error" if _is_infrastructure_error(detail) else "error"
        return [{"kind": kind, "error": detail}]
    return []


def _is_transient_reconnect(detail: Any) -> bool:
    rendered = (
        json.dumps(detail, ensure_ascii=False, sort_keys=True)
        if isinstance(detail, (dict, list))
        else str(detail)
    ).lower()
    return bool(re.search(r"\breconnecting\b|response.?stream.?disconnected", rendered))


def _tool_name_for_item(item: dict[str, Any]) -> str:
    item_type = str(item.get("type") or "")
    if item_type == "commandExecution":
        return "command_execution"
    if item_type == "fileChange":
        return "file_change"
    if item_type in {"mcpToolCall", "dynamicToolCall"}:
        return str(item.get("tool") or "mcp_tool")
    if item_type in {"collabAgentToolCall", "subAgentActivity"}:
        return "subagent"
    names = {
        "webSearch": "web_search",
        "imageView": "image_view",
        "imageGeneration": "image_generation",
        "sleep": "sleep",
    }
    return names.get(item_type, "")


__all__ = [
    "ApprovalResponder",
    "CodexAppServerTrialRunner",
    "InstructionsResolver",
    "codex_desktop_version",
]


