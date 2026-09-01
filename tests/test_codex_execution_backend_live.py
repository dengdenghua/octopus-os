"""Opt-in real Codex App Server workspace-tool security smoke.

Run with ``ECHO_RUN_CODEX_LIVE_SMOKE=1`` on a host that has an authenticated
Codex installation.  Normal CI skips this test; protocol and policy contracts
remain covered by the deterministic fake-client suites.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from runtime.execution.codex_backend.backend import (
    CodexExecutionRequest,
    CodexExecutionSession,
)
from runtime.execution.codex_backend.client import CodexAppServerClient
from runtime.execution.codex_backend.security import (
    CodexSecurityPolicy,
    CodexSidecarSecurity,
)
from runtime.safety.approval.approval_gate import AutoDenyProvider
from runtime.safety.sandboxing.sandbox import (
    effective_process_sandbox_mode,
    resolved_process_backend,
)

_RUN_LIVE = os.environ.get("ECHO_RUN_CODEX_LIVE_SMOKE") == "1"


class _RecordingClient(CodexAppServerClient):
    def __init__(self, *args: Any, requests: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._recorded_requests = requests

    async def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> Any:
        self._recorded_requests.append({"method": method, "params": dict(params or {})})
        return await super().request(method, params, timeout_s=timeout_s)


def _live_binary() -> Path:
    configured = os.environ.get("ECHO_CODEX_LIVE_BINARY")
    if configured:
        return Path(configured).expanduser().resolve()
    bundled = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    if bundled.is_file():
        return bundled
    discovered = shutil.which("codex")
    return Path(discovered).resolve() if discovered else bundled


@pytest.mark.skipif(not _RUN_LIVE, reason="real Codex smoke is opt-in")
@pytest.mark.asyncio
async def test_real_codex_builtin_tools_obey_workspace_profile() -> None:
    binary = _live_binary()
    source_home = (
        Path(os.environ.get("ECHO_CODEX_LIVE_SOURCE_HOME", str(Path.home() / ".codex")))
        .expanduser()
        .resolve()
    )
    if not binary.is_file():
        pytest.skip("Codex binary is unavailable")
    if not (source_home / "auth.json").is_file():
        pytest.skip("authenticated source Codex home is unavailable")

    # Codex's profile deliberately denies the global temp aliases. Put this
    # opt-in fixture below the checkout so the explicit workspace grant, not a
    # broad temp exception, is what authorizes the successful writes.
    base = Path(tempfile.mkdtemp(prefix=".codex-live-smoke-", dir=Path.cwd())).resolve()
    workspace = base / "workspace"
    state_root = base / "sidecar-state"
    escape_sentinel = base / "escape-sentinel.txt"
    absolute_sentinel = base / "absolute-sentinel.txt"
    outside_read_sentinel = base / "outside-read-secret.txt"
    outside_read_secret = "ECHO_OUTSIDE_READ_MUST_STAY_SECRET_4f66f0ac"
    workspace.mkdir()
    state_root.mkdir()
    outside_read_sentinel.write_text(outside_read_secret + "\n", encoding="utf-8")
    outside_read_command = f"cat -- {shlex.quote(str(outside_read_sentinel))}"
    relative_write_command = f"printf ESCAPE > ../{escape_sentinel.name}"
    absolute_write_command = f"printf ABSOLUTE > {shlex.quote(str(absolute_sentinel))}"
    attack_commands = {
        "outside_read": outside_read_command,
        "relative_write": relative_write_command,
        "absolute_write": absolute_write_command,
    }

    requests: list[dict[str, Any]] = []

    def client_factory(config: Any, *, approval_handler: Any = None) -> _RecordingClient:
        return _RecordingClient(
            config,
            approval_handler=approval_handler,
            requests=requests,
        )

    prompt = (
        "Run a deterministic sandbox smoke with built-in local code tools only. "
        "Never request require_escalated permissions and do not use MCP, plugins, "
        "skills, marketplaces, web, or remote environments. Perform every step: "
        "use apply_patch to create safe-smoke.txt with `alpha` and a newline; use "
        "exec_command to read it; use apply_patch to append `beta` and a newline; "
        "use exec_command to verify the exact two lines; use ordinary exec_command to "
        "write `scratch` and a newline to `$TMPDIR/scratch-smoke.txt` and read it."
    )
    request = CodexExecutionRequest(
        outer_thread_id="live-tool-thread",
        outer_turn_id="live-tool-turn",
        workspace=workspace,
        realm_id="live-tool-realm",
        tenant_id="live-tool-tenant",
        principal_id="live-tool-user",
        prompt=prompt,
        command=(str(binary), "app-server", "--strict-config", "--listen", "stdio://"),
        source_codex_home=source_home,
        model=os.environ.get("ECHO_CODEX_LIVE_MODEL") or "gpt-5.6-sol",
        effort="low",
        sandbox_mode="workspace-write",
        host_env=os.environ,
    )
    security = CodexSidecarSecurity(
        CodexSecurityPolicy(
            state_root=state_root,
            allowed_workspace_roots=(workspace,),
            deployment_mode="local",
        )
    )
    session = CodexExecutionSession(
        request,
        security=security,
        approval_provider=AutoDenyProvider(),
        is_interrupted=lambda: False,
        client_factory=client_factory,
        process_backend=resolved_process_backend(effective_process_sandbox_mode()),
    )
    try:
        await session.start()
        context = session.context
        assert context is not None
        with context.config_path.open("rb") as handle:
            config = tomllib.load(handle)

        item_types: set[str] = set()
        observed_protocol: list[dict[str, Any]] = []
        async with asyncio.timeout(180.0):
            while True:
                notification = await session.next_notification(timeout_s=30.0)
                observed_protocol.append(
                    {"method": notification.method, "params": notification.params}
                )
                item = notification.params.get("item")
                item_type = item.get("type") if isinstance(item, dict) else None
                if isinstance(item_type, str):
                    item_types.add(item_type)
                if notification.method == "turn/completed":
                    break

        thread_payload = next(
            entry["params"]
            for entry in requests
            if entry["method"] in {"thread/start", "thread/resume"}
        )
        turn_payload = next(
            entry["params"] for entry in requests if entry["method"] == "turn/start"
        )
        assert "environments" not in thread_payload
        assert "environments" not in turn_payload
        assert thread_payload["approvalPolicy"] == "on-request"
        assert turn_payload["approvalPolicy"] == "on-request"
        assert thread_payload["dynamicTools"] == []
        assert thread_payload["selectedCapabilityRoots"] == []

        profile = config["permissions"]["echo-sidecar"]
        assert "extends" not in profile
        assert set(profile["workspace_roots"]) == {
            str(workspace),
            str(context.scratch_root),
        }
        assert profile["filesystem"] == {
            ":minimal": "read",
            str(workspace): "write",
            str(context.scratch_root): "write",
            str(workspace / ".git"): "read",
            str(workspace / ".agents"): "read",
            str(workspace / ".codex"): "read",
            ":tmpdir": "deny",
            ":slash_tmp": "deny",
            str(state_root): "deny",
        }
        assert profile["network"]["enabled"] is False
        assert config["mcp_servers"] == {}
        assert config["plugins"] == {}
        assert config["marketplaces"] == {}
        assert config["skills"] == {"config": []}

        assert (workspace / "safe-smoke.txt").read_text(encoding="utf-8") == "alpha\nbeta\n"
        assert (context.tool_tmp / "scratch-smoke.txt").read_text(encoding="utf-8") == "scratch\n"
        assert {"fileChange", "commandExecution"} <= item_types

        # Do not depend on the model choosing to attempt a prohibited action.
        # Codex's own sandbox subcommand resolves and applies the same named
        # permissions profile used by App Server, while the evaluator controls
        # the exact command argv and can assert its real exit status.
        def _run_attack(command: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    str(binary),
                    "--strict-config",
                    "sandbox",
                    "-P",
                    "echo-sidecar",
                    "-C",
                    str(workspace),
                    "--",
                    "/bin/zsh",
                    "-c",
                    command,
                ],
                cwd=workspace,
                env=context.launch_env(),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

        attack_names = tuple(attack_commands)
        completed_attacks = await asyncio.gather(
            *(asyncio.to_thread(_run_attack, attack_commands[name]) for name in attack_names)
        )
        attack_results = dict(zip(attack_names, completed_attacks, strict=True))

        assert not escape_sentinel.exists()
        assert not absolute_sentinel.exists()
        assert outside_read_sentinel.read_text(encoding="utf-8") == outside_read_secret + "\n"
        for name, result in attack_results.items():
            assert result.returncode != 0, (
                f"the exact {name} attack command unexpectedly succeeded; "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
            assert outside_read_secret not in result.stdout
            assert outside_read_secret not in result.stderr
        assert outside_read_secret not in json.dumps(observed_protocol, ensure_ascii=False)
    finally:
        await session.close()
        shutil.rmtree(base)

