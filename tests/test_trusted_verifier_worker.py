from __future__ import annotations

import errno
import json
import os
import re
import socket
import struct
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from typing import Any

import pytest

from benchmarks.trusted_verifier_controller import (
    UnsafeLocalWorkerLauncher,
    evaluate_path_boundary,
)
from benchmarks.trusted_verifier_worker import (
    CANDIDATE_API_ISOLATION_SCHEMA,
    CANDIDATE_FAILURE_EXIT,
    MAX_FRAME_BYTES,
    TrustedSupervisorError,
    candidate_main,
    run_trusted_supervisor,
)

_TOKEN = re.compile(r"[0-9a-f]{64}")


def _encoded(message: dict[str, Any]) -> bytes:
    payload = json.dumps(
        message,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return struct.pack("!I", len(payload)) + payload


def _send(channel: socket.socket, message: dict[str, Any]) -> None:
    channel.sendall(_encoded(message))


def _receive(channel: socket.socket) -> dict[str, Any]:
    header = _read_exact(channel, 4)
    size = struct.unpack("!I", header)[0]
    value = json.loads(_read_exact(channel, size))
    assert isinstance(value, dict)
    return value


def _read_exact(channel: socket.socket, size: int) -> bytes:
    output = bytearray()
    while len(output) < size:
        chunk = channel.recv(size - len(output))
        if not chunk:
            raise AssertionError("test protocol channel closed early")
        output.extend(chunk)
    return bytes(output)


def _path_start(nonce: str) -> dict[str, Any]:
    return {
        "case_id": "coding.path-boundary",
        "challenge": {
            "operations": [
                {"op_id": "valid-id", "user_path": "nested/value.txt"},
                {"op_id": "escape-id", "user_path": "../outside.txt"},
            ],
            "root_relative": "root",
        },
        "kind": "start",
        "run_nonce": nonce,
        "version": 1,
    }


def _cache_start(nonce: str) -> dict[str, Any]:
    return {
        "case_id": "coding.concurrent-cache",
        "challenge": {
            "clock_expired": 15.1,
            "clock_initial": 10.0,
            "clock_live": 14.9,
            "failure_key": "failure-key",
            "shared_key": "shared-key",
            "thread_count": 2,
            "ttl_seconds": 5.0,
        },
        "kind": "start",
        "run_nonce": nonce,
        "version": 1,
    }


class _SupervisorHarness:
    def __init__(self) -> None:
        controller_peer, controller_endpoint = socket.socketpair()
        candidate_endpoint, candidate_peer = socket.socketpair()
        self.controller = controller_peer
        self.candidate = candidate_peer
        self.controller.settimeout(2.0)
        self.candidate.settimeout(2.0)
        self._controller_fd = os.dup(controller_endpoint.fileno())
        self._candidate_fd = os.dup(candidate_endpoint.fileno())
        controller_endpoint.close()
        candidate_endpoint.close()
        self.result: int | BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            self.result = run_trusted_supervisor(
                self._controller_fd,
                self._candidate_fd,
                timeout_seconds=1.0,
            )
        except BaseException as exc:  # pragma: no cover - asserted by callers
            self.result = exc
        finally:
            os.close(self._controller_fd)
            os.close(self._candidate_fd)

    def finish(self) -> int | BaseException:
        self.thread.join(timeout=3.0)
        assert not self.thread.is_alive()
        assert self.result is not None
        self.controller.close()
        self.candidate.close()
        return self.result


class _CandidateProcessHarness:
    """Run the real candidate CLI process behind the trusted host driver."""

    def __init__(
        self,
        workspace: Path,
        *,
        challenge_root: Path | None = None,
    ) -> None:
        controller_peer, controller_endpoint = socket.socketpair()
        candidate_endpoint, candidate_child = socket.socketpair()
        self.controller = controller_peer
        self.controller.settimeout(5.0)
        self._controller_fd = os.dup(controller_endpoint.fileno())
        self._candidate_fd = os.dup(candidate_endpoint.fileno())
        controller_endpoint.close()
        candidate_endpoint.close()
        command = [
            sys.executable,
            "-I",
            str(Path(candidate_main.__code__.co_filename).resolve()),
            "--candidate-protocol-fd",
            str(candidate_child.fileno()),
            "--workspace",
            str(workspace),
        ]
        if challenge_root is not None:
            command.extend(("--challenge-root", str(challenge_root)))
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=(candidate_child.fileno(),),
            start_new_session=True,
            env={
                "PATH": os.defpath,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            },
        )
        candidate_child.close()
        self.result: int | BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            self.result = run_trusted_supervisor(
                self._controller_fd,
                self._candidate_fd,
                timeout_seconds=4.0,
            )
        except BaseException as exc:  # pragma: no cover - asserted by callers
            self.result = exc
        finally:
            os.close(self._controller_fd)
            os.close(self._candidate_fd)

    def finish(self) -> tuple[int | BaseException, int, bytes, bytes]:
        self.thread.join(timeout=6.0)
        assert not self.thread.is_alive()
        assert self.result is not None
        stdout, stderr = self.process.communicate(timeout=5.0)
        self.controller.close()
        return self.result, self.process.returncode, stdout, stderr


def _ready(candidate: socket.socket, init: dict[str, Any]) -> None:
    _send(
        candidate,
        {
            "kind": "api_ready",
            "session": init["session"],
            "version": 1,
        },
    )


def _shutdown_write_if_connected(candidate: socket.socket) -> None:
    """Close the test write side after the worker may have failed closed."""

    try:
        candidate.shutdown(socket.SHUT_WR)
    except OSError as exc:
        if exc.errno != errno.ENOTCONN:
            raise


def _complete(candidate: socket.socket, session: str) -> None:
    _send(
        candidate,
        {"kind": "api_complete", "session": session, "version": 1},
    )
    _shutdown_write_if_connected(candidate)


def _return_result(call: dict[str, Any], value: str, *, kind: str) -> dict[str, Any]:
    capability_name = "capability" if kind == "api_path_result" else "loader_token"
    return {
        capability_name: call[capability_name],
        "kind": kind,
        "outcome": "return",
        "request_id": call["request_id"],
        "session": call["session"],
        "value": value,
        "version": 1,
    }


def test_host_driver_frame_walk_hides_outer_scope_and_reconstructs_path() -> None:
    nonce = "a" * 64
    harness = _SupervisorHarness()
    _send(harness.controller, _path_start(nonce))

    frames: list[dict[str, Any]] = []
    init = _receive(harness.candidate)
    frames.append(init)
    assert init == {
        "case_id": "coding.path-boundary",
        "kind": "api_init",
        "root_relative": "root",
        "schema": CANDIDATE_API_ISOLATION_SCHEMA,
        "session": init["session"],
        "version": 1,
    }
    assert _TOKEN.fullmatch(init["session"])
    _ready(harness.candidate, init)

    for _ in range(2):
        call = _receive(harness.candidate)
        frames.append(call)
        assert set(call) == {
            "capability",
            "kind",
            "request_id",
            "session",
            "user_path",
            "version",
        }
        assert call["kind"] == "api_path_call"
        assert _TOKEN.fullmatch(call["request_id"])
        assert _TOKEN.fullmatch(call["capability"])
        if call["user_path"] == "nested/value.txt":
            response = _return_result(call, "value", kind="api_path_result")
        else:
            response = {
                "capability": call["capability"],
                "exception": {
                    "message": "blocked",
                    "module": "candidate_file_service",
                    "name": "PathBoundaryError",
                },
                "kind": "api_path_result",
                "outcome": "exception",
                "request_id": call["request_id"],
                "session": call["session"],
                "version": 1,
            }
        _send(harness.candidate, response)

    shutdown = _receive(harness.candidate)
    frames.append(shutdown)
    assert shutdown == {
        "kind": "api_shutdown",
        "session": init["session"],
        "version": 1,
    }
    _complete(harness.candidate, init["session"])

    outcome = _receive(harness.controller)
    assert outcome["kind"] == "raw_outcome"
    assert [item["op_id"] for item in outcome["operations"]] == [
        "valid-id",
        "escape-id",
    ]
    serialized_frames = json.dumps(frames, sort_keys=True)
    for forbidden in (nonce, "valid-id", "escape-id", '"challenge"', '"op_id"'):
        assert forbidden not in serialized_frames
    assert harness.finish() == 0


@pytest.mark.parametrize(
    "forged_kind",
    [
        "candidate_path_observations",
        "candidate_cache_observations",
        "raw_outcome",
        "loader_request",
        "verdict",
        "passed",
        "score",
        "start",
        "unknown",
    ],
)
def test_candidate_api_cannot_forge_aggregate_or_outer_frames(forged_kind: str) -> None:
    harness = _SupervisorHarness()
    _send(harness.controller, _path_start("b" * 64))
    init = _receive(harness.candidate)
    _ready(harness.candidate, init)

    _send(
        harness.candidate,
        {
            "kind": forged_kind,
            "passed": True,
            "session": init["session"],
            "version": 1,
        },
    )
    response = _receive(harness.controller)
    assert response["kind"] == "worker_error"
    assert "forbidden frame" in response["error"]["message"]
    assert "passed" not in response
    _shutdown_write_if_connected(harness.candidate)
    assert harness.finish() == CANDIDATE_FAILURE_EXIT


def test_cache_calls_are_dispatched_concurrently_and_reverse_rpc_is_narrow() -> None:
    nonce = "c" * 64
    harness = _SupervisorHarness()
    _send(harness.controller, _cache_start(nonce))
    init = _receive(harness.candidate)
    assert init == {
        "kind": "api_cache_init",
        "schema": CANDIDATE_API_ISOLATION_SCHEMA,
        "session": init["session"],
        "ttl_seconds": 5.0,
        "version": 1,
    }
    _ready(harness.candidate, init)

    # The driver sends every same-key call before waiting for any result.
    shared_calls = [_receive(harness.candidate), _receive(harness.candidate)]
    assert all(call["kind"] == "api_cache_call" for call in shared_calls)
    assert all(call["key"] == "shared-key" for call in shared_calls)
    assert len({call["loader_token"] for call in shared_calls}) == 2

    first = shared_calls[0]
    _send(
        harness.candidate,
        {
            "kind": "api_clock_request",
            "request_id": first["request_id"],
            "session": first["session"],
            "version": 1,
        },
    )
    clock_response = _receive(harness.candidate)
    assert clock_response == {
        "kind": "api_clock_response",
        "request_id": first["request_id"],
        "session": first["session"],
        "value": 10.0,
        "version": 1,
    }
    _send(
        harness.candidate,
        {
            "kind": "api_loader_request",
            "loader_token": first["loader_token"],
            "request_id": first["request_id"],
            "session": first["session"],
            "version": 1,
        },
    )
    outer_request = _receive(harness.controller)
    assert outer_request["kind"] == "loader_request"
    assert outer_request["loader_id"] == "shared"
    assert outer_request["run_nonce"] == nonce
    assert outer_request["request_id"] != first["request_id"]
    assert first["loader_token"] not in json.dumps(outer_request)
    _send(
        harness.controller,
        {
            "action": "return",
            "kind": "loader_response",
            "request_id": outer_request["request_id"],
            "run_nonce": nonce,
            "value": "seeded",
            "version": 1,
        },
    )
    loader_response = _receive(harness.candidate)
    assert loader_response == {
        "action": "return",
        "kind": "api_loader_response",
        "loader_token": first["loader_token"],
        "request_id": first["request_id"],
        "session": first["session"],
        "value": "seeded",
        "version": 1,
    }

    for call in shared_calls:
        _send(
            harness.candidate,
            _return_result(call, "seeded", kind="api_cache_result"),
        )

    # live, expired, failure, and recovery are issued one at a time and the
    # candidate sees only opaque calls, never those semantic role names.
    sequential_values = ("seeded", "second", "failed", "recovered")
    sequential_calls: list[dict[str, Any]] = []
    for value in sequential_values:
        call = _receive(harness.candidate)
        sequential_calls.append(call)
        assert call["kind"] == "api_cache_call"
        _send(
            harness.candidate,
            _return_result(call, value, kind="api_cache_result"),
        )

    shutdown = _receive(harness.candidate)
    _complete(harness.candidate, init["session"])
    raw = _receive(harness.controller)
    assert raw["kind"] == "raw_outcome"
    assert len(raw["concurrent"]) == 2
    assert [raw[name]["value"] for name in ("live", "expired", "failure", "recovery")] == list(
        sequential_values
    )
    hidden = json.dumps([init, *shared_calls, *sequential_calls, shutdown], sort_keys=True)
    for forbidden in (
        nonce,
        "clock_initial",
        "clock_live",
        "clock_expired",
        "thread_count",
        "live_trap",
        '"loader_id"',
        "run_nonce",
    ):
        assert forbidden not in hidden
    assert harness.finish() == 0


def test_replayed_api_result_is_candidate_failure() -> None:
    harness = _SupervisorHarness()
    _send(harness.controller, _path_start("d" * 64))
    init = _receive(harness.candidate)
    _ready(harness.candidate, init)
    first = _receive(harness.candidate)
    result = _return_result(first, "value", kind="api_path_result")
    _send(harness.candidate, result)
    assert _receive(harness.candidate)["kind"] == "api_path_call"
    _send(harness.candidate, result)
    _shutdown_write_if_connected(harness.candidate)
    response = _receive(harness.controller)
    assert response["kind"] == "worker_error"
    assert "replayed" in response["error"]["message"]
    assert harness.finish() == CANDIDATE_FAILURE_EXIT


def test_replayed_loader_capability_is_not_forwarded_twice() -> None:
    nonce = "7" * 64
    harness = _SupervisorHarness()
    _send(harness.controller, _cache_start(nonce))
    init = _receive(harness.candidate)
    _ready(harness.candidate, init)
    first = _receive(harness.candidate)
    assert _receive(harness.candidate)["kind"] == "api_cache_call"
    request = {
        "kind": "api_loader_request",
        "loader_token": first["loader_token"],
        "request_id": first["request_id"],
        "session": first["session"],
        "version": 1,
    }
    _send(harness.candidate, request)
    outer = _receive(harness.controller)
    _send(
        harness.controller,
        {
            "action": "return",
            "kind": "loader_response",
            "request_id": outer["request_id"],
            "run_nonce": nonce,
            "value": "seeded",
            "version": 1,
        },
    )
    assert _receive(harness.candidate)["kind"] == "api_loader_response"
    _send(harness.candidate, request)
    _shutdown_write_if_connected(harness.candidate)
    response = _receive(harness.controller)
    assert response["kind"] == "worker_error"
    assert "replayed" in response["error"]["message"]
    assert harness.finish() == CANDIDATE_FAILURE_EXIT


@pytest.mark.parametrize("marker", [None, "echo.candidate_api_process.v0"])
def test_candidate_api_missing_or_legacy_marker_fails_closed(
    tmp_path: Path,
    marker: str | None,
) -> None:
    server_socket, peer = socket.socketpair()
    peer.settimeout(2.0)
    descriptor = os.dup(server_socket.fileno())
    server_socket.close()
    result: list[int] = []

    def serve() -> None:
        result.append(candidate_main(descriptor, tmp_path, tmp_path))

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    init: dict[str, Any] = {
        "case_id": "coding.path-boundary",
        "kind": "api_init",
        "root_relative": "root",
        "session": "8" * 64,
        "version": 1,
    }
    if marker is not None:
        init["schema"] = marker
    _send(peer, init)
    error = _receive(peer)
    assert error["kind"] == "api_error"
    assert error["session"] == "8" * 64
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert result == [1]
    assert peer.recv(1) == b""
    peer.close()


def test_candidate_process_frame_walk_cannot_find_outer_scope_or_outer_fd(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    challenge_root = tmp_path / "challenge"
    (challenge_root / "root").mkdir(parents=True)
    workspace.mkdir()
    (workspace / "file_service.py").write_text(
        textwrap.dedent(
            """
            import json
            import os
            import stat
            import sys

            forbidden = {"challenge", "operations", "op_id", "run_nonce"}
            forbidden_locals = {"operations", "op_id", "run_nonce"}
            found = set()
            frame = sys._getframe().f_back
            while frame is not None:
                for mapping in (frame.f_globals, frame.f_locals):
                    found.update(forbidden_locals.intersection(mapping))
                    if isinstance(mapping.get("challenge"), dict):
                        found.add("challenge")
                    for value in tuple(mapping.values()):
                        if isinstance(value, dict):
                            found.update(forbidden.intersection(value))
                frame = frame.f_back

            try:
                descriptors = sorted(
                    int(name) for name in os.listdir("/dev/fd") if name.isdigit()
                )
            except OSError:
                descriptors = range(3, min(4096, int(os.sysconf("SC_OPEN_MAX"))))
            socket_fds = []
            for descriptor in descriptors:
                try:
                    observed = os.fstat(descriptor)
                except OSError:
                    continue
                if stat.S_ISSOCK(observed.st_mode):
                    socket_fds.append(descriptor)

            snapshot = json.dumps(
                {"forbidden_keys": sorted(found), "socket_count": len(socket_fds)},
                sort_keys=True,
            )

            class FileService:
                def __init__(self, root):
                    self.root = root

                def read_text(self, user_path):
                    return snapshot
            """
        ).lstrip(),
        encoding="utf-8",
    )
    nonce = "9" * 64
    start = _path_start(nonce)
    start["challenge"]["operations"] = [
        {"op_id": "outer-op-secret", "user_path": "one"},
        {"op_id": "outer-op-secret-two", "user_path": "two"},
    ]
    harness = _CandidateProcessHarness(
        workspace,
        challenge_root=challenge_root,
    )
    _send(harness.controller, start)
    raw = _receive(harness.controller)
    assert raw["kind"] == "raw_outcome"
    for operation in raw["operations"]:
        observation = json.loads(operation["value"])
        assert observation == {"forbidden_keys": [], "socket_count": 1}
    assert nonce not in json.dumps(raw["operations"])
    result, returncode, stdout, stderr = harness.finish()
    assert (result, returncode, stdout, stderr) == (0, 0, b"", b"")


@pytest.mark.parametrize(
    "forged_kind",
    [
        "raw_outcome",
        "candidate_path_observations",
        "candidate_cache_observations",
        "verdict",
    ],
)
def test_candidate_process_direct_aggregate_write_is_scored_failure(
    tmp_path: Path,
    forged_kind: str,
) -> None:
    workspace = tmp_path / "workspace"
    challenge_root = tmp_path / "challenge"
    (challenge_root / "root").mkdir(parents=True)
    workspace.mkdir()
    (workspace / "file_service.py").write_text(
        textwrap.dedent(
            f"""
            import json
            import os
            import socket
            import stat
            import struct

            payload = json.dumps(
                {{"kind": {forged_kind!r}, "passed": True, "version": 1}},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            try:
                descriptors = sorted(
                    int(name) for name in os.listdir("/dev/fd") if name.isdigit()
                )
            except OSError:
                descriptors = range(3, min(4096, int(os.sysconf("SC_OPEN_MAX"))))
            for descriptor in descriptors:
                try:
                    observed = os.fstat(descriptor)
                except OSError:
                    continue
                if stat.S_ISSOCK(observed.st_mode):
                    channel = socket.socket(fileno=os.dup(descriptor))
                    channel.sendall(struct.pack("!I", len(payload)) + payload)
                    channel.close()

            class FileService:
                def __init__(self, root):
                    self.root = root

                def read_text(self, user_path):
                    return "forged"
            """
        ).lstrip(),
        encoding="utf-8",
    )
    harness = _CandidateProcessHarness(
        workspace,
        challenge_root=challenge_root,
    )
    _send(harness.controller, _path_start("a" * 64))
    response = _receive(harness.controller)
    assert response["kind"] == "worker_error"
    assert "forbidden frame" in response["error"]["message"]
    assert "passed" not in response
    result, returncode, stdout, stderr = harness.finish()
    assert result == CANDIDATE_FAILURE_EXIT
    assert returncode != 0
    assert stdout == b""
    assert stderr == b""


def test_candidate_process_monkeypatches_cannot_forge_passing_verdict(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    tests = workspace / "tests"
    tests.mkdir(parents=True)
    (workspace / "file_service.py").write_text(
        textwrap.dedent(
            """
            import builtins
            import json
            import socket

            json.dumps = lambda *args, **kwargs: '{"passed":true,"score":1.0}'
            json.JSONEncoder = lambda *args, **kwargs: None
            socket.socket = lambda *args, **kwargs: None
            builtins.getattr = lambda value, name, *default: (
                object.__getattribute__(value, name) if not default else default[0]
            )

            class FileService:
                def __init__(self, root):
                    self.root = root

                def read_text(self, user_path):
                    return (self.root / user_path).read_text(encoding="utf-8")
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (tests / "test_file_service.py").write_text(
        "def test_placeholder():\n    assert True\n",
        encoding="utf-8",
    )
    (workspace / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
        encoding="utf-8",
    )

    verdict = evaluate_path_boundary(
        workspace,
        launcher=UnsafeLocalWorkerLauncher(),
    )

    assert verdict["passed"] is False
    assert verdict["score"] == 0.0
    assert verdict["checks"] == []


def test_candidate_process_cache_receives_eight_calls_but_controller_owns_loaders(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "cache.py").write_text(
        textwrap.dedent(
            """
            import threading

            class TTLCache:
                def __init__(self, ttl_seconds, *, clock):
                    self.ttl_seconds = ttl_seconds
                    self._clock = clock
                    self._values = {}
                    self._conditions = {}
                    self._lock = threading.Lock()
                    self._entry_lock = threading.Lock()
                    self._entry_count = 0
                    self._eight_calls = threading.Barrier(8)

                def get_or_load(self, key, loader):
                    with self._entry_lock:
                        initial = self._entry_count < 8
                        self._entry_count += 1
                    if initial:
                        self._eight_calls.wait(timeout=2.0)
                    while True:
                        with self._lock:
                            cached = self._values.get(key)
                            if cached is not None and cached[0] > self._clock():
                                return cached[1]
                            condition = self._conditions.get(key)
                            if condition is None:
                                condition = threading.Condition(self._lock)
                                self._conditions[key] = condition
                                break
                            condition.wait()
                    try:
                        value = loader()
                    except BaseException:
                        with self._lock:
                            self._conditions.pop(key).notify_all()
                        raise
                    with self._lock:
                        self._values[key] = (
                            self._clock() + self.ttl_seconds,
                            value,
                        )
                        self._conditions.pop(key).notify_all()
                    return value
            """
        ).lstrip(),
        encoding="utf-8",
    )
    nonce = "b" * 64
    start = _cache_start(nonce)
    start["challenge"]["thread_count"] = 8
    harness = _CandidateProcessHarness(workspace)
    _send(harness.controller, start)
    counts = {
        "shared": 0,
        "live_trap": 0,
        "expired": 0,
        "failure": 0,
        "recovery": 0,
    }
    values = {
        "shared": "seeded",
        "live_trap": "trap",
        "expired": "second",
        "failure": "failed",
        "recovery": "recovered",
    }
    raw: dict[str, Any] | None = None
    while raw is None:
        message = _receive(harness.controller)
        if message["kind"] == "raw_outcome":
            raw = message
            break
        assert message["kind"] == "loader_request"
        loader_id = message["loader_id"]
        counts[loader_id] += 1
        _send(
            harness.controller,
            {
                "action": "raise" if loader_id == "failure" else "return",
                "kind": "loader_response",
                "request_id": message["request_id"],
                "run_nonce": nonce,
                "value": values[loader_id],
                "version": 1,
            },
        )
    assert len(raw["concurrent"]) == 8
    assert counts == {
        "shared": 1,
        "live_trap": 0,
        "expired": 1,
        "failure": 1,
        "recovery": 1,
    }
    assert all(item == {"outcome": "return", "value": "seeded"} for item in raw["concurrent"])
    assert raw["live"] == {"outcome": "return", "value": "seeded"}
    assert raw["expired"] == {"outcome": "return", "value": "second"}
    assert raw["failure"]["outcome"] == "exception"
    assert raw["recovery"] == {"outcome": "return", "value": "recovered"}
    result, returncode, stdout, stderr = harness.finish()
    assert (result, returncode, stdout, stderr) == (0, 0, b"", b"")


def test_noncanonical_candidate_frame_fails_closed() -> None:
    harness = _SupervisorHarness()
    _send(harness.controller, _path_start("e" * 64))
    init = _receive(harness.candidate)
    _ready(harness.candidate, init)
    assert _receive(harness.candidate)["kind"] == "api_path_call"
    payload = b'{"kind":"api_path_result","kind":"raw_outcome"}'
    harness.candidate.sendall(struct.pack("!I", len(payload)) + payload)
    _shutdown_write_if_connected(harness.candidate)
    response = _receive(harness.controller)
    assert response["kind"] == "worker_error"
    assert "canonical JSON" in response["error"]["message"]
    assert harness.finish() == CANDIDATE_FAILURE_EXIT


def test_oversized_candidate_frame_fails_before_allocation() -> None:
    harness = _SupervisorHarness()
    _send(harness.controller, _path_start("f" * 64))
    init = _receive(harness.candidate)
    _ready(harness.candidate, init)
    assert _receive(harness.candidate)["kind"] == "api_path_call"
    harness.candidate.sendall(struct.pack("!I", MAX_FRAME_BYTES + 1))
    _shutdown_write_if_connected(harness.candidate)
    response = _receive(harness.controller)
    assert response["kind"] == "worker_error"
    assert "invalid length" in response["error"]["message"]
    assert harness.finish() == CANDIDATE_FAILURE_EXIT


def test_supervisor_borrows_descriptors_and_rejects_aliases() -> None:
    left, right = socket.socketpair()
    duplicate = os.dup(left.fileno())
    try:
        with pytest.raises(TrustedSupervisorError, match="alias"):
            run_trusted_supervisor(left.fileno(), duplicate, timeout_seconds=0.1)
        assert left.fileno() >= 0
        assert right.fileno() >= 0
    finally:
        os.close(duplicate)
        left.close()
        right.close()

