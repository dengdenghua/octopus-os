"""认证编排器 — 连接/断开/状态 + auth 头/环境变量注入。

对齐 WorkBuddy 连接器 / Codex connector_* 的认证编排:
  - CLI 型连接器: 执行 cli.json 的 init / auth / unAuth / status 命令
  - token / oauth 型: 通过网关接口收 token,加密存入 CredentialStore
  - 注入: 按 connector 的 auth_injection_rules 生成 Authorization / 自定义头,
    或按 cli.json authEnv 生成环境变量

注意: CLI auth 命令会弹浏览器/起交互进程;网关调用 connect 时默认以
``detached`` 方式(仅输出提示)执行,除非显式 ``run=true`` 同步跑。
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import re
import secrets
import shlex
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from runtime.platform.connectors import cli_lifecycle
from runtime.platform.connectors._token_refresher import (
    ConnectorTokenRefresher,
    RefreshCleanupRequiredError,
    _RefreshCancellation,
)
from runtime.platform.connectors.connector_registry import ConnectorDefinition
from runtime.platform.connectors.credential_store import CredentialStore

# 允许从连接器注入的 header / env 白名单(防任意注入)
_ALLOWED_HEADERS = {
    "authorization",
    "x-oneid-access-token",
    "x-auth-token",
    "x-api-key",
    "cookie",
}
_ALLOWED_ENV_PREFIX = ("MCP_", "CONNECTOR_", "LARK_", "DWS_", "WECOM_", "WESTOCK_", "TENCENT_")


def _platform_key() -> str:
    sys = platform.system().lower()
    if sys == "darwin":
        return "darwin"
    if sys == "windows":
        return "win32"
    return "linux"


# ── 设备流会话(WorkBuddy ``authDeviceFlow``)──────────────
# CLI 登录命令往往是阻塞的设备流:后台跑 auth 命令 → 解析 verification_uri /
# user_code → 前端弹窗 + 轮询 status。登录成功或超时后终止后台进程。


@dataclass
class DeviceFlowSession:
    connector_id: str
    proc: Any  # subprocess.Popen | None
    verification_uri: str
    user_code: str
    expires_in: int
    started_at: float
    flow_id: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    flow_key: str = ""
    opened: bool = False
    watchdog_stop: threading.Event = field(default_factory=threading.Event, repr=False)


_device_flows: dict[str, DeviceFlowSession] = {}
_device_lock = threading.Lock()
_device_flow_guards: dict[str, Any] = {}
_DEVICE_PROCESS_WAIT_SECONDS = 3.0
_LifecycleResult = TypeVar("_LifecycleResult")


def _device_flow_guard(connector_id: str) -> Any:
    """Return the stable per-connector lifecycle lock.

    The global lock protects only the tiny guard/session maps. Slow operations
    such as process startup and ``wait`` are serialized per connector without
    blocking an unrelated connector's auth flow.
    """

    with _device_lock:
        guard = _device_flow_guards.get(connector_id)
        if guard is None:
            guard = threading.RLock()
            _device_flow_guards[connector_id] = guard
        return guard


def _device_flow_end_reason(
    sess: DeviceFlowSession,
    *,
    max_ttl: float | None = None,
) -> str | None:
    ttl = float(sess.expires_in)
    if max_ttl is not None:
        ttl = min(ttl, max_ttl)
    if time.time() - sess.started_at >= max(0.0, ttl):
        return "expired"
    if sess.proc is None:
        return "auth_process_exited"
    try:
        return "auth_process_exited" if sess.proc.poll() is not None else None
    except Exception:  # noqa: BLE001 - an unreadable child handle is not reusable
        return "auth_process_exited"


def _reap_device_process(proc: Any) -> None:
    """Terminate a live auth child and always collect its exit status.

    ``terminate`` without ``wait`` leaves zombies on POSIX. A child that does
    not honor termination is killed after a short bounded wait. Cleanup stays
    outside ``_device_lock`` at every call site.
    """

    if proc is None:
        return
    try:
        alive = proc.poll() is None
    except Exception:  # noqa: BLE001 - still attempt cleanup for opaque handles
        alive = True
    if alive:
        with contextlib.suppress(Exception):
            proc.terminate()
    try:
        proc.wait(timeout=_DEVICE_PROCESS_WAIT_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception:  # noqa: BLE001 - cleanup is best effort after handle failure
        return
    with contextlib.suppress(Exception):
        proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(timeout=_DEVICE_PROCESS_WAIT_SECONDS)


def _watch_device_flow_expiry(
    connector_key: str,
    sess: DeviceFlowSession,
    max_ttl: float,
) -> None:
    """Reap one canonical auth child at TTL even when nobody polls status."""

    ttl = max(0.0, min(float(sess.expires_in), float(max_ttl)))
    while True:
        remaining = ttl - (time.time() - sess.started_at)
        if remaining > 0 and sess.watchdog_stop.wait(remaining):
            return
        guard = _device_flow_guard(connector_key)
        with guard:
            with _device_lock:
                if _device_flows.get(connector_key) is not sess:
                    return
                if _device_flow_end_reason(sess, max_ttl=max_ttl) != "expired":
                    continue
                _device_flows.pop(connector_key, None)
            sess.watchdog_stop.set()
            _reap_device_process(sess.proc)
            return


def _pick_platform(cmd_map: Any) -> str | None:
    if not isinstance(cmd_map, dict):
        return None
    return cmd_map.get(_platform_key()) or cmd_map.get("darwin") or cmd_map.get("linux")


class AuthOrchestrator:
    """连接器认证编排:负责把「连上外部服务」这件事做掉。"""

    def __init__(
        self,
        *,
        credentials: CredentialStore | None = None,
        auth_injection_rules: list[dict[str, Any]] | None = None,
        refresher: ConnectorTokenRefresher | None = None,
    ) -> None:
        self._credentials = credentials or CredentialStore()
        self._rules = auth_injection_rules or []
        self._refresher = refresher or ConnectorTokenRefresher(self._credentials)

    def _device_flow_key(self, connector_id: str) -> str:
        from runtime.platform.capabilities.tenant_context import (
            current_capability_scope,
        )

        if current_capability_scope() is None:
            return connector_id
        return f"{self._credentials.storage_identity}\0{connector_id}"

    def run_connector_lifecycle(
        self,
        conn: ConnectorDefinition,
        operation: Callable[[], _LifecycleResult],
        *,
        cancel_device_flow: bool = False,
    ) -> _LifecycleResult:
        """Run one registry/auth transition under the connector lifecycle guard.

        Registry install state and auth children otherwise live in separate
        stores. Serializing their transition prevents an uninstall from
        cancelling generation A, publishing ``installed=false``, and then
        losing a race to a pre-checked connect that spawns generation B.
        The guard is re-entrant because ``operation`` may call ``connect`` or
        ``start_device_flow``, which use this same per-connector boundary.
        """

        flow_key = self._device_flow_key(conn.id)
        guard = _device_flow_guard(flow_key)
        cancellation: _RefreshCancellation | None = None
        try:
            with guard, self._credentials.connector_lifecycle(conn.id):
                if cancel_device_flow:
                    self._cancel_device_flow_locked(flow_key)
                    # Revocation is durable before any wait: another worker's
                    # supervisor observes the generation fence and reaps its
                    # child, while refreshed credentials can no longer return.
                    self._credentials.clear_connector(conn.id)
                    cancellation = self._refresher._cancel_under_lifecycle(conn.id)
                    self._refresher._wait_for_lease(conn.id, cancellation.lease)
                return operation()
        finally:
            if cancellation is not None:
                self._refresher._wait_for_entries(conn.id, cancellation.entries)

    # ── 状态 ──────────────────────────────────────────────────
    def status(self, conn: ConnectorDefinition) -> dict[str, Any]:
        # CLI status may block long enough for the user to cancel the current
        # flow and start a replacement. Bind this observation to the session
        # generation that existed when status began; its late result must not
        # finish a newer canonical flow.
        flow_key = self._device_flow_key(conn.id)
        with _device_lock:
            expected_device_flow = _device_flows.get(flow_key)
        stored = self._credentials.list_secrets(conn.id)
        authed = self._credentials.has_credentials(conn.id)
        detail: dict[str, Any] = {
            "connector_id": conn.id,
            "auth_mode": conn.auth_mode,
            "connected": authed,
            "has_token": "access_token" in stored,
            "stored_keys": stored,
        }
        # CLI 型:如果存了 access_token 认为已连;否则(可选)跑 status 命令
        from runtime.platform.capabilities.tenant_context import (
            current_capability_scope,
        )

        shared_cli = bool(conn.cli and current_capability_scope() is not None)
        if shared_cli:
            detail["cli_isolation"] = "host_cli_disabled_for_shared_principal"
        if not authed and conn.cli and "status" in conn.cli and not shared_cli:
            code, stdout = self._run_cli(conn, conn.cli.get("status"))
            status_output = stdout or ""
            detail["cli_status"] = {
                "exit_code": code,
                "output": status_output[:500],
                "connected": code == 0 and self._match_status(conn, status_output),
            }
        # 设备流:status 确认已登录(或超时) → 终止并清理后台 auth 进程
        cli_connected = bool((detail.get("cli_status") or {}).get("connected")) or bool(
            detail.get("has_token")
        )
        self._finish_device_flow(conn, cli_connected, expected_device_flow)
        return detail

    def connect(
        self,
        conn: ConnectorDefinition,
        *,
        tokens: dict[str, str] | None = None,
        run_cli: bool = False,
    ) -> dict[str, Any]:
        """编排连接流程。

        - tokens 提供时直接加密存储(token/oauth 型)。
        - 否则 CLI 型执行 cli.json auth 命令(run_cli=True 时同步跑并校验)。

        返回统一带 ``next_action`` 服务端决策(对齐 WorkBuddy connect 编排):
          - ``"connected"``     已连接(凭据已存/无需认证)
          - ``"redirect"``      需要用户跳官网授权(设备流,已拿到 verification_uri)
          - ``"device_code"``   需要用户输入 user_code(设备流,只解析到码)
          - ``"poll"``          授权已发起,等待用户完成(前端轮询 status)
          - ``"form"``          需要用户填 token / API Key
          - ``"cli_command"``   需要用户在终端执行 auth 命令
          - ``"error"``         连接失败
        """
        if conn.auth_mode == "none":
            return {"connected": True, "next_action": "connected", "message": "该连接器无需认证。"}

        if tokens:
            flow_key = self._device_flow_key(conn.id)
            guard = _device_flow_guard(flow_key)
            with guard, self._credentials.connector_lifecycle(conn.id):
                self._cancel_device_flow_locked(flow_key)
                generation = self._credentials.begin_auth_generation(conn.id, tokens)
                result = {
                    "connected": True,
                    "next_action": "connected",
                    "connector_id": conn.id,
                    "stored_keys": self._credentials.list_secrets(conn.id),
                    "message": f"已保存 {len(tokens)} 项凭据(AES-256-GCM 加密)。",
                }
                # token 型连接器若声明了 tokenRefresh,注册自动刷新
                if self._refresher.schedule(
                    conn,
                    initial_token=tokens.get("access_token"),
                    generation=generation,
                ):
                    result["token_refresh"] = "scheduled"
                return result

        if conn.cli and "auth" in conn.cli:
            from runtime.platform.capabilities.tenant_context import (
                current_capability_scope,
            )

            if current_capability_scope() is not None:
                raise ValueError("共享部署暂不允许使用主机级 CLI 登录；请改用令牌或 OAuth 连接器。")
            cmd = _pick_platform(conn.cli.get("auth"))
            if not cmd:
                return {
                    "connected": False,
                    "next_action": "error",
                    "message": "当前平台无 auth 命令。",
                }
            if run_cli:
                # 设备流:后台启动登录,返回 verification_uri / user_code 供前端
                # 弹窗 + 轮询 status,不再阻塞等 CLI 退出。
                if conn.cli.get("authDeviceFlow"):
                    return self.start_device_flow(conn)
                code, stdout = self._run_cli(conn, conn.cli.get("auth"), timeout=600)
                connected = code == 0 and self._match_status(conn, stdout or "")
                return {
                    "connected": connected,
                    "next_action": "connected" if connected else "error",
                    "exit_code": code,
                    "output": (stdout or "")[:500],
                    "message": "CLI 登录完成" if connected else "CLI 登录未确认",
                }
            return {
                "connected": False,
                "next_action": "cli_command",
                "message": f"请在终端执行: {cmd}\n或带 tokens 调用本接口。",
                "command": cmd,
            }

        return {
            "connected": False,
            "next_action": "form",
            "message": f"不支持 {conn.auth_mode} 自动连接,请提供 tokens。",
        }

    def disconnect(self, conn: ConnectorDefinition) -> dict[str, Any]:
        # A disconnect is terminal for any in-flight login attempt. Reap the
        # auth child before running the connector's own logout command so the
        # two CLI processes cannot race over the same credential state.
        flow_key = self._device_flow_key(conn.id)
        guard = _device_flow_guard(flow_key)
        cancellation: _RefreshCancellation | None = None
        try:
            with guard, self._credentials.connector_lifecycle(conn.id):
                self._cancel_device_flow_locked(flow_key)
                removed = self._credentials.clear_connector(conn.id)
                cancellation = self._refresher._cancel_under_lifecycle(conn.id)
                self._refresher._wait_for_lease(conn.id, cancellation.lease)
                cli_out: str | None = None
                from runtime.platform.capabilities.tenant_context import (
                    current_capability_scope,
                )

                if conn.cli and "unAuth" in conn.cli and current_capability_scope() is None:
                    cmd = _pick_platform(conn.cli.get("unAuth"))
                    if cmd:
                        _code, stdout = self._run_cli(conn, conn.cli.get("unAuth"))
                        cli_out = (stdout or "")[:300]
                return {
                    "disconnected": True,
                    "credentials_removed": removed,
                    "cli_output": cli_out,
                }
        finally:
            if cancellation is not None:
                self._refresher._wait_for_entries(conn.id, cancellation.entries)

    # ── 设备流(WorkBuddy ``authDeviceFlow``)──────────────────
    def start_device_flow(self, conn: ConnectorDefinition) -> dict[str, Any]:
        """后台启动 CLI 设备流登录,解析 authorization URI + user_code。

        返回 ``{"connected": False, "device_flow": {...}}``,前端弹窗打开
        ``verification_uri``(URI 通常已内嵌 user_code)并轮询 ``status``。
        """
        spec = (conn.cli or {}).get("authDeviceFlow") or {}
        default_ttl = max(0.0, float(spec.get("defaultExpiresInSeconds", 240)))
        cmd = cli_lifecycle.resolve_cmd((conn.cli or {}).get("auth"))
        if not cmd:
            return {
                "connected": False,
                "next_action": "error",
                "message": "当前平台无 auth 命令。",
            }
        flow_key = self._device_flow_key(conn.id)
        guard = _device_flow_guard(flow_key)
        with guard:
            env = self.resolve_env(conn)
            with _device_lock:
                existing = _device_flows.get(flow_key)
                ended = (
                    _device_flow_end_reason(existing, max_ttl=default_ttl)
                    if existing is not None
                    else None
                )
                if existing is not None and ended is None:
                    return self._device_flow_payload(conn, existing)
                if existing is not None:
                    _device_flows.pop(flow_key, None)

            # An expired/dead session must be fully reaped before its
            # replacement is spawned. The per-connector guard keeps a second
            # caller from racing through this transition.
            if existing is not None:
                existing.watchdog_stop.set()
                _reap_device_process(existing.proc)

            proc: Any = None
            sess: DeviceFlowSession | None = None
            try:
                proc = subprocess.Popen(
                    shlex.split(cmd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env={**os.environ, **env},
                )
                sess = DeviceFlowSession(
                    connector_id=conn.id,
                    proc=proc,
                    verification_uri="",
                    user_code="",
                    expires_in=max(1, int(default_ttl)),
                    started_at=time.time(),
                    flow_key=flow_key,
                )
                with _device_lock:
                    # All production lifecycle writes use ``guard``. Keep this
                    # defensive check so an injected/legacy active session is
                    # never overwritten by a just-spawned child.
                    canonical = _device_flows.get(flow_key)
                    if canonical is None:
                        _device_flows[flow_key] = sess
                if canonical is not None:
                    _reap_device_process(proc)
                    return self._device_flow_payload(conn, canonical)
                threading.Thread(
                    target=self._drain_device_output,
                    args=(conn, sess),
                    daemon=True,
                ).start()
                threading.Thread(
                    target=_watch_device_flow_expiry,
                    args=(flow_key, sess, default_ttl),
                    daemon=True,
                ).start()
            except Exception as exc:  # noqa: BLE001
                if proc is not None:
                    with _device_lock:
                        if sess is not None and _device_flows.get(flow_key) is sess:
                            _device_flows.pop(flow_key, None)
                    if sess is not None:
                        sess.watchdog_stop.set()
                    _reap_device_process(proc)
                return {
                    "connected": False,
                    "next_action": "error",
                    "message": f"设备流启动失败: {exc}",
                }

        # 等最多 6s 解析出授权 URI(命令行启动 + 首行输出)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and not sess.verification_uri:
            if _device_flow_end_reason(sess) is not None:
                break
            time.sleep(0.2)
        with _device_lock:
            if _device_flows.get(flow_key) is not sess:
                return {
                    "connected": False,
                    "next_action": "error",
                    "message": "设备流启动已结束,请重试。",
                }
        return self._device_flow_payload(conn, sess)

    def _drain_device_output(self, conn: ConnectorDefinition, sess: DeviceFlowSession) -> None:
        """持续读取 auth 进程输出:解析授权地址/用户码,并保持 stdout 不被写满。

        两个必须遵守的约束(都踩过):
        1) 解析到授权地址后**不能 break**。一旦停止读 stdout,CLI 后续输出会把
           PIPE 写满并阻塞在 write 上,用户在官网授权成功后的回调处理不了。
        2) 解析到授权地址后**不能终止进程**。设备流的语义就是"CLI 挂在后台等用户
           去官网完成登录授权",进程必须活到 status 确认登录成功(或超时),
           由 _finish_device_flow 统一回收。只有连授权地址都没解析出来
           (CLI 启动/登录异常)才立即终止,避免进程泄漏。
        """
        spec = (conn.cli or {}).get("authDeviceFlow") or {}
        uri_re = re.compile(str(spec.get("uriPattern") or ""))
        code_re = re.compile(str(spec.get("codePattern") or ""))
        try:
            if not sess.proc or not sess.proc.stdout:
                return
            for line in sess.proc.stdout:
                if uri_re.pattern and not sess.verification_uri:
                    m = uri_re.search(line)
                    if m:
                        uri = m.group(1) if m.groups() else m.group(0)
                        if cli_lifecycle.validate_auth_uri(uri, conn):
                            sess.verification_uri = uri
                            # 授权地址已就绪(可交付前端弹窗)→ 进程转入"等授权"态
                            sess.opened = True
                if code_re.pattern and not sess.user_code:
                    m = code_re.search(line)
                    if m:
                        sess.user_code = m.group(1) if m.groups() else m.group(0)
        finally:
            if sess.proc and not sess.verification_uri:
                # 没拿到授权地址 = CLI 启动/登录异常,避免进程泄漏
                flow_key = sess.flow_key or conn.id
                guard = _device_flow_guard(flow_key)
                with guard:
                    with _device_lock:
                        if _device_flows.get(flow_key) is sess:
                            _device_flows.pop(flow_key, None)
                    sess.watchdog_stop.set()
                    _reap_device_process(sess.proc)

    def _device_flow_payload(
        self,
        conn: ConnectorDefinition,
        sess: DeviceFlowSession,
    ) -> dict[str, Any]:
        spec = (conn.cli or {}).get("authDeviceFlow") or {}
        # next_action 决策(对齐 WorkBuddy):有授权 URL → redirect(直接打开);
        # 只有 user_code → device_code(展示码让用户输入);都没有 → poll(等待 CLI 输出)。
        if sess.verification_uri:
            next_action = "redirect"
        elif sess.user_code:
            next_action = "device_code"
        else:
            next_action = "poll"
        return {
            "connected": False,
            "next_action": next_action,
            "auth_mode": conn.auth_mode,
            "device_flow": {
                "flow_id": sess.flow_id,
                "connector_id": conn.id,
                "verification_uri": sess.verification_uri,
                "user_code": sess.user_code,
                "expires_in": sess.expires_in,
                "code_embedded_in_uri": bool(spec.get("codeEmbeddedInUri")),
                "message": (
                    "已启动设备流登录,请在打开的页面完成授权。"
                    if sess.verification_uri
                    else "设备流已启动,正在等待授权地址…"
                ),
            },
        }

    def _finish_device_flow(
        self,
        conn: ConnectorDefinition,
        connected: bool,
        expected_session: DeviceFlowSession | None,
    ) -> None:
        """Finish only the device-flow generation observed by ``status``."""
        if expected_session is None:
            return
        flow_key = expected_session.flow_key or self._device_flow_key(conn.id)
        guard = _device_flow_guard(flow_key)
        with guard:
            with _device_lock:
                sess = _device_flows.get(flow_key)
                if sess is not expected_session:
                    return
                ended = _device_flow_end_reason(sess)
                if not (connected or ended is not None):
                    return
                _device_flows.pop(flow_key, None)
            sess.watchdog_stop.set()
            _reap_device_process(sess.proc)

    def device_flow_status(self, conn: ConnectorDefinition) -> dict[str, Any]:
        """返回活跃设备流信息(未启动/已结束 → device_flow=None)。

        auth 进程可能自行退出(CLI 自带超时、用户在官网取消、登录失败),此时会话必须
        判定为结束——否则前端会一直轮询到 ``active:true`` 和一个早已失效的授权链接。
        """
        flow_key = self._device_flow_key(conn.id)
        guard = _device_flow_guard(flow_key)
        with guard:
            with _device_lock:
                sess = _device_flows.get(flow_key)
                if sess is None:
                    return {"connector_id": conn.id, "active": False, "device_flow": None}
                ended = _device_flow_end_reason(sess)
                if ended is not None:
                    _device_flows.pop(flow_key, None)
                else:
                    return {
                        "connector_id": conn.id,
                        "active": True,
                        **self._device_flow_payload(conn, sess),
                    }
            sess.watchdog_stop.set()
            _reap_device_process(sess.proc)
            return {
                "connector_id": conn.id,
                "active": False,
                "device_flow": None,
                "ended_reason": ended,
            }

    def cancel_device_flow(
        self,
        conn: ConnectorDefinition,
        *,
        expected_flow_id: str,
    ) -> dict[str, Any]:
        """只取消客户端观测到的那一代设备流。

        同一 connector 的旧弹窗可能在新弹窗启动后才发出 DELETE。
        ``expected_flow_id`` 把取消绑定到旧会话；失配时幂等返回且不回收新进程。
        """
        flow_key = self._device_flow_key(conn.id)
        guard = _device_flow_guard(flow_key)
        with guard:
            outcome = self._cancel_device_flow_locked(
                flow_key,
                expected_flow_id=expected_flow_id,
            )
        if outcome == "cancelled":
            return {"cancelled": True, "connector_id": conn.id}
        return {
            "cancelled": False,
            "connector_id": conn.id,
            "reason": outcome,
        }

    @staticmethod
    def _cancel_device_flow_locked(
        connector_id: str,
        *,
        expected_flow_id: str | None = None,
    ) -> str:
        """Cancel one flow while its per-connector lifecycle guard is held."""

        with _device_lock:
            sess = _device_flows.get(connector_id)
            if sess is None:
                return "inactive"
            if expected_flow_id is not None and not secrets.compare_digest(
                sess.flow_id,
                expected_flow_id,
            ):
                return "generation_mismatch"
            _device_flows.pop(connector_id, None)
        if sess is not None:
            sess.watchdog_stop.set()
            _reap_device_process(sess.proc)
        return "cancelled"

    # ── 注入 ──────────────────────────────────────────────────
    def resolve_headers(self, conn: ConnectorDefinition) -> dict[str, str]:
        """为 MCP/HTTP 请求生成要注入的 auth 头。"""
        headers: dict[str, str] = {}
        # 0) oneid-token(腾讯统一身份):注入 X-ONEID-ACCESS-TOKEN
        if conn.auth_mode == "oneid-token":
            tok = self._credentials.get_secret(
                conn.id, "oneid_token"
            ) or self._credentials.get_secret(conn.id, "access_token")
            if tok:
                headers["X-ONEID-ACCESS-TOKEN"] = tok
        # 1) connector 自带规则(connectors.json auth_injection_rules)
        for rule in self._rules:
            if conn.id not in rule.get("applies_to_connectors", []):
                continue
            for inject in rule.get("inject", []):
                token_type = inject.get("token_type") or "access_token"
                header = inject.get("header") or ""
                if not header or header.lower() not in _ALLOWED_HEADERS:
                    continue
                token = self._credentials.get_secret(conn.id, token_type)
                if not token:
                    continue
                template = inject.get("value_template") or "${access_token}"
                headers[header] = template.replace("${access_token}", token)
        # 2) 兜底:有 access_token 就挂 Bearer
        if not headers and self._credentials.has_credentials(conn.id):
            token = self._credentials.get_secret(conn.id, "access_token")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def resolve_env(self, conn: ConnectorDefinition) -> dict[str, str]:
        """为 CLI 子进程生成环境变量注入。"""
        env: dict[str, str] = {}
        for key in self._credentials.list_secrets(conn.id):
            if key.startswith(_ALLOWED_ENV_PREFIX) or key in {"access_token", "api_key"}:
                val = self._credentials.get_secret(conn.id, key)
                if val:
                    env[key] = val
        return env

    # ── CLI 辅助 ──────────────────────────────────────────────
    def _run_cli(
        self, conn: ConnectorDefinition, cmd_spec: Any, timeout: int = 120
    ) -> tuple[int, str | None]:
        cmd = _pick_platform(cmd_spec) if isinstance(cmd_spec, dict) else cmd_spec
        if not cmd:
            return -1, None
        env = self.resolve_env(conn)
        try:
            r = subprocess.run(
                shlex.split(cmd),
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, **env},
            )
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except Exception as exc:  # noqa: BLE001
            return -1, str(exc)

    @staticmethod
    def _match_status(conn: ConnectorDefinition, output: str) -> bool:
        status_match = conn.cli.get("statusMatch") or ""
        if status_match and status_match in output:
            return True
        match_json = conn.cli.get("statusMatchJson") or {}
        if match_json:
            try:
                start = output.find("{")
                data = json.loads(output[start:]) if start >= 0 else {}
                for k, v in match_json.items():
                    if str(data.get(k)) == str(v):
                        return True
            except (json.JSONDecodeError, ValueError):  # noqa: BLE001
                pass
        return False


def mcp_injection_for_server(
    server_name: str,
    *,
    registry: Any = None,
    orchestrator: AuthOrchestrator | None = None,
) -> dict[str, dict[str, str]]:
    """为 MCP server 名解析连接器认证注入(仅 已安装 + 已启用 + 已连接)。

    连接器 id 与 MCP server 名不一致(如 ``canva-ai`` → ``canva-mcp``),
    这里按 ``conn.mcp_servers`` 的 key 反查。找不到匹配 / 未安装 /
    未启用 / 未连接时返回空注入,MCP 代理侧不附加任何凭据。

    返回 ``{"headers": {...}, "env": {...}}``,分别供 HTTP / stdio 传输注入。
    """
    from runtime.platform.connectors.connector_registry import ConnectorRegistry

    registry = registry or ConnectorRegistry()
    orch = orchestrator or AuthOrchestrator()
    state = registry._state()
    for cid, st in state.items():
        if not (st.get("installed") and st.get("enabled")):
            continue
        conn = registry.get(cid)
        if conn is None or not conn.mcp_servers:
            continue
        if server_name not in conn.mcp_servers:
            continue
        return {
            "headers": orch.resolve_headers(conn),
            "env": orch.resolve_env(conn),
        }
    return {"headers": {}, "env": {}}


__all__ = [
    "AuthOrchestrator",
    "ConnectorTokenRefresher",
    "RefreshCleanupRequiredError",
    "mcp_injection_for_server",
]
