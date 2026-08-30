"""Static Vite/React WebUI mounting helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def _find_webui_dist() -> Path | None:

    env_path = os.environ.get("ECHO_WEBUI_DIST")
    if env_path:
        p = Path(env_path)
        if p.is_dir() and (p / "index.html").is_file():
            return p

    # Historical note: before the ui package moved under ``runtime/platform``
    # the hop count was 3 · the old comment/code wasn't updated when the
    # move happened, which silently 404'd /ui/ on dev builds.
    candidate = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
    if candidate.is_dir() and (candidate / "index.html").is_file():
        return candidate

    candidate = Path(__file__).resolve().parent / "dist"
    if candidate.is_dir() and (candidate / "index.html").is_file():
        return candidate

    return None


def _mount_webui(app: Any, dist: Path) -> None:
    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/ui/assets",
            StaticFiles(directory=str(assets_dir)),
            name="webui-assets",
        )
        # Mirror at root so Vite's default absolute-root asset URLs work.
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="webui-assets-root",
        )

    for file in dist.iterdir():
        if file.is_file() and file.name not in ("index.html",):
            ui_path = f"/ui/{file.name}"
            root_path = f"/{file.name}"

            def _make_handler(fp: Path):
                def _h() -> FileResponse:
                    return FileResponse(str(fp))

                return _h

            app.get(ui_path, include_in_schema=False)(_make_handler(file))
            app.get(root_path, include_in_schema=False)(_make_handler(file))

    index_file = dist / "index.html"

    @app.get("/ui", include_in_schema=False)
    @app.get("/ui/", include_in_schema=False)
    @app.get("/ui/{full_path:path}", include_in_schema=False)
    def _webui_spa(full_path: str = "") -> FileResponse:
        _ = full_path
        return FileResponse(str(index_file))
