#!/usr/bin/env python3
"""Reproduce the bundled Codex Windows Rust license reports.

The checked-in reports are release artifacts. This script deliberately works
from a git archive of the exact upstream commit so local source changes cannot
silently alter the dependency graph.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

CODEX_VERSION = "0.149.0"
CODEX_TAG = f"rust-v{CODEX_VERSION}"
CODEX_COMMIT = "758ef40f50c1a458425c7cfbf1eb12cbc07af0b0"
CODEX_CARGO_LOCK_SHA256 = "0c32858e9c47d0acf82735c8620c96840a5381152eec63acad15d1acadb9edad"
RIPGREP_VERSION = "15.2.0"
RIPGREP_TAG = RIPGREP_VERSION
RIPGREP_COMMIT = "e89fff89ac9af12e8d4ce9d5fd07beb408ca730f"
RIPGREP_CARGO_LOCK_SHA256 = "7a7d39cda8a03930e578f1dbb724e055771901842eca239e03b01e19da946a64"
CARGO_ABOUT_VERSION = "0.9.2"
TARGET = "x86_64-pc-windows-msvc"

REPO_ROOT = Path(__file__).resolve().parents[2]
LICENSE_ROOT = REPO_ROOT / "extras/desktop/licenses" / f"codex-{CODEX_VERSION}"
ABOUT_CONFIG = LICENSE_ROOT / "cargo-about.toml"
ABOUT_TEMPLATE = LICENSE_ROOT / "cargo-about.hbs"
RIPGREP_LICENSE_ROOT = REPO_ROOT / "extras/desktop/licenses" / f"ripgrep-{RIPGREP_VERSION}"
RIPGREP_ABOUT_CONFIG = RIPGREP_LICENSE_ROOT / "cargo-about.toml"
RIPGREP_ABOUT_TEMPLATE = RIPGREP_LICENSE_ROOT / "cargo-about.hbs"
RIPGREP_REPORT = "THIRD_PARTY_LICENSES-ripgrep.html"

COMPONENTS = {
    "codex-cli": (
        "codex-rs/cli/Cargo.toml",
        "THIRD_PARTY_LICENSES-codex-cli.html",
        "codex-cli 0.0.0",
    ),
    "code-mode-host": (
        "codex-rs/code-mode-host/Cargo.toml",
        "THIRD_PARTY_LICENSES-code-mode-host.html",
        "codex-code-mode-host 0.0.0",
    ),
    "windows-sandbox": (
        "codex-rs/windows-sandbox-rs/Cargo.toml",
        "THIRD_PARTY_LICENSES-windows-sandbox.html",
        "codex-windows-sandbox 0.0.0",
    ),
}


def _run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"command failed ({args[0]}): {detail}")
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_source(source: Path, *, tag: str, commit: str) -> None:
    tag_commit = _run("git", "rev-parse", f"{tag}^{{commit}}", cwd=source)
    pinned_commit = _run("git", "rev-parse", f"{commit}^{{commit}}", cwd=source)
    if tag_commit != commit or pinned_commit != commit:
        raise RuntimeError(f"{tag} must resolve to the reviewed commit {commit}")


def _extract_pinned_source(
    source: Path,
    destination: Path,
    *,
    commit: str,
    archived_paths: tuple[str, ...] = (),
) -> None:
    archive_path = destination / "codex-source.tar"
    with archive_path.open("wb") as archive_stream:
        result = subprocess.run(
            ["git", "archive", "--format=tar", commit, *archived_paths],
            cwd=source,
            check=False,
            stdout=archive_stream,
            stderr=subprocess.PIPE,
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to archive pinned Codex source: {result.stderr.decode().strip()}"
        )

    extract_root = destination / "source"
    extract_root.mkdir()
    with tarfile.open(archive_path, mode="r:") as archive:
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"unsafe path in Codex source archive: {member.name}")
            if member.issym() or member.islnk():
                link = PurePosixPath(member.linkname)
                if link.is_absolute() or ".." in link.parts:
                    raise RuntimeError(f"unsafe link in Codex source archive: {member.name}")
        try:
            archive.extractall(extract_root, filter="data")
        except TypeError:  # Python 3.11 compatibility after explicit validation.
            archive.extractall(extract_root)


def _normalize_workspace_version(source_root: Path) -> Path:
    cargo_root = source_root / "codex-rs"
    lock_path = cargo_root / "Cargo.lock"
    if _sha256(lock_path) != CODEX_CARGO_LOCK_SHA256:
        raise RuntimeError("pinned Codex Cargo.lock hash does not match the review record")

    manifest_path = cargo_root / "Cargo.toml"
    text = manifest_path.read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^\[workspace\.package\]\n(?P<body>.*?)(?=^\[)",
        text,
    )
    if match is None:
        raise RuntimeError("Codex workspace package section is missing")
    body = match.group("body")
    expected = f'version = "{CODEX_VERSION}"'
    if body.count(expected) != 1 or 'version = "0.0.0"' in body:
        raise RuntimeError("Codex workspace version no longer matches the reviewed tag")
    normalized_body = body.replace(expected, 'version = "0.0.0"', 1)
    normalized = text[: match.start("body")] + normalized_body + text[match.end("body") :]
    manifest_path.write_text(normalized, encoding="utf-8")
    return cargo_root


def _generate_reports(*, source: Path, cargo_about: Path, destination: Path) -> dict[str, Path]:
    _verify_source(source, tag=CODEX_TAG, commit=CODEX_COMMIT)
    version = _run(str(cargo_about), "--version")
    if version != f"cargo-about {CARGO_ABOUT_VERSION}":
        raise RuntimeError(f"cargo-about must be exactly {CARGO_ABOUT_VERSION}; found {version}")

    with tempfile.TemporaryDirectory(prefix="echo-codex-licenses-") as raw_temp:
        temp_root = Path(raw_temp)
        _extract_pinned_source(
            source,
            temp_root,
            commit=CODEX_COMMIT,
            archived_paths=("codex-rs",),
        )
        cargo_root = _normalize_workspace_version(temp_root / "source")
        lock_path = cargo_root / "Cargo.lock"
        generated: dict[str, Path] = {}

        for component, (manifest, filename, expected_package) in COMPONENTS.items():
            output = temp_root / filename
            _run(
                str(cargo_about),
                "generate",
                "--locked",
                "--fail",
                "--target",
                TARGET,
                "--manifest-path",
                str(temp_root / "source" / manifest),
                "-c",
                str(ABOUT_CONFIG),
                "-o",
                str(output),
                str(ABOUT_TEMPLATE),
                cwd=cargo_root,
            )
            if _sha256(lock_path) != CODEX_CARGO_LOCK_SHA256:
                raise RuntimeError(f"cargo-about modified the pinned Cargo.lock for {component}")
            rendered = output.read_text(encoding="utf-8")
            if (
                "OpenAI Codex 0.149.0 Third-Party Licenses" not in rendered
                or expected_package not in rendered
                or len(rendered) < 50_000
            ):
                raise RuntimeError(f"incomplete third-party report for {component}")
            copied = destination / filename
            shutil.copyfile(output, copied)
            generated[component] = copied
        return generated


def _generate_ripgrep_report(*, source: Path, cargo_about: Path, destination: Path) -> Path:
    _verify_source(source, tag=RIPGREP_TAG, commit=RIPGREP_COMMIT)
    version = _run(str(cargo_about), "--version")
    if version != f"cargo-about {CARGO_ABOUT_VERSION}":
        raise RuntimeError(f"cargo-about must be exactly {CARGO_ABOUT_VERSION}; found {version}")

    with tempfile.TemporaryDirectory(prefix="echo-ripgrep-licenses-") as raw_temp:
        temp_root = Path(raw_temp)
        _extract_pinned_source(source, temp_root, commit=RIPGREP_COMMIT)
        source_root = temp_root / "source"
        lock_path = source_root / "Cargo.lock"
        if _sha256(lock_path) != RIPGREP_CARGO_LOCK_SHA256:
            raise RuntimeError("pinned ripgrep Cargo.lock hash does not match the review record")

        output = temp_root / RIPGREP_REPORT
        _run(
            str(cargo_about),
            "generate",
            "--locked",
            "--fail",
            "--target",
            TARGET,
            "--features",
            "pcre2",
            "--manifest-path",
            str(source_root / "Cargo.toml"),
            "-c",
            str(RIPGREP_ABOUT_CONFIG),
            "-o",
            str(output),
            str(RIPGREP_ABOUT_TEMPLATE),
            cwd=source_root,
        )
        if _sha256(lock_path) != RIPGREP_CARGO_LOCK_SHA256:
            raise RuntimeError("cargo-about modified the pinned ripgrep Cargo.lock")
        rendered = output.read_text(encoding="utf-8")
        if (
            "ripgrep 15.2.0 Third-Party Licenses" not in rendered
            or "ripgrep 15.2.0" not in rendered
            or len(rendered) < 20_000
        ):
            raise RuntimeError("incomplete third-party report for ripgrep")
        copied = destination / RIPGREP_REPORT
        shutil.copyfile(output, copied)
        return copied


def _install_file_atomically(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _install_atomically(generated: dict[str, Path], ripgrep_report: Path) -> None:
    for component, (_, filename, _) in COMPONENTS.items():
        _install_file_atomically(generated[component], LICENSE_ROOT / filename)
    _install_file_atomically(ripgrep_report, RIPGREP_LICENSE_ROOT / RIPGREP_REPORT)


def _check_committed(generated: dict[str, Path], ripgrep_report: Path) -> None:
    failures: list[str] = []
    for component, (_, filename, _) in COMPONENTS.items():
        expected = LICENSE_ROOT / filename
        actual = generated[component]
        if not expected.is_file() or expected.read_bytes() != actual.read_bytes():
            failures.append(filename)
    expected_ripgrep = RIPGREP_LICENSE_ROOT / RIPGREP_REPORT
    if (
        not expected_ripgrep.is_file()
        or expected_ripgrep.read_bytes() != ripgrep_report.read_bytes()
    ):
        failures.append(RIPGREP_REPORT)
    if failures:
        raise RuntimeError(
            "bundled Rust third-party license reports are stale: " + ", ".join(failures)
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex-source",
        required=True,
        type=Path,
        help="local clone containing the reviewed rust-v0.149.0 tag",
    )
    parser.add_argument(
        "--cargo-about",
        type=Path,
        default=Path(shutil.which("cargo-about") or "cargo-about"),
        help="path to cargo-about 0.9.2",
    )
    parser.add_argument(
        "--ripgrep-source",
        required=True,
        type=Path,
        help="local clone containing the reviewed ripgrep 15.2.0 tag",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="atomically replace the checked-in reports instead of verifying them",
    )
    args = parser.parse_args()

    if not args.codex_source.is_dir():
        raise SystemExit(f"Codex source directory does not exist: {args.codex_source}")
    if not args.ripgrep_source.is_dir():
        raise SystemExit(f"ripgrep source directory does not exist: {args.ripgrep_source}")
    if not args.cargo_about.is_file():
        raise SystemExit(f"cargo-about executable does not exist: {args.cargo_about}")

    with tempfile.TemporaryDirectory(prefix="echo-codex-license-output-") as raw_output:
        generated = _generate_reports(
            source=args.codex_source.resolve(),
            cargo_about=args.cargo_about.resolve(),
            destination=Path(raw_output),
        )
        ripgrep_report = _generate_ripgrep_report(
            source=args.ripgrep_source.resolve(),
            cargo_about=args.cargo_about.resolve(),
            destination=Path(raw_output),
        )
        if args.write:
            _install_atomically(generated, ripgrep_report)
        else:
            _check_committed(generated, ripgrep_report)

    action = "generated" if args.write else "verified"
    print(
        f"{action} Codex {CODEX_VERSION} and ripgrep {RIPGREP_VERSION} "
        "Windows third-party license reports"
    )


if __name__ == "__main__":
    main()
