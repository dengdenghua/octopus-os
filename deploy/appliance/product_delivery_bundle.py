#!/usr/bin/env python3
"""Build and reverify one complete offline Echo NAS product-delivery directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from deploy.appliance import physical_acceptance
except ModuleNotFoundError:
    import physical_acceptance

SCHEMA_VERSION = 1
MANIFEST_NAME = "echo-nas-product-delivery-bundle.json"
REPORT_NAME = "echo-nas-product-delivery-release.json"
KEYRING_NAME = "echo-physical-acceptance-keyring.gpg"
CANDIDATE_DIRECTORY = "candidate"
PHYSICAL_DIRECTORY = "physical-evidence"
TOOLS_DIRECTORY = "tools"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
MAX_FILES = 512
MAX_DEPTH = 8
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MODE_BY_SUFFIX = {".py": 0o555, ".sh": 0o555}
TOOL_FILES = {
    "hub_lifecycle_lab.py": "deploy/appliance/hub_lifecycle_lab.py",
    "lan_discovery_functional_lab.py": "deploy/appliance/lan_discovery_functional_lab.py",
    "paperless_functional_lab.py": "deploy/appliance/paperless_functional_lab.py",
    "physical_acceptance.py": "deploy/appliance/physical_acceptance.py",
    "physical_acceptance_capture.py": "deploy/appliance/physical_acceptance_capture.py",
    "product_delivery_bundle.py": "deploy/appliance/product_delivery_bundle.py",
    "release_evidence_index.py": "deploy/appliance/release_evidence_index.py",
    "verify_public_keyring.py": "deploy/installer/verify_public_keyring.py",
}

SignatureVerifier = Callable[[Path, Path, Path], Mapping[str, str]]
CandidateVerifier = Callable[[Path], None]


class ProductDeliveryBundleError(RuntimeError):
    """The final delivery directory is incomplete, unsafe or inconsistent."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductDeliveryBundleError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _safe_relative(relative: str) -> PurePosixPath:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or not pure.parts
        or len(pure.parts) > MAX_DEPTH
        or any(part in {"", ".", ".."} or SAFE_NAME.fullmatch(part) is None for part in pure.parts)
    ):
        raise ProductDeliveryBundleError(f"delivery bundle path is unsafe: {relative}")
    return pure


def _mode_for(path: Path) -> int:
    return MODE_BY_SUFFIX.get(path.suffix.casefold(), 0o444)


def _default_source_root() -> Path:
    script = Path(__file__).resolve()
    repository_root = script.parents[2]
    if (repository_root / TOOL_FILES["product_delivery_bundle.py"]).is_file():
        return repository_root
    return script.parent


def _tool_source(source_root: Path, destination: str, repository_relative: str) -> Path:
    repository_path = source_root / repository_relative
    flat_path = source_root / destination
    if repository_path.is_file() and not repository_path.is_symlink():
        return repository_path
    if flat_path.is_file() and not flat_path.is_symlink():
        return flat_path
    raise ProductDeliveryBundleError(f"offline delivery tool is unavailable: {destination}")


def _open_regular(path: Path, maximum: int, label: str) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductDeliveryBundleError(f"{label} is unavailable or unsafe") from exc
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= maximum:
        os.close(descriptor)
        raise ProductDeliveryBundleError(f"{label} is empty, oversized or unsafe")
    return descriptor, info


def _hash_regular(path: Path, maximum: int, label: str) -> tuple[str, int]:
    descriptor, before = _open_regular(path, maximum, label)
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
            if total > maximum:
                raise ProductDeliveryBundleError(f"{label} exceeds its size limit")
        after = os.fstat(descriptor)
        if (
            total != before.st_size
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise ProductDeliveryBundleError(f"{label} changed while hashing")
        return digest.hexdigest(), total
    finally:
        os.close(descriptor)


def _copy_regular(source: Path, destination: Path, *, mode: int) -> None:
    source_fd, before = _open_regular(source, MAX_FILE_BYTES, f"bundle input {source}")
    destination_fd = -1
    try:
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
        )
        remaining = before.st_size
        while remaining:
            chunk = os.read(source_fd, min(1024 * 1024, remaining))
            if not chunk:
                raise ProductDeliveryBundleError(f"bundle input ended while copying: {source}")
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise ProductDeliveryBundleError("delivery bundle output write failed")
                view = view[written:]
            remaining -= len(chunk)
        after = os.fstat(source_fd)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise ProductDeliveryBundleError(f"bundle input changed while copying: {source}")
        os.fchmod(destination_fd, mode)
        os.fsync(destination_fd)
    finally:
        os.close(source_fd)
        if destination_fd >= 0:
            os.close(destination_fd)


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_absolute() or source.is_symlink() or not source.is_dir():
        raise ProductDeliveryBundleError(
            "delivery bundle input root must be absolute and non-symlink"
        )
    destination.mkdir(mode=0o755)
    file_count = 0
    for current, directories, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative_root = current_path.relative_to(source)
        if len(relative_root.parts) > MAX_DEPTH:
            raise ProductDeliveryBundleError("delivery bundle input exceeds its depth bound")
        directories.sort()
        files.sort()
        for name in [*directories, *files]:
            if SAFE_NAME.fullmatch(name) is None:
                raise ProductDeliveryBundleError(f"delivery bundle input name is unsafe: {name}")
        for name in directories:
            source_directory = current_path / name
            if source_directory.is_symlink() or not source_directory.is_dir():
                raise ProductDeliveryBundleError(
                    "delivery bundle input contains an unsafe directory"
                )
            (destination / relative_root / name).mkdir(mode=0o755)
        for name in files:
            file_count += 1
            if file_count > MAX_FILES:
                raise ProductDeliveryBundleError("delivery bundle input has too many files")
            source_file = current_path / name
            _copy_regular(
                source_file,
                destination / relative_root / name,
                mode=_mode_for(source_file),
            )


def _write_new(path: Path, data: bytes, *, mode: int = 0o444) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ProductDeliveryBundleError("delivery bundle metadata write failed")
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _run_candidate_verifier(candidate: Path) -> None:
    verifier = candidate / "verify-release-candidate-bundle.sh"
    if verifier.is_symlink() or not verifier.is_file() or not os.access(verifier, os.X_OK):
        raise ProductDeliveryBundleError("candidate bundle verifier is unavailable or unsafe")
    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": os.environ.get("TMPDIR") or tempfile.gettempdir(),
    }
    try:
        completed = subprocess.run(  # nosec B603
            [str(verifier.resolve(strict=True))],
            cwd=candidate.resolve(strict=True),
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProductDeliveryBundleError("candidate bundle verifier could not run") from exc
    if (
        completed.returncode != 0
        or "ECHO_DELIVERY_CANDIDATE_OFFLINE_OK" not in completed.stdout
        or len(completed.stdout.encode("utf-8", "replace")) > 1024 * 1024
        or len(completed.stderr.encode("utf-8", "replace")) > 1024 * 1024
    ):
        raise ProductDeliveryBundleError("candidate bundle failed offline replay")


def _inventory(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ProductDeliveryBundleError("delivery bundle contains a symlink")
        relative = path.relative_to(root).as_posix()
        _safe_relative(relative)
        if path.is_dir():
            continue
        if not path.is_file() or relative == MANIFEST_NAME:
            if relative == MANIFEST_NAME and path.is_file():
                continue
            raise ProductDeliveryBundleError("delivery bundle contains a non-regular entry")
        if len(records) >= MAX_FILES:
            raise ProductDeliveryBundleError("delivery bundle contains too many files")
        digest, size = _hash_regular(path, MAX_FILE_BYTES, f"delivery file {relative}")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ProductDeliveryBundleError("delivery bundle exceeds its total size limit")
        mode = stat.S_IMODE(path.stat().st_mode)
        expected_mode = _mode_for(path)
        if mode != expected_mode:
            raise ProductDeliveryBundleError(f"delivery file mode is invalid: {relative}")
        records.append({"path": relative, "sha256": digest, "size": size, "mode": f"{mode:04o}"})
    if not records:
        raise ProductDeliveryBundleError("delivery bundle has no payload files")
    return records


def _manifest(report: Mapping[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.nas-product-delivery-bundle",
        "candidate": report["candidate"],
        "productReportId": report["reportId"],
        "acceptanceKeyringSha256": report["acceptanceKeyringSha256"],
        "acceptanceSignerFingerprint": report["acceptanceSignerFingerprint"],
        "files": records,
        "nasProductDeliveryReady": True,
    }
    payload["bundleId"] = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return payload


def _load_manifest(path: Path) -> dict[str, Any]:
    descriptor, _info = _open_regular(path, MAX_MANIFEST_BYTES, "delivery bundle manifest")
    try:
        raw = bytearray()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            raw.extend(chunk)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(bytes(raw).decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProductDeliveryBundleError("delivery bundle manifest is not strict JSON") from exc
    if not isinstance(value, dict):
        raise ProductDeliveryBundleError("delivery bundle manifest must be an object")
    return value


def _validate_manifest(value: dict[str, Any]) -> list[dict[str, Any]]:
    expected_keys = {
        "schemaVersion",
        "kind",
        "candidate",
        "productReportId",
        "acceptanceKeyringSha256",
        "acceptanceSignerFingerprint",
        "files",
        "nasProductDeliveryReady",
        "bundleId",
    }
    if set(value) != expected_keys:
        raise ProductDeliveryBundleError("delivery bundle manifest has an unexpected schema")
    bundle_id = value["bundleId"]
    unsigned = dict(value)
    del unsigned["bundleId"]
    if (
        value["schemaVersion"] != SCHEMA_VERSION
        or value["kind"] != "echo.nas-product-delivery-bundle"
        or value["nasProductDeliveryReady"] is not True
        or not isinstance(bundle_id, str)
        or bundle_id != hashlib.sha256(_canonical_json(unsigned)).hexdigest()
        or not isinstance(value["productReportId"], str)
        or SHA256.fullmatch(value["productReportId"]) is None
        or not isinstance(value["acceptanceKeyringSha256"], str)
        or SHA256.fullmatch(value["acceptanceKeyringSha256"]) is None
        or not isinstance(value["acceptanceSignerFingerprint"], str)
        or physical_acceptance.OPENPGP_FINGERPRINT.fullmatch(value["acceptanceSignerFingerprint"])
        is None
        or not isinstance(value["candidate"], dict)
        or not isinstance(value["files"], list)
        or not 1 <= len(value["files"]) <= MAX_FILES
    ):
        raise ProductDeliveryBundleError("delivery bundle manifest identity is invalid")
    records: list[dict[str, Any]] = []
    paths: list[str] = []
    for item in value["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size", "mode"}:
            raise ProductDeliveryBundleError("delivery bundle file record is invalid")
        path = item["path"]
        if (
            not isinstance(path, str)
            or path == MANIFEST_NAME
            or not isinstance(item["sha256"], str)
            or SHA256.fullmatch(item["sha256"]) is None
            or not isinstance(item["size"], int)
            or isinstance(item["size"], bool)
            or not 1 <= item["size"] <= MAX_FILE_BYTES
            or item["mode"] not in {"0444", "0555"}
        ):
            raise ProductDeliveryBundleError("delivery bundle file record is invalid")
        _safe_relative(path)
        paths.append(path)
        records.append(dict(item))
    if paths != sorted(paths) or len(set(paths)) != len(paths):
        raise ProductDeliveryBundleError("delivery bundle file inventory is unsorted or duplicated")
    return records


def _read_report(path: Path) -> dict[str, Any]:
    return _load_manifest(path)


def verify(
    bundle: Path,
    *,
    signature_verifier: SignatureVerifier | None = None,
    candidate_verifier: CandidateVerifier | None = None,
) -> dict[str, Any]:
    if bundle.is_symlink() or not bundle.is_dir():
        raise ProductDeliveryBundleError("delivery bundle root is unavailable or unsafe")
    root = bundle.resolve(strict=True)
    manifest_path = root / MANIFEST_NAME
    if stat.S_IMODE(manifest_path.stat().st_mode) != 0o444:
        raise ProductDeliveryBundleError("delivery bundle manifest mode is invalid")
    manifest_digest, _manifest_size = _hash_regular(
        manifest_path, MAX_MANIFEST_BYTES, "delivery bundle manifest"
    )
    manifest = _load_manifest(manifest_path)
    expected_records = _validate_manifest(manifest)
    actual_records = _inventory(root)
    if actual_records != expected_records:
        raise ProductDeliveryBundleError("delivery bundle payload differs from its manifest")
    if root.name != f"echo-nas-product-delivery-{manifest['productReportId'][:16]}":
        raise ProductDeliveryBundleError("delivery bundle directory name is inconsistent")

    candidate_root = root / CANDIDATE_DIRECTORY
    (candidate_verifier or _run_candidate_verifier)(candidate_root)
    if signature_verifier is None:
        signature_verifier = physical_acceptance._verify_physical_signature
    reproduced = physical_acceptance.verify_acceptance(
        candidate_index=candidate_root / "echo-delivery-release-evidence-index.json",
        evidence_root=root / PHYSICAL_DIRECTORY,
        keyring=root / KEYRING_NAME,
        signature_verifier=signature_verifier,
    )
    packaged_report = _read_report(root / REPORT_NAME)
    if reproduced != packaged_report:
        raise ProductDeliveryBundleError("packaged product report differs from physical replay")
    if (
        manifest["candidate"] != reproduced["candidate"]
        or manifest["productReportId"] != reproduced["reportId"]
        or manifest["acceptanceKeyringSha256"] != reproduced["acceptanceKeyringSha256"]
        or manifest["acceptanceSignerFingerprint"] != reproduced["acceptanceSignerFingerprint"]
        or reproduced["nasProductDeliveryReady"] is not True
    ):
        raise ProductDeliveryBundleError("delivery bundle manifest differs from its product report")
    if _inventory(root) != expected_records:
        raise ProductDeliveryBundleError("delivery bundle payload changed during semantic replay")
    final_manifest_digest, _final_manifest_size = _hash_regular(
        manifest_path, MAX_MANIFEST_BYTES, "delivery bundle manifest"
    )
    if final_manifest_digest != manifest_digest:
        raise ProductDeliveryBundleError("delivery bundle manifest changed during verification")
    return {
        "verified": True,
        "bundleId": manifest["bundleId"],
        "productReportId": reproduced["reportId"],
        "candidateIndexId": reproduced["candidate"]["indexId"],
        "acceptanceSignerFingerprint": reproduced["acceptanceSignerFingerprint"],
        "fileCount": len(actual_records),
        "nasProductDeliveryReady": True,
    }


def build(
    *,
    candidate_bundle: Path,
    evidence_root: Path,
    acceptance_keyring: Path,
    source_root: Path,
    output_directory: Path,
    signature_verifier: SignatureVerifier | None = None,
    candidate_verifier: CandidateVerifier | None = None,
) -> dict[str, Any]:
    candidate_verifier = candidate_verifier or _run_candidate_verifier
    signature_verifier = signature_verifier or physical_acceptance._verify_physical_signature
    candidate_verifier(candidate_bundle)
    source_report = physical_acceptance.verify_acceptance(
        candidate_index=candidate_bundle / "echo-delivery-release-evidence-index.json",
        evidence_root=evidence_root,
        keyring=acceptance_keyring,
        signature_verifier=signature_verifier,
    )
    if output_directory.is_symlink() or not output_directory.is_dir():
        raise ProductDeliveryBundleError("delivery output directory is unavailable or unsafe")
    output_parent = output_directory.resolve(strict=True)
    target = output_parent / f"echo-nas-product-delivery-{source_report['reportId'][:16]}"
    try:
        target.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ProductDeliveryBundleError("delivery bundle output already exists") from exc
    try:
        _copy_tree(candidate_bundle, target / CANDIDATE_DIRECTORY)
        _copy_tree(evidence_root, target / PHYSICAL_DIRECTORY)
        _copy_regular(acceptance_keyring, target / KEYRING_NAME, mode=0o444)
        tools = target / TOOLS_DIRECTORY
        tools.mkdir(mode=0o755)
        resolved_source = source_root.resolve(strict=True)
        for destination, source in TOOL_FILES.items():
            _copy_regular(
                _tool_source(resolved_source, destination, source),
                tools / destination,
                mode=0o555,
            )

        candidate_verifier(target / CANDIDATE_DIRECTORY)
        copied_report = physical_acceptance.verify_acceptance(
            candidate_index=(
                target / CANDIDATE_DIRECTORY / "echo-delivery-release-evidence-index.json"
            ),
            evidence_root=target / PHYSICAL_DIRECTORY,
            keyring=target / KEYRING_NAME,
            signature_verifier=signature_verifier,
        )
        if copied_report != source_report:
            raise ProductDeliveryBundleError("copied delivery evidence differs from its source")
        _write_new(
            target / REPORT_NAME,
            json.dumps(copied_report, indent=2, sort_keys=True).encode() + b"\n",
        )
        records = _inventory(target)
        manifest = _manifest(copied_report, records)
        _write_new(
            target / MANIFEST_NAME,
            json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n",
        )
        result = verify(
            target,
            signature_verifier=signature_verifier,
            candidate_verifier=candidate_verifier,
        )
        for directory in sorted(
            (path for path in target.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            directory.chmod(0o555)
        target.chmod(0o555)
        return {**result, "directory": str(target)}
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--candidate-bundle", type=Path, required=True)
    build_parser.add_argument("--evidence-root", type=Path, required=True)
    build_parser.add_argument("--acceptance-keyring", type=Path, required=True)
    build_parser.add_argument("--source-root", type=Path, default=_default_source_root())
    build_parser.add_argument("--output-directory", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("bundle", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "build":
            result = build(
                candidate_bundle=args.candidate_bundle,
                evidence_root=args.evidence_root,
                acceptance_keyring=args.acceptance_keyring,
                source_root=args.source_root,
                output_directory=args.output_directory,
            )
        else:
            result = verify(args.bundle)
    except (
        OSError,
        ProductDeliveryBundleError,
        physical_acceptance.PhysicalAcceptanceError,
    ) as exc:
        print(f"Echo NAS product delivery bundle failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
