#!/usr/bin/env python3
"""Fail closed unless mkosi resolved the complete Echo OS release policy."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

MAX_SUMMARY_BYTES = 4 * 1024 * 1024


class SummaryError(ValueError):
    """The resolved mkosi configuration is not a release configuration."""


def require_equal(value: object, expected: object, label: str) -> None:
    if value != expected:
        raise SummaryError(f"{label} mismatch: expected {expected!r}, found {value!r}")


def resolved_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise SummaryError(f"{label} must be a non-empty path")
    return Path(value).resolve(strict=False)


def require_same_path(value: object, expected: Path, label: str) -> None:
    if resolved_path(value, label) != expected.resolve(strict=False):
        raise SummaryError(f"{label} does not use the selected release input")


def require_items(container: object, required: set[str], label: str) -> set[str]:
    if not isinstance(container, list) or not all(isinstance(item, str) for item in container):
        raise SummaryError(f"{label} must be a string list")
    actual = set(container)
    missing = sorted(required - actual)
    if missing:
        raise SummaryError(f"{label} is missing: {', '.join(missing)}")
    return actual


def verify_summary(
    document: dict[str, Any],
    version: str,
    secure_boot_key: Path,
    secure_boot_certificate: Path,
    pcr_policy_key: Path,
    pcr_policy_certificate: Path,
    factory_key: Path,
    trust_tree: Path,
) -> None:
    images = document.get("Images")
    if not isinstance(images, list) or len(images) != 2:
        raise SummaryError("mkosi summary must contain exactly the initrd and main image")
    if not all(isinstance(image, dict) for image in images):
        raise SummaryError("mkosi image summaries must be objects")

    initrd_matches = [image for image in images if image.get("Image") == "initrd"]
    main_matches = [image for image in images if image.get("Image") is None]
    if len(initrd_matches) != 1 or len(main_matches) != 1:
        raise SummaryError("mkosi summary does not identify one initrd and one main image")
    initrd = initrd_matches[0]
    main = main_matches[0]

    for image, label in ((initrd, "initrd"), (main, "main image")):
        require_equal(image.get("Distribution"), "debian", f"{label} distribution")
        require_equal(image.get("Release"), "trixie", f"{label} release")
        require_equal(image.get("Architecture"), "x86-64", f"{label} architecture")
        require_equal(image.get("ImageId"), "echo-os", f"{label} image ID")
        require_equal(image.get("ImageVersion"), version, f"{label} version")

    require_equal(initrd.get("Format"), "cpio", "initrd format")
    require_equal(initrd.get("Output"), "initrd", "initrd output name")
    require_equal(initrd.get("MakeInitrd"), True, "initrd build mode")
    require_equal(initrd.get("CompressOutput"), "zstd", "initrd compression")
    require_items(
        initrd.get("Packages"),
        {"systemd-cryptsetup", "cryptsetup-bin", "kmod", "util-linux"},
        "initrd packages",
    )
    require_items(
        initrd.get("KernelModulesInclude"),
        {"/dm-crypt.ko", "/dm-verity.ko", "/ext4.ko", "/overlay.ko"},
        "initrd kernel modules",
    )
    extra_trees = initrd.get("ExtraTrees")
    if not isinstance(extra_trees, list) or not all(isinstance(item, dict) for item in extra_trees):
        raise SummaryError("initrd extra trees must be objects")
    initrd_targets = {item.get("Target") for item in extra_trees}
    required_targets = {
        "/etc/crypttab",
        "/usr/lib/echo-os/echo-machine-id",
        "/usr/lib/systemd/system/echo-machine-state-initrd.service",
        "/usr/lib/systemd/system/initrd-root-fs.target.d/echo-machine-state.conf",
    }
    missing_targets = sorted(required_targets - initrd_targets)
    if missing_targets:
        raise SummaryError(f"initrd extra trees are missing: {', '.join(missing_targets)}")

    require_equal(main.get("Format"), "disk", "main image format")
    require_equal(main.get("Output"), f"echo-os_{version}", "main image output name")
    require_equal(main.get("Dependencies"), ["initrd"], "main image dependencies")
    require_equal(main.get("Bootable"), "enabled", "main image bootability")
    require_equal(main.get("Bootloader"), "systemd-boot", "main image bootloader")
    require_equal(main.get("Firmware"), "uefi", "main image firmware")
    require_equal(main.get("UnifiedKernelImages"), "enabled", "UKI generation")
    require_equal(main.get("SecureBoot"), True, "Secure Boot")
    require_equal(main.get("SecureBootAutoEnroll"), True, "virtual-machine Secure Boot enrollment")
    require_equal(main.get("Verity"), "enabled", "dm-verity generation")
    require_equal(main.get("SignExpectedPcr"), "enabled", "signed expected PCR policy")
    require_equal(main.get("SplitArtifacts"), ["uki", "partitions"], "split artifacts")

    initrds = main.get("Initrds")
    if not isinstance(initrds, list) or len(initrds) != 1 or Path(initrds[0]).name != "initrd":
        raise SummaryError("main image must consume exactly the named custom initrd output")
    command_line = require_items(
        main.get("KernelCommandLine"),
        {"ro", "systemd.verity_root_options=panic-on-corruption"},
        "kernel command line",
    )
    if "rw" in command_line or any(item.startswith("root=") for item in command_line):
        raise SummaryError("kernel command line contains a mutable root selector")

    require_same_path(main.get("SecureBootKey"), secure_boot_key, "Secure Boot key")
    require_same_path(
        main.get("SecureBootCertificate"), secure_boot_certificate, "Secure Boot certificate"
    )
    require_same_path(main.get("VerityKey"), secure_boot_key, "dm-verity key")
    require_same_path(
        main.get("VerityCertificate"), secure_boot_certificate, "dm-verity certificate"
    )
    require_same_path(main.get("SignExpectedPcrKey"), pcr_policy_key, "PCR policy key")
    require_same_path(
        main.get("SignExpectedPcrCertificate"),
        pcr_policy_certificate,
        "PCR policy certificate",
    )
    require_same_path(main.get("Passphrase"), factory_key, "factory LUKS2 key")

    main_extra_trees = main.get("ExtraTrees")
    if not isinstance(main_extra_trees, list) or not all(
        isinstance(item, dict) for item in main_extra_trees
    ):
        raise SummaryError("main image extra trees must be objects")
    selected_trust_tree = trust_tree.resolve(strict=False)
    if not any(
        item.get("Target") is None
        and resolved_path(item.get("Source"), "main image extra tree") == selected_trust_tree
        for item in main_extra_trees
    ):
        raise SummaryError("main image does not include the selected release trust tree")


def read_summary(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SummaryError("mkosi summary must be a regular file")
    size = path.stat().st_size
    if size <= 0 or size > MAX_SUMMARY_BYTES:
        raise SummaryError("mkosi summary must be 1 byte to 4 MiB")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SummaryError("mkosi summary is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise SummaryError("mkosi summary root must be an object")
    return value


def main() -> int:
    if len(sys.argv) != 9:
        print(
            "usage: verify-mkosi-summary.py SUMMARY VERSION SECURE_BOOT_KEY "
            "SECURE_BOOT_CERT PCR_KEY PCR_CERT FACTORY_KEY TRUST_TREE",
            file=sys.stderr,
        )
        return 2
    try:
        verify_summary(
            read_summary(Path(sys.argv[1])),
            sys.argv[2],
            *(Path(argument) for argument in sys.argv[3:]),
        )
    except SummaryError as error:
        print(f"mkosi release configuration rejected: {error}", file=sys.stderr)
        return 1
    print("ECHO_MKOSI_RELEASE_CONFIG_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
