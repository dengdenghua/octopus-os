from __future__ import annotations

import io
import logging
import posixpath
import stat
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

logger = logging.getLogger(__name__)


_DEFAULT_MAX_ENTRY_BYTES = 20 * 1024 * 1024  # Implementation note.
_DEFAULT_MAX_TOTAL_BYTES = 100 * 1024 * 1024  # Implementation note.


class ArchiveSafetyError(ValueError):
    pass


def _unsafe_path(name: str) -> bool:
    if not name:
        return False
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    posix = PurePosixPath(normalized)
    if posix.is_absolute():
        return True
    if PureWindowsPath(name).is_absolute():
        return True
    return ".." in posix.parts


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode) if mode else False


def _should_ignore(p: Path) -> bool:
    return p.name.startswith(".") or p.name == "__MACOSX"


def _has_symlink_component(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    cur = root
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            return True
    return False


def safe_extract_zip(
    archive_bytes: bytes | zipfile.ZipFile,
    dest_dir: str | Path,
    *,
    max_entry_bytes: int = _DEFAULT_MAX_ENTRY_BYTES,
    max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
) -> None:
    dest_root = Path(dest_dir).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)

    if isinstance(archive_bytes, zipfile.ZipFile):
        zf = archive_bytes
        owns_zf = False
    else:
        try:
            zf = zipfile.ZipFile(io.BytesIO(archive_bytes))
        except zipfile.BadZipFile as e:
            raise ArchiveSafetyError(f"bad zip: {e}") from e
        owns_zf = True

    try:
        total_written = 0
        for info in zf.infolist():
            if _unsafe_path(info.filename):
                raise ArchiveSafetyError(
                    f"unsafe member path: {info.filename!r}",
                )
            if _is_symlink(info):
                logger.warning(
                    "skipping symlink entry: %s",
                    info.filename,
                )
                continue
            normalized = posixpath.normpath(
                info.filename.replace("\\", "/"),
            )
            if normalized in (".", "..") or normalized.startswith(".."):
                raise ArchiveSafetyError(
                    f"normalized path traversal: {info.filename!r}",
                )
            member_path = dest_root.joinpath(
                *PurePosixPath(normalized).parts,
            )
            if _has_symlink_component(member_path, dest_root):
                raise ArchiveSafetyError(
                    f"zip entry crosses symlink: {info.filename!r}",
                )
            if not member_path.resolve().is_relative_to(dest_root):
                raise ArchiveSafetyError(
                    f"zip entry escapes destination: {info.filename!r}",
                )
            if info.is_dir():
                member_path.mkdir(parents=True, exist_ok=True)
                if _has_symlink_component(member_path, dest_root):
                    raise ArchiveSafetyError(
                        f"zip entry crosses symlink: {info.filename!r}",
                    )
                continue

            member_path.parent.mkdir(parents=True, exist_ok=True)
            if _has_symlink_component(member_path, dest_root):
                raise ArchiveSafetyError(
                    f"zip entry crosses symlink: {info.filename!r}",
                )
            entry_written = 0
            with zf.open(info) as src, member_path.open("wb") as dst:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    entry_written += len(chunk)
                    total_written += len(chunk)
                    if entry_written > max_entry_bytes:
                        raise ArchiveSafetyError(
                            f"entry too large: {info.filename!r} "
                            f"({entry_written} > {max_entry_bytes})",
                        )
                    if total_written > max_total_bytes:
                        raise ArchiveSafetyError(
                            f"archive total too large ({total_written} > {max_total_bytes})",
                        )
                    dst.write(chunk)
    finally:
        if owns_zf:
            zf.close()


def _resolve_skill_dir(extracted: Path) -> Path:
    items = [p for p in extracted.iterdir() if not _should_ignore(p)]
    if not items:
        raise ArchiveSafetyError("archive is empty after filtering")
    if len(items) == 1 and items[0].is_dir():
        return items[0]
    return extracted


def install_from_archive(
    archive_bytes: bytes,
    *,
    dest_dir: str | Path,
    overwrite: bool = False,
    max_entry_bytes: int = _DEFAULT_MAX_ENTRY_BYTES,
    max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
) -> Path:
    dest = Path(dest_dir)
    if dest.exists():
        if not overwrite:
            raise FileExistsError(
                f"{dest} already exists (pass overwrite=True to replace)",
            )
        import shutil

        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    safe_extract_zip(
        archive_bytes,
        dest_dir=dest,
        max_entry_bytes=max_entry_bytes,
        max_total_bytes=max_total_bytes,
    )
    skill_dir = _resolve_skill_dir(dest)

    md_files = list(skill_dir.glob("*.md"))
    if not any(m.name.lower() in {"skill.md", "skill.markdown"} for m in md_files):
        raise ArchiveSafetyError(
            f"no SKILL.md found in {skill_dir} · not a skill archive",
        )
    return skill_dir
