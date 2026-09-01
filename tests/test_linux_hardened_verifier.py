from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from benchmarks import linux_hardened_verifier as hardened


def test_candidate_snapshot_is_content_bound_and_read_only(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "answer.txt").write_text("answer\n", encoding="utf-8")
    executable = source / "run"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    evidence = hardened.create_candidate_snapshot(source, tmp_path / "snapshot")

    assert evidence.source == evidence.copied
    assert evidence.source.entries == 3
    assert evidence.source.regular_files == 2
    assert evidence.source.total_bytes == len(b"answer\n#!/bin/sh\n")
    assert (evidence.destination / "nested" / "answer.txt").read_text() == "answer\n"
    assert (evidence.destination / "nested" / "answer.txt").stat().st_mode & 0o222 == 0
    assert (evidence.destination / "run").stat().st_mode & 0o111


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_candidate_snapshot_rejects_links_and_special_files(tmp_path: Path, kind: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    if kind == "symlink":
        (source / "escape").symlink_to(tmp_path / "outside")
    else:
        os.mkfifo(source / "pipe")

    with pytest.raises(hardened.CandidateSnapshotViolation, match="symlink|special"):
        hardened.create_candidate_snapshot(source, tmp_path / "snapshot")


@pytest.mark.parametrize(
    ("limit_name", "value", "builder", "message"),
    [
        (
            "snapshot_max_entries",
            1,
            lambda root: ((root / "a").write_text("a"), (root / "b").write_text("b")),
            "entry-count",
        ),
        (
            "snapshot_max_depth",
            1,
            lambda root: (root / "a" / "b").mkdir(parents=True),
            "depth",
        ),
        (
            "snapshot_max_path_bytes",
            4,
            lambda root: (root / "long-name").write_text("x"),
            "path-byte",
        ),
        (
            "snapshot_max_single_file_bytes",
            1,
            lambda root: (root / "a").write_bytes(b"ab"),
            "single-file",
        ),
        (
            "snapshot_max_total_bytes",
            2,
            lambda root: ((root / "a").write_bytes(b"ab"), (root / "b").write_bytes(b"c")),
            "total-byte",
        ),
    ],
)
def test_candidate_snapshot_enforces_every_tree_limit(
    tmp_path: Path,
    limit_name: str,
    value: int,
    builder,
    message: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    builder(source)
    limits = replace(hardened.DEFAULT_LIMITS, **{limit_name: value})

    with pytest.raises(hardened.CandidateSnapshotViolation, match=message):
        hardened.create_candidate_snapshot(source, tmp_path / "snapshot", limits=limits)


def test_snapshot_detects_source_drift_between_copy_and_rescan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    mutable = source / "answer"
    mutable.write_text("first", encoding="utf-8")
    original = hardened._copy_or_scan_candidate_tree
    calls = 0

    def drifting(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            mutable.write_text("second", encoding="utf-8")
        return result

    monkeypatch.setattr(hardened, "_copy_or_scan_candidate_tree", drifting)

    with pytest.raises(hardened.LinuxRunnerInfrastructureError, match="drifted"):
        hardened.create_candidate_snapshot(source, tmp_path / "snapshot")


def test_launcher_manifest_exactly_matches_trusted_controller(tmp_path: Path) -> None:
    from benchmarks.trusted_verifier_controller import tree_manifest_sha256

    root = tmp_path / "tree"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "answer.txt").write_text("answer\n", encoding="utf-8")
    (root / "link").symlink_to("nested/answer.txt")

    assert hardened.trusted_controller_tree_manifest_sha256(
        root,
        reject_symlinks=False,
    ) == tree_manifest_sha256(root, reject_symlinks=False)


def test_launcher_manifest_rejects_workspace_hardlinks(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    source = root / "source"
    source.write_text("content", encoding="utf-8")
    os.link(source, root / "alias")

    with pytest.raises(hardened.CandidateSnapshotViolation, match="unsafe links"):
        hardened.trusted_controller_tree_manifest_sha256(
            root,
            reject_symlinks=True,
        )


def test_bounded_capture_never_retains_over_limit() -> None:
    buffer = bytearray()

    assert hardened._append_bounded(buffer, b"abc", 4) is False
    assert hardened._append_bounded(buffer, b"def", 4) is True
    assert bytes(buffer) == b"abcd"


def test_scratch_cleanup_removes_read_only_snapshots_without_following_links(
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "scratch"
    invocation = scratch / ("run-" + "a" * 32)
    nested = invocation / "snapshot" / "nested"
    nested.mkdir(parents=True)
    (nested / "answer").write_text("answer", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.write_text("preserve", encoding="utf-8")
    (nested / "escape").symlink_to(outside)
    (nested / "answer").chmod(0o444)
    nested.chmod(0o000)
    nested.parent.chmod(0o000)
    invocation.chmod(0o000)

    hardened._safe_remove_tree(invocation, expected_parent=scratch)

    assert not invocation.exists()
    assert outside.read_text(encoding="utf-8") == "preserve"


def test_scratch_cleanup_handles_depth_beyond_descriptor_limit(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    invocation = scratch / ("run-" + "b" * 32)
    current = invocation
    current.mkdir(parents=True)
    directories = [current]
    for _index in range(320):
        current = current / "d"
        current.mkdir()
        directories.append(current)
    (current / "leaf").write_text("content", encoding="utf-8")
    for directory in reversed(directories):
        directory.chmod(0o000)

    hardened._safe_remove_tree(invocation, expected_parent=scratch)

    assert not invocation.exists()


def test_scratch_cleanup_rejects_root_replacement(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    invocation = scratch / ("run-" + "c" * 32)
    invocation.mkdir(parents=True)
    original_metadata = invocation.lstat()
    displaced = scratch / "displaced"
    invocation.rename(displaced)
    invocation.mkdir()
    parent_fd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with pytest.raises(hardened.LinuxRunnerInfrastructureError, match="drifted"):
            hardened._remove_tree_at(parent_fd, invocation.name, original_metadata)
    finally:
        os.close(parent_fd)

    assert invocation.is_dir()
    assert displaced.is_dir()


def test_scratch_cleanup_rejects_nested_symlink_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "scratch"
    invocation = scratch / ("run-" + "d" * 32)
    nested = invocation / "nested"
    nested.mkdir(parents=True)
    (nested / "leaf").write_text("candidate", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "protected"
    protected.write_text("preserve", encoding="utf-8")
    displaced = invocation / "displaced"
    original_open = hardened._chmod_open_cleanup_directory
    swapped = False

    def swap_before_nested_open(
        parent_fd: int,
        name: str,
        expected_identity: tuple[int, int, int],
    ) -> int:
        nonlocal swapped
        if name == "nested" and not swapped:
            swapped = True
            nested.rename(displaced)
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(parent_fd, name, expected_identity)

    monkeypatch.setattr(
        hardened,
        "_chmod_open_cleanup_directory",
        swap_before_nested_open,
    )

    with pytest.raises(hardened.LinuxRunnerInfrastructureError, match="drifted"):
        hardened._safe_remove_tree(invocation, expected_parent=scratch)

    assert protected.read_text(encoding="utf-8") == "preserve"


def test_scratch_cleanup_rejects_nested_filesystem_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "scratch"
    invocation = scratch / ("run-" + "9" * 32)
    nested = invocation / "nested"
    nested.mkdir(parents=True)
    (nested / "leaf").write_text("preserve", encoding="utf-8")
    original_stat = hardened.os.stat

    def cross_device_stat(
        path: Any,
        *args: Any,
        **kwargs: Any,
    ) -> os.stat_result:
        observed = original_stat(path, *args, **kwargs)
        if path == "nested" and kwargs.get("dir_fd") is not None:
            fields = list(observed)
            fields[2] = observed.st_dev + 1
            return os.stat_result(fields)
        return observed

    monkeypatch.setattr(hardened.os, "stat", cross_device_stat)

    with pytest.raises(
        hardened.LinuxRunnerInfrastructureError,
        match="filesystem boundary",
    ):
        hardened._safe_remove_tree(invocation, expected_parent=scratch)

    assert (nested / "leaf").read_text(encoding="utf-8") == "preserve"


def test_scratch_lease_reaps_stale_cgroups_before_touching_scratch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "scratch"
    stale = scratch / ("run-" + "e" * 32)
    stale.mkdir(parents=True)
    events: list[str] = []
    original_remove = hardened._safe_remove_tree

    class FakeCgroupRun:
        def __init__(self, _parent: Path, _limits: hardened.RunnerLimits) -> None:
            pass

        def recover_stale(self) -> None:
            events.append("cgroup")

    def record_remove(path: Path, *, expected_parent: Path) -> None:
        events.append(f"scratch:{path.name}")
        original_remove(path, expected_parent=expected_parent)

    monkeypatch.setattr(hardened, "_validate_scratch_mount", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hardened, "_CgroupRun", FakeCgroupRun)
    monkeypatch.setattr(hardened, "_safe_remove_tree", record_remove)

    with hardened._ScratchLease(
        scratch,
        hardened.DEFAULT_LIMITS,
        tmp_path / "cgroup",
    ):
        pass

    assert events[0] == "cgroup"
    assert events[1] == f"scratch:{stale.name}"


def test_scratch_cleanup_preserves_primary_and_cleanup_tracebacks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    class FakeCgroupRun:
        def __init__(self, _parent: Path, _limits: hardened.RunnerLimits) -> None:
            pass

        def recover_stale(self) -> None:
            pass

    monkeypatch.setattr(hardened, "_validate_scratch_mount", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hardened, "_CgroupRun", FakeCgroupRun)
    monkeypatch.setattr(
        hardened,
        "_safe_remove_tree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cleanup-failure")),
    )

    with (
        pytest.raises(hardened.LinuxRunnerInfrastructureError) as caught,
        hardened._ScratchLease(
            scratch,
            hardened.DEFAULT_LIMITS,
            tmp_path / "cgroup",
        ),
    ):
        raise ValueError("primary-body-failure")

    rendered = "".join(traceback.format_exception(caught.value))
    assert "primary-body-failure" in rendered
    assert "cleanup-failure" in rendered
    assert isinstance(caught.value.__cause__, BaseExceptionGroup)


def test_scratch_exit_does_not_touch_tree_until_cgroup_reap_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    recover_calls = 0
    removed: list[Path] = []

    class FakeCgroupRun:
        def __init__(self, _parent: Path, _limits: hardened.RunnerLimits) -> None:
            pass

        def recover_stale(self) -> None:
            nonlocal recover_calls
            recover_calls += 1
            if recover_calls == 2:
                raise RuntimeError("cgroup-reap-failure")

    monkeypatch.setattr(hardened, "_validate_scratch_mount", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hardened, "_CgroupRun", FakeCgroupRun)
    monkeypatch.setattr(
        hardened,
        "_safe_remove_tree",
        lambda path, *, expected_parent: removed.append(path),
    )

    with (
        pytest.raises(hardened.LinuxRunnerInfrastructureError, match="cleanup failed"),
        hardened._ScratchLease(
            scratch,
            hardened.DEFAULT_LIMITS,
            tmp_path / "cgroup",
        ) as lease,
    ):
        invocation = lease.invocation

    assert recover_calls == 2
    assert removed == []
    assert invocation is not None and invocation.is_dir()


def test_rpc_frames_are_canonical_bounded_and_forbid_candidate_verdicts() -> None:
    stream = io.BytesIO()
    hardened.write_rpc_frame(stream, {"kind": "raw_outcome", "value": 1})
    stream.seek(0)
    assert hardened.read_rpc_frame(stream) == {"kind": "raw_outcome", "value": 1}

    forged = hardened._canonical_json({"kind": "raw_outcome", "passed": True})
    framed = io.BytesIO(len(forged).to_bytes(4, "big") + forged)
    with pytest.raises(hardened.LinuxRunnerInfrastructureError, match="forge"):
        hardened.read_rpc_frame(framed)

    noncanonical = b'{"value":1,"kind":"raw_outcome"}'
    framed = io.BytesIO(len(noncanonical).to_bytes(4, "big") + noncanonical)
    with pytest.raises(hardened.LinuxRunnerInfrastructureError, match="canonical"):
        hardened.read_rpc_frame(framed)


def test_worker_contract_uses_launcher_only_control_channel() -> None:
    assert hardened.worker_rpc_contract() == {
        "control_transport": "inherited-unix-stream-fd-launcher-only",
        "worker_transport": "inherited-unix-stream-fd",
        "framing": "u32be-canonical-json-v1",
        "max_frame_bytes": 65_536,
        "max_frames": 64,
        "reserved_control_frames": ["runner_ready", "runner_complete"],
    }
    isolation = hardened.worker_isolation_contract()
    assert isolation["inner_uid"] == 65534
    assert isolation["controller_visible"] is False
    assert isolation["ptrace_controller"] is False
    assert isolation["network"] == "none-including-loopback"
    assert hardened.worker_limit_contract()["output_bytes"] == 4_194_304


def test_worker_cli_requires_distinct_control_and_protocol_fds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hardened,
        "load_attestation",
        lambda _path: SimpleNamespace(worker_path=Path(__file__)),
    )
    with pytest.raises(hardened.LinuxRunnerInfrastructureError, match="distinct"):
        hardened.worker_cli(
            [
                "worker",
                "--attestation",
                "/attestation",
                "--workspace-snapshot",
                "/workspace",
                "--workspace-manifest-sha256",
                "0" * 64,
                "--challenge-manifest-sha256",
                "0" * 64,
                "--control-fd",
                "3",
                "--protocol-fd",
                "3",
                "--run-nonce",
                "a" * 64,
            ]
        )


def test_mountinfo_escape_decoder_is_deterministic() -> None:
    assert hardened._decode_mount_path("/run/path\\040with\\011space") == "/run/path with\tspace"


def test_preexec_setup_moves_child_before_exec_and_sets_hard_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, tuple[int, int]]] = []
    writes: list[tuple[int, bytes]] = []
    closes: list[int] = []

    def record_write(descriptor: int, value: bytes) -> int:
        writes.append((descriptor, value))
        return len(value)

    monkeypatch.setattr(
        hardened.resource,
        "setrlimit",
        lambda name, value: calls.append((name, value)),
    )
    monkeypatch.setattr(
        hardened.resource,
        "getrlimit",
        lambda name: (
            (10, 20)
            if name == hardened.resource.RLIMIT_CPU
            else (hardened.resource.RLIM_INFINITY, hardened.resource.RLIM_INFINITY)
        ),
    )
    monkeypatch.setattr(
        hardened.os,
        "write",
        record_write,
    )
    monkeypatch.setattr(hardened.os, "close", lambda descriptor: closes.append(descriptor))

    hardened._preexec_setup(9, hardened.DEFAULT_LIMITS)()

    assert writes == [(9, b"0")]
    assert closes == [9]
    names = {name for name, _value in calls}
    assert hardened.resource.RLIMIT_CPU in names
    assert hardened.resource.RLIMIT_NOFILE in names
    assert hardened.resource.RLIMIT_FSIZE in names
    assert hardened.resource.RLIMIT_NPROC not in names
    assert (hardened.resource.RLIMIT_CPU, (20, 20)) in calls


def test_process_limit_headroom_fails_closed_and_records_effective_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def constrained(kind: int) -> tuple[int, int]:
        if kind == hardened.resource.RLIMIT_NOFILE:
            return 64, 128
        return hardened.resource.RLIM_INFINITY, hardened.resource.RLIM_INFINITY

    monkeypatch.setattr(hardened.resource, "getrlimit", constrained)
    with pytest.raises(
        hardened.LinuxRunnerInfrastructureError,
        match="descriptors hard limit",
    ):
        hardened._process_limit_headroom(hardened.DEFAULT_LIMITS)

    monkeypatch.setattr(
        hardened.resource,
        "getrlimit",
        lambda _kind: (
            hardened.resource.RLIM_INFINITY,
            hardened.resource.RLIM_INFINITY,
        ),
    )
    evidence = hardened._process_limit_headroom(
        hardened.DEFAULT_LIMITS,
        cpu_seconds=20,
    )
    assert evidence["cpu_seconds"]["effective_child"] == 20
    assert evidence["descriptors"]["effective_child"] == 256
    assert all(record["hard_sufficient"] is True for record in evidence.values())


def test_cgroup_live_probe_start_failure_reaps_created_cgroup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    read_fd, write_fd = os.pipe()
    killed = False

    class FakeCgroup:
        path: Path | None = tmp_path / ("run-" + "f" * 32)

        def process_fd(self) -> int:
            return write_fd

        def kill_and_reap(self, *, ignore_missing: bool = False) -> None:
            nonlocal killed
            assert ignore_missing is True
            killed = True
            self.path = None

    attestation = cast(
        hardened.RunnerAttestation,
        SimpleNamespace(
            launcher_executable_path=Path(sys.executable),
            runtime_root=tmp_path,
            limits=hardened.DEFAULT_LIMITS,
        ),
    )
    monkeypatch.setattr(
        hardened.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("exec-failure")),
    )
    try:
        with pytest.raises(hardened.LinuxRunnerInfrastructureError, match="failed to start"):
            hardened._exercise_cgroup_process_kill(
                attestation,
                cast(hardened._CgroupRun, FakeCgroup()),
                {},
            )
    finally:
        os.close(read_fd)

    assert killed is True


def test_cgroup_live_probe_post_start_cleanup_runs_after_cgroup_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_read, output_write = os.pipe()
    cgroup_read, cgroup_write = os.pipe()
    payload = {"core_bytes": [0, 0]}
    os.write(output_write, json.dumps(payload, separators=(",", ":")).encode() + b"\n")
    os.close(output_write)

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = os.fdopen(output_read, "rb", buffering=0)
            self.pid = 4242
            self.killed = False
            self.wait_calls = 0

        def poll(self) -> int | None:
            return -hardened.signal.SIGKILL if self.killed else None

        def kill(self) -> None:
            self.killed = True

        def wait(self, *, timeout: float) -> int:
            assert timeout == hardened.DEFAULT_LIMITS.reap_timeout_seconds
            self.wait_calls += 1
            return -hardened.signal.SIGKILL

    process = FakeProcess()

    class FakeCgroup:
        path: Path | None = tmp_path / ("run-" + "8" * 32)

        def process_fd(self) -> int:
            return cgroup_write

        def kill_and_reap(self, *, ignore_missing: bool = False) -> None:
            assert ignore_missing is True
            raise RuntimeError("cgroup-cleanup-failure")

    attestation = cast(
        hardened.RunnerAttestation,
        SimpleNamespace(
            launcher_executable_path=Path(sys.executable),
            runtime_root=tmp_path,
            limits=hardened.DEFAULT_LIMITS,
        ),
    )
    monkeypatch.setattr(hardened.subprocess, "Popen", lambda *_args, **_kwargs: process)
    try:
        with pytest.raises(
            hardened.LinuxRunnerInfrastructureError,
            match="execution and teardown",
        ) as caught:
            hardened._exercise_cgroup_process_kill(
                attestation,
                cast(hardened._CgroupRun, FakeCgroup()),
                {"core_bytes": {"effective_child": 0}},
            )
    finally:
        os.close(cgroup_read)

    assert isinstance(caught.value.__cause__, BaseExceptionGroup)
    assert process.killed is True
    assert process.wait_calls == 1
    assert process.stdout.closed


def test_collect_process_initialization_failure_reaps_before_returning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        start_new_session=True,
    )
    killed = False
    selector_closed = False

    class BrokenSelector:
        def register(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("selector-register-failure")

        def close(self) -> None:
            nonlocal selector_closed
            selector_closed = True

    class FakeCgroup:
        path: Path | None = Path("/fake/run")

        def kill_and_reap(self, *, ignore_missing: bool = False) -> None:
            nonlocal killed
            assert ignore_missing is True
            killed = True
            self.path = None
            os.killpg(process.pid, hardened.signal.SIGKILL)

    scratch = cast(
        hardened._ScratchLease,
        SimpleNamespace(usage=lambda: (0, 0)),
    )
    monkeypatch.setattr(hardened.selectors, "DefaultSelector", BrokenSelector)

    with pytest.raises(RuntimeError, match="selector-register-failure"):
        hardened._collect_process(
            process,
            input_bytes=b"",
            cgroup=cast(hardened._CgroupRun, FakeCgroup()),
            scratch=scratch,
            timeout_seconds=1.0,
            limits=hardened.DEFAULT_LIMITS,
        )

    assert killed is True
    assert selector_closed is True
    assert process.poll() == -hardened.signal.SIGKILL
    assert process.stdin is not None and process.stdin.closed
    assert process.stdout is not None and process.stdout.closed
    assert process.stderr is not None and process.stderr.closed


def test_runtime_absolute_link_is_resolved_inside_virtual_root(tmp_path: Path) -> None:
    root = tmp_path / "rootfs"
    (root / "usr/bin").mkdir(parents=True)
    (root / "etc/alternatives").mkdir(parents=True)
    (root / "usr/bin/mawk").write_text("binary", encoding="utf-8")
    (root / "etc/alternatives/awk").symlink_to("/usr/bin/mawk")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        hardened._validate_runtime_symlink(
            descriptor,
            hardened.PurePosixPath("etc/alternatives/awk"),
            "/usr/bin/mawk",
        )
    finally:
        os.close(descriptor)


def test_runtime_link_allows_content_bound_internal_dangling_target(tmp_path: Path) -> None:
    root = tmp_path / "rootfs"
    (root / "usr/share/doc/openssl").mkdir(parents=True)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        hardened._validate_runtime_symlink(
            descriptor,
            hardened.PurePosixPath("usr/share/doc/openssl/changelog.Debian.gz"),
            "../libssl3/changelog.Debian.gz",
        )
    finally:
        os.close(descriptor)


def test_runtime_link_rejects_virtual_root_escape(
    tmp_path: Path,
) -> None:
    root = tmp_path / "rootfs"
    (root / "etc/alternatives").mkdir(parents=True)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(hardened.LinuxRunnerInfrastructureError, match="escapes"):
            hardened._validate_runtime_symlink(
                descriptor,
                hardened.PurePosixPath("etc/alternatives/awk"),
                "../../../outside",
            )
    finally:
        os.close(descriptor)


def test_runtime_link_rejects_cycle(tmp_path: Path) -> None:
    root = tmp_path / "rootfs"
    (root / "links").mkdir(parents=True)
    (root / "links/a").symlink_to("b")
    (root / "links/b").symlink_to("a")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(hardened.LinuxRunnerInfrastructureError, match="cyclic"):
            hardened._validate_runtime_symlink(
                descriptor,
                hardened.PurePosixPath("links/a"),
                "b",
            )
    finally:
        os.close(descriptor)


def test_controller_socket_is_not_the_worker_protocol_socket() -> None:
    control_left, control_right = socket.socketpair()
    worker_left, worker_right = socket.socketpair()
    try:
        assert control_left.fileno() != worker_left.fileno()
        assert control_right.fileno() != worker_right.fileno()
    finally:
        control_left.close()
        control_right.close()
        worker_left.close()
        worker_right.close()


def test_candidate_bwrap_receives_only_narrow_protocol_and_probe_fds(tmp_path: Path) -> None:
    worker = tmp_path / "trusted_verifier_worker.py"
    worker.write_text("# trusted worker\n", encoding="utf-8")
    attestation = cast(
        hardened.RunnerAttestation,
        SimpleNamespace(
            bubblewrap_path=Path("/usr/bin/bwrap"),
            runtime_root=Path("/opt/echo/rootfs"),
            runtime_python="/usr/bin/python3",
            worker_path=worker,
        ),
    )

    command = hardened._worker_bubblewrap_command(
        attestation,
        workspace_snapshot=tmp_path / "workspace",
        challenge_snapshot=tmp_path / "challenge",
        work=tmp_path / "work",
        seccomp_fd=5,
        probe_args=("probe",),
        has_challenge=True,
    )

    assert "--preserve-fds" not in command
    assert command[command.index("--seccomp") + 1] == "5"
    assert str(worker) in command
    assert "/echo-trusted/trusted_verifier_worker.py" in command
    bootstrap = command[command.index("-c") + 1]
    assert '"--candidate-protocol-fd"' in bootstrap
    assert '"--protocol-fd"' not in bootstrap
    assert "descriptor > 3" in bootstrap


def test_worker_probe_requires_every_private_namespace_identity() -> None:
    host = {name: f"{name}:[1]" for name in hardened._NAMESPACE_NAMES}
    inner = {name: f"{name}:[2]" for name in hardened._NAMESPACE_NAMES}
    payload = {
        "candidate_protocol_is_unix_stream": True,
        "cgroup": "0::/run-test",
        "challenge_write_denied": True,
        "controller_channels_absent": True,
        "controller_pid_hidden": True,
        "external_network_denied": True,
        "gid": 65534,
        "gid_map": "0 1000 1",
        "host_secret_scrubbed": True,
        "loopback_network_denied": True,
        "namespace_ids": inner,
        "original_workspace_write_denied": True,
        "outside_read_denied": True,
        "outside_write_denied": True,
        "private_namespaces": True,
        "probe_pipe_present": True,
        "scratch_write_succeeded": True,
        "snapshot_write_denied": True,
        "supervisor_pid_hidden": True,
        "uid": 65534,
        "uid_map": "0 1000 1",
    }

    hardened._validate_worker_probe(payload, host_namespace_ids=host)

    payload["namespace_ids"] = {**inner, "net": host["net"]}
    with pytest.raises(hardened.LinuxRunnerInfrastructureError, match="namespace identity"):
        hardened._validate_worker_probe(payload, host_namespace_ids=host)


def test_invalid_top_level_cli_is_infrastructure_exit() -> None:
    assert hardened.main(["unknown"]) == hardened.INFRASTRUCTURE_INVALID_EXIT


def test_validate_cli_emits_canonical_public_attestation(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    payload = {"authorization": True, "schema": hardened.ATTESTATION_SCHEMA}
    monkeypatch.setattr(
        hardened,
        "load_attestation",
        lambda _path: SimpleNamespace(public_dict=lambda: payload),
    )

    assert hardened.main(["validate", "--attestation", "/attestation.json"]) == 0

    captured = capfd.readouterr()
    assert captured.out.encode() == hardened._canonical_json(payload) + b"\n"
    assert captured.err == ""


def test_validate_cli_bootstraps_as_an_isolated_absolute_script() -> None:
    module = Path(hardened.__file__).resolve(strict=True)

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(module),
            "validate",
            "--attestation",
            "/definitely-missing-attestation.json",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == hardened.INFRASTRUCTURE_INVALID_EXIT
    assert "ModuleNotFoundError" not in completed.stderr
    assert "infrastructure invalid" in completed.stderr


def test_legacy_same_process_worker_is_rejected_before_kernel_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = tmp_path / "legacy_worker.py"
    worker.write_text(
        """
class TrustedSupervisorError(RuntimeError):
    pass
CANDIDATE_FAILURE_EXIT = 81
def run_trusted_supervisor(*args, **kwargs):
    return 0
""".lstrip(),
        encoding="utf-8",
    )
    attestation = cast(
        hardened.RunnerAttestation,
        SimpleNamespace(
            worker_path=worker,
            worker_sha256=hardened.sha256(worker.read_bytes()).hexdigest(),
        ),
    )
    runner = hardened.LinuxHardenedVerifierRunner(attestation)
    monkeypatch.setattr(
        hardened,
        "_ScratchLease",
        lambda *_args, **_kwargs: pytest.fail("kernel preflight must not run for a legacy worker"),
    )

    with pytest.raises(
        hardened.LinuxRunnerInfrastructureError,
        match="isolated candidate-API",
    ):
        runner.provenance()

