from __future__ import annotations

# ╔════════════════════════════════════════════════════════════════════════╗
# ║ code_intelligence_skills.py · navigation map.                          ║
# ║                                                                        ║
# ║   Pure helpers (analysis / AST walk / index / diff) live in            ║
# ║   ``_code_intel_helpers`` and are re-imported below.                   ║
# ║   ``register_code_intelligence_skills`` lives in                       ║
# ║   ``_code_intel_handlers`` and is imported at the BOTTOM of this file   ║
# ║   (after all handlers are defined) so the submodule can resolve them.  ║
# ║                                                                        ║
# ║   §1 code_analyze (AST + structure)                                    ║
# ║   §2 embedder + persisted index                                        ║
# ║   §3 ast_search (pattern + scope)                                      ║
# ║   §4 code_search (semantic + text fallback)                            ║
# ║   §5 code_edit_diff (unified diff application)                         ║
# ║   §6 code_find_symbol (grep-based locator)                             ║
# ║   §7 code_dependency_graph (import graph)                              ║
# ║   §8 register_code_intelligence_skills → _code_intel_handlers          ║
# ╚════════════════════════════════════════════════════════════════════════╝
import ast
import re
from pathlib import Path
from typing import Any

from ._code_intel_helpers import (
    _analyze_generic,
    _analyze_python,
    _apply_unified_diff,
    _build_index,
    _expand_brace_glob,
    _fallback_text_search,
    _guess_language,
    _walk_ast_for_query,
)

# ═══════════════════════════════════════════════════════════
# §1 code_analyze (AST + structure)
# ═══════════════════════════════════════════════════════════


def _code_analyze(
    path: str = "",
    *,
    content: str = "",
    language: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    if not content and path:
        p = Path(path)
        if not p.exists():
            return {"error": f"file not found: {path}"}
        if p.stat().st_size > 500_000:
            return {"error": "file too large (>500KB)"}
        content = p.read_text(encoding="utf-8", errors="replace")

    if not content:
        return {"error": "no content to analyze"}

    if not language:
        language = _guess_language(path)

    if language == "python":
        return _analyze_python(content, path)
    return _analyze_generic(content, path, language)


# ═══════════════════════════════════════════════════════════
# §2 embedder + persisted index
# ═══════════════════════════════════════════════════════════

_EMBEDDER = None
_INDEX: list[tuple[str, str, Any]] = []
_INDEX_DIR: str = ""
_INDEX_DB_PATH = Path("data/code_index.db")


def _get_embedder():
    """The index embedder, via the shared configurable backend so the BUILD and
    the react-grounding QUERY use the SAME model — and the whole stack can point
    at one local Ollama / OpenAI-compatible endpoint (``ECHO_EMBED_URL``),
    unifying with echo-storage. Returns an ``.encode``-compatible object, or
    ``None`` when no backend is available (caller falls back to text search)."""
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    from runtime.memory.hemolymph.embedding_backend import get_encoder

    _EMBEDDER = get_encoder()
    return _EMBEDDER


def _load_persisted_index() -> list[tuple[str, str, Any]]:
    if not _INDEX_DB_PATH.exists():
        return []
    try:
        import sqlite3

        import numpy as np

        conn = sqlite3.connect(str(_INDEX_DB_PATH))
        rows = conn.execute("SELECT path, chunk, embedding FROM code_chunks").fetchall()
        conn.close()
        return [(r[0], r[1], np.frombuffer(r[2], dtype=np.float32)) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def _save_persisted_index(index: list[tuple[str, str, Any]]) -> None:
    try:
        import sqlite3

        _INDEX_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_INDEX_DB_PATH))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS code_chunks (path TEXT, chunk TEXT, embedding BLOB)"
        )
        conn.execute("DELETE FROM code_chunks")
        for path, chunk, emb in index:
            conn.execute(
                "INSERT INTO code_chunks VALUES (?, ?, ?)",
                (path, chunk, emb.tobytes()),
            )
        conn.commit()
        conn.close()
    except (OSError, ImportError, TypeError, ValueError):  # noqa: BLE001
        pass


# ═══════════════════════════════════════════════════════════
# §3 ast_search (pattern + scope)
# ═══════════════════════════════════════════════════════════


def _ast_search(
    *,
    query_type: str,
    target_name: str,
    root: str,
    glob: str,
    sandbox_dir: str | None,
    max_matches: int,
) -> dict[str, Any]:
    allowed = {"function_calls", "function_definitions", "class_definitions", "imports"}
    if not query_type or query_type not in allowed:
        return {
            "error": f"invalid query_type: must be one of {sorted(allowed)}",
            "error_type": "invalid_argument",
        }
    if not target_name:
        return {
            "error": "missing target_name",
            "error_type": "invalid_argument",
        }

    try:
        from .code_edit_skills import _detect_language, _get_parser
    except ImportError:
        return {
            "error": "ast_unavailable",
            "error_type": "dependency_missing",
            "hint": "Install tree-sitter language packages.",
        }
    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return {
            "error": "ast_unavailable",
            "error_type": "dependency_missing",
            "hint": "Install tree-sitter language packages.",
        }

    root_path = Path(root).resolve() if root else Path(".").resolve()
    if not root_path.is_dir():
        return {
            "error": f"root not found: {root}",
            "error_type": "invalid_argument",
        }

    sandbox_root: Path | None = None
    if sandbox_dir:
        try:
            sandbox_root = Path(sandbox_dir).resolve()
        except (OSError, ValueError):
            sandbox_root = None

    candidates: list[Path] = []
    patterns = _expand_brace_glob(glob)
    for pat in patterns:
        for p in root_path.glob(pat):
            if not p.is_file():
                continue
            if any(
                part.startswith(".") or part in ("node_modules", "__pycache__") for part in p.parts
            ):
                continue
            if sandbox_root is not None:
                try:
                    p.resolve().relative_to(sandbox_root)
                except ValueError:
                    continue
            candidates.append(p)

    seen: set[str] = set()
    files: list[Path] = []
    for p in candidates:
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        files.append(p)

    matches: list[dict[str, Any]] = []
    parser_attempted: set[str] = set()
    parser_ok: set[str] = set()
    any_parser_loaded = False

    for f in files:
        if len(matches) >= max_matches:
            break
        language = _detect_language(str(f))
        if not language:
            continue
        if language not in parser_attempted:
            parser_attempted.add(language)
            try:
                p_obj = _get_parser(language)
            except Exception:  # noqa: BLE001
                p_obj = None
            if p_obj is not None:
                parser_ok.add(language)
                any_parser_loaded = True
        if language not in parser_ok:
            continue
        try:
            parser = _get_parser(language)
            if parser is None:
                continue
            source_bytes = f.read_bytes()
            tree = parser.parse(source_bytes)
        except (OSError, UnicodeDecodeError):
            continue
        except Exception:  # noqa: BLE001
            continue

        try:
            rel_path = str(f.relative_to(root_path))
        except ValueError:
            rel_path = str(f)

        for m in _walk_ast_for_query(
            tree.root_node,
            source_bytes,
            language,
            query_type,
            target_name,
        ):
            m["path"] = rel_path.replace("\\", "/")
            matches.append(m)
            if len(matches) >= max_matches:
                break

    # If no parser ever loaded successfully AND tree-sitter import worked,
    # we likely have files but no language packages installed for them.
    # Still return a useful (empty) result rather than erroring.
    result: dict[str, Any] = {
        "matches": matches,
        "count": len(matches),
        "backend": "ast",
        "query_type": query_type,
        "target_name": target_name,
    }
    if any_parser_loaded:
        result["languages"] = sorted(parser_ok)
    elif files:
        result["note"] = "no parser available for any file in scope"
    else:
        result["note"] = "no files matched glob"
    return result


# ═══════════════════════════════════════════════════════════
# §4 code_search (semantic + text fallback)
# ═══════════════════════════════════════════════════════════


def _code_search(
    pattern: str = "",
    *,
    query: str = "",
    mode: str = "regex",
    query_type: str = "",
    target_name: str = "",
    root: str = "",
    directory: str = ".",
    extensions: str = ".py,.ts,.tsx,.js,.go,.rs,.java",
    glob: str = "**/*.{py,ts,tsx,js,jsx}",
    sandbox_dir: str | None = None,
    max_matches: int = 200,
    top_k: int = 10,
    **_kw: Any,
) -> dict[str, Any]:
    # AST structural search mode
    if mode == "ast":
        return _ast_search(
            query_type=query_type,
            target_name=target_name,
            root=root or directory or ".",
            glob=glob,
            sandbox_dir=sandbox_dir,
            max_matches=max_matches,
        )

    # Legacy regex/embedding mode (the "query" / "pattern" param)
    effective_query = query or pattern
    if not effective_query:
        return {"error": "missing query"}

    embedder = _get_embedder()
    if embedder is None:
        return _fallback_text_search(effective_query, directory, extensions, top_k)

    global _INDEX, _INDEX_DIR
    exts = set(extensions.split(","))
    if not _INDEX or directory != _INDEX_DIR:
        _INDEX = _load_persisted_index()
        if not _INDEX or directory != _INDEX_DIR:
            _INDEX = _build_index(embedder, directory, exts)
            _save_persisted_index(_INDEX)
        _INDEX_DIR = directory

    if not _INDEX:
        return {"results": [], "count": 0, "backend": "embedding", "note": "no files indexed"}

    import numpy as np

    q_emb = embedder.encode([effective_query])[0]
    scores = []
    for path, chunk, emb in _INDEX:
        sim = float(np.dot(q_emb, emb) / (np.linalg.norm(q_emb) * np.linalg.norm(emb) + 1e-9))
        scores.append((sim, path, chunk))
    scores.sort(reverse=True)

    results = [{"path": p, "score": round(s, 4), "snippet": c[:300]} for s, p, c in scores[:top_k]]
    return {"results": results, "count": len(results), "backend": "embedding"}


# ═══════════════════════════════════════════════════════════
# §5 code_edit_diff (unified diff application)
# ═══════════════════════════════════════════════════════════


def _code_edit_diff(
    path: str = "",
    *,
    diff: str = "",
    search: str = "",
    replace: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    if not path:
        return {"error": "missing path"}
    p = Path(path)
    if not p.exists():
        return {"error": f"file not found: {path}"}

    content = p.read_text(encoding="utf-8", errors="replace")

    if search:
        if search not in content:
            return {"error": f"search text not found in {path}", "path": path}
        new_content = content.replace(search, replace, 1)
        p.write_text(new_content, encoding="utf-8")
        return {
            "ok": True,
            "path": path,
            "mode": "search_replace",
            "changes": 1,
        }

    if diff:
        try:
            new_content = _apply_unified_diff(content, diff)
            p.write_text(new_content, encoding="utf-8")
            return {"ok": True, "path": path, "mode": "unified_diff"}
        except ValueError as e:
            return {"error": str(e), "path": path}

    return {"error": "provide either 'diff' or 'search'+'replace'"}


# ═══════════════════════════════════════════════════════════
# §6 code_find_symbol (grep-based locator)
# ═══════════════════════════════════════════════════════════


def _code_find_symbol(
    symbol: str = "",
    *,
    directory: str = ".",
    extensions: str = ".py",
    **_kw: Any,
) -> dict[str, Any]:
    if not symbol:
        return {"error": "missing symbol name"}

    root = Path(directory)
    if not root.is_dir():
        return {"error": f"directory not found: {directory}"}

    exts = set(extensions.split(","))
    results: list[dict] = []

    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in exts:
            continue
        if any(part.startswith(".") or part in ("node_modules", "__pycache__") for part in p.parts):
            continue
        if p.stat().st_size > 500_000:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if p.suffix == ".py":
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name == symbol:
                        results.append(
                            {
                                "path": str(p.relative_to(root)),
                                "line": node.lineno,
                                "kind": "function",
                                "signature": f"def {node.name}({', '.join(a.arg for a in node.args.args)})",
                            }
                        )
                elif isinstance(node, ast.ClassDef):
                    if node.name == symbol:
                        results.append(
                            {
                                "path": str(p.relative_to(root)),
                                "line": node.lineno,
                                "kind": "class",
                            }
                        )
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == symbol:
                            results.append(
                                {
                                    "path": str(p.relative_to(root)),
                                    "line": node.lineno,
                                    "kind": "variable",
                                }
                            )
        else:
            for i, line in enumerate(content.splitlines(), 1):
                if re.search(rf"\b{re.escape(symbol)}\b", line):
                    results.append(
                        {
                            "path": str(p.relative_to(root)),
                            "line": i,
                            "kind": "reference",
                            "snippet": line.strip()[:150],
                        }
                    )

        if len(results) >= 50:
            break

    return {"symbol": symbol, "definitions": results, "count": len(results)}


# ═══════════════════════════════════════════════════════════
# §7 code_dependency_graph (import graph)
# ═══════════════════════════════════════════════════════════


def _code_dependency_graph(
    directory: str = ".",
    *,
    package: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    root = Path(directory)
    if not root.is_dir():
        return {"error": f"directory not found: {directory}"}

    nodes: list[dict] = []
    edges: list[dict] = []
    file_modules: dict[str, str] = {}

    for p in sorted(root.rglob("*.py")):
        if any(part.startswith(".") or part in ("node_modules", "__pycache__") for part in p.parts):
            continue
        if p.stat().st_size > 500_000:
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        mod = rel.replace("/", ".").removesuffix(".py").removesuffix(".__init__")
        file_modules[mod] = rel
        nodes.append({"id": rel, "module": mod, "lines": 0})

    for node_info in nodes:
        p = root / node_info["id"]
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        node_info["lines"] = content.count("\n") + 1
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            target_mod = ""
            if isinstance(n, ast.Import):
                for alias in n.names:
                    target_mod = alias.name
            elif isinstance(n, ast.ImportFrom):
                target_mod = n.module or ""
            if not target_mod:
                continue
            if package and not target_mod.startswith(package):
                continue
            for known_mod, known_file in file_modules.items():
                if (
                    target_mod == known_mod
                    or target_mod.startswith(known_mod + ".")
                    or target_mod.endswith("." + known_mod)
                    or target_mod.endswith("." + known_mod.split(".")[-1])
                ):
                    edges.append(
                        {
                            "source": node_info["id"],
                            "target": known_file,
                            "import": target_mod,
                        }
                    )
                    break

    unique_edges = {(e["source"], e["target"]): e for e in edges}
    return {
        "nodes": nodes[:200],
        "edges": list(unique_edges.values())[:500],
        "node_count": len(nodes),
        "edge_count": len(unique_edges),
    }


# ═══════════════════════════════════════════════════════════
# Registrar · moved to _code_intel_handlers to keep this file
# under 1000 lines.  Re-exported below so public callers are
# unaffected.  The import MUST come after all handler definitions
# above so the submodule can resolve them at load time.
# ═══════════════════════════════════════════════════════════
from ._code_intel_handlers import register_code_intelligence_skills  # noqa: E402  (after defs)

__all__ = ["register_code_intelligence_skills"]
