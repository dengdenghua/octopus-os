from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from runtime._cli_parser import _build_parser
from runtime.platform.lifecycle.backup import BackupManager


def test_default_backup_includes_narrative_studio_projects(tmp_path: Path) -> None:
    project_file = (
        tmp_path / "data" / "narrative-studio" / "projects" / "story-one" / "project.json"
    )
    project_file.parent.mkdir(parents=True)
    project_file.write_text('{"id":"story-one"}\n', encoding="utf-8")

    output = tmp_path / "story-backup.tar.gz"
    report = BackupManager(str(tmp_path)).backup(output=output)

    assert report.success is True
    assert "narrative_studio" in report.manifest.components
    with tarfile.open(output, "r:gz") as archive:
        assert "data/narrative-studio/projects/story-one/project.json" in archive.getnames()


def test_narrative_studio_backup_can_be_restored_independently(tmp_path: Path) -> None:
    source = tmp_path / "source"
    project_file = source / "data" / "narrative-studio" / "projects" / "story-one" / "project.json"
    project_file.parent.mkdir(parents=True)
    project_file.write_text('{"id":"story-one"}\n', encoding="utf-8")

    output = tmp_path / "story-backup.tar.gz"
    report = BackupManager(str(source)).backup(
        output=output,
        components=["narrative_studio"],
    )
    assert report.success is True

    destination = tmp_path / "destination"
    restored = BackupManager(str(destination)).restore(
        output,
        components=["narrative_studio"],
    )

    assert restored.success is True
    assert restored.components_restored == ["narrative_studio"]
    restored_file = (
        destination / "data" / "narrative-studio" / "projects" / "story-one" / "project.json"
    )
    assert restored_file.read_text(encoding="utf-8") == '{"id":"story-one"}\n'


def test_restore_rejects_archive_member_outside_backup_root(tmp_path: Path) -> None:
    archive_path = tmp_path / "malicious.tar.gz"
    payload = b"do not write me"
    with tarfile.open(archive_path, "w:gz") as archive:
        member = tarfile.TarInfo("data/narrative-studio/../../../escaped.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    destination = tmp_path / "destination"
    report = BackupManager(str(destination)).restore(
        archive_path,
        components=["narrative_studio"],
    )

    assert report.success is False
    assert "escapes destination" in report.error
    assert not (tmp_path / "escaped.txt").exists()


def test_cli_allows_selective_narrative_backup_restore_and_export() -> None:
    parser = _build_parser()

    backup = parser.parse_args(["backup", "--components", "narrative_studio"])
    restore = parser.parse_args(["restore", "backup.tar.gz", "--components", "narrative_studio"])
    export = parser.parse_args(
        [
            "export",
            "--output",
            "stories.json",
            "--components",
            "narrative_studio",
        ]
    )

    assert backup.components == ["narrative_studio"]
    assert restore.components == ["narrative_studio"]
    assert export.components == ["narrative_studio"]


def test_custom_data_dir_backup_restore_and_export_are_relocatable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_data = tmp_path / "source" / "arbitrary-data-name"
    source_file = source_data / "narrative-studio" / "projects" / "story-one" / "project.json"
    source_file.parent.mkdir(parents=True)
    source_file.write_text('{"id":"story-one"}\n', encoding="utf-8")
    monkeypatch.setenv("ECHO_DATA_DIR", str(source_data))
    monkeypatch.delenv("ECHO_HOME", raising=False)

    artifact = tmp_path / "story-backup.tar.gz"
    backed_up = BackupManager().backup(
        output=artifact,
        components=["narrative_studio"],
    )

    assert backed_up.success is True
    assert backed_up.manifest.components == ["narrative_studio"]
    with tarfile.open(artifact, "r:gz") as archive:
        assert "data/narrative-studio/projects/story-one/project.json" in archive.getnames()
        assert not any("arbitrary-data-name" in name for name in archive.getnames())

    export_path = tmp_path / "stories.json"
    BackupManager().export_json(export_path, components=["narrative_studio"])
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["narrative_studio"] == {
        "files": ["data/narrative-studio/projects/story-one/project.json"],
        "count": 1,
    }

    destination_data = tmp_path / "destination" / "another-data-name"
    monkeypatch.setenv("ECHO_DATA_DIR", str(destination_data))
    restored = BackupManager().restore(
        artifact,
        components=["narrative_studio"],
    )

    assert restored.success is True
    assert restored.components_restored == ["narrative_studio"]
    restored_file = (
        destination_data / "narrative-studio" / "projects" / "story-one" / "project.json"
    )
    assert restored_file.read_text(encoding="utf-8") == '{"id":"story-one"}\n'
    assert not (destination_data / "data" / "narrative-studio").exists()


def test_implicit_echo_home_uses_home_data_directory(tmp_path: Path, monkeypatch) -> None:
    echo_home = tmp_path / "custom-home"
    project_file = (
        echo_home / "data" / "narrative-studio" / "projects" / "home-story" / "project.json"
    )
    project_file.parent.mkdir(parents=True)
    project_file.write_text('{"id":"home-story"}\n', encoding="utf-8")
    monkeypatch.delenv("ECHO_DATA_DIR", raising=False)
    monkeypatch.setenv("ECHO_HOME", str(echo_home))

    artifact = tmp_path / "home-story.tar.gz"
    report = BackupManager().backup(output=artifact, components=["narrative_studio"])

    assert report.success is True
    with tarfile.open(artifact, "r:gz") as archive:
        assert "data/narrative-studio/projects/home-story/project.json" in archive.getnames()


def test_implicit_without_environment_keeps_dot_echo_layout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    user_home = tmp_path / "user-home"
    project_file = (
        user_home
        / ".echo"
        / "data"
        / "narrative-studio"
        / "projects"
        / "legacy-story"
        / "project.json"
    )
    project_file.parent.mkdir(parents=True)
    project_file.write_text('{"id":"legacy-story"}\n', encoding="utf-8")
    monkeypatch.delenv("ECHO_DATA_DIR", raising=False)
    monkeypatch.delenv("ECHO_HOME", raising=False)
    monkeypatch.setenv("HOME", str(user_home))

    artifact = tmp_path / "legacy-story.tar.gz"
    report = BackupManager().backup(output=artifact, components=["narrative_studio"])

    assert report.success is True
    with tarfile.open(artifact, "r:gz") as archive:
        assert "data/narrative-studio/projects/legacy-story/project.json" in archive.getnames()


def test_cli_backup_commands_leave_base_dir_unset_by_default() -> None:
    parser = _build_parser()

    assert parser.parse_args(["backup"]).base_dir is None
    assert parser.parse_args(["restore", "backup.tar.gz"]).base_dir is None
    assert parser.parse_args(["export", "--output", "export.json"]).base_dir is None
    assert parser.parse_args(["backup", "--base-dir", "/explicit"]).base_dir == "/explicit"

