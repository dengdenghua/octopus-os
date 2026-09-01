"""The signed Hub index and published archive must describe one closed set."""

from __future__ import annotations

import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "extensions"
    / "workbuddy-experts"
    / "scripts"
    / "verify-marketplace-release.py"
)
SPEC = importlib.util.spec_from_file_location("verify_marketplace_release", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _release(tmp_path: Path) -> tuple[Path, Path]:
    catalog = tmp_path / "plugin-store.json"
    catalog.write_text(
        json.dumps(
            {
                "meta": {},
                "items": [
                    {
                        "id": "codex_documents",
                        "plugin": "documents",
                        "kind": "plugin",
                        "version": "1.0.0",
                        "host_api": ">=0.2,<0.3",
                        "permissions": ["content.read"],
                        "auth_modes": [],
                        "dependencies": [],
                        "runtime_dependencies": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    archive = tmp_path / "plugins.tar.gz"
    manifest = json.dumps(
        {
            "name": "documents",
            "version": "1.0.0",
            "echo": {
                "host_api": ">=0.2,<0.3",
                "permissions": ["content.read"],
                "auth_modes": [],
                "dependencies": [],
                "runtime_dependencies": [],
            },
        }
    ).encode("utf-8")
    with tarfile.open(archive, "w:gz") as handle:
        member = tarfile.TarInfo("plugins/codex/documents/.codex-plugin/plugin.json")
        member.size = len(manifest)
        handle.addfile(member, io.BytesIO(manifest))
    return catalog, archive


def test_release_verifier_accepts_exact_catalog_archive_closure(tmp_path: Path) -> None:
    catalog, archive = _release(tmp_path)

    assert MODULE.verify_release(catalog, archive) == {
        "plugin": 1,
        "connector": 0,
        "workbench": 0,
    }


def test_release_verifier_rejects_private_build_paths(tmp_path: Path) -> None:
    catalog, archive = _release(tmp_path)
    payload = json.loads(catalog.read_text(encoding="utf-8"))
    payload["items"][0]["path"] = "/Users/release-runner/private/documents"
    catalog.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="private filesystem path leaked"):
        MODULE.verify_release(catalog, archive)


def test_release_verifier_rejects_catalog_entry_missing_from_archive(
    tmp_path: Path,
) -> None:
    catalog, archive = _release(tmp_path)
    payload = json.loads(catalog.read_text(encoding="utf-8"))
    payload["items"][0]["plugin"] = "missing"
    catalog.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="missing from content archive"):
        MODULE.verify_release(catalog, archive)


def test_skill_release_verifier_requires_exact_closed_set(tmp_path: Path) -> None:
    catalog = tmp_path / "skill-registry.json"
    catalog.write_text(
        json.dumps({"meta": {}, "skills": [{"name": "summarize", "version": "1.0.0"}]}),
        encoding="utf-8",
    )
    archive = tmp_path / "skills.tar.gz"
    content = b"---\nname: summarize\ndescription: Summarize a document\n---\n"
    with tarfile.open(archive, "w:gz") as handle:
        member = tarfile.TarInfo("skills/summarize/SKILL.md")
        member.size = len(content)
        handle.addfile(member, io.BytesIO(content))

    assert MODULE.verify_skill_release(catalog, archive) == {"skills": 1}

    payload = json.loads(catalog.read_text(encoding="utf-8"))
    payload["skills"].append({"name": "missing"})
    catalog.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="missing from content archive"):
        MODULE.verify_skill_release(catalog, archive)
