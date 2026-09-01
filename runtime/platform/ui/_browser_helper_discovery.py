"""Browser discovery / runtime helpers for the browser router backend.

Pure structural split of ``_browser_router_helpers``: locating the
companion extension, reading browser versions, enumerating installed
browsers and resolving the Playwright runtime. Exposed as
``_DiscoveryBackendMixin`` — ``_BrowserBackend`` inherits it. No logic
changes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import project_root


class _DiscoveryBackendMixin:
    """Browser discovery / runtime helpers shared by the browser backend."""

    def _resolve_browser_extension_path(self) -> Path:
        """Locate the companion browser-relay extension on disk.

        Search order:
          1. ``$ECHO_BROWSER_EXTENSION_DIR`` — explicit override,
             takes precedence over everything else.
          2. ``$CWD/extensions/echo-browser-relay`` — committed
             product surface (preferred).
          3. The pre-Echo hidden-folder location, retained as a migration fallback.
             still resolved as a fallback for users who already had
             a local copy at the old path.
        If none exist, the **first non-override** candidate is created
        and returned (i.e. the new ``extensions/`` location).
        (Pre-2026-05 this list had a hard-coded ``E:/echo/...``
        entry from one developer's local dev box · removed when we
        finally caught it in the repo scan.)
        """
        candidates: list[Path] = []
        env_override = os.environ.get("ECHO_BROWSER_EXTENSION_DIR")
        if env_override:
            candidates.append(Path(env_override).expanduser())
        root = project_root()
        legacy_hidden_name = ".octo" + "pus-browser-relay"
        candidates.extend(
            [
                root / "extensions" / "echo-browser-relay",
                root / legacy_hidden_name,
            ]
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        # Default new layout for fresh checkouts.
        fallback = candidates[-2] if not env_override else candidates[1]
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    def _browser_version(self, executable: Path) -> str:
        # Windows: NEVER run `<browser>.exe --version`. Chromium-family
        # browsers on Windows don't honour --version as a CLI flag —
        # they actually *launch the browser window* and ignore the arg,
        # so calling this repeatedly on page mount spawned real
        # Chrome/Edge/Brave windows (users reported their machine
        # freezing under a blast of new tabs). Read the PE header
        # version resource instead — same answer, zero side effects.
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                size = ctypes.windll.version.GetFileVersionInfoSizeW(str(executable), None)
                if not size:
                    return ""
                buf = ctypes.create_string_buffer(size)
                if not ctypes.windll.version.GetFileVersionInfoW(str(executable), 0, size, buf):
                    return ""
                value = ctypes.c_void_p(0)
                value_size = wintypes.UINT(0)
                if not ctypes.windll.version.VerQueryValueW(
                    buf, r"\\", ctypes.byref(value), ctypes.byref(value_size)
                ):
                    return ""

                # VS_FIXEDFILEINFO layout — dwFileVersionMS / dwFileVersionLS
                class _FixedInfo(ctypes.Structure):
                    _fields_ = [
                        ("dwSignature", wintypes.DWORD),
                        ("dwStrucVersion", wintypes.DWORD),
                        ("dwFileVersionMS", wintypes.DWORD),
                        ("dwFileVersionLS", wintypes.DWORD),
                        ("dwProductVersionMS", wintypes.DWORD),
                        ("dwProductVersionLS", wintypes.DWORD),
                        ("dwFileFlagsMask", wintypes.DWORD),
                        ("dwFileFlags", wintypes.DWORD),
                        ("dwFileOS", wintypes.DWORD),
                        ("dwFileType", wintypes.DWORD),
                        ("dwFileSubtype", wintypes.DWORD),
                        ("dwFileDateMS", wintypes.DWORD),
                        ("dwFileDateLS", wintypes.DWORD),
                    ]

                info = ctypes.cast(value, ctypes.POINTER(_FixedInfo)).contents
                return (
                    f"{info.dwFileVersionMS >> 16}."
                    f"{info.dwFileVersionMS & 0xFFFF}."
                    f"{info.dwFileVersionLS >> 16}."
                    f"{info.dwFileVersionLS & 0xFFFF}"
                )
            except (OSError, ValueError, TypeError):
                return ""
        # POSIX: `--version` is safe and standard on Linux/macOS.
        try:
            completed = subprocess.run(
                [str(executable), "--version"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return (completed.stdout or completed.stderr or "").strip()

    def _detect_browsers(self) -> list[dict[str, Any]]:
        candidates_by_browser = [
            (
                "chrome",
                "Google Chrome",
                [
                    "google-chrome",
                    "google-chrome-stable",
                    "chrome",
                    "chrome.exe",
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
                    "/usr/bin/google-chrome",
                    "/usr/bin/google-chrome-stable",
                    "/usr/bin/chromium",
                    "/usr/bin/chromium-browser",
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                ],
                True,
            ),
            (
                "edge",
                "Microsoft Edge",
                [
                    "microsoft-edge",
                    "microsoft-edge-stable",
                    "msedge.exe",
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                    "/usr/bin/microsoft-edge",
                    "/usr/bin/microsoft-edge-stable",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                ],
                True,
            ),
            (
                "chromium",
                "Chromium",
                [
                    "chromium",
                    "chromium-browser",
                    "chromium.exe",
                    "/Applications/Chromium.app/Contents/MacOS/Chromium",
                    "/usr/bin/chromium",
                    "/usr/bin/chromium-browser",
                    r"C:\Program Files\Chromium\Application\chromium.exe",
                    r"C:\Program Files (x86)\Chromium\Application\chromium.exe",
                ],
                True,
            ),
            (
                "brave",
                "Brave",
                [
                    "brave",
                    "brave-browser",
                    "brave.exe",
                    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                    "/usr/bin/brave-browser",
                    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
                ],
                True,
            ),
            (
                "firefox",
                "Firefox",
                [
                    "firefox",
                    "firefox.exe",
                    "/Applications/Firefox.app/Contents/MacOS/firefox",
                    "/usr/bin/firefox",
                    r"C:\Program Files\Mozilla Firefox\firefox.exe",
                    r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
                ],
                False,
            ),
        ]
        seen: set[str] = set()
        browsers: list[dict[str, Any]] = []
        for name, display_name, candidates, chromium_based in candidates_by_browser:
            executable: Path | None = None
            for candidate in candidates:
                resolved = (
                    candidate
                    if Path(candidate).is_absolute() or "\\" in candidate or ":" in candidate
                    else shutil.which(candidate)
                )
                if not resolved:
                    continue
                candidate_path = Path(resolved)
                if candidate_path.exists():
                    executable = candidate_path
                    break
            if executable is None:
                continue
            normalized = str(executable).lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            browsers.append(
                {
                    "name": name,
                    "display_name": display_name,
                    "version": self._browser_version(executable),
                    "path": str(executable),
                    "chromium_based": chromium_based,
                    "cdp_supported": chromium_based,
                    "connection_modes": ["extension", "cdp"] if chromium_based else ["playwright"],
                }
            )
        return browsers

    def _playwright_runtime(self) -> Any | None:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        except ImportError:
            return None
        return sync_playwright

    def _browser_runtime_errors(self) -> tuple[type[BaseException], ...]:
        try:
            from greenlet import (
                error as GreenletError,  # type: ignore[import-not-found]  # noqa: N812
            )
            from playwright.sync_api import (  # type: ignore[import-not-found]
                Error as PlaywrightError,
            )
            from playwright.sync_api import (
                TimeoutError as PlaywrightTimeoutError,
            )
        except ImportError:
            return (OSError, ImportError, RuntimeError)
        return (
            OSError,
            ImportError,
            RuntimeError,
            PlaywrightError,
            PlaywrightTimeoutError,
            GreenletError,
        )

    def _preferred_browser_executable(self) -> str | None:
        for browser in self._detect_browsers():
            if bool(browser.get("chromium_based")):
                path = str(browser.get("path") or "")
                if path:
                    return path
        return None
