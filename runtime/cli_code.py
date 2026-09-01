from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from runtime.cli_core import _build_stack
from runtime.core.cerebrum.react_loop import ReActResult, stream_react_loop
from runtime.core.cerebrum.react_step_evaluator import build_runtime_step_evaluator
from runtime.platform.models import ParsedIntent
from runtime.platform.process.session import Session, session_scope
from runtime.safety.approval.approval_gate import (
    ApprovalDecision,
    ApprovalProvider,
    AutoApproveProvider,
    AutoDenyProvider,
)

PERMISSION_MODES = ("default", "acceptEdits", "bypassPermissions", "plan")
OUTPUT_FORMATS = ("text", "json", "stream-json")


@dataclass(slots=True)
class _CliCodeAgent:
    agent_id: str = "echo-cli"
    model: str | None = None
    capabilities: dict[str, bool] | None = None
    # CLI ``code`` mode must be able to call every registered skill
    # (edit_file, write_text_file, git_*, …). Without this, the skill
    # policy resolved from the agent only allows ATOMIC_SKILL_NAMES
    # (read-only tools), silently stripping write tools from the
    # native tool-use catalog and breaking SWE-bench / autonomous edits.
    extra_skills: tuple[str, ...] = ("*",)

    def __post_init__(self) -> None:
        if self.capabilities is None:
            self.capabilities = {"code_mode_unlock": True}


class CliApprovalProvider(ApprovalProvider):
    """Headless-friendly approval policy for the coding CLI."""

    def __init__(self, mode: str, *, interactive: bool) -> None:
        self.mode = mode
        self.interactive = interactive
        self._auto_approve = AutoApproveProvider()
        self._auto_deny = AutoDenyProvider()

    def request(
        self,
        req: Any,
        *,
        timeout: float = 120.0,  # noqa: ARG002
    ) -> ApprovalDecision:
        if self.mode == "bypassPermissions":
            return self._auto_approve.request(req)
        if self.mode == "acceptEdits" and _is_edit_tool(req.tool_name):
            return self._auto_approve.request(req)
        if self.mode == "plan":
            return self._auto_deny.request(req)
        if not self.interactive:
            return ApprovalDecision(
                approved=False,
                reason="approval required but stdin is not interactive",
            )

        print(
            f"\nApproval required: {req.tool_name}\n{req.args_preview}\nAllow? [y/N] ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        answer = sys.stdin.readline().strip().lower()
        if answer in {"y", "yes"}:
            return ApprovalDecision(approved=True, reason="cli approved")
        return ApprovalDecision(approved=False, reason="cli declined")


def run_code_command(args: Any, *, color: bool = True) -> int:  # noqa: ARG001
    prompt = _resolve_prompt(args)
    if not prompt:
        print("missing prompt: pass one as an argument or pipe stdin", file=sys.stderr)
        return 2

    permission_mode = _permission_mode(args)
    output_format = getattr(args, "output_format", "text") or "text"
    if output_format not in OUTPUT_FORMATS:
        print(f"invalid output format: {output_format}", file=sys.stderr)
        return 2

    session_record = _load_requested_session(args)
    session_id = session_record.get("id") or uuid4().hex[:12]
    workspace = _resolve_workspace(args, session_record)
    model = _resolve_model(args, session_record)
    thread_id = str(session_record.get("thread_id") or f"cli-{session_id}")
    history = list(session_record.get("messages") or [])
    history.append({"role": "user", "content": prompt, "ts": _now()})

    planner, executor, journal = _build_stack(
        planner_type="llm",
        planner_model=model,
        mock_response=getattr(args, "mock_response", None),
        allow_untrusted=True,
    )
    # Shell is opt-in everywhere else (``register_all`` excludes it): the
    # headless coding CLI is the surface where compile/test/run commands
    # are the point, and the approval provider still gates dangerous
    # invocations. Without it the model cannot verify or run anything and
    # SWE-bench-style tasks end with empty patches.
    from runtime.execution.suckers.write_skills import register_exec_skill

    register_exec_skill(executor.registry)
    stack = SimpleNamespace(planner=planner, executor=executor, journal=journal)
    agent = _CliCodeAgent(model=model)
    metadata = {
        "mode": "plan" if permission_mode == "plan" else "code",
        "workspace_path": str(workspace),
        "model_name": model,
        "permission_mode": permission_mode,
        "sandbox_mode": "sandbox" if getattr(args, "worktree", False) else "full",
        "extra_workspaces": [str(p) for p in _extra_workspaces(args)],
    }
    intent = ParsedIntent(
        raw=prompt,
        intent_type="task",
        normalized_goal=prompt,
        user_context={
            **metadata,
            "metadata": metadata,
            "conversation_messages": history[:-1],
            "auto_approve": permission_mode == "bypassPermissions",
        },
    )

    provider: ApprovalProvider = CliApprovalProvider(
        permission_mode,
        interactive=sys.stdin.isatty() and not getattr(args, "print", False),
    )
    if permission_mode == "bypassPermissions":
        provider = AutoApproveProvider()

    events: list[dict[str, Any]] = []
    result: ReActResult | None = None
    text_buffer: list[str] = []
    with session_scope(Session(agent=agent, thread_id=thread_id, metadata=metadata)):
        gen = stream_react_loop(
            stack,
            intent,
            agent=agent,
            model=model,
            thread_id=thread_id,
            max_iterations=int(getattr(args, "max_iterations", 30) or 30),
            max_tokens_budget=int(getattr(args, "max_tokens", 100_000) or 100_000),
            max_usd_budget=float(getattr(args, "max_usd", 1.00) or 1.00),
            approval_provider=provider,
            step_evaluator=build_runtime_step_evaluator(),
            planning_mode=permission_mode == "plan",
        )
        while True:
            try:
                event = next(gen)
            except StopIteration as stop:
                result = stop.value
                break
            if not isinstance(event, dict):
                continue
            events.append(event)
            if output_format == "stream-json":
                print(json.dumps(event, ensure_ascii=False), flush=True)
            elif output_format == "text" and not getattr(args, "print", False):
                _render_text_event(event, text_buffer)

    final_answer = result.final_answer if result is not None else "".join(text_buffer).strip()
    history.append({"role": "assistant", "content": final_answer, "ts": _now()})
    saved = _save_session(
        {
            "id": session_id,
            "thread_id": thread_id,
            "created_at": session_record.get("created_at") or _now(),
            "updated_at": _now(),
            "workspace_path": str(workspace),
            "model": model,
            "permission_mode": permission_mode,
            "messages": history,
            "last_result": {
                "success": bool(result.success if result is not None else final_answer),
                "terminated_reason": getattr(result, "terminated_reason", None),
            },
        }
    )

    payload = {
        "session_id": session_id,
        "session_path": str(saved),
        "model": model,
        "workspace_path": str(workspace),
        "permission_mode": permission_mode,
        "success": bool(result.success if result is not None else final_answer),
        "final_answer": final_answer,
        "event_count": len(events),
        "continue_command": "echo-agent code --continue",
        "resume_command": f"echo-agent code --resume {session_id}",
    }
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif output_format == "stream-json":
        print(json.dumps({"type": "result", **payload}, ensure_ascii=False), flush=True)
    elif getattr(args, "print", False):
        print(final_answer)
    else:
        print(f"\n[session] {session_id}")
    return 0 if payload["success"] else 1


def list_code_sessions(args: Any) -> int:
    sessions = _list_sessions()
    if getattr(args, "output_format", "text") == "json":
        print(json.dumps(sessions, ensure_ascii=False, indent=2))
        return 0
    if not sessions:
        print("No coding sessions.")
        return 0
    for item in sessions:
        print(
            f"{item['id']}\t{item.get('updated_at', '')}\t"
            f"{item.get('workspace_path', '')}\t{item.get('model', '')}"
        )
    return 0


def _resolve_prompt(args: Any) -> str:
    parts = getattr(args, "prompt", None) or []
    if parts:
        return " ".join(str(p) for p in parts).strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def _permission_mode(args: Any) -> str:
    mode = getattr(args, "permission_mode", None) or "default"
    if mode not in PERMISSION_MODES:
        raise ValueError(f"invalid permission mode: {mode}")
    return mode


def _resolve_workspace(args: Any, session_record: dict[str, Any]) -> Path:
    raw = getattr(args, "cwd", None) or session_record.get("workspace_path") or os.getcwd()
    return Path(raw).expanduser().resolve()


def _resolve_model(args: Any, session_record: dict[str, Any]) -> str:
    for value in (
        getattr(args, "model", None),
        session_record.get("model"),
        os.environ.get("ECHO_MODEL"),
        os.environ.get("ECHO_MODEL_VALUE"),
        os.environ.get("ANTHROPIC_MODEL"),
    ):
        if value:
            return str(value)
    return "mock/react"


def _extra_workspaces(args: Any) -> list[Path]:
    out: list[Path] = []
    for raw in getattr(args, "add_dir", None) or []:
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            out.append(p)
    return out


def _load_requested_session(args: Any) -> dict[str, Any]:
    if getattr(args, "continue_session", False):
        latest = _latest_session_path()
        return _read_session(latest) if latest is not None else {}
    resume_id = getattr(args, "resume", None)
    if resume_id:
        return _read_session(_session_path(str(resume_id)))
    return {}


def _sessions_dir() -> Path:
    home = os.environ.get("ECHO_HOME")
    root = Path(home).expanduser() if home else Path.home() / ".echo"
    return root / "sessions"


def _session_path(session_id: str) -> Path:
    return _sessions_dir() / f"{session_id}.json"


def _save_session(record: dict[str, Any]) -> Path:
    path = _session_path(str(record["id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = path.parent / "latest"
    latest.write_text(str(record["id"]), encoding="utf-8")
    return path


def _read_session(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _latest_session_path() -> Path | None:
    latest = _sessions_dir() / "latest"
    if latest.exists():
        session_id = latest.read_text(encoding="utf-8").strip()
        if session_id:
            return _session_path(session_id)
    candidates = (
        sorted(
            _sessions_dir().glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if _sessions_dir().exists()
        else []
    )
    return candidates[0] if candidates else None


def _list_sessions() -> list[dict[str, Any]]:
    if not _sessions_dir().exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(
        _sessions_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        data = _read_session(path)
        if data:
            out.append(
                {
                    "id": data.get("id"),
                    "updated_at": data.get("updated_at"),
                    "workspace_path": data.get("workspace_path"),
                    "model": data.get("model"),
                    "permission_mode": data.get("permission_mode"),
                }
            )
    return out


def _render_text_event(event: dict[str, Any], text_buffer: list[str]) -> None:
    typ = event.get("type")
    if typ == "text_delta":
        delta = str(event.get("delta") or "")
        text_buffer.append(delta)
        print(delta, end="", flush=True)
    elif typ == "tool_start":
        print(f"\n[tool] {event.get('tool_name') or 'tool'}", file=sys.stderr)
    elif typ == "tool_end":
        status = event.get("status") or "done"
        print(f"[tool:{status}] {event.get('tool_name') or 'tool'}", file=sys.stderr)


def _is_edit_tool(tool_name: str) -> bool:
    return tool_name.startswith(("write_", "append_", "edit_")) or tool_name in {
        "propose_patch",
        "multi_edit_file",
    }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
