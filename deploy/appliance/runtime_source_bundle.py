#!/usr/bin/env python3
"""Build and verify the corresponding-source bundle for binary Python wheels."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
ARCHIVE_NAME = "echo-appliance-python-runtime-sources.tar.gz"
MANIFEST_NAME = "echo-appliance-python-runtime-sources.json"
CHECKSUM_NAME = "echo-appliance-python-runtime-sources.sha256"
VERIFIER_NAME = "runtime_source_bundle.py"
NOTICE_NAME = "PYTHON_RUNTIME_NOTICES.md"
ARCHIVE_ROOT = "echo-appliance-python-runtime-sources"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 200_000
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = Path(__file__).resolve()
NOTICE_PATH = VERIFIER_PATH.with_name("PYTHON_RUNTIME_NOTICES.md")


class SourceBundleError(RuntimeError):
    """The corresponding-source artifact is incomplete or unverifiable."""


@dataclass(frozen=True)
class SourceComponent:
    name: str
    version: str
    license: str
    repository: str
    ref: str
    commit: str

    @property
    def directory(self) -> str:
        return f"{self.name}-{self.version}"


COMPONENTS = (
    SourceComponent(
        name="pillow-heif",
        version="1.7.0",
        license="BSD-3-Clause; binary wheel GPL-2.0-only",
        repository="https://github.com/bigcat88/pillow_heif.git",
        ref="refs/tags/v1.7.0",
        commit="f65a9ac77809609ad8ebb001c691b5a3ee01146b",
    ),
    SourceComponent(
        name="libheif",
        version="1.23.3",
        license="LGPL-3.0-only",
        repository="https://github.com/strukturag/libheif.git",
        ref="refs/tags/v1.23.3",
        commit="78c9746aea226b22885e8d35241353ce669c4ea5",
    ),
    SourceComponent(
        name="libde265",
        version="1.1.2",
        license="LGPL-3.0-only",
        repository="https://github.com/strukturag/libde265.git",
        ref="refs/tags/v1.1.2",
        commit="d0bcab76380c079358a3156b3e3b37d17c00a078",
    ),
    SourceComponent(
        name="x265",
        version="4.2",
        license="GPL-2.0-only",
        repository="https://bitbucket.org/multicoreware/x265_git.git",
        ref="refs/tags/4.2",
        commit="e444744c03978c1fb4e037168967020cf2648427",
    ),
)

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
VERSION_PATTERN = re.compile(r"^[0-9][0-9A-Za-z.-]{0,31}$")
REF_PATTERN = re.compile(r"^refs/tags/[0-9A-Za-z][0-9A-Za-z._-]{0,95}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir() or path.is_symlink():
        raise SourceBundleError(f"unsafe output path: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o644)
        owned = descriptor
        descriptor = -1
        with os.fdopen(owned, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _run_git(repository: Path, *arguments: str) -> bytes:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "LC_ALL": "C",
        }
    )
    try:
        return subprocess.run(
            [
                "git",
                "-c",
                "advice.detachedHead=false",
                "-c",
                "core.hooksPath=NUL" if os.name == "nt" else "core.hooksPath=/dev/null",
                "-C",
                str(repository),
                *arguments,
            ],
            check=True,
            capture_output=True,
            env=environment,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"")[-1200:].decode("utf-8", "replace")
        raise SourceBundleError(f"git {' '.join(arguments)} failed: {detail}") from exc


def _fetch_archive(component: SourceComponent, workspace: Path) -> tuple[bytes, str]:
    repository = workspace / component.directory
    repository.mkdir()
    _run_git(repository, "init", "--quiet")
    _run_git(repository, "remote", "add", "origin", component.repository)
    _run_git(repository, "fetch", "--quiet", "--depth=1", "--no-tags", "origin", component.ref)
    actual = _run_git(repository, "rev-parse", "FETCH_HEAD^{commit}").decode().strip()
    if actual != component.commit:
        raise SourceBundleError(
            f"{component.name} ref resolved to {actual}, expected {component.commit}"
        )
    _run_git(repository, "fsck", "--no-dangling", "--no-progress", component.commit)
    tree = _run_git(repository, "rev-parse", f"{component.commit}^{{tree}}").decode().strip()
    archive = _run_git(repository, "archive", "--format=tar", component.commit)
    return archive, tree


def _validate_component(component: SourceComponent, *, allow_local_repository: bool) -> None:
    if NAME_PATTERN.fullmatch(component.name) is None:
        raise SourceBundleError(f"invalid source component name: {component.name}")
    if VERSION_PATTERN.fullmatch(component.version) is None:
        raise SourceBundleError(f"invalid source component version: {component.version}")
    if REF_PATTERN.fullmatch(component.ref) is None or COMMIT_PATTERN.fullmatch(component.commit) is None:
        raise SourceBundleError(f"invalid source identity for {component.name}")
    if not component.license or len(component.license) > 160 or any(char < " " for char in component.license):
        raise SourceBundleError(f"invalid source license for {component.name}")
    if component.repository.startswith("https://") and component.repository.endswith(".git"):
        return
    if allow_local_repository:
        repository = Path(component.repository)
        if repository.is_absolute() and repository.is_dir() and not repository.is_symlink():
            return
    raise SourceBundleError(f"invalid source repository for {component.name}")


def _safe_name(name: str) -> PurePosixPath:
    value = PurePosixPath(name)
    if not value.parts or value.is_absolute() or any(part in {"", ".", ".."} for part in value.parts):
        raise SourceBundleError(f"unsafe source archive path: {name}")
    return value


def _link_stays_inside(member_name: PurePosixPath, link_name: str, component_root: str) -> bool:
    link = PurePosixPath(link_name)
    if link.is_absolute():
        return False
    stack = list(member_name.parent.parts)
    for part in link.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not stack:
                return False
            stack.pop()
        else:
            stack.append(part)
    root_parts = PurePosixPath(component_root).parts
    return tuple(stack[: len(root_parts)]) == root_parts


def _normalized_info(source: tarfile.TarInfo, name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.mode = source.mode & 0o777
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    if source.isdir():
        info.type = tarfile.DIRTYPE
        info.mode = info.mode or 0o755
    elif source.isfile():
        info.type = tarfile.REGTYPE
        info.size = source.size
        info.mode = info.mode or 0o644
    elif source.issym():
        info.type = tarfile.SYMTYPE
        info.linkname = source.linkname
        info.size = 0
    else:
        raise SourceBundleError(f"unsupported source archive member: {source.name}")
    return info


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    archive.addfile(info, io.BytesIO(data))


def build_bundle(
    output_directory: Path,
    *,
    components: Sequence[SourceComponent] = COMPONENTS,
    notice_path: Path = NOTICE_PATH,
    allow_local_repositories: bool = False,
) -> dict[str, Any]:
    if not components or len({item.directory for item in components}) != len(components):
        raise SourceBundleError("source component directories must be non-empty and unique")
    if not notice_path.is_file() or notice_path.is_symlink():
        raise SourceBundleError("runtime notice is missing or unsafe")
    for component in components:
        _validate_component(component, allow_local_repository=allow_local_repositories)
    output_directory = output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, str]] = []
    source_archives: list[tuple[SourceComponent, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="echo-runtime-sources-") as temporary:
        workspace = Path(temporary)
        for component in components:
            source_archive, tree = _fetch_archive(component, workspace)
            records.append({**asdict(component), "tree": tree})
            source_archives.append((component, source_archive))

    notice_data = notice_path.read_bytes()
    contents = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo-appliance-python-runtime-source-components",
        "components": records,
    }
    raw = io.BytesIO()
    member_count = 0
    with (
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        tarfile.open(mode="w", fileobj=compressed, format=tarfile.PAX_FORMAT) as output,
    ):
        _add_bytes(output, f"{ARCHIVE_ROOT}/SOURCE_COMPONENTS.json", _json_bytes(contents))
        _add_bytes(output, f"{ARCHIVE_ROOT}/{NOTICE_NAME}", notice_data)
        member_count += 2
        for component, source_archive in source_archives:
            component_root = f"{ARCHIVE_ROOT}/sources/{component.directory}"
            with tarfile.open(mode="r:", fileobj=io.BytesIO(source_archive)) as source:
                for member in source:
                    original = _safe_name(member.name)
                    name = f"{component_root}/{original.as_posix()}"
                    normalized = _normalized_info(member, name)
                    if normalized.issym() and not _link_stays_inside(
                        PurePosixPath(name), normalized.linkname, component_root
                    ):
                        raise SourceBundleError(f"escaping source symlink: {name}")
                    payload = source.extractfile(member) if member.isfile() else None
                    output.addfile(normalized, payload)
                    member_count += 1
                    if member_count > MAX_MEMBERS:
                        raise SourceBundleError("source bundle member limit exceeded")

    archive_data = raw.getvalue()
    if not archive_data or len(archive_data) > MAX_ARCHIVE_BYTES:
        raise SourceBundleError("source bundle size is invalid")
    archive_path = output_directory / ARCHIVE_NAME
    manifest_path = output_directory / MANIFEST_NAME
    checksum_path = output_directory / CHECKSUM_NAME
    verifier_path = output_directory / VERIFIER_NAME
    published_notice_path = output_directory / NOTICE_NAME
    verifier_data = VERIFIER_PATH.read_bytes()
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo-appliance-python-runtime-source-bundle",
        "archive": {
            "file": ARCHIVE_NAME,
            "sha256": _sha256(archive_data),
            "size": len(archive_data),
            "memberCount": member_count,
        },
        "components": records,
        "notice": {"file": NOTICE_NAME, "sha256": _sha256(notice_data)},
        "verifier": {"file": VERIFIER_NAME, "sha256": _sha256(verifier_data)},
    }
    manifest_data = _json_bytes(manifest)
    checksum_data = (
        f"{_sha256(archive_data)}  {ARCHIVE_NAME}\n"
        f"{_sha256(manifest_data)}  {MANIFEST_NAME}\n"
        f"{_sha256(verifier_data)}  {VERIFIER_NAME}\n"
        f"{_sha256(notice_data)}  {NOTICE_NAME}\n"
    ).encode("ascii")
    _atomic_write(archive_path, archive_data)
    _atomic_write(manifest_path, manifest_data)
    _atomic_write(verifier_path, verifier_data)
    _atomic_write(published_notice_path, notice_data)
    _atomic_write(checksum_path, checksum_data)
    return manifest


def verify_bundle(output_directory: Path) -> dict[str, Any]:
    archive_path = output_directory / ARCHIVE_NAME
    manifest_path = output_directory / MANIFEST_NAME
    checksum_path = output_directory / CHECKSUM_NAME
    verifier_path = output_directory / VERIFIER_NAME
    notice_path = output_directory / NOTICE_NAME
    for path, maximum in (
        (archive_path, MAX_ARCHIVE_BYTES),
        (manifest_path, 1024 * 1024),
        (checksum_path, 4096),
        (verifier_path, 1024 * 1024),
        (notice_path, 1024 * 1024),
    ):
        try:
            info = path.lstat()
        except OSError as exc:
            raise SourceBundleError(f"source bundle artifact is missing: {path.name}") from exc
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= maximum:
            raise SourceBundleError(f"source bundle artifact is unsafe: {path.name}")
    archive_data = archive_path.read_bytes()
    manifest_data = manifest_path.read_bytes()
    verifier_data = verifier_path.read_bytes()
    notice_data = notice_path.read_bytes()
    try:
        manifest = json.loads(manifest_data)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SourceBundleError("source bundle manifest is invalid") from exc
    expected_components = [asdict(component) for component in COMPONENTS]
    raw_components = manifest.get("components") if isinstance(manifest, dict) else None
    component_fields = set(expected_components[0]) | {"tree"}
    if (
        not isinstance(manifest, dict)
        or manifest.get("schemaVersion") != SCHEMA_VERSION
        or manifest.get("kind") != "echo-appliance-python-runtime-source-bundle"
        or not isinstance(raw_components, list)
        or not all(
            isinstance(record, dict)
            and set(record) == component_fields
            and isinstance(record.get("tree"), str)
            and COMMIT_PATTERN.fullmatch(record["tree"]) is not None
            for record in raw_components
        )
        or [{key: record[key] for key in expected_components[0]} for record in raw_components]
        != expected_components
    ):
        raise SourceBundleError("source bundle manifest schema or components are invalid")
    archive_record = manifest.get("archive")
    if (
        not isinstance(archive_record, dict)
        or not isinstance(archive_record.get("memberCount"), int)
        or archive_record.get("memberCount", 0) < 3
        or archive_record
        != {
            "file": ARCHIVE_NAME,
            "sha256": _sha256(archive_data),
            "size": len(archive_data),
            "memberCount": archive_record["memberCount"],
        }
    ):
        raise SourceBundleError("source bundle archive identity is invalid")
    if manifest.get("verifier") != {
        "file": VERIFIER_NAME,
        "sha256": _sha256(verifier_data),
    }:
        raise SourceBundleError("source bundle verifier identity is invalid")
    if manifest.get("notice") != {"file": NOTICE_NAME, "sha256": _sha256(notice_data)}:
        raise SourceBundleError("source bundle notice identity is invalid")
    expected_checksum = (
        f"{_sha256(archive_data)}  {ARCHIVE_NAME}\n"
        f"{_sha256(manifest_data)}  {MANIFEST_NAME}\n"
        f"{_sha256(verifier_data)}  {VERIFIER_NAME}\n"
        f"{_sha256(notice_data)}  {NOTICE_NAME}\n"
    ).encode("ascii")
    if checksum_path.read_bytes() != expected_checksum:
        raise SourceBundleError("source bundle checksum file is invalid")

    names: set[str] = set()
    uncompressed_bytes = 0
    embedded_components: bytes | None = None
    embedded_notice: bytes | None = None
    try:
        with tarfile.open(mode="r:gz", fileobj=io.BytesIO(archive_data)) as archive:
            members = archive.getmembers()
            for member in members:
                name = _safe_name(member.name).as_posix()
                if name in names or not name.startswith(f"{ARCHIVE_ROOT}/"):
                    raise SourceBundleError(f"invalid source bundle member: {name}")
                names.add(name)
                if not (member.isfile() or member.isdir() or member.issym()):
                    raise SourceBundleError(f"unsupported source bundle member: {name}")
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise SourceBundleError(f"oversized source bundle member: {name}")
                uncompressed_bytes += member.size
                if uncompressed_bytes > MAX_UNCOMPRESSED_BYTES:
                    raise SourceBundleError("source bundle uncompressed size limit exceeded")
                if member.issym():
                    component_root = "/".join(name.split("/")[:3])
                    if not _link_stays_inside(PurePosixPath(name), member.linkname, component_root):
                        raise SourceBundleError(f"escaping source bundle symlink: {name}")
                if member.isfile() and name in {
                    f"{ARCHIVE_ROOT}/SOURCE_COMPONENTS.json",
                    f"{ARCHIVE_ROOT}/PYTHON_RUNTIME_NOTICES.md",
                }:
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise SourceBundleError(f"cannot read source bundle member: {name}")
                    if name.endswith("SOURCE_COMPONENTS.json"):
                        embedded_components = extracted.read()
                    else:
                        embedded_notice = extracted.read()
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise SourceBundleError("source bundle archive is invalid") from exc
    if len(members) != archive_record.get("memberCount"):
        raise SourceBundleError("source bundle member count is invalid")
    expected_contents = _json_bytes(
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "echo-appliance-python-runtime-source-components",
            "components": manifest["components"],
        }
    )
    if embedded_components != expected_contents or embedded_notice != notice_data:
        raise SourceBundleError("source bundle metadata or notice is invalid")
    for component in COMPONENTS:
        prefix = f"{ARCHIVE_ROOT}/sources/{component.directory}/"
        if not any(name.startswith(prefix) for name in names):
            raise SourceBundleError(f"source bundle is missing {component.directory}")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--output-directory", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = (
            build_bundle(args.output_directory)
            if args.command == "build"
            else verify_bundle(args.output_directory)
        )
    except SourceBundleError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
