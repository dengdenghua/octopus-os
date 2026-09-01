from __future__ import annotations

import json
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
