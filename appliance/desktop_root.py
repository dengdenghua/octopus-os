"""Serve the Echo OS desktop at the appliance's canonical root URL.

Echo Agent deliberately mounts a configured Vite distribution under ``/ui/``
and mirrors its assets at ``/assets/``, but retains its historical dashboard at
``/``.  A NAS installation is opened as ``/#/desktop`` (the hash never reaches
the server), so the OS extension must own the two HTML entry paths while leaving
all Agent APIs untouched.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from starlette.responses import FileResponse


class ApplianceDesktopRootMiddleware:
    """Return the configured Echo OS index for only ``/`` and ``/index.html``."""

    def __init__(self, app: Any, *, webui_dist: str | Path | None = None) -> None:
        self.app = app
        configured = (
            str(webui_dist) if webui_dist is not None else os.environ.get("ECHO_WEBUI_DIST", "")
        ).strip()
        candidate_root = Path(configured).resolve() if configured else None
        candidate_index = candidate_root / "index.html" if candidate_root else None
        if candidate_index is not None and candidate_index.is_file():
            self.dist_root = candidate_root
            self.index_file = candidate_index
        else:
            self.dist_root = None
            self.index_file = None

    def _public_file(self, path: str) -> Path | None:
        if self.dist_root is None or not path.startswith("/") or path.startswith("/assets/"):
            return None
        candidate = (self.dist_root / path.removeprefix("/")).resolve()
        try:
            candidate.relative_to(self.dist_root)
        except ValueError:
            return None
        if candidate.is_file() and candidate != self.index_file:
            return candidate
        return None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        is_read = scope.get("type") == "http" and str(scope.get("method") or "GET").upper() in {
            "GET",
            "HEAD",
        }
        path = str(scope.get("path") or "")
        if is_read and self.index_file is not None and path in {"/", "/index.html"}:
            response = FileResponse(
                self.index_file,
                media_type="text/html",
                headers={"Cache-Control": "no-cache"},
            )
            await response(scope, receive, send)
            return
        public_file = self._public_file(path) if is_read else None
        if public_file is not None:
            response = FileResponse(
                public_file,
                headers={"Cache-Control": "public, max-age=3600"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


__all__ = ["ApplianceDesktopRootMiddleware"]
