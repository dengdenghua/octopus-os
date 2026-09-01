from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from appliance.agent_api.contract import ALL_AGENT_API_DOMAINS
from deploy.appliance import agent_bundle


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def _agent_checkout(tmp_path: Path) -> Path:
    source = tmp_path / "echo-agent"
    source.mkdir()
    _write(
        source / "pyproject.toml",
        """\
[project]
name = "echo-agent-runtime"
version = "1.2.3"
""",
    )
    _write(
        source / "frontend" / "package.json",
        json.dumps(
            {
                "name": "echo-frontend",
                "version": "4.5.6",
                "devDependencies": {"@openai/codex": "0.149.0"},
            }
        ),
    )
    _write(source / "agents" / "default.yaml", "name: default\n")
    _write(source / "skills" / "echo" / "SKILL.md", "# Echo\n")
    _write(source / "prompts" / "system.md", "You are Echo.\n")
    _write(source / "protocols" / "events.yaml", "version: 1\n")
    _write(source / "teams" / "default.yaml", "members: []\n")
    _write(source / "skills.lock.json", "{}\n")
    _write(source / "config.example.yaml", "preset: personal\nname: echo-test\n")
    _run("git", "init", "-q", cwd=source)
    _run("git", "config", "user.email", "test@example.com", cwd=source)
    _run("git", "config", "user.name", "Echo Test", cwd=source)
    _run("git", "add", ".", cwd=source)
    _run("git", "commit", "-qm", "fixture", cwd=source)
    return source


def _fake_wheel(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "echo_agent_runtime-1.2.3.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: echo-agent-runtime\nVersion: 1.2.3\n",
        )


def _fake_dependency_locks(path: Path) -> None:
    build = b"setuptools==70.0.0 \\\n    --hash=sha256:" + b"a" * 64 + b"\n"
    runtime = b"pydantic==2.0.0 \\\n    --hash=sha256:" + b"b" * 64 + b"\n"
    (path / agent_bundle.BUILD_DEPENDENCY_LOCK).write_bytes(build)
    (path / agent_bundle.RUNTIME_DEPENDENCY_LOCK).write_bytes(runtime)
    metadata = {
        "schemaVersion": 1,
        "kind": "echo-appliance-python-dependency-lock",
        "generator": {"name": "uv", "version": "0.11.25"},
        "pythonVersion": "3.12",
        "platforms": ["linux/amd64", "linux/arm64"],
        "onlyBinary": True,
        "inputs": {
            "osProject": {
                "name": "echo-os",
                "file": "pyproject.toml",
                "sha256": "c" * 64,
            },
            "agentProject": {
                "name": "echo-agent-runtime",
                "file": "pyproject.toml",
                "sha256": "d" * 64,
            },
            "agentExtras": ["serve", "tracing", "web", "local-auth", "video"],
            "buildRequirementsSha256": "e" * 64,
            "runtimeRequirementsSha256": "f" * 64,
        },
        "buildLock": {
            "file": agent_bundle.BUILD_DEPENDENCY_LOCK,
            "sha256": hashlib.sha256(build).hexdigest(),
            "packageCount": 1,
        },
        "runtimeLock": {
            "file": agent_bundle.RUNTIME_DEPENDENCY_LOCK,
            "sha256": hashlib.sha256(runtime).hexdigest(),
            "packageCount": 1,
        },
    }
    _write(path / agent_bundle.DEPENDENCY_LOCK_METADATA, json.dumps(metadata) + "\n")


def _fake_codex(path: Path, *, schema: str = "echo.codex_bundle.v1") -> None:
    executable = path / "bin" / "codex"
    executable.parent.mkdir(parents=True, exist_ok=True)
    # Minimal little-endian x86-64 ELF64 header; bundle tests validate target
    # identity and hashes, not process execution.
    header = bytearray(20)
    header[:6] = b"\x7fELF\x02\x01"
    header[18:20] = (62).to_bytes(2, "little")
    executable.write_bytes(header)
    executable.chmod(0o755)
    manifest = {
        "schema": schema,
        "package": "@openai/codex",
        "version": "0.149.0",
        "platformPackage": "@openai/codex-linux-x64",
        "target": "x86_64-unknown-linux-musl",
        "fileHashPhase": "pre-package",
        "files": {"bin/codex": hashlib.sha256(header).hexdigest()},
    }
    _write(path / "echo-codex-bundle.json", json.dumps(manifest) + "\n")


def test_record_codex_rejects_the_pre_echo_manifest_namespace(tmp_path: Path) -> None:
    source = _agent_checkout(tmp_path)
    identity_path = tmp_path / "source.json"
    agent_bundle.capture_source(source, identity_path, allow_dirty=False)
    codex = tmp_path / "agent-codex"
    _fake_codex(codex, schema="octo" + "pus.codex_bundle.v1")

    with pytest.raises(agent_bundle.BundleError, match="Codex bundle does not match"):
        agent_bundle.record_codex(source, identity_path, codex)


def _prepared_bundle(tmp_path: Path) -> tuple[Path, Path]:
    source = _agent_checkout(tmp_path)
    bundle = tmp_path / "bundle"
    identity_path = tmp_path / "source.json"
    agent_bundle.capture_source(source, identity_path, allow_dirty=False)

    wheel_dir = bundle / "agent-dist"
    wheel_path = wheel_dir / "echo_agent_runtime-1.2.3-py3-none-any.whl"
    _fake_wheel(wheel_path)
    _fake_dependency_locks(wheel_dir)
    agent_bundle.record_wheel(
        source,
        identity_path,
        wheel_dir,
        ["serve", "tracing", "web", "local-auth", "video"],
    )

    agent_bundle.export_resources(
        source,
        identity_path,
        bundle / "agent-resources",
    )
    codex = bundle / "agent-codex"
    _fake_codex(codex)
    agent_bundle.record_codex(source, identity_path, codex)
    manifest = bundle / "agent-bundle.json"
    agent_bundle.assemble_bundle(bundle, identity_path, manifest)
    return bundle, manifest


def test_assemble_and_verify_bundle_from_one_source(tmp_path: Path) -> None:
    bundle, manifest_path = _prepared_bundle(tmp_path)
    first_manifest_bytes = manifest_path.read_bytes()

    manifest = agent_bundle.verify_bundle(bundle, manifest_path)
    identity_path = tmp_path / "source.json"
    agent_bundle.assemble_bundle(bundle, identity_path, manifest_path)

    assert manifest["source"]["dirty"] is False
    assert manifest["assembled_at"] == manifest["source"]["commit_time"]
    assert manifest_path.read_bytes() == first_manifest_bytes
    assert int(manifest_path.stat().st_mtime) == manifest["source"]["source_date_epoch"]
    assert manifest["wheel"]["distribution"] == "echo-agent-runtime"
    assert manifest["schema_version"] == 2
    assert "webui" not in manifest
    assert "webui_package" not in manifest["source"]
    assert "webui_version" not in manifest["source"]
    assert manifest["source"]["packaged_codex_version"] == "0.149.0"
    assert manifest["codex"]["target"] == "x86_64-unknown-linux-musl"
    assert manifest["resources"]["file_count"] == 7
    requirement = (bundle / "agent-dist" / "requirements.txt").read_text()
    assert requirement.startswith(
        "echo-agent-runtime[serve,tracing,web,local-auth,video] @ file:///build/agent-dist/"
    )
    assert manifest["wheel"]["python_dependencies"]["platforms"] == [
        "linux/amd64",
        "linux/arm64",
    ]


def test_verify_rejects_mutated_python_dependency_lock(tmp_path: Path) -> None:
    bundle, manifest_path = _prepared_bundle(tmp_path)
    lock = bundle / "agent-dist" / agent_bundle.RUNTIME_DEPENDENCY_LOCK
    lock.write_bytes(lock.read_bytes() + b"changed\n")

    with pytest.raises(agent_bundle.BundleError, match="dependency lock metadata mismatch"):
        agent_bundle.verify_bundle(bundle, manifest_path)


def test_source_identity_accepts_older_frontend_without_bundled_codex(
    tmp_path: Path,
) -> None:
    source = _agent_checkout(tmp_path)
    package_path = source / "frontend" / "package.json"
    package = json.loads(package_path.read_text())
    package.pop("devDependencies")
    package_path.write_text(json.dumps(package))
    _run("git", "add", str(package_path), cwd=source)
    _run("git", "commit", "-qm", "older frontend", cwd=source)

    identity = agent_bundle.source_identity(source)

    assert identity["packaged_codex_version"] is None


def test_verify_rejects_mutated_artifact(tmp_path: Path) -> None:
    bundle, manifest_path = _prepared_bundle(tmp_path)
    _write(bundle / "agent-resources" / "config.example.yaml", "tampered\n")

    with pytest.raises(agent_bundle.BundleError, match="resources"):
        agent_bundle.verify_bundle(bundle, manifest_path)


def test_dirty_source_requires_explicit_local_qa_override(tmp_path: Path) -> None:
    source = _agent_checkout(tmp_path)
    _write(source / "prompts" / "system.md", "Changed.\n")
    identity_path = tmp_path / "source.json"

    with pytest.raises(agent_bundle.BundleError, match="dirty"):
        agent_bundle.capture_source(source, identity_path, allow_dirty=False)

    identity = agent_bundle.capture_source(source, identity_path, allow_dirty=True)
    assert identity["dirty"] is True
    assert identity["source_id"].startswith(f"{identity['commit']}+dirty.")


def test_runtime_state_is_excluded_but_bundled_plugins_remain_source(tmp_path: Path) -> None:
    source = _agent_checkout(tmp_path)
    runtime_paths = (
        ".echo-home/.echo/sessions/private.json",
        ".echo/artifacts/thread/output.txt",
        ".echo/research/cache.json",
        "agents/default/agent-core/.scores.jsonl",
        "agents/default/agent-core/..scores.jsonl.transaction.lock",
        "agents/default/agent-core/tenants/opaque/.scores.jsonl",
        "agents/default/agent-core/sessions/private.json",
        "agents/default/agent-core/workspace/result.txt",
    )
    for relative in runtime_paths:
        _write(source / relative, "private runtime state\n")

    clean = agent_bundle.source_identity(source)
    assert clean["dirty"] is False
    assert clean["snapshot_paths"] == []
    assert not set(runtime_paths).intersection(agent_bundle._resource_files(source))

    plugin = ".echo/plugins/local/skills/demo/SKILL.md"
    _write(source / plugin, "# Bundled capability\n")
    changed = agent_bundle.source_identity(source)

    assert changed["dirty"] is True
    assert changed["snapshot_paths"] == [plugin]
    assert plugin in agent_bundle._resource_files(source)


def test_ephemeral_cache_outputs_are_excluded_from_source_and_resources(tmp_path: Path) -> None:
    source = _agent_checkout(tmp_path)
    ephemeral_paths = (
        ".echo-tmp/.ses",
        ".echo/plugins/local/scripts/__pycache__/tool.cpython-312.pyc",
        ".echo/plugins/local/scripts/tool.pyc",
    )
    for relative in ephemeral_paths:
        _write(source / relative, "generated cache output\n")

    identity = agent_bundle.source_identity(source)

    assert identity["dirty"] is False
    assert identity["snapshot_paths"] == []
    assert not set(ephemeral_paths).intersection(agent_bundle._resource_files(source))


def test_generated_bundle_outputs_do_not_make_dirty_identity_self_referential(
    tmp_path: Path,
) -> None:
    source = _agent_checkout(tmp_path)
    generated = (
        "deploy/appliance/agent-bundle.json",
        "deploy/appliance/agent-dist/agent-build.json",
        "deploy/appliance/agent-resources/agent-build.json",
        "deploy/appliance/agent-webui/agent-build.json",
        "deploy/appliance/agent-codex/agent-build.json",
    )
    for relative in generated:
        _write(source / relative, "generated output\n")

    identity = agent_bundle.source_identity(source)

    assert identity["dirty"] is False
    assert identity["snapshot_paths"] == []


def test_embedded_agent_api_probe_requires_every_domain_in_isolated_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "agent-source"
    source.mkdir()
    report = {
        "schema": "echo.agent_api_contract.v1",
        "compatible": True,
        "required": [
            {"id": domain, "compatible": True, "missing": []} for domain in ALL_AGENT_API_DOMAINS
        ],
        "optional": [],
    }
    observed: dict[str, object] = {}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, json.dumps(report), "")

    monkeypatch.setattr(agent_bundle.subprocess, "run", run)

    result = agent_bundle.verify_embedded_agent_api(source)

    assert result == report
    command = observed["command"]
    assert isinstance(command, list)
    assert command[:2] == [agent_bundle.sys.executable, "-c"]
    assert isinstance(command[2], str)
    assert "ALL_AGENT_API_DOMAINS" in command[2]
    assert command[3] == str(
        Path(agent_bundle.__file__).resolve().parents[2] / "appliance" / "agent_api" / "contract.py"
    )
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert "PYTHONPATH" not in environment
    assert observed["cwd"] == source.resolve()
    assert observed["timeout"] == 60


def test_embedded_agent_api_cannot_shadow_the_os_contract(tmp_path: Path) -> None:
    source = tmp_path / "agent-source"
    marker = source / "forged-contract-loaded"
    _write(source / "runtime" / "__init__.py", "")
    _write(source / "appliance" / "__init__.py", "")
    _write(source / "appliance" / "agent_api" / "__init__.py", "")
    _write(
        source / "appliance" / "agent_api" / "contract.py",
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('forged')\n",
    )

    with pytest.raises(agent_bundle.BundleError, match="does not satisfy"):
        agent_bundle.verify_embedded_agent_api(source)

    assert not marker.exists()


def test_dirty_source_can_be_frozen_while_live_checkout_moves(tmp_path: Path) -> None:
    source = _agent_checkout(tmp_path)
    _write(source / "prompts" / "system.md", "Frozen change.\n")
    _write(source / "agents" / "local.yaml", "name: local\n")
    identity_path = tmp_path / "source.json"
    expected = agent_bundle.capture_source(source, identity_path, allow_dirty=True)

    snapshot = tmp_path / "snapshot"
    actual = agent_bundle.snapshot_source(source, identity_path, snapshot)
    _write(source / "prompts" / "system.md", "Later live change.\n")

    assert actual == expected
    assert agent_bundle.source_identity(snapshot) == expected
    assert (snapshot / "prompts" / "system.md").read_text() == "Frozen change.\n"
    assert (snapshot / "agents" / "local.yaml").read_text() == "name: local\n"


def test_dirty_snapshot_overlays_only_changed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _agent_checkout(tmp_path)
    _write(source / "prompts" / "system.md", "Frozen change.\n")
    _write(source / "agents" / "local.yaml", "name: local\n")
    identity_path = tmp_path / "source.json"
    agent_bundle.capture_source(source, identity_path, allow_dirty=True)
    copied: list[str] = []
    original_copy2 = agent_bundle.shutil.copy2

    def record_copy(source_path: Path, target_path: Path, **kwargs: object) -> str:
        copied.append(Path(source_path).relative_to(source).as_posix())
        return original_copy2(source_path, target_path, **kwargs)

    monkeypatch.setattr(agent_bundle.shutil, "copy2", record_copy)
    agent_bundle.snapshot_source(source, identity_path, tmp_path / "snapshot")

    assert copied == ["agents/local.yaml", "prompts/system.md"]


def test_dirty_snapshot_preserves_clean_mixed_eol_bytes(tmp_path: Path) -> None:
    source = _agent_checkout(tmp_path)
    _write(source / ".gitattributes", "*.txt text eol=lf\n")
    _write(source / "licenses" / "notice.txt", "line one\nline two\n")
    _run("git", "add", ".gitattributes", "licenses/notice.txt", cwd=source)
    _run("git", "commit", "-qm", "add normalized notice", cwd=source)
    notice = source / "licenses" / "notice.txt"
    notice.write_bytes(b"line one\r\nline two\n")
    _write(source / "prompts" / "system.md", "Frozen change.\n")
    assert (
        "licenses/notice.txt"
        not in subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--"],
            cwd=source,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    identity_path = tmp_path / "source.json"
    expected = agent_bundle.capture_source(source, identity_path, allow_dirty=True)

    snapshot = tmp_path / "snapshot"
    actual = agent_bundle.snapshot_source(source, identity_path, snapshot)

    assert actual == expected
    assert (snapshot / "licenses" / "notice.txt").read_bytes() == notice.read_bytes()


def test_verify_installed_checks_version_and_console_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "agent-bundle.json"
    manifest_path.write_text(
        json.dumps(
            {
                "wheel": {
                    "distribution": "echo-agent-runtime",
                    "version": "1.2.3",
                }
            }
        )
    )

    class _EntryPoint:
        group = "console_scripts"
        name = "echo-agent"

    class _Distribution:
        version = "1.2.3"
        entry_points = [_EntryPoint()]

    monkeypatch.setattr(
        agent_bundle.importlib.metadata,
        "distribution",
        lambda _name: _Distribution(),
    )

    assert agent_bundle.verify_installed(manifest_path)["wheel"]["version"] == "1.2.3"


def test_promote_dir_accepts_an_external_staging_directory(tmp_path: Path) -> None:
    stage = tmp_path / "external" / "stage"
    destination = tmp_path / "bundle" / "agent-dist"
    stage.mkdir(parents=True)
    (stage / agent_bundle.PARTIAL_MANIFEST).write_text("{}")
    (stage / "payload.txt").write_text("verified")

    agent_bundle.promote_dir(stage, destination)

    assert (destination / "payload.txt").read_text() == "verified"
    assert stage.is_dir()
