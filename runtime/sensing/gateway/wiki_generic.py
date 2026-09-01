# ruff: noqa: E402 — module-level imports below are intentionally late
"""
Project-agnostic wiki generator · scans an arbitrary user-selected
folder and writes a navigable static documentation tree under
``<root>/.echo-wiki/``.

Why a separate module
---------------------

``scripts/gen_wiki.py`` is hard-wired to Echo's repo layout
(``runtime/`` / ``agents/`` / specific TOC ordering). Users picking
their own project folder via WorkDirSelector need a generator that
makes no assumptions about the tree shape · just walk, parse, and
render whatever is there.

Output layout (under ``<root>/.echo-wiki/``)::

    index.json         · manifest · {generated_at, files_analyzed, by_lang}
    README.md          · top-level project summary + entrypoints
    by-language/
        python.md      · all .py files · grouped by directory · first
                         docstring + exported symbols
        javascript.md  · .js / .jsx / .mjs · first JSDoc / exports
        typescript.md  · .ts / .tsx · same
        go.md / rust.md / java.md · same shape
    by-folder/
        <relpath>.md   · per-directory summary for top-level dirs
    docs.md            · existing user docs index (.md files outside
                         ``.echo-wiki/`` itself)

Design rules
------------

* **Static analysis only.** No LLM call · the wiki is "good enough" out
  of the box · LLM enrichment is a follow-up the user can opt into.
* **Bounded.** Walk caps at 5000 files / 50 MB total · skips
  ``node_modules`` / ``.git`` / ``__pycache__`` / common build dirs ·
  runs in <2s on typical repos.
* **Idempotent.** Re-running overwrites existing files · safe to
  invoke from a file-watcher on every save.
* **Self-contained.** No imports from ``scripts/gen_wiki.py`` · this
  module is the single owner of the generic path.
"""

from __future__ import annotations

import ast
import json
import re
import time
from pathlib import Path
from typing import Any

from runtime.platform.plugins.bundled.project_wiki.service import (
    OUTPUT_DIR_NAME,
    is_current_manifest,
    manifest_metadata,
)

# File extensions we know how to summarize.
_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".h": "cpp",
    ".c": "c",
    ".swift": "swift",
}

# Directories we never enter · noise that bloats the wiki without value.
_SKIP_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        "target",
        "dist",
        "build",
        ".next",
        ".nuxt",
        ".cache",
        ".echo-wiki",
        ".echo-work",
        ".idea",
        ".vscode",
        "coverage",
        "out",
        ".turbo",
        ".parcel-cache",
        ".angular",
    }
)

_MAX_FILES = 5000
_MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MB · skip larger files
_MAX_SUMMARY_LEN = 300


# ─── per-file extractors ──────────────────────────────────────


def _python_summary(text: str) -> tuple[str, list[str]]:
    """Module docstring (first paragraph) + top-level public defs."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return "", []
    doc = ast.get_docstring(tree) or ""
    summary = ""
    if doc:
        for para in doc.split("\n\n"):
            para = para.strip()
            if para:
                summary = re.sub(r"\s+", " ", para)[:_MAX_SUMMARY_LEN]
                break
    symbols: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):  # noqa: SIM102
            if not node.name.startswith("_"):
                symbols.append(node.name)
    return summary, symbols[:30]


_JSDOC_RE = re.compile(r"^\s*/\*\*([\s\S]*?)\*/", re.MULTILINE)
_JS_EXPORT_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?"
    r"(?:function|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)


def _js_like_summary(text: str) -> tuple[str, list[str]]:
    """First JSDoc block + exported names."""
    summary = ""
    m = _JSDOC_RE.search(text[:8000])
    if m:
        body = m.group(1).strip()
        # Strip leading * on each line and the @param/@returns tail
        cleaned = "\n".join(
            line.lstrip(" *").rstrip()
            for line in body.split("\n")
            if not line.lstrip(" *").startswith("@")
        )
        for para in cleaned.split("\n\n"):
            para = para.strip()
            if para:
                summary = re.sub(r"\s+", " ", para)[:_MAX_SUMMARY_LEN]
                break
    symbols = list(dict.fromkeys(_JS_EXPORT_RE.findall(text)))[:30]
    return summary, symbols


def _generic_summary(text: str) -> tuple[str, list[str]]:
    """Fallback: first non-blank lines that look like a comment header."""
    lines = []
    for line in text.split("\n")[:30]:
        s = line.strip()
        if not s:
            continue
        # Strip comment leaders for the most common languages.
        s = re.sub(r"^(//|#|--|;|/\*|\*|<!--)+\s?", "", s)
        s = re.sub(r"(\*/|-->)\s*$", "", s).strip()
        if s and not s.startswith(("import ", "package ", "use ")):
            lines.append(s)
            if len(lines) >= 3:
                break
    summary = re.sub(r"\s+", " ", " ".join(lines))[:_MAX_SUMMARY_LEN]
    return summary, []


def _summarize_file(path: Path, lang: str) -> tuple[str, list[str]]:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return f"(skipped · file > {_MAX_FILE_BYTES // 1024}KB)", []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(unreadable)", []
    if lang == "python":
        return _python_summary(text)
    if lang in ("javascript", "typescript"):
        return _js_like_summary(text)
    return _generic_summary(text)


# ─── walker ───────────────────────────────────────────────────


def _walk(root: Path) -> list[dict[str, Any]]:
    """Walk root, return per-file metadata dicts. Bounded by
    ``_MAX_FILES`` + ``_MAX_TOTAL_BYTES`` so a giant repo doesn't
    OOM the backend."""
    out: list[dict[str, Any]] = []
    total_bytes = 0
    for child in _iter(root):
        try:
            size = child.stat().st_size
        except OSError:
            continue
        if size > _MAX_FILE_BYTES:
            continue
        total_bytes += size
        if total_bytes > _MAX_TOTAL_BYTES or len(out) >= _MAX_FILES:
            break
        ext = child.suffix.lower()
        lang = _LANG_BY_EXT.get(ext)
        if lang is None and ext != ".md":
            continue
        rel = child.relative_to(root).as_posix()
        if ext == ".md":
            out.append(
                {
                    "path": rel,
                    "lang": "markdown",
                    "size": size,
                    "summary": _markdown_first_heading(child),
                    "symbols": [],
                }
            )
            continue
        summary, symbols = _summarize_file(child, lang or "generic")
        out.append(
            {
                "path": rel,
                "lang": lang,
                "size": size,
                "summary": summary,
                "symbols": symbols,
            }
        )
    return out


def _iter(root: Path):
    """Recursive file iterator that respects ``_SKIP_DIRS``."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            children = list(d.iterdir())
        except OSError:
            continue
        for c in children:
            try:
                if c.is_symlink():
                    continue
            except OSError:
                continue
            if c.is_dir():
                if c.name in _SKIP_DIRS or c.name.startswith("."):
                    continue
                stack.append(c)
            elif c.is_file():
                yield c


def _markdown_first_heading(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.split("\n")[:50]:
        s = line.strip()
        if s.startswith("#"):
            return re.sub(r"^#+\s*", "", s)[:_MAX_SUMMARY_LEN]
    return ""


# ─── renderers ────────────────────────────────────────────────


def _render_lang_md(lang: str, files: list[dict[str, Any]]) -> str:
    """One markdown page per language · grouped by top-level dir."""
    by_dir: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        head = f["path"].split("/")[0] if "/" in f["path"] else "(root)"
        by_dir.setdefault(head, []).append(f)
    out = [f"# {lang.title()} files\n", f"_{len(files)} files_\n"]
    for d in sorted(by_dir):
        out.append(f"\n## `{d}/`\n")
        for f in sorted(by_dir[d], key=lambda x: x["path"]):
            line = f"- **`{f['path']}`**"
            if f["summary"]:
                line += f" — {f['summary']}"
            out.append(line)
            if f.get("symbols"):
                out.append("  - exports: " + ", ".join(f"`{s}`" for s in f["symbols"][:10]))
    return "\n".join(out) + "\n"


def _render_readme(root: Path, files: list[dict[str, Any]]) -> str:
    by_lang: dict[str, int] = {}
    for f in files:
        by_lang[f["lang"]] = by_lang.get(f["lang"], 0) + 1
    out = [f"# {root.name}\n"]
    out.append(f"_Auto-generated wiki · {len(files)} files indexed_\n")
    out.append("\n## By language\n")
    for lang, count in sorted(by_lang.items(), key=lambda x: -x[1]):
        out.append(f"- **{lang}** — {count} files · [view](by-language/{lang}.md)")
    # Highlight likely entrypoints
    entry_patterns = (
        "main.py",
        "main.go",
        "main.rs",
        "main.js",
        "main.ts",
        "index.js",
        "index.ts",
        "index.tsx",
        "app.py",
        "server.py",
        "package.json",
        "Cargo.toml",
        "pyproject.toml",
        "go.mod",
    )
    entries = [f for f in files if f["path"].split("/")[-1] in entry_patterns]
    if entries:
        out.append("\n## Likely entrypoints\n")
        for f in entries[:10]:
            line = f"- **`{f['path']}`**"
            if f["summary"]:
                line += f" — {f['summary']}"
            out.append(line)
    return "\n".join(out) + "\n"


# ─── public API ────────────────────────────────────────────────


def wiki_dir(root: Path) -> Path:
    return root / OUTPUT_DIR_NAME


def status(root: Path) -> dict[str, Any]:
    """Cheap check used by the frontend to decide between
    "show generate CTA" vs "load docs"."""
    wd = wiki_dir(root)
    manifest = wd / "index.json"
    if not manifest.is_file():
        return {"exists": False, "status": "not_generated"}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"exists": False, "status": "corrupt"}
    current = is_current_manifest(root, data)
    return {
        "exists": True,
        "status": "current" if current else "outdated",
        "consistent": current,
        "generated_at": data.get("generated_at"),
        "files_analyzed": data.get("files_analyzed", 0),
        "by_lang": data.get("by_lang", {}),
        **manifest_metadata(root),
    }


def list_docs(root: Path) -> list[dict[str, Any]]:
    """Flat listing the WikiPanel renders into a tree."""
    wd = wiki_dir(root)
    if not wd.is_dir():
        return []
    out = []
    for md in sorted(wd.rglob("*.md")):
        rel = md.relative_to(wd).as_posix()
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


def read_doc(root: Path, rel: str) -> str:
    """Read a doc, with traversal protection."""
    base = wiki_dir(root).resolve()
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise PermissionError("path escapes wiki dir") from exc
    if not candidate.is_file():
        raise FileNotFoundError(rel)
    return candidate.read_text(encoding="utf-8", errors="replace")


def generate(root: Path) -> dict[str, Any]:
    """Walk, summarize, write. Returns a manifest dict."""
    t0 = time.time()
    files = _walk(root)
    out_dir = wiki_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_lang: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        by_lang.setdefault(f["lang"], []).append(f)

    (out_dir / "by-language").mkdir(exist_ok=True)
    for lang, lang_files in by_lang.items():
        if lang == "markdown":
            continue
        page = _render_lang_md(lang, lang_files)
        (out_dir / "by-language" / f"{lang}.md").write_text(page, encoding="utf-8")

    if "markdown" in by_lang:
        md_index = ["# User docs\n"]
        md_index.append(f"_{len(by_lang['markdown'])} markdown files in this project_\n\n")
        for f in sorted(by_lang["markdown"], key=lambda x: x["path"]):
            md_index.append(f"- `{f['path']}`" + (f" — {f['summary']}" if f["summary"] else ""))
        (out_dir / "user-docs.md").write_text("\n".join(md_index) + "\n", encoding="utf-8")

    (out_dir / "README.md").write_text(_render_readme(root, files), encoding="utf-8")

    manifest = {
        **manifest_metadata(root),
        "generated_at": int(t0),
        "elapsed_ms": int((time.time() - t0) * 1000),
        "files_analyzed": len(files),
        "by_lang": {k: len(v) for k, v in by_lang.items()},
        "root": str(root),
    }
    (out_dir / "index.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest


# ─── per-project autosync settings ─────────────────────────────


def _settings_path(root: Path) -> Path:
    return wiki_dir(root) / "settings.json"


def get_settings(root: Path) -> dict[str, Any]:
    """Return the autosync flag etc. · safe defaults when file missing."""
    p = _settings_path(root)
    if not p.is_file():
        return {"autosync": False, **manifest_metadata(root)}
    try:
        return {
            "autosync": False,
            **manifest_metadata(root),
            **json.loads(p.read_text(encoding="utf-8")),
        }
    except (OSError, json.JSONDecodeError):
        return {"autosync": False, **manifest_metadata(root)}


def set_settings(root: Path, autosync: bool) -> dict[str, Any]:
    """Persist per-project wiki settings."""
    wd = wiki_dir(root)
    wd.mkdir(parents=True, exist_ok=True)
    payload = {**manifest_metadata(root), "autosync": bool(autosync)}
    _settings_path(root).write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    return payload


# ─── autosync · file watcher ───────────────────────────────────
#
# When ``set_settings(root, autosync=True)`` flips on, we register a
# ``watchdog`` Observer for that root. File changes (debounced ~3s)
# trigger a regen. Setting autosync=False stops + unregisters.
#
# Why watchdog: cross-platform, OS-native (ReadDirectoryChangesW on
# Windows, FSEvents on mac, inotify on Linux) · no busy polling.
# Why debounce: a "save" in an IDE is usually multiple events
# (truncate + rewrite + rename of swap file) · regenerating once per
# event would thrash. 3s is enough for an editor's atomic-save dance.
#
# Why filter ``_SKIP_DIRS`` + ``.echo-wiki/``: the regen WRITES
# to ``.echo-wiki/``, which fires more events, which would loop
# forever. Skipping the same dirs the walker skips keeps the trigger
# set tiny + relevant.

import logging as _logging
import threading as _threading

_LOG = _logging.getLogger("echo.wiki.watcher")

_DEBOUNCE_SECONDS = 3.0


class _WatcherManager:
    """Singleton registry of one watchdog.Observer per active root.

    Keyed by canonical (resolved) root path so repeated calls with
    different path representations of the same dir don't double-watch.
    """

    def __init__(self) -> None:
        self._lock = _threading.Lock()
        self._watchers: dict[str, Any] = {}  # root_str → handler ref
        self._observer: Any | None = None

    def _ensure_observer(self) -> Any:
        if self._observer is not None:
            return self._observer
        try:
            from watchdog.observers import Observer  # type: ignore[import]
        except ImportError:
            _LOG.warning("watchdog not installed · autosync disabled")
            return None
        obs = Observer()
        obs.daemon = True
        obs.start()
        self._observer = obs
        return obs

    def is_watching(self, root: Path) -> bool:
        with self._lock:
            return str(root.resolve()) in self._watchers

    def start(self, root: Path) -> bool:
        try:
            from watchdog.events import FileSystemEventHandler  # type: ignore[import]
        except ImportError:
            return False
        observer = self._ensure_observer()
        if observer is None:
            return False
        key = str(root.resolve())
        with self._lock:
            if key in self._watchers:
                return True

        # Per-root debounced regen handler. ``threading.Timer`` is the
        # simplest way to coalesce a burst of events into a single call.
        class _Handler(FileSystemEventHandler):  # type: ignore[misc]
            def __init__(self, target_root: Path) -> None:
                super().__init__()
                self.root = target_root
                self.timer: Any = None
                self.tlock = _threading.Lock()

            def _should_ignore(self, p: str) -> bool:
                """True if the path is in a dir we never index · keeps
                the regen-on-write feedback loop closed."""
                parts = Path(p).parts
                for part in parts:
                    if part in _SKIP_DIRS or (part.startswith(".") and part != "."):
                        return True
                return False

            def _trigger(self) -> None:
                try:
                    generate(self.root)
                    _LOG.info("autosync: regenerated wiki for %s", self.root)
                except Exception as exc:  # noqa: BLE001
                    _LOG.warning(
                        "autosync: regen failed for %s: %s",
                        self.root,
                        exc,
                    )

            def on_any_event(self, event: Any) -> None:  # noqa: ANN401
                src = getattr(event, "src_path", "") or ""
                if not src or self._should_ignore(src):
                    return
                with self.tlock:
                    if self.timer is not None:
                        self.timer.cancel()
                    self.timer = _threading.Timer(
                        _DEBOUNCE_SECONDS,
                        self._trigger,
                    )
                    self.timer.daemon = True
                    self.timer.start()

        handler = _Handler(root)
        try:
            watch = observer.schedule(handler, str(root), recursive=True)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("autosync: observer.schedule failed for %s: %s", root, exc)
            return False
        with self._lock:
            self._watchers[key] = (handler, watch)
        return True

    def stop(self, root: Path) -> bool:
        if self._observer is None:
            return False
        key = str(root.resolve())
        with self._lock:
            entry = self._watchers.pop(key, None)
        if entry is None:
            return False
        _, watch = entry
        try:
            self._observer.unschedule(watch)
        except (OSError, ValueError):  # noqa: BLE001
            return False
        return True


_WATCHER_MANAGER = _WatcherManager()


def watcher_set(root: Path, on: bool) -> bool:
    """Idempotent · True if the desired state is now active."""
    if on:
        return _WATCHER_MANAGER.start(root)
    return _WATCHER_MANAGER.stop(root)


def watcher_status(root: Path) -> bool:
    return _WATCHER_MANAGER.is_watching(root)


def boot_existing_watchers(search_dirs: list[Path] | None = None) -> int:
    """Scan a list of candidate workspace dirs at backend startup ·
    re-arm watchers for any whose ``settings.json`` has ``autosync=true``.

    The frontend persists workspace_path per-thread in localStorage,
    so the backend doesn't have a definitive list of "all known
    workspaces". For now we accept an explicit list (the launcher
    can pass known recent dirs) · pass empty/None to skip auto-boot.
    """
    if not search_dirs:
        return 0
    count = 0
    for d in search_dirs:
        try:
            if not d.is_dir():
                continue
            if get_settings(d).get("autosync") and _WATCHER_MANAGER.start(d):
                count += 1
        except (OSError, ValueError):  # noqa: BLE001
            continue
    return count


__all__ = [
    "wiki_dir",
    "status",
    "list_docs",
    "read_doc",
    "generate",
    "get_settings",
    "set_settings",
    "watcher_set",
    "watcher_status",
    "boot_existing_watchers",
]
