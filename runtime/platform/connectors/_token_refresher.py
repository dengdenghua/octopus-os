"""Generation-fenced token refresh process supervisor."""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import re
import secrets
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from runtime.platform.connectors import cli_lifecycle
from runtime.platform.connectors.connector_registry import ConnectorDefinition
from runtime.platform.connectors.credential_store import CredentialStore
from runtime.safety.env_scrub import scrub_credential_env

_ALLOWED_ENV_PREFIX = (
    "MCP_",
    "CONNECTOR_",
    "LARK_",
    "DWS_",
    "WECOM_",
    "WESTOCK_",
    "TENCENT_",
)
_PROCESS_WAIT_SECONDS = 3.0
_REFRESH_COMMAND_TIMEOUT_SECONDS = 60.0
_REFRESH_POLL_SECONDS = 0.1
_REFRESH_WORKER_WAIT_SECONDS = 7.0


class RefreshCleanupRequiredError(RuntimeError):
    """A terminal auth transition could not prove its old child was reaped."""

    code = "connector_refresh_cleanup_required"

    def __init__(
        self,
        connector_id: str,
        *,
        reason: str,
        lease: dict[str, Any] | None = None,
    ) -> None:
        self.detail: dict[str, Any] = {
            "code": self.code,
            "connector_id": connector_id,
            "recovery_required": True,
            "reason": reason,
        }
        for key in ("generation", "owner_pid", "child_pid", "started_at"):
            if lease is not None and key in lease:
                self.detail[key] = lease[key]
        super().__init__(f"{self.code}: {connector_id}: {reason}; manual recovery is required")


@dataclass
class _TokenRefreshEntry:
    generation: int
    worker_nonce: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    cancelled: threading.Event = field(default_factory=threading.Event)
    timer: threading.Timer | None = None
    worker: threading.Thread | None = None
    child: Any = None
    child_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


@dataclass(frozen=True)
class _RefreshCancellation:
    connector_id: str
    entries: tuple[_TokenRefreshEntry, ...]
    lease: dict[str, Any] | None


_refresh_supervisor_lock = threading.Lock()
_refresh_supervisor_entries: dict[tuple[str, str], _TokenRefreshEntry] = {}


def reset_refresh_supervisor_for_tests() -> None:
    """Synchronously reap every process-global refresh worker.

    The supervisor is intentionally process-global so multiple registry
    instances share one refresh lease. Test processes create many short-lived
    orchestrators, though, and must not let a daemon worker from one case spawn
    a child while the next case is patching ``subprocess.Popen``.
    """
    with _refresh_supervisor_lock:
        entries = tuple({id(entry): entry for entry in _refresh_supervisor_entries.values()}.values())
        _refresh_supervisor_entries.clear()
    for entry in entries:
        entry.cancelled.set()
        timer = entry.timer
        if timer is not None:
            timer.cancel()
        with entry.child_lock:
            child = entry.child
        _reap_process(child)
    deadline = time.monotonic() + _REFRESH_WORKER_WAIT_SECONDS
    for entry in entries:
        worker = entry.worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(max(0.0, deadline - time.monotonic()))


def _reap_process(proc: Any) -> None:
    if proc is None:
        return
    try:
        alive = proc.poll() is None
    except Exception:  # noqa: BLE001
        alive = True
    if alive:
        with contextlib.suppress(Exception):
            proc.terminate()
    try:
        proc.wait(timeout=_PROCESS_WAIT_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception:  # noqa: BLE001
        return
    with contextlib.suppress(Exception):
        proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(timeout=_PROCESS_WAIT_SECONDS)


class ConnectorTokenRefresher:
    """Schedule token refreshes with durable generation and child leases."""

    def __init__(self, credentials: CredentialStore) -> None:
        self._credentials = credentials
        self._entries: dict[str | tuple[str, str], _TokenRefreshEntry] = {}
        self._lock = threading.Lock()

    def _supervisor_key(self, connector_id: str) -> tuple[str, str]:
        return self._credentials.storage_identity, connector_id

    def _entry_key(self, connector_id: str) -> str | tuple[str, str]:
        from runtime.platform.capabilities.tenant_context import (
            current_capability_scope,
        )

        if current_capability_scope() is None:
            return connector_id
        return self._supervisor_key(connector_id)

    @staticmethod
    def _spec(conn: ConnectorDefinition) -> dict[str, Any]:
        return (conn.cli or {}).get("tokenRefresh") or {}

    def is_scheduled(self, connector_id: str) -> bool:
        with self._lock:
            return self._entry_key(connector_id) in self._entries

    def schedule(
        self,
        conn: ConnectorDefinition,
        *,
        initial_token: str | None = None,
        generation: int | None = None,
    ) -> bool:
        spec = self._spec(conn)
        cmd = cli_lifecycle.resolve_cmd(spec.get("command"))
        if not cmd:
            return False
        with self._credentials.connector_lifecycle(conn.id):
            if generation is None:
                canonical_generation = self._credentials.advance_auth_generation(conn.id)
            else:
                canonical_generation = generation
                if self._credentials.auth_generation(conn.id) != canonical_generation:
                    return False
            cancellation = self._cancel_under_lifecycle(conn.id)
            self._wait_for_lease(cancellation.connector_id, cancellation.lease)
            entry = _TokenRefreshEntry(generation=canonical_generation)
            local_key = self._entry_key(conn.id)
            supervisor_key = self._supervisor_key(conn.id)
            with _refresh_supervisor_lock:
                _refresh_supervisor_entries[supervisor_key] = entry
            with self._lock:
                self._entries[local_key] = entry
            context = contextvars.copy_context()
            worker = threading.Thread(
                target=context.run,
                args=(self._run_loop, conn, entry, initial_token),
                daemon=True,
            )
            entry.worker = worker
            worker.start()
        return True

    def stop(self, connector_id: str, *, fence: bool = True) -> None:
        with self._credentials.connector_lifecycle(connector_id):
            if fence:
                self._credentials.advance_auth_generation(connector_id)
            cancellation = self._cancel_under_lifecycle(connector_id)
        self._wait_for_cancellation(cancellation)

    def stop_all(self) -> None:
        scope = self._credentials.storage_identity
        with _refresh_supervisor_lock:
            connector_ids = {
                connector_id
                for (storage_identity, connector_id) in _refresh_supervisor_entries
                if storage_identity == scope
            }
        with self._lock:
            for key in self._entries:
                if isinstance(key, str):
                    if self._entry_key(key) == key:
                        connector_ids.add(key)
                elif key[0] == scope:
                    connector_ids.add(key[1])
        for connector_id in connector_ids:
            self.stop(connector_id)

    @staticmethod
    def _signal_entry(entry: _TokenRefreshEntry) -> None:
        entry.cancelled.set()
        if entry.timer is not None:
            entry.timer.cancel()
        with entry.child_lock:
            child = entry.child
        if child is not None:
            _reap_process(child)

    def _cancel_under_lifecycle(self, connector_id: str) -> _RefreshCancellation:
        """Signal current work; caller holds the connector lifecycle lock."""

        entries: list[_TokenRefreshEntry] = []
        key = self._supervisor_key(connector_id)
        with _refresh_supervisor_lock:
            shared_entry = _refresh_supervisor_entries.get(key)
        if shared_entry is not None:
            entries.append(shared_entry)
        with self._lock:
            local_entry = self._entries.get(self._entry_key(connector_id))
        if local_entry is not None and all(local_entry is not item for item in entries):
            entries.append(local_entry)
        for entry in entries:
            self._signal_entry(entry)
        return _RefreshCancellation(
            connector_id,
            tuple(entries),
            self._credentials.refresh_lease(connector_id),
        )

    def _wait_for_lease(
        self,
        connector_id: str,
        lease: dict[str, Any] | None,
    ) -> None:
        if not lease:
            return
        lease_nonce = str(lease.get("worker_nonce") or "")
        if not lease_nonce:
            raise RefreshCleanupRequiredError(
                connector_id,
                reason="durable refresh lease has no owner identity",
                lease=lease,
            )
        deadline = time.monotonic() + _REFRESH_WORKER_WAIT_SECONDS
        while True:
            current = self._credentials.refresh_lease(connector_id)
            if current is None or current.get("worker_nonce") != lease_nonce:
                return
            if time.monotonic() >= deadline:
                raise RefreshCleanupRequiredError(
                    connector_id,
                    reason="refresh owner did not acknowledge revocation before timeout",
                    lease=current,
                )
            time.sleep(_REFRESH_POLL_SECONDS)

    def _wait_for_cancellation(self, cancellation: _RefreshCancellation) -> None:
        self._wait_for_entries(cancellation.connector_id, cancellation.entries)
        self._wait_for_lease(cancellation.connector_id, cancellation.lease)

    @staticmethod
    def _wait_for_entries(
        connector_id: str,
        entries: tuple[_TokenRefreshEntry, ...],
    ) -> None:
        deadline = time.monotonic() + _REFRESH_WORKER_WAIT_SECONDS
        for entry in entries:
            worker = entry.worker
            if worker is None or worker is threading.current_thread():
                continue
            worker.join(max(0.0, deadline - time.monotonic()))
            if worker.is_alive():
                with entry.child_lock:
                    child = entry.child
                raise RefreshCleanupRequiredError(
                    connector_id,
                    reason="in-process refresh worker did not stop before timeout",
                    lease={
                        "generation": entry.generation,
                        "owner_pid": os.getpid(),
                        "child_pid": int(child.pid) if child is not None else 0,
                        "started_at": 0.0,
                    },
                )

    def _run_loop(
        self,
        conn: ConnectorDefinition,
        entry: _TokenRefreshEntry,
        initial_token: str | None,
    ) -> None:
        local_key = self._entry_key(conn.id)
        supervisor_key = self._supervisor_key(conn.id)
        try:
            delay = self._refresh_once(conn, entry, initial_token=initial_token)
            while delay is not None:
                with self._lock:
                    if self._entries.get(local_key) is not entry or entry.cancelled.is_set():
                        return
                    timer = threading.Timer(delay, self._fire)
                    entry.timer = timer
                timer.start()
                timer.join()
                with self._lock:
                    if self._entries.get(local_key) is not entry or entry.cancelled.is_set():
                        return
                    entry.timer = None
                delay = self._refresh_once(conn, entry)
        finally:
            with self._lock:
                if self._entries.get(local_key) is entry:
                    self._entries.pop(local_key, None)
            with _refresh_supervisor_lock:
                if _refresh_supervisor_entries.get(supervisor_key) is entry:
                    _refresh_supervisor_entries.pop(supervisor_key, None)

    @staticmethod
    def _fire() -> None:
        pass

    def _refresh_once(
        self,
        conn: ConnectorDefinition,
        entry: _TokenRefreshEntry,
        *,
        initial_token: str | None = None,
    ) -> float | None:
        spec = self._spec(conn)
        cmd = cli_lifecycle.resolve_cmd(spec.get("command"))
        if not cmd:
            return None
        if (
            entry.cancelled.is_set()
            or self._credentials.auth_generation(conn.id) != entry.generation
        ):
            return None
        store_key = str(spec.get("storeKey") or "access_token")
        default_ttl = max(30.0, float(spec.get("defaultExpiresInSeconds") or 300))
        token_pattern = str(spec.get("tokenPattern") or "")
        expires_pattern = str(spec.get("expiresInPattern") or "")
        try:
            code, stdout = self._run_refresh_cmd(conn, cmd, entry)
            out = stdout or ""
            if entry.cancelled.is_set() or (
                self._credentials.auth_generation(conn.id) != entry.generation
            ):
                return None
            if code != 0:
                self._log_warn(conn, f"refresh command exited {code}: {out[:120]}")
                return None
            new_token = None
            if token_pattern:
                match = re.search(token_pattern, out)
                if match:
                    new_token = match.group(1) if match.groups() else match.group(0)
            refreshed_token = new_token or initial_token
            if refreshed_token and not self._credentials.set_secret_if_generation(
                conn.id,
                store_key,
                refreshed_token,
                expected_generation=entry.generation,
            ):
                return None
            if entry.cancelled.is_set() or (
                self._credentials.auth_generation(conn.id) != entry.generation
            ):
                return None
            ttl = default_ttl
            if expires_pattern:
                match = re.search(expires_pattern, out)
                if match:
                    ttl = max(
                        30.0,
                        float(match.group(1) if match.groups() else match.group(0)),
                    )
            return ttl
        except Exception as exc:  # noqa: BLE001
            self._log_warn(conn, f"refresh failed: {exc}")
            if entry.cancelled.is_set() or (
                self._credentials.auth_generation(conn.id) != entry.generation
            ):
                return None
            return default_ttl

    def _run_refresh_cmd(
        self,
        conn: ConnectorDefinition,
        cmd: str,
        entry: _TokenRefreshEntry,
    ) -> tuple[int, str | None]:
        proc: Any = None
        registered = False
        try:
            with self._credentials.connector_lifecycle(conn.id):
                if entry.cancelled.is_set() or (
                    self._credentials.auth_generation(conn.id) != entry.generation
                ):
                    return -1, "refresh cancelled before spawn"
                # The refresh command is a third-party vendor CLI. It needs this
                # connector's own credentials (applied verbatim as the overlay)
                # but must not inherit the host's — model API keys, DB
                # passwords — which a plain ``{**os.environ}`` handed over on
                # every renewal for the life of the service.
                env = scrub_credential_env(self._credentials_env(conn))
                started_at = time.time()
                proc = subprocess.Popen(
                    shlex.split(cmd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                )
                with entry.child_lock:
                    entry.child = proc
                registered = self._credentials.register_refresh_lease(
                    conn.id,
                    expected_generation=entry.generation,
                    worker_nonce=entry.worker_nonce,
                    child_pid=int(proc.pid),
                    started_at=started_at,
                )
                if not registered:
                    _reap_process(proc)
                    return -1, "refresh generation or process lease was replaced"

            deadline = time.monotonic() + _REFRESH_COMMAND_TIMEOUT_SECONDS
            while True:
                if entry.cancelled.is_set() or (
                    self._credentials.auth_generation(conn.id) != entry.generation
                ):
                    _reap_process(proc)
                    return -1, "refresh cancelled"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _reap_process(proc)
                    return -1, "refresh command timed out"
                try:
                    stdout, _stderr = proc.communicate(
                        timeout=min(_REFRESH_POLL_SECONDS, remaining)
                    )
                    return int(proc.returncode or 0), stdout or ""
                except subprocess.TimeoutExpired:
                    continue
        except Exception as exc:  # noqa: BLE001
            if proc is not None:
                _reap_process(proc)
            return -1, str(exc)
        finally:
            if registered:
                self._credentials.clear_refresh_lease(
                    conn.id,
                    worker_nonce=entry.worker_nonce,
                )
            if proc is not None:
                with entry.child_lock:
                    if entry.child is proc:
                        entry.child = None
                for stream in (proc.stdout, proc.stderr):
                    if stream is not None:
                        with contextlib.suppress(Exception):
                            stream.close()

    def _credentials_env(self, conn: ConnectorDefinition) -> dict[str, str]:
        env: dict[str, str] = {}
        for key in self._credentials.list_secrets(conn.id):
            if key.startswith(_ALLOWED_ENV_PREFIX) or key in {"access_token", "api_key"}:
                value = self._credentials.get_secret(conn.id, key)
                if value:
                    env[key] = value
        return env

    @staticmethod
    def _log_warn(conn: ConnectorDefinition, message: str) -> None:
        logging.getLogger("connectors.refresh").warning(
            "[token-refresh] %s (%s): %s", conn.name, conn.id, message
        )


__all__ = ["ConnectorTokenRefresher", "RefreshCleanupRequiredError"]
