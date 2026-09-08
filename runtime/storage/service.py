"""Lightweight standalone Storage protocol, without OCR or model dependencies.

Run: python -m runtime.storage.service serve --root <authorized-directory>
Requires ECHO_STORAGE_TOKEN (or the existing private Storage token file).
"""

from __future__ import annotations

import argparse
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.responses import FileResponse

from appliance.files.manager import FileManager, InsufficientStorage, PathEscape, UploadTooLarge
from appliance.state_lock import StateDirectoryLock
from runtime.storage.desktop_provider import (
    DesktopStorageProvider,
    desktop_file_entry,
    desktop_files,
    desktop_search,
    desktop_source,
)
from runtime.storage.text_documents import MAX_TEXT_BYTES, TEXT_EDIT_CAPABILITY, TextDocuments

_LOG = logging.getLogger("echo.storage.edits")


class TextChange(BaseModel):
    text: str = Field(max_length=MAX_TEXT_BYTES)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class SearchBody(BaseModel):
    query: str = Field(default="", max_length=2000)
    top_k: int = Field(default=8, ge=1, le=50)


def create_storage_app(root: str | Path, *, token: str) -> FastAPI:
    if len(token.strip()) < 24:
        raise ValueError("Storage requires a private token of at least 24 characters")
    root = Path(root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Storage root must be an existing authorized directory")
    manager = FileManager(root)
    documents = TextDocuments(manager)
    provider = DesktopStorageProvider(manager, documents.source_id)

    @asynccontextmanager
    async def lifespan(_app):
        # Refuse a second service process for this root, so all protocol saves
        # participate in the same in-process revision lock.
        with StateDirectoryLock.acquire(
            root / ".echo-upload-storage-runtime",
            exclusive=True,
            create=True,
            purpose="Storage text editing",
        ):
            yield

    def authenticate(request: Request) -> None:
        expected = f"Bearer {token}".encode()
        if not hmac.compare_digest(request.headers.get("authorization", "").encode(), expected):
            raise HTTPException(401, "Storage authentication required")

    app = FastAPI(
        title="Echo Storage",
        dependencies=[Depends(authenticate)],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def bounded_request(request: Request, call_next):
        try:
            authenticate(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        # JSON can escape one character as six bytes. Bound streaming bodies
        # before Pydantic parses them, including requests without Content-Length.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_TEXT_BYTES * 6 + 4096:
                return JSONResponse({"detail": "Storage request too large"}, status_code=413)
        request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        if request.method == "PUT" and request.url.path.endswith("/text"):
            _LOG.info("text.save resource=%s status=%s", request.url.path, response.status_code)
        return response

    @app.exception_handler(FileNotFoundError)
    @app.exception_handler(NotADirectoryError)
    async def missing(_request, _error):
        return JSONResponse({"detail": "文件不存在"}, status_code=404)

    @app.exception_handler(PathEscape)
    @app.exception_handler(ValueError)
    async def invalid(_request, _error):
        return JSONResponse({"detail": "文件路径无效"}, status_code=400)

    @app.exception_handler(PermissionError)
    async def denied(_request, _error):
        return JSONResponse({"detail": "文件没有读写权限"}, status_code=403)

    @app.exception_handler(InsufficientStorage)
    async def full(_request, _error):
        return JSONResponse({"detail": "存储空间不足"}, status_code=507)

    @app.exception_handler(UploadTooLarge)
    async def large(_request, _error):
        return JSONResponse({"detail": "文件超过上传限制"}, status_code=413)

    @app.get("/v1/manifest")
    def manifest():
        return {
            "service": "echo-storage",
            "version": "1.0",
            "role": "external",
            "capabilities": ["browse", "search", "files", "content", TEXT_EDIT_CAPABILITY],
            "text_edit": {"version": 1, "encoding": "utf-8", "max_bytes": MAX_TEXT_BYTES},
        }

    @app.get("/v1/sources")
    def sources():
        return [desktop_source(provider)]

    @app.get("/v1/browse")
    def browse(path: str = ""):
        return [desktop_file_entry(entry, provider.source_id) for entry in manager.list_dir(path)]

    @app.get("/v1/files")
    def files(kind: str = "document", limit: int = Query(500, ge=1, le=500)):
        return [item for item in desktop_files(provider, limit=limit) if item["kind"] == kind]

    @app.get("/v1/files/{resource_id}/content")
    @app.head("/v1/files/{resource_id}/content")
    def content(resource_id: str):
        return FileResponse(documents.target(resource_id))

    @app.get("/v1/files/{resource_id}/text")
    def read_text(resource_id: str):
        return documents.read(resource_id)

    @app.put("/v1/files/{resource_id}/text")
    def save_text(resource_id: str, change: TextChange):
        return documents.save(resource_id, change.text, change.expected_revision)

    @app.post("/v1/files/{resource_id}/diff")
    def diff_text(resource_id: str, change: TextChange):
        return documents.diff(resource_id, change.text, change.expected_revision)

    # The service performs no model inference or network requests. Policy is
    # acknowledged for compatibility with the gateway's privacy admission.
    policy: dict = {"mode": "privacy", "allow_cloud_answering": False}

    @app.put("/v1/policy")
    def set_policy(value: dict):
        policy.clear()
        policy.update(value)
        return dict(policy)

    @app.get("/v1/policy")
    def get_policy():
        return dict(policy)

    @app.get("/v1/models")
    @app.get("/v1/apps")
    @app.get("/v1/albums")
    def empty_list():
        return []

    @app.get("/v1/search")
    def search(query: str = "", top_k: int = Query(8, ge=1, le=50)):
        return {
            "query": query,
            "mode": policy.get("mode", "privacy"),
            "hits": desktop_search(provider, query, top_k=top_k),
            "message": "本地文本检索，未启用语义索引",
        }

    @app.post("/v1/search")
    def post_search(body: SearchBody):
        return search(body.query, body.top_k)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve"])
    parser.add_argument("--root", default=os.environ.get("ECHO_STORAGE_ROOT"))
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    if not args.root:
        parser.error("set --root or ECHO_STORAGE_ROOT to an authorized directory")
    from runtime.execution.suckers.storage_skills import _storage_token

    token = _storage_token()
    if not token:
        parser.error("set ECHO_STORAGE_TOKEN or provision ~/.echo/storage/api_token")
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        create_storage_app(args.root, token=token), host="127.0.0.1", port=args.port, workers=1
    )


if __name__ == "__main__":
    main()
