from __future__ import annotations

import builtins
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from benchmarks import trusted_verifier_controller as trusted_controller
from benchmarks.fixed_suite_fixtures import prepare_coding_fixture_suite
from benchmarks.linux_hardened_verifier import (
    CANDIDATE_API_ISOLATION_SCHEMA as LAUNCHER_CANDIDATE_API_SCHEMA,
)
from benchmarks.linux_hardened_verifier import (
    trusted_controller_tree_manifest_sha256 as launcher_tree_manifest_sha256,
)
from benchmarks.trusted_verifier_contract import (
    CANDIDATE_API_ISOLATION_SCHEMA,
    REQUIRED_ISOLATION,
    WorkerContract,
    WorkerContractError,
    validate_runner_complete,
    validate_runner_ready,
)
from benchmarks.trusted_verifier_controller import (
    INFRASTRUCTURE_EXIT,
    MAX_FRAME_BYTES,
    AttestedWorkerLauncher,
    CandidateObservationError,
    UnsafeLocalWorkerLauncher,
    WorkerInfrastructureError,
    _exchange_raw_outcome,
    _FramedChannel,
    _receive_control_message,
    evaluate_concurrent_cache,
    evaluate_path_boundary,
)
from benchmarks.trusted_verifier_worker import (
    CANDIDATE_API_ISOLATION_SCHEMA as WORKER_CANDIDATE_API_SCHEMA,
)
from benchmarks.trusted_verifier_worker import (
    CANDIDATE_FAILURE_EXIT,
    run_trusted_supervisor,
)
from benchmarks.verifiers import verify_path_boundary

REPO_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
_CORRECT_PATH_SOURCE = """\
from pathlib import Path
from urllib.parse import unquote

class PathBoundaryError(ValueError):
    pass

class FileService:
    def __init__(self, root):
        self.root = Path(root)

    def read_text(self, user_path):
        decoded = user_path
        for _ in range(4):
            updated = unquote(decoded)
            if updated == decoded:
                break
            decoded = updated
        root = self.root.resolve()
        candidate = (root / decoded).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PathBoundaryError("path escapes root") from exc
        return candidate.read_text(encoding="utf-8")
"""
_CORRECT_CACHE_SOURCE = """\
import threading

class TTLCache:
    def __init__(self, ttl_seconds, *, clock):
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.values = {}
        self.loading = {}
        self.lock = threading.Lock()

    def get_or_load(self, key, loader):
        while True:
            with self.lock:
                cached = self.values.get(key)
                if cached is not None and cached[0] > self.clock():
                    return cached[1]
                event = self.loading.get(key)
                if event is None:
                    event = threading.Event()
                    self.loading[key] = event
                    break
            event.wait()
        try:
            value = loader()
        except BaseException:
            with self.lock:
                self.loading.pop(key).set()
            raise
        with self.lock:
            self.values[key] = (self.clock() + self.ttl_seconds, value)
            self.loading.pop(key).set()
        return value
"""


def _path_workspace(tmp_path: Path, source: str = _CORRECT_PATH_SOURCE) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (workspace / "file_service.py").write_text(source, encoding="utf-8")
    (workspace / "tests" / "test_file_service.py").write_text(
        "def test_regression(): assert True\n",
        encoding="utf-8",
    )
    return workspace


def _cache_workspace(tmp_path: Path, source: str = _CORRECT_CACHE_SOURCE) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (workspace / "cache.py").write_text(source, encoding="utf-8")
    (workspace / "tests" / "test_cache.py").write_text(
        "def test_regression(): assert True\n",
        encoding="utf-8",
    )
    return workspace


def _fake_attested_launcher(tmp_path: Path, *, mode: str) -> AttestedWorkerLauncher:
    attestation = tmp_path / f"attestation-{mode}.json"
    attestation.write_text("{}\n", encoding="utf-8")
    script = tmp_path / f"launcher-{mode}.py"
    script.write_text(
        f"""\
import argparse
import hashlib
import json
import socket
import struct
from pathlib import Path

MODE = {mode!r}
ISOLATION = {REQUIRED_ISOLATION!r}

def receive(channel):
    header = channel.recv(4)
    size = struct.unpack("!I", header)[0]
    payload = b""
    while len(payload) < size:
        payload += channel.recv(size - len(payload))
    return json.loads(payload)

def send(channel, message):
    payload = json.dumps(message, sort_keys=True, separators=(",", ":")).encode()
    channel.sendall(struct.pack("!I", len(payload)) + payload)

parser = argparse.ArgumentParser()
parser.add_argument("worker")
parser.add_argument("--attestation", required=True)
parser.add_argument("--workspace-snapshot", required=True)
parser.add_argument("--workspace-manifest-sha256", required=True)
parser.add_argument("--challenge-manifest-sha256", required=True)
parser.add_argument("--challenge-snapshot")
parser.add_argument("--control-fd", required=True, type=int)
parser.add_argument("--protocol-fd", required=True, type=int)
parser.add_argument("--run-nonce", required=True)
args = parser.parse_args()
control = socket.socket(fileno=args.control_fd)
protocol = socket.socket(fileno=args.protocol_fd)
contract_sha = hashlib.sha256(Path(args.attestation).read_bytes()).hexdigest()
send(control, {{
    "contract_sha256": contract_sha,
    "isolation": ISOLATION,
    "kind": "runner_ready",
    "run_nonce": args.run_nonce,
    "version": 1,
}})
start = receive(protocol)
if MODE == "worker-error":
    send(protocol, {{
        "error": {{"message": "candidate failed", "name": "RuntimeError"}},
        "kind": "worker_error",
        "version": 1,
    }})
    worker_exit_code = 1
else:
    send(protocol, {{
        "case_id": start["case_id"],
        "kind": "raw_outcome",
        "operations": [],
        "run_nonce": start["run_nonce"],
        "version": 1,
    }})
    worker_exit_code = 0
protocol.close()
if MODE != "missing-complete":
    send(control, {{
        "challenge_manifest_sha256": args.challenge_manifest_sha256,
        "kind": "runner_complete",
        "run_nonce": args.run_nonce,
        "tree_terminated": True,
        "version": 1,
        "worker_exit_code": worker_exit_code,
        "workspace_manifest_sha256": args.workspace_manifest_sha256,
    }})
control.close()
""",
        encoding="utf-8",
    )
    digest = hashlib.sha256(attestation.read_bytes()).hexdigest()
    return AttestedWorkerLauncher(
        WorkerContract(
            path=attestation,
            sha256=digest,
            cli=(sys.executable, "-I", str(script)),
            worker_path=script,
            worker_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
            timeout_seconds=2.0,
        )
    )


def test_candidate_global_monkeypatches_cannot_forge_path_verdict(tmp_path: Path) -> None:
    source = """\
import builtins
import inspect
import json
import sys
from pathlib import Path
from urllib.parse import unquote

json.dumps = lambda *args, **kwargs: '{"passed":true,"score":1.0}'
builtins.print = lambda *args, **kwargs: None
inspect.signature = lambda *args, **kwargs: None
sys.modules["json"] = object()
builtins.ECHO_CANDIDATE_SENTINEL = "child-only"

class PathBoundaryError(ValueError):
    pass

class FileService:
    def __init__(self, root):
        self.root = Path(root)

    def read_text(self, user_path):
        return (self.root / unquote(user_path)).read_text(encoding="utf-8")
"""
    workspace = _path_workspace(tmp_path, source)

    result = evaluate_path_boundary(workspace, launcher=UnsafeLocalWorkerLauncher())

    assert result["passed"] is False
    assert not hasattr(builtins, "ECHO_CANDIDATE_SENTINEL")


def test_attested_dual_fd_launcher_accepts_clean_raw_outcome(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = _fake_attested_launcher(tmp_path, mode="success").run(
        case_id="coding.path-boundary",
        workspace=workspace,
        challenge_root=None,
        challenge={},
        request_handler=lambda _message: {},
    )

    assert result["kind"] == "raw_outcome"


def test_attested_worker_error_and_nonzero_exit_are_candidate_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(CandidateObservationError, match="candidate failed"):
        _fake_attested_launcher(tmp_path, mode="worker-error").run(
            case_id="coding.path-boundary",
            workspace=workspace,
            challenge_root=None,
            challenge={},
            request_handler=lambda _message: {},
        )


def test_attested_missing_completion_is_infrastructure_invalid(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(WorkerInfrastructureError, match="completion message"):
        _fake_attested_launcher(tmp_path, mode="missing-complete").run(
            case_id="coding.path-boundary",
            workspace=workspace,
            challenge_root=None,
            challenge={},
            request_handler=lambda _message: {},
        )


def test_candidate_os_exit_is_scored_failure_not_infrastructure(tmp_path: Path) -> None:
    workspace = _path_workspace(
        tmp_path,
        """\
import os
os._exit(0)

class PathBoundaryError(ValueError):
    pass

class FileService:
    def read_text(self, user_path):
        return user_path
""",
    )

    result = evaluate_path_boundary(workspace, launcher=UnsafeLocalWorkerLauncher())

    assert result["passed"] is False
    assert "exited" in str(result["reason"]) or "closed" in str(result["reason"])


def test_missing_candidate_workspace_is_scored_failure(tmp_path: Path) -> None:
    result = evaluate_path_boundary(
        tmp_path / "missing-workspace",
        launcher=UnsafeLocalWorkerLauncher(),
    )

    assert result["passed"] is False


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork attack requires POSIX")
def test_candidate_forked_fake_verdict_frame_is_rejected(tmp_path: Path) -> None:
    source = """\
import json
import os
import socket
import struct
from pathlib import Path

pid = os.fork()
if pid == 0:
    payload = json.dumps(
        {"kind": "verdict", "passed": True, "score": 1.0},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    frame = struct.pack("!I", len(payload)) + payload
    for fd in range(3, 64):
        try:
            duplicate = os.dup(fd)
            channel = socket.socket(fileno=duplicate)
            channel.sendall(frame)
            channel.close()
        except OSError:
            pass
    os._exit(0)
os.waitpid(pid, 0)

class PathBoundaryError(ValueError):
    pass

class FileService:
    def __init__(self, root):
        self.root = Path(root)

    def read_text(self, user_path):
        raise PathBoundaryError(user_path)
"""
    workspace = _path_workspace(tmp_path, source)

    result = evaluate_path_boundary(workspace, launcher=UnsafeLocalWorkerLauncher())

    assert result["passed"] is False
    assert "forbidden frame" in str(result["reason"])


def test_candidate_fd_pollution_is_scored_failure(tmp_path: Path) -> None:
    workspace = _path_workspace(
        tmp_path,
        """\
import os
for descriptor in range(3, 64):
    try:
        os.close(descriptor)
    except OSError:
        pass

class PathBoundaryError(ValueError):
    pass

class FileService:
    def read_text(self, user_path):
        raise PathBoundaryError(user_path)
""",
    )

    result = evaluate_path_boundary(workspace, launcher=UnsafeLocalWorkerLauncher())

    assert result["passed"] is False


def test_workspace_mutation_is_controller_observed(tmp_path: Path) -> None:
    workspace = _path_workspace(tmp_path)

    class MutatingLauncher:
        attested = True

        def run(
            self,
            *,
            case_id: str,
            workspace: Path,
            challenge_root: Path | None,
            challenge: dict[str, Any],
            request_handler: Any,
        ) -> dict[str, Any]:
            del request_handler
            assert case_id == "coding.path-boundary"
            assert challenge_root is not None
            (workspace / "file_service.py").write_text(
                _CORRECT_PATH_SOURCE + "\n# changed during verification\n",
                encoding="utf-8",
            )
            outcomes = []
            for operation in challenge["operations"]:
                if operation["user_path"].startswith("nested-"):
                    value = (
                        challenge_root / challenge["root_relative"] / operation["user_path"]
                    ).read_text(encoding="utf-8")
                    outcomes.append(
                        {"op_id": operation["op_id"], "outcome": "return", "value": value}
                    )
                else:
                    outcomes.append(
                        {
                            "exception": {
                                "message": "blocked",
                                "module": "candidate_file_service",
                                "name": "PathBoundaryError",
                            },
                            "op_id": operation["op_id"],
                            "outcome": "exception",
                        }
                    )
            return {
                "case_id": case_id,
                "kind": "raw_outcome",
                "operations": outcomes,
                "run_nonce": "opaque",
                "version": 1,
            }

    result = evaluate_path_boundary(workspace, launcher=MutatingLauncher())

    assert result["passed"] is False
    assert "changed the source workspace" in str(result["reason"])


def test_replayed_loader_request_is_scored_failure(tmp_path: Path) -> None:
    workspace = _cache_workspace(tmp_path)

    class ReplayLauncher:
        attested = True

        def run(self, *, request_handler: Any, **_kwargs: Any) -> dict[str, Any]:
            request = {
                "kind": "loader_request",
                "loader_id": "shared",
                "request_id": "duplicate",
                "run_nonce": "n" * 64,
                "version": 1,
            }
            request_handler(request)
            request_handler(request)
            raise AssertionError("replay unexpectedly accepted")

    result = evaluate_concurrent_cache(workspace, launcher=ReplayLauncher())

    assert result["passed"] is False
    assert "replayed" in str(result["reason"])


def test_oversized_length_prefix_is_rejected_before_allocation() -> None:
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    channel = _FramedChannel(parent, timeout_seconds=1.0)
    try:
        child.sendall(struct.pack("!I", MAX_FRAME_BYTES + 1))
        with pytest.raises(CandidateObservationError, match="length"):
            channel.receive()
    finally:
        parent.close()
        child.close()


def test_tree_manifest_enforces_fixed_prelaunch_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert trusted_controller.MAX_TREE_ENTRIES == 20_000
    assert trusted_controller.MAX_TREE_DEPTH == 48
    assert trusted_controller.MAX_TREE_PATH_BYTES == 2_048
    assert trusted_controller.MAX_SOURCE_BYTES == 256 * 1_024
    assert trusted_controller.MAX_TREE_TOTAL_BYTES == 256 * 1_024 * 1_024

    entry_root = tmp_path / "entries"
    entry_root.mkdir()
    for index in range(3):
        (entry_root / f"entry-{index}").write_text("x", encoding="utf-8")
    monkeypatch.setattr(trusted_controller, "MAX_TREE_ENTRIES", 2)
    with pytest.raises(CandidateObservationError, match="entry-count"):
        trusted_controller.tree_manifest_sha256(entry_root, reject_symlinks=True)

    depth_root = tmp_path / "depth"
    (depth_root / "a" / "b" / "c").mkdir(parents=True)
    monkeypatch.setattr(trusted_controller, "MAX_TREE_ENTRIES", 20_000)
    monkeypatch.setattr(trusted_controller, "MAX_TREE_DEPTH", 2)
    with pytest.raises(CandidateObservationError, match="depth"):
        trusted_controller.tree_manifest_sha256(depth_root, reject_symlinks=True)

    path_root = tmp_path / "path"
    path_root.mkdir()
    (path_root / "too-long").write_text("x", encoding="utf-8")
    monkeypatch.setattr(trusted_controller, "MAX_TREE_DEPTH", 48)
    monkeypatch.setattr(trusted_controller, "MAX_TREE_PATH_BYTES", 4)
    with pytest.raises(CandidateObservationError, match="path-byte"):
        trusted_controller.tree_manifest_sha256(path_root, reject_symlinks=True)

    size_root = tmp_path / "size"
    size_root.mkdir()
    (size_root / "value").write_bytes(b"1234")
    monkeypatch.setattr(trusted_controller, "MAX_TREE_PATH_BYTES", 2_048)
    monkeypatch.setattr(trusted_controller, "MAX_SOURCE_BYTES", 3)
    with pytest.raises(CandidateObservationError, match="file exceeds"):
        trusted_controller.tree_manifest_sha256(size_root, reject_symlinks=True)

    monkeypatch.setattr(trusted_controller, "MAX_SOURCE_BYTES", 256 * 1_024)
    monkeypatch.setattr(trusted_controller, "MAX_TREE_TOTAL_BYTES", 3)
    with pytest.raises(CandidateObservationError, match="total-byte"):
        trusted_controller.tree_manifest_sha256(size_root, reject_symlinks=True)


def test_tree_manifest_rejects_file_drift_during_nofollow_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    target = root / "value"
    target.write_bytes(b"stable")
    original_read = os.read
    changed = False

    def drifting_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        chunk = original_read(descriptor, size)
        if chunk and not changed:
            changed = True
            with target.open("ab") as handle:
                handle.write(b"!")
        return chunk

    monkeypatch.setattr(trusted_controller.os, "read", drifting_read)
    with pytest.raises(CandidateObservationError, match="drifted"):
        trusted_controller.tree_manifest_sha256(root, reject_symlinks=True)


def test_controller_and_launcher_manifest_algorithms_remain_identical(
    tmp_path: Path,
) -> None:
    workspace = _path_workspace(tmp_path)
    assert trusted_controller.tree_manifest_sha256(
        workspace,
        reject_symlinks=True,
    ) == launcher_tree_manifest_sha256(workspace, reject_symlinks=True)

    challenge = tmp_path / "challenge"
    sandbox = challenge / "sandbox"
    sandbox.mkdir(parents=True)
    (challenge / "outside").write_text("secret", encoding="utf-8")
    (sandbox / "link").symlink_to("../outside")
    assert trusted_controller.tree_manifest_sha256(
        challenge,
        reject_symlinks=False,
    ) == launcher_tree_manifest_sha256(challenge, reject_symlinks=False)


def test_duplicate_key_and_noncanonical_frame_is_rejected() -> None:
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    channel = _FramedChannel(parent, timeout_seconds=1.0)
    payload = b'{"kind":"raw_outcome","kind":"verdict"}'
    try:
        child.sendall(struct.pack("!I", len(payload)) + payload)
        with pytest.raises(CandidateObservationError, match="malformed"):
            channel.receive()
    finally:
        parent.close()
        child.close()


def test_deeply_nested_frame_is_scored_as_malformed() -> None:
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    channel = _FramedChannel(parent, timeout_seconds=1.0)
    payload = b'{"value":' + b"[" * 10_000 + b"0" + b"]" * 10_000 + b"}"
    sender = threading.Thread(
        target=child.sendall,
        args=(struct.pack("!I", len(payload)) + payload,),
        daemon=True,
    )
    try:
        sender.start()
        with pytest.raises(CandidateObservationError, match="malformed"):
            channel.receive()
        sender.join(timeout=1.0)
        assert not sender.is_alive()
    finally:
        parent.close()
        child.close()


@pytest.mark.parametrize(
    "forged_kind",
    ["raw_outcome", "candidate_path_observations", "verdict"],
)
def test_narrow_candidate_channel_cannot_emit_aggregate_frame(
    forged_kind: str,
) -> None:
    controller_peer, controller_supervisor = socket.socketpair(
        socket.AF_UNIX,
        socket.SOCK_STREAM,
    )
    candidate_supervisor, candidate_peer = socket.socketpair(
        socket.AF_UNIX,
        socket.SOCK_STREAM,
    )
    result: list[int | BaseException] = []

    def supervise() -> None:
        try:
            result.append(
                run_trusted_supervisor(
                    controller_supervisor.fileno(),
                    candidate_supervisor.fileno(),
                    timeout_seconds=1.0,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            result.append(exc)

    thread = threading.Thread(target=supervise, daemon=True)
    controller = _FramedChannel(controller_peer, timeout_seconds=1.0)
    candidate = _FramedChannel(candidate_peer, timeout_seconds=1.0)
    nonce = "a" * 64
    try:
        thread.start()
        controller.send(
            {
                "case_id": "coding.path-boundary",
                "challenge": {
                    "operations": [{"op_id": "valid", "user_path": "nested/value.txt"}],
                    "root_relative": "root",
                },
                "kind": "start",
                "run_nonce": nonce,
                "version": 1,
            }
        )
        initialization = candidate.receive()
        assert initialization["kind"] == "api_init"
        session = initialization["session"]
        candidate.send({"kind": "api_ready", "session": session, "version": 1})
        assert candidate.receive()["kind"] == "api_path_call"
        candidate.send(
            {
                "kind": forged_kind,
                "session": session,
                "version": 1,
            }
        )

        with pytest.raises(CandidateObservationError, match="forbidden frame"):
            _exchange_raw_outcome(
                controller,
                case_id="coding.path-boundary",
                run_nonce=nonce,
                request_handler=lambda _message: {},
            )
        thread.join(timeout=2.0)
        assert not thread.is_alive()
        assert result == [CANDIDATE_FAILURE_EXIT]
    finally:
        controller_peer.close()
        controller_supervisor.close()
        candidate_supervisor.close()
        candidate_peer.close()


def test_extra_raw_outcome_frame_is_scored_protocol_failure(tmp_path: Path) -> None:
    worker = tmp_path / "extra_worker.py"
    worker.write_text(
        """\
import argparse
import json
import socket
import struct

parser = argparse.ArgumentParser()
parser.add_argument("--protocol-fd", type=int, required=True)
parser.add_argument("--workspace")
parser.add_argument("--challenge-root")
args = parser.parse_args()
channel = socket.socket(fileno=args.protocol_fd)
size = struct.unpack("!I", channel.recv(4))[0]
payload = b""
while len(payload) < size:
    payload += channel.recv(size - len(payload))
start = json.loads(payload)
outcome = json.dumps(
    {
        "case_id": start["case_id"],
        "kind": "raw_outcome",
        "operations": [],
        "run_nonce": start["run_nonce"],
        "version": 1,
    },
    sort_keys=True,
    separators=(",", ":"),
).encode()
frame = struct.pack("!I", len(outcome)) + outcome
channel.sendall(frame + frame)
channel.close()
""",
        encoding="utf-8",
    )
    workspace = tmp_path / "empty-workspace"
    workspace.mkdir()

    with pytest.raises(CandidateObservationError, match="extra or replayed"):
        UnsafeLocalWorkerLauncher(worker_path=worker).run(
            case_id="coding.path-boundary",
            workspace=workspace,
            challenge_root=None,
            challenge={},
            request_handler=lambda _message: {},
        )


def test_missing_trusted_control_ready_is_infrastructure_invalid() -> None:
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    channel = _FramedChannel(parent, timeout_seconds=1.0)
    child.close()
    try:
        with pytest.raises(WorkerInfrastructureError, match="ready message"):
            _receive_control_message(channel, phase="ready")
    finally:
        parent.close()


def test_wrong_worker_uid_or_namespace_ready_claim_is_infrastructure_invalid() -> None:
    isolation = dict(REQUIRED_ISOLATION)
    isolation["inner_uid"] = 0
    message = {
        "contract_sha256": "a" * 64,
        "isolation": isolation,
        "kind": "runner_ready",
        "run_nonce": "b" * 64,
        "version": 1,
    }

    with pytest.raises(WorkerContractError, match="required isolation"):
        validate_runner_ready(
            message,
            run_nonce="b" * 64,
            contract_sha256="a" * 64,
        )


def test_candidate_api_schema_is_content_bound_across_all_trusted_layers() -> None:
    assert CANDIDATE_API_ISOLATION_SCHEMA == "echo.candidate_api_process.v1"
    assert WORKER_CANDIDATE_API_SCHEMA == CANDIDATE_API_ISOLATION_SCHEMA
    assert LAUNCHER_CANDIDATE_API_SCHEMA == CANDIDATE_API_ISOLATION_SCHEMA


def test_nonzero_worker_exit_is_valid_completion_evidence_for_scored_failure() -> None:
    validate_runner_complete(
        {
            "challenge_manifest_sha256": "c" * 64,
            "kind": "runner_complete",
            "run_nonce": "b" * 64,
            "tree_terminated": True,
            "version": 1,
            "worker_exit_code": 1,
            "workspace_manifest_sha256": "a" * 64,
        },
        run_nonce="b" * 64,
        workspace_manifest="a" * 64,
        challenge_manifest="c" * 64,
    )


def test_missing_runner_contract_exits_78_before_candidate_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _path_workspace(tmp_path)
    for name in (
        "ECHO_CANDIDATE_WORKER_CONFIG",
        "ECHO_CANDIDATE_WORKER_CONFIG_SHA256",
        "ECHO_CANDIDATE_WORKER_CLI_JSON",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["verify_path_boundary.py", str(workspace)])

    exit_code = verify_path_boundary.main()

    captured = capsys.readouterr()
    assert exit_code == INFRASTRUCTURE_EXIT
    assert "infrastructure invalid" in captured.err
    assert captured.out == ""


def test_absolute_controller_bootstraps_under_isolated_python(tmp_path: Path) -> None:
    workspace = _path_workspace(tmp_path)
    controller = REPO_ROOT / "benchmarks/trusted_verifier_controller.py"

    completed = subprocess.run(
        [sys.executable, "-I", str(controller), "coding.path-boundary", str(workspace)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
        timeout=10,
        check=False,
    )

    assert completed.returncode == INFRASTRUCTURE_EXIT
    assert "infrastructure invalid" in completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr


def test_absolute_controller_rejects_wrong_contract_digest_as_infrastructure(
    tmp_path: Path,
) -> None:
    workspace = _path_workspace(tmp_path)
    controller = REPO_ROOT / "benchmarks/trusted_verifier_controller.py"

    completed = subprocess.run(
        [sys.executable, "-I", str(controller), "coding.path-boundary", str(workspace)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env={
            "ECHO_CANDIDATE_WORKER_CONTRACT_SHA256": "0" * 64,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        },
        timeout=10,
        check=False,
    )

    assert completed.returncode == INFRASTRUCTURE_EXIT
    assert "contract source digest changed" in completed.stderr
    assert completed.stdout == ""


def test_candidate_failure_entrypoint_is_json_and_exit_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        verify_path_boundary,
        "_run",
        lambda _workspace: {
            "checks": [],
            "passed": False,
            "reason": "candidate protocol failed",
            "score": 0.0,
        },
    )
    monkeypatch.setattr(sys, "argv", ["verify_path_boundary.py", "/unused"])

    exit_code = verify_path_boundary.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out)["passed"] is False
    assert captured.err == ""


def test_coding_fixture_declares_exit_78_as_infrastructure(tmp_path: Path) -> None:
    prepared = prepare_coding_fixture_suite(repo_root=REPO_ROOT, runs_root=tmp_path / "runs")

    for case in prepared.cases:
        assert case.grader.hidden_verifier_infrastructure_exit_codes == frozenset(
            {INFRASTRUCTURE_EXIT}
        )

