"""Code index router · ``/api/index/*``.

Bridges the frontend codebase-index-panel to the existing SQLite-based
code search infrastructure in code_intelligence_skills.py.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Request
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    BaseModel = object  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

_DB_PATH = Path("data/code_index.db")

_index_state: dict[str, Any] = {
    "running": False,
    "total_files": 0,
    "processed_files": 0,
    "total_chunks": 0,
    "embedded_chunks": 0,
    "skipped_files": 0,
    "progress_pct": 0,
    "elapsed_seconds": 0,
    "errors": [],
    "current_file": "",
    "workspace": "",
    "last_indexed_at": "",
}
_index_lock = threading.Lock()


def _count_db_rows() -> tuple[int, int]:
    if not _DB_PATH.exists():
        return 0, 0
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        total = conn.execute("SELECT COUNT(*) FROM code_chunks").fetchone()[0]
        conn.close()
        return total, total
    except Exception:
        return 0, 0


def _get_db_stats() -> dict[str, Any]:
    if not _DB_PATH.exists():
        return {
            "total_files": 0,
            "total_chunks": 0,
            "total_embeddings": 0,
            "db_size_bytes": 0,
            "languages": {},
            "chunk_types": {},
        }
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        rows = conn.execute("SELECT path FROM code_chunks").fetchall()
        conn.close()
        paths = [r[0] for r in rows]
        langs: dict[str, int] = {}
        files_set: set[str] = set()
        for p in paths:
            files_set.add(p)
            ext = Path(p).suffix.lower()
            lang = {
                ".py": "python",
                ".ts": "typescript",
                ".tsx": "tsx",
                ".js": "javascript",
                ".go": "go",
                ".rs": "rust",
                ".java": "java",
            }.get(ext, ext or "unknown")
            langs[lang] = langs.get(lang, 0) + 1
        return {
            "total_files": len(files_set),
            "total_chunks": len(paths),
            "total_embeddings": len(paths),
            "db_size_bytes": _DB_PATH.stat().st_size if _DB_PATH.exists() else 0,
            "languages": langs,
            "chunk_types": {"code": len(paths)},
        }
    except Exception:
        return {
            "total_files": 0,
            "total_chunks": 0,
            "total_embeddings": 0,
            "db_size_bytes": 0,
            "languages": {},
            "chunk_types": {},
        }


def _run_indexing(workspace: str, force: bool = False) -> None:
    global _index_state
    with _index_lock:
        if _index_state["running"]:
            return
        _index_state["running"] = True
        _index_state["workspace"] = workspace
        _index_state["errors"] = []
        _index_state["progress_pct"] = 0
        _index_state["processed_files"] = 0
        _index_state["total_files"] = 0

    t0 = time.monotonic()
    try:
        from runtime.execution.suckers.code_intelligence_skills import (
            _build_index,
            _get_embedder,
            _save_persisted_index,
        )

        embedder = _get_embedder()
        if embedder is None:
            _index_state["errors"].append("sentence-transformers not installed")
            return

        root = Path(workspace)
        exts = {
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".go",
            ".rs",
            ".java",
            ".rb",
            ".c",
            ".cpp",
            ".h",
        }
        all_files = [
            p
            for p in root.rglob("*")
            if p.is_file()
            and p.suffix in exts
            and not any(
                part.startswith(".")
                or part in ("node_modules", "__pycache__", ".venv", "dist", "build")
                for part in p.parts
            )
            and p.stat().st_size <= 200_000
        ]
        _index_state["total_files"] = len(all_files)

        index = _build_index(embedder, workspace, exts)
        _save_persisted_index(index)

        _index_state["processed_files"] = len(all_files)
        _index_state["total_chunks"] = len(index)
        _index_state["embedded_chunks"] = len(index)
        _index_state["progress_pct"] = 100
        from datetime import datetime

        _index_state["last_indexed_at"] = datetime.now().isoformat()
    except Exception as exc:
        _index_state["errors"].append(str(exc))
    finally:
        _index_state["elapsed_seconds"] = round(time.monotonic() - t0, 1)
        _index_state["running"] = False


if FASTAPI_AVAILABLE:

    class IndexStartRequest(BaseModel):
        force: bool = False
        workspace: str = "."

    class IndexSearchRequest(BaseModel):
        query: str
        top_k: int = 10
        workspace: str = "."


def create_index_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(tags=["index"])

    def _auth(request: Request) -> str | None:
        from runtime.safety.auth.principal import require_roles

        principal = require_roles(
            request,
            identity_store,
            require_auth,
            ("admin", "operator"),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        return principal.actor_id if principal is not None else None

    @router.get("/api/index/status")
    def api_index_status(request: Request) -> dict[str, Any]:
        _auth(request)
        chunks, embeddings = _count_db_rows()
        return {
            **_index_state,
            "status": "running"
            if _index_state["running"]
            else ("ready" if chunks > 0 else "empty"),
            "total_chunks": _index_state.get("total_chunks") or chunks,
            "embedded_chunks": _index_state.get("embedded_chunks") or embeddings,
        }

    @router.get("/api/index/stats")
    def api_index_stats(request: Request) -> dict[str, Any]:
        _auth(request)
        stats = _get_db_stats()
        stats["last_indexed_at"] = _index_state.get("last_indexed_at", "")
        stats["config"] = {
            "embedding_backend": "sentence-transformers",
            "embedding_model": "all-MiniLM-L6-v2",
            "chunk_max_tokens": 512,
            "search_top_k": 10,
            "auto_index": False,
        }
        return stats

    @router.post("/api/index/start")
    def api_index_start(request: Request, body: IndexStartRequest) -> dict[str, Any]:
        _auth(request)
        if _index_state["running"]:
            raise HTTPException(409, "Indexing already in progress")
        t = threading.Thread(target=_run_indexing, args=(body.workspace, body.force), daemon=True)
        t.start()
        return {"started": True}

    @router.post("/api/index/rebuild")
    def api_index_rebuild(request: Request) -> dict[str, Any]:
        _auth(request)
        if _DB_PATH.exists():
            _DB_PATH.unlink()
        workspace = _index_state.get("workspace") or "."
        t = threading.Thread(target=_run_indexing, args=(workspace, True), daemon=True)
        t.start()
        return {"started": True}

    @router.delete("/api/index")
    def api_index_clear(request: Request) -> dict[str, Any]:
        _auth(request)
        if _DB_PATH.exists():
            _DB_PATH.unlink()
        _index_state["total_chunks"] = 0
        _index_state["embedded_chunks"] = 0
        _index_state["total_files"] = 0
        _index_state["processed_files"] = 0
        _index_state["last_indexed_at"] = ""
        return {"cleared": True}

    @router.post("/api/index/search")
    def api_index_search(request: Request, body: IndexSearchRequest) -> dict[str, Any]:
        _auth(request)
        t0 = time.monotonic()
        try:
            from runtime.execution.suckers.code_intelligence_skills import _code_search

            raw = _code_search(query=body.query, directory=body.workspace, top_k=body.top_k)
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc

        results = []
        for item in raw.get("results", []):
            path = item.get("path", "")
            snippet = item.get("snippet", "")
            lines = snippet.split("\n")
            name_match = re.search(r"(?:def|class|function|export)\s+(\w+)", snippet)
            name = (
                name_match.group(1) if name_match else path.split("/")[-1] if "/" in path else path
            )
            results.append(
                {
                    "chunk_id": f"{path}:{hash(snippet) & 0xFFFF:04x}",
                    "file_path": path,
                    "start_line": 1,
                    "end_line": len(lines),
                    "content": snippet,
                    "chunk_type": "code",
                    "name": name,
                    "language": Path(path).suffix.lstrip(".") if path else "unknown",
                    "similarity": item.get("score", 0),
                    "context_header": lines[0] if lines else "",
                }
            )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return {
            "query": body.query,
            "results": results,
            "total_results": len(results),
            "elapsed_ms": elapsed_ms,
        }

    return router


__all__ = ["create_index_router"]
