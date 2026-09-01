from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Query, Request
    from fastapi.responses import JSONResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    JSONResponse = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split OKF/YAML frontmatter (JSON-literal values · see gen_wiki) from the
    markdown body so the UI renders clean markdown, not a raw ``---`` block.
    No frontmatter → ``({}, text)``. (ADR-009)"""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    meta: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        key, sep, val = line.partition(":")
        if not sep:
            continue
        try:
            meta[key.strip()] = json.loads(val.strip())
        except (ValueError, TypeError):
            meta[key.strip()] = val.strip().strip('"')
    # Only treat it as OKF frontmatter if the required ``type`` field is present,
    # so a user doc that merely opens with a ``---`` rule isn't truncated.
    if "type" not in meta:
        return {}, text
    return meta, text[end + 5 :]


# ═══════════════════════════════════════════════════════════
# Generation state · in-process singleton
# ═══════════════════════════════════════════════════════════


class _GenState:
    """Tracks the most-recent generation run. Single-shot per process ·
    frontend polls ``/api/wiki/progress`` while running."""

    def __init__(self) -> None:
        self.running: bool = False
        self.started_at: float = 0.0
        self.finished_at: float = 0.0
        self.error: str | None = None
        self._lock = threading.Lock()

    def try_start(self) -> bool:
        """Atomic test-and-set (audit P-05): returns True when this call
        acquired the generation slot (marking the run started), False when
        another generation is already running — so concurrent requests can
        never double-start the subprocess."""
        with self._lock:
            if self.running:
                return False
            self.running = True
            self.started_at = time.time()
            self.finished_at = 0.0
            self.error = None
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            elapsed = (
                (self.finished_at or time.time()) - self.started_at if self.started_at else 0.0
            )
            status = "idle"
            if self.running:
                status = "running"
            elif self.error:
                status = "error"
            elif self.started_at:
                status = "completed"
            return {
                "running": self.running,
                "status": status,
                "error": self.error,
                "elapsed_seconds": elapsed,
            }


_STATE = _GenState()


def _run_generator() -> bool:
    """Kick off ``scripts/gen_wiki.py`` in a worker thread. Because the
    generator is pure static analysis it completes in < 2s · we don't
    need real incremental progress · just flip ``running`` on/off and
    let the UI show ``indeterminate``.

    Returns True when a generator was started, False when one is already
    running (audit P-05: the test-and-set happens under the lock, so two
    concurrent requests cannot both spawn the subprocess)."""

    if not _STATE.try_start():
        return False

    def worker() -> None:
        try:
            repo_root = _repo_root()
            script = repo_root / "scripts" / "gen_wiki.py"
            proc = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode != 0:
                _STATE.error = (proc.stderr or proc.stdout or "non-zero exit")[:500]
        except subprocess.TimeoutExpired:
            _STATE.error = "generator timeout (>120s)"
        except Exception as exc:  # noqa: BLE001
            _STATE.error = f"{type(exc).__name__}: {exc}"
        finally:
            with _STATE._lock:
                _STATE.running = False
                _STATE.finished_at = time.time()

    threading.Thread(target=worker, daemon=True).start()
    return True


# ═══════════════════════════════════════════════════════════
# Path resolution + file walk
# ═══════════════════════════════════════════════════════════


def _repo_root() -> Path:
    """Walk up from this file until we find ``runtime/`` + ``docs/``."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "runtime").is_dir() and (parent / "docs").is_dir():
            return parent
    raise RuntimeError("repo root not found")


def _auto_dir() -> Path:
    return _repo_root() / "docs" / "auto"


def _resolve_doc_path(rel: str) -> Path:
    """Resolve ``rel`` inside ``docs/auto/`` · reject traversal."""
    base = _auto_dir().resolve()
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as e:
        raise HTTPException(400, "path escapes docs/auto/") from e
    if candidate.is_symlink():
        raise HTTPException(400, "symlinks not allowed")
    return candidate


def _flat_docs() -> list[dict[str, Any]]:
    """Walk ``docs/auto/`` and return a flat list matching frontend
    ``WikiDocEntry[]`` · used by ``GET /api/wiki/docs``."""
    out: list[dict[str, Any]] = []
    base = _auto_dir()
    if not base.is_dir():
        return out
    for md in sorted(base.rglob("*.md")):
        rel = md.relative_to(base).as_posix()
        # skip the README · it's the index and the UI has its own
        if rel == "README.md":
            continue
        out.append(
            {
                "path": rel,
                "name": rel.split("/")[-1].removesuffix(".md"),
                "size": md.stat().st_size,
            }
        )
    return out


def _load_manifest() -> dict[str, Any]:
    """Read ``docs/auto/index.json`` if present · otherwise empty."""
    path = _auto_dir() / "index.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ═══════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════


def _validate_user_root(root: str | None) -> Path | None:
    if not root or not isinstance(root, str):
        return None
    try:
        p = Path(root).expanduser()
    except (OSError, ValueError):
        return None
    if not p.is_absolute():
        return None
    try:
        p = p.resolve()
    except OSError:
        return None
    if not p.is_dir():
        return None
    return p


def _answer_from_wiki(question: str, *, model_router: Any, model: str | None) -> dict[str, Any]:
    """Synthesis layer (ADR-009): retrieve the most relevant wiki context and
    compose a grounded, cited answer — gbrain's "give the answer, not raw pages".

    Gated and safe: no model configured → ``grounded=False`` with no LLM call;
    no relevant wiki → ``grounded=False`` with no LLM call. Answers strictly from
    the retrieved wiki and is told to name gaps rather than invent."""
    question = (question or "").strip()
    if not question:
        return {"answer": "", "citations": [], "grounded": False, "reason": "empty question"}

    from runtime.memory.hemolymph.repo_context import build_codebase_context

    context, sources = build_codebase_context(question)
    if not context:
        return {
            "answer": "",
            "citations": [],
            "grounded": False,
            "reason": "no relevant wiki context",
        }
    if model_router is None:
        return {
            "answer": "",
            "citations": sources,
            "grounded": False,
            "reason": "no model configured",
        }

    from runtime.sensing.model_router.models import Message, ModelRequest

    system = (
        "You answer questions about THIS codebase using ONLY the wiki context "
        "provided below. Cite the page titles you relied on. If the context does "
        "not cover the question, say so plainly and name what is missing — never "
        "invent APIs, files, or behaviour."
    )
    try:
        resp = model_router.call(
            ModelRequest(
                model=model or "auto",
                messages=[
                    Message(role="system", content=system),
                    Message(role="user", content=f"Question: {question}\n\n{context}"),
                ],
                max_tokens=700,
                temperature=0.0,
                system_provider="anthropic",
            )
        )
    except Exception as exc:  # noqa: BLE001 — synthesis must never crash the endpoint
        return {
            "answer": "",
            "citations": sources,
            "grounded": False,
            "reason": f"model error: {exc}",
        }
    return {
        "answer": str(getattr(resp, "text", "") or ""),
        "citations": sources,
        "grounded": True,
        "model": model or "auto",
    }


def create_wiki_router(
    *,
    model_router: Any = None,
    model: str | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    """Build + return the FastAPI router. Call site:
    ``app.include_router(create_wiki_router(model_router=..., model=...))``.

    ``model_router`` / ``model`` enable the ``/api/wiki/ask`` synthesis endpoint;
    omit them (the default) and that route degrades to ``grounded=False``."""
    require_fastapi(__name__)

    router = APIRouter(tags=["wiki"])

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

    # Lazy-import the generic generator so the router file stays the
    # only place that knows about the dual-mode dispatch.
    from . import wiki_generic

    @router.post("/api/wiki/ask")
    def api_wiki_ask(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        _auth(request)
        """Synthesis Q&A over the project wiki — retrieve + compose a cited
        answer (ADR-009 · gbrain-style). Returns ``{answer, citations, grounded,
        reason?}``; ``grounded=False`` when no model is configured or the wiki
        doesn't cover the question (no LLM call in those cases)."""
        question = body.get("question")
        if not isinstance(question, str) or not question.strip():
            raise HTTPException(400, "body.question must be a non-empty string")
        return _answer_from_wiki(question, model_router=model_router, model=model)

    @router.get("/api/wiki/graph")
    def api_wiki_graph(request: Request) -> dict[str, Any]:
        _auth(request)
        """Wiki dependency graph (ADR-009): the page nodes + the zero-LLM
        page→page import edges from index.json, for a graph visualiser."""

        def _nodes(tree: Any) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for node in tree or []:
                if isinstance(node, dict):
                    if node.get("type") == "doc" and node.get("path"):
                        out.append({"path": node["path"], "title": node.get("title", "")})
                    out.extend(_nodes(node.get("children")))
            return out

        manifest = _load_manifest()
        return {
            "nodes": _nodes(manifest.get("tree")),
            "edges": manifest.get("edges", []),
            "generated_at": manifest.get("generated_at"),
        }

    @router.get("/api/wiki/okf-bundle")
    def api_wiki_okf_bundle(request: Request) -> Any:
        _auth(request)
        """Export the wiki as a portable OKF bundle — a ``tar.gz`` of
        ``docs/auto`` (markdown + frontmatter + index.json + edges). The family
        lingua franca (ADR-009): any OKF-aware consumer (Storage, mobile, os)
        fetches and ingests it without a proprietary SDK."""
        import io
        import tarfile

        from fastapi.responses import Response

        base = _auto_dir()
        if not base.is_dir():
            raise HTTPException(404, "no wiki bundle · run scripts/gen_wiki.py")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for f in sorted(base.rglob("*")):
                if f.is_file() and not f.is_symlink():
                    tar.add(str(f), arcname=f.relative_to(base).as_posix())
        return Response(
            content=buf.getvalue(),
            media_type="application/gzip",
            headers={"Content-Disposition": "attachment; filename=echo-wiki-okf.tar.gz"},
        )

    @router.get("/api/wiki/status")
    def api_wiki_status(request: Request, root: str | None = Query(None)) -> dict[str, Any]:
        _auth(request)
        """Frontend consumes ``{exists, status, generated_at, ...}``
        and switches between "generate CTA" / "content shell".

        ``root`` (optional, absolute path) routes to the per-project
        ``<root>/.echo-wiki/`` tree. When omitted we fall back to
        Echo's own ``docs/auto/`` (legacy behaviour for the
        self-documenting use case)."""
        user_root = _validate_user_root(root)
        if user_root is not None:
            s = wiki_generic.status(user_root)
            return {
                "exists": bool(s.get("exists")),
                "status": s.get("status", "not_generated"),
                "generated_at": s.get("generated_at"),
                "languages": ["zh", "en"],
                "files_analyzed": s.get("files_analyzed", 0),
                "modules": [],
                "generated_files": [d["path"] for d in wiki_generic.list_docs(user_root)],
                "changes_pending": None,
                "root": str(user_root),
                "autosync": wiki_generic.get_settings(user_root).get("autosync", False),
                "consistent": s.get("consistent", False),
                "schema": s.get("schema"),
                "plugin_id": s.get("plugin_id"),
                "plugin_version": s.get("plugin_version"),
                "generator_version": s.get("generator_version"),
                "policy_digest": s.get("policy_digest"),
                "project_id": s.get("project_id"),
            }
        manifest = _load_manifest()
        exists = bool(manifest)
        docs = _flat_docs()
        if exists and docs:
            status = "current"
        elif docs:
            status = "outdated"
        else:
            status = "not_generated"
        return {
            "exists": exists and bool(docs),
            "status": status,
            "generated_at": manifest.get("generated_at"),
            "languages": ["zh"],
            "files_analyzed": manifest.get("files_analyzed", len(docs)),
            "modules": [],
            "generated_files": [d["path"] for d in docs],
            "changes_pending": None,
        }

    @router.get("/api/wiki/docs")
    def api_wiki_docs_list(
        request: Request,
        lang: str = Query("zh"),
        root: str | None = Query(None),
    ) -> dict[str, Any]:
        _auth(request)
        """Flat WikiDocEntry list · frontend builds the tree via
        ``path.split('/')`` so we don't need to pre-structure here."""
        user_root = _validate_user_root(root)
        if user_root is not None:
            return {"docs": wiki_generic.list_docs(user_root), "lang": lang}
        return {"docs": _flat_docs(), "lang": lang}

    @router.get("/api/wiki/docs/{doc_path:path}")
    def api_wiki_doc_read(
        request: Request,
        doc_path: str,
        root: str | None = Query(None),
    ) -> dict[str, Any]:
        _auth(request)
        user_root = _validate_user_root(root)
        if user_root is not None:
            try:
                content = wiki_generic.read_doc(user_root, doc_path)
            except FileNotFoundError as exc:
                raise HTTPException(404, f"doc not found: {doc_path}") from exc
            except PermissionError as exc:
                raise HTTPException(400, str(exc)) from exc
            return {
                "path": doc_path,
                "content": content,
                "size": len(content.encode("utf-8")),
            }
        path = _resolve_doc_path(doc_path)
        if not path.is_file():
            raise HTTPException(404, f"doc not found: {doc_path}")
        # Strip OKF frontmatter so the UI renders clean markdown; surface the
        # parsed metadata separately for an optional type/tags/tier header.
        meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        return {
            "path": doc_path,
            "content": body,
            "meta": meta,
            "size": len(body.encode("utf-8")),
        }

    @router.put("/api/wiki/docs/{doc_path:path}")
    def api_wiki_doc_write(
        request: Request,
        doc_path: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        _auth(request)
        """Overwrite a generated doc · used by the inline editor in
        the frontend. Note: edits are lost on next ``generate`` run ·
        the generator is the source of truth."""
        if not doc_path.endswith(".md"):
            raise HTTPException(400, "only .md files editable")
        content = body.get("content")
        if not isinstance(content, str):
            raise HTTPException(400, "body.content must be a string")
        path = _resolve_doc_path(doc_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"ok": True, "path": doc_path, "size": path.stat().st_size}

    @router.post("/api/wiki/generate")
    def api_wiki_generate(request: Request, root: str | None = Query(None)) -> dict[str, Any]:
        _auth(request)
        """User-triggered generation. For per-project mode (``root``
        passed) we run synchronously since the generic generator is
        bounded (≤2s on typical repos)."""
        user_root = _validate_user_root(root)
        if user_root is not None:
            try:
                manifest = wiki_generic.generate(user_root)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(500, f"generate failed: {exc}") from exc
            return {"ok": True, "manifest": manifest}
        if not _run_generator():
            # Audit P-05: the atomic test-and-set owns the decision; a 409
            # here means another request already won the slot.
            raise HTTPException(409, "generation already in progress")
        return {"ok": True, "started_at": _STATE.started_at}

    @router.post("/api/wiki/update")
    def api_wiki_update(request: Request, root: str | None = Query(None)) -> dict[str, Any]:
        _auth(request)
        """Alias for generate · frontend uses this button when the
        wiki is already present but needs a refresh."""
        user_root = _validate_user_root(root)
        if user_root is not None:
            try:
                manifest = wiki_generic.generate(user_root)
            except Exception as exc:  # noqa: BLE001
                return {"status": "error", "error": str(exc)}
            return {
                "status": "ok",
                "updated_files": manifest.get("files_analyzed", 0),
                "error": None,
            }
        if not _run_generator():
            raise HTTPException(409, "generation already in progress")
        before = len(_flat_docs())
        for _ in range(60):  # up to ~6s
            if not _STATE.running:
                break
            time.sleep(0.1)
        after_docs = _flat_docs()
        return {
            "status": "ok" if not _STATE.error else "error",
            "updated_files": len(after_docs) - before,
            "error": _STATE.error,
        }

    @router.get("/api/wiki/settings")
    def api_wiki_settings_get(request: Request, root: str = Query(...)) -> dict[str, Any]:
        _auth(request)
        """Per-project wiki settings · currently just the autosync
        flag. ``root`` is required (settings only meaningful in
        per-project mode). The response also reflects whether the
        watcher is currently running so the UI can show a
        "watching" indicator distinct from the persisted flag.

        Also lazy-rehydrates · if autosync is persisted true but the
        watcher isn't running (e.g. backend restarted), we start it
        on the user's next visit. Saves us a separate startup-scan
        (the backend has no canonical list of "all workspaces" since
        the frontend keys those by per-thread localStorage)."""
        user_root = _validate_user_root(root)
        if user_root is None:
            raise HTTPException(400, "root must be an absolute existing dir")
        s = wiki_generic.get_settings(user_root)
        if s.get("autosync") and not wiki_generic.watcher_status(user_root):
            wiki_generic.watcher_set(user_root, True)
        s["watching"] = wiki_generic.watcher_status(user_root)
        return s

    @router.post("/api/wiki/settings")
    def api_wiki_settings_set(
        request: Request,
        body: dict[str, Any],
        root: str = Query(...),
    ) -> dict[str, Any]:
        _auth(request)
        """Persist autosync + arm/disarm the file watcher in one call.

        Watcher lifecycle is bound to the persisted flag so a backend
        restart can rehydrate (see ``boot_existing_watchers``). Failure
        to start the watcher (e.g. watchdog uninstalled) does NOT roll
        back the persisted flag · we return ``watching: false`` and let
        the UI surface "saved but not actively watching"."""
        user_root = _validate_user_root(root)
        if user_root is None:
            raise HTTPException(400, "root must be an absolute existing dir")
        autosync = bool(body.get("autosync", False))
        result = wiki_generic.set_settings(user_root, autosync)
        ok = wiki_generic.watcher_set(user_root, autosync)
        result["watching"] = wiki_generic.watcher_status(user_root)
        if autosync and not ok:
            result["watcher_error"] = "watchdog not available · regen on save disabled"
        return result

    @router.get("/api/wiki/progress")
    def api_wiki_progress(request: Request) -> dict[str, Any]:
        _auth(request)
        snap = _STATE.snapshot()
        total = max(len(_flat_docs()), 1)
        if snap["status"] == "running":
            current = "Scanning repo"
            pct = 50.0  # indeterminate · generator doesn't stream
            completed = 0
        elif snap["status"] == "completed":
            current = "Complete"
            pct = 100.0
            completed = total
        elif snap["status"] == "error":
            current = "Error"
            pct = 0.0
            completed = 0
        else:
            current = "Idle"
            pct = 0.0
            completed = 0
        return {
            "total_steps": total,
            "completed_steps": completed,
            "current_step": current,
            "progress_pct": pct,
            "elapsed_seconds": snap["elapsed_seconds"],
            "errors": [snap["error"]] if snap["error"] else [],
        }

    return router


__all__ = ["create_wiki_router"]
