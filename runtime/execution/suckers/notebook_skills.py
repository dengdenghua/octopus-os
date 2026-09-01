from __future__ import annotations

import contextlib
import json
import uuid
from pathlib import Path
from typing import Any

from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

_MAX_NB_READ_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_NB_WRITE_BYTES = 5 * 1024 * 1024  # 5 MB


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


def _cell_to_wire(cell: dict[str, Any]) -> dict[str, Any]:
    src = cell.get("source", "")
    if isinstance(src, list):
        src = "".join(src)
    out: dict[str, Any] = {
        "cell_type": cell.get("cell_type", "unknown"),
        "source": src,
        "id": cell.get("id"),
    }
    if cell.get("cell_type") == "code":
        outputs = cell.get("outputs") or []
        text_outputs: list[str] = []
        for o in outputs:
            if not isinstance(o, dict):
                continue
            if "text" in o:
                t = o["text"]
                text_outputs.append("".join(t) if isinstance(t, list) else str(t))
            elif "data" in o and isinstance(o["data"], dict):
                for mime, val in o["data"].items():
                    if mime.startswith("text/"):
                        text_outputs.append("".join(val) if isinstance(val, list) else str(val))
        if text_outputs:
            out["output_text"] = "\n".join(text_outputs)[:2000]
        out["execution_count"] = cell.get("execution_count")
    return out


def _notebook_read(
    path: str,
    *,
    sandbox_dir: str | None = None,
    allow_sensitive: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    p, err = _safe_resolve(path, sandbox_dir=sandbox_dir, allow_sensitive=allow_sensitive)
    if err:
        return {"error": err, "path": path}
    if p is None or not p.exists():
        return {"error": f"not found: {path}"}
    if not p.is_file():
        return {"error": f"not a file: {path}"}
    if p.suffix.lower() != ".ipynb":
        return {"error": f"not a notebook: {path}"}

    size = p.stat().st_size
    if size > _MAX_NB_READ_BYTES:
        return {"error": f"too_large: {size} bytes (cap {_MAX_NB_READ_BYTES})"}

    try:
        nb = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"error": f"parse_failed: {exc}", "path": path}

    if not isinstance(nb, dict) or "cells" not in nb:
        return {"error": "invalid nbformat: missing 'cells'"}

    cells = [_cell_to_wire(c) for c in nb.get("cells", []) if isinstance(c, dict)]
    return {
        "path": str(p.resolve()),
        "nbformat": nb.get("nbformat"),
        "kernel": (nb.get("metadata") or {}).get("kernelspec", {}).get("name"),
        "cells": cells,
        "cell_count": len(cells),
    }


def _notebook_edit(
    path: str,
    *,
    mode: str = "replace",
    cell_id: str | None = None,
    cell_index: int | None = None,
    new_source: str = "",
    cell_type: str = "code",
    sandbox_dir: str | None = None,
    allow_sensitive: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    p, err = _safe_resolve(path, sandbox_dir=sandbox_dir, allow_sensitive=allow_sensitive)
    if err:
        return {"error": err, "path": path}
    if p is None or not p.exists():
        return {"error": f"not found: {path}"}
    if not p.is_file() or p.suffix.lower() != ".ipynb":
        return {"error": f"not a notebook: {path}"}

    try:
        nb = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"error": f"parse_failed: {exc}", "path": path}
    cells = nb.get("cells")
    if not isinstance(cells, list):
        return {"error": "invalid nbformat: 'cells' is not a list"}

    if mode not in {"replace", "insert", "delete", "append"}:
        return {"error": f"bad mode: {mode!r}"}
    if cell_type not in {"code", "markdown", "raw"}:
        return {"error": f"bad cell_type: {cell_type!r}"}

    def _find_idx() -> int | None:
        if cell_id is not None:
            for i, c in enumerate(cells):
                if isinstance(c, dict) and c.get("id") == cell_id:
                    return i
            return None
        if cell_index is not None:
            return cell_index if 0 <= cell_index < len(cells) else None
        return None

    def _new_cell() -> dict[str, Any]:
        nc: dict[str, Any] = {
            "cell_type": cell_type,
            "id": uuid.uuid4().hex[:8],
            "source": new_source,
            "metadata": {},
        }
        if cell_type == "code":
            nc["outputs"] = []
            nc["execution_count"] = None
        return nc

    if mode == "append":
        cells.append(_new_cell())
    elif mode == "replace":
        idx = _find_idx()
        if idx is None:
            return {"error": "cell not found (cell_id or cell_index required)"}
        cells[idx]["source"] = new_source
        if "cell_type" in _kw or cells[idx].get("cell_type") != cell_type:
            cells[idx]["cell_type"] = cell_type
            if cell_type == "code" and "outputs" not in cells[idx]:
                cells[idx]["outputs"] = []
    elif mode == "insert":
        idx = _find_idx()
        if idx is None:
            return {"error": "cell not found (cell_id or cell_index required)"}
        cells.insert(idx + 1, _new_cell())
    elif mode == "delete":
        idx = _find_idx()
        if idx is None:
            return {"error": "cell not found (cell_id or cell_index required)"}
        cells.pop(idx)

    serialized = json.dumps(nb, ensure_ascii=False, indent=1)
    if len(serialized.encode("utf-8")) > _MAX_NB_WRITE_BYTES:
        return {"error": f"too_large_after_edit: cap {_MAX_NB_WRITE_BYTES}"}

    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(serialized, encoding="utf-8")
        tmp.replace(p)
    except OSError as exc:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()
        return {"error": f"write_failed: {exc}", "path": path}

    return {
        "path": str(p.resolve()),
        "mode": mode,
        "cell_count": len(cells),
        "ok": True,
    }


def register_notebook_skills(registry: SkillRegistry) -> int:
    """Register notebook_read / notebook_edit. Returns 2."""
    registry.register(
        Skill(
            name="notebook_read",
            description=(
                "Read a Jupyter .ipynb · returns normalized cells with source "
                "and (for code cells) text outputs. Image / binary outputs are dropped."
            ),
            affinity=["file", "notebook"],
            cost_profile="low",
            trusted_source="skill://public/notebook_read",
            handler=_notebook_read,
            tests=[
                SkillTestCase(
                    name="missing_file_returns_error",
                    tier="golden",
                    args={"path": "/does/not/exist/xyz.ipynb"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
                SkillTestCase(
                    name="non_ipynb_returns_error",
                    tier="golden",
                    args={"path": __file__},  # this .py file
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="notebook_edit",
            description=(
                "Replace / insert / delete / append cells in a Jupyter .ipynb. "
                "Atomic write · target by cell_id or cell_index."
            ),
            affinity=["file", "notebook", "write"],
            cost_profile="low",
            trusted_source="skill://public/notebook_edit",
            handler=_notebook_edit,
            tests=[
                SkillTestCase(
                    name="missing_file_returns_error",
                    tier="golden",
                    args={"path": "/does/not/exist/xyz.ipynb", "mode": "append"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
                SkillTestCase(
                    name="bad_mode_returns_error",
                    tier="golden",
                    args={"path": __file__, "mode": "nope"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    return 2


__all__ = [
    "register_notebook_skills",
]
