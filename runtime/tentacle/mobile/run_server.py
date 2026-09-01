"""Echo Tentacle 联调启动脚本.

启动 WebSocket Server + Dashboard，等待 Android 设备连接。

用法::

    python -m runtime.tentacle.mobile.run_server
    python -m runtime.tentacle.mobile.run_server --host 0.0.0.0 --port 8765 --dashboard-port 8766
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from runtime.tentacle.coordinator import TentacleCoordinator


def main() -> None:
    parser = argparse.ArgumentParser(description="Echo Tentacle Server")
    parser.add_argument("--host", default="0.0.0.0", help="WebSocket 监听地址")  # nosec B104 — CLI default; operator overrides via --host
    parser.add_argument("--port", type=int, default=8765, help="WebSocket 监听端口")
    parser.add_argument(
        "--dashboard-host",
        default="127.0.0.1",
        help="Dashboard 监听地址（默认仅本机；局域网访问需显式传 0.0.0.0）",
    )
    parser.add_argument("--dashboard-port", type=int, default=8766, help="Dashboard HTTP 端口")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    coordinator = TentacleCoordinator(
        host=args.host,
        port=args.port,
        dashboard_port=args.dashboard_port,
        dashboard_host=args.dashboard_host,
    )

    print(f"""
╔══════════════════════════════════════════════════╗
║          🐙 Echo Tentacle Server              ║
╠══════════════════════════════════════════════════╣
║  WebSocket:  ws://{args.host}:{args.port}              ║
║  Dashboard:  http://{args.dashboard_host}:{args.dashboard_port}
║  MCP SSE:    http://{args.dashboard_host}:{args.dashboard_port}/api/tentacle/mcp/sse ║
╠══════════════════════════════════════════════════╣
║  等待 Android 设备连接...                         ║
║  App 设置中输入: ws://<本机IP>:{args.port}          ║
╚══════════════════════════════════════════════════╝
""")

    asyncio.run(coordinator.start())


if __name__ == "__main__":
    main()
