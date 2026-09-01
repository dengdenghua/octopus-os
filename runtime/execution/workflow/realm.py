"""Workflow script realm: AST contract + restricted execution (dsh ``realm.ts``).

The model writes a Python orchestration script against a small hook
vocabulary (``agent`` / ``phase`` / ``log`` / ``parallel`` / ``pipeline``
plus the plain-data ``args`` global). The AST contract keeps the script
inside that vocabulary:

* imports, class definitions, generators and async iteration are rejected;
* attribute access is allowed ONLY for non-dunder names (``args.items()``,
  ``s.strip()`` work on plain data) — every introspection escape
  (``x.__class__``, ``f.__globals__``, ``x.__mro__``) is statically
  rejected;
* the builtins table is a small allowlist with no ``open`` / ``import`` /
  ``eval`` / ``getattr`` / ``vars`` / ``globals`` / ``type``.

The script executes in a subprocess worker (``worker.py``), so the AST
layer is the contract, not the only boundary — like dsh, execution is
containment rather than a security boundary by itself.
"""

from __future__ import annotations

import ast
import builtins
from typing import Any

from .types import WorkflowError

# Small allowlist: data manipulation only. No I/O, no imports, no
# introspection, no attribute metaprogramming.
WORKFLOW_BUILTINS: dict[str, Any] = {
    name: getattr(builtins, name)
    for name in (
        "abs",
        "all",
        "any",
        "bin",
        "bool",
        "chr",
        "dict",
        "enumerate",
        "filter",
        "float",
        "frozenset",
        "hex",
        "int",
        "isinstance",
        "len",
        "list",
        "map",
        "max",
        "min",
        "oct",
        "ord",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
        # A script may raise its own errors; the worker maps them to an
        # ``error`` stop reason.
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "RuntimeError",
        "NotImplementedError",
    )
}

_FORBIDDEN_NODES: dict[type[ast.AST], str] = {
    ast.Import: "imports are not supported",
    ast.ImportFrom: "imports are not supported",
    ast.ClassDef: "class definitions are not supported",
    ast.Yield: "generators are not supported",
    ast.YieldFrom: "generators are not supported",
    ast.AsyncFor: "async iteration is not supported",
    ast.AsyncWith: "async with is not supported",
}

_DUNDER = "__"


def _dunder(parts: tuple[str, ...]) -> bool:
    return any(p.startswith(_DUNDER) and p.endswith(_DUNDER) for p in parts)


class _ContractVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[str] = []

    def _reject(self, node: ast.AST, message: str) -> None:
        self.violations.append(f"line {node.lineno}: {message}")

    def visit(self, node: ast.AST) -> None:
        for node_type, message in _FORBIDDEN_NODES.items():
            if isinstance(node, node_type):
                self._reject(node, message)
                return
        if isinstance(node, ast.Attribute) and _dunder((node.attr,)):
            self._reject(node, 'attribute access to "__..." dunder names is not supported')
            return
        if isinstance(node, ast.Name) and _dunder((node.id,)):
            self._reject(node, 'bare "__..." dunder names are not supported')
            return
        super().visit(node)


def validate_script(body: str, *, name: str = "workflow") -> None:
    """Parse and contract-check a workflow script body.

    Raises :class:`WorkflowError` (``SCRIPT_PARSE``) with every violation.
    This is the host-side pre-parse so ``start()`` can throw synchronously
    before a run exists; the worker re-validates defensively.
    """
    try:
        tree = ast.parse(body, filename=f"workflow:{name}")
    except SyntaxError as exc:
        raise WorkflowError(
            f"workflow script does not parse: {exc.msg} (line {exc.lineno})",
            "SCRIPT_PARSE",
            cause=exc,
        ) from exc
    visitor = _ContractVisitor()
    visitor.visit(tree)
    if visitor.violations:
        raise WorkflowError(
            "workflow script violates the supported subset: " + "; ".join(visitor.violations),
            "SCRIPT_PARSE",
        )


def check_meta_statement(body: str, *, name: str = "workflow") -> None:
    """Reject a top-level ``meta = {...}`` assignment with a pointed error.

    dsh's model-facing tool carries ``export const meta`` in the body
    instead of the request field; our analog is a top-level ``meta``
    assignment. The meta block rides the tool's ``meta`` parameter.
    """
    try:
        tree = ast.parse(body, filename=f"workflow:{name}")
    except SyntaxError:
        return  # validate_script reports the real parse error
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "meta":
                    raise WorkflowError(
                        "workflow meta rides the `meta` request field, not the "
                        "script: remove the top-level `meta = {...}` statement "
                        "from the body",
                        "SCRIPT_PARSE",
                    )


def wrap_body(body: str) -> str:
    """Wrap the body so top-level ``return`` and ``await`` are legal.

    Execution reports line numbers shifted by +1 (the wrapper line).
    """
    indented = "\n".join(f"    {line}" if line.strip() else line for line in body.splitlines())
    return f"async def __workflow_main():\n{indented}"


def build_globals(hooks: dict[str, Any], args: Any) -> dict[str, Any]:
    """Restricted globals for one script execution.

    ``__builtins__`` is pinned to the allowlist — CPython injects the real
    builtins when the key is absent, which would defeat the contract.
    """
    globals_dict: dict[str, Any] = dict(hooks)
    if args is not None:
        globals_dict["args"] = args
    globals_dict["__builtins__"] = dict(WORKFLOW_BUILTINS)
    return globals_dict


def materialize_json(value: Any) -> Any:
    """Best-effort JSON materialization check for a script return value.

    Raises :class:`WorkflowError` (``RESULT_UNSERIALIZABLE``) when the
    value is not plain JSON data.
    """
    import json

    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkflowError(
            "the workflow's return value is not plain JSON data — "
            f"{exc}. Return only JSON-serializable objects/arrays/scalars.",
            "RESULT_UNSERIALIZABLE",
            cause=exc,
        ) from exc
    return value


__all__ = [
    "WORKFLOW_BUILTINS",
    "build_globals",
    "check_meta_statement",
    "materialize_json",
    "validate_script",
    "wrap_body",
]
