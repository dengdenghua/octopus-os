"""Built-in project Wiki plugin."""

from __future__ import annotations

from runtime.platform.plugins.bundled.project_wiki.service import contract
from runtime.platform.plugins.plugin_base import ModulePlugin


class ProjectWikiPlugin(ModulePlugin):
    name = "project_wiki"
    version = "1.0.0"
    description = "Unified project Wiki generation and storage contract"
    author = "Echo"

    def register_routes(self) -> None:
        if self.ctx is None or self.ctx.fastapi_app is None:
            return
        from fastapi import APIRouter

        router = APIRouter(prefix="/api/plugins/project-wiki", tags=["project-wiki"])

        @router.get("/contract")
        def api_project_wiki_contract() -> dict[str, object]:
            return contract()

        self.ctx.fastapi_app.include_router(router)


__all__ = ["ProjectWikiPlugin"]
