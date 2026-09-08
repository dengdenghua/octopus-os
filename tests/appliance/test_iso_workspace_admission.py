"""Exercise ISO workspace admission before any download or image mutation."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BUILDER = Path(__file__).resolve().parents[2] / "deploy/provision/build-iso.sh"
RENDERER = Path(__file__).resolve().parents[2] / "deploy/provision/render-release-preseed.sh"
PRESEED = Path(__file__).resolve().parents[2] / "deploy/provision/installer/preseed.cfg"


@unittest.skipUnless(shutil.which("sh"), "requires a POSIX shell")
class ReleasePreseedTest(unittest.TestCase):
    def test_release_policy_removes_online_pkgsel_and_disables_the_mirror(self):
        with tempfile.TemporaryDirectory(prefix="echo-release-preseed-") as tmp:
            output = Path(tmp) / "preseed.cfg"
            subprocess.run(
                ["sh", str(RENDERER), str(PRESEED), str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            rendered = output.read_text(encoding="utf-8")

        self.assertIn("d-i apt-setup/use_mirror boolean false\n", rendered)
        self.assertIn("d-i clock-setup/ntp boolean false\n", rendered)
        self.assertIn("tasksel tasksel/first multiselect\n", rendered)
        self.assertIn("d-i pkgsel/include string\n", rendered)
        self.assertNotIn("d-i pkgsel/include string \\\n", rendered)
        self.assertNotIn("  openssh-server sudo curl", rendered)
        self.assertNotIn("  nginx smartmontools", rendered)


@unittest.skipUnless(
    sys.platform == "linux" and all(shutil.which(t) for t in ("bash", "git", "xorriso", "cpio")),
    "requires Linux ISO build tools",
)
class IsoWorkspaceAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="echo-workspace-test-", dir="/var/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.script = self.repo / "deploy/provision/build-iso.sh"
        self.script.parent.mkdir(parents=True)
        shutil.copyfile(BUILDER, self.script)
        (self.repo / "frontend").mkdir()
        (self.repo / "frontend/package.json").write_text(
            '{\n  "@openai/codex": "0.149.0"\n}\n', encoding="utf-8"
        )
        for args in (
            ["init", "-q"],
            ["add", "."],
            [
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "commit.gpgSign=false",
                "commit",
                "-qm",
                "fixture",
            ],
        ):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()

    def run_builder(self, available, *, default_directory=False):
        # Only capacity is simulated; the real builder, git cleanliness gate,
        # directory allocation, and cleanup trap all run.
        df = self.bin / "df"
        df.write_text(
            "#!/bin/sh\nprintf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n"
            f"test 99999999 0 {available} 1%% /test\\n'\n",
            encoding="utf-8",
        )
        df.chmod(0o755)
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")
        if default_directory:
            env.pop("TMPDIR", None)
        else:
            env["TMPDIR"] = str(self.scratch)
        result = subprocess.run(
            ["bash", str(self.script), "--iso", str(self.root / "absent.iso")],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.scratch.iterdir()), [])
        return result.stderr

    def test_small_workspace_fails_before_reading_iso(self):
        error = self.run_builder(2097152)
        self.assertIn("6 GiB", error)
        self.assertIn(str(self.scratch), error)
        self.assertNotIn("ISO 不存在", error)

    def test_sufficient_workspace_reaches_iso_validation(self):
        self.assertIn("ISO 不存在", self.run_builder(6291456))

    def test_default_workspace_uses_var_tmp(self):
        self.assertIn("/var/tmp/echo-iso.", self.run_builder(1024, default_directory=True))

    def test_invalid_capacity_fails_closed(self):
        self.assertIn("无法确定", self.run_builder("unknown"))


@unittest.skipUnless(
    sys.platform == "linux" and all(shutil.which(t) for t in ("bash", "xorriso", "md5sum")),
    "requires Linux ISO build tools",
)
class IsoMediaChecksumTest(unittest.TestCase):
    def test_manifest_survives_boot_image_rewrite_and_detects_payload_tampering(self):
        with tempfile.TemporaryDirectory(prefix="echo-checksum-test-", dir="/var/tmp") as tmp:
            work = Path(tmp)
            source = work / "iso"
            boot = source / "isolinux"
            boot.mkdir(parents=True)
            # Synthetic boot sectors exercise xorriso's rewriting, not bootability.
            (boot / "isolinux.bin").write_bytes(bytes(2048))
            (boot / "boot.cat").write_bytes(bytes(2048))
            (source / "payload.txt").write_text("original payload\n", encoding="utf-8")
            (source / "md5sum.txt").touch()
            (source / "debian").symlink_to(".", target_is_directory=True)
            builder = BUILDER.read_text(encoding="utf-8")
            start = builder.index('if [ -f "$WORK/iso/md5sum.txt" ]; then')
            end = builder.index("\nfi", start) + len("\nfi")
            subprocess.run(
                ["bash", "-euo", "pipefail", "-c", builder[start:end]],
                env=dict(os.environ, WORK=str(work)),
                check=True,
                capture_output=True,
            )
            manifest = (source / "md5sum.txt").read_text(encoding="utf-8")
            self.assertIn("./payload.txt", manifest)
            self.assertNotIn("isolinux.bin", manifest)
            self.assertNotIn("boot.cat", manifest)
            medium = work / "test.iso"
            subprocess.run(
                [
                    "xorriso",
                    "-as",
                    "mkisofs",
                    "-r",
                    "-o",
                    str(medium),
                    "-b",
                    "isolinux/isolinux.bin",
                    "-c",
                    "isolinux/boot.cat",
                    "-no-emul-boot",
                    "-boot-load-size",
                    "4",
                    "-boot-info-table",
                    str(source),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            extracted = work / "extracted"
            subprocess.run(
                [
                    "xorriso",
                    "-osirrox",
                    "on",
                    "-indev",
                    str(medium),
                    "-extract",
                    "/",
                    str(extracted),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            self.assertNotEqual((extracted / "isolinux/isolinux.bin").read_bytes(), bytes(2048))
            self.assertNotEqual((extracted / "isolinux/boot.cat").read_bytes(), bytes(2048))
            subprocess.run(
                ["md5sum", "--quiet", "-c", "md5sum.txt"],
                cwd=extracted,
                check=True,
                capture_output=True,
            )
            (extracted / "payload.txt").chmod(0o644)
            (extracted / "payload.txt").write_text("tampered\n", encoding="utf-8")
            rejected = subprocess.run(
                ["md5sum", "--quiet", "-c", "md5sum.txt"],
                cwd=extracted,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("payload.txt: FAILED", rejected.stdout)


if __name__ == "__main__":
    unittest.main()
