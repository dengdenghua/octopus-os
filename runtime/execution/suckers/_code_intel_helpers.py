"""Pure helper functions for code_intelligence_skills · extracted from
code_intelligence_skills.py to keep the parent file under 1000 lines.

All functions here are stateless (no module-level mutable state) and depend
only on the standard library.  The parent module re-imports them so existing
callers (``from code_intelligence_skills import _build_index`` etc.) keep
working.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

# ═══════════════════════════════════════════════════════════
# §1 code_analyze helpers (language detection + AST/regex analysis)
# ═══════════════════════════════════════════════════════════


def _guess_language(path: str) -> str:
    ext = Path(path).suffix.lower() if path else ""
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".rb": "ruby",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
    }.get(ext, "unknown")


def _analyze_python(content: str, path: str) -> dict[str, Any]:
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return {"error": f"syntax error: {e}", "language": "python"}

    functions: list[dict] = []
    classes: list[dict] = []
    imports: list[str] = []
    top_vars: list[str] = []
    calls: list[str] = []
    call_edges: list[dict] = []
    symbols: list[dict] = []

    current_scope: str = "<module>"

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            args = [a.arg for a in node.args.args]
            decorators = [
                ast.dump(d) if not isinstance(d, ast.Name) else d.id for d in node.decorator_list
            ]
            functions.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "end_line": getattr(node, "end_lineno", None),
                    "args": args,
                    "decorators": decorators[:3],
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "docstring": ast.get_docstring(node) or "",
                }
            )
            symbols.append(
                {
                    "name": node.name,
                    "kind": "function",
                    "line": node.lineno,
                    "scope": current_scope,
                }
            )
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    callee = ""
                    if isinstance(child.func, ast.Name):
                        callee = child.func.id
                    elif isinstance(child.func, ast.Attribute):
                        callee = child.func.attr
                    if callee:
                        call_edges.append(
                            {
                                "caller": node.name,
                                "callee": callee,
                                "line": getattr(child, "lineno", 0),
                            }
                        )
        elif isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
            bases = [ast.dump(b) for b in node.bases]
            classes.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "end_line": getattr(node, "end_lineno", None),
                    "methods": methods,
                    "bases": bases[:5],
                    "docstring": ast.get_docstring(node) or "",
                }
            )
            symbols.append(
                {
                    "name": node.name,
                    "kind": "class",
                    "line": node.lineno,
                    "scope": current_scope,
                }
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                imports.append(f"{mod}.{alias.name}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    top_vars.append(target.id)
                    symbols.append(
                        {
                            "name": target.id,
                            "kind": "variable",
                            "line": node.lineno,
                            "scope": "<module>",
                        }
                    )

    unique_edges = {(e["caller"], e["callee"]): e for e in call_edges}

    return {
        "path": path,
        "language": "python",
        "lines": content.count("\n") + 1,
        "functions": functions[:50],
        "classes": classes[:30],
        "imports": imports[:50],
        "top_level_vars": top_vars[:30],
        "call_graph": list(set(calls))[:50],
        "call_edges": list(unique_edges.values())[:100],
        "symbols": symbols[:100],
    }


def _analyze_generic(content: str, path: str, language: str) -> dict[str, Any]:
    functions: list[dict] = []
    classes: list[dict] = []
    imports: list[str] = []

    fn_patterns = {
        "javascript": r"(?:export\s+)?(?:async\s+)?function\s+(\w+)",
        "typescript": r"(?:export\s+)?(?:async\s+)?function\s+(\w+)",
        "go": r"func\s+(?:\([^)]*\)\s+)?(\w+)",
        "rust": r"(?:pub\s+)?fn\s+(\w+)",
        "java": r"(?:public|private|protected)?\s+\w+\s+(\w+)\s*\(",
        "ruby": r"def\s+(\w+)",
    }
    cls_patterns = {
        "javascript": r"class\s+(\w+)",
        "typescript": r"(?:export\s+)?class\s+(\w+)",
        "go": r"type\s+(\w+)\s+struct",
        "rust": r"(?:pub\s+)?struct\s+(\w+)",
        "java": r"class\s+(\w+)",
        "ruby": r"class\s+(\w+)",
    }
    import_patterns = {
        "javascript": r"import\s+.*?from\s+['\"]([^'\"]+)",
        "typescript": r"import\s+.*?from\s+['\"]([^'\"]+)",
        "go": r'"([^"]+)"',
        "rust": r"use\s+([\w:]+)",
        "java": r"import\s+([\w.]+)",
        "ruby": r"require\s+['\"]([^'\"]+)",
    }

    fn_re = fn_patterns.get(language)
    if fn_re:
        for i, line in enumerate(content.splitlines(), 1):
            m = re.search(fn_re, line)
            if m:
                functions.append({"name": m.group(1), "line": i})

    cls_re = cls_patterns.get(language)
    if cls_re:
        for i, line in enumerate(content.splitlines(), 1):
            m = re.search(cls_re, line)
            if m:
                classes.append({"name": m.group(1), "line": i})

    imp_re = import_patterns.get(language)
    if imp_re:
        imports = re.findall(imp_re, content)[:50]

    return {
        "path": path,
        "language": language,
        "lines": content.count("\n") + 1,
        "functions": functions[:50],
        "classes": classes[:30],
        "imports": imports,
    }


# ═══════════════════════════════════════════════════════════
# §3 ast_search helpers (tree-sitter node traversal)
# ═══════════════════════════════════════════════════════════


def _expand_brace_glob(pattern: str) -> list[str]:
    """Expand simple `{a,b,c}` brace alternatives in a glob into multiple patterns."""
    if "{" not in pattern or "}" not in pattern:
        return [pattern]
    start = pattern.index("{")
    end = pattern.index("}", start)
    prefix = pattern[:start]
    suffix = pattern[end + 1 :]
    options = pattern[start + 1 : end].split(",")
    out: list[str] = []
    for opt in options:
        sub = f"{prefix}{opt.strip()}{suffix}"
        out.extend(_expand_brace_glob(sub))
    return out


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _line_snippet(source: bytes, line_idx0: int, max_len: int = 200) -> str:
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if 0 <= line_idx0 < len(lines):
        return lines[line_idx0].strip()[:max_len]
    return ""


def _call_target_name(call_node: Any, source: bytes, language: str) -> str:
    func_child = call_node.child_by_field_name("function")
    if func_child is None and call_node.child_count > 0:
        func_child = call_node.children[0]
    if func_child is None:
        return ""

    if func_child.type == "identifier":
        return _node_text(func_child, source)
    if func_child.type in ("attribute", "member_expression"):
        attr = func_child.child_by_field_name("attribute") or func_child.child_by_field_name(
            "property"
        )
        if attr is not None:
            return _node_text(attr, source)
        last_ident = ""
        cursor = [func_child]
        while cursor:
            n = cursor.pop()
            if n.type in ("identifier", "property_identifier"):
                last_ident = _node_text(n, source)
            cursor.extend(n.children)
        return last_ident
    return ""


def _def_name(def_node: Any, source: bytes) -> str:
    name_node = def_node.child_by_field_name("name")
    if name_node is not None:
        return _node_text(name_node, source)
    for child in def_node.children:
        if child.type in ("identifier", "property_identifier"):
            return _node_text(child, source)
    return ""


def _import_mentions_target(import_node: Any, source: bytes, target_name: str) -> bool:
    text = _node_text(import_node, source)
    if target_name not in text:
        return False
    stack = list(import_node.children)
    while stack:
        n = stack.pop()
        if n.type in ("identifier", "dotted_name"):
            t = _node_text(n, source)
            if t == target_name or t.split(".")[-1] == target_name:
                return True
        if n.type == "aliased_import":
            for c in n.children:
                if c.type in ("identifier", "dotted_name"):
                    t = _node_text(c, source)
                    if t == target_name or t.split(".")[-1] == target_name:
                        return True
        stack.extend(n.children)
    return False


_CALL_TYPES = {
    "python": {"call"},
    "javascript": {"call_expression"},
    "typescript": {"call_expression"},
}
_FUNC_DEF_TYPES = {
    "python": {"function_definition"},
    "javascript": {"function_declaration", "method_definition", "function_expression"},
    "typescript": {"function_declaration", "method_definition", "function_expression"},
}
_CLASS_DEF_TYPES = {
    "python": {"class_definition"},
    "javascript": {"class_declaration"},
    "typescript": {"class_declaration"},
}
_IMPORT_TYPES = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_statement"},
    "typescript": {"import_statement"},
}


def _walk_ast_for_query(
    root_node: Any,
    source: bytes,
    language: str,
    query_type: str,
    target_name: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    stack = [root_node]
    while stack:
        node = stack.pop()
        try:
            node_type = node.type
        except AttributeError:
            continue

        if query_type == "function_calls" and node_type in _CALL_TYPES.get(language, set()):
            name = _call_target_name(node, source, language)
            if name == target_name:
                line0 = node.start_point[0]
                out.append(
                    {
                        "line": line0 + 1,
                        "column": node.start_point[1],
                        "kind": "call",
                        "snippet": _line_snippet(source, line0),
                    }
                )
        elif query_type == "function_definitions" and node_type in _FUNC_DEF_TYPES.get(
            language, set()
        ):
            name = _def_name(node, source)
            if name == target_name:
                line0 = node.start_point[0]
                out.append(
                    {
                        "line": line0 + 1,
                        "column": node.start_point[1],
                        "kind": "function_definition",
                        "snippet": _line_snippet(source, line0),
                    }
                )
        elif query_type == "class_definitions" and node_type in _CLASS_DEF_TYPES.get(
            language, set()
        ):
            name = _def_name(node, source)
            if name == target_name:
                line0 = node.start_point[0]
                out.append(
                    {
                        "line": line0 + 1,
                        "column": node.start_point[1],
                        "kind": "class_definition",
                        "snippet": _line_snippet(source, line0),
                    }
                )
        elif query_type == "imports" and node_type in _IMPORT_TYPES.get(language, set()):
            if _import_mentions_target(node, source, target_name):
                line0 = node.start_point[0]
                out.append(
                    {
                        "line": line0 + 1,
                        "column": node.start_point[1],
                        "kind": "import",
                        "snippet": _line_snippet(source, line0),
                    }
                )

        stack.extend(node.children)

    return out


# ═══════════════════════════════════════════════════════════
# §4 code_search helpers (index build / chunk / text fallback)
# ═══════════════════════════════════════════════════════════


def _build_index(embedder: Any, directory: str, exts: set[str]) -> list[tuple[str, str, Any]]:
    chunks: list[tuple[str, str]] = []
    root = Path(directory)
    if not root.is_dir():
        return []

    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in exts:
            continue
        if any(
            part.startswith(".") or part == "node_modules" or part == "__pycache__"
            for part in p.parts
        ):
            continue
        if p.stat().st_size > 200_000:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(p.relative_to(root))
        for chunk in _split_into_chunks(content, rel):
            chunks.append((rel, chunk))
        if len(chunks) > 5000:
            break

    if not chunks:
        return []

    texts = [c for _, c in chunks]
    embeddings = embedder.encode(texts, show_progress_bar=False, batch_size=64)
    return [(p, c, e) for (p, c), e in zip(chunks, embeddings, strict=False)]


def _split_into_chunks(content: str, path: str) -> list[str]:
    lines = content.splitlines()
    if len(lines) <= 60:
        return [f"# {path}\n{content}"] if content.strip() else []

    chunks: list[str] = []
    current: list[str] = []
    for line in lines:
        current.append(line)
        if len(current) >= 50 and (
            re.match(r"^(def |class |function |export |pub fn |func )", line.strip())
            or not line.strip()
        ):
            chunks.append(f"# {path}\n" + "\n".join(current))
            current = []
    if current:
        chunks.append(f"# {path}\n" + "\n".join(current))
    return chunks


def _fallback_text_search(
    query: str,
    directory: str,
    extensions: str,
    top_k: int,
) -> dict[str, Any]:
    results: list[dict] = []
    exts = set(extensions.split(","))
    root = Path(directory)
    if not root.is_dir():
        return {"results": [], "count": 0, "backend": "text_fallback"}

    q_lower = query.lower()
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in exts:
            continue
        if any(part.startswith(".") or part == "node_modules" for part in p.parts):
            continue
        if p.stat().st_size > 200_000:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if q_lower in content.lower():
            for i, line in enumerate(content.splitlines(), 1):
                if q_lower in line.lower():
                    results.append(
                        {
                            "path": str(p.relative_to(root)),
                            "line": i,
                            "snippet": line.strip()[:200],
                        }
                    )
                    if len(results) >= top_k:
                        return {
                            "results": results,
                            "count": len(results),
                            "backend": "text_fallback",
                        }
    return {"results": results[:top_k], "count": len(results), "backend": "text_fallback"}


# ═══════════════════════════════════════════════════════════
# §5 code_edit_diff helper (unified diff application)
# ═══════════════════════════════════════════════════════════


def _apply_unified_diff(original: str, diff_text: str) -> str:
    lines = original.splitlines(keepends=True)
    result = list(lines)
    offset = 0

    for hunk_match in re.finditer(
        r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@.*?\n((?:[+ \-].*?\n)*)",
        diff_text,
    ):
        old_start = int(hunk_match.group(1)) - 1 + offset
        hunk_lines = hunk_match.group(3).splitlines(keepends=True)

        removes = []
        adds = []
        for hl in hunk_lines:
            if hl.startswith("-"):
                removes.append(hl[1:])
            elif hl.startswith("+"):
                adds.append(hl[1:])

        for j, rem in enumerate(removes):
            idx = old_start + j
            if idx < len(result) and result[idx].rstrip("\n") == rem.rstrip("\n"):
                result[idx] = None  # type: ignore[assignment]

        insert_at = old_start
        for add in adds:
            result.insert(insert_at, add)
            insert_at += 1
            offset += 1

        offset -= len(removes)

    return "".join(line for line in result if line is not None)
