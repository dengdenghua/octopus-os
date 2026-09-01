"""Safe upstream release radar for the bundled Codex runtime.

The radar discovers release metadata only.  It never downloads, installs, or
executes an unreviewed upstream binary; promotion remains an Echo build and
release action with the existing pinned-integrity checks.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import app_paths

CODEX_PACKAGE_NAME = "@openai/codex"
DEFAULT_REGISTRY_URL = "https://registry.npmjs.org/@openai%2Fcodex/latest"
DEFAULT_CHECK_INTERVAL_SECONDS = 6 * 60 * 60
DEFAULT_INITIAL_CHECK_DELAY_SECONDS = 15
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_BUNDLE_MANIFEST_BYTES = 1024 * 1024


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _version_parts(value: str) -> tuple[int, int, int, int, str]:
    """Return a stable comparison key for semver-like Codex package versions."""

    core, _, suffix = value.strip().partition("-")
    parts = core.split(".")
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"invalid Codex version: {value!r}")
    numbers = [int(part) for part in parts]
    numbers.extend([0] * (3 - len(numbers)))
    # A stable version sorts after a prerelease with the same numeric core.
    return numbers[0], numbers[1], numbers[2], 1 if not suffix else 0, suffix


def _read_bundle_manifest_version(manifest_path: Path) -> str | None:
    """Read a version only from an authentic-looking Echo Codex manifest."""

    try:
        stat = manifest_path.stat()
        if not manifest_path.is_file() or not 0 < stat.st_size <= MAX_BUNDLE_MANIFEST_BYTES:
            return None
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            payload.get("schema") != "echo.codex_bundle.v1"
            or payload.get("package") != CODEX_PACKAGE_NAME
        ):
            return None
        version = str(payload["version"]).strip()
        _version_parts(version)
        return version
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def resolve_bundled_codex_version() -> str:
    configured = os.environ.get("ECHO_PACKAGED_CODEX_VERSION", "").strip()
    if configured:
        _version_parts(configured)
        return configured

    # A frozen desktop backend has no frontend/package.json beside its
    # PyInstaller extraction root. The verified executable path is still
    # provided by Electron, and its bundle manifest is the authoritative
    # release record immediately above bin/.
    codex_executable = os.environ.get("ECHO_CODEX_EXECUTABLE", "").strip()
    if codex_executable:
        executable_manifest = (
            Path(codex_executable).expanduser().resolve(strict=False).parent.parent
            / "echo-codex-bundle.json"
        )
        if version := _read_bundle_manifest_version(executable_manifest):
            return version

    candidates = [app_paths().root / "frontend" / "package.json"]
    resources = os.environ.get("ECHO_RESOURCES_DIR", "").strip()
    if resources:
        candidates.append(Path(resources) / "frontend" / "package.json")
    candidates.append(Path(__file__).resolve().parents[3] / "frontend" / "package.json")
    for package_path in dict.fromkeys(candidates):
        try:
            payload = json.loads(package_path.read_text(encoding="utf-8"))
            version = str(payload["devDependencies"][CODEX_PACKAGE_NAME]).strip()
            _version_parts(version)
            return version
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    bundle_manifest = (
        Path(__file__).resolve().parents[3]
        / "deploy"
        / "appliance"
        / "agent-codex"
        / "echo-codex-bundle.json"
    )
    if version := _read_bundle_manifest_version(bundle_manifest):
        return version
    raise RuntimeError("bundled Codex version is unavailable")


@dataclass(frozen=True)
class CodexUpdateStatus:
    package: str
    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    checked_at: str | None = None
    source_url: str = DEFAULT_REGISTRY_URL
    release_url: str = "https://github.com/openai/codex/releases"
    integrity: str | None = None
    tarball_url: str | None = None
    approval_status: str = "none"
    approved_version: str | None = None
    approved_at: str | None = None
    error: str | None = None

    def to_wire(self) -> dict[str, object]:
        return asdict(self)


Fetcher = Callable[[str, float], dict[str, Any]]


def _fetch_registry_metadata(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Echo-Codex-Update-Radar/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_RESPONSE_BYTES:
            raise ValueError("Codex registry response is too large")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Codex registry response is too large")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Codex registry response is invalid")
    return payload


class CodexUpstreamUpdateService:
    """Persisted, bounded release detector with an approval-only transition."""

    def __init__(
        self,
        state_path: Path | str,
        *,
        current_version: str | None = None,
        registry_url: str = DEFAULT_REGISTRY_URL,
        fetcher: Fetcher | None = None,
        check_interval_seconds: float = DEFAULT_CHECK_INTERVAL_SECONDS,
        initial_check_delay_seconds: float = DEFAULT_INITIAL_CHECK_DELAY_SECONDS,
    ) -> None:
        if not registry_url.startswith("https://"):
            raise ValueError("Codex registry URL must use HTTPS")
        self._state_path = Path(state_path).expanduser().resolve(strict=False)
        self._current_version = current_version or resolve_bundled_codex_version()
        _version_parts(self._current_version)
        self._registry_url = registry_url
        self._fetcher = fetcher or _fetch_registry_metadata
        self._check_interval_seconds = max(60.0, float(check_interval_seconds))
        self._initial_check_delay_seconds = max(0.0, float(initial_check_delay_seconds))
        self._lock = threading.RLock()
        self._task: asyncio.Task[None] | None = None

    def read(self) -> CodexUpdateStatus:
        with self._lock:
            try:
                payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return self._empty_status()
            if not isinstance(payload, dict):
                return self._empty_status()
            return self._status_from_payload(payload)

    def check(self, *, timeout: float = 8.0) -> CodexUpdateStatus:
        with self._lock:
            previous = self.read()
            checked_at = _utc_now()
            try:
                payload = self._fetcher(self._registry_url, timeout)
                latest = str(payload.get("version") or "").strip()
                _version_parts(latest)
                dist = payload.get("dist")
                if not isinstance(dist, dict):
                    raise ValueError("Codex package metadata has no dist record")
                integrity = str(dist.get("integrity") or "").strip()
                tarball = str(dist.get("tarball") or "").strip()
                if not integrity.startswith("sha512-"):
                    raise ValueError("Codex package integrity is missing")
                if not tarball.startswith("https://"):
                    raise ValueError("Codex package tarball must use HTTPS")
                update_available = _version_parts(latest) > _version_parts(self._current_version)
                approval_status = previous.approval_status
                approved_version = previous.approved_version
                approved_at = previous.approved_at
                if approved_version != latest:
                    approval_status = "pending" if update_available else "none"
                    approved_version = None
                    approved_at = None
                status = CodexUpdateStatus(
                    package=CODEX_PACKAGE_NAME,
                    current_version=self._current_version,
                    latest_version=latest,
                    update_available=update_available,
                    checked_at=checked_at,
                    source_url=self._registry_url,
                    integrity=integrity,
                    tarball_url=tarball,
                    approval_status=approval_status,
                    approved_version=approved_version,
                    approved_at=approved_at,
                )
            except (
                OSError,
                TimeoutError,
                ValueError,
                TypeError,
                json.JSONDecodeError,
                urllib.error.URLError,
            ) as exc:
                status = CodexUpdateStatus(
                    **{
                        **previous.to_wire(),
                        "current_version": self._current_version,
                        "checked_at": checked_at,
                        "error": str(exc)[:300] or type(exc).__name__,
                    }
                )
            self._write(status)
            return status

    def approve(self, version: str) -> CodexUpdateStatus:
        with self._lock:
            current = self.read()
            if not current.update_available or current.latest_version != version:
                raise ValueError("Codex update candidate is not current")
            status = CodexUpdateStatus(
                **{
                    **current.to_wire(),
                    "approval_status": "approved_for_next_release",
                    "approved_version": version,
                    "approved_at": _utc_now(),
                    "error": None,
                }
            )
            self._write(status)
            return status

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="codex-update-radar")

    async def close(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        await asyncio.sleep(self._initial_check_delay_seconds)
        while True:
            await asyncio.to_thread(self.check)
            await asyncio.sleep(self._check_interval_seconds)

    def _empty_status(self) -> CodexUpdateStatus:
        return CodexUpdateStatus(
            package=CODEX_PACKAGE_NAME,
            current_version=self._current_version,
            source_url=self._registry_url,
        )

    def _status_from_payload(self, payload: dict[str, Any]) -> CodexUpdateStatus:
        allowed = CodexUpdateStatus.__dataclass_fields__
        values = {key: value for key, value in payload.items() if key in allowed}
        values["package"] = CODEX_PACKAGE_NAME
        values["current_version"] = self._current_version
        values["source_url"] = self._registry_url
        try:
            return CodexUpdateStatus(**values)
        except TypeError:
            return self._empty_status()

    def _write(self, status: CodexUpdateStatus) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._state_path.parent,
                prefix=f".{self._state_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(status.to_wire(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                temp_path = Path(handle.name)
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self._state_path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)


__all__ = [
    "CODEX_PACKAGE_NAME",
    "DEFAULT_REGISTRY_URL",
    "CodexUpdateStatus",
    "CodexUpstreamUpdateService",
    "resolve_bundled_codex_version",
]
