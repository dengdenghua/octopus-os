"""
AST-aware code editing skills · tree-sitter powered.

Provides precise code manipulation without breaking structure:
    - edit_code        · replace function/class/method by name
    - add_import       · add import statement if not exists

vs _edit_text_file (simple string replace):
    - understands code structure (functions, classes, imports)
    - handles indentation automatically
    - won't accidentally match similar text elsewhere
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from .registry import Skill, SkillRegistry

# Language parser mapping
_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
}

# Node type mapping per language
_NODE_TYPE_MAP: dict[str, dict[str, str]] = {
    "python": {
        "function": "function_definition",
        "class": "class_definition",
        "method": "function_definition",
    },
    "javascript": {
        "function": "function_declaration",
        "class": "class_declaration",
        "method": "method_definition",
    },
    "typescript": {
        "function": "function_declaration",
        "class": "class_declaration",
        "method": "method_definition",
    },
}

# Cached parsers to avoid re-creation
_parser_cache: dict[str, Any] = {}


def _get_parser(language: str):
    """Lazy-load and cache tree-sitter parser for a language."""
    if language in _parser_cache:
        return _parser_cache[language]

    from tree_sitter import Language, Parser

    try:
        if language == "python":
            from tree_sitter_python import language as lang_func
        elif language in ("javascript", "typescript"):
            # tree-sitter-typescript ships two grammars and exports
            # language_typescript / language_tsx — there is no bare `language`.
            from tree_sitter_typescript import language_typescript as lang_func
        else:
            return None
    except ImportError:
        return None

    parser = Parser(Language(lang_func()))
    _parser_cache[language] = parser
    return parser


def _detect_language(path: str) -> str | None:
    """Detect language from file extension."""
    ext = Path(path).suffix.lower()
    return _LANGUAGE_MAP.get(ext)


def _find_node_by_type_and_name(
    tree,
    node_type: str,
    name: str,
    source: bytes,
) -> Any | None:
    """Find first AST node matching type and identifier name."""
    cursor = tree.walk()

    def _walk():
        # Check current node
        node = cursor.node
        if node.type == node_type:
            for child in node.children:
                if child.type in ("identifier", "name"):
                    child_text = source[child.start_byte : child.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    if child_text == name:
                        return node

        # Visit children
        if cursor.goto_first_child():
            while True:
                result = _walk()
                if result is not None:
                    return result
                if not cursor.goto_next_sibling():
                    break
            cursor.goto_parent()
        return None

    return _walk()


def _get_indent_at_byte(source: str, byte_pos: int) -> str:
    """Extract indentation string at given byte position."""
    line_start = source.rfind("\n", 0, byte_pos) + 1
    indent = []
    for i in range(line_start, byte_pos):
        ch = source[i]
        if ch in " \t":
            indent.append(ch)
        else:
            break
    return "".join(indent)


def _auto_indent(content: str, base_indent: str) -> str:
    """Indent content to match base indentation level.

    If content has its own relative indentation, preserve the relative
    structure while shifting to the base level.
    """
    lines = content.split("\n")
    if not lines:
        return content

    # Find the minimum indentation among non-empty lines
    min_indent_len = float("inf")
    for line in lines:
        if line.strip():
            indent_len = len(line) - len(line.lstrip())
            min_indent_len = min(min_indent_len, indent_len)

    if min_indent_len == float("inf"):
        return content  # All empty lines

    if min_indent_len == 0:
        # Content has no indentation - just prepend base
        return "\n".join(base_indent + line if line.strip() else line for line in lines)

    # Remove common indentation and add base indent
    result_lines = []
    for line in lines:
        if line.strip() == "":
            result_lines.append("")
        elif len(line) >= min_indent_len:
            result_lines.append(base_indent + line[min_indent_len:])
        else:
            result_lines.append(base_indent + line.lstrip())

    return "\n".join(result_lines)


def _generate_diff(path: Path, old_text: str, new_text: str) -> str:
    """Generate unified diff between old and new text."""
    diff = list(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
        )
    )
    return "".join(diff[:50]) if diff else "no changes"


def _edit_code(
    path: str = "",
    edit_type: str = "",
    target_name: str = "",
    new_content: str = "",
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    """AST-aware code editor.

    Parameters
    ----------
    path        : file path
    edit_type   : 'function' | 'class' | 'method'
    target_name : name of function/class/method to edit
    new_content : replacement code (will be auto-indented)
    """
    from runtime.execution.suckers.write_skills import _ensure_sandbox

    if not path:
        return {"error": "missing path"}
    if not edit_type:
        return {"error": "missing edit_type"}
    if not target_name:
        return {"error": "missing target_name"}

    resolved, err = _ensure_sandbox(path, sandbox_dir)
    if err:
        return {"error": err}
    if not resolved.exists():
        return {"error": f"not found: {resolved}"}

    language = _detect_language(str(resolved))
    if not language:
        return {
            "error": f"unsupported file type: {resolved.suffix}",
            "supported": list(_LANGUAGE_MAP.keys()),
        }

    parser = _get_parser(language)
    if not parser:
        return {
            "error": f"parser not available for {language}",
            "hint": "pip install tree-sitter tree-sitter-python tree-sitter-typescript",
        }

    try:
        source_bytes = resolved.read_bytes()
        source_text = source_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"error": f"read_failed: {e}"}

    try:
        tree = parser.parse(source_bytes)
    except Exception as e:
        return {"error": f"parse_failed: {e}"}

    node_type_map = _NODE_TYPE_MAP.get(language, {})
    node_type = node_type_map.get(edit_type)
    if not node_type:
        return {
            "error": f"unknown edit_type: {edit_type}",
            "supported": list(node_type_map.keys()),
        }

    target_node = _find_node_by_type_and_name(tree, node_type, target_name, source_bytes)
    if not target_node:
        return {
            "error": f"{edit_type} '{target_name}' not found in file",
            "suggestion": "use read_file to check exact name",
        }

    # Get indentation of the target node
    indent_str = _get_indent_at_byte(source_text, target_node.start_byte)

    # Auto-indent new_content to match target
    indented_content = _auto_indent(new_content, indent_str)

    # Build new source
    new_source = (
        source_text[: target_node.start_byte]
        + indented_content
        + source_text[target_node.end_byte :]
    )

    # Generate diff for preview
    diff_text = _generate_diff(resolved, source_text, new_source)

    try:
        resolved.write_bytes(new_source.encode("utf-8"))
    except OSError as e:
        return {"error": f"write_failed: {e}"}

    changed_lines = len(
        [
            d
            for d in diff_text.split("\n")
            if d.startswith(("+", "-")) and not d.startswith(("+++", "---"))
        ]
    )

    return {
        "path": str(resolved),
        "edit_type": edit_type,
        "target_name": target_name,
        "lines_changed": changed_lines,
        "diff_preview": diff_text,
    }


def _add_import(
    path: str = "",
    import_statement: str = "",
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Add an import statement if it doesn't already exist.

    Automatically places it in the correct location (after existing imports).
    """
    from runtime.execution.suckers.write_skills import _ensure_sandbox

    if not path:
        return {"error": "missing path"}
    if not import_statement:
        return {"error": "missing import_statement"}

    resolved, err = _ensure_sandbox(path, sandbox_dir)
    if err:
        return {"error": err}
    if not resolved.exists():
        return {"error": f"not found: {resolved}"}

    try:
        source_text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"error": f"read_failed: {e}"}

    # Check if already imported
    if import_statement.strip() in source_text:
        return {
            "path": str(resolved),
            "action": "skipped",
            "reason": "import already exists",
        }

    # Find insertion point: after last import, or at top
    lines = source_text.split("\n")
    last_import_line = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            last_import_line = i

    if last_import_line >= 0:
        lines.insert(last_import_line + 1, import_statement)
    else:
        # Insert after any shebang/module docstring, or at very top
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.startswith("#!") or line.startswith('"""') or line.startswith("'''"):
                insert_idx = i + 1
        lines.insert(insert_idx, import_statement)

    new_source = "\n".join(lines)

    try:
        resolved.write_bytes(new_source.encode("utf-8"))
    except OSError as e:
        return {"error": f"write_failed: {e}"}

    return {
        "path": str(resolved),
        "action": "added",
        "import": import_statement,
    }


# ═══════════════════════════════════════════════════════════
# Registration
# ═══════════════════════════════════════════════════════════


def _propose_patch(
    path: str = "",
    unified_diff: str = "",
    *,
    dry_run: bool = True,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Apply a unified-diff patch to a file (echo optimisation lane G).

    The model can emit a unified-diff describing its intended change
    instead of a write_text_file payload of the entire new file.
    For large files this saves a lot of output tokens.

    ``dry_run=True`` (default): parse + apply the patch in memory and
    return a preview (the new content's relevant region + ±10 line
    context window around each hunk), but does NOT write the file.
    ``dry_run=False``: apply and write the file.
    """
    from .registry import Skill  # noqa: F401 — keep import scope local

    if not path:
        return {"error": "missing path", "error_type": "invalid_argument"}
    if not unified_diff or not unified_diff.strip():
        return {"error": "missing unified_diff", "error_type": "invalid_argument"}

    # Reuse the proven path-guard from write_skills so the patch
    # respects sandbox / scope rules.
    from runtime.execution.suckers.write_skills import _ensure_sandbox

    resolved, err = _ensure_sandbox(path, sandbox_dir)
    if err:
        return {"error": err, "error_type": "permission_denied"}
    if not resolved.exists():
        return {"error": f"not found: {resolved}", "error_type": "not_found"}

    try:
        original = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"error": f"read_failed: {exc}", "error_type": "read_failed"}

    # Reuse the existing patch applier — keeps semantics consistent
    # across the codebase. If it raises / returns unchanged, we
    # surface a structured error rather than silently no-op.
    from runtime.execution.suckers.code_intelligence_skills import (
        _apply_unified_diff,
    )

    try:
        new_text = _apply_unified_diff(original, unified_diff)
    except Exception as exc:  # noqa: BLE001
        return {
            "error": f"patch_apply_failed: {type(exc).__name__}: {exc}",
            "error_type": "patch_apply_failed",
            "hint": (
                "Common causes: (1) hunk header line numbers don't "
                "match the file (re-read the file then regenerate the "
                "diff against current content), (2) trailing whitespace "
                "differs between your diff and the file, (3) you "
                "supplied multiple files in one diff (this skill takes "
                "one file at a time)."
            ),
        }

    if new_text == original:
        return {
            "error": "no_op_patch",
            "error_type": "invalid_argument",
            "hint": (
                "The diff produced no change. Either (a) the diff was "
                "already applied, or (b) the hunks didn't match — "
                "verify line numbers via read_file before re-trying."
            ),
        }

    # Build a preview: per-hunk start line + ±10 lines of new context.
    new_lines = new_text.splitlines()
    hunks_preview: list[dict[str, Any]] = []
    import re as _re_local

    for m in _re_local.finditer(
        r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@",
        unified_diff,
    ):
        start = max(0, int(m.group(1)) - 1)
        end = min(len(new_lines), start + 20)
        hunks_preview.append(
            {
                "start_line": start + 1,
                "preview": "\n".join(new_lines[max(0, start - 10) : end]),
            }
        )

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "path": str(resolved),
            "original_size": len(original),
            "new_size": len(new_text),
            "delta_bytes": len(new_text) - len(original),
            "hunks_preview": hunks_preview,
        }

    try:
        resolved.write_bytes(new_text.encode("utf-8"))
    except OSError as exc:
        return {
            "error": f"write_failed: {exc}",
            "error_type": "write_failed",
        }
    return {
        "ok": True,
        "dry_run": False,
        "path": str(resolved),
        "original_size": len(original),
        "new_size": len(new_text),
        "delta_bytes": len(new_text) - len(original),
        "hunks_preview": hunks_preview,
    }


def register_code_edit_skills(registry: SkillRegistry) -> int:
    """Register AST-aware code editing skills."""
    registry.register(
        Skill(
            name="edit_code",
            description=(
                "AST-aware code editor. Precisely replace a function, class, or method "
                "by name without breaking file structure. Supports Python, JavaScript, TypeScript. "
                "Parameters: path (file path), edit_type ('function'|'class'|'method'), "
                "target_name (name to find), new_content (replacement code)."
            ),
            affinity=["code", "edit"],
            cost_profile="low",
            trusted_source="skill://public/edit_code",
            handler=_edit_code,
            tests=[],
        )
    )
    registry.register(
        Skill(
            name="add_import",
            description=(
                "Add an import statement to a file if it doesn't already exist. "
                "Automatically places it after existing imports. "
                "Parameters: path (file path), import_statement (e.g. 'import os' or 'from typing import List')."
            ),
            affinity=["code", "edit"],
            cost_profile="low",
            trusted_source="skill://public/add_import",
            handler=_add_import,
            tests=[],
        )
    )
    registry.register(
        Skill(
            name="propose_patch",
            description=(
                "用途: 用 unified-diff 格式把改动应用到文件; 大改时比 "
                "write_text_file 省 70% output tokens。\n"
                "何时不用: 简单单点改用 edit_file (old_string/new_string); "
                "新建文件用 write_text_file。\n"
                "关键参数: path, unified_diff, dry_run (默认 True 仅预览, "
                "False 真写)。\n"
                '示例: propose_patch({"path":"a.py","unified_diff":'
                '"--- a/a.py\\n+++ b/a.py\\n@@ -1 +1 @@\\n-old\\n+new\\n",'
                '"dry_run":false})'
            ),
            affinity=["code", "edit", "diff"],
            cost_profile="low",
            trusted_source="skill://public/propose_patch",
            handler=_propose_patch,
            tests=[],
        )
    )
    return 3
