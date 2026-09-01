"""Codex CLI adapter for same-task behavioral evaluation.

The runner uses ``codex exec --json --ephemeral`` and never invokes a shell.
It is intentionally inert until called by an eval suite.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WorkspaceResolver = str | Path | Callable[[], str | Path]


@dataclass
class CodexCliTrialRunner:
    executable: str | Path
    workspace: WorkspaceResolver
    model: str | None = None
    sandbox: str = "workspace-write"
    timeout_seconds: float = 900.0
    ignore_user_config: bool = False

    def __call__(self, prompt: str):
        workspace = self.workspace() if callable(self.workspace) else self.workspace
        root = Path(workspace).resolve()
        if not root.is_dir():
            raise ValueError(f"Codex evaluation workspace does not exist: {root}")
        command = [
            str(self.executable),
            "exec",
            "--json",
            "--ephemeral",
            "--color",
            "never",
            "--sandbox",
            self.sandbox,
            "--cd",
            str(root),
            "-",
        ]
        if self.model:
            command[2:2] = ["--model", self.model]
        if self.ignore_user_config:
            command[2:2] = ["--ignore-user-config"]
        environment = {**os.environ, "NO_COLOR": "1"}
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
            env=environment,
        )
        events: list[dict[str, Any]] = []
        for line in completed.stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                events.append({"kind": "protocol_error", "line": line[:1000]})
                continue
            if isinstance(event, dict):
                events.extend(_codex_event_to_eval(event))
        if completed.returncode != 0:
            kind = "infrastructure_error" if _is_infrastructure_error(completed.stderr) else "error"
            events.append(
                {
                    "kind": kind,
                    "error": {
                        "returncode": completed.returncode,
                        "stderr": completed.stderr[-4000:],
                        **({"type": "infrastructure"} if kind == "infrastructure_error" else {}),
                    },
                }
            )
        return iter(events)


def codex_cli_version(executable: str | Path) -> str:
    completed = subprocess.run(
        [str(executable), "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Codex version command failed: {completed.stderr[-1000:]}")
    return completed.stdout.strip()


def _codex_event_to_eval(event: dict[str, Any]) -> list[dict[str, Any]]:
    event_type = str(event.get("type") or event.get("event") or "")
    item = event.get("item")
    if isinstance(item, dict):
        item_type = str(item.get("type") or "")
        if item_type in {"agent_message", "agentMessage"}:
            text = str(item.get("text") or "")
            return [{"kind": "text_delta", "delta": text}] if text else []
        if item_type in {"command_execution", "commandExecution"}:
            kind = "tool_start" if event_type.endswith("started") else "tool_end"
            return [{"kind": kind, "tool_name": "command_execution", "item": item}]
        if item_type in {"file_change", "fileChange"}:
            kind = "tool_start" if event_type.endswith("started") else "tool_end"
            return [{"kind": kind, "tool_name": "file_change", "item": item}]
        if item_type in {"mcp_tool_call", "mcpToolCall"}:
            kind = "tool_start" if event_type.endswith("started") else "tool_end"
            return [
                {
                    "kind": kind,
                    "tool_name": str(item.get("tool") or item.get("name") or "mcp_tool"),
                    "item": item,
                }
            ]
        if item_type in {
            "subagent",
            "collab_agent_tool_call",
            "collabAgentToolCall",
            "agent_tool_call",
        }:
            kind = "tool_start" if event_type.endswith("started") else "tool_end"
            return [{"kind": kind, "tool_name": "subagent", "item": item}]
        if item_type == "reasoning":
            text = str(item.get("text") or item.get("summary") or "")
            return [{"kind": "reasoning_delta", "delta": text}] if text else []
    if event_type in {"error", "turn.failed"}:
        detail = event.get("error") or event
        kind = "infrastructure_error" if _is_infrastructure_error(detail) else "error"
        return [{"kind": kind, "error": detail}]
    return [{"kind": "protocol_event", "event_type": event_type, "event": event}]


_INFRASTRUCTURE_ERROR_PATTERNS = (
    r"\breconnecting\b",
    r"stream disconnected",
    r"tls (?:handshake|error|failure)",
    r"connection (?:reset|refused|aborted|closed)",
    r"failed to connect",
    r"network (?:is unreachable|error|failure)",
    r"temporary failure in name resolution",
    r"\b(?:dns|econnreset|econnrefused)\b",
    r"\b(?:402|429|502|503|504)\b",
    r"rate.?limit",
    r"(?:usage|spend|quota) limit",
    r"insufficient_quota",
    r"insufficient (?:balance|funds)",
    r"payment required",
    r"credit balance",
    r"余额不足",
    r"service unavailable",
    r"gateway timeout",
    r"provider (?:is )?(?:unavailable|overloaded)",
    r"authentication (?:failed|required)",
    r"invalid api key",
)


def _is_infrastructure_error(detail: Any) -> bool:
    """Recognize provider/transport failures without scoring them as agent mistakes."""

    if isinstance(detail, (dict, list)):
        rendered = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    else:
        rendered = str(detail)
    lowered = rendered.lower()
    return any(re.search(pattern, lowered) for pattern in _INFRASTRUCTURE_ERROR_PATTERNS)


__all__ = ["CodexCliTrialRunner", "WorkspaceResolver", "codex_cli_version"]

