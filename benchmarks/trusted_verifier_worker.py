"""Trusted per-call driver and isolated candidate API process.

The host-side :func:`run_trusted_supervisor` owns the controller descriptor,
the complete challenge, operation identities, and aggregation.  The process
inside the candidate sandbox is only an API server: it sees one opaque call at
a time over FD 3 and can return only a result for a driver-issued capability.
It never receives the controller nonce/protocol, operation IDs, the complete
challenge, or an aggregate/result/verdict capability.
"""

from __future__ import annotations

import argparse
import builtins
import importlib.util
import json
import os
import queue
import secrets
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any, NoReturn

PROTOCOL_VERSION = 1
CANDIDATE_API_ISOLATION_SCHEMA = "echo.candidate_api_process.v1"
MAX_FRAME_BYTES = 64 * 1024
MAX_FRAMES = 64
MAX_TEXT_BYTES = 8 * 1024
CANDIDATE_FAILURE_EXIT = 81


class TrustedSupervisorError(RuntimeError):
    """The trusted launcher/controller boundary is invalid."""


class _ProtocolFailure(RuntimeError):
    pass


class _CandidateProtocolFailure(_ProtocolFailure):
    pass


class _ControllerProtocolFailure(TrustedSupervisorError):
    pass


class _SeededLoaderFailure(RuntimeError):
    pass


class _FramedRpc:
    """Candidate-side bounded canonical channel with pre-import bindings."""

    def __init__(self, descriptor: int) -> None:
        self._socket = socket.socket(fileno=descriptor)
        self._socket.settimeout(20.0)
        self._encode = json.JSONEncoder(
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode
        self._decode = json.JSONDecoder(
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        ).decode
        self._header = struct.Struct("!I")
        self._sent = 0
        self._received = 0
        self._send_lock = threading.Lock()

    def close(self) -> None:
        with suppress(OSError):
            self._socket.close()

    def send(self, message: dict[str, Any]) -> None:
        try:
            payload = self._encode(message).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise _ProtocolFailure("candidate API frame could not be encoded") from exc
        if not payload or len(payload) > MAX_FRAME_BYTES:
            raise _ProtocolFailure("candidate API frame exceeds the byte limit")
        with self._send_lock:
            self._sent += 1
            if self._sent > MAX_FRAMES:
                raise _ProtocolFailure("candidate API sent too many frames")
            try:
                self._socket.sendall(self._header.pack(len(payload)) + payload)
            except (OSError, TimeoutError) as exc:
                raise _ProtocolFailure("candidate API channel closed while sending") from exc

    def receive(self) -> dict[str, Any]:
        header = self._read_exact(self._header.size)
        (size,) = self._header.unpack(header)
        if size < 2 or size > MAX_FRAME_BYTES:
            raise _ProtocolFailure("candidate API frame has an invalid length")
        payload = self._read_exact(size)
        try:
            text = payload.decode("utf-8", errors="strict")
            decoded = self._decode(text)
            canonical = self._encode(decoded).encode("utf-8")
        except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise _ProtocolFailure("candidate API frame is not canonical JSON") from exc
        if not isinstance(decoded, dict) or canonical != payload:
            raise _ProtocolFailure("candidate API frame is not a canonical JSON object")
        self._received += 1
        if self._received > MAX_FRAMES:
            raise _ProtocolFailure("candidate API received too many frames")
        return decoded

    def _read_exact(self, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            try:
                chunk = self._socket.recv(remaining)
            except (OSError, TimeoutError) as exc:
                raise _ProtocolFailure("candidate API frame timed out") from exc
            if not chunk:
                raise _ProtocolFailure("candidate API channel closed early")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


class _SupervisorChannel:
    """A bounded canonical channel on one side of the trusted host driver."""

    def __init__(self, descriptor: int, *, peer: str, timeout_seconds: float) -> None:
        failure_type: type[RuntimeError]
        if peer == "candidate":
            failure_type = _CandidateProtocolFailure
        elif peer == "controller":
            failure_type = _ControllerProtocolFailure
        else:  # pragma: no cover - trusted caller programming error
            raise ValueError(f"unsupported supervisor peer: {peer}")
        self._failure_type = failure_type
        try:
            os.set_inheritable(descriptor, False)
            self._socket = socket.socket(fileno=descriptor)
            self._socket.settimeout(timeout_seconds)
        except (OSError, ValueError) as exc:
            raise failure_type(f"{peer} protocol descriptor is unavailable") from exc
        self._encode = json.JSONEncoder(
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode
        self._decode = json.JSONDecoder(
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        ).decode
        self._header = struct.Struct("!I")
        self._sent = 0
        self._received = 0
        self._peer = peer

    def close(self) -> None:
        with suppress(OSError):
            self._socket.close()

    def send(self, message: dict[str, Any]) -> None:
        try:
            payload = self._encode(message).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise self._failure_type(f"trusted {self._peer} frame could not be encoded") from exc
        if not payload or len(payload) > MAX_FRAME_BYTES:
            raise self._failure_type(f"trusted {self._peer} frame exceeds the byte limit")
        self._sent += 1
        if self._sent > MAX_FRAMES:
            raise self._failure_type(f"trusted driver sent too many {self._peer} frames")
        try:
            self._socket.sendall(self._header.pack(len(payload)) + payload)
        except (OSError, TimeoutError) as exc:
            raise self._failure_type(f"{self._peer} protocol channel closed while sending") from exc

    def receive(self) -> dict[str, Any]:
        header = self._read_exact(self._header.size)
        (size,) = self._header.unpack(header)
        if size < 2 or size > MAX_FRAME_BYTES:
            raise self._failure_type(f"{self._peer} protocol frame has an invalid length")
        payload = self._read_exact(size)
        try:
            text = payload.decode("utf-8", errors="strict")
            decoded = self._decode(text)
            canonical = self._encode(decoded).encode("utf-8")
        except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise self._failure_type(f"{self._peer} protocol frame is not canonical JSON") from exc
        if not isinstance(decoded, dict) or canonical != payload:
            raise self._failure_type(f"{self._peer} protocol frame is not a canonical JSON object")
        self._received += 1
        if self._received > MAX_FRAMES:
            raise self._failure_type(f"{self._peer} sent too many protocol frames")
        return decoded

    def expect_eof(self) -> None:
        try:
            trailing = self._socket.recv(1)
        except (OSError, TimeoutError) as exc:
            raise self._failure_type(
                f"{self._peer} protocol channel did not close cleanly"
            ) from exc
        if trailing:
            raise self._failure_type(f"{self._peer} sent an extra or replayed protocol frame")

    def _read_exact(self, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            try:
                chunk = self._socket.recv(remaining)
            except (OSError, TimeoutError) as exc:
                raise self._failure_type(f"{self._peer} protocol frame timed out") from exc
            if not chunk:
                raise self._failure_type(f"{self._peer} protocol channel closed early")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _require_exact_keys(message: dict[str, Any], expected: set[str]) -> None:
    if set(message) != expected:
        raise _ProtocolFailure(f"unexpected frame fields: {sorted(set(message) ^ expected)}")


def _load_candidate(workspace: Path, module_name: str, filename: str) -> Any:
    candidate_path = workspace / filename
    spec = importlib.util.spec_from_file_location(module_name, candidate_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def _bounded_text(value: Any) -> str:
    rendered = str(value)
    encoded = rendered.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_TEXT_BYTES:
        return rendered
    return encoded[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore")


def _exception_record(exc: BaseException) -> dict[str, str]:
    return {
        "message": _bounded_text(exc),
        "module": _bounded_text(type(exc).__module__),
        "name": _bounded_text(type(exc).__name__),
    }


def _is_nonce(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_version(value: Any) -> bool:
    return type(value) is int and value == PROTOCOL_VERSION


def _encoded_text_is_bounded(value: Any, *, allow_empty: bool = True) -> bool:
    if not isinstance(value, str) or (not allow_empty and not value):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= MAX_TEXT_BYTES
    except UnicodeEncodeError:
        return False


def _validate_api_identity(message: dict[str, Any], *, session: str) -> None:
    if not _is_version(message.get("version")) or message.get("session") != session:
        raise _ProtocolFailure("candidate API message is outside the active session")


def _candidate_path_api(
    rpc: _FramedRpc,
    *,
    init: dict[str, Any],
    workspace: Path,
    challenge_root: Path | None,
) -> int:
    _require_exact_keys(
        init,
        {"case_id", "kind", "root_relative", "schema", "session", "version"},
    )
    session = init["session"]
    root_relative = init["root_relative"]
    if (
        init["kind"] != "api_init"
        or init["schema"] != CANDIDATE_API_ISOLATION_SCHEMA
        or init["case_id"] != "coding.path-boundary"
        or not _is_version(init["version"])
        or not _is_nonce(session)
        or not _encoded_text_is_bounded(root_relative, allow_empty=False)
        or challenge_root is None
    ):
        raise _ProtocolFailure("candidate path API init is invalid")

    local_getattr = builtins.getattr
    module = _load_candidate(workspace, "candidate_file_service", "file_service.py")
    service_type = local_getattr(module, "FileService")
    service = service_type(challenge_root / root_relative)
    read_text = local_getattr(service, "read_text")
    rpc.send({"kind": "api_ready", "session": session, "version": PROTOCOL_VERSION})
    seen_requests: set[str] = set()
    seen_capabilities: set[str] = set()
    while True:
        message = rpc.receive()
        kind = message.get("kind")
        if kind == "api_shutdown":
            _require_exact_keys(message, {"kind", "session", "version"})
            _validate_api_identity(message, session=session)
            rpc.send({"kind": "api_complete", "session": session, "version": PROTOCOL_VERSION})
            return 0
        if kind != "api_path_call":
            raise _ProtocolFailure(f"candidate path API received forbidden frame kind {kind!r}")
        _require_exact_keys(
            message,
            {"capability", "kind", "request_id", "session", "user_path", "version"},
        )
        _validate_api_identity(message, session=session)
        request_id = message["request_id"]
        capability = message["capability"]
        user_path = message["user_path"]
        if (
            not _is_nonce(request_id)
            or request_id in seen_requests
            or not _is_nonce(capability)
            or capability in seen_capabilities
            or not _encoded_text_is_bounded(user_path)
        ):
            raise _ProtocolFailure("candidate path API call is invalid or replayed")
        seen_requests.add(request_id)
        seen_capabilities.add(capability)
        try:
            value = read_text(user_path)
        except BaseException as exc:
            result: dict[str, Any] = {
                "capability": capability,
                "exception": _exception_record(exc),
                "kind": "api_path_result",
                "outcome": "exception",
                "request_id": request_id,
                "session": session,
                "version": PROTOCOL_VERSION,
            }
        else:
            result = {
                "capability": capability,
                "kind": "api_path_result",
                "outcome": "return",
                "request_id": request_id,
                "session": session,
                "value": _bounded_text(value),
                "version": PROTOCOL_VERSION,
            }
        rpc.send(result)


class _CandidateCacheApi:
    """Candidate-side concurrent call server; only the main thread reads FD 3."""

    def __init__(self, rpc: _FramedRpc, *, session: str) -> None:
        self._rpc = rpc
        self._session = session
        self._cache: Any = None
        self._local = threading.local()
        self._state_lock = threading.Lock()
        self._pending: dict[tuple[str, str], queue.Queue[dict[str, Any]]] = {}
        self._active: set[str] = set()
        self._seen_requests: set[str] = set()
        self._seen_tokens: set[str] = set()
        self._threads: list[threading.Thread] = []

    def set_cache(self, cache: Any) -> None:
        self._cache = cache

    def clock(self) -> float:
        request_id = getattr(self._local, "request_id", None)
        if not isinstance(request_id, str):
            raise _ProtocolFailure("candidate clock was called outside an API call")
        response = self._reverse(
            {
                "kind": "api_clock_request",
                "request_id": request_id,
                "session": self._session,
                "version": PROTOCOL_VERSION,
            },
            response_kind="api_clock_response",
        )
        _require_exact_keys(
            response,
            {"kind", "request_id", "session", "value", "version"},
        )
        _validate_api_identity(response, session=self._session)
        value = response["value"]
        if (
            response["request_id"] != request_id
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
        ):
            raise _ProtocolFailure("candidate clock response is invalid")
        return float(value)

    def loader(self) -> str:
        request_id = getattr(self._local, "request_id", None)
        loader_token = getattr(self._local, "loader_token", None)
        if not isinstance(request_id, str) or not isinstance(loader_token, str):
            raise _ProtocolFailure("candidate loader was called outside an API call")
        response = self._reverse(
            {
                "kind": "api_loader_request",
                "loader_token": loader_token,
                "request_id": request_id,
                "session": self._session,
                "version": PROTOCOL_VERSION,
            },
            response_kind="api_loader_response",
        )
        _require_exact_keys(
            response,
            {
                "action",
                "kind",
                "loader_token",
                "request_id",
                "session",
                "value",
                "version",
            },
        )
        _validate_api_identity(response, session=self._session)
        if (
            response["request_id"] != request_id
            or response["loader_token"] != loader_token
            or response["action"] not in {"return", "raise"}
            or not _encoded_text_is_bounded(response["value"])
        ):
            raise _ProtocolFailure("candidate loader response is invalid")
        if response["action"] == "raise":
            raise _SeededLoaderFailure(response["value"])
        return response["value"]

    def _reverse(
        self,
        request: dict[str, Any],
        *,
        response_kind: str,
    ) -> dict[str, Any]:
        request_id = request["request_id"]
        key = (response_kind, request_id)
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._state_lock:
            if key in self._pending:
                raise _ProtocolFailure("candidate API reverse request was replayed")
            self._pending[key] = response_queue
        try:
            self._rpc.send(request)
            try:
                return response_queue.get(timeout=20.0)
            except queue.Empty as exc:
                raise _ProtocolFailure("candidate API reverse request timed out") from exc
        finally:
            with self._state_lock:
                self._pending.pop(key, None)

    def route_response(self, message: dict[str, Any]) -> None:
        kind = message.get("kind")
        request_id = message.get("request_id")
        if kind not in {"api_clock_response", "api_loader_response"}:
            raise _ProtocolFailure(f"candidate cache API received forbidden frame kind {kind!r}")
        if not isinstance(request_id, str):
            raise _ProtocolFailure("candidate API reverse response has no request identity")
        key = (kind, request_id)
        with self._state_lock:
            target = self._pending.get(key)
        if target is None:
            raise _ProtocolFailure("candidate API reverse response is unknown or replayed")
        try:
            target.put_nowait(message)
        except queue.Full as exc:
            raise _ProtocolFailure("candidate API reverse response was replayed") from exc

    def launch_call(self, message: dict[str, Any]) -> None:
        _require_exact_keys(
            message,
            {"key", "kind", "loader_token", "request_id", "session", "version"},
        )
        _validate_api_identity(message, session=self._session)
        request_id = message["request_id"]
        loader_token = message["loader_token"]
        key = message["key"]
        if (
            not _is_nonce(request_id)
            or not _is_nonce(loader_token)
            or not _encoded_text_is_bounded(key, allow_empty=False)
        ):
            raise _ProtocolFailure("candidate cache API call is invalid")
        with self._state_lock:
            if request_id in self._seen_requests or loader_token in self._seen_tokens:
                raise _ProtocolFailure("candidate cache API call is replayed")
            self._seen_requests.add(request_id)
            self._seen_tokens.add(loader_token)
            self._active.add(request_id)
        thread = threading.Thread(
            target=self._execute_call,
            args=(request_id, loader_token, key),
            name="echo-candidate-api-call",
            daemon=True,
        )
        self._threads.append(thread)
        thread.start()

    def _execute_call(self, request_id: str, loader_token: str, key: str) -> None:
        self._local.request_id = request_id
        self._local.loader_token = loader_token
        try:
            value = self._cache.get_or_load(key, self.loader)
        except BaseException as exc:
            result: dict[str, Any] = {
                "exception": _exception_record(exc),
                "kind": "api_cache_result",
                "loader_token": loader_token,
                "outcome": "exception",
                "request_id": request_id,
                "session": self._session,
                "version": PROTOCOL_VERSION,
            }
        else:
            result = {
                "kind": "api_cache_result",
                "loader_token": loader_token,
                "outcome": "return",
                "request_id": request_id,
                "session": self._session,
                "value": _bounded_text(value),
                "version": PROTOCOL_VERSION,
            }
        finally:
            with self._state_lock:
                self._active.discard(request_id)
        self._rpc.send(result)

    def require_idle(self) -> None:
        with self._state_lock:
            if self._active or self._pending:
                raise _ProtocolFailure("candidate cache API shut down with active calls")
        for thread in self._threads:
            thread.join(timeout=0.1)
            if thread.is_alive():
                raise _ProtocolFailure("candidate cache API thread did not terminate")


def _candidate_cache_api(
    rpc: _FramedRpc,
    *,
    init: dict[str, Any],
    workspace: Path,
) -> int:
    _require_exact_keys(
        init,
        {"kind", "schema", "session", "ttl_seconds", "version"},
    )
    session = init["session"]
    ttl_seconds = init["ttl_seconds"]
    if (
        init["kind"] != "api_cache_init"
        or init["schema"] != CANDIDATE_API_ISOLATION_SCHEMA
        or not _is_version(init["version"])
        or not _is_nonce(session)
        or isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, (int, float))
    ):
        raise _ProtocolFailure("candidate cache API init is invalid")
    api = _CandidateCacheApi(rpc, session=session)
    local_getattr = builtins.getattr
    module = _load_candidate(workspace, "candidate_cache", "cache.py")
    cache_type = local_getattr(module, "TTLCache")
    cache = cache_type(float(ttl_seconds), clock=api.clock)
    api.set_cache(cache)
    rpc.send({"kind": "api_ready", "session": session, "version": PROTOCOL_VERSION})
    while True:
        message = rpc.receive()
        kind = message.get("kind")
        if kind == "api_cache_call":
            api.launch_call(message)
            continue
        if kind in {"api_clock_response", "api_loader_response"}:
            api.route_response(message)
            continue
        if kind == "api_shutdown":
            _require_exact_keys(message, {"kind", "session", "version"})
            _validate_api_identity(message, session=session)
            api.require_idle()
            rpc.send({"kind": "api_complete", "session": session, "version": PROTOCOL_VERSION})
            return 0
        raise _ProtocolFailure(f"candidate cache API received forbidden frame kind {kind!r}")


def candidate_main(
    candidate_protocol_fd: int,
    workspace: Path,
    challenge_root: Path | None,
) -> int:
    """Serve only the isolated per-call candidate API on the sandbox FD."""

    sys.dont_write_bytecode = True
    rpc = _FramedRpc(candidate_protocol_fd)
    session = ""
    try:
        init = rpc.receive()
        observed_session = init.get("session")
        if isinstance(observed_session, str):
            session = observed_session
        kind = init.get("kind")
        if kind == "api_init":
            return _candidate_path_api(
                rpc,
                init=init,
                workspace=workspace,
                challenge_root=challenge_root,
            )
        if kind == "api_cache_init":
            return _candidate_cache_api(rpc, init=init, workspace=workspace)
        raise _ProtocolFailure(f"candidate API received forbidden init frame kind {kind!r}")
    except BaseException as exc:
        with suppress(BaseException):
            rpc.send(
                {
                    "error": _exception_record(exc),
                    "kind": "api_error",
                    "session": session,
                    "version": PROTOCOL_VERSION,
                }
            )
        return 1
    finally:
        rpc.close()


def _peer_exact_keys(
    message: dict[str, Any],
    expected: set[str],
    *,
    candidate: bool,
) -> None:
    if set(message) == expected:
        return
    error_type = _CandidateProtocolFailure if candidate else _ControllerProtocolFailure
    raise error_type(f"unexpected {'candidate' if candidate else 'controller'} frame fields")


def _validate_controller_start(message: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    _peer_exact_keys(
        message,
        {"case_id", "challenge", "kind", "run_nonce", "version"},
        candidate=False,
    )
    case_id = message["case_id"]
    run_nonce = message["run_nonce"]
    challenge = message["challenge"]
    if (
        message["kind"] != "start"
        or not _is_version(message["version"])
        or not isinstance(case_id, str)
        or case_id not in {"coding.path-boundary", "coding.concurrent-cache"}
        or not _is_nonce(run_nonce)
        or not isinstance(challenge, dict)
    ):
        raise _ControllerProtocolFailure("controller start frame is invalid")
    if case_id == "coding.path-boundary":
        _validate_path_challenge(challenge)
    else:
        _validate_cache_challenge(challenge)
    return case_id, run_nonce, challenge


def _validate_path_challenge(challenge: dict[str, Any]) -> None:
    _peer_exact_keys(challenge, {"operations", "root_relative"}, candidate=False)
    root_relative = challenge["root_relative"]
    operations = challenge["operations"]
    if not _encoded_text_is_bounded(root_relative, allow_empty=False):
        raise _ControllerProtocolFailure("controller path root is invalid")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 16:
        raise _ControllerProtocolFailure("controller path operations are invalid")
    seen: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            raise _ControllerProtocolFailure("controller path operation is not an object")
        _peer_exact_keys(operation, {"op_id", "user_path"}, candidate=False)
        op_id = operation["op_id"]
        user_path = operation["user_path"]
        if (
            not _encoded_text_is_bounded(op_id, allow_empty=False)
            or op_id in seen
            or not _encoded_text_is_bounded(user_path)
        ):
            raise _ControllerProtocolFailure("controller path operation is invalid")
        seen.add(op_id)


def _validate_cache_challenge(challenge: dict[str, Any]) -> None:
    _peer_exact_keys(
        challenge,
        {
            "clock_expired",
            "clock_initial",
            "clock_live",
            "failure_key",
            "shared_key",
            "thread_count",
            "ttl_seconds",
        },
        candidate=False,
    )
    thread_count = challenge["thread_count"]
    if (
        isinstance(thread_count, bool)
        or not isinstance(thread_count, int)
        or not 2 <= thread_count <= 16
    ):
        raise _ControllerProtocolFailure("controller cache thread count is invalid")
    for name in ("shared_key", "failure_key"):
        if not _encoded_text_is_bounded(challenge[name], allow_empty=False):
            raise _ControllerProtocolFailure(f"controller cache {name} is invalid")
    for name in ("clock_initial", "clock_live", "clock_expired", "ttl_seconds"):
        value = challenge[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _ControllerProtocolFailure(f"controller cache {name} is invalid")


def _validated_exception_record(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise _CandidateProtocolFailure("candidate exception record is not an object")
    _peer_exact_keys(value, {"message", "module", "name"}, candidate=True)
    for name in ("message", "module", "name"):
        if not _encoded_text_is_bounded(value[name]):
            raise _CandidateProtocolFailure("candidate exception record is invalid")
    return {
        "message": value["message"],
        "module": value["module"],
        "name": value["name"],
    }


def _validated_call_observation(value: dict[str, Any], *, identity_field: str) -> dict[str, Any]:
    outcome = value.get("outcome")
    common = {
        identity_field,
        "kind",
        "outcome",
        "request_id",
        "session",
        "version",
    }
    if outcome == "return":
        _peer_exact_keys(value, {"value", *common}, candidate=True)
        if not _encoded_text_is_bounded(value["value"]):
            raise _CandidateProtocolFailure("candidate return observation is invalid")
        return {"outcome": "return", "value": value["value"]}
    if outcome == "exception":
        _peer_exact_keys(value, {"exception", *common}, candidate=True)
        return {
            "exception": _validated_exception_record(value["exception"]),
            "outcome": "exception",
        }
    raise _CandidateProtocolFailure("candidate observation outcome is invalid")


def _candidate_forbidden(kind: Any) -> NoReturn:
    raise _CandidateProtocolFailure(
        f"candidate sent forbidden frame kind {kind!r} on per-call API channel"
    )


def _candidate_error_record(message: dict[str, Any], *, session: str) -> dict[str, str]:
    _peer_exact_keys(
        message,
        {"error", "kind", "session", "version"},
        candidate=True,
    )
    if (
        message["kind"] != "api_error"
        or message["session"] != session
        or not _is_version(message["version"])
    ):
        raise _CandidateProtocolFailure("candidate API error is outside the active session")
    return _validated_exception_record(message["error"])


def _receive_api_ready(candidate: _SupervisorChannel, *, session: str) -> None:
    message = candidate.receive()
    kind = message.get("kind")
    if kind == "api_error":
        error = _candidate_error_record(message, session=session)
        raise _CandidateProtocolFailure(
            f"candidate API initialization failed: {error['name']}: {error['message']}"
        )
    if kind != "api_ready":
        _candidate_forbidden(kind)
    _peer_exact_keys(message, {"kind", "session", "version"}, candidate=True)
    if message["session"] != session or not _is_version(message["version"]):
        raise _CandidateProtocolFailure("candidate API ready frame changed session")


def _finish_candidate_api(candidate: _SupervisorChannel, *, session: str) -> None:
    candidate.send({"kind": "api_shutdown", "session": session, "version": PROTOCOL_VERSION})
    message = candidate.receive()
    kind = message.get("kind")
    if kind == "api_error":
        error = _candidate_error_record(message, session=session)
        raise _CandidateProtocolFailure(
            f"candidate API shutdown failed: {error['name']}: {error['message']}"
        )
    if kind != "api_complete":
        _candidate_forbidden(kind)
    _peer_exact_keys(message, {"kind", "session", "version"}, candidate=True)
    if message["session"] != session or not _is_version(message["version"]):
        raise _CandidateProtocolFailure("candidate API completion changed session")
    candidate.expect_eof()


def _validated_api_result(
    message: dict[str, Any],
    *,
    expected_kind: str,
    identity_field: str,
    session: str,
    pending: dict[str, dict[str, Any]],
    completed: set[str],
) -> tuple[str, dict[str, Any]]:
    kind = message.get("kind")
    if kind != expected_kind:
        _candidate_forbidden(kind)
    request_id = message.get("request_id")
    if not isinstance(request_id, str):
        raise _CandidateProtocolFailure("candidate API result has no request identity")
    if request_id in completed:
        raise _CandidateProtocolFailure("candidate API result was replayed")
    expected = pending.get(request_id)
    if expected is None:
        raise _CandidateProtocolFailure("candidate API result identity is unknown")
    if (
        message.get("session") != session
        or not _is_version(message.get("version"))
        or message.get(identity_field) != expected[identity_field]
    ):
        raise _CandidateProtocolFailure("candidate API result changed its capability")
    result = _validated_call_observation(message, identity_field=identity_field)
    completed.add(request_id)
    pending.pop(request_id)
    return request_id, result


def _run_path_driver(
    candidate: _SupervisorChannel,
    *,
    case_id: str,
    run_nonce: str,
    challenge: dict[str, Any],
) -> dict[str, Any]:
    session = secrets.token_hex(32)
    candidate.send(
        {
            "case_id": case_id,
            "kind": "api_init",
            "root_relative": challenge["root_relative"],
            "schema": CANDIDATE_API_ISOLATION_SCHEMA,
            "session": session,
            "version": PROTOCOL_VERSION,
        }
    )
    _receive_api_ready(candidate, session=session)
    operations = list(challenge["operations"])
    secrets.SystemRandom().shuffle(operations)
    by_op_id: dict[str, dict[str, Any]] = {}
    pending: dict[str, dict[str, Any]] = {}
    completed: set[str] = set()
    request_to_op: dict[str, str] = {}
    for operation in operations:
        request_id = secrets.token_hex(32)
        capability = secrets.token_hex(32)
        pending[request_id] = {"capability": capability}
        request_to_op[request_id] = operation["op_id"]
        candidate.send(
            {
                "capability": capability,
                "kind": "api_path_call",
                "request_id": request_id,
                "session": session,
                "user_path": operation["user_path"],
                "version": PROTOCOL_VERSION,
            }
        )
        message = candidate.receive()
        if message.get("kind") == "api_error":
            error = _candidate_error_record(message, session=session)
            raise _CandidateProtocolFailure(
                f"candidate path API failed: {error['name']}: {error['message']}"
            )
        observed_request, result = _validated_api_result(
            message,
            expected_kind="api_path_result",
            identity_field="capability",
            session=session,
            pending=pending,
            completed=completed,
        )
        op_id = request_to_op.pop(observed_request)
        by_op_id[op_id] = {"op_id": op_id, **result}
    _finish_candidate_api(candidate, session=session)
    expected_ids = [operation["op_id"] for operation in challenge["operations"]]
    return {
        "case_id": case_id,
        "kind": "raw_outcome",
        "operations": [by_op_id[op_id] for op_id in expected_ids],
        "run_nonce": run_nonce,
        "version": PROTOCOL_VERSION,
    }


def _validated_clock_request(
    message: dict[str, Any],
    *,
    session: str,
    pending: dict[str, dict[str, Any]],
) -> tuple[str, float]:
    _peer_exact_keys(
        message,
        {"kind", "request_id", "session", "version"},
        candidate=True,
    )
    request_id = message["request_id"]
    if (
        message["kind"] != "api_clock_request"
        or message["session"] != session
        or not _is_version(message["version"])
        or not isinstance(request_id, str)
        or request_id not in pending
    ):
        raise _CandidateProtocolFailure("candidate clock request is outside an active call")
    return request_id, pending[request_id]["clock_value"]


def _validated_loader_request(
    message: dict[str, Any],
    *,
    session: str,
    pending: dict[str, dict[str, Any]],
    used_tokens: set[str],
) -> tuple[str, dict[str, Any]]:
    _peer_exact_keys(
        message,
        {"kind", "loader_token", "request_id", "session", "version"},
        candidate=True,
    )
    request_id = message["request_id"]
    loader_token = message["loader_token"]
    expected = pending.get(request_id) if isinstance(request_id, str) else None
    if (
        message["kind"] != "api_loader_request"
        or message["session"] != session
        or not _is_version(message["version"])
        or expected is None
        or loader_token != expected["loader_token"]
        or loader_token in used_tokens
    ):
        raise _CandidateProtocolFailure("candidate loader capability is invalid or replayed")
    used_tokens.add(loader_token)
    return request_id, expected


def _validated_controller_loader_response(
    message: dict[str, Any],
    *,
    request_id: str,
    run_nonce: str,
) -> tuple[str, str]:
    _peer_exact_keys(
        message,
        {"action", "kind", "request_id", "run_nonce", "value", "version"},
        candidate=False,
    )
    if (
        message["kind"] != "loader_response"
        or message["request_id"] != request_id
        or message["run_nonce"] != run_nonce
        or not _is_version(message["version"])
        or not isinstance(message["action"], str)
        or message["action"] not in {"return", "raise"}
        or not _encoded_text_is_bounded(message["value"])
    ):
        raise _ControllerProtocolFailure("controller loader response is invalid")
    return message["action"], message["value"]


def _collect_cache_results(
    candidate: _SupervisorChannel,
    controller: _SupervisorChannel,
    *,
    session: str,
    run_nonce: str,
    pending: dict[str, dict[str, Any]],
    completed: set[str],
    used_loader_tokens: set[str],
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    while pending:
        message = candidate.receive()
        kind = message.get("kind")
        if kind == "api_error":
            error = _candidate_error_record(message, session=session)
            raise _CandidateProtocolFailure(
                f"candidate cache API failed: {error['name']}: {error['message']}"
            )
        if kind == "api_clock_request":
            request_id, clock_value = _validated_clock_request(
                message,
                session=session,
                pending=pending,
            )
            candidate.send(
                {
                    "kind": "api_clock_response",
                    "request_id": request_id,
                    "session": session,
                    "value": clock_value,
                    "version": PROTOCOL_VERSION,
                }
            )
            continue
        if kind == "api_loader_request":
            request_id, expected = _validated_loader_request(
                message,
                session=session,
                pending=pending,
                used_tokens=used_loader_tokens,
            )
            outer_request_id = secrets.token_hex(32)
            controller.send(
                {
                    "kind": "loader_request",
                    "loader_id": expected["loader_id"],
                    "request_id": outer_request_id,
                    "run_nonce": run_nonce,
                    "version": PROTOCOL_VERSION,
                }
            )
            action, loader_value = _validated_controller_loader_response(
                controller.receive(),
                request_id=outer_request_id,
                run_nonce=run_nonce,
            )
            candidate.send(
                {
                    "action": action,
                    "kind": "api_loader_response",
                    "loader_token": expected["loader_token"],
                    "request_id": request_id,
                    "session": session,
                    "value": loader_value,
                    "version": PROTOCOL_VERSION,
                }
            )
            continue
        request_id, result = _validated_api_result(
            message,
            expected_kind="api_cache_result",
            identity_field="loader_token",
            session=session,
            pending=pending,
            completed=completed,
        )
        results[request_id] = result
    return results


def _issue_cache_call(
    candidate: _SupervisorChannel,
    *,
    session: str,
    key: str,
    loader_id: str,
    clock_value: float,
    pending: dict[str, dict[str, Any]],
) -> str:
    request_id = secrets.token_hex(32)
    loader_token = secrets.token_hex(32)
    pending[request_id] = {
        "clock_value": clock_value,
        "loader_id": loader_id,
        "loader_token": loader_token,
    }
    candidate.send(
        {
            "key": key,
            "kind": "api_cache_call",
            "loader_token": loader_token,
            "request_id": request_id,
            "session": session,
            "version": PROTOCOL_VERSION,
        }
    )
    return request_id


def _run_cache_driver(
    candidate: _SupervisorChannel,
    controller: _SupervisorChannel,
    *,
    case_id: str,
    run_nonce: str,
    challenge: dict[str, Any],
) -> dict[str, Any]:
    session = secrets.token_hex(32)
    candidate.send(
        {
            "kind": "api_cache_init",
            "schema": CANDIDATE_API_ISOLATION_SCHEMA,
            "session": session,
            "ttl_seconds": challenge["ttl_seconds"],
            "version": PROTOCOL_VERSION,
        }
    )
    _receive_api_ready(candidate, session=session)
    completed: set[str] = set()
    used_loader_tokens: set[str] = set()

    pending: dict[str, dict[str, Any]] = {}
    concurrent_ids = [
        _issue_cache_call(
            candidate,
            session=session,
            key=challenge["shared_key"],
            loader_id="shared",
            clock_value=float(challenge["clock_initial"]),
            pending=pending,
        )
        for _ in range(challenge["thread_count"])
    ]
    concurrent_results = _collect_cache_results(
        candidate,
        controller,
        session=session,
        run_nonce=run_nonce,
        pending=pending,
        completed=completed,
        used_loader_tokens=used_loader_tokens,
    )

    def one_call(*, key: str, loader_id: str, clock_value: float) -> dict[str, Any]:
        one_pending: dict[str, dict[str, Any]] = {}
        request_id = _issue_cache_call(
            candidate,
            session=session,
            key=key,
            loader_id=loader_id,
            clock_value=clock_value,
            pending=one_pending,
        )
        return _collect_cache_results(
            candidate,
            controller,
            session=session,
            run_nonce=run_nonce,
            pending=one_pending,
            completed=completed,
            used_loader_tokens=used_loader_tokens,
        )[request_id]

    live = one_call(
        key=challenge["shared_key"],
        loader_id="live_trap",
        clock_value=float(challenge["clock_live"]),
    )
    expired = one_call(
        key=challenge["shared_key"],
        loader_id="expired",
        clock_value=float(challenge["clock_expired"]),
    )
    failure = one_call(
        key=challenge["failure_key"],
        loader_id="failure",
        clock_value=float(challenge["clock_expired"]),
    )
    recovery = one_call(
        key=challenge["failure_key"],
        loader_id="recovery",
        clock_value=float(challenge["clock_expired"]),
    )
    _finish_candidate_api(candidate, session=session)
    return {
        "case_id": case_id,
        "concurrent": [concurrent_results[request_id] for request_id in concurrent_ids],
        "expired": expired,
        "failure": failure,
        "kind": "raw_outcome",
        "live": live,
        "recovery": recovery,
        "run_nonce": run_nonce,
        "version": PROTOCOL_VERSION,
    }


def _synthesized_candidate_error(exc: BaseException) -> dict[str, str]:
    return {
        "message": _bounded_text(exc),
        "module": "trusted_verifier_worker",
        "name": "CandidateProtocolFailure",
    }


def _send_controller_worker_error(
    controller: _SupervisorChannel,
    error: dict[str, str],
) -> None:
    controller.send({"error": error, "kind": "worker_error", "version": PROTOCOL_VERSION})


def _borrowed_channel(
    descriptor: int,
    *,
    peer: str,
    timeout_seconds: float,
) -> _SupervisorChannel:
    try:
        duplicate = os.dup(descriptor)
    except OSError as exc:
        raise TrustedSupervisorError(f"{peer} protocol descriptor is invalid") from exc
    try:
        return _SupervisorChannel(
            duplicate,
            peer=peer,
            timeout_seconds=timeout_seconds,
        )
    except BaseException:
        with suppress(OSError):
            os.close(duplicate)
        raise


def _validate_supervisor_descriptors(
    controller_protocol_fd: int,
    candidate_protocol_fd: int,
) -> None:
    if (
        isinstance(controller_protocol_fd, bool)
        or isinstance(candidate_protocol_fd, bool)
        or not isinstance(controller_protocol_fd, int)
        or not isinstance(candidate_protocol_fd, int)
        or controller_protocol_fd < 3
        or candidate_protocol_fd < 3
        or controller_protocol_fd == candidate_protocol_fd
    ):
        raise TrustedSupervisorError("supervisor protocol descriptors are invalid or aliased")
    try:
        controller_stat = os.fstat(controller_protocol_fd)
        candidate_stat = os.fstat(candidate_protocol_fd)
    except OSError as exc:
        raise TrustedSupervisorError("supervisor protocol descriptor is unavailable") from exc
    if not stat.S_ISSOCK(controller_stat.st_mode) or not stat.S_ISSOCK(candidate_stat.st_mode):
        raise TrustedSupervisorError("supervisor protocols must be socket descriptors")
    if (controller_stat.st_dev, controller_stat.st_ino) == (
        candidate_stat.st_dev,
        candidate_stat.st_ino,
    ):
        raise TrustedSupervisorError("supervisor protocol descriptors alias one socket")
    for descriptor in (controller_protocol_fd, candidate_protocol_fd):
        duplicate = os.dup(descriptor)
        try:
            probe = socket.socket(fileno=duplicate)
            duplicate = -1
            try:
                socket_type = probe.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
                if probe.family != socket.AF_UNIX or socket_type != socket.SOCK_STREAM:
                    raise TrustedSupervisorError("supervisor protocols must be Unix stream sockets")
            finally:
                probe.close()
        except OSError as exc:
            raise TrustedSupervisorError("supervisor protocol socket is invalid") from exc
        finally:
            if duplicate >= 0:
                with suppress(OSError):
                    os.close(duplicate)
        try:
            os.set_inheritable(descriptor, False)
        except OSError as exc:
            raise TrustedSupervisorError(
                "supervisor protocol descriptor could not be sealed"
            ) from exc


def run_trusted_supervisor(
    controller_protocol_fd: int,
    candidate_protocol_fd: int,
    *,
    timeout_seconds: float = 20.0,
) -> int:
    """Drive isolated candidate calls and emit the sole aggregate raw outcome."""

    if not isinstance(timeout_seconds, (int, float)) or not 0.1 <= timeout_seconds <= 300:
        raise TrustedSupervisorError("supervisor timeout is outside the fixed safety range")
    _validate_supervisor_descriptors(controller_protocol_fd, candidate_protocol_fd)
    controller = _borrowed_channel(
        controller_protocol_fd,
        peer="controller",
        timeout_seconds=float(timeout_seconds),
    )
    try:
        candidate = _borrowed_channel(
            candidate_protocol_fd,
            peer="candidate",
            timeout_seconds=float(timeout_seconds),
        )
    except BaseException:
        controller.close()
        raise
    try:
        start = controller.receive()
        case_id, run_nonce, challenge = _validate_controller_start(start)
        try:
            if case_id == "coding.path-boundary":
                raw = _run_path_driver(
                    candidate,
                    case_id=case_id,
                    run_nonce=run_nonce,
                    challenge=challenge,
                )
            else:
                raw = _run_cache_driver(
                    candidate,
                    controller,
                    case_id=case_id,
                    run_nonce=run_nonce,
                    challenge=challenge,
                )
            controller.send(raw)
            return 0
        except _CandidateProtocolFailure as exc:
            _send_controller_worker_error(controller, _synthesized_candidate_error(exc))
            return CANDIDATE_FAILURE_EXIT
    finally:
        candidate.close()
        controller.close()


def _terminate_local_candidate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=2.0)


def _unsafe_local_supervisor(
    controller_protocol_fd: int,
    workspace: Path,
    challenge_root: Path | None,
) -> int:
    """Test-only compatibility path used by ``UnsafeLocalWorkerLauncher``."""

    supervisor_socket, candidate_socket = socket.socketpair(
        socket.AF_UNIX,
        socket.SOCK_STREAM,
    )
    command = [
        sys.executable,
        "-I",
        str(Path(__file__).resolve(strict=True)),
        "--candidate-protocol-fd",
        str(candidate_socket.fileno()),
        "--workspace",
        str(workspace),
    ]
    if challenge_root is not None:
        command.extend(("--challenge-root", str(challenge_root)))
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        pass_fds=(candidate_socket.fileno(),),
        start_new_session=True,
        env={
            "PATH": os.defpath,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        },
    )
    candidate_socket.close()
    try:
        result = run_trusted_supervisor(
            controller_protocol_fd,
            supervisor_socket.fileno(),
        )
        try:
            candidate_returncode = process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            return CANDIDATE_FAILURE_EXIT
        if result == 0 and candidate_returncode != 0:
            return CANDIDATE_FAILURE_EXIT
        return result
    finally:
        supervisor_socket.close()
        _terminate_local_candidate(process)


def _cli_error(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-fd", type=int)
    parser.add_argument("--supervisor-controller-fd", type=int)
    parser.add_argument("--supervisor-candidate-fd", type=int)
    parser.add_argument("--candidate-protocol-fd", type=int)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--challenge-root", type=Path, default=None)
    args = parser.parse_args(argv)
    modes = (
        args.protocol_fd is not None,
        args.candidate_protocol_fd is not None,
        args.supervisor_controller_fd is not None or args.supervisor_candidate_fd is not None,
    )
    if sum(modes) != 1:
        _cli_error("exactly one worker execution mode is required")
    if modes[2]:
        if (
            args.supervisor_controller_fd is None
            or args.supervisor_candidate_fd is None
            or args.workspace is not None
            or args.challenge_root is not None
        ):
            _cli_error("supervisor mode requires exactly two protocol descriptors")
        return run_trusted_supervisor(
            args.supervisor_controller_fd,
            args.supervisor_candidate_fd,
        )
    if args.workspace is None:
        _cli_error("candidate workspace is required")
    try:
        workspace = args.workspace.resolve(strict=True)
        challenge = args.challenge_root.resolve(strict=True) if args.challenge_root else None
    except OSError as exc:
        _cli_error(f"candidate mount is unavailable: {exc}")
    if args.candidate_protocol_fd is not None:
        if args.candidate_protocol_fd < 3:
            _cli_error("candidate protocol descriptor must not alias stdio")
        return candidate_main(args.candidate_protocol_fd, workspace, challenge)
    assert args.protocol_fd is not None
    if args.protocol_fd < 3:
        _cli_error("controller protocol descriptor must not alias stdio")
    return _unsafe_local_supervisor(args.protocol_fd, workspace, challenge)


__all__ = [
    "CANDIDATE_API_ISOLATION_SCHEMA",
    "CANDIDATE_FAILURE_EXIT",
    "MAX_FRAME_BYTES",
    "MAX_FRAMES",
    "PROTOCOL_VERSION",
    "TrustedSupervisorError",
    "candidate_main",
    "run_trusted_supervisor",
]


if __name__ == "__main__":
    raise SystemExit(main())


