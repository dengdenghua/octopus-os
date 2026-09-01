"""Private helper functions for the health router.

Pure structural split of ``health_router``: version/process inspection,
journal-path checks, and host/URL normalization helpers. No logic changes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import Request


def _project_version(root: Path) -> str:
    try:
        import tomllib

        payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        return str(payload.get("project", {}).get("version") or "")
    except (OSError, ValueError, ImportError, TypeError):
        return ""


def _frontend_version(root: Path) -> str:
    try:
        payload = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return ""
    return str(payload.get("version") or "")


def _process_info() -> dict[str, Any]:
    argv = [str(part) for part in sys.argv[:8]]
    if len(sys.argv) > len(argv):
        argv.append("...")
    return {
        "schema": "echo.runtime_process.v1",
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "argv": argv,
    }


def _journal_source_usable(state: Any) -> bool:
    journal_path = getattr(state, "journal_path", None)
    if journal_path is None:
        return True
    try:
        parent = Path(journal_path).expanduser().resolve().parent
    except (OSError, TypeError, ValueError):
        return False
    return parent.exists() and os.access(parent, os.W_OK)


def _journal_source_detail(state: Any) -> str:
    journal_path = getattr(state, "journal_path", None)
    if journal_path is None:
        return "in-memory"
    try:
        parent = Path(journal_path).expanduser().resolve().parent
    except (OSError, TypeError, ValueError):
        return f"invalid journal_path={journal_path}"
    writable = parent.exists() and os.access(parent, os.W_OK)
    return f"journal_path={journal_path} parent={parent} writable={writable}"


def _clean_host(value: str | None) -> str:
    host = str(value or "").strip().strip("[]").lower()
    return host or "127.0.0.1"


def _canonical_backend_host(host: str) -> str:
    cleaned = _clean_host(host)
    if cleaned in {"localhost", "::1", "0:0:0:0:0:0:0:1"}:
        return "127.0.0.1"
    if cleaned == "0.0.0.0":  # nosec B104 — string comparison, not a bind
        return "127.0.0.1"
    return cleaned


def _loopback_aliases(host: str, port: int, scheme: str) -> dict[str, Any]:
    canonical = _canonical_backend_host(host)
    is_loopback = canonical.startswith("127.") or canonical == "::1"
    urls = [
        f"{scheme}://127.0.0.1:{port}",
        f"{scheme}://localhost:{port}",
    ]
    return {
        "schema": "echo.loopback_aliases.v1",
        "requested_host": host,
        "canonical_host": canonical,
        "same_loopback_family": is_loopback,
        "aliases": urls if is_loopback else [f"{scheme}://{canonical}:{port}"],
    }


def _frontend_runtime_info(
    *,
    request: Request | None,
    request_scheme: str,
    backend_canonical_base_url: str,
    frontend_host: str | None = None,
    frontend_port: int | None = None,
    frontend_proxy_target: str | None = None,
) -> dict[str, Any]:
    observed_origin = _request_frontend_origin(request)
    frontend_env_port = _coerce_port(os.environ.get("FRONTEND_PORT"))
    port = _coerce_port(frontend_port) or _origin_port(observed_origin) or frontend_env_port or 3000
    configured_host = _clean_host(
        frontend_host or os.environ.get("VITE_CANONICAL_LOOPBACK_HOST") or "localhost"
    )
    canonical_host = _frontend_canonical_host(configured_host)
    canonical_origin = f"{request_scheme}://{canonical_host}:{port}"
    # Backend APIs can treat localhost/127 as equivalent, but the browser cannot:
    # frontend assets, localStorage, sessionStorage and auth state are origin-
    # partitioned. A 127.0.0.1 frontend origin must be redirected to the canonical
    # localhost origin instead of being accepted as "close enough".
    origin_normalized = not observed_origin or observed_origin == canonical_origin
    proxy_target = _normalize_base_url(
        frontend_proxy_target
        or os.environ.get("ECHO_INTERNAL_GATEWAY_BASE_URL")
        or f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT') or '8000'}"
    )
    proxy_targets_backend = (
        _same_local_base_url(proxy_target, backend_canonical_base_url) if proxy_target else False
    )
    aliases = _loopback_aliases(canonical_host, port, request_scheme)["aliases"]
    return {
        "schema": "echo.frontend_runtime.v1",
        "observed_origin": observed_origin,
        "canonical_origin": canonical_origin,
        "canonical_host": canonical_host,
        "port": port,
        "env_port": frontend_env_port,
        "dev_proxy_mode": True,
        "proxy_target": proxy_target,
        "proxy_targets_backend": proxy_targets_backend,
        "origin_normalized": origin_normalized,
        "loopback_aliases": aliases,
    }


def _request_frontend_origin(request: Request | None) -> str:
    if request is None:
        return ""
    headers = getattr(request, "headers", {}) or {}
    for key in ("origin", "referer"):
        value = str(headers.get(key) or "").strip()
        if not value:
            continue
        try:
            parsed = urlparse(value)
        except (AttributeError, ValueError):
            continue
        if not parsed.scheme or not parsed.netloc:
            continue
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}"
    return ""


def _frontend_canonical_host(host: str) -> str:
    cleaned = _clean_host(host)
    if cleaned in {"0.0.0.0", "::", ""}:  # nosec B104 — string comparison, not a bind
        return "localhost"
    if cleaned in {"::1", "0:0:0:0:0:0:0:1"}:
        return "localhost"
    return cleaned


def _origin_host(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = urlparse(value)
    except (AttributeError, ValueError):
        return ""
    return _clean_host(parsed.hostname or "")


def _origin_port(value: str) -> int | None:
    if not value:
        return None
    try:
        parsed = urlparse(value)
        return _coerce_port(parsed.port)
    except (AttributeError, ValueError):
        return None


def _normalize_base_url(value: str | None) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except (AttributeError, ValueError):
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{_clean_host(parsed.hostname)}{port}"


def _is_loopback_host(host: str) -> bool:
    cleaned = _clean_host(host)
    return (
        cleaned == "localhost"
        or cleaned == "::1"
        or cleaned == "0:0:0:0:0:0:0:1"
        or cleaned.startswith("127.")
    )


def _same_local_base_url(left: str, right: str) -> bool:
    left_norm = _normalize_base_url(left)
    right_norm = _normalize_base_url(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    try:
        left_parsed = urlparse(left_norm)
        right_parsed = urlparse(right_norm)
    except (AttributeError, ValueError):
        return False
    return (
        left_parsed.scheme == right_parsed.scheme
        and left_parsed.port == right_parsed.port
        and _is_loopback_host(left_parsed.hostname or "")
        and _is_loopback_host(right_parsed.hostname or "")
    )


def _coerce_port(value: Any) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= port <= 65535:
        return port
    return None
