from __future__ import annotations

import io
import json
import os
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from runtime.platform.io import atomic_write_json


class BackupManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str = "1"
    created_at: str = ""
    echo_version: str = "0.1.0"
    components: list[str] = Field(default_factory=list)
    total_bytes: int = 0
    total_files: int = 0


class BackupReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    output_path: str
    manifest: BackupManifest
    success: bool = True
    error: str = ""


class RestoreReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_path: str
    components_restored: list[str] = Field(default_factory=list)
    files_restored: int = 0
    success: bool = True
    error: str = ""


class BackupManager:
    COMPONENTS = {
        "journal": "events.jsonl",
        "kg": "knowledge_graph.json",
        "config": "config.yaml",
        "hot_cache": "hot_cache/",
        "skills": "skills/",
        "agents": "agents/",
        # Narrative Studio stores projects below the shared Echo data root.
        # Treat it as a first-class backup component so story worlds, drafts,
        # reviews and immutable canon commits survive a machine migration.
        "narrative_studio": "data/narrative-studio/",
    }

    def __init__(self, base_dir: str | Path | None = None) -> None:
        """Create a backup manager for the active Echo data layout.

        ``base_dir`` is intentionally optional.  An explicit value preserves
        the historical layout where every component is resolved below that
        directory (including ``data/narrative-studio``).  Without one, the
        runtime environment owns the location of Narrative Studio data:

        * ``ECHO_DATA_DIR`` points directly at the shared data directory;
        * otherwise ``ECHO_HOME`` stores data below ``<home>/data``;
        * without either override, the legacy ``~/.echo`` root is kept.

        Archive names remain independent from those physical locations so a
        backup can be restored on a machine with a different data directory.
        """

        self._explicit_base = base_dir is not None
        if self._explicit_base:
            self._base = Path(os.path.expanduser(str(base_dir)))
            self._data_root = self._base / "data"
            return

        home_override = os.environ.get("ECHO_HOME")
        self._base = (
            Path(os.path.expanduser(home_override)) if home_override else Path.home() / ".echo"
        )
        data_override = os.environ.get("ECHO_DATA_DIR")
        self._data_root = (
            Path(os.path.expanduser(data_override)) if data_override else self._base / "data"
        )

    def backup(
        self,
        output: str | Path | None = None,
        components: list[str] | None = None,
    ) -> BackupReport:
        if output is None:
            ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            output = self._base / f"backup-{ts}.tar.gz"
        output = Path(os.path.expanduser(str(output)))
        output.parent.mkdir(parents=True, exist_ok=True)

        selected = components or list(self.COMPONENTS.keys())
        included_components: list[str] = []
        total_bytes = 0
        total_files = 0

        try:
            with tarfile.open(str(output), "w:gz") as tar:
                for comp_name in selected:
                    rel_path = self.COMPONENTS.get(comp_name)
                    if rel_path is None:
                        continue
                    abs_path = self._component_path(comp_name, rel_path)
                    if not abs_path.exists():
                        continue
                    included_components.append(comp_name)
                    if abs_path.is_file():
                        tar.add(str(abs_path), arcname=rel_path)
                        total_bytes += abs_path.stat().st_size
                        total_files += 1
                    elif abs_path.is_dir():
                        for f in abs_path.rglob("*"):
                            if f.is_file():
                                tar.add(
                                    str(f),
                                    arcname=self._archive_name(comp_name, rel_path, abs_path, f),
                                )
                                total_bytes += f.stat().st_size
                                total_files += 1

                manifest = BackupManifest(
                    created_at=datetime.now(UTC).isoformat(),
                    components=included_components,
                    total_bytes=total_bytes,
                    total_files=total_files,
                )
                manifest_data = manifest.model_dump_json(indent=2).encode("utf-8")
                info = tarfile.TarInfo(name="MANIFEST.json")
                info.size = len(manifest_data)
                tar.addfile(info, io.BytesIO(manifest_data))

            return BackupReport(
                output_path=str(output),
                manifest=manifest,
            )
        except Exception as exc:
            return BackupReport(
                output_path=str(output),
                manifest=BackupManifest(),
                success=False,
                error=str(exc),
            )

    def restore(
        self,
        input_path: str | Path,
        components: list[str] | None = None,
        overwrite: bool = False,
    ) -> RestoreReport:
        input_path = Path(os.path.expanduser(str(input_path)))
        if not input_path.exists():
            return RestoreReport(
                input_path=str(input_path),
                success=False,
                error=f"backup file not found: {input_path}",
            )

        selected = set(components or list(self.COMPONENTS.keys()))
        restored_components: list[str] = []
        files_restored = 0

        try:
            with tarfile.open(str(input_path), "r:gz") as tar:
                for member in tar.getmembers():
                    if member.name == "MANIFEST.json":
                        continue
                    comp_name = self._component_from_path(member.name)
                    # Restore only declared components.  This also keeps an
                    # archive from smuggling unrelated files into the Echo
                    # home directory.
                    if comp_name is None or comp_name not in selected:
                        continue
                    dest = self._restore_target(comp_name, member.name)
                    if dest.exists() and not overwrite:
                        continue
                    if member.isdir():
                        dest.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with tar.extractfile(member) as src:
                            if src is not None:
                                dest.write_bytes(src.read())
                                files_restored += 1
                        if comp_name and comp_name not in restored_components:
                            restored_components.append(comp_name)

            return RestoreReport(
                input_path=str(input_path),
                components_restored=restored_components,
                files_restored=files_restored,
            )
        except Exception as exc:
            return RestoreReport(
                input_path=str(input_path),
                success=False,
                error=str(exc),
            )

    def export_json(
        self,
        output: str | Path,
        components: list[str] | None = None,
    ) -> Path:
        output = Path(os.path.expanduser(str(output)))
        output.parent.mkdir(parents=True, exist_ok=True)
        selected = components or list(self.COMPONENTS.keys())
        data: dict[str, Any] = {
            "exported_at": datetime.now(UTC).isoformat(),
            "echo_version": "0.1.0",
        }

        for comp_name in selected:
            rel_path = self.COMPONENTS.get(comp_name)
            if rel_path is None:
                continue
            abs_path = self._component_path(comp_name, rel_path)
            if not abs_path.exists():
                continue
            if abs_path.is_file():
                try:
                    text = abs_path.read_text(encoding="utf-8")
                    if abs_path.suffix == ".json":
                        data[comp_name] = json.loads(text)
                    elif abs_path.suffix == ".jsonl":
                        data[comp_name] = [
                            json.loads(line) for line in text.strip().splitlines() if line.strip()
                        ]
                    else:
                        data[comp_name] = text
                except (OSError, json.JSONDecodeError):
                    data[comp_name] = f"<unreadable: {abs_path}>"
            elif abs_path.is_dir():
                file_list = sorted(
                    self._archive_name(comp_name, rel_path, abs_path, f)
                    for f in abs_path.rglob("*")
                    if f.is_file()
                )
                data[comp_name] = {"files": file_list, "count": len(file_list)}

        atomic_write_json(output, data)
        return output

    def _component_path(self, comp_name: str, rel_path: str | None = None) -> Path:
        """Return a component's physical path for this machine."""

        rel_path = rel_path or self.COMPONENTS[comp_name]
        if comp_name == "narrative_studio" and not self._explicit_base:
            return self._data_root / "narrative-studio"
        return self._base / rel_path

    @staticmethod
    def _archive_name(
        comp_name: str,
        rel_path: str,
        component_path: Path,
        file_path: Path,
    ) -> str:
        """Map a physical file to its stable, portable archive name."""

        del comp_name  # reserved for future component-specific archive schemas
        archive_root = PurePosixPath(rel_path.strip("/"))
        relative = file_path.relative_to(component_path)
        return archive_root.joinpath(*relative.parts).as_posix()

    def _restore_target(self, comp_name: str, member_name: str) -> Path:
        """Map a portable archive member to a safe machine-local target."""

        if "\\" in member_name or "\x00" in member_name:
            raise ValueError(f"backup member escapes destination: {member_name}")

        archive_member = PurePosixPath(member_name)
        if archive_member.is_absolute() or ".." in archive_member.parts:
            raise ValueError(f"backup member escapes destination: {member_name}")

        rel_path = self.COMPONENTS[comp_name]
        archive_root = PurePosixPath(rel_path.strip("/"))
        try:
            suffix = archive_member.relative_to(archive_root)
        except ValueError as exc:  # defensive: component matching must agree
            raise ValueError(f"backup member escapes destination: {member_name}") from exc

        component_path = self._component_path(comp_name, rel_path)
        if not rel_path.endswith("/") and suffix.parts:
            raise ValueError(f"backup member escapes destination: {member_name}")

        target = component_path.joinpath(*suffix.parts).resolve()
        if rel_path.endswith("/"):
            destination_root = component_path.resolve()
            if target != destination_root and destination_root not in target.parents:
                raise ValueError(f"backup member escapes destination: {member_name}")
        elif target != component_path.resolve():
            raise ValueError(f"backup member escapes destination: {member_name}")
        return target

    def _component_from_path(self, arcname: str) -> str | None:
        candidate = arcname.strip("/")
        # Component roots may be nested (for example
        # ``data/narrative-studio``), so matching only the first path segment
        # loses the component identity during a selective restore.
        for comp_name, rel_path in sorted(
            self.COMPONENTS.items(),
            key=lambda item: len(item[1]),
            reverse=True,
        ):
            root = rel_path.strip("/")
            if candidate == root or candidate.startswith(root + "/"):
                return comp_name
        return None
