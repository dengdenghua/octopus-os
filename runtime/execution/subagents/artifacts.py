"""
Sub-agent file artifacts.

Why this exists
---------------

The design splits coordination into two channels:

* control/messages — task specs + conclusions (small, typed),
* payload/artifacts — large intermediate state written to FILES and only
  referenced by path + hash (Kimi-style context sharding / Claude-style
  file artifacts).

Keeping big payloads out of the model context stops the "game of
telephone" and context pollution that breaks long tasks. This module is
the payload channel: a small, dependency-light writer that drops an
artifact under a deterministic, lineage-scoped directory and returns a
structured reference the caller can stash on the blackboard or send back.

Layout
------

``<workspace>/.echo/artifacts/<root_thread_id>/<sub_thread_id>/<sanitized-name>``

When no ``workspace_path`` is available it falls back to the project's
``.echo/artifacts/`` root (mirroring how memory skills locate the repo
root), so the module stays usable in CLI / single-process runs.

Safety
------

* Writes only under the dedicated artifacts directory (never outside it).
* File names are sanitized (alnum, ``-``, ``_``, ``.``) — no path traversal.
* Content is written atomically (temp + rename) so partial writes never
  leave a corrupt artifact.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

_MAX_ARTIFACT_NAME_LEN = 80
_MAX_ARTIFACT_CHARS = 512 * 1024  # guard against accidental huge blobs

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _sanitize_name(name: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", name or "artifact").lstrip("._")
    if not cleaned:
        cleaned = "artifact"
    return cleaned[:_MAX_ARTIFACT_NAME_LEN]


def _artifacts_dir(
    workspace_path: str | os.PathLike[str] | None,
    root_thread_id: str,
    sub_thread_id: str,
) -> Path:
    base = Path(workspace_path).expanduser().resolve() if workspace_path else _project_root()
    root = _sanitize_name(root_thread_id or "root")[:64]
    sub = _sanitize_name(sub_thread_id or "run")[:64]
    return base / ".echo" / "artifacts" / root / sub


def save_artifact(
    content: str | bytes,
    *,
    name: str,
    workspace_path: str | os.PathLike[str] | None = None,
    root_thread_id: str = "",
    sub_thread_id: str = "",
) -> dict[str, Any]:
    """Write one artifact and return a structured reference.

    Returns ``{"ok": True, "path", "name", "hash", "size"}`` on success, or
    ``{"ok": False, "error"}`` (e.g. content too large). The ``hash`` is the
    SHA-256 of the written bytes, so a reference is self-authenticating.
    """
    if isinstance(content, str):
        data = content.encode("utf-8")
    elif isinstance(content, (bytes, bytearray)):
        data = bytes(content)
    else:
        return {"ok": False, "error": "content must be str or bytes"}

    if len(data) > _MAX_ARTIFACT_CHARS:
        return {
            "ok": False,
            "error": (f"artifact too large ({len(data)} bytes > {_MAX_ARTIFACT_CHARS})"),
        }

    d = _artifacts_dir(workspace_path, root_thread_id, sub_thread_id)
    try:
        d.mkdir(parents=True, exist_ok=True)
        fname = _sanitize_name(name)
        path = d / fname
        fd, tmp = tempfile.mkstemp(dir=str(d), prefix=f".{fname}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                with contextlib.suppress(OSError):
                    os.remove(tmp)
    except OSError as exc:  # noqa: BLE001
        return {"ok": False, "error": f"OSError: {exc}"}

    digest = hashlib.sha256(data).hexdigest()
    return {
        "ok": True,
        "path": str(path),
        "name": fname,
        "hash": digest,
        "size": len(data),
    }


def read_artifact(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read an artifact back by its returned path.

    Returns ``{"ok": True, "content", "path", "size"}`` or
    ``{"ok": False, "error"}``.
    """
    try:
        p = Path(path)
        data = p.read_bytes()
    except OSError as exc:  # noqa: BLE001
        return {"ok": False, "error": f"OSError: {exc}"}
    return {
        "ok": True,
        "content": data.decode("utf-8", errors="replace"),
        "path": str(p),
        "size": len(data),
    }


__all__ = ["read_artifact", "save_artifact"]
