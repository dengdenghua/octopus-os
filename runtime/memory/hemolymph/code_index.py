"""Auto-retrieve relevant *source* chunks for planner grounding.

The wiki retriever (``repo_context``) grounds the planner in code *summaries*;
this grounds it in the actual *source*, the way Qoder pulls real code into
context. It builds a bounded BM25 index over the project's source files
(line-window chunks, no AST parse so it's fast and language-agnostic) and
returns the top chunks for a goal as ``path:line`` excerpts — no LLM, no new
dependency. Ranking reuses ``repo_context``'s BM25 + identifier-aware
tokenization, so a word goal matches camelCase / snake_case identifiers.

Cost is bounded (file / chunk caps, noise dirs pruned, large files skipped)
and the index is cached per root with a short TTL, so a hot planning loop pays
the walk once. Self-gating: no source under the root → ``None``.
"""

from __future__ import annotations

import ast
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

from runtime.memory.hemolymph.repo_context import (
    _bm25,
    _tokenize,
    is_private_agent_context_path,
)
from runtime.memory.hemolymph.semantic_code_index import search_persisted

# The chunker is language-agnostic (Python merely gets AST-aware boundaries),
# so index the source formats used by the workspace instead of silently
# dropping the frontend half of cross-stack questions.
_CODE_EXTS = frozenset(
    {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt", ".swift"}
)
_EXPLICIT_QUERY_PATH_RE = re.compile(
    r"(?<![\w./-])(?:[A-Za-z0-9_@.-]+/)+[A-Za-z0-9_@.-]+"
    r"\.(?:py|ts|tsx|js|jsx|go|rs|java|kt|swift)\b",
    re.IGNORECASE,
)
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        "site-packages",
        "docs",
        "tests",
        "test",
        "migrations",
        ".tox",
        "vendor",
        ".idea",
        ".vscode",
        "coverage",
        "htmlcov",
    }
)

_MAX_FILES = 1200
_MAX_CHUNKS = 2500
_MAX_FILE_BYTES = 200_000
_CHUNK_LINES = 50
_MAX_CHUNK_LINES = 120  # cap a single semantic chunk (huge funcs / classes)
_INDEX_TTL_S = 120.0

_CACHE_LOCK = threading.Lock()
# root -> (built_monotonic, index)
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _iter_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
            if Path(name).suffix in _CODE_EXTS and not is_private_agent_context_path(rel):
                files.append(path)
                if len(files) >= _MAX_FILES:
                    return files
    return files


def _chunk_file(path: Path, rel: str) -> list[tuple[str, int, str]]:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return []
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = source.splitlines()
    # Semantic chunks (one per function / class, real start line) beat fixed
    # line windows: a retrieved chunk is a whole unit with its signature intact,
    # not a mid-function slice. Python only → stdlib ``ast``, no new dependency.
    if path.suffix == ".py":
        ast_chunks = _ast_chunks(rel, source, lines)
        if ast_chunks:
            return ast_chunks
    return _window_chunks(rel, lines)


def _window_chunks(rel: str, lines: list[str]) -> list[tuple[str, int, str]]:
    """Fixed line-window fallback (non-Python or unparseable source)."""
    out: list[tuple[str, int, str]] = []
    for start in range(0, len(lines), _CHUNK_LINES):
        body = "\n".join(lines[start : start + _CHUNK_LINES]).strip()
        if body:
            out.append((rel, start + 1, body))
    return out


def _capped(lines: list[str], start: int, end: int) -> str:
    """Join 1-indexed line span ``[start, end]``, capping runaway units."""
    seg = lines[start - 1 : end]
    if len(seg) > _MAX_CHUNK_LINES:
        seg = [*seg[:_MAX_CHUNK_LINES], "    # …(truncated)"]
    return "\n".join(seg).strip()


def _def_chunks(rel: str, node: ast.AST, lines: list[str]) -> list[tuple[str, int, str]]:
    start = getattr(node, "lineno", 0)
    end = getattr(node, "end_lineno", start) or start
    # A large class is split into its header + one chunk per method, so a big
    # file doesn't collapse into a single diluted chunk.
    if isinstance(node, ast.ClassDef) and (end - start + 1) > _MAX_CHUNK_LINES:
        methods = [n for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
        out: list[tuple[str, int, str]] = []
        header_end = (methods[0].lineno - 1) if methods else end
        header = _capped(lines, start, header_end)
        if header:
            out.append((rel, start, header))
        for m in methods:
            m_end = getattr(m, "end_lineno", m.lineno) or m.lineno
            body = _capped(lines, m.lineno, m_end)
            if body:
                out.append((rel, m.lineno, body))
        return out
    body = _capped(lines, start, end)
    return [(rel, start, body)] if body else []


def _ast_chunks(rel: str, source: str, lines: list[str]) -> list[tuple[str, int, str]]:
    """One chunk per top-level function / class (real line numbers), plus
    module-level statements grouped into preamble chunks. ``[]`` on a syntax
    error so the caller falls back to line windows."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out: list[tuple[str, int, str]] = []
    pending: int | None = None  # 1-indexed start of an accumulating module block

    def flush(end_line: int) -> None:
        nonlocal pending
        if pending is not None:
            body = _capped(lines, pending, end_line)
            if body:
                out.append((rel, pending, body))
            pending = None

    for node in tree.body:
        start = getattr(node, "lineno", None)
        if not start:
            continue
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            flush(start - 1)
            out.extend(_def_chunks(rel, node, lines))
        elif pending is None:
            pending = start
    flush(len(lines))
    return out


def _build_index(root: Path) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    df: Counter[str] = Counter()
    total_len = 0
    for path in _iter_source_files(root):
        # Posix-style everywhere: retrieved-source headers feed prompts and
        # tests compare them literally, so Windows separators must not leak.
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        for rel_path, line, body in _chunk_file(path, rel):
            # The path joins the tokens so a filename hit counts toward the chunk.
            tf = Counter(_tokenize(f"{rel_path} {body}"))
            if not tf:
                continue
            length = sum(tf.values())
            total_len += length
            for term in tf:
                df[term] += 1
            pages.append(
                {
                    "path": rel_path,
                    "line": line,
                    "body": body,
                    "tf": tf,
                    "length": length,
                }
            )
            if len(pages) >= _MAX_CHUNKS:
                break
        if len(pages) >= _MAX_CHUNKS:
            break
    n = len(pages)
    return {
        "pages": pages,
        "df": df,
        "n": n,
        "avgdl": (total_len / n) if n else 0.0,
    }


def _default_root() -> Path:
    return Path.cwd()


def _explicit_query_paths(query: str) -> list[str]:
    """Return de-duplicated repo-relative source paths named in a query."""

    return list(
        dict.fromkeys(
            match.group(0).replace("\\", "/").strip("/")
            for match in _EXPLICIT_QUERY_PATH_RE.finditer(query or "")
        )
    )


def reciprocal_rank_fusion(rankings: list[list[str]], *, k: int = 60) -> list[str]:
    """Fuse several ranked key-lists into one order by Reciprocal Rank Fusion.

    Each key's score is ``Σ 1/(k + rank)`` across the lists it appears in (rank
    0-based, first occurrence per list wins). RRF needs no score calibration —
    it merges a BM25 ranking and a dense-vector ranking by *position*, so a chunk
    that both retrievers rank highly floats to the top even though their raw
    scores aren't comparable. Ties break by first-seen order (stable)."""
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, key in enumerate(ranking):
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            first_seen.setdefault(key, len(first_seen))
    return sorted(scores, key=lambda key: (-scores[key], first_seen[key]))


def _get_index(root: Path, *, ttl: float = _INDEX_TTL_S) -> dict[str, Any]:
    key = str(root)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None and (now - cached[0]) < ttl:
            return cached[1]
    built = _build_index(root)
    with _CACHE_LOCK:
        _CACHE[key] = (now, built)
    return built


# Structural identifiers (CamelCase / snake_case) carry cross-file signal —
# they're the symbols a chunk *uses* whose definitions likely live elsewhere.
# Bare lowercase words (locals, comment prose) don't, so we skip them.
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_IDENT_STOP = frozenset(
    {
        "self",
        "none",
        "true",
        "false",
        "return",
        "import",
        "from",
        "class",
        "async",
        "await",
        "with",
        "for",
        "while",
        "elif",
        "else",
        "try",
        "except",
        "finally",
        "raise",
        "yield",
        "lambda",
        "global",
        "nonlocal",
        "assert",
        "pass",
        "break",
        "continue",
        "print",
        "this",
        "that",
        "your",
        "list",
        "dict",
        "bool",
        "float",
        "tuple",
        "object",
        "super",
        "init",
        "args",
        "kwargs",
        "value",
        "result",
        "data",
        "item",
        "items",
        "name",
    }
)


def _salient_identifiers(text: str, *, max_n: int = 12) -> list[str]:
    """Pull the most frequent *structural* identifiers (CamelCase or snake_case)
    out of retrieved chunk bodies — the symbols worth following to their
    definitions in a second retrieval hop. Pure + deterministic."""
    counts: Counter[str] = Counter()
    for tok in _IDENT_RE.findall(text or ""):
        low = tok.lower()
        if low in _IDENT_STOP:
            continue
        structural = ("_" in tok) or tok[0].isupper() or any(c.isupper() for c in tok[1:])
        if structural:
            counts[tok] += 1
    return [tok for tok, _n in counts.most_common(max_n)]


def _render_code_chunks(
    chunks: list[dict[str, Any]],
    *,
    budget_tokens: int,
    sink: list[dict[str, str]] | None,
    strict_explicit: bool = False,
) -> str:
    per_chunk_chars = max(300, (budget_tokens * 4) // max(1, len(chunks)))
    parts = [
        (
            "EXPLICITLY REQUESTED SOURCE (bounded to the user's named files; "
            "do not infer or retrieve neighbouring files):"
            if strict_explicit
            else "RELEVANT SOURCE (auto-retrieved by relevance to the goal; some entries are "
            "dependencies the top hits reference — read the files for full context "
            "before editing):"
        )
    ]
    for item in chunks:
        body = item["body"]
        if len(body) > per_chunk_chars:
            body = body[:per_chunk_chars].rstrip() + "\n…(truncated)"
        path = str(item["path"])
        loc = f"{path}:{item['line']}" if item["line"] is not None else path
        tag = "  (dependency)" if item.get("hop") else ""
        if sink is not None:
            sink.append(
                {
                    "kind": "source",
                    "title": path.rsplit("/", 1)[-1],
                    "path": loc,
                }
            )
        parts.append(f"\n### {loc}{tag}\n{body}")
    return "\n".join(parts)


def retrieve_code_context(
    query: str,
    *,
    root: str | Path | None = None,
    budget_tokens: int = 1500,
    max_chunks: int = 3,
    ttl: float = _INDEX_TTL_S,
    _sink: list[dict[str, str]] | None = None,
    strict_explicit_paths: bool = False,
) -> str | None:
    """Return the source chunks most relevant to ``query`` as a prompt section,
    or ``None`` when there is no source or no chunk overlaps the query.

    ``_sink``: if given, the EXACT chunks chosen for the prompt are appended as
    ``{"kind": "source", "title", "path"}`` (``path`` carries ``file:line``) so
    a UI grounding chip is faithful to what was injected — no second scoring."""
    query = (query or "").strip()
    if not query:
        return None
    q_terms = list(dict.fromkeys(_tokenize(query)))
    if not q_terms:
        return None

    base = Path(root) if root is not None else _default_root()
    explicit_paths = _explicit_query_paths(query)
    path_terms = set(_tokenize(" ".join(explicit_paths)))
    content_query_terms = set(q_terms) - path_terms or set(q_terms)
    exact_chunks: list[dict[str, Any]] = []
    for requested in explicit_paths:
        candidate = (base / requested).resolve()
        try:
            in_root = candidate.is_relative_to(base.resolve())
        except (OSError, RuntimeError):
            in_root = False
        if (
            not in_root
            or not candidate.is_file()
            or candidate.suffix not in _CODE_EXTS
            or is_private_agent_context_path(requested)
        ):
            continue
        chunks = _chunk_file(candidate, requested)
        if not chunks:
            continue
        # A large named file may have many chunks. Pick the one with the
        # strongest identifier overlap (then the earliest line) while still
        # guaranteeing that every explicitly named file wins a slot even when
        # the global index hit its file cap before reaching that path.
        rel, line, body = max(
            chunks,
            key=lambda chunk: (
                len(content_query_terms & set(_tokenize(chunk[2]))),
                -int(chunk[1]),
            ),
        )
        exact_chunks.append({"path": rel, "line": line, "body": body})

    if strict_explicit_paths and explicit_paths:
        if not exact_chunks:
            return None
        return _render_code_chunks(
            exact_chunks,
            budget_tokens=budget_tokens,
            sink=_sink,
            strict_explicit=True,
        )

    idx = _get_index(base, ttl=ttl)
    if not idx["pages"] and not exact_chunks:
        return None

    scored = [(_bm25(q_terms, p, idx), p) for p in idx["pages"]]
    scored = [(s, p) for s, p in scored if s > 0]
    if not scored and not exact_chunks:
        return None
    scored.sort(key=lambda sp: (-sp[0], sp[1]["path"], sp[1]["line"]))
    bm25_chunks = [chunk for _s, chunk in scored]

    # Dense-semantic recall, FUSED with BM25 — reuses the work-mode KB's
    # persisted index (data/code_index.db). Self-gating: no index / no model →
    # ``None`` and we stay pure BM25 (byte-identical to before). When present,
    # RRF blends the two rankings at the file level: BM25 nails exact-token
    # chunks, the embedder catches files that share meaning but no literal token
    # — the synonym-bridging gap BM25 alone can't close.
    # The persisted KB index (data/code_index.db) is built for the cwd
    # workspace, so only fuse it when we're grounding THAT workspace (root
    # unset, the real react-chat path) — never when retrieving over a different
    # explicit root, where the global index would be incoherent.
    use_semantic = root is None or Path(root).resolve() == Path.cwd().resolve()
    semantic = search_persisted(query, top_k=max(6, max_chunks * 2)) if use_semantic else None
    if semantic:
        # The persisted semantic index may predate this policy.  Filter its
        # rows too, otherwise an old index can reintroduce agent-private chunks
        # even though the fresh BM25 index correctly excludes them.
        semantic = [
            row for row in semantic if not is_private_agent_context_path(str(row.get("path") or ""))
        ]
    if semantic:
        first_chunk: dict[str, dict[str, Any]] = {}
        for chunk in bm25_chunks:
            first_chunk.setdefault(str(chunk["path"]), chunk)
        sem_snippet: dict[str, str] = {}
        for r in semantic:
            sem_snippet.setdefault(str(r["path"]), str(r.get("snippet") or ""))
        fused = reciprocal_rank_fusion(
            [[str(c["path"]) for c in bm25_chunks], [str(r["path"]) for r in semantic]]
        )
        ranked_chosen: list[dict[str, Any]] = []
        for path in fused:
            if path in first_chunk:
                c = first_chunk[path]
                ranked_chosen.append({"path": path, "line": c["line"], "body": c["body"]})
            elif sem_snippet.get(path, "").strip():
                ranked_chosen.append({"path": path, "line": None, "body": sem_snippet[path]})
            if len(ranked_chosen) >= max_chunks:
                break
    else:
        ranked_chosen = [
            {"path": str(c["path"]), "line": c["line"], "body": c["body"]}
            for c in bm25_chunks[:max_chunks]
        ]

    chosen: list[dict[str, Any]] = []
    chosen_paths: set[str] = set()
    for item in [*exact_chunks, *ranked_chosen]:
        path = str(item["path"])
        if path in chosen_paths:
            continue
        chosen_paths.add(path)
        chosen.append({"path": path, "line": item.get("line"), "body": str(item.get("body") or "")})
        if len(chosen) >= max_chunks:
            break

    # ── Hop 2 · follow the code graph one step ─────────────────────────
    # The round-0 chunks USE symbols whose definitions usually live in OTHER
    # files. Extract those identifiers and run a second BM25 pass to pull the
    # files that define/use them — the deterministic half of "retrieve → read →
    # re-retrieve" (the model-driven half is its grep/read tools). BM25-only:
    # we have exact symbol names, no dense pass needed. Off with
    # ECHO_GROUNDING_HOPS=0 → byte-identical to the one-shot grounding.
    hop_chunks: list[dict[str, Any]] = []
    try:
        hops = int(os.environ.get("ECHO_GROUNDING_HOPS", "1") or "1")
    except ValueError:
        hops = 1
    if hops >= 1 and chosen:
        chosen_paths = {str(c["path"]) for c in chosen}
        idents = _salient_identifiers(" ".join(str(c["body"]) for c in chosen))
        hop_terms = list(dict.fromkeys(_tokenize(" ".join(idents))))
        if hop_terms:
            hop_scored = [
                (_bm25(hop_terms, p, idx), p)
                for p in idx["pages"]
                if str(p["path"]) not in chosen_paths
            ]
            hop_scored = [(s, p) for s, p in hop_scored if s > 0]
            hop_scored.sort(key=lambda sp: (-sp[0], sp[1]["path"], sp[1]["line"]))
            seen: set[str] = set()
            for _s, c in hop_scored:
                p = str(c["path"])
                if p in seen:
                    continue
                seen.add(p)
                hop_chunks.append({"path": p, "line": c["line"], "body": c["body"], "hop": True})
                if len(seen) >= max_chunks:
                    break

    return _render_code_chunks(
        chosen + hop_chunks,
        budget_tokens=budget_tokens,
        sink=_sink,
    )
