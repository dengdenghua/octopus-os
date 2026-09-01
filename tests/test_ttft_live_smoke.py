"""Live TTFT / streaming-UX gate: real server, real model, real WS timeline.

Opt-in (``ECHO_TTFT_LIVE_SMOKE=1``) because it boots a full server
subprocess and spends real provider quota — same opt-in philosophy as
``tests/test_openai_compat_provider_smoke.py``. Skips cleanly when the
live config (default ``config.local.yaml``) or the flag is absent, so CI
without credentials is unaffected.

Contracts asserted (see ``docs/ttft-acceptance-checklist.md``):
- a reasoning/thinking block appears before every tool row;
- long answers stream progressively, not one end-of-round dump;
- no ``Update:``/``Progress:`` label leaks into visible text.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.request
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.ttft_smoke import check, run_turn

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("ECHO_TTFT_LIVE_SMOKE") != "1",
        reason="set ECHO_TTFT_LIVE_SMOKE=1 with a live config to run the TTFT gate",
    ),
    # Live turns routinely run minutes (model latency + tool execution);
    # the repo-wide 60s default would kill a healthy run mid-stream.
    pytest.mark.timeout(900),
]

ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path(os.environ.get("ECHO_TTFT_SMOKE_CONFIG", "config.local.yaml"))
MODEL = os.environ.get("ECHO_TTFT_SMOKE_MODEL", "kimi-k3")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(port: int, proc: subprocess.Popen, timeout_s: float = 45.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early (code {proc.returncode})")
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/openapi.json", timeout=2) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"server on :{port} did not become ready in {timeout_s}s")


@pytest.fixture(scope="module")
def live_server():
    if not (ROOT / CONFIG).exists():
        pytest.skip(f"live config {CONFIG} not present")
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "runtime",
            "serve",
            "--config",
            str(CONFIG),
            "--port",
            str(port),
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, proc)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _run(prompt: str, port: int, *, react: bool = False) -> dict:
    args = Namespace(
        prompt=prompt,
        model=MODEL,
        port=port,
        react=react,
        timeout=240,
        do_assert=True,
        out="",
    )
    _tl, stats = asyncio.run(run_turn(args))
    return stats


def test_chat_turn_streaming_contracts(live_server: int) -> None:
    stats = _run("用一句话介绍上海", live_server)
    failures = check(stats)
    assert not failures, f"streaming contract failures: {failures}"


def test_tool_turn_streaming_contracts(live_server: int) -> None:
    # The model decides whether a prompt warrants tools; retry once with a
    # more insistent prompt before declaring the tool path untestable.
    stats = None
    for prompt in (
        "联网搜索一条最近的科技新闻，一句话总结并注明来源",
        "必须使用联网搜索工具：查一条本周的科技新闻并给出来源链接",
    ):
        stats = _run(prompt, live_server)
        failures = check(stats)
        assert not failures, f"streaming contract failures: {failures}"
        if stats["tool_rows"] >= 1:
            break
    else:
        pytest.skip(
            "model declined tool use in both attempts — the "
            "reasoning-before-tool assertion was vacuous this run"
        )

