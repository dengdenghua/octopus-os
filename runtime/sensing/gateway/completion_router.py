"""Inline code completion endpoint — Tab-complete skeleton.

Provides a lightweight ``/api/complete`` endpoint that accepts a file
context (prefix + suffix around cursor) and returns a short completion
suggestion. The frontend renders this as ghost text in CodeMirror.

MVP: uses the configured LLM with a fill-in-the-middle prompt.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Request

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

_logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a code completion engine. Given the code before and after the cursor, "
    "output ONLY the code that should be inserted at the cursor position. "
    "No explanation, no markdown, no backticks. Just the raw code to insert. "
    "Keep completions short (1-3 lines). If unsure, output nothing."
)


def _build_prompt(prefix: str, suffix: str, language: str) -> str:
    return (
        f"Language: {language}\n"
        f"Code before cursor:\n```\n{prefix[-2000:]}\n```\n"
        f"Code after cursor:\n```\n{suffix[:500]}\n```\n"
        "Complete the code at the cursor position:"
    )


def create_completion_router(
    *,
    stack: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(tags=["completion"])

    def _auth(request: Request) -> str | None:
        if require_auth and identity_store is None:
            raise HTTPException(401, "auth required")
        from runtime.sensing.gateway.openai_gateway_router import _resolve_actor

        return _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    @router.post("/api/complete")
    async def complete(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        _auth(request)
        prefix = body.get("prefix", "")
        suffix = body.get("suffix", "")
        language = body.get("language", "")
        file_path = body.get("file_path", "")

        if not prefix.strip():
            return {"completion": "", "cached": False}

        if not language and file_path:
            ext = file_path.rsplit(".", 1)[-1] if "." in file_path else ""
            lang_map = {
                "py": "python",
                "ts": "typescript",
                "tsx": "typescript",
                "js": "javascript",
                "jsx": "javascript",
                "rs": "rust",
                "go": "go",
                "java": "java",
                "rb": "ruby",
                "cpp": "c++",
                "c": "c",
                "cs": "c#",
                "swift": "swift",
                "kt": "kotlin",
                "sql": "sql",
                "sh": "bash",
                "yml": "yaml",
                "yaml": "yaml",
                "json": "json",
                "html": "html",
                "css": "css",
                "vue": "vue",
                "svelte": "svelte",
            }
            language = lang_map.get(ext, ext)

        prompt = _build_prompt(prefix, suffix, language)

        try:
            from runtime.sensing.model_router.models import Message, ModelRequest

            # The model router lives on the built stack's planner (a
            # ModelDispatchRouter). The previous code reached for a
            # ``get_app_state()`` helper that never existed, so the whole
            # endpoint silently failed on ImportError.
            model_router = getattr(getattr(stack, "planner", None), "router", None)
            if model_router is None:
                return {"completion": "", "error": "no model router"}
            req = ModelRequest(
                model="auto",
                messages=[
                    Message(role="system", content=_SYSTEM),
                    Message(role="user", content=prompt),
                ],
                max_tokens=150,
                temperature=0.0,
            )
            resp = model_router.call(req)
            text = resp.text.strip() if resp.text else ""
            if text.startswith("```"):
                text = text.split("\n", 1)[-1] if "\n" in text else ""
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            return {"completion": text.strip(), "cached": False}
        except Exception as exc:
            _logger.debug("completion failed: %s", exc)
            return {"completion": "", "error": str(exc)}

    return router
