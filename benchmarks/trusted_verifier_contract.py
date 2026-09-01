"""Content-bound launcher contract for trusted coding verifiers."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
CONTRACT_SCHEMA = "echo.candidate_worker_contract.v1"
CANDIDATE_API_ISOLATION_SCHEMA = "echo.candidate_api_process.v1"
RUNNER_ATTESTATION_SCHEMA = "echo.hardened_verifier_runner.v2"
CONFIG_ENV = "ECHO_CANDIDATE_WORKER_CONFIG"
CONFIG_SHA256_ENV = "ECHO_CANDIDATE_WORKER_CONFIG_SHA256"
CONTRACT_SHA256_ENV = "ECHO_CANDIDATE_WORKER_CONTRACT_SHA256"
CLI_JSON_ENV = "ECHO_CANDIDATE_WORKER_CLI_JSON"
MAX_FRAME_BYTES = 64 * 1024
MAX_FRAMES = 64
MAX_CONTRACT_BYTES = 256 * 1024

REQUIRED_ISOLATION = {
    "cgroup_kill": True,
    "cgroup_v2_controllers": ["cpu", "memory", "pids"],
    "challenge": "read-only",
    "controller_visible": False,
    "different_process": True,
    "inner_gid": 65534,
    "inner_uid": 65534,
    "network": "none-including-loopback",
    "per_invocation_probe": True,
    "pid_namespace": "private",
    "ptrace_controller": False,
    "user_namespace": "private",
    "workspace": "read-only",
}
REQUIRED_LIMITS = {
    "descriptors_bounded": True,
    "memory_bounded": True,
    "output_bytes": MAX_FRAME_BYTES * MAX_FRAMES,
    "pids_bounded": True,
    "scratch_bytes_bounded": True,
    "scratch_inodes_bounded": True,
    "wall_time_bounded": True,
}


class WorkerContractError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerContract:
    path: Path
    sha256: str
    cli: tuple[str, ...]
    worker_path: Path
    worker_sha256: str
    timeout_seconds: float


def load_contract_from_environment(*, controller_path: Path) -> WorkerContract:
    config_raw = os.environ.get(CONFIG_ENV, "").strip()
    expected_digest = os.environ.get(CONFIG_SHA256_ENV, "").strip().lower()
    expected_contract_digest = os.environ.get(CONTRACT_SHA256_ENV, "").strip().lower()
    cli_raw = os.environ.get(CLI_JSON_ENV, "").strip()
    if not config_raw or not expected_digest or not expected_contract_digest or not cli_raw:
        raise WorkerContractError("trusted candidate worker contract is not configured")
    if not _is_sha256(expected_digest) or not _is_sha256(expected_contract_digest):
        raise WorkerContractError("candidate worker contract digest is invalid")
    config_path = Path(config_raw)
    if not config_path.is_absolute():
        raise WorkerContractError("candidate worker contract path must be absolute")
    try:
        metadata = config_path.lstat()
        content = config_path.read_bytes()
    except OSError as exc:
        raise WorkerContractError(f"candidate worker contract is unreadable: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise WorkerContractError("candidate worker contract must be a regular non-symlink")
    if (
        metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or len(content) > MAX_CONTRACT_BYTES
    ):
        raise WorkerContractError("candidate worker contract is writable or oversized")
    _require_root_protected_ancestry(config_path)
    observed_digest = hashlib.sha256(content).hexdigest()
    if observed_digest != expected_digest:
        raise WorkerContractError("candidate worker contract digest changed")
    try:
        config = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
        cli_value = json.loads(cli_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise WorkerContractError("candidate worker contract JSON is invalid") from exc
    if not isinstance(config, dict) or not isinstance(cli_value, list):
        raise WorkerContractError("candidate worker contract has an invalid shape")
    _require_keys(
        config,
        {
            "authorization",
            "candidate_api_isolation_schema",
            "candidate_worker_contract_schema",
            "contract",
            "controller",
            "isolation",
            "launcher",
            "limits",
            "rpc",
            "schema",
            "worker",
        },
    )
    if (
        config["schema"] != RUNNER_ATTESTATION_SCHEMA
        or config["candidate_api_isolation_schema"] != CANDIDATE_API_ISOLATION_SCHEMA
        or config["candidate_worker_contract_schema"] != CONTRACT_SCHEMA
        or config["authorization"] is not True
    ):
        raise WorkerContractError("candidate worker contract is not authorized")
    if config["rpc"] != {
        "control_transport": "inherited-unix-stream-fd-launcher-only",
        "framing": "u32be-canonical-json-v1",
        "max_frame_bytes": MAX_FRAME_BYTES,
        "max_frames": MAX_FRAMES,
        "reserved_control_frames": ["runner_ready", "runner_complete"],
        "worker_transport": "inherited-unix-stream-fd",
    }:
        raise WorkerContractError("candidate worker RPC contract is incomplete")
    if config["isolation"] != REQUIRED_ISOLATION or config["limits"] != REQUIRED_LIMITS:
        raise WorkerContractError("candidate worker isolation contract is incomplete")
    launcher = config["launcher"]
    contract = config["contract"]
    controller = config["controller"]
    worker = config["worker"]
    if not all(isinstance(value, dict) for value in (launcher, contract, controller, worker)):
        raise WorkerContractError("candidate launcher/controller/worker identity is invalid")
    _require_exact_keys(
        launcher,
        {"argv", "executable_sha256", "module_path", "module_sha256"},
    )
    _require_exact_keys(contract, {"path", "sha256"})
    _require_exact_keys(controller, {"path", "sha256"})
    _require_exact_keys(worker, {"path", "sha256"})
    if (
        launcher["argv"] != cli_value
        or len(cli_value) != 3
        or any(not isinstance(item, str) or not item or "\x00" in item for item in cli_value)
        or cli_value[1] != "-I"
    ):
        raise WorkerContractError("candidate launcher argv is not content-bound")
    executable = Path(cli_value[0])
    if (
        not executable.is_absolute()
        or _root_protected_file_sha256(executable) != launcher["executable_sha256"]
    ):
        raise WorkerContractError("candidate launcher executable identity changed")
    launcher_module = Path(str(launcher["module_path"]))
    if (
        not launcher_module.is_absolute()
        or Path(cli_value[2]) != launcher_module
        or _root_protected_file_sha256(launcher_module) != launcher["module_sha256"]
    ):
        raise WorkerContractError("candidate launcher module identity changed")
    try:
        expected_controller = controller_path.resolve(strict=True)
        controller_identity = Path(str(controller["path"])).resolve(strict=True)
        expected_contract = Path(__file__).resolve(strict=True)
        contract_identity = Path(str(contract["path"])).resolve(strict=True)
        expected_worker = expected_controller.with_name("trusted_verifier_worker.py").resolve(
            strict=True
        )
        worker_path = Path(str(worker["path"]))
        worker_identity = worker_path.resolve(strict=True)
    except OSError as exc:
        raise WorkerContractError("trusted verifier source identity is unavailable") from exc
    if (
        controller_identity != expected_controller
        or _root_protected_file_sha256(expected_controller) != controller["sha256"]
    ):
        raise WorkerContractError("trusted verifier controller identity changed")
    if (
        contract_identity != expected_contract
        or expected_contract != expected_controller.with_name("trusted_verifier_contract.py")
        or contract["sha256"] != expected_contract_digest
        or _root_protected_file_sha256(expected_contract) != expected_contract_digest
    ):
        raise WorkerContractError("trusted verifier contract identity changed")
    worker_digest = str(worker["sha256"])
    if (
        not worker_path.is_absolute()
        or worker_identity != expected_worker
        or not _is_sha256(worker_digest)
        or _root_protected_file_sha256(worker_path) != worker_digest
    ):
        raise WorkerContractError("candidate worker source identity changed")
    return WorkerContract(
        path=config_path,
        sha256=observed_digest,
        cli=tuple(cli_value),
        worker_path=worker_path,
        worker_sha256=worker_digest,
        timeout_seconds=30.0,
    )


def validate_runner_ready(
    message: Mapping[str, Any],
    *,
    run_nonce: str,
    contract_sha256: str,
) -> None:
    _require_exact_keys(
        message,
        {"contract_sha256", "isolation", "kind", "run_nonce", "version"},
    )
    if (
        message["kind"] != "runner_ready"
        or message["version"] != PROTOCOL_VERSION
        or message["run_nonce"] != run_nonce
        or message["contract_sha256"] != contract_sha256
        or message["isolation"] != REQUIRED_ISOLATION
    ):
        raise WorkerContractError("candidate runner did not attest the required isolation")


def validate_runner_complete(
    message: Mapping[str, Any],
    *,
    run_nonce: str,
    workspace_manifest: str,
    challenge_manifest: str,
) -> None:
    _require_exact_keys(
        message,
        {
            "challenge_manifest_sha256",
            "kind",
            "run_nonce",
            "tree_terminated",
            "version",
            "worker_exit_code",
            "workspace_manifest_sha256",
        },
    )
    worker_exit_code = message.get("worker_exit_code")
    if (
        message["kind"] != "runner_complete"
        or message["version"] != PROTOCOL_VERSION
        or message["run_nonce"] != run_nonce
        or message["tree_terminated"] is not True
        or isinstance(worker_exit_code, bool)
        or not isinstance(worker_exit_code, int)
        or not -255 <= worker_exit_code <= 255
        or message["workspace_manifest_sha256"] != workspace_manifest
        or message["challenge_manifest_sha256"] != challenge_manifest
    ):
        raise WorkerContractError("candidate runner completion proof is invalid")


def minimal_launcher_environment() -> dict[str, str]:
    output = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    for name in ("LANG", "LC_ALL"):
        if name in os.environ:
            output[name] = os.environ[name]
    return output


def _file_sha256(path: Path) -> str:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError("not a regular file")
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise WorkerContractError(f"content-bound executable is unavailable: {path}") from exc


def _root_protected_file_sha256(path: Path) -> str:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise WorkerContractError(f"content-bound source is unavailable: {path}") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise WorkerContractError(f"content-bound source is not root protected: {path}")
    _require_root_protected_ancestry(path)
    return _file_sha256(path)


def _require_root_protected_ancestry(path: Path) -> None:
    try:
        current = path.resolve(strict=True).parent
        while True:
            metadata = current.stat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise WorkerContractError(
                    f"content-bound path ancestry is not root protected: {current}"
                )
            if current.parent == current:
                return
            current = current.parent
    except OSError as exc:
        raise WorkerContractError(f"content-bound path ancestry is unavailable: {path}") from exc


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _require_exact_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise WorkerContractError(f"unexpected contract fields: {sorted(set(value) ^ expected)}")


def _require_keys(value: Mapping[str, Any], required: set[str]) -> None:
    missing = required - set(value)
    if missing:
        raise WorkerContractError(f"missing contract fields: {sorted(missing)}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


__all__ = [
    "CLI_JSON_ENV",
    "CANDIDATE_API_ISOLATION_SCHEMA",
    "CONFIG_ENV",
    "CONFIG_SHA256_ENV",
    "CONTRACT_SCHEMA",
    "CONTRACT_SHA256_ENV",
    "MAX_FRAME_BYTES",
    "MAX_FRAMES",
    "RUNNER_ATTESTATION_SCHEMA",
    "WorkerContract",
    "WorkerContractError",
    "load_contract_from_environment",
    "minimal_launcher_environment",
    "validate_runner_complete",
    "validate_runner_ready",
]


