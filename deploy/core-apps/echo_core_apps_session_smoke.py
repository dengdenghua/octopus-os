#!/usr/bin/env python3
"""Exercise fixed XDG defaults through real native application windows.

This diagnostic is intentionally opt-in. It creates only fixed, non-user test
fixtures below the private session runtime, opens each fixture through
``xdg-open``, observes the resulting KWin/EWMH window, and closes that exact
window through the same fixed-action providers used by Echo Desktop.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import http.server
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SOURCE_TEST_SENTINEL = "USE-EPHEMERAL-RUNTIME"
MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024
WINDOW_TIMEOUT_SECONDS = 30.0
POLL_SECONDS = 0.1
HTTP_FIXTURE_NAME = "echo-core-browser.html"
HTTP_FIXTURE_BODY = (
    b"<!doctype html><html><head><meta charset=utf-8>"
    b"<title>echo-core-browser.html</title></head>"
    b"<body>Echo OS local browser association smoke</body></html>\n"
)

XDG_MIME = Path("/usr/bin/xdg-mime")
XDG_OPEN = Path("/usr/bin/xdg-open")
GIO = Path("/usr/bin/gio")
DESKTOP_FILE_VALIDATE = Path("/usr/bin/desktop-file-validate")
WMCTRL = Path("/usr/bin/wmctrl")
ZIP = Path("/usr/bin/zip")
KWIN_BRIDGE = Path("/usr/lib/echo-os/echo-kwin-window-bridge")
APPLICATIONS_DIR = Path("/usr/share/applications")


class SessionSmokeError(RuntimeError):
    """The functional core-application session contract was not satisfied."""


def required_session_executables(session: str) -> tuple[Path, ...]:
    common = (XDG_MIME, XDG_OPEN, GIO, DESKTOP_FILE_VALIDATE, ZIP)
    if session == "x11":
        return (*common, WMCTRL)
    if session == "wayland":
        return (*common, KWIN_BRIDGE)
    raise SessionSmokeError("unsupported core-app session type")


@dataclass(frozen=True)
class AppCase:
    name: str
    filename: str
    desktop_id: str
    detected_mimes: tuple[str, ...]
    identity_tokens: tuple[str, ...]
    target_kind: str = "file"
    require_fixture_title: bool = True


CASES = (
    AppCase(
        "directory",
        "echo-core-directory",
        "org.kde.dolphin.desktop",
        ("inode/directory",),
        ("dolphin",),
    ),
    AppCase(
        "http",
        HTTP_FIXTURE_NAME,
        "firefox-esr.desktop",
        ("x-scheme-handler/http",),
        ("firefox", "esr"),
        "loopback-http",
    ),
    AppCase(
        "text",
        "echo-core-text.txt",
        "org.kde.kate.desktop",
        ("text/plain",),
        ("kate",),
    ),
    AppCase(
        "pdf",
        "echo-core-document.pdf",
        "org.kde.okular.desktop",
        ("application/pdf",),
        ("okular",),
    ),
    AppCase(
        "image",
        "echo-core-image.png",
        "org.kde.gwenview.desktop",
        ("image/png",),
        ("gwenview",),
    ),
    AppCase(
        "archive",
        "echo-core-archive.zip",
        "org.kde.ark.desktop",
        ("application/zip",),
        ("ark",),
    ),
    AppCase(
        "audio",
        "echo-core-audio.wav",
        "org.kde.haruna.desktop",
        ("audio/x-wav", "audio/vnd.wave"),
        ("haruna",),
    ),
    AppCase(
        "terminal",
        "Konsole",
        "org.kde.konsole.desktop",
        ("application/x-desktop",),
        ("konsole",),
        "desktop-entry",
        False,
    ),
    AppCase(
        "calculator",
        "KCalc",
        "org.kde.kcalc.desktop",
        ("application/x-desktop",),
        ("kcalc",),
        "desktop-entry",
        False,
    ),
)


class _FixtureRequestHandler(http.server.BaseHTTPRequestHandler):
    """Serve one bounded page without logging request or user data."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != f"/{HTTP_FIXTURE_NAME}":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(HTTP_FIXTURE_BODY)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(HTTP_FIXTURE_BODY)

    def log_message(self, _format: str, *_arguments: object) -> None:
        return


def start_loopback_http_server() -> tuple[http.server.ThreadingHTTPServer, threading.Thread, str]:
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FixtureRequestHandler)
    except OSError as error:
        raise SessionSmokeError("loopback HTTP fixture could not bind") from error
    host, port = server.server_address[:2]
    if host != "127.0.0.1" or isinstance(port, bool) or not isinstance(port, int) or port <= 0:
        server.server_close()
        raise SessionSmokeError("loopback HTTP fixture received an unsafe address")
    thread = threading.Thread(
        target=server.serve_forever,
        name="echo-core-apps-loopback-http",
        daemon=True,
    )
    thread.start()
    return server, thread, f"http://127.0.0.1:{port}/{HTTP_FIXTURE_NAME}"


def stop_loopback_http_server(
    server: http.server.ThreadingHTTPServer, thread: threading.Thread
) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    if thread.is_alive():
        raise SessionSmokeError("loopback HTTP fixture did not stop")


def _run(
    executable: Path,
    arguments: list[str],
    *,
    timeout: float = 10.0,
    cwd: Path | None = None,
) -> str:
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            check=True,
            capture_output=True,
            cwd=cwd,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SessionSmokeError(f"fixed diagnostic command failed: {executable.name}") from error
    encoded = completed.stdout.encode("utf-8", errors="replace")
    if len(encoded) > MAX_COMMAND_OUTPUT_BYTES:
        raise SessionSmokeError(f"diagnostic output is too large: {executable.name}")
    return completed.stdout


def _write_pdf(path: Path) -> None:
    stream = "BT /F1 18 Tf 36 72 Td (Echo OS core PDF smoke) Tj ET\n"
    objects = (
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream.encode('ascii'))} >>\nstream\n{stream}endstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    )
    content = bytearray(b"%PDF-1.4\n%EchoOS\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n{body}\nendobj\n".encode("ascii"))
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(content)


def _write_wav(path: Path) -> None:
    sample_rate = 8000
    sample_count = 800
    samples = b"\0\0" * sample_count
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(samples),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        len(samples),
    )
    path.write_bytes(header + samples)


def create_fixtures(directory: Path) -> dict[str, Path]:
    fixture_cases = [case for case in CASES if case.target_kind != "desktop-entry"]
    fixtures = {case.name: directory / case.filename for case in fixture_cases}
    fixtures["directory"].mkdir(mode=0o700)
    fixtures["http"].write_bytes(HTTP_FIXTURE_BODY)
    fixtures["text"].write_text("Echo OS core text association smoke\n", encoding="utf-8")
    _write_pdf(fixtures["pdf"])
    fixtures["image"].write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
            "AAAAC0lEQVR42mP8/x8AAusB9Y9Z6hUAAAAASUVORK5CYII="
        )
    )
    archive_member = directory / "echo-core-archive-member.txt"
    archive_member.write_text("Echo OS archive association smoke\n", encoding="utf-8")
    _run(ZIP, ["-q", fixtures["archive"].name, archive_member.name], cwd=directory)
    _write_wav(fixtures["audio"])
    for case in fixture_cases:
        fixture = fixtures[case.name]
        fixture.chmod(0o700 if case.name == "directory" else 0o600)
    return fixtures


def safe_desktop_entry(case: AppCase) -> Path:
    if case.target_kind != "desktop-entry" or "/" in case.desktop_id:
        raise SessionSmokeError("desktop launch case has an unsafe identity")
    desktop_entry = APPLICATIONS_DIR / case.desktop_id
    try:
        metadata = desktop_entry.lstat()
    except OSError as error:
        raise SessionSmokeError(f"{case.name} desktop entry cannot be inspected") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or metadata.st_mode & 0o022
        or metadata.st_size <= 0
        or metadata.st_size > MAX_COMMAND_OUTPUT_BYTES
    ):
        raise SessionSmokeError(f"{case.name} desktop entry is not immutable root content")
    _run(DESKTOP_FILE_VALIDATE, [str(desktop_entry)])
    return desktop_entry


def parse_x11_windows(output: str) -> list[dict[str, Any]]:
    if len(output.encode("utf-8", errors="replace")) > MAX_COMMAND_OUTPUT_BYTES:
        raise SessionSmokeError("X11 window list is too large")
    windows: list[dict[str, Any]] = []
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        match = re.match(
            r"^(0x[0-9a-f]+)\s+(-?\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s*(.*)$",
            raw_line.strip(),
            flags=re.IGNORECASE,
        )
        if match is None:
            continue
        if len(windows) >= 4096:
            raise SessionSmokeError("X11 window list is too large")
        window_id, _desktop, pid, _host, wm_class, title = match.groups()
        windows.append(
            {
                "id": window_id.lower(),
                "pid": int(pid),
                "wmClass": wm_class,
                "title": title or wm_class,
            }
        )
    return windows


def parse_wayland_windows(output: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise SessionSmokeError("KWin bridge returned invalid JSON") from error
    windows = payload.get("windows") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or not payload.get("ok")
        or not isinstance(windows, list)
        or len(windows) > 4096
    ):
        raise SessionSmokeError("KWin bridge returned an invalid window list")
    result: list[dict[str, Any]] = []
    for window in windows:
        if not isinstance(window, dict):
            raise SessionSmokeError("KWin bridge returned an invalid window")
        window_id = str(window.get("id") or "")
        pid = window.get("pid")
        if (
            re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                window_id,
            )
            is None
            or isinstance(pid, bool)
            or not isinstance(pid, int)
        ):
            raise SessionSmokeError("KWin bridge window identity is invalid")
        result.append(
            {
                "id": window_id,
                "pid": pid,
                "wmClass": str(window.get("wmClass") or ""),
                "title": str(window.get("title") or ""),
            }
        )
    return result


def identity_matches(window: dict[str, Any], case: AppCase) -> bool:
    identity = str(window.get("wmClass") or "").lower().removesuffix(".desktop")
    tokens = {token for token in re.split(r"[^a-z0-9]+", identity) if token}
    expected = case.desktop_id.lower().removesuffix(".desktop")
    return identity == expected or all(token in tokens for token in case.identity_tokens)


def case_window(
    windows: list[dict[str, Any]], baseline_ids: set[str], case: AppCase
) -> dict[str, Any] | None:
    matches = [
        window
        for window in windows
        if str(window.get("id")) not in baseline_ids
        and int(window.get("pid") or 0) > 0
        and identity_matches(window, case)
        and (
            not case.require_fixture_title
            or case.filename.lower() in str(window.get("title") or "").lower()
        )
    ]
    if len(matches) > 1:
        raise SessionSmokeError(f"multiple {case.name} fixture windows appeared")
    return matches[0] if matches else None


class WindowProvider:
    def __init__(self, session: str, bridge_socket: Path | None) -> None:
        self.session = session
        self.bridge_socket = bridge_socket

    def list(self) -> list[dict[str, Any]]:
        if self.session == "x11":
            return parse_x11_windows(_run(WMCTRL, ["-l", "-x", "-p"]))
        if self.bridge_socket is None:
            raise SessionSmokeError("Wayland session requires the KWin bridge socket")
        return parse_wayland_windows(
            _run(
                KWIN_BRIDGE,
                ["--socket", str(self.bridge_socket), "--request", "list"],
            )
        )

    def close(self, window_id: str) -> None:
        if self.session == "x11":
            if re.fullmatch(r"0x[0-9a-f]+", window_id) is None:
                raise SessionSmokeError("invalid X11 window id")
            _run(WMCTRL, ["-ic", window_id])
            return
        if self.bridge_socket is None:
            raise SessionSmokeError("Wayland session requires the KWin bridge socket")
        response = _run(
            KWIN_BRIDGE,
            [
                "--socket",
                str(self.bridge_socket),
                "--request",
                "close",
                "--window-id",
                window_id,
            ],
        )
        try:
            payload = json.loads(response)
        except json.JSONDecodeError as error:
            raise SessionSmokeError("KWin close returned invalid JSON") from error
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise SessionSmokeError("KWin did not acknowledge the close action")


def _wait_for_window(
    provider: WindowProvider, baseline_ids: set[str], case: AppCase
) -> dict[str, Any]:
    deadline = time.monotonic() + WINDOW_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        window = case_window(provider.list(), baseline_ids, case)
        if window is not None:
            return window
        time.sleep(POLL_SECONDS)
    raise SessionSmokeError(f"{case.name} fixture did not open in its native application")


def _wait_closed(provider: WindowProvider, window_id: str) -> None:
    deadline = time.monotonic() + WINDOW_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if window_id not in {str(item["id"]) for item in provider.list()}:
            return
        time.sleep(POLL_SECONDS)
    raise SessionSmokeError("native application window did not close")


def _query_mime(arguments: list[str]) -> str:
    result = _run(XDG_MIME, arguments).strip()
    if not result or "\0" in result or "\n" in result:
        raise SessionSmokeError("xdg-mime returned an invalid single-line result")
    return result


def _safe_runtime_directory() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR", "")
    runtime = Path(value)
    if not value or not runtime.is_absolute():
        raise SessionSmokeError("XDG_RUNTIME_DIR is required and must be absolute")
    try:
        metadata = runtime.lstat()
    except OSError as error:
        raise SessionSmokeError("XDG_RUNTIME_DIR cannot be inspected") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise SessionSmokeError("XDG_RUNTIME_DIR is not a private owned directory")
    return runtime


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True, choices=("x11", "wayland"))
    parser.add_argument("--bridge-socket", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    if os.environ.get("ECHO_CORE_APPS_SESSION_TEST") != SOURCE_TEST_SENTINEL:
        raise SessionSmokeError("core-app session smoke requires the explicit CI sentinel")
    if os.environ.get("XDG_SESSION_TYPE") != arguments.session:
        raise SessionSmokeError("requested session differs from XDG_SESSION_TYPE")
    if arguments.session == "wayland":
        if (
            arguments.bridge_socket is None
            or not arguments.bridge_socket.is_absolute()
            or not arguments.bridge_socket.is_socket()
        ):
            raise SessionSmokeError("Wayland KWin bridge socket is missing or unsafe")
    elif arguments.bridge_socket is not None:
        raise SessionSmokeError("X11 session does not accept a KWin bridge socket")

    for executable in required_session_executables(arguments.session):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise SessionSmokeError(f"core-app session executable is missing: {executable}")

    runtime = _safe_runtime_directory()
    fixture_root = Path(tempfile.mkdtemp(prefix="core-apps-session-", dir=runtime))
    fixture_root.chmod(0o700)
    provider = WindowProvider(arguments.session, arguments.bridge_socket)
    baseline_ids: set[str] = set()
    opened_window_ids: set[str] = set()
    launchers: list[subprocess.Popen[bytes]] = []
    http_server: http.server.ThreadingHTTPServer | None = None
    http_thread: threading.Thread | None = None
    http_target = ""
    try:
        baseline_ids = {str(item["id"]) for item in provider.list()}
        fixtures = create_fixtures(fixture_root)
        http_server, http_thread, http_target = start_loopback_http_server()
        for case in CASES:
            if case.target_kind == "desktop-entry":
                detected_mime = "application/x-desktop"
                launch_executable = GIO
                launch_arguments = ["launch", str(safe_desktop_entry(case))]
                transport = "gio-launch"
            elif case.target_kind == "loopback-http":
                detected_mime = "x-scheme-handler/http"
                launch_executable = XDG_OPEN
                launch_arguments = [http_target]
                transport = "xdg-open"
            else:
                fixture = fixtures[case.name]
                detected_mime = _query_mime(["query", "filetype", str(fixture)])
                launch_executable = XDG_OPEN
                launch_arguments = [str(fixture)]
                transport = "xdg-open"
            if detected_mime not in case.detected_mimes:
                raise SessionSmokeError(
                    f"{case.name} fixture has unexpected MIME type: {detected_mime}"
                )
            if case.target_kind != "desktop-entry":
                default_handler = _query_mime(["query", "default", detected_mime])
                if default_handler != case.desktop_id:
                    raise SessionSmokeError(
                        f"{case.name} default differs from the signed-root policy"
                    )

            log_path = fixture_root / f"{case.name}.log"
            with log_path.open("wb") as log_stream:
                try:
                    launcher = subprocess.Popen(
                        [str(launch_executable), *launch_arguments],
                        stdin=subprocess.DEVNULL,
                        stdout=log_stream,
                        stderr=subprocess.STDOUT,
                    )
                except OSError as error:
                    raise SessionSmokeError(
                        f"xdg-open could not launch the {case.name} fixture"
                    ) from error
            launchers.append(launcher)
            window = _wait_for_window(provider, baseline_ids, case)
            window_id = str(window["id"])
            opened_window_ids.add(window_id)
            provider.close(window_id)
            _wait_closed(provider, window_id)
            opened_window_ids.discard(window_id)
            try:
                launcher.wait(timeout=5)
            except subprocess.TimeoutExpired:
                launcher.terminate()
                launcher.wait(timeout=5)
            if launcher.returncode not in (0, -15):
                raise SessionSmokeError(f"xdg-open failed for the {case.name} fixture")
            print(
                f"ECHO_CORE_APP_OPENED case={case.name} mime={detected_mime} "
                f"desktop={case.desktop_id} transport={transport} "
                "window=observed cleanup=closed"
            )
        print(
            f"ECHO_CORE_APPS_SESSION_READY session={arguments.session} "
            "cases=directory,http,text,pdf,image,archive,audio,terminal,calculator "
            "transports=xdg-open,gio-launch "
            "windows=native cleanup=closed fixtures=runtime-and-loopback-only"
        )
        return 0
    finally:
        with contextlib.suppress(SessionSmokeError):
            for window in provider.list():
                window_id = str(window["id"])
                if window_id in opened_window_ids:
                    with contextlib.suppress(SessionSmokeError):
                        provider.close(window_id)
        for launcher in launchers:
            if launcher.poll() is None:
                launcher.terminate()
                try:
                    launcher.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    launcher.kill()
                    launcher.wait(timeout=5)
        if http_server is not None and http_thread is not None:
            with contextlib.suppress(SessionSmokeError):
                stop_loopback_http_server(http_server, http_thread)
        shutil.rmtree(fixture_root)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SessionSmokeError as error:
        print(f"core-app session smoke error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
