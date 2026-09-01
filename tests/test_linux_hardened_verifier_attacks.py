"""Opt-in attacks against the real Linux hardened-verifier boundary.

These tests are intentionally not a mocked portability suite.  They consume a
root-provisioned v2 attestation and exercise the actual bubblewrap, cgroup-v2,
tmpfs, supervisor, and trusted-controller/worker chain.  Normal developer test
runs skip the module; an explicitly requested attack run fails (rather than
skips) when any required infrastructure is missing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import textwrap
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from benchmarks import linux_hardened_verifier as hardened
from benchmarks.trusted_verifier_contract import (
    CLI_JSON_ENV,
    CONFIG_ENV,
    CONFIG_SHA256_ENV,
    CONTRACT_SHA256_ENV,
)

_OPT_IN_ENV = "ECHO_RUN_HARDENED_VERIFIER_ATTACKS"
_ATTESTATION_ENV = "ECHO_HARDENED_VERIFIER_RUNNER"
_RUN_NAME = re.compile(r"^run-[0-9a-f]{32}$")

pytestmark = [
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="the hardened verifier attack suite requires Linux",
    ),
    pytest.mark.skipif(
        os.environ.get(_OPT_IN_ENV) != "1",
        reason=f"set {_OPT_IN_ENV}=1 to run destructive boundary attacks",
    ),
]


@dataclass(frozen=True, slots=True)
class _AttackContext:
    runner: hardened.LinuxHardenedVerifierRunner
    attestation: hardened.RunnerAttestation


@pytest.fixture(scope="module")
def attack_context() -> _AttackContext:
    configured = os.environ.get(_ATTESTATION_ENV, "").strip()
    if not configured:
        pytest.fail(f"{_OPT_IN_ENV}=1 requires an absolute {_ATTESTATION_ENV} attestation")
    path = Path(configured)
    if not path.is_absolute():
        pytest.fail(f"{_ATTESTATION_ENV} must be an absolute path")
    try:
        runner = hardened.LinuxHardenedVerifierRunner.from_config(path)
        provenance = runner.provenance()
    except Exception as exc:  # noqa: BLE001 - explicit opt-in must expose infra drift
        pytest.fail(f"hardened verifier infrastructure is invalid: {exc!r}")
    assert provenance["schema"] == hardened.ATTESTATION_SCHEMA
    assert provenance["authorization"] is True
    context = _AttackContext(runner=runner, attestation=runner.attestation)
    _assert_supervisor_healthy(context)
    yield context
    _assert_supervisor_healthy(context)


def _source(value: str) -> bytes:
    return textwrap.dedent(value).lstrip().encode("utf-8")


def _run_attack(
    context: _AttackContext,
    workspace: Path,
    verifier_source: bytes,
    *,
    arguments: tuple[str, ...] = (),
    timeout_seconds: float = 30.0,
) -> hardened.HardenedProcessResult:
    result = context.runner.run_hidden_verifier(
        verifier_source=verifier_source,
        verifier_source_sha256=hashlib.sha256(verifier_source).hexdigest(),
        argument_templates=arguments,
        workspace=workspace,
        timeout_seconds=timeout_seconds,
    )
    _assert_bounded_result(context, result)
    _assert_supervisor_healthy(context)
    return result


def _assert_bounded_result(
    context: _AttackContext,
    result: hardened.HardenedProcessResult,
) -> None:
    limits = context.attestation.limits
    assert result.returncode != hardened.INFRASTRUCTURE_INVALID_EXIT
    assert len(result.stdout) <= limits.stdout_max_bytes
    assert len(result.stderr) <= limits.stderr_max_bytes
    evidence = result.evidence
    assert evidence["schema"] == "echo.hardened_verifier_run.v2"
    assert evidence["authorization"] is True
    assert evidence["git_sha"] == context.attestation.git_sha
    assert evidence["runtime_pre"] == evidence["runtime_post"]
    assert evidence["workspace_pre"] == evidence["workspace_snapshot"]
    assert evidence["workspace_pre"] == evidence["workspace_post"]
    assert evidence["verifier_pre_sha256"] == evidence["verifier_post_sha256"]
    resources = evidence["resources"]
    assert resources["cgroup_reaped"] is True
    assert resources["memory_peak_bytes"] <= limits.memory_max_bytes
    assert resources["pids_peak"] <= limits.pids_max
    assert resources["scratch_used_bytes"] <= limits.scratch_max_bytes
    assert resources["scratch_used_inodes"] <= limits.scratch_max_inodes


def _assert_supervisor_healthy(context: _AttackContext) -> None:
    cgroup_residue = [
        child.name
        for child in context.attestation.cgroup_parent.iterdir()
        if child.is_dir() and _RUN_NAME.fullmatch(child.name)
    ]
    scratch_residue = [
        child.name
        for child in context.attestation.scratch_mount.iterdir()
        if _RUN_NAME.fullmatch(child.name) or child.name.startswith(".reap-")
    ]
    assert cgroup_residue == []
    assert scratch_residue == []
    provenance = context.runner.provenance()
    assert provenance["authorization"] is True


def _workspace(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    return root


def _json_stdout(result: hardened.HardenedProcessResult) -> dict[str, Any]:
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def _controller_environment(context: _AttackContext) -> dict[str, str]:
    attestation = context.attestation
    return {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.defpath,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        CONFIG_ENV: str(attestation.path),
        CONFIG_SHA256_ENV: attestation.config_sha256,
        CONTRACT_SHA256_ENV: attestation.contract_sha256,
        CLI_JSON_ENV: json.dumps(
            list(attestation.launcher_argv),
            ensure_ascii=True,
            separators=(",", ":"),
        ),
    }


def _run_controller_case(
    context: _AttackContext,
    *,
    case_id: str,
    workspace: Path,
) -> tuple[subprocess.CompletedProcess[bytes], dict[str, Any]]:
    attestation = context.attestation
    command = [
        str(attestation.launcher_executable_path),
        "-I",
        str(attestation.controller_path),
        case_id,
        str(workspace),
    ]
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=min(90.0, attestation.limits.wall_max_seconds),
        check=False,
        env=_controller_environment(context),
    )
    assert len(completed.stdout) <= attestation.limits.stdout_max_bytes
    assert len(completed.stderr) <= attestation.limits.stderr_max_bytes
    assert completed.returncode != hardened.INFRASTRUCTURE_INVALID_EXIT, (
        "trusted controller classified the run as infrastructure-invalid: "
        + completed.stderr.decode("utf-8", errors="replace")[:1000]
    )
    assert completed.returncode == 0
    verdict = json.loads(completed.stdout)
    assert isinstance(verdict, dict)
    assert set(verdict) == {"checks", "passed", "reason", "score"}
    _assert_supervisor_healthy(context)
    return completed, verdict


def test_external_filesystem_and_network_escape_attempts_are_denied(
    attack_context: _AttackContext,
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    outside_read = tmp_path / "outside-secret"
    outside_read.write_text("do-not-leak", encoding="utf-8")
    outside_write = tmp_path / "outside-write"
    original_write = workspace / "original-write"
    source = _source(
        r"""
        import json
        import socket
        import sys
        from pathlib import Path

        outside_read, outside_write, original_write = sys.argv[1:]

        def denied(action):
            try:
                action()
            except BaseException:
                return True
            return False

        outcomes = {
            "external_read_denied": denied(lambda: Path(outside_read).read_bytes()),
            "external_write_denied": denied(lambda: Path(outside_write).write_text("escape")),
            "original_workspace_write_denied": denied(
                lambda: Path(original_write).write_text("changed")
            ),
            "snapshot_write_denied": denied(
                lambda: Path("/workspace/candidate-write").write_text("changed")
            ),
            "loopback_denied": denied(
                lambda: socket.create_connection(("127.0.0.1", 1), timeout=.2)
            ),
            "public_network_denied": denied(
                lambda: socket.create_connection(("1.1.1.1", 53), timeout=.2)
            ),
        }
        print(json.dumps(outcomes, sort_keys=True))
        """
    )

    result = _run_attack(
        attack_context,
        workspace,
        source,
        arguments=(str(outside_read), str(outside_write), str(original_write)),
    )

    assert result.returncode == 0
    assert set(_json_stdout(result).values()) == {True}
    assert outside_read.read_text(encoding="utf-8") == "do-not-leak"
    assert not outside_write.exists()
    assert not original_write.exists()
    assert not (workspace / "candidate-write").exists()
    probe = result.evidence["probe"]
    assert probe["outside_read_denied"] is True
    assert probe["outside_write_denied"] is True
    assert probe["original_workspace_write_denied"] is True
    assert probe["loopback_network_denied"] is True
    assert probe["external_network_denied"] is True


@pytest.mark.parametrize(("descriptor", "reason"), [(1, "stdout_limit"), (2, "stderr_limit")])
def test_stdout_and_stderr_floods_are_bounded_candidate_failures(
    attack_context: _AttackContext,
    tmp_path: Path,
    descriptor: int,
    reason: str,
) -> None:
    workspace = _workspace(tmp_path / f"workspace-{descriptor}")
    source = _source(
        f"""
        import os
        block = b"x" * 65536
        while True:
            os.write({descriptor}, block)
        """
    )

    result = _run_attack(attack_context, workspace, source)

    assert result.returncode == hardened.OUTPUT_LIMIT_EXIT
    assert result.timed_out is False
    assert result.evidence["resources"]["termination_reason"] == reason


def test_memory_fanout_is_killed_by_the_cgroup_without_killing_the_supervisor(
    attack_context: _AttackContext,
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace-memory")
    source = _source(
        """
        import os
        import time

        for _ in range(4):
            if os.fork() == 0:
                blocks = []
                while True:
                    block = bytearray(32 * 1024 * 1024)
                    block[::4096] = b"x" * (len(block[::4096]))
                    blocks.append(block)
        while True:
            time.sleep(1)
        """
    )

    result = _run_attack(attack_context, workspace, source, timeout_seconds=45.0)

    assert result.returncode == hardened.RESOURCE_LIMIT_EXIT
    assert result.timed_out is False
    assert result.evidence["resources"]["termination_reason"] in {
        "memory_limit",
        "memory_oom_kill",
    }


def test_pid_fanout_is_killed_and_the_cgroup_tree_is_empty(
    attack_context: _AttackContext,
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace-pids")
    source = _source(
        """
        import os
        import time

        while True:
            try:
                child = os.fork()
            except OSError:
                time.sleep(.01)
                continue
            if child == 0:
                time.sleep(3600)
                os._exit(0)
        """
    )

    result = _run_attack(attack_context, workspace, source, timeout_seconds=30.0)

    assert result.returncode == hardened.RESOURCE_LIMIT_EXIT
    assert result.timed_out is False
    assert result.evidence["resources"]["termination_reason"] == "pids_limit"


def test_forked_setsid_descendant_cannot_survive_leader_exit(
    attack_context: _AttackContext,
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace-orphan")
    process_name = ("oat" + uuid.uuid4().hex)[:15]
    source = _source(
        """
        import os
        import signal
        import sys
        import time
        from pathlib import Path

        process_name = sys.argv[1]
        child = os.fork()
        if child == 0:
            os.setsid()
            grandchild = os.fork()
            if grandchild:
                os._exit(0)
            Path("/proc/self/comm").write_text(process_name + "\\n", encoding="ascii")
            Path("/work/orphan-marker").write_text(process_name, encoding="ascii")
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            while True:
                time.sleep(1)
        os.waitpid(child, 0)
        print(process_name, flush=True)
        """
    )

    result = _run_attack(
        attack_context,
        workspace,
        source,
        arguments=(process_name,),
    )

    assert result.returncode == 0
    assert result.stdout.strip() == process_name.encode("ascii")
    surviving = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            observed = (process / "comm").read_text(encoding="ascii").strip()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if observed == process_name:
            surviving.append(process.name)
    assert surviving == []


def test_file_descriptor_exhaustion_is_bounded_to_the_candidate(
    attack_context: _AttackContext,
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace-fds")
    source = _source(
        """
        import errno
        import json
        import os

        descriptors = []
        while True:
            try:
                descriptors.append(os.open("/dev/null", os.O_RDONLY))
            except OSError as exc:
                print(json.dumps({"errno": exc.errno, "opened": len(descriptors)}))
                raise SystemExit(23 if exc.errno == errno.EMFILE else 24)
        """
    )

    result = _run_attack(attack_context, workspace, source)

    assert result.returncode == 23
    observation = _json_stdout(result)
    assert observation["errno"] != 0
    assert 0 < observation["opened"] <= attack_context.attestation.limits.fds_max
    assert result.evidence["resources"]["termination_reason"] is None


def test_cpu_burn_is_stopped_by_the_wall_deadline(
    attack_context: _AttackContext,
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace-cpu")
    source = _source(
        """
        value = 0
        while True:
            value = (value * 33 + 17) & 0xffffffffffffffff
        """
    )

    result = _run_attack(
        attack_context,
        workspace,
        source,
        timeout_seconds=1.0,
    )

    assert result.returncode == 124
    assert result.timed_out is True
    assert result.evidence["resources"]["termination_reason"] == "wall_timeout"
    assert result.evidence["resources"]["cpu_usage_usec"] > 0


@pytest.mark.parametrize("resource", ["bytes", "inodes"])
def test_scratch_byte_and_inode_caps_are_real_kernel_limits(
    attack_context: _AttackContext,
    tmp_path: Path,
    resource: str,
) -> None:
    workspace = _workspace(tmp_path / f"workspace-scratch-{resource}")
    if resource == "bytes":
        body = """
        import errno
        import json
        from pathlib import Path

        block = b"x" * (1024 * 1024)
        files = 0
        try:
            while True:
                with (Path("/work") / f"fill-{files}").open("wb", buffering=0) as handle:
                    for _ in range(60):
                        handle.write(block)
                files += 1
        except OSError as exc:
            print(json.dumps({"errno": exc.errno, "files": files}))
            raise SystemExit(31 if exc.errno in {errno.ENOSPC, errno.EDQUOT} else 32)
        """
        minimum_key = "scratch_used_bytes"
        scratch_stats = os.statvfs(attack_context.attestation.scratch_mount)
        minimum = scratch_stats.f_blocks * scratch_stats.f_frsize // 2
    else:
        body = """
        import errno
        import json
        from pathlib import Path

        root = Path("/work/inodes")
        root.mkdir()
        created = 0
        try:
            while True:
                (root / str(created)).touch()
                created += 1
        except OSError as exc:
            print(json.dumps({"created": created, "errno": exc.errno}))
            raise SystemExit(31 if exc.errno in {errno.ENOSPC, errno.EDQUOT} else 32)
        """
        minimum_key = "scratch_used_inodes"
        minimum = os.statvfs(attack_context.attestation.scratch_mount).f_files // 2

    result = _run_attack(
        attack_context,
        workspace,
        _source(body),
        timeout_seconds=120.0,
    )

    assert result.returncode == 31
    assert _json_stdout(result)["errno"] in {28, 122}
    assert result.evidence["resources"][minimum_key] >= minimum
    assert result.evidence["resources"]["termination_reason"] is None


@pytest.mark.parametrize("tree_entry", ["symlink", "fifo"])
def test_candidate_symlink_and_special_trees_are_candidate_rejections(
    attack_context: _AttackContext,
    tmp_path: Path,
    tree_entry: str,
) -> None:
    workspace = _workspace(tmp_path / f"workspace-tree-{tree_entry}")
    if tree_entry == "symlink":
        (workspace / "escape").symlink_to(tmp_path / "outside")
    else:
        os.mkfifo(workspace / "candidate-fifo")

    source = _source("raise AssertionError('candidate must never execute')")
    with pytest.raises(hardened.CandidateSnapshotViolation):
        attack_context.runner.run_hidden_verifier(
            verifier_source=source,
            verifier_source_sha256=hashlib.sha256(source).hexdigest(),
            argument_templates=(),
            workspace=workspace,
            timeout_seconds=5.0,
        )
    _assert_supervisor_healthy(attack_context)


def test_attested_verifier_source_and_runtime_drift_fail_closed(
    attack_context: _AttackContext,
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace-drift")
    source = _source("print('must not execute')")

    with pytest.raises(hardened.LinuxRunnerInfrastructureError, match="digest"):
        attack_context.runner.run_hidden_verifier(
            verifier_source=source,
            verifier_source_sha256="0" * 64,
            argument_templates=(),
            workspace=workspace,
            timeout_seconds=5.0,
        )

    drifted_attestation = replace(
        attack_context.attestation,
        runtime_tree_sha256="0" * 64,
    )
    drifted_runner = hardened.LinuxHardenedVerifierRunner(drifted_attestation)
    with pytest.raises(hardened.LinuxRunnerInfrastructureError, match="runtime image drifted"):
        drifted_runner.run_hidden_verifier(
            verifier_source=source,
            verifier_source_sha256=hashlib.sha256(source).hexdigest(),
            argument_templates=(),
            workspace=workspace,
            timeout_seconds=5.0,
        )
    _assert_supervisor_healthy(attack_context)


def test_real_trusted_controller_worker_chain_owns_the_final_verdict(
    attack_context: _AttackContext,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "controller-workspace"
    tests = workspace / "tests"
    tests.mkdir(parents=True)
    (workspace / "file_service.py").write_text(
        textwrap.dedent(
            """
            import builtins
            import json
            import os

            # Same-process verifiers could be forged by these mutations.  The
            # trusted controller must remain a separate, attested process.
            json.dumps = lambda *args, **kwargs: '{"passed":true,"score":1.0}'
            builtins.getattr = lambda value, name, *default: (
                object.__getattribute__(value, name) if not default else default[0]
            )

            class FileService:
                def __init__(self, root):
                    self.root = root

                def read_text(self, user_path):
                    # Deliberately vulnerable: a forged candidate verdict must
                    # not turn these traversal outcomes into a pass.
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

    completed, verdict = _run_controller_case(
        attack_context,
        case_id="coding.path-boundary",
        workspace=workspace,
    )

    assert completed.stderr == b""
    assert verdict["passed"] is False
    assert verdict["score"] == 0.0
    assert verdict["checks"] == []
    assert stat.S_ISREG((workspace / "file_service.py").lstat().st_mode)

