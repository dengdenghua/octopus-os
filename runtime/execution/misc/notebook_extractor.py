"""Bounded, non-executing normalization for Jupyter notebooks.

The notebook format is JSON, but a notebook may contain large source strings
and base64 encoded display data.  This module deliberately keeps only the
fields exposed by the read API and never evaluates cell code or returns binary
outputs.  It is used by the fixed document worker as well as the parent-side
wire adapter.
"""

from __future__ import annotations

import json
from typing import Any

_MAX_CELL_SOURCE_CHARS = 64 * 1024
_MAX_TEXT_OUTPUT_CHARS = 2_000
_CELL_OVERHEAD_CHARS = 256


def _string_source(value: Any) -> str:
    if isinstance(value, list):
        return "".join(item if isinstance(item, str) else str(item) for item in value)
    return value if isinstance(value, str) else str(value or "")


def _cell_to_wire(cell: dict[str, Any], *, source_budget: int) -> tuple[dict[str, Any], bool]:
    source = _string_source(cell.get("source", ""))
    take = min(len(source), _MAX_CELL_SOURCE_CHARS, max(0, source_budget))
    truncated = take < len(source)
    out: dict[str, Any] = {
        "cell_type": cell.get("cell_type", "unknown"),
        "source": source[:take],
        "id": cell.get("id") if isinstance(cell.get("id"), str) else None,
    }
    if cell.get("cell_type") == "code":
        outputs = cell.get("outputs") or []
        text_outputs: list[str] = []
        output_budget = min(_MAX_TEXT_OUTPUT_CHARS, max(0, source_budget - take))
        for output in outputs:
            if not isinstance(output, dict):
                continue
            if isinstance(output.get("text"), (str, list)):
                text_outputs.append(_string_source(output["text"]))
            elif isinstance(output.get("data"), dict):
                for mime, value in output["data"].items():
                    if isinstance(mime, str) and mime.startswith("text/"):
                        text_outputs.append(_string_source(value))
        output_text = "\n".join(text_outputs)
        if len(output_text) > output_budget:
            output_text = output_text[:output_budget]
            truncated = True
        if output_text:
            out["output_text"] = output_text[:_MAX_TEXT_OUTPUT_CHARS]
        out["execution_count"] = cell.get("execution_count")
    return out, truncated


def normalize_notebook(
    data: bytes,
    *,
    max_chars: int = 1_000_000,
    max_cells: int = 10_000,
) -> tuple[dict[str, Any], bool]:
    """Return the structured read payload and whether it was bounded.

    ``ValueError`` denotes invalid JSON or an invalid notebook shape.  The
    caller decides whether that is a user-facing parse error or a worker
    failure.  The function does not accept paths and therefore cannot escape
    the parent process's already-authorized file boundary.
    """

    if not isinstance(data, bytes) or max_chars <= 0 or max_cells <= 0:
        raise ValueError("invalid notebook extraction budget")
    try:
        notebook = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid notebook JSON") from exc
    if not isinstance(notebook, dict) or "cells" not in notebook:
        raise ValueError("invalid nbformat: missing 'cells'")
    raw_cells = notebook.get("cells")
    if not isinstance(raw_cells, list):
        raise ValueError("invalid nbformat: 'cells' is not a list")

    metadata = notebook.get("metadata")
    kernelspec = metadata.get("kernelspec") if isinstance(metadata, dict) else None
    kernel = kernelspec.get("name") if isinstance(kernelspec, dict) else None
    if not isinstance(kernel, str):
        kernel = None

    remaining = max_chars
    cells: list[dict[str, Any]] = []
    truncated = False
    for raw_cell in raw_cells[:max_cells]:
        if not isinstance(raw_cell, dict):
            continue
        if remaining <= _CELL_OVERHEAD_CHARS:
            truncated = True
            break
        cell, cell_truncated = _cell_to_wire(
            raw_cell,
            source_budget=remaining - _CELL_OVERHEAD_CHARS,
        )
        cells.append(cell)
        truncated = truncated or cell_truncated
        remaining -= len(cell.get("source", ""))
        remaining -= len(cell.get("output_text", "")) if "output_text" in cell else 0
        remaining -= _CELL_OVERHEAD_CHARS
    if len(raw_cells) > max_cells:
        truncated = True

    return (
        {
            "nbformat": notebook.get("nbformat"),
            "kernel": kernel,
            "cells": cells,
            "cell_count": len(cells),
        },
        truncated,
    )


__all__ = ["normalize_notebook"]
