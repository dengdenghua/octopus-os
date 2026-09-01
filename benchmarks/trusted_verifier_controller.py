"""Controller-owned verdicts for Python coding fixtures.

The controller in this module never imports model-written code.  A separately
attested Linux launcher runs :mod:`benchmarks.trusted_verifier_worker` in a
private process/user/PID/mount/network boundary and relays only bounded raw
observations.  The controller owns challenges, filesystem facts, loader
responses, validation, and the final pass/fail JSON.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import secrets
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

sys.dont_write_bytecode = True
INFRASTRUCTURE_EXIT = 78


def _load_sibling_contract() -> Any:
    """Load exact sibling bytes when this controller is started with ``-I``."""

    contract_path = Path(__file__).with_name("trusted_verifier_contract.py")
    expected = os.environ.get("ECHO_CANDIDATE_WORKER_CONTRACT_SHA256", "").strip()
    try:
        metadata = contract_path.lstat()
        source = contract_path.read_bytes()
    except OSError as exc:
        raise RuntimeError("trusted verifier contract source is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("trusted verifier contract source is not a regular sibling file")
    observed = hashlib.sha256(source).hexdigest()
    if expected:
        if len(expected) != 64 or any(
            character not in "0123456789abcdef" for character in expected
        ):
            raise RuntimeError("trusted verifier contract expected digest is invalid")
        if observed != expected:
            raise RuntimeError("trusted verifier contract source digest changed")
        current = contract_path.resolve(strict=True)
        for protected in (current, *current.parents):
            protected_metadata = protected.stat()
            if protected_metadata.st_uid != 0 or stat.S_IMODE(protected_metadata.st_mode) & 0o022:
                raise RuntimeError("trusted verifier contract path is not root protected")
    spec = importlib.util.spec_from_file_location(
        "_echo_trusted_verifier_contract", contract_path
    )
    if spec is None:
        raise RuntimeError("trusted verifier contract module identity is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        exec(compile(source, str(contract_path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


if __package__:
    from benchmarks import trusted_verifier_contract as _contract
else:  # ``python -I /absolute/trusted_verifier_controller.py``
    try:
        _contract = _load_sibling_contract()
    except Exception as exc:  # noqa: BLE001 - bootstrap is trusted infrastructure
        print(f"trusted verifier infrastructure invalid: {exc}", file=sys.stderr)
        raise SystemExit(INFRASTRUCTURE_EXIT) from exc

CLI_JSON_ENV = _contract.CLI_JSON_ENV
CONFIG_ENV = _contract.CONFIG_ENV
CONFIG_SHA256_ENV = _contract.CONFIG_SHA256_ENV
CONTRACT_SCHEMA = _contract.CONTRACT_SCHEMA
CONTRACT_SHA256_ENV = _contract.CONTRACT_SHA256_ENV
MAX_FRAME_BYTES = _contract.MAX_FRAME_BYTES
MAX_FRAMES = _contract.MAX_FRAMES
WorkerContract = _contract.WorkerContract
WorkerContractError = _contract.WorkerContractError
load_contract_from_environment = _contract.load_contract_from_environment
minimal_launcher_environment = _contract.minimal_launcher_environment
validate_runner_complete = _contract.validate_runner_complete
validate_runner_ready = _contract.validate_runner_ready

PROTOCOL_VERSION = 1
MAX_SOURCE_BYTES = 256 * 1024
MAX_TEXT_BYTES = 8 * 1024
MAX_TREE_DEPTH = 48
MAX_TREE_ENTRIES = 20_000
MAX_TREE_PATH_BYTES = 2_048
MAX_TREE_TOTAL_BYTES = 256 * 1024 * 1024
_EXPECTED_PYPROJECT = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'


class WorkerInfrastructureError(RuntimeError):
    """The evaluator could not establish the trusted worker boundary."""


class CandidateObservationError(RuntimeError):
    """The untrusted candidate produced invalid or failing observations."""


class WorkerLauncher(Protocol):
    attested: bool

    def run(
        self,
        *,
        case_id: str,
        workspace: Path,
        challenge_root: Path | None,
        challenge: dict[str, Any],
        request_handler: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]: ...


class _FramedChannel:
    def __init__(self, connection: socket.socket, *, timeout_seconds: float) -> None:
        self._connection = connection
        self._connection.settimeout(timeout_seconds)
        self._encoder = json.JSONEncoder(
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode
        self._decoder = json.JSONDecoder(
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        ).decode
        self._header = struct.Struct("!I")
        self._sent = 0
        self._received = 0

    def send(self, message: Mapping[str, Any]) -> None:
        payload = self._encoder(dict(message)).encode("utf-8")
        if not payload or len(payload) > MAX_FRAME_BYTES:
            raise CandidateObservationError("controller frame exceeds the byte limit")
        self._sent += 1
        if self._sent > MAX_FRAMES:
            raise CandidateObservationError("controller sent too many RPC frames")
        try:
            self._connection.sendall(self._header.pack(len(payload)) + payload)
        except (OSError, TimeoutError) as exc:
            raise CandidateObservationError("candidate RPC channel closed while sending") from exc

    def receive(self) -> dict[str, Any]:
        header = self._read_exact(self._header.size)
        (size,) = self._header.unpack(header)
        if size < 2 or size > MAX_FRAME_BYTES:
            raise CandidateObservationError("candidate RPC frame length is invalid")
        payload = self._read_exact(size)
        try:
            text = payload.decode("utf-8", errors="strict")
            value = self._decoder(text)
            canonical = self._encoder(value).encode("utf-8")
        except (UnicodeDecodeError, RecursionError, ValueError) as exc:
            raise CandidateObservationError("candidate RPC frame is malformed") from exc
        if not isinstance(value, dict) or canonical != payload:
            raise CandidateObservationError("candidate RPC frame is not canonical JSON")
        self._received += 1
        if self._received > MAX_FRAMES:
            raise CandidateObservationError("candidate sent too many RPC frames")
        return value

    def expect_eof(self, *, infrastructure: bool = False) -> None:
        """Require the peer to close without trailing or replayed bytes."""

        error_type = WorkerInfrastructureError if infrastructure else CandidateObservationError
        try:
            trailing = self._connection.recv(1)
        except (OSError, TimeoutError) as exc:
            raise error_type("RPC channel did not close cleanly") from exc
        if trailing:
            raise error_type("RPC channel contained an extra or replayed frame")

    def _read_exact(self, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            try:
                chunk = self._connection.recv(remaining)
            except (OSError, TimeoutError) as exc:
                raise CandidateObservationError("candidate RPC frame timed out") from exc
            if not chunk:
                raise CandidateObservationError("candidate worker exited before a complete frame")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


class AttestedWorkerLauncher:
    """Launch a worker only through a content-bound hardened Linux runner."""

    attested = True

    def __init__(self, contract: WorkerContract) -> None:
        self._contract = contract

    @classmethod
    def from_environment(cls) -> AttestedWorkerLauncher:
        try:
            return cls(load_contract_from_environment(controller_path=Path(__file__)))
        except WorkerContractError as exc:
            raise WorkerInfrastructureError(str(exc)) from exc

    def run(
        self,
        *,
        case_id: str,
        workspace: Path,
        challenge_root: Path | None,
        challenge: dict[str, Any],
        request_handler: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        workspace_manifest = tree_manifest_sha256(workspace, reject_symlinks=True)
        challenge_manifest = (
            tree_manifest_sha256(challenge_root, reject_symlinks=False)
            if challenge_root is not None
            else hashlib.sha256(b"no-challenge-tree").hexdigest()
        )
        run_nonce = secrets.token_hex(32)
        control_parent, control_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        worker_parent, worker_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        command = [
            *self._contract.cli,
            "worker",
            "--attestation",
            str(self._contract.path),
            "--workspace-snapshot",
            str(workspace),
            "--workspace-manifest-sha256",
            workspace_manifest,
            "--challenge-manifest-sha256",
            challenge_manifest,
            "--control-fd",
            str(control_child.fileno()),
            "--protocol-fd",
            str(worker_child.fileno()),
            "--run-nonce",
            run_nonce,
        ]
        if challenge_root is not None:
            command.extend(("--challenge-snapshot", str(challenge_root)))
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                pass_fds=(control_child.fileno(), worker_child.fileno()),
                start_new_session=True,
                env=minimal_launcher_environment(),
            )
        except OSError as exc:
            control_parent.close()
            control_child.close()
            worker_parent.close()
            worker_child.close()
            raise WorkerInfrastructureError(f"candidate worker launcher failed: {exc}") from exc
        control_child.close()
        worker_child.close()
        control_channel = _FramedChannel(
            control_parent,
            timeout_seconds=self._contract.timeout_seconds,
        )
        worker_channel = _FramedChannel(
            worker_parent,
            timeout_seconds=self._contract.timeout_seconds,
        )
        try:
            ready = _receive_control_message(control_channel, phase="ready")
            try:
                validate_runner_ready(
                    ready,
                    run_nonce=run_nonce,
                    contract_sha256=self._contract.sha256,
                )
            except WorkerContractError as exc:
                raise WorkerInfrastructureError(str(exc)) from exc
            worker_channel.send(
                {
                    "case_id": case_id,
                    "challenge": challenge,
                    "kind": "start",
                    "run_nonce": run_nonce,
                    "version": PROTOCOL_VERSION,
                }
            )
            result: dict[str, Any] | None = None
            candidate_error: CandidateObservationError | None = None
            try:
                result = _exchange_raw_outcome(
                    worker_channel,
                    case_id=case_id,
                    run_nonce=run_nonce,
                    request_handler=request_handler,
                )
            except CandidateObservationError as exc:
                # Even a malformed/early-exit candidate must be reaped by the
                # trusted launcher before the evaluator returns a scored fail.
                candidate_error = exc
            complete = _receive_control_message(control_channel, phase="completion")
            try:
                validate_runner_complete(
                    complete,
                    run_nonce=run_nonce,
                    workspace_manifest=workspace_manifest,
                    challenge_manifest=challenge_manifest,
                )
            except WorkerContractError as exc:
                raise WorkerInfrastructureError(str(exc)) from exc
            try:
                returncode = process.wait(timeout=2.0)
            except subprocess.TimeoutExpired as exc:
                raise WorkerInfrastructureError(
                    "candidate launcher did not exit after completion"
                ) from exc
            if returncode != 0:
                raise WorkerInfrastructureError(
                    f"candidate launcher exited {returncode} after attested completion"
                )
            control_channel.expect_eof(infrastructure=True)
            try:
                worker_channel.expect_eof()
            except CandidateObservationError as exc:
                if candidate_error is None:
                    candidate_error = exc
            worker_exit_code = complete["worker_exit_code"]
            if worker_exit_code != 0 and candidate_error is None:
                candidate_error = CandidateObservationError(
                    f"candidate worker exited {worker_exit_code}"
                )
            if candidate_error is not None:
                raise candidate_error
            if result is None:
                raise CandidateObservationError("candidate returned no raw outcome")
            return result
        except CandidateObservationError:
            raise
        except WorkerInfrastructureError:
            raise
        finally:
            control_parent.close()
            worker_parent.close()
            _terminate_process(process)


class UnsafeLocalWorkerLauncher:
    """Test-only worker process; never selected by a verifier entry point."""

    attested = False

    def __init__(self, worker_path: Path | None = None, *, timeout_seconds: float = 20.0) -> None:
        self._worker_path = (
            worker_path or Path(__file__).with_name("trusted_verifier_worker.py")
        ).resolve(strict=True)
        self._timeout_seconds = timeout_seconds

    def run(
        self,
        *,
        case_id: str,
        workspace: Path,
        challenge_root: Path | None,
        challenge: dict[str, Any],
        request_handler: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        run_nonce = secrets.token_hex(32)
        parent_socket, child_socket = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        command = [
            sys.executable,
            "-I",
            str(self._worker_path),
            "--protocol-fd",
            str(child_socket.fileno()),
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
            pass_fds=(child_socket.fileno(),),
            start_new_session=True,
            env=_minimal_worker_environment(),
        )
        child_socket.close()
        channel = _FramedChannel(parent_socket, timeout_seconds=self._timeout_seconds)
        try:
            channel.send(
                {
                    "case_id": case_id,
                    "challenge": challenge,
                    "kind": "start",
                    "run_nonce": run_nonce,
                    "version": PROTOCOL_VERSION,
                }
            )
            result = _exchange_raw_outcome(
                channel,
                case_id=case_id,
                run_nonce=run_nonce,
                request_handler=request_handler,
            )
            try:
                returncode = process.wait(timeout=2.0)
            except subprocess.TimeoutExpired as exc:
                raise CandidateObservationError("candidate worker did not terminate") from exc
            if returncode != 0:
                raise CandidateObservationError(f"candidate worker exited {returncode}")
            channel.expect_eof()
            return result
        finally:
            parent_socket.close()
            _terminate_process(process)


def _exchange_raw_outcome(
    channel: _FramedChannel,
    *,
    case_id: str,
    run_nonce: str,
    request_handler: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    seen_result = False
    while True:
        message = channel.receive()
        kind = message.get("kind")
        if kind == "loader_request" and case_id == "coding.concurrent-cache":
            if seen_result:
                raise CandidateObservationError("candidate sent a loader request after its result")
            if message.get("run_nonce") != run_nonce:
                raise CandidateObservationError(
                    "candidate loader request is outside the active scope"
                )
            response = request_handler(message)
            channel.send(response)
            continue
        if kind == "worker_error":
            raise CandidateObservationError(_worker_error_reason(message))
        if kind != "raw_outcome" or seen_result:
            # In particular, a child-authored ``verdict``/``passed`` frame is
            # never interpreted as evidence.
            raise CandidateObservationError(f"candidate sent forbidden frame kind {kind!r}")
        if (
            message.get("version") != PROTOCOL_VERSION
            or message.get("case_id") != case_id
            or message.get("run_nonce") != run_nonce
            or "passed" in message
            or "score" in message
            or "reason" in message
        ):
            raise CandidateObservationError("candidate raw outcome is outside the active scope")
        seen_result = True
        return message


def _worker_error_reason(message: Mapping[str, Any]) -> str:
    error = message.get("error")
    if not isinstance(error, Mapping):
        return "candidate worker reported an invalid error"
    name = str(error.get("name") or "worker error")
    detail = str(error.get("message") or "")
    return f"candidate worker failed: {name}: {detail}"[:4000]


def _receive_control_message(channel: _FramedChannel, *, phase: str) -> dict[str, Any]:
    try:
        return channel.receive()
    except CandidateObservationError as exc:
        raise WorkerInfrastructureError(
            f"trusted launcher {phase} message was unavailable: {exc}"
        ) from exc


def evaluate_path_boundary(
    workspace: str | Path,
    *,
    launcher: WorkerLauncher | None = None,
) -> dict[str, Any]:
    try:
        root = Path(workspace).resolve(strict=True)
        pre_manifest, checks = _validate_workspace(
            root,
            module_name="file_service.py",
            class_name="FileService",
            method_name="read_text",
            parameter_names=("self", "user_path"),
            test_name="tests/test_file_service.py",
        )
        with tempfile.TemporaryDirectory(prefix="echo-path-challenge-") as temporary:
            challenge_root = Path(temporary).resolve(strict=True)
            token = secrets.token_hex(16)
            root_name = f"root-{token}"
            nested_name = f"nested-{token}"
            valid_name = f"valid-{token}.txt"
            outside_name = f"outside-{token}.txt"
            symlink_name = f"link-{token}.txt"
            valid_value = f"valid-value-{secrets.token_hex(24)}"
            outside_value = f"outside-secret-{secrets.token_hex(24)}"
            sandbox_root = challenge_root / root_name
            nested = sandbox_root / nested_name
            nested.mkdir(parents=True)
            (nested / valid_name).write_text(valid_value, encoding="utf-8")
            (challenge_root / outside_name).write_text(outside_value, encoding="utf-8")
            (sandbox_root / symlink_name).symlink_to(Path("..") / outside_name)
            challenge_manifest = tree_manifest_sha256(
                challenge_root,
                reject_symlinks=False,
            )
            operation_kinds: dict[str, str] = {}
            operations: list[dict[str, str]] = []

            def add(kind: str, user_path: str) -> None:
                op_id = secrets.token_hex(16)
                operation_kinds[op_id] = kind
                operations.append({"op_id": op_id, "user_path": user_path})

            add("valid", f"{nested_name}/{valid_name}")
            add("plain", f"../{outside_name}")
            add("encoded", f"%2e%2e/{outside_name}")
            add("double_encoded", f"%252e%252e%252f{outside_name}")
            add("symlink", symlink_name)
            selected_launcher = launcher or AttestedWorkerLauncher.from_environment()
            raw = selected_launcher.run(
                case_id="coding.path-boundary",
                workspace=root,
                challenge_root=challenge_root,
                challenge={"operations": operations, "root_relative": root_name},
                request_handler=_reject_unexpected_request,
            )
            _validate_path_raw_outcome(
                raw,
                operation_kinds=operation_kinds,
                valid_value=valid_value,
            )
            if tree_manifest_sha256(challenge_root, reject_symlinks=False) != challenge_manifest:
                raise CandidateObservationError("candidate changed the controller challenge tree")
        if tree_manifest_sha256(root, reject_symlinks=True) != pre_manifest:
            raise CandidateObservationError("candidate changed the source workspace during grading")
        checks.extend(
            [
                "valid nested path preserved",
                "plain traversal rejected",
                "encoded traversal rejected",
                "double-encoded traversal rejected",
                "symlink escape rejected",
                "controller-owned randomized outcomes validated",
            ]
        )
        return _passing("all path-boundary outcomes pass", checks)
    except WorkerInfrastructureError:
        raise
    except Exception as exc:  # noqa: BLE001 - all candidate-shaped input fails closed
        return _failing(str(exc))


def evaluate_concurrent_cache(
    workspace: str | Path,
    *,
    launcher: WorkerLauncher | None = None,
) -> dict[str, Any]:
    try:
        root = Path(workspace).resolve(strict=True)
        pre_manifest, checks = _validate_workspace(
            root,
            module_name="cache.py",
            class_name="TTLCache",
            method_name="get_or_load",
            parameter_names=("self", "key", "loader"),
            test_name="tests/test_cache.py",
        )
        value_a = f"cache-a-{secrets.token_hex(24)}"
        value_b = f"cache-b-{secrets.token_hex(24)}"
        recovered = f"cache-recovered-{secrets.token_hex(24)}"
        failure_token = f"seeded-failure-{secrets.token_hex(24)}"
        trap = f"unexpected-live-loader-{secrets.token_hex(24)}"
        counts = {name: 0 for name in ("shared", "live_trap", "expired", "failure", "recovery")}
        request_ids: set[str] = set()
        active_nonce: str | None = None

        def handle_loader(message: dict[str, Any]) -> dict[str, Any]:
            nonlocal active_nonce
            _require_exact_keys(
                message,
                {"kind", "loader_id", "request_id", "run_nonce", "version"},
            )
            loader_id = message["loader_id"]
            request_id = message["request_id"]
            nonce = message["run_nonce"]
            if (
                message["kind"] != "loader_request"
                or message["version"] != PROTOCOL_VERSION
                or not isinstance(loader_id, str)
                or loader_id not in counts
                or not isinstance(request_id, str)
                or not request_id
                or request_id in request_ids
                or not isinstance(nonce, str)
            ):
                raise CandidateObservationError("candidate loader request is invalid or replayed")
            if active_nonce is None:
                active_nonce = nonce
            elif active_nonce != nonce:
                raise CandidateObservationError("candidate loader request changed run scope")
            request_ids.add(request_id)
            counts[loader_id] += 1
            if loader_id == "shared" and counts[loader_id] == 1:
                time.sleep(0.04)
            action = "raise" if loader_id == "failure" else "return"
            values = {
                "shared": value_a,
                "live_trap": trap,
                "expired": value_b,
                "failure": failure_token,
                "recovery": recovered,
            }
            return {
                "action": action,
                "kind": "loader_response",
                "request_id": request_id,
                "run_nonce": nonce,
                "value": values[loader_id],
                "version": PROTOCOL_VERSION,
            }

        selected_launcher = launcher or AttestedWorkerLauncher.from_environment()
        raw = selected_launcher.run(
            case_id="coding.concurrent-cache",
            workspace=root,
            challenge_root=None,
            challenge={
                "clock_expired": 15.1,
                "clock_initial": 10.0,
                "clock_live": 14.9,
                "failure_key": f"failure-{secrets.token_hex(16)}",
                "shared_key": f"shared-{secrets.token_hex(16)}",
                "thread_count": 8,
                "ttl_seconds": 5.0,
            },
            request_handler=handle_loader,
        )
        _validate_cache_raw_outcome(
            raw,
            counts=counts,
            value_a=value_a,
            value_b=value_b,
            recovered=recovered,
            failure_token=failure_token,
        )
        if tree_manifest_sha256(root, reject_symlinks=True) != pre_manifest:
            raise CandidateObservationError("candidate changed the source workspace during grading")
        checks.extend(
            [
                "same-key concurrent loads coalesced",
                "live cached value reused",
                "TTL expiry enforced",
                "exceptions are not cached",
                "controller-owned randomized outcomes validated",
            ]
        )
        return _passing("all cache outcomes pass", checks)
    except WorkerInfrastructureError:
        raise
    except Exception as exc:  # noqa: BLE001 - all candidate-shaped input fails closed
        return _failing(str(exc))


def _validate_path_raw_outcome(
    raw: Mapping[str, Any],
    *,
    operation_kinds: Mapping[str, str],
    valid_value: str,
) -> None:
    _require_exact_keys(
        raw,
        {"case_id", "kind", "operations", "run_nonce", "version"},
    )
    operations = raw["operations"]
    if not isinstance(operations, list) or len(operations) != len(operation_kinds):
        raise CandidateObservationError("candidate returned an incomplete path outcome set")
    observed: set[str] = set()
    for item in operations:
        if not isinstance(item, Mapping):
            raise CandidateObservationError("candidate path outcome is not an object")
        op_id = item.get("op_id")
        if not isinstance(op_id, str) or op_id in observed or op_id not in operation_kinds:
            raise CandidateObservationError(
                "candidate path outcome identity is invalid or replayed"
            )
        observed.add(op_id)
        kind = operation_kinds[op_id]
        if kind == "valid":
            if set(item) != {"op_id", "outcome", "value"}:
                raise CandidateObservationError("valid path outcome has forbidden fields")
            if item["outcome"] != "return" or item["value"] != valid_value:
                raise CandidateObservationError("valid nested path no longer returns exact bytes")
        else:
            if set(item) != {"exception", "op_id", "outcome"}:
                raise CandidateObservationError(f"{kind} path outcome has forbidden fields")
            exception = item["exception"]
            if (
                item["outcome"] != "exception"
                or not isinstance(exception, Mapping)
                or set(exception) != {"message", "module", "name"}
                or exception["name"] != "PathBoundaryError"
            ):
                raise CandidateObservationError(
                    f"{kind} path was not rejected as PathBoundaryError"
                )
    if observed != set(operation_kinds):
        raise CandidateObservationError("candidate path outcome omitted an operation")


def _validate_cache_raw_outcome(
    raw: Mapping[str, Any],
    *,
    counts: Mapping[str, int],
    value_a: str,
    value_b: str,
    recovered: str,
    failure_token: str,
) -> None:
    _require_exact_keys(
        raw,
        {
            "case_id",
            "concurrent",
            "expired",
            "failure",
            "kind",
            "live",
            "recovery",
            "run_nonce",
            "version",
        },
    )
    concurrent = raw["concurrent"]
    if not isinstance(concurrent, list) or len(concurrent) != 8:
        raise CandidateObservationError("candidate returned incomplete concurrent outcomes")
    if any(not _is_return(item, value_a) for item in concurrent):
        raise CandidateObservationError("concurrent callers did not all receive the loaded value")
    if counts != {
        "shared": 1,
        "live_trap": 0,
        "expired": 1,
        "failure": 1,
        "recovery": 1,
    }:
        raise CandidateObservationError(f"loader invocation counts are wrong: {dict(counts)}")
    if not _is_return(raw["live"], value_a):
        raise CandidateObservationError("live cached value was not reused")
    if not _is_return(raw["expired"], value_b):
        raise CandidateObservationError("expired value was not refreshed")
    failure = raw["failure"]
    if not isinstance(failure, Mapping) or set(failure) != {"exception", "outcome"}:
        raise CandidateObservationError("seeded loader failure outcome is malformed")
    exception = failure["exception"]
    if (
        failure["outcome"] != "exception"
        or not isinstance(exception, Mapping)
        or set(exception) != {"message", "module", "name"}
        or exception["name"] != "_SeededLoaderFailure"
        or exception["message"] != failure_token
    ):
        raise CandidateObservationError("loader exception was swallowed or changed")
    if not _is_return(raw["recovery"], recovered):
        raise CandidateObservationError("loader exception was cached or left a tombstone")


def _is_return(value: Any, expected: str) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"outcome", "value"}
        and value["outcome"] == "return"
        and value["value"] == expected
    )


def _validate_workspace(
    workspace: Path,
    *,
    module_name: str,
    class_name: str,
    method_name: str,
    parameter_names: Sequence[str],
    test_name: str,
) -> tuple[str, list[str]]:
    allowed = {
        ".echo-eval/run-tests",
        ".echo-eval/run-tests.cmd",
        module_name,
        "pyproject.toml",
        "tests/.gitkeep",
        test_name,
    }
    pre_manifest, observed = _tree_manifest_and_files(
        workspace,
        reject_symlinks=True,
    )
    unexpected = sorted(set(observed) - allowed)
    if unexpected:
        raise CandidateObservationError(f"unrelated files changed or added: {unexpected}")
    if module_name not in observed:
        raise CandidateObservationError(f"candidate module is missing: {module_name}")
    if test_name not in observed:
        raise CandidateObservationError("focused regression tests were not added")
    pyproject = _read_regular_text(workspace / "pyproject.toml")
    if pyproject != _EXPECTED_PYPROJECT:
        raise CandidateObservationError("unrelated pyproject.toml was modified")
    source = _read_regular_text(workspace / module_name)
    tree = ast.parse(source, filename=module_name)
    classes = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    if len(classes) != 1:
        raise CandidateObservationError(f"candidate must define exactly one {class_name}")
    methods = [
        node
        for node in classes[0].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    ]
    if len(methods) != 1 or isinstance(methods[0], ast.AsyncFunctionDef):
        raise CandidateObservationError(f"{class_name}.{method_name} public API changed")
    arguments = methods[0].args
    names = [argument.arg for argument in (*arguments.posonlyargs, *arguments.args)]
    if (
        names != list(parameter_names)
        or arguments.vararg is not None
        or arguments.kwarg is not None
        or arguments.kwonlyargs
        or arguments.defaults
    ):
        raise CandidateObservationError(f"{class_name}.{method_name} public signature changed")
    test_source = _read_regular_text(workspace / test_name)
    test_tree = ast.parse(test_source, filename=test_name)
    if not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        for node in ast.walk(test_tree)
    ):
        raise CandidateObservationError("focused regression test file defines no test")
    return pre_manifest, [
        "no unrelated diff",
        "public API unchanged",
        "focused tests added",
    ]


def _read_regular_text(path: Path) -> str:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise CandidateObservationError(f"expected a regular evaluator input: {path}")
    if metadata.st_size > MAX_SOURCE_BYTES:
        raise CandidateObservationError(f"evaluator input is too large: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if _tree_stat_identity(before) != _tree_stat_identity(metadata):
            raise CandidateObservationError(f"evaluator input changed while opening: {path}")
        content = bytearray()
        while len(content) <= MAX_SOURCE_BYTES:
            chunk = os.read(descriptor, min(64 * 1024, MAX_SOURCE_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(content) > MAX_SOURCE_BYTES
            or len(content) != after.st_size
            or _tree_stat_identity(before) != _tree_stat_identity(after)
        ):
            raise CandidateObservationError(f"evaluator input drifted while reading: {path}")
        try:
            return bytes(content).decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise CandidateObservationError(f"evaluator input is not valid UTF-8: {path}") from exc
    finally:
        os.close(descriptor)


def tree_manifest_sha256(root: Path | None, *, reject_symlinks: bool) -> str:
    digest, _regular_files = _tree_manifest_and_files(
        root,
        reject_symlinks=reject_symlinks,
    )
    return digest


def _tree_manifest_and_files(
    root: Path | None,
    *,
    reject_symlinks: bool,
) -> tuple[str, dict[str, os.stat_result]]:
    if root is None:
        raise ValueError("tree manifest root is required")
    root = Path(root)
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise CandidateObservationError(f"tree root is unavailable: {root}") from exc
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise CandidateObservationError("tree root must be a non-symlink directory")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        root_descriptor = os.open(root, flags)
    except OSError as exc:
        raise CandidateObservationError("tree root is unsafe to open") from exc
    rows: list[dict[str, Any]] = []
    regular_files: dict[str, os.stat_result] = {}
    state = {"entries": 0, "total_bytes": 0}
    try:
        if _tree_stat_identity(os.fstat(root_descriptor)) != _tree_stat_identity(root_metadata):
            raise CandidateObservationError("tree root changed while opening")
        _scan_tree_directory(
            root_descriptor,
            relative_parts=(),
            reject_symlinks=reject_symlinks,
            regular_files=regular_files,
            rows=rows,
            state=state,
        )
    finally:
        os.close(root_descriptor)
    rows.sort(key=lambda row: str(row["path"]))
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), regular_files


def _scan_tree_directory(
    directory_descriptor: int,
    *,
    relative_parts: tuple[str, ...],
    reject_symlinks: bool,
    regular_files: dict[str, os.stat_result],
    rows: list[dict[str, Any]],
    state: dict[str, int],
) -> None:
    before_directory = os.fstat(directory_descriptor)
    try:
        names: list[str] = []
        with os.scandir(directory_descriptor) as entries:
            for entry in entries:
                state["entries"] += 1
                if state["entries"] > MAX_TREE_ENTRIES:
                    raise CandidateObservationError("tree entry-count limit exceeded")
                names.append(entry.name)
    except CandidateObservationError:
        raise
    except OSError as exc:
        raise CandidateObservationError("tree directory is unsafe to scan") from exc
    for name in sorted(names):
        parts = (*relative_parts, name)
        if len(parts) > MAX_TREE_DEPTH:
            raise CandidateObservationError("tree depth limit exceeded")
        relative = "/".join(parts)
        try:
            relative_bytes = relative.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise CandidateObservationError("tree path is not valid UTF-8") from exc
        if len(relative_bytes) > MAX_TREE_PATH_BYTES:
            raise CandidateObservationError("tree path-byte limit exceeded")
        try:
            metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        except OSError as exc:
            raise CandidateObservationError(
                f"tree entry changed while scanning: {relative}"
            ) from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            if reject_symlinks:
                raise CandidateObservationError(f"tree contains a forbidden symlink: {relative}")
            try:
                target = os.readlink(name, dir_fd=directory_descriptor)
                after = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
                target_bytes = target.encode("utf-8", errors="strict")
            except (OSError, UnicodeError) as exc:
                raise CandidateObservationError(
                    f"tree symlink changed while scanning: {relative}"
                ) from exc
            if _tree_stat_identity(metadata) != _tree_stat_identity(after):
                raise CandidateObservationError(f"tree symlink drifted while read: {relative}")
            if len(target_bytes) > MAX_TREE_PATH_BYTES:
                raise CandidateObservationError("tree symlink target-byte limit exceeded")
            rows.append({"kind": "symlink", "mode": mode, "path": relative, "target": target})
            continue
        if stat.S_ISDIR(metadata.st_mode):
            rows.append({"kind": "directory", "mode": mode, "path": relative})
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                child_descriptor = os.open(
                    name,
                    flags,
                    dir_fd=directory_descriptor,
                )
            except OSError as exc:
                raise CandidateObservationError(
                    f"tree directory is unsafe to open: {relative}"
                ) from exc
            try:
                if _tree_stat_identity(os.fstat(child_descriptor)) != _tree_stat_identity(metadata):
                    raise CandidateObservationError(
                        f"tree directory changed while opening: {relative}"
                    )
                _scan_tree_directory(
                    child_descriptor,
                    relative_parts=parts,
                    reject_symlinks=reject_symlinks,
                    regular_files=regular_files,
                    rows=rows,
                    state=state,
                )
            finally:
                os.close(child_descriptor)
            continue
        if stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1 and reject_symlinks:
                raise CandidateObservationError(f"tree file has unsafe links: {relative}")
            digest, size = _hash_tree_regular_at(
                directory_descriptor,
                name,
                metadata,
                relative=relative,
            )
            state["total_bytes"] += size
            if state["total_bytes"] > MAX_TREE_TOTAL_BYTES:
                raise CandidateObservationError("tree total-byte limit exceeded")
            rows.append(
                {
                    "kind": "file",
                    "mode": mode,
                    "path": relative,
                    "sha256": digest,
                    "size": size,
                }
            )
            regular_files[relative] = metadata
            continue
        raise CandidateObservationError(f"tree contains a special file: {relative}")
    after_directory = os.fstat(directory_descriptor)
    if _tree_stat_identity(before_directory) != _tree_stat_identity(after_directory):
        relative = "/".join(relative_parts) or "."
        raise CandidateObservationError(f"tree directory drifted while scanning: {relative}")


def _hash_tree_regular_at(
    directory_descriptor: int,
    name: str,
    metadata: os.stat_result,
    *,
    relative: str,
) -> tuple[str, int]:
    if metadata.st_size > MAX_SOURCE_BYTES:
        raise CandidateObservationError(f"tree file exceeds the byte limit: {relative}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError as exc:
        raise CandidateObservationError(f"tree file is unsafe to open: {relative}") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or _tree_stat_identity(before) != _tree_stat_identity(
            metadata
        ):
            raise CandidateObservationError(f"tree file changed while opening: {relative}")
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_SOURCE_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SOURCE_BYTES:
                raise CandidateObservationError(f"tree file exceeds the byte limit: {relative}")
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise CandidateObservationError(f"tree file changed while reading: {relative}") from exc
    finally:
        os.close(descriptor)
    if total != after.st_size or _tree_stat_identity(before) != _tree_stat_identity(after):
        raise CandidateObservationError(f"tree file drifted while read: {relative}")
    return digest.hexdigest(), total


def _tree_stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _minimal_worker_environment() -> dict[str, str]:
    return {
        "HOME": "/nonexistent",
        "PATH": os.defpath,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    *,
    error_type: type[RuntimeError] = CandidateObservationError,
) -> None:
    if set(value) != expected:
        raise error_type(f"unexpected fields: {sorted(set(value) ^ expected)}")


def _reject_unexpected_request(_message: dict[str, Any]) -> dict[str, Any]:
    raise CandidateObservationError("path candidate sent an unexpected RPC request")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _terminate_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        process.kill()
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=2.0)


def _passing(reason: str, checks: Sequence[str]) -> dict[str, Any]:
    return {"checks": list(checks), "passed": True, "reason": reason, "score": 1.0}


def _failing(reason: str) -> dict[str, Any]:
    return {"checks": [], "passed": False, "reason": reason[:4000], "score": 0.0}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2 or arguments[0] not in {
        "coding.concurrent-cache",
        "coding.path-boundary",
    }:
        print(
            "trusted verifier infrastructure invalid: expected CASE_ID WORKSPACE", file=sys.stderr
        )
        return INFRASTRUCTURE_EXIT
    evaluator = (
        evaluate_concurrent_cache if arguments[0].endswith("cache") else evaluate_path_boundary
    )
    try:
        result = evaluator(Path(arguments[1]))
    except WorkerInfrastructureError as exc:
        print(f"trusted verifier infrastructure invalid: {exc}", file=sys.stderr)
        return INFRASTRUCTURE_EXIT
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


