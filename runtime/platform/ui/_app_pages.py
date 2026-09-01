"""Index / chat page + Vite webui mount for ``create_app``.

Extracted from ``app.py`` during the god-file reduction (§2.1 of the
navigation map). Serves the root index HTML, the legacy /chat page, and
mounts the built Vite webui static bundle when present.
"""

from __future__ import annotations

from fastapi.responses import HTMLResponse

from runtime.platform.ui.pages import _INDEX_HTML
from runtime.platform.ui.webui_static import _find_webui_dist, _mount_webui

from ._app_context import AppContext


def mount_pages(ctx: AppContext) -> None:
    """Mount the index / chat pages and the built webui bundle."""
    app = ctx.app

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    from .chat_page import get_chat_html

    @app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/chat.html", response_class=HTMLResponse, include_in_schema=False)
    def chat_page() -> str:
        return get_chat_html()

    _webui_dist = _find_webui_dist()
    if _webui_dist is not None:
        _mount_webui(app, _webui_dist)
