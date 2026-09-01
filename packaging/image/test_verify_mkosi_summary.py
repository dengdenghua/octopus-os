#!/usr/bin/env python3
"""Tests for the resolved mkosi release configuration gate."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("verify-mkosi-summary.py")
SPEC = importlib.util.spec_from_file_location("verify_mkosi_summary", MODULE_PATH)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class MkosiSummaryVerifierTests(unittest.TestCase):
    def paths(self) -> tuple[Path, ...]:
        return tuple(
            Path(f"/release/{name}")
            for name in ("db.key", "db.crt", "pcr.key", "pcr.crt", "factory.key", "trust-tree")
        )

    def document(self) -> dict[str, object]:
        secure_key, secure_cert, pcr_key, pcr_cert, factory_key, trust_tree = self.paths()
        common = {
            "Distribution": "debian",
            "Release": "trixie",
            "Architecture": "x86-64",
            "ImageId": "echo-os",
            "ImageVersion": "0.2.0",
        }
        initrd = {
            **common,
            "Image": "initrd",
            "Format": "cpio",
            "Output": "initrd",
            "MakeInitrd": True,
            "CompressOutput": "zstd",
            "Packages": ["systemd-cryptsetup", "cryptsetup-bin", "kmod", "util-linux"],
            "KernelModulesInclude": ["/dm-crypt.ko", "/dm-verity.ko", "/ext4.ko", "/overlay.ko"],
            "ExtraTrees": [
                {"Source": "/source/crypttab", "Target": "/etc/crypttab"},
                {"Source": "/source/machine-id", "Target": "/usr/lib/echo-os/echo-machine-id"},
                {
                    "Source": "/source/service",
                    "Target": "/usr/lib/systemd/system/echo-machine-state-initrd.service",
                },
                {
                    "Source": "/source/dropin",
                    "Target": "/usr/lib/systemd/system/initrd-root-fs.target.d/echo-machine-state.conf",
                },
            ],
        }
        main = {
            **common,
            "Image": None,
            "Format": "disk",
            "Output": "echo-os_0.2.0",
            "Dependencies": ["initrd"],
            "Bootable": "enabled",
            "Bootloader": "systemd-boot",
            "Firmware": "uefi",
            "UnifiedKernelImages": "enabled",
            "SecureBoot": True,
            "SecureBootAutoEnroll": True,
            "Verity": "enabled",
            "SignExpectedPcr": "enabled",
            "SplitArtifacts": ["uki", "partitions"],
            "Initrds": ["/output/initrd"],
            "KernelCommandLine": ["ro", "systemd.verity_root_options=panic-on-corruption"],
            "SecureBootKey": str(secure_key),
            "SecureBootCertificate": str(secure_cert),
            "VerityKey": str(secure_key),
            "VerityCertificate": str(secure_cert),
            "SignExpectedPcrKey": str(pcr_key),
            "SignExpectedPcrCertificate": str(pcr_cert),
            "Passphrase": str(factory_key),
            "ExtraTrees": [{"Source": str(trust_tree), "Target": None}],
        }
        return {"Images": [initrd, main]}

    def verify(self, document: dict[str, object]) -> None:
        verifier.verify_summary(document, "0.2.0", *self.paths())

    def test_accepts_exact_release_configuration(self) -> None:
        self.verify(self.document())

    def test_rejects_disabled_release_security(self) -> None:
        document = self.document()
        document["Images"][1]["Verity"] = "auto"
        with self.assertRaises(verifier.SummaryError):
            self.verify(document)

    def test_rejects_mutable_root_selector(self) -> None:
        document = self.document()
        document["Images"][1]["KernelCommandLine"].append("root=PARTLABEL=mutable")
        with self.assertRaises(verifier.SummaryError):
            self.verify(document)

    def test_rejects_incomplete_custom_initrd(self) -> None:
        document = self.document()
        document["Images"][0]["KernelModulesInclude"].remove("/overlay.ko")
        with self.assertRaises(verifier.SummaryError):
            self.verify(document)


if __name__ == "__main__":
    unittest.main()
