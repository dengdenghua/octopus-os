from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

_MAX_GLOB_RESULTS = 500  # Implementation note.
_DEFAULT_GREP_FILES = 5_000  # Implementation note.
_MAX_GREP_FILES = 20_000  # Implementation note.
_DEFAULT_GREP_MATCHES = 100  # Keep model-context payloads bounded by default.
_MAX_GREP_MATCHES = 500  # Implementation note.
_MAX_GREP_FILE_BYTES = 1_024 * 1024  # Implementation note.
_MAX_TREE_NODES = 1_000  # Implementation note.
_MAX_TREE_DEPTH = 8  # Implementation note.
_MAX_RANGE_LINES = 2_000  # Implementation note.
_SEARCH_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
    }
)


def _is_search_excluded(path: Path, base: Path) -> bool:
    """Skip hidden/generated dependency trees, not merely hidden basenames."""
    try:
        relative_parts = path.relative_to(base).parts
    except ValueError:
        relative_parts = path.parts
    return any(part.startswith(".") or part in _SEARCH_EXCLUDED_DIRS for part in relative_parts)


def _expand_brace_patterns(pattern: str) -> tuple[str, ...]:
    """Expand the common ``*.{ts,tsx}`` form that pathlib does not support."""
    match = re.search(r"\{([^{}]+)\}", pattern)
    if match is None:
        return (pattern,)
    choices = [choice.strip() for choice in match.group(1).split(",") if choice.strip()]
    if not choices:
        return (pattern,)
    expanded: list[str] = []
    for choice in choices:
        candidate = pattern[: match.start()] + choice + pattern[match.end() :]
        expanded.extend(_expand_brace_patterns(candidate))
    return tuple(dict.fromkeys(expanded))


def _safe_resolve(
    path: str,
    *,
    sandbox_dir: str | None = None,
    allow_sensitive: bool = False,
) -> tuple[Path | None, str | None]:
    from runtime.safety.auth.path_guard import check_path

    verdict = check_path(
        path,
        sandbox_dir=sandbox_dir,
        allow_sensitive=allow_sensitive,
    )
    if not verdict.allow:
        return None, f"path_blocked: {verdict.reason}"
    return Path(verdict.resolved) if verdict.resolved else Path(path), None


# ─── handlers ────────────────────────────────────────────────


def _glob_files(
    pattern: str | None = None,
    root: str = ".",
    *,
    sandbox_dir: str | None = None,
    allow_sensitive: bool = False,
    max_results: int = _MAX_GLOB_RESULTS,
    include_dirs: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    if not pattern or not str(pattern).strip():
        return {"error": "missing required 'pattern' (e.g. '**/*.py')"}
    base, err = _safe_resolve(root, sandbox_dir=sandbox_dir, allow_sensitive=allow_sensitive)
    if err:
        return {"error": err, "root": root}
    if base is None or not base.exists():
        return {"error": f"not found: {root}"}
    if not base.is_dir():
        return {"error": f"not a directory: {root}"}

    cap = max(1, min(int(max_results), _MAX_GLOB_RESULTS))
    matches: list[Path] = []
    try:
        seen: set[Path] = set()
        for expanded_pattern in _expand_brace_patterns(pattern):
            for p in base.glob(expanded_pattern):
                if p in seen or _is_search_excluded(p, base):
                    continue
                seen.add(p)
                if not include_dirs and p.is_dir():
                    continue
                matches.append(p)
                if len(matches) >= cap * 4:
                    break
            if len(matches) >= cap * 4:
                break
    except Exception as exc:  # noqa: BLE001
        return {"error": f"glob_failed: {exc}", "pattern": pattern}

    try:
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        matches.sort(key=lambda p: str(p))
    truncated = len(matches) > cap
    matches = matches[:cap]

    files = [
        {
            "path": str(p.relative_to(base) if p.is_relative_to(base) else p),
            "abs_path": str(p.resolve()),
            "is_dir": p.is_dir(),
        }
        for p in matches
    ]
    return {
        "root": str(base.resolve()),
        "pattern": pattern,
        "files": files,
        "count": len(files),
        "truncated": truncated,
    }


def _grep_text(
    pattern: str = "",
    root: str = ".",
    *,
    query: str = "",
    path: str = "",
    glob: str = "**/*",
    ignore_case: bool = False,
    max_matches: int = _DEFAULT_GREP_MATCHES,
    max_files: int = _DEFAULT_GREP_FILES,
    sandbox_dir: str | None = None,
    allow_sensitive: bool = False,
    context_lines: int = 0,
    **_kw: Any,
) -> dict[str, Any]:
    effective_pattern = str(pattern or query or "")
    if not effective_pattern:
        return {"error": "missing pattern", "pattern": effective_pattern}
    # ``root`` may be injected by the workspace executor even when the model
    # supplied the narrower provider-style ``path`` alias.  The explicit path
    # must win; otherwise a one-file lookup silently scans the whole repo.
    effective_root = path or root
    base, err = _safe_resolve(
        effective_root,
        sandbox_dir=sandbox_dir,
        allow_sensitive=allow_sensitive,
    )
    if err:
        return {"error": err, "root": effective_root}
    if base is None or not base.exists():
        return {"error": f"not found: {effective_root}"}
    search_base = base if base.is_dir() else base.parent

    try:
        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(effective_pattern, flags)
    except re.error as exc:
        return {"error": f"bad_regex: {exc}", "pattern": effective_pattern}

    match_cap = max(1, min(int(max_matches), _MAX_GREP_MATCHES))
    file_cap = max(1, min(int(max_files), _MAX_GREP_FILES))
    # Context lines around each match (ripgrep -C). Capped at 10 to
    # keep payload bounded — anything beyond that and the user should
    # read_file the affected region.
    ctx = max(0, min(int(context_lines), 10))

    scanned = 0
    matches: list[dict[str, Any]] = []
    truncated = False
    truncation_reason = ""

    try:
        if base.is_file():
            candidates: Any = iter((base,))
        else:
            seen: set[Path] = set()

            def _candidates() -> Any:
                for expanded_glob in _expand_brace_patterns(glob):
                    for candidate in base.glob(expanded_glob):
                        if candidate in seen:
                            continue
                        seen.add(candidate)
                        yield candidate

            candidates = _candidates()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"bad_glob: {exc}", "glob": glob}

    for p in candidates:
        if not p.is_file() or _is_search_excluded(p, search_base):
            continue
        if scanned >= file_cap:
            truncated = True
            truncation_reason = "file_limit"
            break
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > _MAX_GREP_FILE_BYTES:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        lines = text.splitlines()
        for lineno, line in enumerate(lines, start=1):
            if regex.search(line):
                snippet = line if len(line) <= 500 else line[:497] + "..."
                rel = p.relative_to(search_base) if p.is_relative_to(search_base) else p
                entry: dict[str, Any] = {
                    "path": str(rel),
                    "line": lineno,
                    "text": snippet,
                }
                if ctx > 0:
                    # 0-indexed window into ``lines``. ripgrep-style:
                    # ``before`` lists [-ctx .. -1], ``after`` lists
                    # [+1 .. +ctx]. Each entry is {line, text}.
                    start = max(0, (lineno - 1) - ctx)
                    end = min(len(lines), lineno + ctx)
                    before = [
                        {
                            "line": j + 1,
                            "text": (lines[j] if len(lines[j]) <= 500 else lines[j][:497] + "..."),
                        }
                        for j in range(start, lineno - 1)
                    ]
                    after = [
                        {
                            "line": j + 1,
                            "text": (lines[j] if len(lines[j]) <= 500 else lines[j][:497] + "..."),
                        }
                        for j in range(lineno, end)
                    ]
                    if before:
                        entry["before"] = before
                    if after:
                        entry["after"] = after
                matches.append(entry)
                if len(matches) >= match_cap:
                    truncated = True
                    truncation_reason = "match_limit"
                    break
        if truncated:
            break

    return {
        "root": str(base.resolve()),
        "pattern": effective_pattern,
        "glob": glob,
        "scanned_files": scanned,
        "matches": matches,
        "count": len(matches),
        "truncated": truncated,
        "truncation_reason": truncation_reason,
        "context_lines": ctx,
    }


def _tree(
    root: str = ".",
    *,
    max_depth: int = 3,
    max_nodes: int = _MAX_TREE_NODES,
    include_hidden: bool = False,
    sandbox_dir: str | None = None,
    allow_sensitive: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    base, err = _safe_resolve(root, sandbox_dir=sandbox_dir, allow_sensitive=allow_sensitive)
    if err:
        return {"error": err, "root": root}
    if base is None or not base.is_dir():
        return {"error": f"not a directory: {root}"}

    depth_cap = max(1, min(int(max_depth), _MAX_TREE_DEPTH))
    node_cap = max(1, min(int(max_nodes), _MAX_TREE_NODES))

    nodes_emitted = [0]  # list so inner function can mutate
    truncated = [False]

    def _build(d: Path, depth: int) -> dict[str, Any]:
        node: dict[str, Any] = {"name": d.name, "is_dir": True}
        if depth >= depth_cap:
            node["children_truncated"] = "depth_limit"
            return node
        children: list[dict[str, Any]] = []
        try:
            entries = sorted(d.iterdir(), key=lambda e: (not e.is_dir(), e.name))
        except OSError:
            return node
        for e in entries:
            if nodes_emitted[0] >= node_cap:
                truncated[0] = True
                node["children_truncated"] = "node_limit"
                break
            if not include_hidden and e.name.startswith("."):
                continue
            nodes_emitted[0] += 1
            if e.is_dir():
                children.append(_build(e, depth + 1))
            else:
                try:
                    size = e.stat().st_size
                except OSError:
                    size = None
                children.append({"name": e.name, "is_dir": False, "size": size})
        node["children"] = children
        return node

    root_node = _build(base, 0)
    return {
        "root": str(base.resolve()),
        "tree": root_node,
        "nodes": nodes_emitted[0],
        "truncated": truncated[0],
    }


def _read_file_range(
    path: str,
    *,
    offset: int = 1,
    limit: int = 200,
    sandbox_dir: str | None = None,
    allow_sensitive: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    base, err = _safe_resolve(path, sandbox_dir=sandbox_dir, allow_sensitive=allow_sensitive)
    if err:
        return {"error": err, "path": path}
    if base is None or not base.exists():
        return {"error": f"not found: {path}"}
    if not base.is_file():
        return {"error": f"not a file: {path}"}

    start = max(1, int(offset))
    cap = max(1, min(int(limit), _MAX_RANGE_LINES))
    # Streaming window read: iterate the file once and keep only the requested
    # slice, instead of reading the whole file into memory then slicing. This
    # keeps both time and memory proportional to the file size (it must be
    # scanned to count total_lines / detect truncation) but never materializes
    # the full text, avoiding slow/giant reads that used to surface as tool
    # timeouts on large files.
    want_lo = start - 1
    want_hi = start - 1 + cap  # exclusive
    lines: list[str] = []
    total = 0
    try:
        with base.open("r", encoding="utf-8", errors="strict", newline="") as fh:
            for line in fh:
                total += 1
                if want_lo <= total - 1 < want_hi:
                    lines.append(line.rstrip("\r\n"))
    except UnicodeDecodeError:
        return {"error": "non-utf8 content", "path": path}
    except OSError as exc:
        return {"error": f"read_failed: {exc}", "path": path}

    end = min(total, start - 1 + cap)
    sliced = lines
    return {
        "path": str(base.resolve()),
        "total_lines": total,
        "offset": start,
        "returned_lines": len(sliced),
        "end_line": end,
        "truncated": end < total,
        "content": "\n".join(sliced),
    }


# ─── registration ────────────────────────────────────────────


def register_fs_search_skills(registry: SkillRegistry) -> int:
    """Register glob_files / grep_text / tree / read_file_range.

    Returns number of skills registered (always 4)."""
    registry.register(
        Skill(
            name="glob_files",
            description=(
                "用途: 按 glob (支持 ** 递归) 列文件，按 mtime 倒序返回；用于「找所有 *.py」「找最新改的 .md」之类。\n"
                "何时不用: 要按内容 / 正则找用 grep_text；只看一个目录的直接子项用 list_cwd；要看完整树形结构用 tree；知道精确路径直接 read_file。\n"
                "关键参数: pattern (必填, 例如 '**/*.py'); root (默认 '.'); max_results (默认 500, 上限 500); include_dirs (默认 False, 默认只返回文件)。\n"
                '示例: glob_files({"pattern": "runtime/**/*.py", "root": "."})'
            ),
            affinity=["file", "search"],
            cost_profile="low",
            trusted_source="skill://public/glob_files",
            handler=_glob_files,
            tests=[
                SkillTestCase(
                    name="missing_root_returns_error",
                    tier="golden",
                    args={"pattern": "*.py", "root": "/definitely/does/not/exist/zzz"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
                SkillTestCase(
                    name="glob_returns_expected_shape",
                    tier="golden",
                    args={"pattern": "*.py", "root": "."},
                    expect=SkillExpect(schema_keys=["files", "count", "pattern"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="grep_text",
            description=(
                "用途: 在文本文件里跑 Python 正则搜内容 (不限于代码 — 配置 / 文档 / 日志都行)；返回 [{path, line, text}] 行级匹配。\n"
                "何时不用: 只想按文件名 / 路径找用 glob_files；要读完整文件用 read_file；要做语义级代码检索用 code_search / lsp_skills；二进制或 >1MB 的文件会被自动跳过。\n"
                "关键参数: pattern (必填, Python re); root (默认 '.'); path (可选, 精确文件/目录且优先于注入的 root); glob (默认 '**/*'); ignore_case (默认 False); max_matches (默认 100, 上限 500); max_files (默认 5000, 上限 20000)。默认跳过 node_modules、dist、build、coverage、虚拟环境和隐藏目录。\n"
                '示例: grep_text({"pattern": "def register_", "root": "runtime", "glob": "**/*.py"})'
            ),
            affinity=["file", "search", "text"],
            cost_profile="mid",
            trusted_source="skill://public/grep_text",
            handler=_grep_text,
            tests=[
                SkillTestCase(
                    name="bad_regex_returns_error",
                    tier="golden",
                    args={"pattern": "[unclosed", "root": "."},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
                SkillTestCase(
                    name="valid_search_returns_matches_shape",
                    tier="golden",
                    args={"pattern": "^$|.*", "root": ".", "glob": "*.py", "max_matches": 5},
                    expect=SkillExpect(schema_keys=["matches", "count", "scanned_files"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="tree",
            description=(
                "用途: 递归打出目录结构 (默认 3 层, 上限 8 层 / 1000 节点)；用于第一次进项目时建立全局认知。\n"
                "何时不用: 只看一层用 list_cwd 更省 token；按 pattern 找文件用 glob_files；按内容找用 grep_text；要文件元数据用 file_stats。\n"
                "关键参数: root (默认 '.'); max_depth (默认 3, 上限 8); max_nodes (默认 1000); include_hidden (默认 False)。\n"
                '示例: tree({"root": "runtime", "max_depth": 2})'
            ),
            affinity=["file", "io"],
            cost_profile="low",
            trusted_source="skill://public/tree",
            handler=_tree,
            tests=[
                SkillTestCase(
                    name="missing_root_returns_error",
                    tier="golden",
                    args={"root": "/definitely/does/not/exist/zzz"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
                SkillTestCase(
                    name="valid_tree_shape",
                    tier="golden",
                    args={"root": ".", "max_depth": 1},
                    expect=SkillExpect(schema_keys=["tree", "nodes", "root"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="read_file_range",
            description=(
                "用途: 按 1-based 行号读文件的一段切片 (offset + limit, 上限 2000 行)；只想看「前 N 行」「某个区间」时省 token 的首选。\n"
                "何时不用: 整文件不大用 read_file 一把读完；要找内容位置用 grep_text 后再来精读；要按 pattern 找用 glob_files；二进制 / 非 UTF-8 会拒读。\n"
                "关键参数: path (必填); offset (默认 1, 起始行号); limit (默认 200, 上限 2000)。\n"
                '示例: read_file_range({"path": "runtime/execution/suckers/builtins.py", "offset": 100, "limit": 50})'
            ),
            affinity=["file", "io"],
            cost_profile="low",
            trusted_source="skill://public/read_file_range",
            handler=_read_file_range,
            tests=[
                SkillTestCase(
                    name="missing_file_returns_error",
                    tier="golden",
                    args={"path": "/definitely/does/not/exist/zzz.txt"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    return 4


__all__ = [
    "register_fs_search_skills",
]
