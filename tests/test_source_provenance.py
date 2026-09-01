from __future__ import annotations

import os
from pathlib import Path

import pytest

from benchmarks.source_provenance import (
    ENGINE_COMPARISON_SOURCE_RULE,
    FILE_DIGEST_SCHEMA,
    FILE_DIGEST_VERSION,
    SOURCE_MANIFEST_SCHEMA,
    SOURCE_MANIFEST_VERSION,
    build_file_manifest,
    build_source_manifest,
)


def _repository(root: Path) -> Path:
    files = {
        "pyproject.toml": "[project]\nname='fixture'\n",
        "uv.lock": "version = 1\n",
        "skills.lock.json": "{}\n",
        "agents/_shared/IDENTITY_BANNER.md": "shared\n",
        "prompts/planner_base.yaml": "content: planner\n",
        "benchmarks/behavioral-surpass-suite.json": "{}\n",
        "benchmarks/harness.py": "HARNESS = 1\n",
        "benchmarks/results/ignored.py": "IGNORED = True\n",
        "benchmarks/verifiers/verify_concurrent_cache.py": "print('verify cache')\n",
        "benchmarks/verifiers/verify_path_boundary.py": "print('verify')\n",
        "benchmarks/verifiers/unselected.py": "print('ignored')\n",
        "benchmarks/fixtures/coding.path-boundary/task.py": "VALUE = 1\n",
        "benchmarks/fixtures/coding.path-boundary/.contract": "fixture-v1\n",
        "runtime/engine.py": "ENGINE = 1\n",
        "runtime/execution/all_skills/ignored.py": "SKILL_PAYLOAD = True\n",
        "runtime/ignored.txt": "not executable source\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_source_manifest_is_versioned_stable_and_selective(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")

    first = build_source_manifest(root, selected_case_ids=["coding.path-boundary"])
    second = build_source_manifest(root, selected_case_ids=["coding.path-boundary"])

    assert first == second
    assert first.rule_id == ENGINE_COMPARISON_SOURCE_RULE
    assert first.selected_case_ids == ("coding.path-boundary",)
    assert first.to_dict()["schema"] == SOURCE_MANIFEST_SCHEMA
    assert first.to_dict()["version"] == SOURCE_MANIFEST_VERSION
    assert all(row["schema"] == FILE_DIGEST_SCHEMA for row in first.to_dict()["files"])
    assert all(row["version"] == FILE_DIGEST_VERSION for row in first.to_dict()["files"])
    paths = [record.path for record in first.files]
    assert paths == sorted(paths)
    assert "benchmarks/fixtures/coding.path-boundary/.contract" in paths
    assert "benchmarks/fixtures/coding.path-boundary/task.py" in paths
    assert "benchmarks/verifiers/verify_concurrent_cache.py" in paths
    assert "benchmarks/verifiers/verify_path_boundary.py" in paths
    assert "benchmarks/verifiers/unselected.py" not in paths
    assert "benchmarks/results/ignored.py" not in paths
    assert "runtime/execution/all_skills/ignored.py" not in paths
    assert "runtime/ignored.txt" not in paths
    assert "agents/_shared/IDENTITY_BANNER.md" in paths
    assert "prompts/planner_base.yaml" in paths
    assert "skills.lock.json" in paths


def test_selected_agent_inputs_are_bound(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    profile = root / "agents/coder/profile.jsonc"
    profile.parent.mkdir(parents=True)
    profile.write_text('{"id":"coder"}\n', encoding="utf-8")
    soul = root / "agents/coder/agent-core/SOUL.md"
    soul.parent.mkdir(parents=True)
    soul.write_text("persona v1\n", encoding="utf-8")

    before = build_source_manifest(
        root,
        selected_case_ids=["coding.path-boundary"],
        selected_agent_ids=["coder"],
    )
    soul.write_text("persona v2\n", encoding="utf-8")
    after = build_source_manifest(
        root,
        selected_case_ids=["coding.path-boundary"],
        selected_agent_ids=["coder"],
    )

    assert before.file("agents/coder/profile.jsonc")
    assert before.sha256 != after.sha256


@pytest.mark.parametrize(
    "relative_path",
    [
        "benchmarks/fixtures/coding.path-boundary/task.py",
        "benchmarks/verifiers/verify_concurrent_cache.py",
        "benchmarks/verifiers/verify_path_boundary.py",
        "runtime/engine.py",
    ],
)
def test_one_byte_change_changes_file_and_manifest_digest(
    tmp_path: Path,
    relative_path: str,
) -> None:
    root = _repository(tmp_path / "repo")
    before = build_source_manifest(root, selected_case_ids=["coding.path-boundary"])

    target = root / relative_path
    original = target.read_bytes()
    target.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    after = build_source_manifest(root, selected_case_ids=["coding.path-boundary"])

    assert before.sha256 != after.sha256
    assert before.file(relative_path).sha256 != after.file(relative_path).sha256


def test_explicit_manifest_order_is_canonical(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a").write_bytes(b"a")
    (root / "b").write_bytes(b"b")

    forward = build_file_manifest(root, ["a", "b"])
    reverse = build_file_manifest(root, ["b", "a"])

    assert forward == reverse


def test_shared_verifier_is_hashed_once_for_multiple_selected_cases(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    fixture = root / "benchmarks/fixtures/frontend.responsive-settings"
    fixture.mkdir(parents=True)
    (fixture / "contract.json").write_text("{}\n", encoding="utf-8")
    fixture = root / "benchmarks/fixtures/frontend.async-form-recovery"
    fixture.mkdir(parents=True)
    (fixture / "contract.json").write_text("{}\n", encoding="utf-8")
    verifier = root / "benchmarks/verifiers/verify_contract_case.py"
    verifier.write_text("print('verify contract')\n", encoding="utf-8")

    manifest = build_source_manifest(
        root,
        selected_case_ids=[
            "frontend.responsive-settings",
            "frontend.async-form-recovery",
        ],
    )

    paths = [record.path for record in manifest.files]
    assert paths.count("benchmarks/verifiers/verify_contract_case.py") == 1
    assert paths.count("benchmarks/verifiers/verify_concurrent_cache.py") == 1
    assert paths.count("benchmarks/verifiers/verify_path_boundary.py") == 1
    reversed_manifest = build_source_manifest(
        root,
        selected_case_ids=[
            "frontend.async-form-recovery",
            "frontend.responsive-settings",
        ],
    )
    assert manifest == reversed_manifest


def test_missing_selected_verifier_fails_closed(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    (root / "benchmarks/verifiers/verify_path_boundary.py").unlink()

    with pytest.raises(ValueError, match="does not exist"):
        build_source_manifest(root, selected_case_ids=["coding.path-boundary"])


def test_missing_unselected_full_chain_smoke_verifier_fails_closed(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    (root / "benchmarks/verifiers/verify_concurrent_cache.py").unlink()

    with pytest.raises(ValueError, match="does not exist"):
        build_source_manifest(root, selected_case_ids=["coding.path-boundary"])


def test_empty_selected_fixture_fails_closed(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    fixture = root / "benchmarks/fixtures/coding.path-boundary"
    for path in fixture.iterdir():
        path.unlink()

    with pytest.raises(ValueError, match="contains no regular files"):
        build_source_manifest(root, selected_case_ids=["coding.path-boundary"])


def test_missing_explicit_file_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    with pytest.raises(ValueError, match="does not exist"):
        build_file_manifest(root, ["missing.py"])


def test_unknown_and_duplicate_selected_cases_fail_closed(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")

    with pytest.raises(ValueError, match="not declared"):
        build_source_manifest(root, selected_case_ids=["coding.unknown"])
    with pytest.raises(ValueError, match="duplicates"):
        build_source_manifest(
            root,
            selected_case_ids=["coding.path-boundary", "coding.path-boundary"],
        )


def test_duplicate_file_input_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "source.py").write_text("pass\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate source input"):
        build_file_manifest(root, ["source.py", root / "source.py"])


def test_outside_and_non_regular_inputs_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret\n", encoding="utf-8")
    directory = root / "directory.py"
    directory.mkdir()

    with pytest.raises(ValueError, match="outside repository root"):
        build_file_manifest(root, [outside])
    with pytest.raises(ValueError, match="not a regular file"):
        build_file_manifest(root, [directory])


def test_symlink_input_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target.py"
    target.write_text("pass\n", encoding="utf-8")
    link = root / "link.py"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")

    with pytest.raises(ValueError, match="symlink"):
        build_file_manifest(root, [link])


def test_symlink_inside_selected_fixture_fails_closed(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    target = root / "benchmarks/fixtures/coding.path-boundary/task.py"
    link = target.with_name("linked.py")
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")

    with pytest.raises(ValueError, match="symlink"):
        build_source_manifest(root, selected_case_ids=["coding.path-boundary"])

