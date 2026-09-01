"""Bounded async JSONL client for ``codex app-server --listen stdio://``.

The App Server protocol is bidirectional: ordinary responses and streaming
notifications share stdout with server-initiated approval requests.  A single
reader owns stdout and routes those three message classes without allowing an
unbounded queue, callback, or subprocess tree to escape the client boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import signal
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, cast

from ._transport import (
    APPROVAL_METHODS,
    MCP_ELICITATION_APPROVAL_METHOD,
    TOOL_USER_INPUT_METHOD,
    build_environment,
    decode_message,
    default_process_factory,
    deny_approval,
    encode_message,
    merge_extra_params,
    normalize_input_items,
    normalize_object,
    parse_mcp_elicitation_approval,
    require_entity_response,
    taskkill_process_tree,
    validate_absolute_path,
    validate_approval_response,
    validate_identifier,
    validate_method,
    validate_thread_security,
    wait_for_exit,
)
from .dynamic_tools import (
    DYNAMIC_TOOL_CALL_METHOD,
    dynamic_tool_failure,
    validate_dynamic_tool_response,
)
from .types import (
    ApprovalHandler,
    ApprovalRequest,
    AppServerProcess,
    BackpressureError,
    CodexAppServerConfig,
    ConfigurationError,
    JsonObject,
    JsonValue,
    MessageTooLargeError,
    Notification,
    ProcessFactory,
    ProcessLaunch,
    ProtocolError,
    RemoteError,
    RequestId,
    RequestTimeoutError,
    TransportClosedError,
)

_logger = logging.getLogger(__name__)


class _State(Enum):
    NEW = "new"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class _StreamTerminal:
    error: BaseException | None


_StreamItem = Notification | _StreamTerminal


class CodexAppServerClient:
    """One App Server process and one initialized JSON-RPC connection.

    ``start()`` performs both required handshake steps (``initialize`` then
    ``initialized``).  The convenience thread methods choose conservative
    defaults, while ``request()`` remains available for version-pinned callers
    that need newer protocol methods.
    """

    def __init__(
        self,
        config: CodexAppServerConfig | None = None,
        *,
        approval_handler: ApprovalHandler | None = None,
        dynamic_tool_handler: ApprovalHandler | None = None,
        process_factory: ProcessFactory | None = None,
    ) -> None:
        self.config = config or CodexAppServerConfig()
        self._approval_handler = approval_handler
        self._dynamic_tool_handler = dynamic_tool_handler
        self._process_factory = process_factory or default_process_factory
        self._owns_process_group = process_factory is None
        self._process: AppServerProcess | None = None
        self._state = _State.NEW
        self._state_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._next_request_id = 1
        self._pending: dict[RequestId, asyncio.Future[JsonValue]] = {}
        self._notifications: asyncio.Queue[_StreamItem] = asyncio.Queue(
            maxsize=self.config.notification_queue_size
        )
        self._approval_requests: asyncio.Queue[ApprovalRequest] = asyncio.Queue(
            maxsize=self.config.approval_queue_size
        )
        self._dynamic_tool_requests: asyncio.Queue[ApprovalRequest] = asyncio.Queue(
            maxsize=self.config.approval_queue_size
        )
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._approval_task: asyncio.Task[None] | None = None
        self._dynamic_tool_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._stderr_tail = bytearray()
        self._terminal_error: BaseException | None = None
        self._terminal_published = False
        self._initialize_response: JsonObject | None = None

    async def __aenter__(self) -> CodexAppServerClient:
        await self.start()
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.close()

    @property
    def ready(self) -> bool:
        return self._state is _State.READY

    @property
    def process_id(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def stderr_tail(self) -> str:
        return bytes(self._stderr_tail).decode("utf-8", errors="replace")

    async def start(self) -> JsonObject:
        """Launch App Server and complete its mandatory handshake once."""
        async with self._state_lock:
            if self._state is _State.READY:
                return dict(self._initialize_response or {})
            if self._state is not _State.NEW:
                raise TransportClosedError(f"client cannot start from state {self._state.value}")
            self._state = _State.STARTING

            launch = ProcessLaunch(
                argv=self.config.command,
                cwd=self.config.cwd,
                env=build_environment(self.config),
                stream_limit=self.config.max_message_bytes + 1,
            )
            try:
                process = await self._process_factory(launch)
                if process.stdin is None or process.stdout is None:
                    raise TransportClosedError("App Server process must expose stdin and stdout")
                self._process = process
                self._reader_task = asyncio.create_task(
                    self._reader_loop(), name="codex-app-server-reader"
                )
                self._approval_task = asyncio.create_task(
                    self._approval_loop(), name="codex-app-server-approvals"
                )
                self._dynamic_tool_task = asyncio.create_task(
                    self._dynamic_tool_loop(), name="codex-app-server-dynamic-tools"
                )
                if process.stderr is not None:
                    self._stderr_task = asyncio.create_task(
                        self._stderr_loop(), name="codex-app-server-stderr"
                    )
                result = await self._request_raw(
                    "initialize",
                    self._initialize_params(),
                    timeout_s=self.config.initialize_timeout_s,
                    allow_starting=True,
                )
                if not isinstance(result, dict):
                    raise ProtocolError("initialize response must be a JSON object")
                await self._send_message({"method": "initialized"})
                self._initialize_response = cast(JsonObject, result)
                self._state = _State.READY
                return dict(self._initialize_response)
            except BaseException as exc:
                self._fail_connection(exc)
                await self._close_locked()
                raise

    async def initialize(self) -> JsonObject:
        """Public handshake entry point; equivalent to ``start()`` and idempotent."""
        return await self.start()

    async def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> JsonValue:
        """Send a version-pinned App Server request and await its response."""
        self._require_ready()
        normalized = normalize_object(params or {}, self.config)
        effective_timeout = self.config.request_timeout_s if timeout_s is None else timeout_s
        return await self._request_raw(
            method,
            normalized,
            timeout_s=effective_timeout,
        )

    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        """Send a client notification after initialization."""
        self._require_ready()
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = normalize_object(params, self.config)
        await self._send_message(message)

    async def account_read(
        self,
        *,
        refresh_token: bool = False,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Read App Server's current account without exposing stored tokens."""

        result = await self.request(
            "account/read",
            {"refreshToken": bool(refresh_token)},
            timeout_s=timeout_s,
        )
        return _require_object_result(result, "account/read")

    async def login_api_key(
        self,
        api_key: str,
        *,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Hand an OpenAI API key directly to App Server's credential store."""

        if not isinstance(api_key, str) or not api_key.strip() or "\x00" in api_key:
            raise ConfigurationError("api_key must be a non-empty, NUL-free string")
        result = await self.request(
            "account/login/start",
            {"type": "apiKey", "apiKey": api_key.strip()},
            timeout_s=timeout_s,
        )
        return _require_object_result(result, "account/login/start")

    async def login_chatgpt(
        self,
        *,
        device_code: bool = False,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Start a managed ChatGPT browser or device-code login flow."""

        params: JsonObject
        if device_code:
            params = {"type": "chatgptDeviceCode"}
        else:
            params = {
                "type": "chatgpt",
                "useHostedLoginSuccessPage": True,
                "appBrand": "chatgpt",
            }
        result = await self.request("account/login/start", params, timeout_s=timeout_s)
        response = _require_object_result(result, "account/login/start")
        expected_type = "chatgptDeviceCode" if device_code else "chatgpt"
        if response.get("type") != expected_type:
            raise ProtocolError("account/login/start returned an unexpected login type")
        required = (
            ("loginId", "verificationUrl", "userCode") if device_code else ("loginId", "authUrl")
        )
        if any(
            not isinstance(response.get(name), str) or not response.get(name) for name in required
        ):
            raise ProtocolError("account/login/start returned an incomplete login response")
        return response

    async def cancel_login(
        self,
        login_id: str,
        *,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Cancel one managed ChatGPT login generation."""

        validate_identifier(login_id, "login_id")
        result = await self.request(
            "account/login/cancel",
            {"loginId": login_id},
            timeout_s=timeout_s,
        )
        return _require_object_result(result, "account/login/cancel")

    async def logout_account(self, *, timeout_s: float | None = None) -> JsonObject:
        """Remove App Server's stored account for this isolated Codex home."""

        result = await self.request("account/logout", {}, timeout_s=timeout_s)
        return _require_object_result(result, "account/logout")

    async def read_account_rate_limits(
        self,
        *,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Read ChatGPT quota windows without exposing account credentials."""

        result = await self.request(
            "account/rateLimits/read",
            {},
            timeout_s=timeout_s,
        )
        return _require_object_result(result, "account/rateLimits/read")

    async def read_account_usage(
        self,
        *,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Read account-level ChatGPT token activity summaries."""

        result = await self.request(
            "account/usage/read",
            {},
            timeout_s=timeout_s,
        )
        return _require_object_result(result, "account/usage/read")

    async def list_apps(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
        force_refetch: bool = False,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Return one account-aware page from the App connector catalog."""

        if cursor is not None:
            validate_identifier(cursor, "cursor")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ConfigurationError("app list limit must be an integer from 1 to 100")
        params: JsonObject = {
            "cursor": cursor,
            "limit": limit,
            "forceRefetch": bool(force_refetch),
        }
        result = await self.request("app/list", params, timeout_s=timeout_s)
        response = _require_object_result(result, "app/list")
        if not isinstance(response.get("data"), list):
            raise ProtocolError("app/list response must contain a data array")
        next_cursor = response.get("nextCursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ProtocolError("app/list nextCursor must be a string or null")
        return response

    async def list_plugins(
        self,
        *,
        cwds: Sequence[str] | None = None,
        force_refetch: bool = False,
        marketplace_kinds: Sequence[str] | None = None,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Return the discovered Codex plugin marketplaces.

        ``plugin/list`` is still marked under development upstream, so this
        wrapper deliberately validates only the small response surface used by
        Echo and keeps the raw protocol behind one adapter boundary.
        """

        params: JsonObject = {"forceRefetch": bool(force_refetch)}
        if cwds is not None:
            normalized_cwds: list[str] = []
            for cwd in cwds:
                validate_absolute_path(cwd, "cwd")
                normalized_cwds.append(cwd)
            params["cwds"] = normalized_cwds
        if marketplace_kinds is not None:
            allowed = {
                "local",
                "vertical",
                "workspace-directory",
                "shared-with-me",
                "created-by-me-remote",
            }
            kinds = list(dict.fromkeys(marketplace_kinds))
            if any(kind not in allowed for kind in kinds):
                raise ConfigurationError("plugin marketplace kind is invalid")
            params["marketplaceKinds"] = kinds
        result = await self.request("plugin/list", params, timeout_s=timeout_s)
        response = _require_object_result(result, "plugin/list")
        if not isinstance(response.get("marketplaces"), list):
            raise ProtocolError("plugin/list response must contain a marketplaces array")
        return response

    async def install_plugin(
        self,
        plugin_name: str,
        *,
        marketplace_path: str | None = None,
        remote_marketplace_name: str | None = None,
        install_attempt_id: str | None = None,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Install one plugin from exactly one App Server marketplace source."""

        validate_identifier(plugin_name, "plugin_name")
        if (marketplace_path is None) == (remote_marketplace_name is None):
            raise ConfigurationError(
                "exactly one of marketplace_path or remote_marketplace_name is required"
            )
        params: JsonObject = {"pluginName": plugin_name}
        if marketplace_path is not None:
            validate_absolute_path(marketplace_path, "marketplace_path")
            params["marketplacePath"] = marketplace_path
        if remote_marketplace_name is not None:
            validate_identifier(remote_marketplace_name, "remote_marketplace_name")
            params["remoteMarketplaceName"] = remote_marketplace_name
        if install_attempt_id is not None:
            validate_identifier(install_attempt_id, "install_attempt_id")
            params["installAttemptId"] = install_attempt_id
        result = await self.request("plugin/install", params, timeout_s=timeout_s)
        response = _require_object_result(result, "plugin/install")
        if not isinstance(response.get("appsNeedingAuth"), list):
            raise ProtocolError("plugin/install response must contain an appsNeedingAuth array")
        return response

    async def uninstall_plugin(
        self,
        plugin_id: str,
        *,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Uninstall one App Server plugin by its catalog id."""

        validate_identifier(plugin_id, "plugin_id")
        result = await self.request(
            "plugin/uninstall",
            {"pluginId": plugin_id},
            timeout_s=timeout_s,
        )
        return _require_object_result(result, "plugin/uninstall")

    async def list_models(
        self,
        *,
        include_hidden: bool = False,
        cursor: str | None = None,
        limit: int | None = None,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Return one page from App Server's account-aware model catalog."""

        params: JsonObject = {"includeHidden": bool(include_hidden)}
        if cursor is not None:
            validate_identifier(cursor, "cursor")
            params["cursor"] = cursor
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
                raise ConfigurationError("model list limit must be an integer from 1 to 1000")
            params["limit"] = limit
        result = await self.request("model/list", params, timeout_s=timeout_s)
        response = _require_object_result(result, "model/list")
        if not isinstance(response.get("data"), list):
            raise ProtocolError("model/list response must contain a data array")
        next_cursor = response.get("nextCursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ProtocolError("model/list nextCursor must be a string or null")
        return response

    async def start_thread(
        self,
        *,
        cwd: str,
        model: str | None = None,
        approval_policy: str = "on-request",
        sandbox: str | None = "workspace-write",
        permissions: str | None = None,
        ephemeral: bool = False,
        extra_params: Mapping[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Create a thread with exactly one safe execution-policy selector.

        The legacy ``sandbox`` default remains backwards compatible. New
        experimental clients may instead pass ``sandbox=None`` and a named
        ``permissions`` profile; App Server rejects requests containing both.
        """
        validate_absolute_path(cwd, "cwd")
        self._validate_thread_execution_policy(approval_policy, sandbox, permissions)
        payload = merge_extra_params(
            extra_params,
            reserved={
                "cwd",
                "model",
                "approvalPolicy",
                "approvalsReviewer",
                "sandbox",
                "permissions",
                "ephemeral",
            },
        )
        payload.update(
            {
                "cwd": cwd,
                "approvalPolicy": approval_policy,
                "approvalsReviewer": "user",
                "ephemeral": ephemeral,
            }
        )
        if sandbox is not None:
            payload["sandbox"] = sandbox
        else:
            payload["permissions"] = cast(str, permissions)
        if model is not None:
            payload["model"] = model
        result = await self.request("thread/start", payload, timeout_s=timeout_s)
        return require_entity_response(result, "thread/start", "thread")

    async def resume_thread(
        self,
        thread_id: str,
        *,
        cwd: str | None = None,
        model: str | None = None,
        approval_policy: str = "on-request",
        sandbox: str | None = "workspace-write",
        permissions: str | None = None,
        exclude_turns: bool = False,
        extra_params: Mapping[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Resume a durable thread while reasserting safe execution defaults."""
        validate_identifier(thread_id, "thread_id")
        if cwd is not None:
            validate_absolute_path(cwd, "cwd")
        self._validate_thread_execution_policy(approval_policy, sandbox, permissions)
        payload = merge_extra_params(
            extra_params,
            reserved={
                "threadId",
                "cwd",
                "model",
                "approvalPolicy",
                "approvalsReviewer",
                "sandbox",
                "permissions",
                "excludeTurns",
            },
        )
        payload.update(
            {
                "threadId": thread_id,
                "approvalPolicy": approval_policy,
                "approvalsReviewer": "user",
                "excludeTurns": exclude_turns,
            }
        )
        if sandbox is not None:
            payload["sandbox"] = sandbox
        else:
            payload["permissions"] = cast(str, permissions)
        if cwd is not None:
            payload["cwd"] = cwd
        if model is not None:
            payload["model"] = model
        result = await self.request("thread/resume", payload, timeout_s=timeout_s)
        return require_entity_response(result, "thread/resume", "thread")

    def _validate_thread_execution_policy(
        self,
        approval_policy: str,
        sandbox: str | None,
        permissions: str | None,
    ) -> None:
        if (sandbox is None) == (permissions is None):
            raise ConfigurationError("exactly one of sandbox or permissions must be provided")
        if sandbox is not None:
            validate_thread_security(approval_policy, sandbox)
            return
        # Reuse the stable approval-policy validation while the permission
        # profile replaces only the execution-policy half of that contract.
        validate_thread_security(approval_policy, "read-only")
        assert permissions is not None
        validate_identifier(permissions, "permissions")
        if not self.config.experimental_api:
            raise ConfigurationError("permissions profiles require experimental_api=True")

    async def start_turn(
        self,
        thread_id: str,
        input_items: str | Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        extra_params: Mapping[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> JsonObject:
        """Start a turn; consume subsequent events with ``notifications()``."""
        validate_identifier(thread_id, "thread_id")
        payload = merge_extra_params(extra_params, reserved={"threadId", "input"})
        payload["threadId"] = thread_id
        payload["input"] = normalize_input_items(input_items, self.config)
        result = await self.request("turn/start", payload, timeout_s=timeout_s)
        return require_entity_response(result, "turn/start", "turn")

    async def interrupt(
        self,
        thread_id: str,
        turn_id: str,
        *,
        timeout_s: float | None = None,
    ) -> None:
        """Request cancellation of one active turn."""
        validate_identifier(thread_id, "thread_id")
        validate_identifier(turn_id, "turn_id")
        result = await self.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
            timeout_s=timeout_s,
        )
        if not isinstance(result, dict):
            raise ProtocolError("turn/interrupt response must be a JSON object")

    async def next_notification(self, *, timeout_s: float | None = None) -> Notification:
        """Read one event, optionally bounded by a caller-supplied timeout."""
        item = await self._next_stream_item(timeout_s)
        if isinstance(item, _StreamTerminal):
            if item.error is not None:
                raise item.error
            raise TransportClosedError("App Server notification stream is closed")
        return item

    async def notifications(self) -> AsyncIterator[Notification]:
        """Yield notifications until graceful close or a transport failure."""
        while True:
            item = await self._next_stream_item(None)
            if isinstance(item, _StreamTerminal):
                if item.error is not None:
                    raise item.error
                return
            yield item

    async def close(self) -> None:
        """Close stdin, then terminate and hard-kill the owned process tree."""
        async with self._state_lock:
            await self._close_locked()

    async def _close_locked(self) -> None:
        if self._state is _State.CLOSED:
            return
        self._state = _State.CLOSING
        process = self._process
        current = asyncio.current_task()

        if process is not None and process.stdin is not None:
            with contextlib.suppress(Exception):
                process.stdin.close()
            wait_closed = getattr(process.stdin, "wait_closed", None)
            if callable(wait_closed):
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(wait_closed(), timeout=self.config.close_grace_s)

        if process is not None and process.returncode is None:
            exited = await wait_for_exit(process, self.config.close_grace_s)
            if not exited:
                await self._signal_process(process, hard=False)
                exited = await wait_for_exit(process, self.config.terminate_grace_s)
            if not exited:
                await self._signal_process(process, hard=True)
                await wait_for_exit(process, self.config.kill_wait_s)

        tasks = (
            self._reader_task,
            self._approval_task,
            self._dynamic_tool_task,
            self._stderr_task,
        )
        for task in tasks:
            if task is not None and task is not current and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in tasks if task is not None and task is not current),
            return_exceptions=True,
        )

        if self._pending:
            self._fail_pending(TransportClosedError("App Server client closed"))
        if not self._terminal_published:
            self._publish_terminal(self._terminal_error)
        self._process = None
        self._state = _State.CLOSED

    def _initialize_params(self) -> JsonObject:
        capabilities: JsonObject = {"experimentalApi": self.config.experimental_api}
        if self.config.opt_out_notification_methods:
            capabilities["optOutNotificationMethods"] = list(
                self.config.opt_out_notification_methods
            )
        return {
            "clientInfo": {
                "name": self.config.client_name,
                "title": self.config.client_title,
                "version": self.config.client_version,
            },
            "capabilities": capabilities,
        }

    async def _request_raw(
        self,
        method: str,
        params: JsonObject,
        *,
        timeout_s: float,
        allow_starting: bool = False,
    ) -> JsonValue:
        if not allow_starting:
            self._require_ready()
        elif self._state is not _State.STARTING:
            raise TransportClosedError("initialize is only valid while the client is starting")
        validate_method(method, self.config.max_method_chars)
        if timeout_s <= 0:
            raise ConfigurationError("request timeout must be positive")
        if len(self._pending) >= self.config.max_pending_requests:
            raise BackpressureError("maximum pending App Server requests reached")

        request_id = self._next_request_id
        self._next_request_id += 1
        future: asyncio.Future[JsonValue] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send_message({"id": request_id, "method": method, "params": params})
            try:
                return await asyncio.wait_for(future, timeout=timeout_s)
            except TimeoutError as exc:
                raise RequestTimeoutError(
                    f"App Server request {method!r} timed out after {timeout_s:g}s"
                ) from exc
        finally:
            self._pending.pop(request_id, None)

    async def _send_message(self, message: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise TransportClosedError("App Server process is not running")
        payload = encode_message(message, self.config)
        async with self._write_lock:
            try:
                process.stdin.write(payload)
                await asyncio.wait_for(process.stdin.drain(), timeout=self.config.request_timeout_s)
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise TransportClosedError("failed to write to App Server stdin") from exc

    async def _reader_loop(self) -> None:
        try:
            while True:
                message = await self._read_message()
                await self._route_message(message)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if self._state not in {_State.CLOSING, _State.CLOSED}:
                self._fail_connection(exc)
                self._schedule_failure_cleanup()

    async def _read_message(self) -> JsonObject:
        process = self._process
        if process is None or process.stdout is None:
            raise TransportClosedError("App Server stdout is unavailable")
        try:
            line = await process.stdout.readline()
        except ValueError as exc:
            raise MessageTooLargeError("App Server frame exceeded stream limit") from exc
        if not line:
            detail = self.stderr_tail[-2000:]
            suffix = f"; stderr tail: {detail}" if detail else ""
            raise TransportClosedError(f"App Server closed stdout{suffix}")
        if len(line) > self.config.max_message_bytes:
            raise MessageTooLargeError(
                f"App Server frame exceeds {self.config.max_message_bytes} bytes"
            )
        if not line.endswith(b"\n"):
            raise ProtocolError("App Server JSONL frame is missing its newline terminator")
        try:
            text = line.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProtocolError("App Server frame is not valid UTF-8") from exc
        return decode_message(text, self.config)

    async def _route_message(self, message: JsonObject) -> None:
        method = message.get("method")
        if isinstance(method, str):
            request_id = message.get("id")
            params = message.get("params")
            normalized_params = cast(JsonObject, params) if isinstance(params, dict) else {}
            if request_id is None:
                try:
                    self._notifications.put_nowait(Notification(method, normalized_params))
                except asyncio.QueueFull as exc:
                    raise BackpressureError("notification queue is full") from exc
                return
            if method == DYNAMIC_TOOL_CALL_METHOD:
                request = ApprovalRequest(cast(RequestId, request_id), method, normalized_params)
                try:
                    self._dynamic_tool_requests.put_nowait(request)
                except asyncio.QueueFull:
                    await self._send_message(
                        {
                            "id": request_id,
                            "result": dynamic_tool_failure(
                                "Echo dynamic tool request queue is full"
                            ),
                        }
                    )
                return
            if method == TOOL_USER_INPUT_METHOD:
                # requestUserInput is an arbitrary questionnaire protocol, not
                # a binary Apps approval. This host has no form UI at this
                # boundary, so answer nothing without invoking ApprovalProvider.
                await self._send_message({"id": request_id, "result": deny_approval(method)})
                return
            if method not in APPROVAL_METHODS:
                await self._send_message(
                    {
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"unsupported server request: {method}",
                        },
                    }
                )
                return
            request = ApprovalRequest(cast(RequestId, request_id), method, normalized_params)
            try:
                self._approval_requests.put_nowait(request)
            except asyncio.QueueFull as exc:
                raise BackpressureError("approval request queue is full") from exc
            return

        request_id = cast(RequestId, message["id"])
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            _logger.debug("discarding late or unknown App Server response id=%r", request_id)
            return
        error = message.get("error")
        if isinstance(error, dict):
            future.set_exception(
                RemoteError(
                    cast(int, error["code"]),
                    cast(str, error["message"]),
                    cast(JsonValue, error.get("data")),
                )
            )
        else:
            future.set_result(cast(JsonValue, message.get("result")))

    async def _approval_loop(self) -> None:
        try:
            while True:
                request = await self._approval_requests.get()
                response = await self._resolve_approval(request)
                await self._send_message({"id": request.request_id, "result": response})
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if self._state not in {_State.CLOSING, _State.CLOSED}:
                self._fail_connection(exc)
                self._schedule_failure_cleanup()

    async def _resolve_approval(self, request: ApprovalRequest) -> JsonObject:
        fallback = deny_approval(request.method)
        if (
            request.method == MCP_ELICITATION_APPROVAL_METHOD
            and parse_mcp_elicitation_approval(request.params) is None
        ):
            return fallback
        handler = self._approval_handler
        if handler is None:
            return fallback

        async def _invoke() -> Mapping[str, Any]:
            if inspect.iscoroutinefunction(handler):
                return await handler(request)  # type: ignore[misc]
            result = await asyncio.to_thread(handler, request)
            if inspect.isawaitable(result):
                return await result
            return result

        try:
            raw = await asyncio.wait_for(_invoke(), timeout=self.config.approval_timeout_s)
            return validate_approval_response(request.method, raw, self.config)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.warning("approval handler failed closed for %s: %s", request.method, exc)
            return fallback

    async def _dynamic_tool_loop(self) -> None:
        try:
            while True:
                request = await self._dynamic_tool_requests.get()
                response = await self._resolve_dynamic_tool(request)
                await self._send_message({"id": request.request_id, "result": response})
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if self._state not in {_State.CLOSING, _State.CLOSED}:
                self._fail_connection(exc)
                self._schedule_failure_cleanup()

    async def _resolve_dynamic_tool(self, request: ApprovalRequest) -> JsonObject:
        fallback = dynamic_tool_failure("Echo dynamic tool bridge is unavailable")
        handler = self._dynamic_tool_handler
        if handler is None:
            return fallback

        async def _invoke() -> Mapping[str, Any]:
            if inspect.iscoroutinefunction(handler):
                return await handler(request)  # type: ignore[misc]
            result = await asyncio.to_thread(handler, request)
            if inspect.isawaitable(result):
                return await result
            return result

        try:
            raw = await asyncio.wait_for(_invoke(), timeout=self.config.approval_timeout_s)
            return cast(JsonObject, validate_dynamic_tool_response(raw))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Dynamic handlers may fail with provider payloads, host paths or
            # credentials in their exception message.  Keep model response and
            # server logs on the fixed, non-secret failure surface.
            _logger.warning(
                "dynamic tool handler failed closed (%s)",
                type(exc).__name__,
            )
            return fallback

    async def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                chunk = await process.stderr.read(4096)
                if not chunk:
                    return
                self._stderr_tail.extend(chunk)
                overflow = len(self._stderr_tail) - self.config.stderr_tail_bytes
                if overflow > 0:
                    del self._stderr_tail[:overflow]
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError):
            _logger.debug("App Server stderr drain stopped", exc_info=True)

    async def _next_stream_item(self, timeout_s: float | None) -> _StreamItem:
        if timeout_s is not None and timeout_s <= 0:
            raise ConfigurationError("notification timeout must be positive")
        try:
            if timeout_s is None:
                return await self._notifications.get()
            return await asyncio.wait_for(self._notifications.get(), timeout=timeout_s)
        except TimeoutError as exc:
            raise RequestTimeoutError(
                f"App Server notification timed out after {timeout_s:g}s"
            ) from exc

    def _require_ready(self) -> None:
        if self._state is not _State.READY:
            detail = f": {self._terminal_error}" if self._terminal_error else ""
            raise TransportClosedError(f"App Server client is not ready{detail}")

    def _fail_connection(self, exc: BaseException) -> None:
        if self._terminal_error is not None:
            return
        if not isinstance(exc, CodexAppServerBaseErrors):
            exc = TransportClosedError(str(exc) or type(exc).__name__)
        self._terminal_error = exc
        if self._state not in {_State.CLOSING, _State.CLOSED}:
            self._state = _State.FAILED
        self._fail_pending(exc)
        self._publish_terminal(exc)

    def _schedule_failure_cleanup(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(
                self.close(), name="codex-app-server-failure-cleanup"
            )

    def _fail_pending(self, exc: BaseException) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    def _publish_terminal(self, error: BaseException | None) -> None:
        if self._terminal_published:
            return
        terminal = _StreamTerminal(error)
        try:
            self._notifications.put_nowait(terminal)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._notifications.get_nowait()
            self._notifications.put_nowait(terminal)
        self._terminal_published = True

    async def _signal_process(self, process: AppServerProcess, *, hard: bool) -> None:
        if not self._owns_process_group:
            with contextlib.suppress(ProcessLookupError, OSError):
                process.kill() if hard else process.terminate()
            return

        if os.name == "nt":
            if not hard:
                send_signal = getattr(process, "send_signal", None)
                if callable(send_signal):
                    with contextlib.suppress(ProcessLookupError, OSError):
                        send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
                        return
                with contextlib.suppress(ProcessLookupError, OSError):
                    process.terminate()
                return
            await taskkill_process_tree(process.pid)
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    process.kill()
            return

        sig = signal.SIGKILL if hard else signal.SIGTERM
        try:
            process_group = os.getpgid(process.pid)
            if process_group == process.pid and process_group != os.getpgrp():
                os.killpg(process_group, sig)
                return
        except (ProcessLookupError, PermissionError, OSError):
            pass
        with contextlib.suppress(ProcessLookupError, OSError):
            process.kill() if hard else process.terminate()


def _require_object_result(result: JsonValue, operation: str) -> JsonObject:
    if not isinstance(result, dict):
        raise ProtocolError(f"{operation} response must be a JSON object")
    return cast(JsonObject, result)


# Keep isinstance narrow without hiding programming errors behind fail-closed
# transport wrapping.
CodexAppServerBaseErrors = (
    BackpressureError,
    MessageTooLargeError,
    ProtocolError,
    RemoteError,
    RequestTimeoutError,
    TransportClosedError,
)


__all__ = ["CodexAppServerClient"]
