#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEALTH = HERE / "echo-core-apps-health"

EXECUTABLES = (
    "usr/bin/python3",
    "usr/bin/stat",
    "usr/bin/desktop-file-validate",
    "usr/lib/echo-os/echo-core-apps-policy.py",
    "usr/bin/xdg-mime",
    "usr/bin/xdg-open",
    "usr/bin/gio",
    "usr/bin/dolphin",
    "usr/bin/konsole",
    "usr/bin/firefox-esr",
    "usr/bin/kate",
    "usr/bin/okular",
    "usr/bin/gwenview",
    "usr/bin/ark",
    "usr/bin/haruna",
    "usr/bin/spectacle",
    "usr/bin/kcalc",
    "usr/bin/7z",
    "usr/bin/bzip2",
    "usr/bin/unar",
    "usr/bin/unzip",
    "usr/bin/zip",
)
DESKTOP_FILES = (
    "usr/share/applications/org.kde.dolphin.desktop",
    "usr/share/applications/org.kde.konsole.desktop",
    "usr/share/applications/firefox-esr.desktop",
    "usr/share/applications/org.kde.kate.desktop",
    "usr/share/applications/org.kde.okular.desktop",
    "usr/share/applications/org.kde.gwenview.desktop",
    "usr/share/applications/org.kde.ark.desktop",
    "usr/share/applications/org.kde.haruna.desktop",
    "usr/share/applications/org.kde.spectacle.desktop",
    "usr/share/applications/org.kde.kcalc.desktop",
)
POLICY_FILES = ("etc/xdg/mimeapps.list",)


class CoreAppsHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        for relative in EXECUTABLES:
            source = """
                #!/bin/bash
                echo "boot health launched a core application or opener" >&2
                exit 99
            """
            if relative == "usr/bin/stat":
                source = """
                    #!/bin/bash
                    [[ "$1" == -Lc && "$2" == '%u:%g:%a' && -n "$3" ]] || exit 9
                    printf '%s\n' "${ECHO_TEST_RUNTIME_METADATA:-0:0:755}"
                """
            elif relative == "usr/bin/python3":
                source = """
                    #!/bin/bash
                    [[ "$#" == 1 && "$1" == */usr/lib/echo-os/echo-core-apps-policy.py ]] || exit 9
                    [[ "${ECHO_TEST_CORE_APPS_POLICY:-yes}" == yes ]]
                """
            elif relative == "usr/bin/desktop-file-validate":
                source = """
                    #!/bin/bash
                    [[ "$#" == 1 && "$1" == */usr/share/applications/*.desktop ]] || exit 9
                    [[ "${ECHO_TEST_DESKTOP_FILES:-yes}" == yes ]]
                """
            elif relative == "usr/lib/echo-os/echo-core-apps-policy.py":
                source = "#!/bin/bash\nexit 99\n"
            self.write(relative, source, executable=True)
        for relative in (*DESKTOP_FILES, *POLICY_FILES):
            self.write(relative, "fixture\n", executable=False)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, source: str, *, executable: bool) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        target.chmod(0o755 if executable else 0o644)
        return target

    def run_health(
        self,
        overrides: dict[str, str] | None = None,
        *,
        sentinel: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "ECHO_CORE_APPS_RUNTIME_ROOT": str(self.root),
        }
        if sentinel:
            environment["ECHO_CORE_APPS_SOURCE_TEST"] = "USE-SOURCE-RUNTIME"
        if overrides:
            environment.update(overrides)
        return subprocess.run(
            [str(HEALTH)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=10,
        )

    def test_complete_runtime_emits_one_bounded_marker_without_launching_apps(self) -> None:
        result = self.run_health()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "ECHO_CORE_APPS_READY files=dolphin terminal=konsole browser=firefox "
            "text=kate documents=okular images=gwenview archives=ark media=haruna "
            "capture=spectacle calculator=kcalc defaults=xdg\n",
        )

    def test_policy_and_desktop_validation_fail_closed(self) -> None:
        cases = (
            ({"ECHO_TEST_CORE_APPS_POLICY": "no"}, "policy"),
            ({"ECHO_TEST_DESKTOP_FILES": "no"}, "desktop entry"),
        )
        for environment, message in cases:
            with self.subTest(environment=environment):
                result = self.run_health(environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_missing_application_or_desktop_entry_fails_closed(self) -> None:
        for relative in (
            "usr/bin/firefox-esr",
            "usr/bin/okular",
            "usr/bin/haruna",
            "usr/bin/spectacle",
            "usr/bin/7z",
            "usr/share/applications/org.kde.kate.desktop",
            "etc/xdg/mimeapps.list",
        ):
            with self.subTest(relative=relative):
                target = self.root / relative
                saved = target.read_bytes()
                target.unlink()
                result = self.run_health()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("missing", result.stderr)
                target.write_bytes(saved)
                target.chmod(0o755 if relative in EXECUTABLES else 0o644)

    def test_mutable_or_non_root_runtime_is_rejected(self) -> None:
        for metadata in ("501:20:755", "0:0:775", "0:0:777"):
            with self.subTest(metadata=metadata):
                result = self.run_health({"ECHO_TEST_RUNTIME_METADATA": metadata})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("immutable root-owned", result.stderr)

    def test_override_requires_explicit_source_test_sentinel(self) -> None:
        result = self.run_health(sentinel=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("source-test sentinel", result.stderr)


if __name__ == "__main__":
    unittest.main()
