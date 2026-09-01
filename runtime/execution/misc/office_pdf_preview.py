"""High-fidelity Office preview conversion with a bounded local cache."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from html import escape
from pathlib import Path

_SUPPORTED = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
_MAX_SOURCE_BYTES = 128 * 1024 * 1024
_CONVERSION_TIMEOUT_SECONDS = 45
_CACHE_ROOT = Path(tempfile.gettempdir()) / "echo-office-preview-cache"
_CONVERSION_LOCK = threading.Lock()
_CACHE_VERSION = "2"


def render_office_pdf(path: Path) -> Path | None:
    """Convert an Office file to cached PDF, returning ``None`` on fallback."""

    if path.suffix.lower() not in _SUPPORTED:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file() or stat.st_size > _MAX_SOURCE_BYTES:
        return None
    digest = hashlib.sha256(
        f"{_CACHE_VERSION}\0{path.resolve()}\0{stat.st_mtime_ns}\0{stat.st_size}".encode()
    ).hexdigest()
    cached = _CACHE_ROOT / f"{digest}.pdf"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached
    soffice = _find_soffice()
    if not soffice:
        return None

    with _CONVERSION_LOCK:
        if cached.is_file() and cached.stat().st_size > 0:
            return cached
        _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="convert-", dir=_CACHE_ROOT) as output_dir:
            try:
                subprocess.run(
                    [
                        soffice,
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        output_dir,
                        str(path),
                    ],
                    check=True,
                    capture_output=True,
                    env=_conversion_environment(),
                    timeout=_CONVERSION_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.SubprocessError):
                return None
            candidates = list(Path(output_dir).glob("*.pdf"))
            if len(candidates) != 1 or candidates[0].stat().st_size <= 0:
                return None
            temporary = _CACHE_ROOT / f".{digest}.{threading.get_ident()}.pdf"
            shutil.copyfile(candidates[0], temporary)
            temporary.replace(cached)
    return cached


def _find_soffice() -> str | None:
    configured = os.environ.get("ECHO_SOFFICE_PATH", "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
        Path("/usr/bin/soffice"),
        Path("/usr/local/bin/soffice"),
    ]
    discovered = shutil.which("soffice")
    if discovered:
        candidates.insert(0, Path(discovered))
    runtime_root = Path.home() / ".cache" / "codex-runtimes"
    if runtime_root.is_dir():
        candidates.extend(runtime_root.glob("*/dependencies/bin/override/soffice"))
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _conversion_environment() -> dict[str, str]:
    env = os.environ.copy()
    font_dirs = [
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path.home() / "Library" / "Fonts",
    ]
    available = [directory for directory in font_dirs if directory.is_dir()]
    if not available:
        return env
    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    font_cache = _CACHE_ROOT / "font-cache"
    font_cache.mkdir(exist_ok=True)
    config = _CACHE_ROOT / "fontconfig.xml"
    directories = "".join(f"<dir>{escape(str(directory))}</dir>" for directory in available)
    config.write_text(
        "<?xml version='1.0'?><!DOCTYPE fontconfig SYSTEM 'fonts.dtd'>"
        f"<fontconfig>{directories}<cachedir>{escape(str(font_cache))}</cachedir>"
        "</fontconfig>",
        encoding="utf-8",
    )
    env["FONTCONFIG_FILE"] = str(config)
    return env


__all__ = ["render_office_pdf"]
