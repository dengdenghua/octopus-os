"""Cross-file symbol lookup and Python import-graph analysis."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any


def find_symbol(
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
    results: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in exts:
            continue
        if any(
            part.startswith(".") or part in ("node_modules", "__pycache__") for part in path.parts
        ):
            continue
        if path.stat().st_size > 500_000:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        relative_path = str(path.relative_to(root))
        if path.suffix == ".py":
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == symbol:
                    results.append(
                        {
                            "path": relative_path,
                            "line": node.lineno,
                            "kind": "function",
                            "signature": (
                                f"def {node.name}({', '.join(arg.arg for arg in node.args.args)})"
                            ),
                        }
                    )
                elif isinstance(node, ast.ClassDef) and node.name == symbol:
                    results.append({"path": relative_path, "line": node.lineno, "kind": "class"})
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == symbol:
                            results.append(
                                {
                                    "path": relative_path,
                                    "line": node.lineno,
                                    "kind": "variable",
                                }
                            )
        else:
            for line_number, line in enumerate(content.splitlines(), 1):
                if re.search(rf"\b{re.escape(symbol)}\b", line):
                    results.append(
                        {
                            "path": relative_path,
                            "line": line_number,
                            "kind": "reference",
                            "snippet": line.strip()[:150],
                        }
                    )
        if len(results) >= 50:
            break
    return {"symbol": symbol, "definitions": results, "count": len(results)}


def dependency_graph(
    directory: str = ".",
    *,
    package: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    root = Path(directory)
    if not root.is_dir():
        return {"error": f"directory not found: {directory}"}

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    file_modules: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        if any(
            part.startswith(".") or part in ("node_modules", "__pycache__") for part in path.parts
        ):
            continue
        if path.stat().st_size > 500_000:
            continue
        relative_path = str(path.relative_to(root)).replace("\\", "/")
        module = relative_path.replace("/", ".").removesuffix(".py").removesuffix(".__init__")
        file_modules[module] = relative_path
        nodes.append({"id": relative_path, "module": module, "lines": 0})

    for node_info in nodes:
        path = root / node_info["id"]
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        node_info["lines"] = content.count("\n") + 1
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            target_module = ""
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target_module = alias.name
            elif isinstance(node, ast.ImportFrom):
                target_module = node.module or ""
            if not target_module or (package and not target_module.startswith(package)):
                continue
            for known_module, known_file in file_modules.items():
                if (
                    target_module == known_module
                    or target_module.startswith(known_module + ".")
                    or target_module.endswith("." + known_module)
                    or target_module.endswith("." + known_module.split(".")[-1])
                ):
                    edges.append(
                        {
                            "source": node_info["id"],
                            "target": known_file,
                            "import": target_module,
                        }
                    )
                    break

    unique_edges = {(edge["source"], edge["target"]): edge for edge in edges}
    return {
        "nodes": nodes[:200],
        "edges": list(unique_edges.values())[:500],
        "node_count": len(nodes),
        "edge_count": len(unique_edges),
    }


__all__ = ["dependency_graph", "find_symbol"]
