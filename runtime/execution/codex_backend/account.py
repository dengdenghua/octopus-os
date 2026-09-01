"""Principal-scoped Codex account control plane.

Managed ChatGPT login is a multi-request protocol: App Server must remain
alive while the user completes a browser/device ceremony.  The service keeps a
small bounded pool of principal-isolated control processes, fences login ids,
reaps idle sessions, and closes every child on application shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import stat
import threading
import time
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from runtime.safety.auth.scope import TenantScope

from ._security_support import (
    _atomic_write_private,
    _ensure_private_directory,
    _prepare_state_root,
    _read_owned_private_file,
)
from .client import CodexAppServerClient
from .command import resolve_codex_app_server_command
from .types import (
    CodexAppServerConfig,
    ConfigurationError,
    Notification,
    ProcessFactory,
    ProtocolError,
    RequestTimeoutError,
    TransportClosedError,
)

_LOG = logging.getLogger(__name__)
_LEGACY_MARKETPLACE_MAX_BYTES = 16 * 1024 * 1024
_LEGACY_PLUGIN_MANIFEST_MAX_BYTES = 2 * 1024 * 1024


class CodexAccountConflict(RuntimeError):
    """An account mutation conflicts with a newer login generation."""


class CodexAccountCapacityError(RuntimeError):
    """Every bounded control slot currently owns an active login."""


class CodexAccountLeaseError(RuntimeError):
    """Another server worker owns this principal's control process."""


_CONTROL_LEASES: set[str] = set()
_CONTROL_LEASES_LOCK = threading.Lock()
_ACCOUNT_SERVICES: weakref.WeakSet[CodexAccountService] = weakref.WeakSet()
_ACCOUNT_SERVICES_LOCK = threading.Lock()


@dataclass(slots=True)
class _ControlLease:
    key: str
    descriptor: int
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        descriptor = self.descriptor
        self.descriptor = -1
        with contextlib.suppress(ImportError, OSError):
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            elif os.name == "posix":
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(descriptor)
        with _CONTROL_LEASES_LOCK:
            _CONTROL_LEASES.discard(self.key)


@dataclass(frozen=True, slots=True)
class CodexAccountStatus:
    account: dict[str, object] | None
    requires_openai_auth: bool
    login_pending: bool
    login_id: str | None = None
    login_error: str | None = None

    def to_wire(self) -> dict[str, object]:
        return {
            "account": self.account,
            "requires_openai_auth": self.requires_openai_auth,
            "login_pending": self.login_pending,
            "login_id": self.login_id,
            "login_error": self.login_error,
        }


@dataclass(slots=True)
class _ControlRuntime:
    client: CodexAppServerClient
    home: Path
    lease: _ControlLease
    loop: asyncio.AbstractEventLoop
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_login_id: str | None = None
    active_login_type: str | None = None
    login_started_at: float | None = None
    login_error: str | None = None
    model_catalog_cache: dict[bool, tuple[float, list[dict[str, object]]]] = field(
        default_factory=dict
    )
    plugin_catalog_cache: tuple[float, list[dict[str, object]]] | None = None
    last_used: float = field(default_factory=time.monotonic)
    closed: bool = False


ClientFactory = Callable[[CodexAppServerConfig], CodexAppServerClient]


class CodexAccountService:
    """Bounded persistent App Server sessions partitioned by principal."""

    def __init__(
        self,
        state_root: str | Path,
        *,
        command: tuple[str, ...] | None = None,
        client_factory: ClientFactory | None = None,
        process_factory: ProcessFactory | None = None,
        legacy_source_home: str | Path | None = None,
        allow_local_principal_inheritance: bool = False,
        max_sessions: int = 16,
        idle_timeout_s: float = 15 * 60,
        login_timeout_s: float = 20 * 60,
    ) -> None:
        root = Path(state_root).expanduser().resolve(strict=False)
        if not root.is_absolute():
            raise ConfigurationError("Codex account state root must be absolute")
        if command is not None and (
            not command or any(not part or "\x00" in part for part in command)
        ):
            raise ConfigurationError("Codex account command must be explicit and NUL-free")
        if max_sessions < 1 or max_sessions > 256:
            raise ConfigurationError("Codex account max_sessions must be from 1 to 256")
        if idle_timeout_s <= 0:
            raise ConfigurationError("Codex account idle timeout must be positive")
        if login_timeout_s <= 0:
            raise ConfigurationError("Codex account login timeout must be positive")
        if root.parent == root:
            raise ConfigurationError("filesystem root cannot be Codex account state_root")
        # Create the tree lazily on the first account operation. Merely
        # mounting the config router must not mutate an otherwise unused data
        # directory (and keeps read-only API/schema tests side-effect free).
        self._state_root = root
        self._command = command
        self._client_factory = client_factory
        self._process_factory = process_factory
        self._legacy_source_home = (
            Path(legacy_source_home).expanduser().resolve(strict=False)
            if legacy_source_home is not None
            else None
        )
        # Local desktop may still have an authenticated Echo principal even
        # though one OS user owns the whole installation.  Opting in lets that
        # principal reuse the machine owner's ChatGPT login without copying it
        # to the renderer.  Shared/server construction never enables this.
        self._allow_local_principal_inheritance = bool(allow_local_principal_inheritance)
        self._max_sessions = max_sessions
        self._idle_timeout_s = idle_timeout_s
        self._login_timeout_s = login_timeout_s
        self._runtimes: dict[str, _ControlRuntime] = {}
        self._pool_lock = asyncio.Lock()
        self._reaper_task: asyncio.Task[None] | None = None
        self._closed = False
        with _ACCOUNT_SERVICES_LOCK:
            _ACCOUNT_SERVICES.add(self)

    @property
    def state_root(self) -> Path:
        return self._state_root

    def account_home(self, scope: TenantScope | None) -> Path:
        """Return the opaque tenant/principal partition used by control + turns."""

        return codex_account_home(self._state_root, scope)

    def resolve_execution_auth_home(self, scope: TenantScope | None) -> Path | None:
        """Prefer scoped auth; optionally inherit host auth on local desktop."""

        scoped_home = self.account_home(scope)
        if _valid_auth_file(scoped_home / "auth.json", required=False):
            return scoped_home
        # Shared principals never inherit the OS user's ambient Codex account.
        # Local desktop can opt in because the OS user is the installation's
        # trust boundary even when the UI itself has an authenticated actor.
        if self._may_inherit_local_auth(scope) and _valid_auth_file(
            self._legacy_source_home / "auth.json", required=False
        ):
            return self._legacy_source_home
        return None

    def _may_inherit_local_auth(self, scope: TenantScope | None) -> bool:
        return self._legacy_source_home is not None and (
            scope is None or self._allow_local_principal_inheritance
        )

    def cached_models(
        self,
        scope: TenantScope | None,
        *,
        include_hidden: bool = False,
    ) -> list[dict[str, object]] | None:
        """Return the already-loaded catalog without starting or waiting on App Server."""

        runtime = self._runtimes.get(self._scope_key(scope))
        if runtime is None:
            return None
        cached = runtime.model_catalog_cache.get(include_hidden)
        if cached is None:
            return None
        return [dict(model) for model in cached[1]]

    async def run_on_runtime_loop(
        self,
        scope: TenantScope | None,
        operation: Callable[[], Any],
    ) -> Any:
        """Run a control operation on the loop that owns its App Server client.

        Native model credential refresh can initialize a principal runtime from
        a worker thread.  Later HTTP requests arrive on the ASGI loop; directly
        awaiting the existing client's queues there raises ``bound to a
        different event loop`` and makes a valid login look disconnected.
        """

        runtime = self._runtimes.get(self._scope_key(scope))
        current_loop = asyncio.get_running_loop()
        if runtime is None or runtime.loop is current_loop:
            return await operation()
        if not runtime.loop.is_running():
            raise TransportClosedError("Codex account control loop is unavailable")
        future = asyncio.run_coroutine_threadsafe(operation(), runtime.loop)
        return await asyncio.wrap_future(future)

    async def read_account(
        self,
        scope: TenantScope | None,
        *,
        refresh_token: bool = False,
    ) -> CodexAccountStatus:
        runtime = await self._runtime(scope)
        async with runtime.lock:
            await self._drain_notifications(runtime)
            response = await runtime.client.account_read(refresh_token=refresh_token)
            runtime.last_used = time.monotonic()
            status = _normalize_account_response(response, runtime)
            if status.account is not None:
                runtime.active_login_id = None
                runtime.active_login_type = None
                runtime.login_started_at = None
                runtime.login_error = None
                status = _normalize_account_response(response, runtime)
            return status

    async def refresh_for_execution(self, scope: TenantScope | None) -> Path | None:
        """Refresh master credentials under the principal control lease.

        Execution homes receive a copy of ``auth.json``. Refreshing here just
        before each turn keeps that copy from starting with an expired access
        token and prevents independent threads from racing on a stale master
        refresh token during normal-length turns.
        """

        runtime = await self._runtime(scope)
        async with runtime.lock:
            await self._drain_notifications(runtime)
            response = await runtime.client.account_read(refresh_token=True)
            status = _normalize_account_response(response, runtime)
            runtime.last_used = time.monotonic()
            if status.account is None:
                return None
            managed = self.account_home(scope)
            return managed if _valid_auth_file(managed / "auth.json", required=False) else None

    async def refresh_for_execution_any_loop(
        self,
        scope: TenantScope | None,
    ) -> Path | None:
        """Route refresh to the event loop that owns the persistent client."""

        runtime = self._runtimes.get(self._scope_key(scope))
        current_loop = asyncio.get_running_loop()
        if runtime is None or runtime.loop is current_loop:
            return await self.refresh_for_execution(scope)
        if not runtime.loop.is_running():
            raise TransportClosedError("Codex account control loop is unavailable")
        future = asyncio.run_coroutine_threadsafe(
            self.refresh_for_execution(scope),
            runtime.loop,
        )
        return await asyncio.wrap_future(future)

    async def login(
        self,
        scope: TenantScope | None,
        *,
        login_type: str,
        api_key: str | None = None,
    ) -> dict[str, object]:
        runtime = await self._runtime(scope)
        async with runtime.lock:
            await self._drain_notifications(runtime)
            runtime.model_catalog_cache.clear()
            if runtime.active_login_id is not None:
                raise CodexAccountConflict("a Codex login is already pending for this account")
            runtime.login_error = None
            if login_type == "apiKey":
                if api_key is None:
                    raise ConfigurationError("api_key is required for API key login")
                response = await runtime.client.login_api_key(api_key)
            elif login_type == "chatgpt":
                response = await runtime.client.login_chatgpt(device_code=False)
            elif login_type == "chatgptDeviceCode":
                response = await runtime.client.login_chatgpt(device_code=True)
            else:
                raise ConfigurationError("unsupported Codex login type")
            safe = _normalize_login_response(response)
            login_id = safe.get("login_id")
            if isinstance(login_id, str):
                runtime.active_login_id = login_id
                runtime.active_login_type = login_type
                runtime.login_started_at = time.monotonic()
            runtime.last_used = time.monotonic()
            return safe

    async def cancel_login(
        self,
        scope: TenantScope | None,
        *,
        login_id: str,
    ) -> dict[str, object]:
        runtime = await self._runtime(scope)
        async with runtime.lock:
            await self._drain_notifications(runtime)
            if runtime.active_login_id != login_id:
                return {
                    "cancelled": False,
                    "login_id": login_id,
                    "reason": "stale_or_unknown_login",
                }
            await runtime.client.cancel_login(login_id)
            runtime.active_login_id = None
            runtime.active_login_type = None
            runtime.login_started_at = None
            runtime.login_error = None
            runtime.last_used = time.monotonic()
            return {"cancelled": True, "login_id": login_id, "reason": None}

    async def logout(self, scope: TenantScope | None) -> dict[str, object]:
        runtime = await self._runtime(scope)
        async with runtime.lock:
            await self._drain_notifications(runtime)
            if runtime.active_login_id is not None:
                await runtime.client.cancel_login(runtime.active_login_id)
            await runtime.client.logout_account()
            runtime.model_catalog_cache.clear()
            runtime.active_login_id = None
            runtime.active_login_type = None
            runtime.login_started_at = None
            runtime.login_error = None
            runtime.last_used = time.monotonic()
            return {"logged_out": True}

    async def list_models(
        self,
        scope: TenantScope | None,
        *,
        include_hidden: bool = False,
    ) -> list[dict[str, object]]:
        runtime = await self._runtime(scope)
        async with runtime.lock:
            await self._drain_notifications(runtime)
            cached = runtime.model_catalog_cache.get(include_hidden)
            if cached is not None:
                runtime.last_used = time.monotonic()
                return [dict(model) for model in cached[1]]
            cursor: str | None = None
            models: list[dict[str, object]] = []
            for _page in range(20):
                response = await runtime.client.list_models(
                    include_hidden=include_hidden,
                    cursor=cursor,
                    limit=100,
                )
                raw_models = response.get("data")
                assert isinstance(raw_models, list)
                for raw_model in raw_models:
                    if isinstance(raw_model, Mapping):
                        models.append(_normalize_model_entry(raw_model))
                raw_cursor = response.get("nextCursor")
                cursor = raw_cursor if isinstance(raw_cursor, str) and raw_cursor else None
                if cursor is None:
                    break
            else:
                raise ProtocolError("model/list exceeded the bounded pagination limit")
            runtime.last_used = time.monotonic()
            runtime.model_catalog_cache[include_hidden] = (
                runtime.last_used,
                [dict(model) for model in models],
            )
            return models

    async def read_rate_limits(
        self,
        scope: TenantScope | None,
    ) -> dict[str, object]:
        """Return a bounded, display-safe view of ChatGPT quota windows."""

        runtime = await self._runtime(scope)
        async with runtime.lock:
            await self._drain_notifications(runtime)
            response = await runtime.client.read_account_rate_limits()
            runtime.last_used = time.monotonic()
            return _normalize_rate_limits(response)

    async def read_usage(
        self,
        scope: TenantScope | None,
    ) -> dict[str, object]:
        """Return account token activity without exposing auth metadata."""

        runtime = await self._runtime(scope)
        async with runtime.lock:
            await self._drain_notifications(runtime)
            response = await runtime.client.read_account_usage()
            runtime.last_used = time.monotonic()
            return _normalize_usage(response)

    async def list_apps(
        self,
        scope: TenantScope | None,
        *,
        force_refetch: bool = False,
    ) -> list[dict[str, object]]:
        """List account-accessible connectors with bounded public metadata."""

        runtime = await self._runtime(scope)
        async with runtime.lock:
            await self._drain_notifications(runtime)
            cursor: str | None = None
            apps: list[dict[str, object]] = []
            for _page in range(20):
                response = await runtime.client.list_apps(
                    cursor=cursor,
                    limit=100,
                    force_refetch=force_refetch and cursor is None,
                )
                raw_apps = response.get("data")
                assert isinstance(raw_apps, list)
                apps.extend(
                    _normalize_app_entry(item) for item in raw_apps if isinstance(item, Mapping)
                )
                raw_cursor = response.get("nextCursor")
                cursor = raw_cursor if isinstance(raw_cursor, str) and raw_cursor else None
                if cursor is None:
                    break
            else:
                raise ProtocolError("app/list exceeded the bounded pagination limit")
            runtime.last_used = time.monotonic()
            return apps

    async def list_plugins(
        self,
        scope: TenantScope | None,
        *,
        force_refetch: bool = False,
    ) -> list[dict[str, object]]:
        """List Codex marketplace plugins as Echo capability rows."""

        runtime = await self._runtime(scope)
        async with runtime.lock:
            await self._drain_notifications(runtime)
            if not force_refetch and runtime.plugin_catalog_cache is not None:
                runtime.last_used = time.monotonic()
                return [dict(item) for item in runtime.plugin_catalog_cache[1]]
            try:
                response = await runtime.client.list_plugins(
                    force_refetch=force_refetch,
                    marketplace_kinds=(
                        "local",
                        "vertical",
                        "workspace-directory",
                        "shared-with-me",
                        "created-by-me-remote",
                    ),
                )
            except Exception as exc:
                # The official remote catalog is independently rate limited.  A local
                # desktop user may safely fall back to their already-downloaded Codex
                # marketplace checkout; authenticated/shared principals must never
                # inherit that OS-user cache.
                if scope is not None or self._legacy_source_home is None:
                    raise
                response = _legacy_plugin_catalog_response(
                    self._legacy_source_home,
                    install_home=runtime.home,
                )
                _LOG.warning(
                    "Codex remote plugin catalog unavailable; using local official cache: %s",
                    exc,
                )
            rows = _normalize_plugin_catalog(response)
            if scope is None and self._legacy_source_home is not None:
                # App Server may return a successful but partial response while the
                # remote marketplace is throttled. Merge the local official checkout
                # as a stale-while-revalidate source; live rows win on matching ids so
                # their current install/auth state is preserved.
                try:
                    cached_rows = _normalize_plugin_catalog(
                        _legacy_plugin_catalog_response(
                            self._legacy_source_home,
                            install_home=runtime.home,
                        )
                    )
                except (ConfigurationError, ProtocolError):
                    cached_rows = []
                live_ids = {str(item.get("id") or "") for item in rows}
                rows.extend(item for item in cached_rows if item.get("id") not in live_ids)
            runtime.last_used = time.monotonic()
            runtime.plugin_catalog_cache = (runtime.last_used, [dict(item) for item in rows])
            return rows

    async def plugin_icon_path(
        self,
        scope: TenantScope | None,
        *,
        catalog_id: str,
    ) -> Path | None:
        """Resolve a catalog icon without exposing marketplace filesystem paths."""

        entry = await self._plugin_catalog_entry(scope, catalog_id)
        raw_path = entry.get("_icon_path")
        if not isinstance(raw_path, str) or not raw_path:
            return None
        path = Path(raw_path).resolve(strict=False)
        if scope is not None or self._legacy_source_home is None:
            return None
        try:
            path.relative_to(self._legacy_source_home)
        except ValueError:
            return None
        return path if path.is_file() else None

    async def install_plugin(
        self,
        scope: TenantScope | None,
        *,
        catalog_id: str,
        install_attempt_id: str | None = None,
    ) -> dict[str, object]:
        """Install one catalog row through the principal-scoped App Server."""

        entry = await self._plugin_catalog_entry(scope, catalog_id)
        runtime = await self._runtime(scope)
        async with runtime.lock:
            response = await runtime.client.install_plugin(
                str(entry["plugin_name"]),
                marketplace_path=(
                    str(entry["_marketplace_path"])
                    if isinstance(entry.get("_marketplace_path"), str)
                    else None
                ),
                remote_marketplace_name=(
                    str(entry["remote_marketplace_name"])
                    if isinstance(entry.get("remote_marketplace_name"), str)
                    else None
                ),
                install_attempt_id=install_attempt_id,
            )
            runtime.plugin_catalog_cache = None
            runtime.last_used = time.monotonic()
        apps = response.get("appsNeedingAuth")
        return {
            "installed": True,
            "enabled": True,
            "capability_id": catalog_id,
            "source": "codex_plugin",
            "auth_policy": response.get("authPolicy"),
            "apps_needing_auth": apps if isinstance(apps, list) else [],
            "message": "Codex 插件已按需安装。",
        }

    async def uninstall_plugin(
        self,
        scope: TenantScope | None,
        *,
        catalog_id: str,
    ) -> dict[str, object]:
        """Uninstall one catalog row through the principal-scoped App Server."""

        entry = await self._plugin_catalog_entry(scope, catalog_id)
        plugin_id = entry.get("codex_plugin_id")
        if not isinstance(plugin_id, str) or not plugin_id:
            raise ProtocolError("plugin catalog row is missing its Codex plugin id")
        runtime = await self._runtime(scope)
        async with runtime.lock:
            await runtime.client.uninstall_plugin(plugin_id)
            runtime.plugin_catalog_cache = None
            runtime.last_used = time.monotonic()
        return {"installed": False, "capability_id": catalog_id}

    async def _plugin_catalog_entry(
        self,
        scope: TenantScope | None,
        catalog_id: str,
    ) -> dict[str, object]:
        if not isinstance(catalog_id, str) or not catalog_id.startswith("codex-marketplace:"):
            raise ConfigurationError("Codex plugin catalog id is invalid")
        rows = await self.list_plugins(scope)
        match = next((item for item in rows if item.get("id") == catalog_id), None)
        if match is None:
            rows = await self.list_plugins(scope, force_refetch=True)
            match = next((item for item in rows if item.get("id") == catalog_id), None)
        if match is None:
            raise ConfigurationError("Codex marketplace plugin was not found")
        return match

    async def close_all(self) -> None:
        """Reap every control child; safe and idempotent at app shutdown."""

        reaper = self._reaper_task
        self._reaper_task = None
        if reaper is not None and reaper is not asyncio.current_task():
            reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reaper
        async with self._pool_lock:
            self._closed = True
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        await asyncio.gather(
            *(self._close_runtime(runtime) for runtime in runtimes),
            return_exceptions=True,
        )

    def start_idle_reaper(self) -> None:
        """Start one bounded background reaper in the active app loop."""

        if self._closed or (self._reaper_task is not None and not self._reaper_task.done()):
            return
        self._reaper_task = asyncio.create_task(
            self._idle_reaper_loop(),
            name="codex-account-control-reaper",
        )

    async def reap_idle(self) -> int:
        """Close non-login sessions idle beyond the configured bound."""

        now = time.monotonic()
        async with self._pool_lock:
            stale_keys = []
            for key, runtime in self._runtimes.items():
                idle_expired = (
                    runtime.active_login_id is None
                    and now - runtime.last_used >= self._idle_timeout_s
                )
                login_expired = (
                    runtime.active_login_id is not None
                    and runtime.login_started_at is not None
                    and now - runtime.login_started_at >= self._login_timeout_s
                )
                if not runtime.lock.locked() and (idle_expired or login_expired):
                    stale_keys.append(key)
            stale = [self._runtimes.pop(key) for key in stale_keys]
        await asyncio.gather(
            *(self._close_runtime(runtime) for runtime in stale),
            return_exceptions=True,
        )
        return len(stale)

    async def _runtime(self, scope: TenantScope | None) -> _ControlRuntime:
        if self._closed:
            raise TransportClosedError("Codex account service is closed")
        await self.reap_idle()
        key = self._scope_key(scope)
        async with self._pool_lock:
            existing = self._runtimes.get(key)
            if existing is not None and not existing.closed:
                existing.last_used = time.monotonic()
                return existing
            if len(self._runtimes) >= self._max_sessions:
                raise CodexAccountCapacityError(
                    "all Codex account control sessions are occupied by active logins"
                )
            runtime = await self._start_runtime(scope)
            self._runtimes[key] = runtime
            return runtime

    async def _start_runtime(self, scope: TenantScope | None) -> _ControlRuntime:
        self._state_root = _prepare_state_root(self._state_root)
        command = self._command or resolve_codex_app_server_command()
        home = self.account_home(scope)
        app_home = home.parent / "app-home"
        temporary = home.parent / "tmp"
        for directory in (home, app_home, temporary):
            _ensure_private_directory(directory, root=self._state_root)
        lease = _acquire_control_lease(home.parent / ".control-runtime.lock")
        try:
            if self._may_inherit_local_auth(scope):
                _seed_legacy_auth(home, self._legacy_source_home)
            _atomic_write_private(home / "config.toml", _control_config().encode("utf-8"))
            environment = {
                "CODEX_HOME": str(home),
                "HOME": str(app_home),
                "USERPROFILE": str(app_home),
                "TMPDIR": str(temporary),
                "TMP": str(temporary),
                "TEMP": str(temporary),
                "PATH": os.environ.get("PATH") or os.defpath,
            }
            config = CodexAppServerConfig(
                command=command,
                cwd=str(home.parent),
                env_allowlist=frozenset(environment),
                env_overrides=environment,
                source_environment={},
                experimental_api=False,
                notification_queue_size=64,
                approval_queue_size=1,
            )
            if self._client_factory is not None:
                client = self._client_factory(config)
            else:
                client = CodexAppServerClient(config, process_factory=self._process_factory)
            await client.start()
            return _ControlRuntime(
                client=client,
                home=home,
                lease=lease,
                loop=asyncio.get_running_loop(),
            )
        except BaseException:
            lease.release()
            raise

    async def _drain_notifications(self, runtime: _ControlRuntime) -> None:
        for _ in range(64):
            try:
                notification = await runtime.client.next_notification(timeout_s=0.001)
            except RequestTimeoutError:
                return
            _apply_account_notification(runtime, notification)
        raise ProtocolError("Codex account notification queue did not quiesce")

    async def _close_runtime(self, runtime: _ControlRuntime) -> None:
        if runtime.closed:
            return
        runtime.closed = True
        if runtime.active_login_id is not None:
            with contextlib.suppress(Exception):
                await runtime.client.cancel_login(runtime.active_login_id)
        try:
            await runtime.client.close()
        finally:
            runtime.lease.release()

    async def _idle_reaper_loop(self) -> None:
        interval = min(60.0, max(1.0, min(self._idle_timeout_s, self._login_timeout_s) / 2))
        try:
            while not self._closed:
                await asyncio.sleep(interval)
                await self.reap_idle()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A later API access also performs a synchronous reap. Keep an
            # unexpected housekeeping failure from taking down the app loop.
            return

    @staticmethod
    def _scope_key(scope: TenantScope | None) -> str:
        return codex_account_scope_key(scope)


def _normalize_account_response(
    response: Mapping[str, Any],
    runtime: _ControlRuntime,
) -> CodexAccountStatus:
    raw_account = response.get("account")
    account: dict[str, object] | None = None
    if raw_account is not None:
        if not isinstance(raw_account, Mapping):
            raise ProtocolError("account/read account must be an object or null")
        account_type = raw_account.get("type")
        if account_type not in {"apiKey", "chatgpt", "amazonBedrock"}:
            raise ProtocolError("account/read returned an unsupported account type")
        account = {
            "type": account_type,
            "email": raw_account.get("email")
            if isinstance(raw_account.get("email"), str)
            else None,
            "plan_type": (
                raw_account.get("planType")
                if isinstance(raw_account.get("planType"), str)
                else None
            ),
        }
    requires_auth = response.get("requiresOpenaiAuth")
    if not isinstance(requires_auth, bool):
        raise ProtocolError("account/read requiresOpenaiAuth must be boolean")
    return CodexAccountStatus(
        account=account,
        requires_openai_auth=requires_auth,
        login_pending=runtime.active_login_id is not None,
        login_id=runtime.active_login_id,
        login_error=runtime.login_error,
    )


def _normalize_login_response(response: Mapping[str, Any]) -> dict[str, object]:
    login_type = response.get("type")
    if login_type == "apiKey":
        return {"type": "apiKey", "login_id": None}
    if login_type == "chatgpt":
        return {
            "type": "chatgpt",
            "login_id": response.get("loginId"),
            "auth_url": response.get("authUrl"),
        }
    if login_type == "chatgptDeviceCode":
        return {
            "type": "chatgptDeviceCode",
            "login_id": response.get("loginId"),
            "verification_url": response.get("verificationUrl"),
            "user_code": response.get("userCode"),
        }
    raise ProtocolError("account/login/start returned an unsupported login type")


def _normalize_model_entry(raw: Mapping[str, Any]) -> dict[str, object]:
    model_id = raw.get("id")
    display_name = raw.get("displayName")
    if not isinstance(model_id, str) or not model_id:
        raise ProtocolError("model/list item id must be a non-empty string")
    if not isinstance(display_name, str) or not display_name:
        display_name = model_id
    efforts: list[str] = []
    raw_efforts = raw.get("supportedReasoningEfforts")
    if isinstance(raw_efforts, list):
        for item in raw_efforts:
            if isinstance(item, Mapping) and isinstance(item.get("reasoningEffort"), str):
                efforts.append(str(item["reasoningEffort"]))
    modalities = raw.get("inputModalities")
    if not isinstance(modalities, list):
        modalities = ["text", "image"]
    return {
        "id": model_id,
        "display_name": display_name,
        "description": raw.get("description") if isinstance(raw.get("description"), str) else "",
        "reasoning_efforts": efforts,
        "default_reasoning_effort": (
            raw.get("defaultReasoningEffort")
            if isinstance(raw.get("defaultReasoningEffort"), str)
            else None
        ),
        "hidden": bool(raw.get("hidden")),
        "is_default": bool(raw.get("isDefault")),
        "input_modalities": [str(item) for item in modalities if isinstance(item, str)],
    }


def _normalize_rate_window(raw: Any) -> dict[str, object] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ProtocolError("account/rateLimits/read window must be an object or null")
    used = raw.get("usedPercent")
    duration = raw.get("windowDurationMins")
    reset = raw.get("resetsAt")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        raise ProtocolError("account/rateLimits/read usedPercent must be numeric")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
        raise ProtocolError("account/rateLimits/read windowDurationMins must be non-negative")
    if isinstance(reset, bool) or not isinstance(reset, int) or reset < 0:
        raise ProtocolError("account/rateLimits/read resetsAt must be non-negative")
    used_percent = min(100.0, max(0.0, float(used)))
    return {
        "used_percent": used_percent,
        "remaining_percent": 100.0 - used_percent,
        "window_duration_mins": duration,
        "resets_at": reset,
    }


def _normalize_app_entry(raw: Mapping[str, Any]) -> dict[str, object]:
    app_id = raw.get("id")
    name = raw.get("name")
    if not isinstance(app_id, str) or not app_id.strip() or len(app_id) > 256:
        raise ProtocolError("app/list item id must be a bounded string")
    if any(char in app_id for char in "\x00\r\n"):
        raise ProtocolError("app/list item id contains unsafe characters")
    return {
        "id": app_id.strip(),
        "name": name if isinstance(name, str) and name.strip() else app_id.strip(),
        "description": raw.get("description") if isinstance(raw.get("description"), str) else "",
        "logo_url": raw.get("logoUrl") if isinstance(raw.get("logoUrl"), str) else None,
        "install_url": raw.get("installUrl") if isinstance(raw.get("installUrl"), str) else None,
        "is_accessible": raw.get("isAccessible") is True,
        "is_enabled": raw.get("isEnabled") is True,
    }


def _legacy_plugin_catalog_response(
    source_home: Path,
    *,
    install_home: Path,
) -> dict[str, object]:
    """Project the official on-disk marketplace into the App Server wire shape."""

    source_root = source_home.expanduser().resolve(strict=False)
    catalog_path = source_root / ".tmp" / "plugins" / ".agents" / "plugins" / "marketplace.json"
    payload = _read_bounded_json_object(
        catalog_path,
        root=source_root,
        max_bytes=_LEGACY_MARKETPLACE_MAX_BYTES,
    )
    raw_plugins = payload.get("plugins")
    if not isinstance(raw_plugins, list) or len(raw_plugins) > 5000:
        raise ProtocolError("cached Codex marketplace plugins must be a bounded array")

    # Marketplace source paths are relative to the checkout root, two levels
    # above `.agents/plugins/marketplace.json`.
    checkout_root = catalog_path.parent.parent.parent
    plugins: list[dict[str, object]] = []
    for raw in raw_plugins:
        if not isinstance(raw, Mapping):
            continue
        name = raw.get("name")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 128
            or any(char in name for char in "/\\\x00\r\n")
        ):
            continue
        name = name.strip()
        raw_source = raw.get("source")
        raw_source = raw_source if isinstance(raw_source, Mapping) else {}
        relative_source = raw_source.get("path")
        if not isinstance(relative_source, str) or not relative_source.strip():
            continue
        plugin_root = (checkout_root / relative_source).resolve(strict=False)
        try:
            plugin_root.relative_to(checkout_root)
        except ValueError:
            continue
        manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
        try:
            manifest = _read_bounded_json_object(
                manifest_path,
                root=checkout_root,
                max_bytes=_LEGACY_PLUGIN_MANIFEST_MAX_BYTES,
            )
        except (ConfigurationError, ProtocolError):
            continue
        interface = manifest.get("interface")
        interface = dict(interface) if isinstance(interface, Mapping) else {}
        icon_value = interface.get("composerIcon") or interface.get("logo")
        icon_path: Path | None = None
        if isinstance(icon_value, str) and icon_value.strip():
            candidate = (plugin_root / icon_value).resolve(strict=False)
            try:
                candidate.relative_to(plugin_root)
            except ValueError:
                candidate = plugin_root / "__invalid__"
            if candidate.is_file():
                icon_path = candidate
                interface["composerIconUrl"] = icon_value
        policy = raw.get("policy")
        policy = policy if isinstance(policy, Mapping) else {}
        author = manifest.get("author")
        author = author if isinstance(author, Mapping) else {}
        if not interface.get("developerName") and isinstance(author.get("name"), str):
            interface["developerName"] = author["name"]
        if not interface.get("category") and isinstance(raw.get("category"), str):
            interface["category"] = raw["category"]
        if not interface.get("shortDescription") and isinstance(manifest.get("description"), str):
            interface["shortDescription"] = manifest["description"]
        installed = _legacy_plugin_is_installed(install_home, name)
        plugins.append(
            {
                "id": f"{name}@openai-curated",
                "name": name,
                "version": manifest.get("version"),
                "interface": interface,
                "source": {"type": "local"},
                "installPolicy": policy.get("installation"),
                "installed": installed,
                "enabled": installed,
                "_iconPath": str(icon_path) if icon_path is not None else None,
            }
        )
    if not plugins:
        raise ProtocolError("cached Codex marketplace contains no usable plugins")
    return {
        "marketplaces": [
            {
                "name": "openai-curated",
                "path": str(catalog_path),
                "plugins": plugins,
            }
        ],
        "featuredPluginIds": [],
    }


def _read_bounded_json_object(path: Path, *, root: Path, max_bytes: int) -> dict[str, Any]:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise ConfigurationError("cached Codex marketplace escaped its source root") from exc
    try:
        metadata = resolved.stat()
    except OSError as exc:
        raise ConfigurationError("cached Codex marketplace is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
        raise ConfigurationError("cached Codex marketplace file is invalid")
    try:
        parsed = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProtocolError("cached Codex marketplace JSON is invalid") from exc
    if not isinstance(parsed, dict):
        raise ProtocolError("cached Codex marketplace JSON must contain an object")
    return parsed


def _legacy_plugin_is_installed(install_home: Path, plugin_name: str) -> bool:
    cache_root = install_home / "plugins" / "cache"
    if not cache_root.is_dir():
        return False
    try:
        marketplaces = list(cache_root.iterdir())[:256]
    except OSError:
        return False
    for marketplace in marketplaces:
        plugin_root = marketplace / plugin_name
        if plugin_root.is_dir():
            return True
    return False


def _normalize_plugin_catalog(response: Mapping[str, Any]) -> list[dict[str, object]]:
    raw_marketplaces = response.get("marketplaces")
    if not isinstance(raw_marketplaces, list):
        raise ProtocolError("plugin/list marketplaces must be an array")
    featured = {value for value in response.get("featuredPluginIds", []) if isinstance(value, str)}
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_marketplace in raw_marketplaces:
        if not isinstance(raw_marketplace, Mapping):
            continue
        marketplace_name = raw_marketplace.get("name")
        if not isinstance(marketplace_name, str) or not marketplace_name.strip():
            continue
        marketplace_name = marketplace_name.strip()
        marketplace_path = raw_marketplace.get("path")
        if not isinstance(marketplace_path, str) or not Path(marketplace_path).is_absolute():
            marketplace_path = None
        raw_plugins = raw_marketplace.get("plugins")
        if not isinstance(raw_plugins, list):
            continue
        for raw_plugin in raw_plugins:
            if not isinstance(raw_plugin, Mapping):
                continue
            plugin_name = raw_plugin.get("name")
            codex_plugin_id = raw_plugin.get("id")
            if not isinstance(plugin_name, str) or not plugin_name.strip():
                continue
            if not isinstance(codex_plugin_id, str) or not codex_plugin_id.strip():
                codex_plugin_id = f"{plugin_name.strip()}@{marketplace_name}"
            catalog_id = f"codex-marketplace:{codex_plugin_id.strip()}"
            if catalog_id in seen:
                continue
            seen.add(catalog_id)
            interface = raw_plugin.get("interface")
            interface = interface if isinstance(interface, Mapping) else {}
            source = raw_plugin.get("source")
            source = source if isinstance(source, Mapping) else {}
            remote_marketplace_name = (
                marketplace_name
                if source.get("type") == "remote" or marketplace_path is None
                else None
            )
            display_name = interface.get("displayName")
            short_description = interface.get("shortDescription")
            logo_url = interface.get("logoUrl") or interface.get("composerIconUrl")
            category = interface.get("category")
            developer_name = interface.get("developerName")
            capabilities = interface.get("capabilities")
            capability_names = (
                [value for value in capabilities if isinstance(value, str)]
                if isinstance(capabilities, list)
                else []
            )
            install_policy = raw_plugin.get("installPolicy")
            availability = raw_plugin.get("availability")
            installable = install_policy != "NOT_AVAILABLE" and availability != "DISABLED_BY_ADMIN"
            rows.append(
                {
                    "id": catalog_id,
                    "name": (
                        display_name.strip()
                        if isinstance(display_name, str) and display_name.strip()
                        else plugin_name.strip()
                    ),
                    "name_zh": (
                        display_name.strip()
                        if isinstance(display_name, str) and display_name.strip()
                        else plugin_name.strip()
                    ),
                    "description": (
                        short_description if isinstance(short_description, str) else ""
                    ),
                    "description_zh": (
                        short_description if isinstance(short_description, str) else ""
                    ),
                    "type": "plugin",
                    "auth_mode": "none",
                    "source": "codex_plugin",
                    "provider_id": plugin_name.strip(),
                    "author": developer_name if isinstance(developer_name, str) else "",
                    "category": category if isinstance(category, str) else "plugin",
                    "icon": logo_url if isinstance(logo_url, str) else "",
                    "surface_capabilities": capability_names,
                    "mcp_servers": [],
                    "skill_count": 0,
                    "examples_zh": [],
                    "installed": raw_plugin.get("installed") is True,
                    "enabled": raw_plugin.get("enabled") is True,
                    "connected": False,
                    "version": (
                        str(raw_plugin.get("version") or raw_plugin.get("localVersion") or "")
                    ),
                    "featured": codex_plugin_id in featured or plugin_name in featured,
                    "installable": installable,
                    "marketplace_name": marketplace_name,
                    "_marketplace_path": marketplace_path,
                    "remote_marketplace_name": remote_marketplace_name,
                    "plugin_name": plugin_name.strip(),
                    "codex_plugin_id": codex_plugin_id.strip(),
                    "is_codex_marketplace": True,
                    "_icon_path": (
                        raw_plugin.get("_iconPath")
                        if isinstance(raw_plugin.get("_iconPath"), str)
                        else None
                    ),
                }
            )
    return rows


def _normalize_rate_bucket(raw: Any) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise ProtocolError("account/rateLimits/read bucket must be an object")
    limit_id = raw.get("limitId")
    if not isinstance(limit_id, str) or not limit_id.strip() or len(limit_id) > 256:
        raise ProtocolError("account/rateLimits/read limitId must be a bounded string")
    limit_name = raw.get("limitName")
    plan_type = raw.get("planType")
    reached = raw.get("rateLimitReachedType")
    return {
        "limit_id": limit_id.strip(),
        "limit_name": limit_name if isinstance(limit_name, str) else None,
        "primary": _normalize_rate_window(raw.get("primary")),
        "secondary": _normalize_rate_window(raw.get("secondary")),
        "plan_type": plan_type if isinstance(plan_type, str) else None,
        "rate_limit_reached_type": reached if isinstance(reached, str) else None,
    }


def _normalize_rate_limits(response: Mapping[str, Any]) -> dict[str, object]:
    buckets: list[dict[str, object]] = []
    raw_by_id = response.get("rateLimitsByLimitId")
    if isinstance(raw_by_id, Mapping):
        for raw in raw_by_id.values():
            buckets.append(_normalize_rate_bucket(raw))
    elif response.get("rateLimits") is not None:
        buckets.append(_normalize_rate_bucket(response.get("rateLimits")))
    reset_credits = response.get("rateLimitResetCredits")
    available_count: int | None = None
    if isinstance(reset_credits, Mapping):
        raw_count = reset_credits.get("availableCount")
        if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count >= 0:
            available_count = raw_count
    return {"buckets": buckets, "reset_credits_available": available_count}


def _optional_non_negative_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(f"account/usage/read {field} must be non-negative or null")
    return value


def _normalize_usage(response: Mapping[str, Any]) -> dict[str, object]:
    raw_summary = response.get("summary")
    if not isinstance(raw_summary, Mapping):
        raise ProtocolError("account/usage/read summary must be an object")
    summary = {
        "lifetime_tokens": _optional_non_negative_int(
            raw_summary.get("lifetimeTokens"), "lifetimeTokens"
        ),
        "peak_daily_tokens": _optional_non_negative_int(
            raw_summary.get("peakDailyTokens"), "peakDailyTokens"
        ),
        "longest_running_turn_sec": _optional_non_negative_int(
            raw_summary.get("longestRunningTurnSec"), "longestRunningTurnSec"
        ),
        "current_streak_days": _optional_non_negative_int(
            raw_summary.get("currentStreakDays"), "currentStreakDays"
        ),
        "longest_streak_days": _optional_non_negative_int(
            raw_summary.get("longestStreakDays"), "longestStreakDays"
        ),
    }
    buckets: list[dict[str, object]] = []
    raw_buckets = response.get("dailyUsageBuckets")
    if raw_buckets is not None:
        if not isinstance(raw_buckets, list):
            raise ProtocolError("account/usage/read dailyUsageBuckets must be an array or null")
        for raw in raw_buckets[:3660]:
            if not isinstance(raw, Mapping):
                raise ProtocolError("account/usage/read daily bucket must be an object")
            date = raw.get("startDate")
            if not isinstance(date, str) or not date or len(date) > 32:
                raise ProtocolError("account/usage/read startDate must be a bounded string")
            buckets.append(
                {
                    "start_date": date,
                    "tokens": _optional_non_negative_int(raw.get("tokens"), "tokens"),
                }
            )
    return {"summary": summary, "daily_usage_buckets": buckets}


def _apply_account_notification(runtime: _ControlRuntime, notification: Notification) -> None:
    if notification.method != "account/login/completed":
        return
    login_id = notification.params.get("loginId")
    if runtime.active_login_id is not None and login_id != runtime.active_login_id:
        return
    success = notification.params.get("success") is True
    runtime.login_error = None if success else "Codex login did not complete"
    runtime.active_login_id = None
    runtime.active_login_type = None
    runtime.login_started_at = None


def codex_account_scope_key(scope: TenantScope | None) -> str:
    """Return the opaque stable partition shared by control and execution."""

    if scope is None:
        return "local"
    material = f"{scope.tenant_id}\x00{scope.actor_id}".encode()
    return sha256(material).hexdigest()[:40]


def _acquire_control_lease(path: Path) -> _ControlLease:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _CONTROL_LEASES_LOCK:
        if key in _CONTROL_LEASES:
            raise CodexAccountLeaseError("Codex account is controlled by another worker")
        _CONTROL_LEASES.add(key)
    descriptor = -1
    try:
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CodexAccountLeaseError("Codex account control lease is invalid")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        if os.name == "nt":
            import msvcrt

            if metadata.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        elif os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            raise CodexAccountLeaseError("platform has no safe Codex account lease")
        return _ControlLease(key=key, descriptor=descriptor)
    except (BlockingIOError, ImportError, OSError) as exc:
        if descriptor >= 0:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        with _CONTROL_LEASES_LOCK:
            _CONTROL_LEASES.discard(key)
        raise CodexAccountLeaseError("Codex account is controlled by another worker") from exc
    except BaseException:
        if descriptor >= 0:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        with _CONTROL_LEASES_LOCK:
            _CONTROL_LEASES.discard(key)
        raise


def codex_account_home(state_root: str | Path, scope: TenantScope | None) -> Path:
    """Resolve a principal's managed Codex home without creating it."""

    root = Path(state_root).expanduser().resolve(strict=False)
    if not root.is_absolute() or root.parent == root:
        raise ConfigurationError("Codex account state root must be a safe absolute path")
    return root / "accounts" / codex_account_scope_key(scope) / "codex-home"


def resolve_codex_execution_auth_home(
    *,
    state_root: str | Path,
    scope: TenantScope | None,
    deployment_mode: str,
    legacy_source_home: str | Path | None = None,
    allow_local_principal_inheritance: bool = False,
) -> Path | None:
    """Select the only auth source an execution sidecar may inherit.

    A principal-scoped managed login always wins. The OS user's legacy
    ``~/.codex`` is eligible for unauthenticated local mode and, when the
    caller explicitly opts in, authenticated principals in the single-user
    desktop trust boundary. Shared/server deployments never inherit it.
    """

    managed = codex_account_home(state_root, scope)
    if _valid_auth_file(managed / "auth.json", required=False):
        return managed
    normalized_mode = str(deployment_mode or "local").strip().casefold()
    if (
        normalized_mode == "local"
        and legacy_source_home is not None
        and (scope is None or allow_local_principal_inheritance)
    ):
        legacy = Path(legacy_source_home).expanduser().resolve(strict=False)
        if _valid_auth_file(legacy / "auth.json", required=False):
            return legacy
    return None


async def refresh_codex_execution_auth_home(
    *,
    state_root: str | Path,
    scope: TenantScope | None,
) -> Path | None:
    """Refresh the managed master using its persistent control process.

    Services are process-local but the selected service holds a lifetime OS
    lease. In another backend worker, attempting a transient control process
    collides with that lease and fails closed instead of reading/writing the
    same principal home concurrently.
    """

    root = Path(state_root).expanduser().resolve(strict=False)
    scope_key = codex_account_scope_key(scope)
    with _ACCOUNT_SERVICES_LOCK:
        candidates = [
            service
            for service in _ACCOUNT_SERVICES
            if not service._closed and service.state_root == root
        ]
    candidates.sort(key=lambda service: scope_key in service._runtimes, reverse=True)
    lease_error: CodexAccountLeaseError | None = None
    for service in candidates:
        try:
            return await service.refresh_for_execution_any_loop(scope)
        except CodexAccountLeaseError as exc:
            lease_error = exc
    if lease_error is not None:
        raise lease_error
    transient = CodexAccountService(root, legacy_source_home=None)
    try:
        return await transient.refresh_for_execution_any_loop(scope)
    finally:
        await transient.close_all()


def _control_config() -> str:
    return "\n".join(
        [
            "# Managed by Echo account control plane.",
            "check_for_update_on_startup = false",
            'cli_auth_credentials_store = "file"',
            'file_opener = "none"',
            "notify = []",
            "mcp_servers = {}",
            "plugins = {}",
            "marketplaces = {}",
            "skills = { config = [] }",
            "agents = { enabled = false }",
            "",
        ]
    )


def _valid_auth_file(path: Path, *, required: bool) -> bool:
    data = _read_owned_private_file(path, max_bytes=1024 * 1024)
    if data is None:
        if required:
            raise ConfigurationError("Codex auth file is missing")
        return False
    try:
        parsed = json.loads(data.decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError) as exc:
        raise ConfigurationError("Codex auth file is invalid") from exc
    if not isinstance(parsed, dict):
        raise ConfigurationError("Codex auth file must contain an object")
    return True


def _seed_legacy_auth(target_home: Path, source_home: Path) -> None:
    target = target_home / "auth.json"
    if target.exists():
        _valid_auth_file(target, required=True)
        return
    source = source_home / "auth.json"
    data = _read_owned_private_file(source, max_bytes=1024 * 1024)
    if data is None:
        return
    try:
        parsed = json.loads(data.decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError) as exc:
        raise ConfigurationError("legacy Codex auth file is invalid") from exc
    if not isinstance(parsed, dict):
        raise ConfigurationError("legacy Codex auth file must contain an object")
    canonical = (json.dumps(parsed, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    _atomic_write_private(target, canonical)


__all__ = [
    "CodexAccountCapacityError",
    "CodexAccountConflict",
    "CodexAccountLeaseError",
    "CodexAccountService",
    "CodexAccountStatus",
    "codex_account_home",
    "codex_account_scope_key",
    "resolve_codex_execution_auth_home",
    "refresh_codex_execution_auth_home",
]
