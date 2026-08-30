from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from runtime.adapters.mcp_client.trust import get_trust_store


def run_mcp_command(args: argparse.Namespace) -> int:
    op = getattr(args, "mcp_op", None)
    if op == "add":
        return _add_server(args)
    if op == "list":
        return _list_servers(args)
    if op == "remove":
        return _remove_server(args)
    if op == "trust":
        return _trust_server(args)
    if op == "revoke":
        return _revoke_server(args)
    if op == "serve":
        return _serve_mcp(args)
    print("missing mcp operation", file=sys.stderr)
    return 2


def _add_server(args: argparse.Namespace) -> int:
    tail_options, command = _split_add_tail(getattr(args, "server_command", None) or [])
    if not command:
        print(
            "missing command: use `echo-agent mcp add NAME -- COMMAND [ARGS...]`",
            file=sys.stderr,
        )
        return 2

    env_items = list(getattr(args, "env", None) or []) + tail_options["env"]
    trust_level = tail_options["trust_level"] or getattr(args, "trust_level", None) or "custom"
    timeout_ms = tail_options["timeout_ms"] or int(getattr(args, "timeout_ms", 30_000) or 30_000)
    disabled = bool(getattr(args, "disabled", False) or tail_options["disabled"])
    trust_requested = bool(getattr(args, "trust", False) or tail_options["trust"])

    data = _read_config()
    servers = data.setdefault("mcpServers", {})
    name = str(args.name)
    servers[name] = {
        "command": command[0],
        "args": command[1:],
        "env": _parse_env(env_items),
        "trust_level": trust_level,
        "timeout_ms": timeout_ms,
        "enabled": not disabled,
        "updated_at": _now(),
    }
    _write_config(data)
    if trust_requested:
        get_trust_store().approve(name, note="trusted by echo-agent mcp add --trust")
    print(f"Added MCP server {name}.")
    return 0


def _list_servers(args: argparse.Namespace) -> int:
    data = _read_config()
    servers = data.get("mcpServers") or {}
    trust = {entry.server_name: entry for entry in get_trust_store().list_all()}
    rows: list[dict[str, Any]] = []
    for name, cfg in sorted(servers.items()):
        entry = trust.get(name)
        rows.append(
            {
                "name": name,
                "command": cfg.get("command"),
                "args": list(cfg.get("args") or []),
                "enabled": bool(cfg.get("enabled", True)),
                "trusted": bool(entry.approved) if entry else False,
                "trust_level": cfg.get("trust_level") or "custom",
                "timeout_ms": cfg.get("timeout_ms"),
            }
        )

    if getattr(args, "output_format", "text") == "json":
        print(json.dumps({"mcpServers": rows}, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("No MCP servers configured.")
        return 0
    for row in rows:
        status = "trusted" if row["trusted"] else "untrusted"
        enabled = "enabled" if row["enabled"] else "disabled"
        tail = " ".join([row["command"] or "", *row["args"]]).strip()
        print(f"{row['name']}\t{enabled}\t{status}\t{tail}")
    return 0


def _remove_server(args: argparse.Namespace) -> int:
    data = _read_config()
    servers = data.setdefault("mcpServers", {})
    name = str(args.name)
    existed = name in servers
    servers.pop(name, None)
    _write_config(data)
    if getattr(args, "forget_trust", True):
        get_trust_store().forget(name)
    if existed:
        print(f"Removed MCP server {name}.")
        return 0
    print(f"MCP server not found: {name}", file=sys.stderr)
    return 1


def _trust_server(args: argparse.Namespace) -> int:
    name = str(args.name)
    tools = list(getattr(args, "tools", None) or [])
    note = getattr(args, "note", None) or "trusted by echo-agent mcp trust"
    get_trust_store().approve(name, tools, note=note)
    print(f"Trusted MCP server {name}.")
    return 0


def _revoke_server(args: argparse.Namespace) -> int:
    name = str(args.name)
    ok = get_trust_store().revoke(name)
    if ok:
        print(f"Revoked MCP server {name}.")
        return 0
    print(f"MCP trust entry not found: {name}", file=sys.stderr)
    return 1


def _config_path() -> Path:
    home = os.environ.get("ECHO_HOME")
    root = Path(home).expanduser() if home else Path.home() / ".echo"
    return root / "mcp_servers.json"


def _read_config() -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return {"version": 1, "mcpServers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "mcpServers": {}}
    if not isinstance(data, dict):
        return {"version": 1, "mcpServers": {}}
    if not isinstance(data.get("mcpServers"), dict):
        data["mcpServers"] = {}
    data.setdefault("version", 1)
    return data


def _write_config(data: dict[str, Any]) -> Path:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _split_add_tail(raw: list[str]) -> tuple[dict[str, Any], list[str]]:
    options: dict[str, Any] = {
        "env": [],
        "trust_level": None,
        "timeout_ms": None,
        "disabled": False,
        "trust": False,
    }
    if "--" in raw:
        marker = raw.index("--")
        prefix, command = raw[:marker], raw[marker + 1 :]
    else:
        prefix, command = [], raw

    idx = 0
    while idx < len(prefix):
        token = prefix[idx]
        if token == "--env" and idx + 1 < len(prefix):
            options["env"].append(prefix[idx + 1])
            idx += 2
            continue
        if token == "--trust-level" and idx + 1 < len(prefix):
            options["trust_level"] = prefix[idx + 1]
            idx += 2
            continue
        if token == "--timeout-ms" and idx + 1 < len(prefix):
            options["timeout_ms"] = int(prefix[idx + 1])
            idx += 2
            continue
        if token == "--disabled":
            options["disabled"] = True
            idx += 1
            continue
        if token == "--trust":
            options["trust"] = True
            idx += 1
            continue
        command = prefix[idx:] + command
        break
    return options, [str(part) for part in command]


def _parse_env(items: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"invalid --env value {item!r}; expected KEY=VALUE")
        key, value = item.split("=", 1)
        if key:
            env[key] = value
    return env


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _serve_mcp(args: argparse.Namespace) -> int:
    """启动 MCP Server（stdio 或 SSE 模式）.

    用法::

        # Stdio 模式（Claude Desktop 配置）
        echo-agent mcp serve --stdio

        # SSE 模式（远程连接）
        echo-agent mcp serve --host 0.0.0.0 --port 8766
    """
    import asyncio
    import logging

    stdio = getattr(args, "stdio", False)
    host = getattr(args, "host", "0.0.0.0")  # nosec B104 — CLI default; operator overrides via --host
    port = getattr(args, "port", 8766)

    # 配置日志（stdio 模式下日志写 stderr，避免干扰 JSON-RPC）
    log_level = getattr(args, "log_level", "warning")
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.WARNING),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    if stdio:
        # Stdio 模式：不需要 coordinator，独立运行
        asyncio.run(_run_stdio_server())
        return 0
    # SSE 模式：启动 coordinator + dashboard（含 SSE 端点）
    return asyncio.run(_run_sse_server(host, port))


async def _run_stdio_server() -> None:
    """运行 stdio 模式的 MCP server."""
    from runtime.tentacle.mobile.mcp_server import serve_stdio

    # stdio 模式下不连接 coordinator，仅暴露工具定义
    # 实际调用时如果没有设备在线会返回错误信息
    await serve_stdio(coordinator=None)


def _is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        # Unknown hostnames may resolve to a routable interface. Treat them as
        # network-facing so standalone MCP never disables auth on assumption.
        return False


async def _run_sse_server(host: str, port: int) -> int:
    """运行 SSE 模式的 MCP server（集成到 Dashboard）."""
    try:
        from runtime.tentacle.coordinator import TentacleCoordinator
    except ImportError as e:
        print(f"Error: tentacle module not available: {e}", file=sys.stderr)
        return 2

    auth_token = (os.environ.get("ECHO_TENTACLE_TOKEN") or "").strip() or None
    loopback = _is_loopback_host(host)
    if not loopback and auth_token is None:
        print(
            "Error: refusing to expose Tentacle MCP SSE without authentication; "
            "set ECHO_TENTACLE_TOKEN or bind --host 127.0.0.1",
            file=sys.stderr,
        )
        return 2

    # The CLI owns both the device WebSocket and the dashboard/MCP HTTP
    # listener. Keep them on the requested interface. Local loopback without a
    # token is the explicit development mode; every network-facing listener
    # reuses the pairing token as its Bearer/API-key credential.
    coordinator = TentacleCoordinator(
        host=host,
        port=port - 1,  # WebSocket 端口比 dashboard 少 1
        dashboard_port=port,
        dashboard_host=host,
        mcp_server=True,
        auth_token=auth_token,
        dashboard_require_auth=auth_token is not None,
    )

    print(f"Starting Tentacle MCP Server (SSE mode) at http://{host}:{port}")
    print(f"  MCP SSE endpoint: http://{host}:{port}/api/tentacle/mcp/sse")
    print(f"  WebSocket port: {port - 1}")
    print()
    print("MCP 客户端配置（网关开启账号鉴权时，需在配置里带上账号登录 Token）:")
    print(
        json.dumps(
            {
                "mcpServers": {
                    "echo-tentacle": {
                        "url": f"http://localhost:{port}/api/tentacle/mcp/sse",
                        "headers": {
                            "Authorization": "Bearer <账号登录后获取的 Token>",
                        },
                    }
                }
            },
            indent=2,
        )
    )

    await coordinator.start()

    # 保持运行
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):  # expected · normal shutdown signal
        pass
    finally:
        await coordinator.stop()
    return 0
