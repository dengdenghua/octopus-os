#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("verify-ab-update-evidence.py")
SPEC = importlib.util.spec_from_file_location("verify_ab_update_evidence", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AbUpdateEvidenceTests(unittest.TestCase):
    base = "0.2.0"
    update = "0.2.1"
    commit = "d" * 40
    tree = "e" * 40
    agent = "a" * 40

    def source_bytes(self) -> bytes:
        return (
            json.dumps(
                {
                    "schema": 1,
                    "kind": "echo-os-source-identity",
                    "repository": "https://github.com/example/echo-os.git",
                    "commit": self.commit,
                    "tree": self.tree,
                    "commit_time": "2024-01-01T00:00:00+00:00",
                    "source_date_epoch": 1704067200,
                    "dirty": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()

    def update_manifest_bytes(self) -> bytes:
        source_digest = hashlib.sha256(self.source_bytes()).hexdigest()
        return f"{source_digest}  OS-SOURCE-IDENTITY.json\n".encode()

    def update_signature_bytes(self) -> bytes:
        return b"signature"

    def systemd_report_bytes(self) -> bytes:
        unit_digest_characters = ("1", "2", "3", "4")
        self.assertEqual(len(MODULE.OPERATIONS_UNITS), len(unit_digest_characters))
        return (
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "echo.operations-systemd-native-verification",
                    "sourceRevision": self.commit,
                    "os": {"id": "debian", "versionId": "13", "codename": "trixie"},
                    "systemdVersion": "systemd 257 (257.8-1)",
                    "units": {
                        name: {"sha256": unit_digest_characters[index] * 64}
                        for index, name in enumerate(MODULE.OPERATIONS_UNITS)
                    },
                    "verified": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()

    def logs(self) -> dict[str, str]:
        manifest = hashlib.sha256(self.source_bytes()).hexdigest()
        update_manifest = hashlib.sha256(self.update_manifest_bytes()).hexdigest()
        update_signature = hashlib.sha256(self.update_signature_bytes()).hexdigest()
        agent = f"ECHO_AGENT_READY source={self.agent} endpoint=http://127.0.0.1:8000 recovery=0"

        def boot(version: str) -> str:
            return (
                f"ECHO_BOOT_HEALTHY version={version} os={self.commit} "
                "provider=ewmh-x11 window=0x1234 auth=ready power=ready "
                "notifications=ready input=ready clipboard=ready accessibility=ready"
            )

        return {
            "echo-update-interrupted.log": "\n".join(
                (
                    f"ECHO_UPDATE_BUNDLE_AUTHENTICATED version={self.update} os={self.commit} tree={self.tree} source-manifest={manifest} manifest={update_manifest} signature={update_signature}",
                    f"ECHO_UPDATE_CANDIDATE_READY version={self.update} source=authenticated-bundle",
                    f"ECHO_UPDATE_INTERRUPTION_TRIGGERED sample=inactive-root-first-65536 signal=SIGKILL before={'1' * 64} after={'2' * 64}",
                    "ECHO_UPDATE_INTERRUPTION_OBSERVED result=signal-9",
                    f"ECHO_UPDATE_INTERRUPTION_CONFIRMED version={self.update} inactive-root=changed labels=unpublished uki=unpublished applied-marker=absent",
                    f"ECHO_UPDATE_INTERRUPTION_RECOVERED version={self.update} result=flushed-and-applied",
                )
            ),
            "interrupted-base-boot/echo-os-boot.log": f"{boot(self.base)}\n{agent}",
            "echo-update-esp-full.log": "\n".join(
                (
                    "ECHO_UPDATE_ESP_EXHAUSTED fillers=4 filler-bytes=67108864 target=esp",
                    f"ECHO_UPDATE_BUNDLE_AUTHENTICATED version={self.update} os={self.commit} tree={self.tree} source-manifest={manifest} manifest={update_manifest} signature={update_signature}",
                    f"ECHO_UPDATE_CANDIDATE_READY version={self.update} source=authenticated-bundle",
                    "Failed to write UKI: No space left on device",
                    f"ECHO_UPDATE_ESP_FULL_CONFIRMED version={self.update} labels=unpublished uki=unpublished applied-marker=absent old-boot-entry=present",
                    f"ECHO_UPDATE_ESP_FULL_RECOVERED version={self.update} result=fillers-removed-and-applied",
                )
            ),
            "esp-full-base-boot/echo-os-boot.log": f"{boot(self.base)}\n{agent}",
            "echo-update-apply.log": "\n".join(
                (
                    f"ECHO_UPDATE_BUNDLE_AUTHENTICATED version={self.update} os={self.commit} tree={self.tree} source-manifest={manifest} manifest={update_manifest} signature={update_signature}",
                    f"ECHO_UPDATE_CANDIDATE_READY version={self.update} source=authenticated-bundle",
                    f"ECHO_UPDATE_APPLIED version={self.update} os={self.commit} tree={self.tree} source-manifest={manifest} manifest={update_manifest} signature={update_signature} target=inactive-root-uki-last",
                )
            ),
            "echo-ab-update-evidence.log": (
                f"ECHO_AB_UPDATE_RAW_OK base={self.base} update={self.update} "
                f"os={self.commit} tree={self.tree} source-manifest={manifest} "
                f"manifest={update_manifest} signature={update_signature} "
                "interruption=mid-write-no-uki-recovered "
                "esp-space=exhausted-no-uki-recovered "
                "update-boot=healthy corruption=rejected attempts=3 rollback=healthy "
                "state=machine,account,network,region,flatpak"
            ),
            "dm-verity-rejection.log": "veritysetup rejected the verity set",
            "good-boot/echo-os-boot.log": f"{boot(self.update)}\n{agent}",
            "updated-production-login/echo-os-boot.log": (
                f"ECHO_LOGIN_READY version={self.update} os={self.commit} "
                f"provider=sddm-x11 seat=seat0\n{agent}"
            ),
            "failed-boot-1/echo-os-boot.log": "dm-verity stopped boot attempt 1",
            "failed-boot-2/echo-os-boot.log": "dm-verity stopped boot attempt 2",
            "failed-boot-3/echo-os-boot.log": "dm-verity stopped boot attempt 3",
            "rollback-boot/echo-os-boot.log": f"{boot(self.base)}\n{agent}",
            "rollback-production-login/echo-os-boot.log": (
                f"ECHO_LOGIN_READY version={self.base} os={self.commit} "
                f"provider=sddm-x11 seat=seat0\n{agent}"
            ),
        }

    def materialize(self, root: Path) -> dict[str, Path]:
        source = root / "source.json"
        source.write_bytes(self.source_bytes())
        agent = root / "agent.json"
        agent.write_text(
            json.dumps({"source": {"source_id": self.agent, "dirty": False}}),
            encoding="utf-8",
        )
        bundle = root / "bundle"
        bundle.mkdir()
        embedded = bundle / "OS-SOURCE-IDENTITY.json"
        embedded.write_bytes(self.source_bytes())
        (bundle / "SHA256SUMS").write_bytes(self.update_manifest_bytes())
        (bundle / "SHA256SUMS.gpg").write_bytes(self.update_signature_bytes())
        keyring = root / "update-keyring.gpg"
        keyring.write_bytes(b"public-keyring")
        base_image = root / "base.raw"
        base_image.write_bytes(b"base-image")
        runner_preflight = root / "echo-image-runner-preflight.log"
        runner_preflight.write_text(
            "ECHO_IMAGE_RUNNER_READY arch=x86_64 cpu=4 memory-gib=16 "
            "storage-margin-gib=200 kvm=ready loops=4 nbd=2 "
            "secure-boot-firmware=1\n",
            encoding="utf-8",
        )
        systemd_report = root / "operations-systemd.json"
        systemd_report.write_bytes(self.systemd_report_bytes())
        logs = root / "logs"
        logs.mkdir()
        for relative, text in self.logs().items():
            target = logs / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text + "\n", encoding="utf-8")
        return {
            "source": source,
            "agent": agent,
            "bundle": bundle,
            "keyring": keyring,
            "base_image": base_image,
            "runner_preflight": runner_preflight,
            "systemd_report": systemd_report,
            "logs": logs,
        }

    def test_complete_run_is_bound_to_sources_and_fourteen_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = self.materialize(root)
            identity = MODULE.os_source_identity.load_identity(paths["source"])
            checks = MODULE.verify_logs(
                paths["logs"],
                MODULE.requirements(
                    self.base,
                    self.update,
                    self.commit,
                    self.tree,
                    str(identity["manifest_sha256"]),
                    hashlib.sha256(self.update_manifest_bytes()).hexdigest(),
                    hashlib.sha256(self.update_signature_bytes()).hexdigest(),
                    self.agent,
                ),
            )
            self.assertEqual(len(checks), 14)
            bundle = MODULE.load_update_bundle(paths["bundle"], paths["source"])
            self.assertEqual(bundle["source_identity_sha256"], identity["manifest_sha256"])

    def test_wrong_runtime_source_or_false_success_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = self.materialize(root)
            expected = MODULE.requirements(
                self.base,
                self.update,
                self.commit,
                self.tree,
                hashlib.sha256(self.source_bytes()).hexdigest(),
                hashlib.sha256(self.update_manifest_bytes()).hexdigest(),
                hashlib.sha256(self.update_signature_bytes()).hexdigest(),
                self.agent,
            )
            good = paths["logs"] / "good-boot" / "echo-os-boot.log"
            good.write_text(
                good.read_text(encoding="utf-8").replace(self.commit, "f" * 40),
                encoding="utf-8",
            )
            with self.assertRaises(MODULE.AbEvidenceError):
                MODULE.verify_logs(paths["logs"], expected)

            good.write_text(self.logs()["good-boot/echo-os-boot.log"] + "\n", encoding="utf-8")
            failed = paths["logs"] / "failed-boot-2" / "echo-os-boot.log"
            failed.write_text(self.logs()["good-boot/echo-os-boot.log"] + "\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.AbEvidenceError, "forbidden marker"):
                MODULE.verify_logs(paths["logs"], expected)

            failed.write_text(
                self.logs()["failed-boot-2/echo-os-boot.log"] + "\n", encoding="utf-8"
            )
            interrupted = paths["logs"] / "echo-update-interrupted.log"
            interrupted.write_text(
                interrupted.read_text(encoding="utf-8")
                + f"ECHO_UPDATE_APPLIED version={self.update}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MODULE.AbEvidenceError, "interrupted_apply"):
                MODULE.verify_logs(paths["logs"], expected)

            interrupted.write_text(
                self.logs()["echo-update-interrupted.log"] + "\n", encoding="utf-8"
            )
            esp_full = paths["logs"] / "echo-update-esp-full.log"
            esp_full.write_text(
                esp_full.read_text(encoding="utf-8")
                + f"ECHO_UPDATE_APPLIED version={self.update}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MODULE.AbEvidenceError, "esp_full_apply"):
                MODULE.verify_logs(paths["logs"], expected)

    def test_update_bundle_from_another_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = self.materialize(root)
            embedded = paths["bundle"] / "OS-SOURCE-IDENTITY.json"
            embedded.write_bytes(self.source_bytes().replace(self.commit.encode(), b"f" * 40))
            with self.assertRaisesRegex(MODULE.AbEvidenceError, "differs"):
                MODULE.load_update_bundle(paths["bundle"], paths["source"])

    def test_runner_preflight_must_contain_one_exact_ready_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = self.materialize(root)
            runner_preflight = paths["runner_preflight"]
            expected = MODULE.load_runner_preflight(runner_preflight)
            self.assertEqual(expected["size"], runner_preflight.stat().st_size)

            runner_preflight.write_text("ECHO_IMAGE_RUNNER_READY arch=arm64\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.AbEvidenceError, "missing or duplicated"):
                MODULE.load_runner_preflight(runner_preflight)

    def test_operations_systemd_report_is_strict_and_source_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = self.materialize(root)
            report = MODULE.load_operations_systemd_report(paths["systemd_report"], self.commit)
            self.assertTrue(report["verified"])
            self.assertEqual(
                report["reportSha256"],
                hashlib.sha256(self.systemd_report_bytes()).hexdigest(),
            )

            paths["systemd_report"].write_bytes(
                self.systemd_report_bytes().replace(self.commit.encode(), b"f" * 40)
            )
            with self.assertRaisesRegex(MODULE.AbEvidenceError, "identity is invalid"):
                MODULE.load_operations_systemd_report(paths["systemd_report"], self.commit)

    def test_operations_systemd_report_rejects_false_or_incomplete_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = self.materialize(root)
            original = json.loads(self.systemd_report_bytes())
            for mutate, message in (
                (lambda value: value.__setitem__("verified", False), "identity is invalid"),
                (
                    lambda value: value["os"].__setitem__("versionId", "12"),
                    "identity is invalid",
                ),
                (
                    lambda value: value["units"].pop("echo-state-backup.timer"),
                    "unexpected schema",
                ),
                (
                    lambda value: value.__setitem__("schemaVersion", True),
                    "identity is invalid",
                ),
            ):
                value = json.loads(json.dumps(original))
                mutate(value)
                paths["systemd_report"].write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(MODULE.AbEvidenceError, message):
                    MODULE.load_operations_systemd_report(paths["systemd_report"], self.commit)

    def test_cli_writes_one_content_free_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = self.materialize(root)
            output = root / "ab-evidence.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = MODULE.main(
                    (
                        "--base-version",
                        self.base,
                        "--update-version",
                        self.update,
                        "--os-source-manifest",
                        str(paths["source"]),
                        "--agent-manifest",
                        str(paths["agent"]),
                        "--update-bundle",
                        str(paths["bundle"]),
                        "--update-keyring",
                        str(paths["keyring"]),
                        "--base-image",
                        str(paths["base_image"]),
                        "--runner-preflight",
                        str(paths["runner_preflight"]),
                        "--operations-systemd-verification",
                        str(paths["systemd_report"]),
                        "--logs-root",
                        str(paths["logs"]),
                        "--output",
                        str(output),
                    )
                )
            self.assertEqual(result, 0)
            self.assertIn("ECHO_AB_UPDATE_EVIDENCE_OK", stdout.getvalue())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], 3)
            self.assertEqual(payload["evidence_kind"], "echo-os-ab-update")
            self.assertEqual(payload["os_source"]["commit"], self.commit)
            self.assertEqual(payload["agent"]["source_id"], self.agent)
            self.assertEqual(
                payload["runner_preflight"]["sha256"],
                hashlib.sha256(paths["runner_preflight"].read_bytes()).hexdigest(),
            )
            self.assertEqual(len(payload["checks"]), 14)
            self.assertEqual(
                payload["operations_systemd_verification"]["sourceRevision"],
                self.commit,
            )
            self.assertNotIn("ECHO_BOOT_HEALTHY", json.dumps(payload))
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(
                    MODULE.main(
                        (
                            "--base-version",
                            self.base,
                            "--update-version",
                            self.update,
                            "--os-source-manifest",
                            str(paths["source"]),
                            "--agent-manifest",
                            str(paths["agent"]),
                            "--update-bundle",
                            str(paths["bundle"]),
                            "--update-keyring",
                            str(paths["keyring"]),
                            "--base-image",
                            str(paths["base_image"]),
                            "--runner-preflight",
                            str(paths["runner_preflight"]),
                            "--operations-systemd-verification",
                            str(paths["systemd_report"]),
                            "--logs-root",
                            str(paths["logs"]),
                            "--output",
                            str(output),
                        )
                    ),
                    1,
                )


if __name__ == "__main__":
    unittest.main()
