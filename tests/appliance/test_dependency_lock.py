from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from deploy.appliance import dependency_lock


def _write_projects(tmp_path: Path) -> tuple[Path, Path]:
    os_project = tmp_path / "echo-os" / "pyproject.toml"
    agent_project = tmp_path / "echo-agent" / "pyproject.toml"
    os_project.parent.mkdir()
    agent_project.parent.mkdir()
    os_project.write_text(
        """\
[project]
name = "echo-os"
version = "1.0.0"
dependencies = ["fastapi>=0.115", "cryptography>=50.0.0"]

[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[tool.uv]
required-version = "==0.11.25"
"""
    )
    agent_project.write_text(
        """\
[project]
name = "echo-agent-runtime"
version = "2.0.0"
dependencies = ["pydantic>=2.0"]

[project.optional-dependencies]
serve = ["uvicorn>=0.32"]
tracing = ["opentelemetry-api>=1.25"]
web = ["httpx>=0.27"]
local-auth = ["python-jose>=3.3"]
video = ["pillow>=10.0"]

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"
"""
    )
    return os_project, agent_project


def _lock_for(requirements: list[str], *, version: str = "1.0.0") -> bytes:
    names = sorted(dependency_lock._direct_names(requirements))
    digest = "a" * 64
    return "".join(f"{name}=={version} \\\n    --hash=sha256:{digest}\n" for name in names).encode()


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    return (
        tmp_path / "dist" / "build-requirements.lock",
        tmp_path / "dist" / "runtime-requirements.lock",
        tmp_path / "dist" / "python-dependency-lock.json",
    )


def _options(tmp_path: Path) -> dict[str, object]:
    os_project, agent_project = _write_projects(tmp_path)
    build_lock, runtime_lock, metadata = _paths(tmp_path)
    return {
        "os_project_path": os_project,
        "agent_project_path": agent_project,
        "extras": dependency_lock.DEFAULT_EXTRAS,
        "build_lock_path": build_lock,
        "runtime_lock_path": runtime_lock,
        "metadata_path": metadata,
        "uv_binary": "uv",
    }


def _mock_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dependency_lock, "_uv_version", lambda _binary: "0.11.25")
    monkeypatch.setattr(
        dependency_lock,
        "_compile_lock",
        lambda requirements, **_kwargs: _lock_for(requirements),
    )


def test_refresh_and_verify_are_deterministic_and_platform_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = _options(tmp_path)
    _mock_resolver(monkeypatch)

    first = dependency_lock.refresh_locks(**options)
    build_lock, runtime_lock, metadata_path = _paths(tmp_path)
    first_bytes = tuple(path.read_bytes() for path in (build_lock, runtime_lock, metadata_path))
    second = dependency_lock.refresh_locks(**options)
    verified = dependency_lock.verify_locks(**options)
    metadata = json.loads(metadata_path.read_text())

    assert first == second
    assert (
        tuple(path.read_bytes() for path in (build_lock, runtime_lock, metadata_path))
        == first_bytes
    )
    assert verified["verified"] is True
    assert verified["refreshed"] is False
    assert verified["platforms"] == ["linux/amd64", "linux/arm64"]
    assert metadata["kind"] == "echo-appliance-python-dependency-lock"
    assert metadata["generator"] == {"name": "uv", "version": "0.11.25"}
    assert metadata["platforms"] == ["linux/amd64", "linux/arm64"]
    assert metadata["onlyBinary"] is True
    assert metadata["inputs"]["agentExtras"] == list(dependency_lock.DEFAULT_EXTRAS)
    assert metadata["buildLock"]["packageCount"] == 3
    assert metadata["runtimeLock"]["packageCount"] == 7
    for path in (build_lock, runtime_lock, metadata_path):
        if os.name == "nt":
            assert stat.S_ISREG(path.stat().st_mode)
            assert path.stat().st_mode & stat.S_IWRITE
        else:
            assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_refresh_rejects_architecture_specific_runtime_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = _options(tmp_path)
    monkeypatch.setattr(dependency_lock, "_uv_version", lambda _binary: "0.11.25")

    def compile_lock(requirements: list[str], *, platform: str | None, **_kwargs: object) -> bytes:
        version = "2.0.0" if platform == "aarch64-unknown-linux-gnu" else "1.0.0"
        return _lock_for(requirements, version=version)

    monkeypatch.setattr(dependency_lock, "_compile_lock", compile_lock)

    with pytest.raises(
        dependency_lock.DependencyLockError,
        match="amd64 and arm64 runtime dependency resolutions differ",
    ):
        dependency_lock.refresh_locks(**options)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"demo>=1.0 \\\n    --hash=sha256:" + b"a" * 64 + b"\n", "unpinned"),
        (b"demo==1.0.0 \\\n", "without hashes"),
        (b"demo @ https://packages.example/demo.whl\n", "mutable package source"),
        (
            b"demo==1.0.0 \\\n    --hash=sha256:"
            + b"a" * 64
            + b"\ndemo==1.0.0 \\\n    --hash=sha256:"
            + b"b" * 64
            + b"\n",
            "repeats a package",
        ),
    ],
)
def test_lock_validation_rejects_mutable_or_unhashed_inputs(data: bytes, message: str) -> None:
    with pytest.raises(dependency_lock.DependencyLockError, match=message):
        dependency_lock.validate_lock_bytes(data, context="test", required_names=set())


def test_verify_rejects_changed_source_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = _options(tmp_path)
    _mock_resolver(monkeypatch)
    dependency_lock.refresh_locks(**options)
    os_project = options["os_project_path"]
    assert isinstance(os_project, Path)
    os_project.write_text(os_project.read_text().replace("fastapi>=0.115", "fastapi>=0.116"))

    with pytest.raises(dependency_lock.DependencyLockError, match="metadata does not match"):
        dependency_lock.verify_locks(**options, recompile=False)


def test_refresh_requires_the_exact_uv_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = _options(tmp_path)
    monkeypatch.setattr(dependency_lock, "_uv_version", lambda _binary: "0.12.0")

    with pytest.raises(dependency_lock.DependencyLockError, match="does not match required"):
        dependency_lock.refresh_locks(**options)


def test_refresh_refuses_symlink_output_and_preserves_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = _options(tmp_path)
    _mock_resolver(monkeypatch)
    build_lock = options["build_lock_path"]
    assert isinstance(build_lock, Path)
    build_lock.parent.mkdir()
    outside = tmp_path / "outside.lock"
    outside.write_text("keep\n")
    build_lock.symlink_to(outside)

    with pytest.raises(dependency_lock.DependencyLockError, match="output path is unsafe"):
        dependency_lock.refresh_locks(**options)

    assert outside.read_text() == "keep\n"


def test_atomic_publish_contains_complete_fsynced_bytes_before_replacing_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "公开 dependency.lock"
    target.write_bytes(b"old complete lock\n")
    data = b"new complete lock\n" * 10_000
    fsync = os.fsync
    publish = dependency_lock._publish_lock
    flushed = []

    def flush(descriptor):
        info = os.fstat(descriptor)
        if stat.S_ISREG(info.st_mode):
            flushed.append(info.st_size)
        return fsync(descriptor)

    def observe_publish(temporary, destination):
        assert temporary.parent == target.parent
        assert temporary.read_bytes() == data
        assert target.read_bytes() == b"old complete lock\n"
        assert flushed == [len(data)]
        return publish(temporary, destination)

    monkeypatch.setattr(os, "fsync", flush)
    monkeypatch.setattr(dependency_lock, "_publish_lock", observe_publish)
    dependency_lock._atomic_write(target, data)
    assert target.read_bytes() == data
    assert list(tmp_path.glob(f".{target.name}.*")) == []


@pytest.mark.parametrize("failure", ["fsync", "publish"])
def test_atomic_write_failure_preserves_original_and_removes_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    target = tmp_path / "dependency.lock"
    target.write_bytes(b"keep original\n")

    def fail(*args):
        raise OSError("synthetic dependency lock IO failure")

    if failure == "fsync":
        monkeypatch.setattr(os, "fsync", fail)
    else:
        monkeypatch.setattr(dependency_lock, "_publish_lock", fail)
    with pytest.raises(OSError, match="synthetic dependency lock IO failure"):
        dependency_lock._atomic_write(target, b"new lock\n")
    assert target.read_bytes() == b"keep original\n"
    assert list(tmp_path.glob(f".{target.name}.*")) == []


@pytest.mark.skipif(os.name != "nt", reason="actual Windows publication and ACL inheritance")
def test_windows_write_uses_real_file_fsync_and_inherits_public_directory_acl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.appliance.windows_acl_assertions import read_windows_acl

    def unsupported_fchmod(*args):
        pytest.fail("Windows must not pretend to set POSIX permissions")

    fsync = os.fsync

    def flush_regular_file(descriptor):
        assert stat.S_ISREG(os.fstat(descriptor).st_mode)
        return fsync(descriptor)

    monkeypatch.setattr(os, "fchmod", unsupported_fchmod, raising=False)
    monkeypatch.setattr(os, "fsync", flush_regular_file)
    target = tmp_path / "dependency.lock"
    dependency_lock._atomic_write(target, b"first\n")
    dependency_lock._atomic_write(target, b"replacement\n")
    control = tmp_path / "ordinary-public-output.lock"
    control.write_bytes(b"control\n")
    assert target.read_bytes() == b"replacement\n"
    assert read_windows_acl(target) == read_windows_acl(control)


@pytest.mark.skipif(os.name != "nt", reason="real Windows sharing violation")
def test_windows_busy_destination_failure_keeps_original(tmp_path: Path) -> None:
    target = tmp_path / "dependency.lock"
    target.write_bytes(b"old complete lock\n")
    # The actual CRT read handle omits delete-sharing, so MoveFileExW must fail.
    with target.open("rb") as held:
        with pytest.raises(PermissionError):
            dependency_lock._atomic_write(target, b"new lock\n")
        assert held.read() == b"old complete lock\n"
    assert target.read_bytes() == b"old complete lock\n"
    assert list(tmp_path.glob(f".{target.name}.*")) == []
    dependency_lock._atomic_write(target, b"new lock\n")
    assert target.read_bytes() == b"new lock\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows does not implement private POSIX modes")
def test_windows_private_mode_is_rejected_before_creating_output(tmp_path: Path) -> None:
    target = tmp_path / "missing-parent" / "dependency.lock"
    with pytest.raises(dependency_lock.DependencyLockError, match="public artifact mode"):
        dependency_lock._atomic_write(target, b"not a secret store\n", mode=0o600)
    assert not target.parent.exists()


@pytest.mark.skipif(os.name == "nt", reason="actual POSIX descriptor modes and directory fsync")
def test_posix_mode_is_committed_with_contents_and_parent_is_fsynced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "dependency.lock"
    observed = []
    fsync = os.fsync

    def flush(descriptor):
        info = os.fstat(descriptor)
        observed.append((stat.S_ISDIR(info.st_mode), stat.S_IMODE(info.st_mode)))
        return fsync(descriptor)

    monkeypatch.setattr(os, "fsync", flush)
    dependency_lock._atomic_write(target, b"posix artifact\n", mode=0o640)
    assert target.stat().st_mode & 0o777 == 0o640
    assert observed[0] == (False, 0o640)
    assert observed[1][0] is True
