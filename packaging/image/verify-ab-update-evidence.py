#!/usr/bin/env python3
"""Bind one complete Echo OS A/B update and rollback run into a manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import os_source_identity

SCHEMA = 3
MAX_LOG_BYTES = 32 * 1024 * 1024
MAX_TOTAL_LOG_BYTES = 256 * 1024 * 1024
MAX_AGENT_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_UPDATE_MANIFEST_BYTES = 64 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
MAX_KEYRING_BYTES = 16 * 1024 * 1024
MAX_BASE_IMAGE_BYTES = 128 * 1024**3
MAX_SYSTEMD_REPORT_BYTES = 1024 * 1024
VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$")
GIT_ID = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
HASH_LINE = re.compile(r"^(?P<digest>[0-9a-f]{64}) [ *](?P<name>[^/\\]+)$")
SYSTEMD_VERSION = re.compile(r"^systemd [0-9]{1,4}(?: [ -~]{0,240})?$")
OPERATIONS_UNITS = (
    "echo-state-backup.service",
    "echo-state-backup.timer",
    "echo-audit-evidence.service",
    "echo-audit-evidence.timer",
)
RUNNER_READY = re.compile(
    r"^ECHO_IMAGE_RUNNER_READY arch=x86_64 "
    r"cpu=(?:[4-9]|[1-9][0-9]+) "
    r"memory-gib=(?:1[6-9]|[2-9][0-9]+) "
    r"storage-margin-gib=[0-9]+ kvm=ready "
    r"loops=(?:[4-9]|[1-9][0-9]+) "
    r"nbd=(?:[2-9]|[1-9][0-9]+) "
    r"secure-boot-firmware=[1-9][0-9]*$",
    re.MULTILINE,
)


class AbEvidenceError(RuntimeError):
    pass


def read_regular(path: Path, maximum: int, label: str) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AbEvidenceError(f"{label} is unavailable") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > maximum:
            raise AbEvidenceError(f"{label} is empty, oversized or unsafe")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise AbEvidenceError(f"{label} changed while reading")
        if len(raw) > maximum:
            raise AbEvidenceError(f"{label} exceeds its size bound")
        return raw
    finally:
        os.close(descriptor)


def hash_large_regular(path: Path, maximum: int, label: str) -> dict[str, object]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AbEvidenceError(f"{label} is unavailable") from error
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > maximum:
            raise AbEvidenceError(f"{label} is empty, oversized or unsafe")
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(4 * 1024 * 1024, remaining))
            if not block:
                raise AbEvidenceError(f"{label} ended before its recorded size")
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise AbEvidenceError(f"{label} grew while hashing")
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise AbEvidenceError(f"{label} changed while hashing")
        return {"size": before.st_size, "sha256": digest.hexdigest()}
    finally:
        os.close(descriptor)


def load_agent(path: Path) -> dict[str, str]:
    raw = read_regular(path, MAX_AGENT_MANIFEST_BYTES, "Agent manifest")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise AbEvidenceError("Agent manifest is malformed") from error
    source = payload.get("source") if isinstance(payload, dict) else None
    source_id = source.get("source_id") if isinstance(source, dict) else None
    if (
        not isinstance(source_id, str)
        or GIT_ID.fullmatch(source_id) is None
        or source.get("dirty") is not False
    ):
        raise AbEvidenceError("Agent manifest does not identify one clean source")
    return {"source_id": source_id, "manifest_sha256": hashlib.sha256(raw).hexdigest()}


def load_update_bundle(bundle_input: Path, os_manifest: Path) -> dict[str, object]:
    if not bundle_input.is_absolute() or bundle_input.is_symlink():
        raise AbEvidenceError("update bundle must be an absolute non-symlink directory")
    bundle = bundle_input.resolve(strict=True)
    if not bundle.is_dir():
        raise AbEvidenceError("update bundle is unavailable")
    manifest = read_regular(bundle / "SHA256SUMS", MAX_UPDATE_MANIFEST_BYTES, "update manifest")
    signature = read_regular(bundle / "SHA256SUMS.gpg", MAX_SIGNATURE_BYTES, "update signature")
    embedded_source = read_regular(
        bundle / "OS-SOURCE-IDENTITY.json",
        os_source_identity.MAX_MANIFEST_BYTES,
        "update OS source identity",
    )
    captured_source = read_regular(
        os_manifest,
        os_source_identity.MAX_MANIFEST_BYTES,
        "captured OS source identity",
    )
    if embedded_source != captured_source:
        raise AbEvidenceError("update bundle source identity differs from the build identity")
    try:
        lines = manifest.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise AbEvidenceError("update manifest is not UTF-8") from error
    source_digest = hashlib.sha256(embedded_source).hexdigest()
    matches = [
        match
        for line in lines
        if (match := HASH_LINE.fullmatch(line)) and match.group("name") == "OS-SOURCE-IDENTITY.json"
    ]
    if len(matches) != 1 or matches[0].group("digest") != source_digest:
        raise AbEvidenceError("update manifest does not bind the captured OS source identity")
    return {
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "signature_sha256": hashlib.sha256(signature).hexdigest(),
        "source_identity_sha256": source_digest,
    }


def load_runner_preflight(path: Path) -> dict[str, object]:
    raw = read_regular(path, MAX_LOG_BYTES, "runner preflight")
    try:
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError as error:
        raise AbEvidenceError("runner preflight is not UTF-8") from error
    if len(RUNNER_READY.findall(text)) != 1:
        raise AbEvidenceError("runner preflight marker is missing or duplicated")
    return {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise AbEvidenceError(f"operations systemd report repeats JSON key: {key}")
        value[key] = item
    return value


def _exact_object(value: object, keys: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AbEvidenceError(f"{label} has an unexpected schema")
    return value


def load_operations_systemd_report(path: Path, expected_source: str) -> dict[str, object]:
    raw = read_regular(path, MAX_SYSTEMD_REPORT_BYTES, "operations systemd report")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise AbEvidenceError("operations systemd report is malformed") from error
    report = _exact_object(
        value,
        {
            "schemaVersion",
            "kind",
            "sourceRevision",
            "os",
            "systemdVersion",
            "units",
            "verified",
        },
        "operations systemd report",
    )
    operating_system = _exact_object(
        report["os"], {"id", "versionId", "codename"}, "operations systemd OS"
    )
    units = _exact_object(report["units"], set(OPERATIONS_UNITS), "operations systemd units")
    for name in OPERATIONS_UNITS:
        record = _exact_object(units[name], {"sha256"}, f"operations systemd unit {name}")
        if not isinstance(record["sha256"], str) or SHA256.fullmatch(record["sha256"]) is None:
            raise AbEvidenceError("operations systemd unit digest is invalid")
    if (
        not isinstance(report["schemaVersion"], int)
        or isinstance(report["schemaVersion"], bool)
        or report["schemaVersion"] != 1
        or report["kind"] != "echo.operations-systemd-native-verification"
        or report["sourceRevision"] != expected_source
        or operating_system != {"id": "debian", "versionId": "13", "codename": "trixie"}
        or not isinstance(report["systemdVersion"], str)
        or SYSTEMD_VERSION.fullmatch(report["systemdVersion"]) is None
        or report["verified"] is not True
    ):
        raise AbEvidenceError("operations systemd report identity is invalid")
    return {**report, "reportSha256": hashlib.sha256(raw).hexdigest()}


def requirements(
    base: str,
    update: str,
    os_commit: str,
    os_tree: str,
    os_manifest_sha256: str,
    update_manifest_sha256: str,
    update_signature_sha256: str,
    agent_source: str,
) -> dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]]:
    base_v = re.escape(base)
    update_v = re.escape(update)
    commit = re.escape(os_commit)
    tree = re.escape(os_tree)
    source_manifest = re.escape(os_manifest_sha256)
    update_manifest = re.escape(update_manifest_sha256)
    update_signature = re.escape(update_signature_sha256)
    agent = re.escape(agent_source)
    boot_update = rf"^ECHO_BOOT_HEALTHY version={update_v} os={commit} provider=ewmh-x11 window=0x[0-9A-Fa-f]+ auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready$"
    boot_base = rf"^ECHO_BOOT_HEALTHY version={base_v} os={commit} provider=ewmh-x11 window=0x[0-9A-Fa-f]+ auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready$"
    agent_ready = rf"^ECHO_AGENT_READY source={agent} endpoint=http://127\.0\.0\.1:8000 recovery=[0-9]+[ \t]*$"
    return {
        "interrupted_apply": (
            "echo-update-interrupted.log",
            (
                rf"^ECHO_UPDATE_BUNDLE_AUTHENTICATED version={update_v} os={commit} tree={tree} source-manifest={source_manifest} manifest={update_manifest} signature={update_signature}$",
                rf"^ECHO_UPDATE_CANDIDATE_READY version={update_v} source=authenticated-bundle$",
                r"^ECHO_UPDATE_INTERRUPTION_TRIGGERED sample=inactive-root-first-[1-9][0-9]* signal=SIGKILL before=[0-9a-f]{64} after=[0-9a-f]{64}$",
                r"^ECHO_UPDATE_INTERRUPTION_OBSERVED result=signal-9$",
                rf"^ECHO_UPDATE_INTERRUPTION_CONFIRMED version={update_v} inactive-root=changed labels=unpublished uki=unpublished applied-marker=absent$",
                rf"^ECHO_UPDATE_INTERRUPTION_RECOVERED version={update_v} result=flushed-and-applied$",
            ),
            (r"^ECHO_UPDATE_APPLIED ",),
        ),
        "interrupted_boot": (
            "interrupted-base-boot/echo-os-boot.log",
            (boot_base, agent_ready),
            (rf"^ECHO_BOOT_HEALTHY version={update_v} ",),
        ),
        "esp_full_apply": (
            "echo-update-esp-full.log",
            (
                r"^ECHO_UPDATE_ESP_EXHAUSTED fillers=[1-9][0-9]* filler-bytes=[1-9][0-9]* target=esp$",
                rf"^ECHO_UPDATE_BUNDLE_AUTHENTICATED version={update_v} os={commit} tree={tree} source-manifest={source_manifest} manifest={update_manifest} signature={update_signature}$",
                rf"^ECHO_UPDATE_CANDIDATE_READY version={update_v} source=authenticated-bundle$",
                r"(?:No space left on device|ENOSPC|Disk full)",
                rf"^ECHO_UPDATE_ESP_FULL_CONFIRMED version={update_v} labels=unpublished uki=unpublished applied-marker=absent old-boot-entry=present$",
                rf"^ECHO_UPDATE_ESP_FULL_RECOVERED version={update_v} result=fillers-removed-and-applied$",
            ),
            (r"^ECHO_UPDATE_APPLIED ",),
        ),
        "esp_full_boot": (
            "esp-full-base-boot/echo-os-boot.log",
            (boot_base, agent_ready),
            (rf"^ECHO_BOOT_HEALTHY version={update_v} ",),
        ),
        "production_apply": (
            "echo-update-apply.log",
            (
                rf"^ECHO_UPDATE_BUNDLE_AUTHENTICATED version={update_v} os={commit} tree={tree} source-manifest={source_manifest} manifest={update_manifest} signature={update_signature}$",
                rf"^ECHO_UPDATE_CANDIDATE_READY version={update_v} source=authenticated-bundle$",
                rf"^ECHO_UPDATE_APPLIED version={update_v} os={commit} tree={tree} source-manifest={source_manifest} manifest={update_manifest} signature={update_signature} target=inactive-root-uki-last$",
            ),
            (),
        ),
        "completion": (
            "echo-ab-update-evidence.log",
            (
                rf"^ECHO_AB_UPDATE_RAW_OK base={base_v} update={update_v} os={commit} tree={tree} source-manifest={source_manifest} manifest={update_manifest} signature={update_signature} interruption=mid-write-no-uki-recovered esp-space=exhausted-no-uki-recovered update-boot=healthy corruption=rejected attempts=3 rollback=healthy state=machine,account,network,region,flatpak$",
            ),
            (),
        ),
        "verity_rejection": (
            "dm-verity-rejection.log",
            (r"veritysetup rejected the verity set",),
            (),
        ),
        "updated_boot": (
            "good-boot/echo-os-boot.log",
            (boot_update, agent_ready),
            (),
        ),
        "updated_login": (
            "updated-production-login/echo-os-boot.log",
            (
                rf"^ECHO_LOGIN_READY version={update_v} os={commit} provider=sddm-x11 seat=seat0$",
                agent_ready,
            ),
            (),
        ),
        "failed_boot_1": (
            "failed-boot-1/echo-os-boot.log",
            (),
            (r"^ECHO_(?:BOOT_HEALTHY|LOGIN_READY|AGENT_READY) ",),
        ),
        "failed_boot_2": (
            "failed-boot-2/echo-os-boot.log",
            (),
            (r"^ECHO_(?:BOOT_HEALTHY|LOGIN_READY|AGENT_READY) ",),
        ),
        "failed_boot_3": (
            "failed-boot-3/echo-os-boot.log",
            (),
            (r"^ECHO_(?:BOOT_HEALTHY|LOGIN_READY|AGENT_READY) ",),
        ),
        "rollback_boot": (
            "rollback-boot/echo-os-boot.log",
            (boot_base, agent_ready),
            (),
        ),
        "rollback_login": (
            "rollback-production-login/echo-os-boot.log",
            (
                rf"^ECHO_LOGIN_READY version={base_v} os={commit} provider=sddm-x11 seat=seat0$",
                agent_ready,
            ),
            (),
        ),
    }


def verify_logs(
    root_input: Path,
    expected: Mapping[str, tuple[str, tuple[str, ...], tuple[str, ...]]],
) -> dict[str, dict[str, object]]:
    if not root_input.is_absolute() or root_input.is_symlink():
        raise AbEvidenceError("A/B evidence root must be an absolute non-symlink directory")
    root = root_input.resolve(strict=True)
    if not root.is_dir():
        raise AbEvidenceError("A/B evidence root is unavailable")
    checks: dict[str, dict[str, object]] = {}
    total = 0
    for role, (relative_name, required, forbidden) in expected.items():
        candidate = root / relative_name
        current = root
        for component in Path(relative_name).parts:
            current /= component
            if current.is_symlink():
                raise AbEvidenceError(f"A/B evidence path contains a symlink: {relative_name}")
        try:
            candidate.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise AbEvidenceError(f"A/B evidence path escapes its root: {relative_name}") from error
        raw = read_regular(candidate, MAX_LOG_BYTES, relative_name)
        total += len(raw)
        if total > MAX_TOTAL_LOG_BYTES:
            raise AbEvidenceError("combined A/B evidence exceeds its size bound")
        try:
            text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeError as error:
            raise AbEvidenceError(f"A/B evidence is not UTF-8: {relative_name}") from error
        for pattern in required:
            if len(re.findall(pattern, text, flags=re.MULTILINE)) != 1:
                raise AbEvidenceError(f"A/B evidence marker is missing or duplicated: {role}")
        for pattern in forbidden:
            if re.search(pattern, text, flags=re.MULTILINE):
                raise AbEvidenceError(f"A/B evidence contains a forbidden marker: {role}")
        checks[role] = {
            "path": relative_name,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    return checks


def write_manifest(path: Path, payload: Mapping[str, object]) -> None:
    if (
        not path.is_absolute()
        or path.name in {"", ".", ".."}
        or path.parent.is_symlink()
        or not path.parent.is_dir()
        or path.exists()
        or path.is_symlink()
    ):
        raise AbEvidenceError("A/B evidence output must be one new absolute file")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > 1024 * 1024:
        raise AbEvidenceError("A/B evidence manifest exceeds 1 MiB")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise AbEvidenceError("cannot write A/B evidence manifest")
            offset += written
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o444)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-version", required=True)
    parser.add_argument("--update-version", required=True)
    parser.add_argument("--os-source-manifest", type=Path, required=True)
    parser.add_argument("--agent-manifest", type=Path, required=True)
    parser.add_argument("--update-bundle", type=Path, required=True)
    parser.add_argument("--update-keyring", type=Path, required=True)
    parser.add_argument("--base-image", type=Path, required=True)
    parser.add_argument("--runner-preflight", type=Path, required=True)
    parser.add_argument("--operations-systemd-verification", type=Path, required=True)
    parser.add_argument("--logs-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if (
        VERSION.fullmatch(args.base_version) is None
        or VERSION.fullmatch(args.update_version) is None
        or args.base_version == args.update_version
    ):
        print("A/B evidence failed: invalid or equal image versions", file=sys.stderr)
        return 1
    try:
        os_source = os_source_identity.load_identity(args.os_source_manifest)
        agent = load_agent(args.agent_manifest)
        update_bundle = load_update_bundle(args.update_bundle, args.os_source_manifest)
        keyring = hash_large_regular(args.update_keyring, MAX_KEYRING_BYTES, "update keyring")
        base_image = hash_large_regular(args.base_image, MAX_BASE_IMAGE_BYTES, "base image")
        runner_preflight = load_runner_preflight(args.runner_preflight)
        operations_systemd = load_operations_systemd_report(
            args.operations_systemd_verification,
            str(os_source["commit"]),
        )
        checks = verify_logs(
            args.logs_root,
            requirements(
                args.base_version,
                args.update_version,
                str(os_source["commit"]),
                str(os_source["tree"]),
                str(os_source["manifest_sha256"]),
                str(update_bundle["manifest_sha256"]),
                str(update_bundle["signature_sha256"]),
                agent["source_id"],
            ),
        )
        payload: dict[str, object] = {
            "schema": SCHEMA,
            "evidence_kind": "echo-os-ab-update",
            "architecture": "x86-64",
            "base_version": args.base_version,
            "update_version": args.update_version,
            "os_source": os_source,
            "agent": agent,
            "base_image": base_image,
            "runner_preflight": runner_preflight,
            "operations_systemd_verification": operations_systemd,
            "update_bundle": update_bundle,
            "update_keyring": keyring,
            "checks": checks,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        payload["evidence_id"] = hashlib.sha256(canonical).hexdigest()
        write_manifest(args.output, payload)
    except (
        AbEvidenceError,
        OSError,
        UnicodeError,
        os_source_identity.SourceIdentityError,
    ) as error:
        print(f"A/B evidence failed: {error}", file=sys.stderr)
        return 1
    print(
        f"ECHO_AB_UPDATE_EVIDENCE_OK base={args.base_version} update={args.update_version} "
        f"os={os_source['commit']} agent={agent['source_id']} checks={len(checks)} "
        f"systemd={operations_systemd['systemdVersion']} evidence={payload['evidence_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
