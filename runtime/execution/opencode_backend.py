"""Managed, loopback-only OpenCode engine with Echo-owned host tools.

The official process owns inference and its tools. Echo owns the authenticated
principal, conversation, cancellation and permission ceiling. No provider
headers are emulated and no model API is exposed to other engines.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from runtime.execution.host_mcp_connection import HostMCPConnection
from runtime.platform.capabilities.tenant_context import use_capability_scope
from runtime.platform.connectors.credential_store import CredentialStore
from runtime.platform.models.custom_model_selection import resolve_custom_model_selection
from runtime.platform.process.paths import app_paths
from runtime.safety.auth.scope import TenantScope


class OpenCodeError(RuntimeError):
    """Safe-to-display engine failure, without credentials or raw responses."""


def executable() -> str | None:
    configured = os.environ.get("ECHO_OPENCODE_BIN")
    if configured:
        path = Path(configured).expanduser()
        return str(path.resolve()) if path.is_absolute() and path.is_file() else None
    # Optional engine execution is an operator-configured path.
    # Never borrow an unrelated source tree or ambient PATH installation.
    return None


def zen_catalog() -> dict[str, Any]:
    try:
        value = json.loads(app_paths().custom_models_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def resolve_zen_model(selection: str | None, catalog: dict[str, Any]) -> str:
    if not selection or selection == "auto":
        raise OpenCodeError("请显式选择已配置的 OpenCode Zen 模型。")
    entry = catalog.get("opencode-zen")
    if not isinstance(entry, dict) or entry.get("managed_by_plugin") != "opencode-zen":
        raise OpenCodeError("请先安装并连接 OpenCode Zen 模型插件。")
    selected = resolve_custom_model_selection(catalog, selection or "")
    if selected:
        if selected.entry_id != "opencode-zen" or selected.context_profile != "default":
            raise OpenCodeError("OpenCode 引擎请选择 Zen 插件中的默认上下文模型。")
        model = selected.model
    else:
        model = (selection or "big-pickle").removeprefix("opencode/")
    if model not in entry.get("models", []):
        raise OpenCodeError("OpenCode 引擎请选择 Zen 模型，例如 big-pickle。")
    return model


def zen_key(scope: TenantScope | None) -> str | None:
    with use_capability_scope(scope):
        key = CredentialStore().get_secret("opencode-zen", "api_key")
    return key or None


def inspect_readiness(scope: TenantScope | None, selection: str | None = None) -> dict[str, Any]:
    from runtime.safety.privacy import privacy_enabled

    if privacy_enabled():
        return {"available": False, "reason": "隐私模式不允许 OpenCode Zen 云端推理。"}
    if os.environ.get("ECHO_DEPLOYMENT_MODE", "local").strip().lower() != "local":
        return {"available": False, "reason": "OpenCode 引擎目前仅支持本地运行。"}
    if not executable():
        return {"available": False, "reason": "请安装 OpenCode 并配置 ECHO_OPENCODE_BIN。"}
    try:
        resolve_zen_model(selection, zen_catalog())
        zen_key(scope)
    except OpenCodeError as exc:
        return {"available": False, "reason": str(exc)}
    except (OSError, ValueError, RuntimeError):
        return {"available": False, "reason": "暂时无法读取 Zen 连接配置。"}
    return {
        "available": True,
        "reason": None,
        "capabilities": ["chat", "web_research", "host_tools", "skills", "plugin_actions"],
    }


def state_directory(scope: TenantScope | None, thread_id: str) -> Path:
    # Never use browser-supplied paths or ids as filesystem components.
    identity = [scope.tenant_id, scope.actor_id] if scope else [None, None]
    digest = hashlib.sha256(json.dumps([*identity, thread_id]).encode()).hexdigest()
    return app_paths().data_dir / "opencode" / digest


def child_environment(
    root: Path,
    key: str | None,
    password: str,
    model: str,
    web: bool,
    *,
    host_mcp: HostMCPConnection | None = None,
) -> dict[str, str]:
    # Do not inherit other provider credentials, plugins, or OpenCode settings.
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "LANG",
        "LC_ALL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_EXTRA_CA_CERTS",
    }
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    for name in ("CONFIG", "DATA", "CACHE", "STATE"):
        directory = root / name.lower()
        directory.mkdir(parents=True, exist_ok=True)
        env[f"XDG_{name}_HOME"] = str(directory)
    permission = {"*": "deny"}
    # Web access, like files and shell, must use the advertised Echo tools.
    # A network grant does not authorize unaudited engine-native web helpers.
    if host_mcp:
        permission["echo_*"] = "allow"
    config = {
        "model": f"opencode/{model}",
        "small_model": f"opencode/{model}",
        "share": "disabled",
        "autoupdate": False,
        "snapshot": False,
        "permission": permission,
        "default_agent": "echo",
        "agent": {"echo": {"mode": "primary", "permission": permission, "steps": 16}},
    }
    if key:
        config["provider"] = {"opencode": {"options": {"apiKey": "{env:OPENCODE_API_KEY}"}}}
        env["OPENCODE_API_KEY"] = key
    if host_mcp:
        config["mcp"] = {
            "echo": {
                "type": "remote",
                "url": host_mcp.url,
                "headers": {"Authorization": "Bearer {env:ECHO_HOST_MCP_TOKEN}"},
                "oauth": False,
                "enabled": True,
                # Native approval can wait up to 120s before tool execution.
                "timeout": 180_000,
            }
        }
        env["ECHO_HOST_MCP_TOKEN"] = host_mcp.token
    env.update(
        OPENCODE_SERVER_PASSWORD=password,
        OPENCODE_SERVER_USERNAME="opencode",
        OPENCODE_DISABLE_AUTOUPDATE="true",
        OPENCODE_DISABLE_DEFAULT_PLUGINS="true",
        OPENCODE_DISABLE_CLAUDE_CODE="true",
        OPENCODE_DISABLE_EXTERNAL_SKILLS="true",
        OPENCODE_DISABLE_PROJECT_CONFIG="true",
        OPENCODE_DISABLE_LSP_DOWNLOAD="true",
        OPENCODE_CONFIG_CONTENT=json.dumps(config),
    )
    return env


@asynccontextmanager
async def managed_server(
    command: str,
    root: Path,
    key: str | None,
    model: str,
    web: bool,
    *,
    host_mcp: HostMCPConnection | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    from runtime.safety.privacy import deny_private_operation

    deny_private_operation("opencode_zen")
    if os.environ.get("ECHO_DEPLOYMENT_MODE", "local").strip().lower() != "local":
        raise OpenCodeError("OpenCode 引擎目前仅支持本地运行。")
    if command != executable():
        raise OpenCodeError("OpenCode 执行文件未经过本机配置。")
    from runtime.platform.process.tree import process_group_kwargs, terminate_pid_tree

    group = process_group_kwargs()
    if os.name == "nt":
        group["creationflags"] = group.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    password = secrets.token_urlsafe(32)
    process = await asyncio.create_subprocess_exec(
        command,
        "serve",
        "--hostname",
        "127.0.0.1",
        "--port",
        str(port),
        cwd=workspace,
        env=child_environment(root, key, password, model, web, host_mcp=host_mcp),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **group,
    )
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            auth=("opencode", password),
            trust_env=False,
            timeout=10,
        ) as client:
            for _ in range(100):
                if process.returncode is not None:
                    raise OpenCodeError("OpenCode 启动失败，请检查安装版本和引擎配置。")
                try:
                    response = await client.get("/global/health", timeout=1)
                    if response.status_code == 200 and response.json().get("healthy"):
                        break
                except (httpx.TransportError, ValueError):
                    pass
                await asyncio.sleep(0.2)
            else:
                raise OpenCodeError("OpenCode 启动超时，请检查本地引擎。")
            await validate_catalog_model(client, model, free_only=not key)
            yield client
    finally:
        if process.returncode is None:
            await asyncio.to_thread(terminate_pid_tree, process.pid, grace_s=1.0, kill_wait_s=2.0)
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()


async def validate_catalog_model(
    client: httpx.AsyncClient, model: str, *, free_only: bool = False
) -> None:
    """Reject stale Zen selections before OpenCode reports an opaque HTTP 500."""
    try:
        response = await client.get("/provider")
        response.raise_for_status()
        payload = response.json()
        providers = payload.get("all") if isinstance(payload, dict) else None
        if not isinstance(providers, list):
            raise ValueError("invalid provider catalog")
        zen = next(
            (p for p in providers if isinstance(p, dict) and p.get("id") == "opencode"),
            {},
        )
        models = zen.get("models", {})
        if not isinstance(models, dict):
            raise ValueError("invalid model catalog")
    except (httpx.HTTPError, ValueError):
        raise OpenCodeError("无法读取 OpenCode 模型列表，请稍后重试。") from None
    if model not in models:
        raise OpenCodeError("OpenCode 当前未提供所选 Zen 模型，请在输入框选择其他模型后重试。")
    if free_only:
        cost = models[model].get("cost") if isinstance(models[model], dict) else None

        def zero_price(value: Any) -> bool:
            if isinstance(value, dict):
                return all(zero_price(item) for item in value.values())
            return type(value) in (int, float) and value == 0

        if (
            not isinstance(cost, dict)
            or not {"input", "output"}.issubset(cost)
            or not zero_price(cost)
        ):
            raise OpenCodeError("未连接 Zen 账号时仅可使用官方目录中明确标为零费用的模型。")


async def session_for_thread(client: httpx.AsyncClient, root: Path) -> str:
    mapping = root / "session.json"
    if mapping.exists():
        try:
            session_id = json.loads(mapping.read_text(encoding="utf-8"))["session_id"]
            if not isinstance(session_id, str) or not re.fullmatch(r"ses_[A-Za-z0-9]+", session_id):
                raise ValueError("invalid session coordinate")
        except (ValueError, KeyError):
            raise OpenCodeError("OpenCode 会话记录损坏，请新建对话。") from None
        response = await client.get(f"/session/{session_id}")
        if response.status_code == 200:
            return session_id
        if response.status_code != 404:
            raise OpenCodeError("无法读取 OpenCode 会话，请稍后重试。")
        # Do not silently lose a conversation that used to exist.
        raise OpenCodeError("OpenCode 会话已不存在，请新建对话继续。")
    response = await client.post("/session", json={"title": "Echo"})
    response.raise_for_status()
    try:
        session_id = response.json()["id"]
        if not isinstance(session_id, str) or not re.fullmatch(r"ses_[A-Za-z0-9]+", session_id):
            raise ValueError("invalid session coordinate")
    except (ValueError, KeyError, TypeError):
        raise OpenCodeError("OpenCode 返回了无效的会话记录。") from None
    temporary = mapping.with_suffix(".tmp")
    temporary.write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
    temporary.replace(mapping)
    return session_id


def public_model_error(error: Any) -> str:
    detail = json.dumps(error, ensure_ascii=False).lower()
    if "429" in detail or "limit" in detail or "rate" in detail:
        return "Zen 当前额度或请求频率受限，请稍后重试或切换模型。"
    if "401" in detail or "unauthorized" in detail or "authentication" in detail:
        return "Zen 连接已失效，请在插件设置中重新连接。"
    if "model" in detail and any(x in detail for x in ("not found", "unavailable", "404")):
        return "当前 Zen 模型不可用，请切换其他 Zen 模型。"
    return "OpenCode 调用 Zen 失败，请稍后重试或切换 Zen 模型。"


@dataclass
class MessageEvents:
    """Reduce growing message snapshots without replaying old prose or tools."""

    baseline: set[str]
    text: dict[str, str] = field(default_factory=dict)
    tools: dict[str, str] = field(default_factory=dict)
    last_text_part: str | None = None
    tool_names: dict[str, str] = field(default_factory=dict)

    def consume(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events = []
        for message in messages:
            info = message.get("info", {})
            if info.get("role") != "assistant" or info.get("id") in self.baseline:
                continue
            for part in message.get("parts", []):
                part_id = str(part.get("id", ""))
                if part.get("type") == "text" and not part.get("synthetic"):
                    value = str(part.get("text") or "")
                    old = self.text.get(part_id, "")
                    if value.startswith(old) and len(value) > len(old):
                        events.append(
                            {
                                "type": "text_delta",
                                "delta": value[len(old) :],
                                "start_new_segment": self.last_text_part not in (None, part_id),
                            }
                        )
                        self.last_text_part = part_id
                        self.text[part_id] = value
                elif part.get("type") == "tool":
                    state = part.get("state", {})
                    status = state.get("status")
                    call_id = str(part.get("callID") or part_id)
                    engine_name = str(part.get("tool") or "tool")
                    tool = self.tool_names.get(engine_name, f"opencode.{engine_name}")
                    tool_input = state.get("input", {})
                    encoded_input = json.dumps(tool_input, ensure_ascii=False)
                    input_preview = (
                        tool_input
                        if isinstance(tool_input, dict) and len(encoded_input) <= 100_000
                        else encoded_input[:2000]
                    )
                    if status in ("running", "completed", "error") and call_id not in self.tools:
                        events.append(
                            {
                                "type": "tool_start",
                                "tool_name": tool,
                                "tool_call_id": call_id,
                                "input_preview": input_preview,
                            }
                        )
                    if status in ("completed", "error") and self.tools.get(call_id) not in (
                        "completed",
                        "error",
                    ):
                        events.append(
                            {
                                "type": "tool_end",
                                "tool_name": tool,
                                "tool_call_id": call_id,
                                "success": status == "completed",
                                "status": "success" if status == "completed" else "error",
                                "output_preview": str(
                                    state.get("output") or state.get("error") or ""
                                )[:4000],
                            }
                        )
                    if status != "pending":
                        self.tools[call_id] = status
        return events


async def stream_prompt(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    text: str,
    system: str,
    model: str,
    interrupted: Callable[[], bool],
    poll_s: float = 0.4,
    tool_names: dict[str, str] | None = None,
    fresh_thread_text: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    url = f"/session/{session_id}"
    from runtime.safety.privacy import deny_private_operation

    deny_private_operation("opencode_zen_prompt")
    previous = await client.get(f"{url}/message")
    previous.raise_for_status()
    previous_messages = previous.json()
    if not previous_messages and fresh_thread_text is not None:
        text = fresh_thread_text
    reducer = MessageEvents(
        {m["info"]["id"] for m in previous_messages}, tool_names=tool_names or {}
    )
    pending = asyncio.create_task(
        client.post(
            f"{url}/message",
            json={
                "model": {"providerID": "opencode", "modelID": model},
                "agent": "echo",
                "system": system,
                "parts": [{"type": "text", "text": text}],
            },
            timeout=None,
        )
    )
    try:
        while True:
            deny_private_operation("opencode_zen_prompt")
            if interrupted():
                yield {"type": "react_cancelled", "reason": "用户停止了任务"}
                return
            await asyncio.wait({pending}, timeout=poll_s)
            response = await client.get(f"{url}/message")
            response.raise_for_status()
            messages = response.json()
            for event in reducer.consume(messages):
                yield event
            if not pending.done():
                continue
            result = await pending
            if result.is_error:
                raise OpenCodeError(
                    public_model_error(
                        {"statusCode": result.status_code, "body": result.text[:16_384]}
                    )
                )
            final = result.json()
            for event in reducer.consume([final]):
                yield event
            info = final.get("info", {})
            if info.get("error"):
                raise OpenCodeError(public_model_error(info["error"]))
            if info.get("finish") not in ("stop", "end_turn"):
                raise OpenCodeError("OpenCode 未完成本次回答，请重试继续。")
            if not any(reducer.text.values()):
                raise OpenCodeError("Zen 没有返回回答，请重试或切换模型。")
            yield {
                "type": "react_completed",
                "success": True,
                "terminated_reason": "completed",
                "completion_receipt": {
                    "engine": "opencode",
                    "model": model,
                    "cost": info.get("cost"),
                    "tokens": info.get("tokens"),
                },
            }
            return
    finally:
        if not pending.done():
            with contextlib.suppress(Exception):
                await client.post(f"{url}/abort", timeout=2)
            pending.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pending
