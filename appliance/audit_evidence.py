"""Encrypted, externally retained evidence bundles for appliance audit records.

The live journal remains append-only in the appliance state directory.  This
module exports only the journal, signed checkpoint, public key metadata and an
Ed25519 anchor into an authenticated AES-256-GCM envelope.  It never includes
NAS user data or the appliance authentication store.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import re
import secrets
import stat
import struct
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

from appliance.audit import (
    AUDIT_FILENAME,
    AUDIT_KEYRING_FILENAME,
    ApplianceAudit,
    AuditIntegrityError,
    verify_audit_anchor,
)
from appliance.auth import read_auth_store
from appliance.state_lock import StateDirectoryLock, StateLockError

MAGIC = b"ECHO-AUDIT-EVIDENCE-v1\n"
FORMAT_VERSION = 1
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
PASSPHRASE_ENV = "ECHO_AUDIT_EXPORT_PASSPHRASE"  # nosec B105
MANIFEST_NAME = "echo-audit-evidence-manifest.json"
ANCHOR_NAME = "echo-audit-anchor.json"
CHECKPOINT_NAME = f"{AUDIT_FILENAME}.checkpoint"
MANAGED_NAME = re.compile(r"^echo-audit-(\d{8}T\d{6}Z)\.echo-audit$")
_ARTIFACT_NAMES = {AUDIT_FILENAME, CHECKPOINT_NAME, AUDIT_KEYRING_FILENAME}


class AuditEvidenceError(RuntimeError):
    """The requested audit evidence operation was unsafe or unverifiable."""


def _secret(value: bytes | None, environment_name: str = PASSPHRASE_ENV) -> bytes:
    if value is None:
        raw = os.environ.get(environment_name, "")
        value = raw.encode("utf-8")
    if not 12 <= len(value) <= 4096:
        raise AuditEvidenceError("audit export passphrase must contain 12 to 4096 bytes")
    return value


def _derived_key(passphrase: bytes, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _safe_regular_file(path: Path, *, required: bool = True) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if required:
            raise AuditEvidenceError(f"required audit artifact is missing: {path.name}") from None
        return None
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise AuditEvidenceError(f"audit artifact is unsafe: {path.name}")
    if info.st_size > MAX_ARCHIVE_BYTES:
        raise AuditEvidenceError(f"audit artifact is too large: {path.name}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise AuditEvidenceError(f"audit artifact is unreadable: {path.name}") from exc


def _auth_secret(state: Path) -> str:
    auth_path = state / "appliance-auth.json"
    try:
        if auth_path.is_symlink() or not auth_path.is_file():
            raise ValueError("unsafe auth store")
        value = read_auth_store(auth_path).get("jwt_secret")
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AuditEvidenceError("appliance authentication store is unreadable") from exc
    if not isinstance(value, str) or not value:
        raise AuditEvidenceError("appliance authentication store has no signing secret")
    return value


def _archive_payload(state: Path, archive_path: Path) -> dict[str, Any]:
    audit = ApplianceAudit.from_data_dir(state, jwt_secret=_auth_secret(state))
    report = audit.verify()
    if not report.ok:
        raise AuditEvidenceError(report.error or "audit integrity check failed")
    anchor = audit.anchor()
    anchor_bytes = _json_bytes(anchor)
    artifacts: dict[str, bytes] = {}
    for name, required in (
        (AUDIT_FILENAME, report.entries_checked > 0),
        (CHECKPOINT_NAME, report.entries_checked > 0),
        (AUDIT_KEYRING_FILENAME, False),
    ):
        content = _safe_regular_file(state / name, required=required)
        if content is not None:
            artifacts[name] = content

    manifest = {
        "format": FORMAT_VERSION,
        "kind": "echo-appliance-audit-evidence",
        "createdAt": anchor["createdAt"],
        "auditEntries": report.entries_checked,
        "nasUserDataIncluded": False,
        "authenticationStoreIncluded": False,
        "anchorSigningKeyId": anchor["signing"]["keyId"],
        "files": {
            **{
                name: {"sha256": _sha256(content), "bytes": len(content)}
                for name, content in sorted(artifacts.items())
            },
            ANCHOR_NAME: {"sha256": _sha256(anchor_bytes), "bytes": len(anchor_bytes)},
        },
    }
    manifest_bytes = _json_bytes(manifest)
    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for name, content in (
            (MANIFEST_NAME, manifest_bytes),
            (ANCHOR_NAME, anchor_bytes),
            *sorted(artifacts.items()),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o600
            member.mtime = 0
            archive.addfile(member, io.BytesIO(content))
    return _inspect_archive(archive_path)


def _inspect_archive(
    archive_path: Path,
    *,
    expected_signing_key_id: str | None = None,
) -> dict[str, Any]:
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive:
                if (
                    member.name in members
                    or member.name not in {MANIFEST_NAME, ANCHOR_NAME, *_ARTIFACT_NAMES}
                    or not member.isfile()
                    or member.size < 0
                    or member.size > MAX_ARCHIVE_BYTES
                ):
                    raise AuditEvidenceError("audit evidence archive has unsafe entries")
                source = archive.extractfile(member)
                if source is None:
                    raise AuditEvidenceError("audit evidence archive is unreadable")
                members[member.name] = source.read(MAX_ARCHIVE_BYTES + 1)
    except (OSError, tarfile.TarError) as exc:
        raise AuditEvidenceError("audit evidence archive is corrupt") from exc

    try:
        manifest = json.loads(members[MANIFEST_NAME])
        anchor = json.loads(members[ANCHOR_NAME])
        if not isinstance(manifest, dict) or not isinstance(anchor, dict):
            raise ValueError("metadata is not an object")
        if set(manifest) != {
            "format",
            "kind",
            "createdAt",
            "auditEntries",
            "nasUserDataIncluded",
            "authenticationStoreIncluded",
            "anchorSigningKeyId",
            "files",
        }:
            raise ValueError("manifest fields are invalid")
        if (
            manifest["format"] != FORMAT_VERSION
            or manifest["kind"] != "echo-appliance-audit-evidence"
            or manifest["nasUserDataIncluded"] is not False
            or manifest["authenticationStoreIncluded"] is not False
            or not isinstance(manifest["files"], dict)
        ):
            raise ValueError("manifest is incompatible")
        anchor_report = verify_audit_anchor(
            anchor,
            expected_signing_key_id=expected_signing_key_id,
        )
        if (
            manifest["createdAt"] != anchor["createdAt"]
            or manifest["auditEntries"] != anchor_report["entries"]
            or manifest["anchorSigningKeyId"] != anchor_report["signingKeyId"]
        ):
            raise ValueError("manifest does not match anchor")
        declared = manifest["files"]
        if set(declared) != set(members) - {MANIFEST_NAME}:
            raise ValueError("manifest file set is invalid")
        for name, metadata in declared.items():
            content = members[name]
            if (
                not isinstance(metadata, dict)
                or set(metadata) != {"sha256", "bytes"}
                or metadata["sha256"] != _sha256(content)
                or metadata["bytes"] != len(content)
            ):
                raise ValueError("manifest file digest is invalid")
        audit_meta = anchor["audit"]
        expected_hashes = {
            AUDIT_FILENAME: audit_meta["logSha256"],
            CHECKPOINT_NAME: audit_meta["checkpointSha256"],
            AUDIT_KEYRING_FILENAME: audit_meta["keyringSha256"],
        }
        for name, expected_hash in expected_hashes.items():
            actual = _sha256(members[name]) if name in members else None
            if actual != expected_hash:
                raise ValueError("anchor artifact digest is invalid")
        lines = [line for line in members.get(AUDIT_FILENAME, b"").splitlines() if line]
        if len(lines) != anchor_report["entries"]:
            raise ValueError("audit entry count is invalid")
        if lines:
            tail = json.loads(lines[-1])
            if (
                tail.get("seq") != anchor_report["tailSeq"]
                or tail.get("mac") != anchor_report["tailMac"]
                or tail.get("key_id") != audit_meta["tailKeyId"]
            ):
                raise ValueError("audit tail does not match anchor")
    except (
        AuditIntegrityError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeError,
    ) as exc:
        raise AuditEvidenceError("audit evidence verification failed") from exc

    return {
        "ok": True,
        "format": FORMAT_VERSION,
        "createdAt": manifest["createdAt"],
        "entries": anchor_report["entries"],
        "tailSeq": anchor_report["tailSeq"],
        "tailMac": anchor_report["tailMac"],
        "signingKeyId": anchor_report["signingKeyId"],
        "files": sorted(declared),
        "nasUserDataIncluded": False,
        "authenticationStoreIncluded": False,
    }


def _header(*, salt: bytes, nonce: bytes, payload_bytes: int) -> bytes:
    return _json_bytes(
        {
            "format": FORMAT_VERSION,
            "cipher": "AES-256-GCM",
            "kdf": "scrypt",
            "scryptN": 2**15,
            "scryptR": 8,
            "scryptP": 1,
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "payloadBytes": payload_bytes,
        }
    )


def _write_encrypted(archive_path: Path, target: Path, passphrase: bytes) -> None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    header = _header(salt=salt, nonce=nonce, payload_bytes=archive_path.stat().st_size)
    prefix = MAGIC + struct.pack(">I", len(header)) + header
    encryptor = Cipher(algorithms.AES(_derived_key(passphrase, salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(prefix)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise AuditEvidenceError(f"audit evidence destination already exists: {target}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output, archive_path.open("rb") as source:
            output.write(prefix)
            while block := source.read(CHUNK_BYTES):
                output.write(encryptor.update(block))
            output.write(encryptor.finalize())
            output.write(encryptor.tag)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _read_exact(source: BinaryIO, count: int) -> bytes:
    value = source.read(count)
    if len(value) != count:
        raise AuditEvidenceError("audit evidence envelope is truncated")
    return value


def _decrypt(source_path: Path, output_path: Path, passphrase: bytes) -> None:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if source_path.is_symlink() or not source_path.is_file():
        raise AuditEvidenceError(f"audit evidence file is missing or unsafe: {source_path}")
    total = source_path.stat().st_size
    try:
        with source_path.open("rb") as source:
            if _read_exact(source, len(MAGIC)) != MAGIC:
                raise AuditEvidenceError("not an Echo audit evidence bundle")
            header_size = struct.unpack(">I", _read_exact(source, 4))[0]
            if not 1 <= header_size <= MAX_HEADER_BYTES:
                raise AuditEvidenceError("audit evidence envelope header is invalid")
            header_bytes = _read_exact(source, header_size)
            header = json.loads(header_bytes)
            if not isinstance(header, dict):
                raise TypeError("header is not an object")
            salt = base64.b64decode(header["salt"], validate=True)
            nonce = base64.b64decode(header["nonce"], validate=True)
            payload_bytes = int(header["payloadBytes"])
            if (
                set(header)
                != {
                    "format",
                    "cipher",
                    "kdf",
                    "scryptN",
                    "scryptR",
                    "scryptP",
                    "salt",
                    "nonce",
                    "payloadBytes",
                }
                or header["format"] != FORMAT_VERSION
                or header["cipher"] != "AES-256-GCM"
                or header["kdf"] != "scrypt"
                or header["scryptN"] != 2**15
                or header["scryptR"] != 8
                or header["scryptP"] != 1
                or len(salt) != 16
                or len(nonce) != 12
                or not 1 <= payload_bytes <= MAX_ARCHIVE_BYTES
            ):
                raise AuditEvidenceError("audit evidence envelope parameters are unsupported")
            prefix_size = len(MAGIC) + 4 + header_size
            if total != prefix_size + payload_bytes + TAG_BYTES:
                raise AuditEvidenceError("audit evidence envelope size is invalid")
            source.seek(total - TAG_BYTES)
            tag = _read_exact(source, TAG_BYTES)
            source.seek(prefix_size)
            decryptor = Cipher(
                algorithms.AES(_derived_key(passphrase, salt)), modes.GCM(nonce, tag)
            ).decryptor()
            decryptor.authenticate_additional_data(
                MAGIC + struct.pack(">I", header_size) + header_bytes
            )
            remaining = payload_bytes
            with output_path.open("wb") as output:
                while remaining:
                    block = source.read(min(CHUNK_BYTES, remaining))
                    if not block:
                        raise AuditEvidenceError("audit evidence ciphertext is truncated")
                    remaining -= len(block)
                    output.write(decryptor.update(block))
                output.write(decryptor.finalize())
    except InvalidTag as exc:
        raise AuditEvidenceError(
            "audit evidence authentication failed (wrong passphrase or tampering)"
        ) from exc
    except (binascii.Error, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, AuditEvidenceError):
            raise
        raise AuditEvidenceError("audit evidence envelope is invalid") from exc


def export_evidence(
    state_dir: Path | str,
    destination: Path | str,
    *,
    passphrase: bytes | None = None,
) -> dict[str, Any]:
    state = Path(state_dir).resolve()
    target = Path(destination).resolve(strict=False)
    if not state.is_dir():
        raise AuditEvidenceError(f"state directory does not exist: {state}")
    with contextlib.suppress(ValueError):
        target.relative_to(state)
        raise AuditEvidenceError("audit evidence destination must be outside appliance state")
    try:
        with (
            StateDirectoryLock.acquire(state, exclusive=True),
            tempfile.TemporaryDirectory(prefix="echo-audit-export-") as temporary,
        ):
            archive = Path(temporary) / "audit.tar.gz"
            report = _archive_payload(state, archive)
            _write_encrypted(archive, target, _secret(passphrase))
    except (AuditIntegrityError, StateLockError) as exc:
        raise AuditEvidenceError(str(exc)) from exc
    return {**report, "encrypted": True, "outputBytes": target.stat().st_size}


def verify_evidence(
    evidence_path: Path | str,
    *,
    passphrase: bytes | None = None,
    expected_signing_key_id: str | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="echo-audit-verify-") as temporary:
        archive = Path(temporary) / "audit.tar.gz"
        _decrypt(Path(evidence_path), archive, _secret(passphrase))
        return {
            **_inspect_archive(archive, expected_signing_key_id=expected_signing_key_id),
            "encrypted": True,
        }


def _fsync_directory(path: Path) -> None:
    with contextlib.suppress(OSError):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def prune_evidence_set(
    directory: Path | str,
    *,
    keep_days: int = 365,
    keep_minimum: int = 12,
    passphrase: bytes | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise AuditEvidenceError(f"audit evidence directory is missing or unsafe: {root}")
    if not 30 <= keep_days <= 3650 or not 2 <= keep_minimum <= 1000:
        raise AuditEvidenceError("retention must keep 30-3650 days and at least 2-1000 bundles")
    candidates: list[tuple[dt.datetime, Path, os.stat_result]] = []
    for entry in root.iterdir():
        match = MANAGED_NAME.fullmatch(entry.name)
        if match is None or entry.is_symlink() or not entry.is_file():
            continue
        created = dt.datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC)
        candidates.append((created, entry, entry.stat()))
    candidates.sort(key=lambda item: (item[0], item[1].name))
    if not candidates:
        return {"matched": 0, "deleted": [], "kept": [], "verifiedNewest": None}
    secret = _secret(passphrase)
    newest = candidates[-1][1]
    verify_evidence(newest, passphrase=secret)
    current = now or dt.datetime.now(dt.UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.UTC)
    cutoff = current.astimezone(dt.UTC) - dt.timedelta(days=keep_days)
    protected = {item[1] for item in candidates[-keep_minimum:]}
    deleted: list[str] = []
    kept: list[str] = []
    for created, entry, before in candidates:
        if entry in protected or created >= cutoff:
            kept.append(entry.name)
            continue
        after = entry.lstat()
        if not stat.S_ISREG(after.st_mode) or (after.st_ino, after.st_size, after.st_mtime_ns) != (
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ):
            raise AuditEvidenceError(f"audit evidence changed during retention: {entry.name}")
        entry.unlink()
        deleted.append(entry.name)
    _fsync_directory(root)
    return {
        "matched": len(candidates),
        "deleted": deleted,
        "kept": kept,
        "verifiedNewest": newest.name,
        "keepDays": keep_days,
        "keepMinimum": keep_minimum,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Encrypted Echo appliance audit evidence")
    parser.add_argument("--passphrase-env", default=PASSPHRASE_ENV)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("evidence", type=Path)
    export.add_argument(
        "--state-dir", type=Path, default=Path(os.environ.get("ECHO_DATA_DIR") or "/data")
    )
    verify = commands.add_parser("verify")
    verify.add_argument("evidence", type=Path)
    verify.add_argument("--expected-signing-key-id")
    prune = commands.add_parser("prune")
    prune.add_argument("directory", type=Path)
    prune.add_argument("--keep-days", type=int, default=365)
    prune.add_argument("--keep-minimum", type=int, default=12)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        secret = _secret(None, args.passphrase_env)
        if args.command == "export":
            report = export_evidence(args.state_dir, args.evidence, passphrase=secret)
        elif args.command == "verify":
            report = verify_evidence(
                args.evidence,
                passphrase=secret,
                expected_signing_key_id=args.expected_signing_key_id,
            )
        else:
            report = prune_evidence_set(
                args.directory,
                keep_days=args.keep_days,
                keep_minimum=args.keep_minimum,
                passphrase=secret,
            )
    except AuditEvidenceError as exc:
        print(f"Echo audit evidence failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AuditEvidenceError",
    "export_evidence",
    "main",
    "prune_evidence_set",
    "verify_evidence",
]
