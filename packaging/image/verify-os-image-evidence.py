#!/usr/bin/env python3
"""Bind the complete Linux image acceptance logs into one bounded manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

SCHEMA = 1
MAX_LOG_BYTES = 32 * 1024 * 1024
MAX_TOTAL_LOG_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_OS_SOURCE_MANIFEST_BYTES = 16 * 1024
MAX_INSTALL_MANIFEST_BYTES = 64 * 1024
MAX_INSTALL_SIGNATURE_BYTES = 1024 * 1024
MAX_INSTALLED_IMAGE_BYTES = 128 * 1024**3
MAX_PUBLIC_TRUST_BYTES = 1024 * 1024
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$")
SOURCE_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_PATTERN = re.compile(
    r"^(?:https://[0-9A-Za-z._-]+(?::[0-9]+)?/[^\s?#]+|"
    r"ssh://(?:[0-9A-Za-z._-]+@)?[0-9A-Za-z._-]+(?::[0-9]+)?/[^\s?#]+|"
    r"git@[0-9A-Za-z._-]+:[^\s?#]+)$"
)


class EvidenceError(RuntimeError):
    pass


def requirements(
    version: str,
    os_commit: str,
    source: str,
    image_source_sha256: str,
    install_manifest_sha256: str,
) -> dict[str, tuple[str, tuple[str, ...]]]:
    escaped_version = re.escape(version)
    escaped_os_commit = re.escape(os_commit)
    escaped_source = re.escape(source)
    escaped_image_source = re.escape(image_source_sha256)
    escaped_install_manifest = re.escape(install_manifest_sha256)
    agent = rf"^ECHO_AGENT_READY source={escaped_source} endpoint=http://127\.0\.0\.1:8000 recovery=[0-9]+[ \t\r]*$"
    return {
        "runner_preflight": (
            "echo-image-runner-preflight.log",
            (
                r"^ECHO_IMAGE_RUNNER_READY arch=x86_64 cpu=(?:[4-9]|[1-9][0-9]+) memory-gib=(?:1[6-9]|[2-9][0-9]+) storage-margin-gib=[0-9]+ kvm=ready loops=(?:[4-9]|[1-9][0-9]+) nbd=(?:[2-9]|[1-9][0-9]+) secure-boot-firmware=[1-9][0-9]*$",
            ),
        ),
        "installer": (
            "echo-installer-install.log",
            (
                rf"^ECHO_INSTALL_BUNDLE_AUTHENTICATED action=plan version={escaped_version} manifest={escaped_install_manifest} source={escaped_image_source}$",
                rf"^ECHO_INSTALL_PLAN_READY target=/dev/nbd[0-9]+ version={escaped_version} source={escaped_image_source}$",
                rf"^ECHO_INSTALL_BUNDLE_AUTHENTICATED action=install version={escaped_version} manifest={escaped_install_manifest} source={escaped_image_source}$",
                r"^ECHO_INSTALL_TARGET_LOCKED target=/dev/nbd[0-9]+ device-id=[0-9]+:[0-9]+ identity=stable$",
                rf"^ECHO_INSTALL_COMPLETE target=/dev/nbd[0-9]+ version={escaped_version} source={escaped_image_source} home=/dev/nbd[0-9]+p10 data=luks2-tpm2-signed-pcr11-recovery$",
            ),
        ),
        "key_lifecycle": (
            "echo-key-lifecycle.log",
            (
                r"^ECHO_KEY_LIFECYCLE_SMOKE_OK output=.+ old=revoked new=verified tpm2=replacement-srk data=preserved$",
            ),
        ),
        "factory_reset_lifecycle": (
            "echo-factory-reset.log",
            (
                r"^ECHO_FACTORY_RESET_SMOKE_OK output=.+ volumes=var,swap,home old-recovery=revoked factory=absent tpm2=offline-srk$",
            ),
        ),
        "recovery_boot": (
            "echo-recovery-boot/echo-os-boot.log",
            (rf"^ECHO_RECOVERY_READY version={escaped_version} os={escaped_os_commit}$",),
        ),
        "replacement_tpm_boot": (
            "echo-replacement-tpm-boot/echo-os-boot.log",
            (
                rf"^ECHO_BOOT_HEALTHY version={escaped_version} os={escaped_os_commit} provider=ewmh-x11 window=0x[0-9A-Fa-f]+ auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready$",
                agent,
            ),
        ),
        "factory_reset_boot": (
            "echo-factory-reset-boot/echo-os-boot.log",
            (
                rf"^ECHO_BOOT_HEALTHY version={escaped_version} os={escaped_os_commit} provider=ewmh-x11 window=0x[0-9A-Fa-f]+ auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready$",
                agent,
            ),
        ),
        "oem_login": (
            "echo-oem-login-boot/echo-os-boot.log",
            (
                r"^ECHO_OEM_PROVISIONED account=echo source=system-credential locale=zh_CN\.UTF-8 keymap=us timezone=Asia/Shanghai$",
                rf"^ECHO_LOGIN_READY version={escaped_version} os={escaped_os_commit} provider=sddm-x11 seat=seat0$",
                agent,
            ),
        ),
        "sddm_accessibility": (
            "echo-sddm-accessibility-boot/echo-os-boot.log",
            (
                r"^ECHO_SDDM_ACCESSIBILITY_ARMED provider=at-spi2 screen-reader=orca trigger=super-alt-s$",
                r"^ECHO_SDDM_ACCESSIBILITY_READY provider=at-spi2 screen-reader=orca trigger=super-alt-s$",
                r"^ECHO_QMP_KEY_SENT chord=super-alt-s$",
                r"^ECHO_SDDM_SCREEN_READER_STARTED provider=orca trigger=super-alt-s$",
            ),
        ),
        "user_backup": (
            "echo-user-backup-boot/echo-restore-transaction.log",
            (
                r"^ECHO_USER_BACKUP_RAW_OK repository=[0-9a-f]{16} snapshot=[0-9a-f]{12} transaction=[0-9a-f]{24} wrong-password=rejected disk-full=rejected corruption=rejected restore=promote,rollback,commit trial-boot=ready confirmation=rejected metadata=acl,xattr,sparse$",
            ),
        ),
        "restore_trial_boot": (
            "echo-user-backup-boot/restore-trial-boot/echo-os-boot.log",
            (
                r"^ECHO_RESTORE_TRANSACTION_READY phase=promoted trial=yes transaction=[0-9a-f]{24}$",
                rf"^ECHO_LOGIN_READY version={escaped_version} os={escaped_os_commit} provider=sddm-x11 seat=seat0$",
                r"^ECHO_DESKTOP_READY provider=ewmh-x11 window=0x[0-9A-Fa-f]+ auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready$",
                agent,
            ),
        ),
        "x11_login": (
            "echo-login-boot/echo-os-boot.log",
            (
                rf"^ECHO_LOGIN_READY version={escaped_version} os={escaped_os_commit} provider=sddm-x11 seat=seat0$",
                r"^ECHO_DESKTOP_READY provider=ewmh-x11 window=0x[0-9A-Fa-f]+ auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready$",
                agent,
            ),
        ),
        "wayland_login": (
            "echo-wayland-login-boot/echo-os-boot.log",
            (
                r"^ECHO_DESKTOP_READY provider=kwin-wayland renderer=ready lock=kscreenlocker auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready$",
                r"^ECHO_NATIVE_APP_IPC_READY session=wayland app=org\.kde\.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed$",
                agent,
            ),
        ),
        "direct_desktop": (
            "echo-os-boot/echo-os-boot.log",
            (
                rf"^ECHO_BOOT_HEALTHY version={escaped_version} os={escaped_os_commit} provider=ewmh-x11 window=0x[0-9A-Fa-f]+ auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready$",
                r"^ECHO_CORE_APPS_SESSION_READY session=x11 cases=directory,http,text,pdf,image,archive,audio,terminal,calculator transports=xdg-open,gio-launch windows=native cleanup=closed fixtures=runtime-and-loopback-only$",
                r"^ECHO_NATIVE_APP_IPC_READY app=org\.kde\.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed$",
                agent,
            ),
        ),
        "agent_recovery": (
            "echo-agent-recovery-boot/echo-os-boot.log",
            (
                rf"^ECHO_AGENT_READY source={escaped_source} endpoint=http://127\.0\.0\.1:8000 recovery=1[ \t\r]*$",
            ),
        ),
    }


def fixture_logs(
    version: str,
    os_commit: str,
    source: str,
    image_source_sha256: str,
    install_manifest_sha256: str,
) -> Mapping[str, str]:
    agent = f"ECHO_AGENT_READY source={source} endpoint=http://127.0.0.1:8000 recovery=0"
    boot = (
        f"ECHO_BOOT_HEALTHY version={version} os={os_commit} "
        "provider=ewmh-x11 window=0x1234 auth=ready power=ready "
        "notifications=ready input=ready clipboard=ready accessibility=ready"
    )
    return {
        "echo-image-runner-preflight.log": (
            "ECHO_IMAGE_RUNNER_READY arch=x86_64 cpu=8 memory-gib=32 "
            "storage-margin-gib=48 kvm=ready loops=8 nbd=4 "
            "secure-boot-firmware=2"
        ),
        "echo-installer-install.log": "\n".join(
            (
                f"ECHO_INSTALL_BUNDLE_AUTHENTICATED action=plan version={version} manifest={install_manifest_sha256} source={image_source_sha256}",
                f"ECHO_INSTALL_PLAN_READY target=/dev/nbd7 version={version} source={image_source_sha256}",
                f"ECHO_INSTALL_BUNDLE_AUTHENTICATED action=install version={version} manifest={install_manifest_sha256} source={image_source_sha256}",
                "ECHO_INSTALL_TARGET_LOCKED target=/dev/nbd7 device-id=43:7 identity=stable",
                f"ECHO_INSTALL_COMPLETE target=/dev/nbd7 version={version} source={image_source_sha256} home=/dev/nbd7p10 data=luks2-tpm2-signed-pcr11-recovery",
            )
        ),
        "echo-key-lifecycle.log": "ECHO_KEY_LIFECYCLE_SMOKE_OK output=/tmp/lifecycle.raw old=revoked new=verified tpm2=replacement-srk data=preserved",
        "echo-factory-reset.log": "ECHO_FACTORY_RESET_SMOKE_OK output=/tmp/reset.raw volumes=var,swap,home old-recovery=revoked factory=absent tpm2=offline-srk",
        "echo-recovery-boot/echo-os-boot.log": f"ECHO_RECOVERY_READY version={version} os={os_commit}",
        "echo-replacement-tpm-boot/echo-os-boot.log": f"{boot}\n{agent}",
        "echo-factory-reset-boot/echo-os-boot.log": f"{boot}\n{agent}",
        "echo-oem-login-boot/echo-os-boot.log": "\n".join(
            (
                "ECHO_OEM_PROVISIONED account=echo source=system-credential locale=zh_CN.UTF-8 keymap=us timezone=Asia/Shanghai",
                f"ECHO_LOGIN_READY version={version} os={os_commit} provider=sddm-x11 seat=seat0",
                agent,
            )
        ),
        "echo-sddm-accessibility-boot/echo-os-boot.log": "\n".join(
            (
                "ECHO_SDDM_ACCESSIBILITY_ARMED provider=at-spi2 screen-reader=orca trigger=super-alt-s",
                "ECHO_SDDM_ACCESSIBILITY_READY provider=at-spi2 screen-reader=orca trigger=super-alt-s",
                "ECHO_QMP_KEY_SENT chord=super-alt-s",
                "ECHO_SDDM_SCREEN_READER_STARTED provider=orca trigger=super-alt-s",
            )
        ),
        "echo-user-backup-boot/echo-restore-transaction.log": "\n".join(
            (
                "ECHO_USER_BACKUP_STAGE_OK repository=0123456789abcdef snapshot=0123456789ab wrong-password=rejected disk-full=rejected corruption=rejected restore=staged metadata=acl,xattr,sparse",
                "ECHO_RESTORE_TRANSACTION_STATUS transaction=111111111111111111111111 phase=planned snapshot=0123456789ab",
                "Echo restore transaction failed: restore promotion confirmation does not match the plan",
                "ECHO_RESTORE_PROMOTED transaction=111111111111111111111111 phase=trial snapshot=0123456789ab old-data=retained",
                "ECHO_RESTORE_ROLLED_BACK transaction=111111111111111111111111 old-data=active trial-agent=retained",
                "ECHO_RESTORE_TRANSACTION_STATUS phase=none",
                "ECHO_RESTORE_TRANSACTION_STATUS transaction=111111111111111111111111 phase=planned snapshot=0123456789ab",
                "ECHO_RESTORE_PROMOTED transaction=111111111111111111111111 phase=trial snapshot=0123456789ab old-data=retained",
                "ECHO_RESTORE_TRANSACTION_READY phase=promoted trial=yes transaction=111111111111111111111111",
                "ECHO_RESTORE_TRANSACTION_STATUS transaction=111111111111111111111111 phase=promoted snapshot=0123456789ab",
                "ECHO_RESTORE_COMMITTED transaction=111111111111111111111111 old-data=deleted staging=retained",
                "ECHO_RESTORE_TRANSACTION_STATUS phase=none",
                "ECHO_USER_BACKUP_RAW_OK repository=0123456789abcdef snapshot=0123456789ab transaction=111111111111111111111111 wrong-password=rejected disk-full=rejected corruption=rejected restore=promote,rollback,commit trial-boot=ready confirmation=rejected metadata=acl,xattr,sparse",
            )
        ),
        "echo-user-backup-boot/restore-trial-boot/echo-os-boot.log": "\n".join(
            (
                "ECHO_RESTORE_TRANSACTION_READY phase=promoted trial=yes transaction=111111111111111111111111",
                f"ECHO_LOGIN_READY version={version} os={os_commit} provider=sddm-x11 seat=seat0",
                "ECHO_DESKTOP_READY provider=ewmh-x11 window=0x5678 auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready",
                agent,
            )
        ),
        "echo-login-boot/echo-os-boot.log": "\n".join(
            (
                f"ECHO_LOGIN_READY version={version} os={os_commit} provider=sddm-x11 seat=seat0",
                "ECHO_DESKTOP_READY provider=ewmh-x11 window=0x1234 auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready",
                agent,
            )
        ),
        "echo-wayland-login-boot/echo-os-boot.log": "\n".join(
            (
                "ECHO_DESKTOP_READY provider=kwin-wayland renderer=ready lock=kscreenlocker auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready",
                "ECHO_NATIVE_APP_IPC_READY session=wayland app=org.kde.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed",
                agent,
            )
        ),
        "echo-os-boot/echo-os-boot.log": "\n".join(
            (
                boot,
                "ECHO_CORE_APPS_SESSION_READY session=x11 cases=directory,http,text,pdf,image,archive,audio,terminal,calculator transports=xdg-open,gio-launch windows=native cleanup=closed fixtures=runtime-and-loopback-only",
                "ECHO_NATIVE_APP_IPC_READY app=org.kde.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed",
                agent,
            )
        ),
        "echo-agent-recovery-boot/echo-os-boot.log": f"ECHO_AGENT_READY source={source} endpoint=http://127.0.0.1:8000 recovery=1",
    }


def read_regular(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise EvidenceError(f"evidence input is unavailable: {path}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise EvidenceError(f"evidence input is unsafe or oversized: {path}")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        verified = os.fstat(descriptor)
        if (
            verified.st_dev != metadata.st_dev
            or verified.st_ino != metadata.st_ino
            or verified.st_size != metadata.st_size
            or verified.st_mtime_ns != metadata.st_mtime_ns
            or verified.st_ctime_ns != metadata.st_ctime_ns
        ):
            raise EvidenceError(f"evidence input changed while reading: {path}")
        if len(value) > maximum:
            raise EvidenceError(f"evidence input is oversized: {path}")
        return value
    finally:
        os.close(descriptor)


def load_agent_source(manifest_path: Path) -> str:
    return str(load_agent_identity(manifest_path)["source_id"])


def load_agent_identity(manifest_path: Path) -> dict[str, str]:
    raw = read_regular(manifest_path, MAX_MANIFEST_BYTES)
    try:
        payload = json.loads(raw)
        source = payload["source"]
        source_id = source["source_id"]
        dirty = source["dirty"]
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeError) as error:
        raise EvidenceError("Agent bundle manifest is invalid") from error
    if (
        dirty is not False
        or not isinstance(source_id, str)
        or not SOURCE_PATTERN.fullmatch(source_id)
    ):
        raise EvidenceError("release evidence requires one clean Agent commit")
    return {
        "source_id": source_id,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }


def load_os_source_identity(manifest_path: Path) -> dict[str, object]:
    raw = read_regular(manifest_path, MAX_OS_SOURCE_MANIFEST_BYTES)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise EvidenceError("OS source identity is malformed") from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "kind",
        "repository",
        "commit",
        "tree",
        "commit_time",
        "source_date_epoch",
        "dirty",
    }:
        raise EvidenceError("OS source identity top-level contract is invalid")
    schema = payload.get("schema")
    repository = payload.get("repository")
    commit = payload.get("commit")
    tree = payload.get("tree")
    commit_time = payload.get("commit_time")
    source_date_epoch = payload.get("source_date_epoch")
    if (
        not isinstance(schema, int)
        or isinstance(schema, bool)
        or schema != 1
        or payload.get("kind") != "echo-os-source-identity"
        or not isinstance(repository, str)
        or REPOSITORY_PATTERN.fullmatch(repository) is None
        or not isinstance(commit, str)
        or SOURCE_PATTERN.fullmatch(commit) is None
        or not isinstance(tree, str)
        or SOURCE_PATTERN.fullmatch(tree) is None
        or not isinstance(commit_time, str)
        or not 1 <= len(commit_time) <= 64
        or not isinstance(source_date_epoch, int)
        or isinstance(source_date_epoch, bool)
        or not 1 <= source_date_epoch < 2**63
        or payload.get("dirty") is not False
    ):
        raise EvidenceError("OS source identity fields are invalid")
    try:
        parsed_time = datetime.fromisoformat(commit_time)
    except ValueError as error:
        raise EvidenceError("OS source commit time is invalid") from error
    if parsed_time.tzinfo is None or int(parsed_time.timestamp()) != source_date_epoch:
        raise EvidenceError("OS source commit time and epoch do not agree")
    return {
        "repository": repository,
        "commit": commit,
        "tree": tree,
        "commit_time": commit_time,
        "source_date_epoch": source_date_epoch,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }


def hash_public_trust_input(path: Path, description: str) -> dict[str, object]:
    raw = read_regular(path, MAX_PUBLIC_TRUST_BYTES)
    if not raw:
        raise EvidenceError(f"{description} must be non-empty")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def load_install_identity(
    manifest_path: Path,
    signature_path: Path,
    expected_version: str,
) -> dict[str, object]:
    if (
        manifest_path.name != "INSTALL-MANIFEST.json"
        or signature_path.name != "INSTALL-MANIFEST.json.gpg"
        or manifest_path.parent.resolve(strict=True) != signature_path.parent.resolve(strict=True)
    ):
        raise EvidenceError("install manifest and signature paths are not one bundle identity")
    manifest_raw = read_regular(manifest_path, MAX_INSTALL_MANIFEST_BYTES)
    signature_raw = read_regular(signature_path, MAX_INSTALL_SIGNATURE_BYTES)
    if not manifest_raw or not signature_raw:
        raise EvidenceError("install manifest and signature must be non-empty")
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise EvidenceError("install manifest is malformed") from error
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema",
        "product",
        "architecture",
        "version",
        "source",
        "payload",
        "disk",
        "data_protection",
    }:
        raise EvidenceError("install manifest top-level contract is invalid")
    schema = manifest.get("schema")
    if (
        not isinstance(schema, int)
        or isinstance(schema, bool)
        or schema != 3
        or manifest.get("product") != "echo-os"
        or manifest.get("architecture") != "x86-64"
        or manifest.get("version") != expected_version
    ):
        raise EvidenceError("install manifest release identity is invalid")
    os_source = manifest.get("source")
    if not isinstance(os_source, dict) or set(os_source) != {
        "repository",
        "commit",
        "tree",
        "manifest_sha256",
    }:
        raise EvidenceError("install manifest OS source contract is invalid")
    os_source_repository = os_source.get("repository")
    os_source_commit = os_source.get("commit")
    os_source_tree = os_source.get("tree")
    os_source_manifest_sha256 = os_source.get("manifest_sha256")
    if (
        not isinstance(os_source_repository, str)
        or REPOSITORY_PATTERN.fullmatch(os_source_repository) is None
        or not isinstance(os_source_commit, str)
        or SOURCE_PATTERN.fullmatch(os_source_commit) is None
        or not isinstance(os_source_tree, str)
        or SOURCE_PATTERN.fullmatch(os_source_tree) is None
        or not isinstance(os_source_manifest_sha256, str)
        or SHA256_PATTERN.fullmatch(os_source_manifest_sha256) is None
    ):
        raise EvidenceError("install manifest OS source identity is invalid")
    payload = manifest.get("payload")
    if not isinstance(payload, dict) or set(payload) != {
        "filename",
        "compression",
        "sha256",
        "uncompressed_sha256",
        "uncompressed_size",
    }:
        raise EvidenceError("install manifest payload contract is invalid")
    if (
        payload.get("filename") != f"echo-os_{expected_version}.raw.zst"
        or payload.get("compression") != "zstd"
    ):
        raise EvidenceError("install manifest payload identity is invalid")
    compressed_sha256 = payload.get("sha256")
    raw_sha256 = payload.get("uncompressed_sha256")
    raw_size = payload.get("uncompressed_size")
    if (
        not isinstance(compressed_sha256, str)
        or SHA256_PATTERN.fullmatch(compressed_sha256) is None
        or not isinstance(raw_sha256, str)
        or SHA256_PATTERN.fullmatch(raw_sha256) is None
        or not isinstance(raw_size, int)
        or isinstance(raw_size, bool)
        or raw_size <= 0
        or raw_size % 512 != 0
        or raw_size > MAX_INSTALLED_IMAGE_BYTES
    ):
        raise EvidenceError("install manifest payload measurements are invalid")
    disk = manifest.get("disk")
    expected_labels = [
        "echo-esp",
        f"echo-root-{expected_version}",
        f"echo-root-{expected_version}-verity",
        f"echo-root-{expected_version}-verity-sig",
        "_empty",
        "_empty",
        "_empty",
        "echo-var",
        "echo-swap",
        "echo-home",
    ]
    if (
        not isinstance(disk, dict)
        or set(disk) != {"partition_table", "partition_labels"}
        or disk.get("partition_table") != "gpt"
        or disk.get("partition_labels") != expected_labels
    ):
        raise EvidenceError("install manifest disk contract is invalid")
    data_protection = manifest.get("data_protection")
    if not isinstance(data_protection, dict) or set(data_protection) != {
        "scheme",
        "factory_key_filename",
        "factory_key_sha256",
        "encrypted_partitions",
        "tpm2_policy",
    }:
        raise EvidenceError("install manifest data-protection contract is invalid")
    factory_key_sha256 = data_protection.get("factory_key_sha256")
    if (
        data_protection.get("scheme") != "luks2-factory-key"
        or data_protection.get("factory_key_filename") != "FACTORY-DATA-KEY"
        or not isinstance(factory_key_sha256, str)
        or SHA256_PATTERN.fullmatch(factory_key_sha256) is None
        or data_protection.get("encrypted_partitions") != ["echo-var", "echo-swap", "echo-home"]
    ):
        raise EvidenceError("install manifest encrypted-data contract is invalid")
    tpm2_policy = data_protection.get("tpm2_policy")
    if not isinstance(tpm2_policy, dict) or set(tpm2_policy) != {
        "direct_pcrs",
        "signed_pcrs",
        "public_key_sha256",
    }:
        raise EvidenceError("install manifest TPM2 policy contract is invalid")
    pcr_public_key_sha256 = tpm2_policy.get("public_key_sha256")
    if (
        tpm2_policy.get("direct_pcrs") != []
        or tpm2_policy.get("signed_pcrs") != [11]
        or not isinstance(pcr_public_key_sha256, str)
        or SHA256_PATTERN.fullmatch(pcr_public_key_sha256) is None
    ):
        raise EvidenceError("install manifest TPM2 policy identity is invalid")
    return {
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "signature_sha256": hashlib.sha256(signature_raw).hexdigest(),
        "payload_sha256": compressed_sha256,
        "source_raw_sha256": raw_sha256,
        "source_raw_size": raw_size,
        "pcr_policy_public_key_sha256": pcr_public_key_sha256,
        "os_source_repository": os_source_repository,
        "os_source_commit": os_source_commit,
        "os_source_tree": os_source_tree,
        "os_source_manifest_sha256": os_source_manifest_sha256,
    }


def verify_os_source_binding(
    os_source_identity: Mapping[str, object],
    install_identity: Mapping[str, object],
) -> None:
    if (
        install_identity.get("os_source_repository") != os_source_identity.get("repository")
        or install_identity.get("os_source_commit") != os_source_identity.get("commit")
        or install_identity.get("os_source_tree") != os_source_identity.get("tree")
        or install_identity.get("os_source_manifest_sha256")
        != os_source_identity.get("manifest_sha256")
    ):
        raise EvidenceError("authenticated install manifest does not match the OS source identity")


def hash_installed_image(path: Path) -> dict[str, object]:
    if not path.is_absolute():
        raise EvidenceError("installed image path must be absolute")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise EvidenceError("installed image is unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size % 512 != 0
            or metadata.st_size > MAX_INSTALLED_IMAGE_BYTES
        ):
            raise EvidenceError("installed image size or type is invalid")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        verified = os.fstat(descriptor)
        if (
            verified.st_dev != metadata.st_dev
            or verified.st_ino != metadata.st_ino
            or verified.st_size != metadata.st_size
            or verified.st_mtime_ns != metadata.st_mtime_ns
            or verified.st_ctime_ns != metadata.st_ctime_ns
        ):
            raise EvidenceError("installed image changed while it was being hashed")
        return {"sha256": digest.hexdigest(), "size": metadata.st_size}
    finally:
        os.close(descriptor)


def verify_user_backup_flow(text: str) -> tuple[str, str, str]:
    stage = re.findall(
        r"^ECHO_USER_BACKUP_STAGE_OK repository=([0-9a-f]{16}) snapshot=([0-9a-f]{12}) wrong-password=rejected disk-full=rejected corruption=rejected restore=staged metadata=acl,xattr,sparse$",
        text,
        flags=re.MULTILINE,
    )
    plans = re.findall(
        r"^ECHO_RESTORE_TRANSACTION_STATUS transaction=([0-9a-f]{24}) phase=planned snapshot=([0-9a-f]{12})$",
        text,
        flags=re.MULTILINE,
    )
    promoted = re.findall(
        r"^ECHO_RESTORE_PROMOTED transaction=([0-9a-f]{24}) phase=trial snapshot=([0-9a-f]{12}) old-data=retained$",
        text,
        flags=re.MULTILINE,
    )
    rolled_back = re.findall(
        r"^ECHO_RESTORE_ROLLED_BACK transaction=([0-9a-f]{24}) old-data=active trial-agent=retained$",
        text,
        flags=re.MULTILINE,
    )
    trial_ready = re.findall(
        r"^ECHO_RESTORE_TRANSACTION_READY phase=promoted trial=yes transaction=([0-9a-f]{24})$",
        text,
        flags=re.MULTILINE,
    )
    trial_status = re.findall(
        r"^ECHO_RESTORE_TRANSACTION_STATUS transaction=([0-9a-f]{24}) phase=promoted snapshot=([0-9a-f]{12})$",
        text,
        flags=re.MULTILINE,
    )
    committed = re.findall(
        r"^ECHO_RESTORE_COMMITTED transaction=([0-9a-f]{24}) old-data=deleted staging=retained$",
        text,
        flags=re.MULTILINE,
    )
    complete = re.findall(
        r"^ECHO_USER_BACKUP_RAW_OK repository=([0-9a-f]{16}) snapshot=([0-9a-f]{12}) transaction=([0-9a-f]{24}) wrong-password=rejected disk-full=rejected corruption=rejected restore=promote,rollback,commit trial-boot=ready confirmation=rejected metadata=acl,xattr,sparse$",
        text,
        flags=re.MULTILINE,
    )
    wrong_confirmations = len(
        re.findall(
            r"^Echo restore transaction failed: restore promotion confirmation does not match the plan$",
            text,
            flags=re.MULTILINE,
        )
    )
    cleared = len(
        re.findall(
            r"^ECHO_RESTORE_TRANSACTION_STATUS phase=none$",
            text,
            flags=re.MULTILINE,
        )
    )
    if not (
        len(stage) == 1
        and len(plans) == 2
        and len(promoted) == 2
        and len(rolled_back) == 1
        and len(trial_ready) == 1
        and len(trial_status) == 1
        and len(committed) == 1
        and len(complete) == 1
        and wrong_confirmations == 1
        and cleared == 2
    ):
        raise EvidenceError("user-backup evidence does not contain one complete two-branch flow")
    repository_id, snapshot_id = stage[0]
    transaction_id = plans[0][0]
    if any(item != (transaction_id, snapshot_id) for item in plans + promoted):
        raise EvidenceError("restore plan and promotion identities do not agree")
    if trial_status[0] != (transaction_id, snapshot_id):
        raise EvidenceError("trial status does not match the planned restore")
    if rolled_back[0] != transaction_id or trial_ready[0] != transaction_id:
        raise EvidenceError("rollback or trial boot used a different restore transaction")
    if committed[0] != transaction_id:
        raise EvidenceError("commit used a different restore transaction")
    if complete[0] != (repository_id, snapshot_id, transaction_id):
        raise EvidenceError("backup completion marker does not bind the tested restore flow")
    return repository_id, snapshot_id, transaction_id


def verify_logs(
    root: Path,
    version: str,
    os_commit: str,
    source: str,
    image_source_sha256: str,
    install_manifest_sha256: str,
) -> dict[str, dict[str, object]]:
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise EvidenceError("evidence root must be an absolute real directory")
    checks: dict[str, dict[str, object]] = {}
    total_size = 0
    restore_transaction_id: str | None = None
    for role, (relative_name, marker_patterns) in requirements(
        version, os_commit, source, image_source_sha256, install_manifest_sha256
    ).items():
        candidate = root / relative_name
        current = root
        for component in Path(relative_name).parts:
            current /= component
            if current.is_symlink():
                raise EvidenceError(f"evidence path contains a symlink: {relative_name}")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as error:
            raise EvidenceError(f"evidence path escapes its root: {relative_name}") from error
        raw = read_regular(candidate, MAX_LOG_BYTES)
        total_size += len(raw)
        if total_size > MAX_TOTAL_LOG_BYTES:
            raise EvidenceError("combined evidence logs exceed the safety bound")
        if b"\0" in raw:
            raise EvidenceError(f"evidence log contains NUL data: {relative_name}")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise EvidenceError(f"evidence log is not UTF-8: {relative_name}") from error
        # QEMU serial files commonly use CRLF even though host-side lifecycle
        # logs use LF. Preserve and hash the original bytes, but make marker
        # matching independent of the transport's line-ending convention.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if role == "user_backup":
            _, _, restore_transaction_id = verify_user_backup_flow(text)
        if role == "restore_trial_boot":
            trial_transactions = re.findall(
                r"^ECHO_RESTORE_TRANSACTION_READY phase=promoted trial=yes transaction=([0-9a-f]{24})$",
                text,
                flags=re.MULTILINE,
            )
            if trial_transactions != [restore_transaction_id]:
                raise EvidenceError(
                    "promoted trial boot does not match the backup restore transaction"
                )
        matched: list[str] = []
        for index, pattern in enumerate(marker_patterns, start=1):
            count = len(re.findall(pattern, text, flags=re.MULTILINE))
            if count != 1:
                raise EvidenceError(
                    f"{role} marker {index} must appear exactly once, found {count}"
                )
            matched.append(f"marker-{index}")
        checks[role] = {
            "path": relative_name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "markers": matched,
        }
    return checks


def write_manifest(path: Path, payload: dict[str, object]) -> None:
    if not path.is_absolute() or not path.parent.is_dir() or path.is_symlink() or path.exists():
        raise EvidenceError("evidence output must be a new absolute regular path")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise EvidenceError("cannot write the evidence manifest")
            offset += written
        os.fsync(descriptor)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-version", required=True)
    parser.add_argument("--os-source-manifest", type=Path, required=True)
    parser.add_argument("--agent-manifest", type=Path, required=True)
    parser.add_argument("--install-manifest", type=Path, required=True)
    parser.add_argument("--install-signature", type=Path, required=True)
    parser.add_argument("--install-keyring", type=Path, required=True)
    parser.add_argument("--secure-boot-certificate", type=Path, required=True)
    parser.add_argument("--pcr-policy-public-key", type=Path, required=True)
    parser.add_argument("--installed-image", type=Path, required=True)
    parser.add_argument("--logs-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if VERSION_PATTERN.fullmatch(args.image_version) is None:
        print("OS image evidence failed: invalid image version", file=sys.stderr)
        return 1
    try:
        os_source_identity = load_os_source_identity(args.os_source_manifest)
        agent_identity = load_agent_identity(args.agent_manifest)
        source = str(agent_identity["source_id"])
        install_identity = load_install_identity(
            args.install_manifest,
            args.install_signature,
            args.image_version,
        )
        verify_os_source_binding(os_source_identity, install_identity)
        release_trust = {
            "install_keyring": hash_public_trust_input(
                args.install_keyring, "installer public keyring"
            ),
            "secure_boot_certificate": hash_public_trust_input(
                args.secure_boot_certificate, "Secure Boot certificate"
            ),
            "pcr_policy_public_key": hash_public_trust_input(
                args.pcr_policy_public_key, "PCR policy public key"
            ),
        }
        if (
            release_trust["pcr_policy_public_key"]["sha256"]
            != install_identity["pcr_policy_public_key_sha256"]
        ):
            raise EvidenceError(
                "PCR policy public key does not match the authenticated install manifest"
            )
        installed_image = hash_installed_image(args.installed_image)
        if int(installed_image["size"]) < int(install_identity["source_raw_size"]):
            raise EvidenceError("installed disk is smaller than its signed source image")
        if not args.logs_root.is_absolute() or args.logs_root.is_symlink():
            raise EvidenceError("evidence root must be an absolute non-symlink path")
        root = args.logs_root.resolve(strict=True)
        checks = verify_logs(
            root,
            args.image_version,
            str(os_source_identity["commit"]),
            source,
            str(install_identity["source_raw_sha256"]),
            str(install_identity["manifest_sha256"]),
        )
        payload: dict[str, object] = {
            "schema": SCHEMA,
            "image_id": "echo-os",
            "image_version": args.image_version,
            "architecture": "x86-64",
            "os_source": os_source_identity,
            "agent_source_id": source,
            "agent_manifest_sha256": agent_identity["manifest_sha256"],
            "install_bundle": install_identity,
            "release_trust": release_trust,
            "installed_image": installed_image,
            "checks": checks,
        }
        canonical_checks = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        payload["evidence_id"] = hashlib.sha256(canonical_checks).hexdigest()
        write_manifest(args.output, payload)
    except (EvidenceError, OSError, UnicodeError) as error:
        print(f"OS image evidence failed: {error}", file=sys.stderr)
        return 1
    print(
        f"ECHO_OS_IMAGE_EVIDENCE_OK version={args.image_version} "
        f"os={os_source_identity['commit']} agent={source} "
        f"source={install_identity['source_raw_sha256']} "
        f"installed={installed_image['sha256']} checks={len(checks)} "
        f"evidence={payload['evidence_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
