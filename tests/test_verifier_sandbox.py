from __future__ import annotations

import os
import socket
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.verifier_sandbox import (
    HARDENED_RUNNER_CONTRACT,
    HARDENED_RUNNER_ENV,
    FixtureInfrastructureError,
    run_hidden_verifier,
    verifier_permission_diagnostics,
    verifier_sandbox_provenance,
)


def test_builtin_verifier_backends_are_never_authorized() -> None:
    with pytest.raises(FixtureInfrastructureError) as exc_info:
        verifier_sandbox_provenance()

    message = str(exc_info.value)
    assert "infrastructure is invalid" in message
    assert "permission diagnostics only" in message
    assert "resource/output/scratch/kill-tree/runtime-content bounds" in message
    assert HARDENED_RUNNER_CONTRACT["builtin_backends_authorized"] is False
    assert HARDENED_RUNNER_CONTRACT["linux_required"] == ("cgroup-v2(memory,pids,cpu)+cgroup.kill")


def test_unattested_external_runner_setting_does_not_bypass_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(HARDENED_RUNNER_ENV, "/tmp/unattested-runner")

    with pytest.raises(FixtureInfrastructureError, match="cannot authorize execution"):
        verifier_sandbox_provenance()


def test_malicious_candidate_is_not_executed_without_hardened_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "trial"
    workspace.mkdir()
    outside_read = tmp_path / "outside-read.txt"
    outside_read.write_text("host secret", encoding="utf-8")
    outside_write = tmp_path / "outside-write.txt"
    leaked_env = workspace / "leaked-env.txt"
    execution_flag = workspace / "candidate-executed.txt"
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.1)
    port = listener.getsockname()[1]
    monkeypatch.setenv("ECHO_SANDBOX_PROBE_SECRET", "must-not-leak")
    (workspace / "candidate.py").write_text(
        f"""\
import os
import socket
from pathlib import Path

Path({str(execution_flag)!r}).write_text("executed", encoding="utf-8")
Path({str(leaked_env)!r}).write_text(
    os.environ.get("ECHO_SANDBOX_PROBE_SECRET", ""), encoding="utf-8"
)
Path({str(outside_write)!r}).write_text(
    Path({str(outside_read)!r}).read_text(encoding="utf-8"), encoding="utf-8"
)
socket.create_connection(("127.0.0.1", {port}), timeout=1)
""",
        encoding="utf-8",
    )
    verifier = tmp_path / "trusted-verifier.py"
    verifier.write_text(
        """
import importlib.util
import sys
from pathlib import Path

workspace = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("candidate", workspace / "candidate.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
""".lstrip(),
        encoding="utf-8",
    )

    try:
        with pytest.raises(FixtureInfrastructureError, match="infrastructure is invalid"):
            run_hidden_verifier(
                verifier_source=verifier,
                argument_templates=("{workspace}",),
                workspace=workspace,
                timeout_seconds=10,
            )
        with pytest.raises(TimeoutError):
            listener.accept()
    finally:
        listener.close()

    assert outside_read.read_text(encoding="utf-8") == "host secret"
    assert not outside_write.exists()
    assert not leaked_env.exists()
    assert not execution_flag.exists()


def test_permission_probe_is_explicitly_non_authorizing() -> None:
    try:
        diagnostics = verifier_permission_diagnostics()
    except FixtureInfrastructureError as exc:
        pytest.skip(f"permission diagnostic unavailable: {exc}")

    assert diagnostics["authorization"] is False
    assert diagnostics["coverage"] == "partial_permissions_only"
    assert diagnostics["probe"] == {
        "outside_read_denied": True,
        "symlink_escape_read_denied": True,
        "original_workspace_write_denied": True,
        "outside_write_denied": True,
        "loopback_network_denied": True,
        "host_secret_scrubbed": True,
    }
    assert {
        "bounded_output",
        "bounded_cpu_memory_pids_fds",
        "bounded_scratch_bytes_inodes",
        "kernel_tree_kill",
        "immutable_runtime_content",
    } == set(diagnostics["missing_guarantees"])


def test_windows_permission_probe_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from benchmarks import verifier_sandbox as sandbox

    sandbox._resolve_permission_probe_sandbox.cache_clear()
    monkeypatch.setattr(sandbox.os, "name", "nt")
    try:
        with pytest.raises(FixtureInfrastructureError, match="Windows"):
            sandbox.verifier_permission_diagnostics()
    finally:
        sandbox._resolve_permission_probe_sandbox.cache_clear()


def test_hardened_contract_names_every_resource_boundary() -> None:
    required = HARDENED_RUNNER_CONTRACT["required"]

    assert set(required) == {
        "filesystem",
        "network",
        "environment",
        "output",
        "descriptors",
        "scratch",
        "resources",
        "termination",
        "runtime",
        "probe",
    }
    assert os.path.isabs(os.path.abspath(os.sys.executable))


def test_exact_coding_wrapper_routes_only_to_trusted_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from benchmarks import verifier_sandbox as sandbox

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    wrapper = Path("benchmarks/verifiers/verify_path_boundary.py").resolve(strict=True)
    calls: list[dict[str, object]] = []

    class FakeRunner:
        def run_trusted_controller(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                returncode=0,
                stdout=b'{"passed":false,"reason":"candidate fail","score":0.0}\n',
                stderr=b"",
                timed_out=False,
            )

    monkeypatch.setattr(sandbox, "_configured_hardened_runner", lambda: FakeRunner())
    digest = sha256(wrapper.read_bytes()).hexdigest()

    completed = sandbox.run_hidden_verifier(
        verifier_source=wrapper,
        argument_templates=("{workspace}",),
        workspace=workspace,
        timeout_seconds=30,
        expected_source_sha256=digest,
    )

    assert completed.returncode == 0
    assert calls == [
        {
            "case_id": "coding.path-boundary",
            "workspace": workspace,
            "timeout_seconds": 30,
        }
    ]


def test_unmapped_wrapper_stays_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from benchmarks import verifier_sandbox as sandbox

    wrapper = tmp_path / "verify_path_boundary.py"
    wrapper.write_text("print('forged')\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Runner provenance is resolved before wrapper selection, so provide a
    # harmless fake and prove the digest/name pair is still rejected.
    monkeypatch.setattr(sandbox, "_configured_hardened_runner", lambda: SimpleNamespace())
    with pytest.raises(FixtureInfrastructureError, match="no attested controller mapping"):
        sandbox.run_hidden_verifier(
            verifier_source=wrapper,
            argument_templates=("{workspace}",),
            workspace=workspace,
            timeout_seconds=30,
            expected_source_sha256=sha256(wrapper.read_bytes()).hexdigest(),
        )

