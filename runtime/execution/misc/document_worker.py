"""Fixed internal document worker. Only framed configuration and document bytes enter."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path


def _exact(stream, count: int) -> bytes:
    pieces = bytearray()
    while len(pieces) < count:
        chunk = stream.read(min(65536, count - len(pieces)))
        if not chunk:
            raise ValueError("incomplete document frame")
        pieces.extend(chunk)
    return bytes(pieces)


def _send(value: dict) -> None:
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("!I", len(payload)))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def main() -> int:
    # -I script execution excludes the current working directory and PYTHONPATH.
    # This root derives only from the installed, fixed worker source location.
    if not getattr(sys, "frozen", False) and __package__ in {"", None}:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    try:
        length = struct.unpack("!I", _exact(sys.stdin.buffer, 4))[0]
        if not 0 < length <= 16384:
            return 2
        config = json.loads(_exact(sys.stdin.buffer, length).decode("utf-8"))
        required = {
            "version",
            "extension",
            "input_bytes",
            "memory_bytes",
            "cpu_seconds",
            "max_chars",
            "max_pages",
            "max_expanded_bytes",
            "process",
        }
        if not isinstance(config, dict) or not required <= set(config) or config["version"] != 1:
            return 2
        pages = config.get("pages")
        include_page_markers = config.get("include_page_markers", False)
        if type(include_page_markers) is not bool:
            return 2
        if pages is not None and (
            config["extension"] != "pdf"
            or not isinstance(pages, list)
            or len(pages) > 200
            or any(type(page) is not int or page <= 0 for page in pages)
            or len(set(pages)) != len(pages)
        ):
            return 2
        for key in (
            "input_bytes",
            "memory_bytes",
            "cpu_seconds",
            "max_chars",
            "max_pages",
            "max_expanded_bytes",
        ):
            if type(config[key]) is not int or config[key] < (0 if key == "input_bytes" else 1):
                return 2
        if config["input_bytes"] > 16 * 1024 * 1024 or config["max_chars"] > 1_000_000:
            return 2
        if config["extension"] not in {
            "pdf",
            "docx",
            "pptx",
            "xlsx",
            "txt",
            "md",
            "csv",
            "tsv",
            "ipynb",
        }:
            return 2
        from runtime.execution.misc.document_process_limits import apply_worker_limits

        applied = apply_worker_limits(
            config["process"],
            memory_bytes=config["memory_bytes"],
            cpu_seconds=config["cpu_seconds"],
        )
    except Exception:
        _send(
            {
                "version": 1,
                "phase": "result",
                "outcome": "unavailable",
                "text": None,
                "truncated": False,
            }
        )
        return 3
    _send({"version": 1, "phase": "ready", "limits": applied})
    try:
        data = _exact(sys.stdin.buffer, config["input_bytes"])
        # Import the actual parser only after applying limits and completing setup.
        import importlib.util

        notebook = None
        page_count = None
        pages_extracted = None
        error = None
        if config["extension"] == "pdf" and not any(
            importlib.util.find_spec(name) is not None for name in ("pypdf", "pdfplumber")
        ):
            outcome, text, truncated = "unavailable", None, False
        else:
            if config["extension"] == "ipynb":
                from runtime.execution.misc.notebook_extractor import normalize_notebook

                notebook, truncated = normalize_notebook(
                    data,
                    max_chars=config["max_chars"],
                    max_cells=config["max_pages"],
                )
                outcome, text = "ok", None
            else:
                from runtime.execution.misc.document_text_extractor import extract_document_text

                result = extract_document_text(
                    data,
                    config["extension"],
                    max_chars=config["max_chars"],
                    max_pages=config["max_pages"],
                    max_expanded_bytes=config["max_expanded_bytes"],
                    pages=pages,
                    include_page_markers=include_page_markers,
                )
                outcome = "ok" if result is not None else "no_text"
                text = result.text if result is not None else None
                truncated = result.truncated if result is not None else False
                if result is not None:
                    page_count = result.page_count
                    pages_extracted = list(result.pages_extracted)
    except Exception as exc:
        from runtime.execution.misc.document_text_extractor import PageSelectionError

        if isinstance(exc, PageSelectionError):
            outcome, text, truncated = "invalid_argument", None, False
            page_count, pages_extracted, error = exc.page_count, None, str(exc)
        elif isinstance(exc, MemoryError):
            outcome, text, truncated = "resource_limited", None, False
        elif isinstance(exc, ValueError):
            # Notebook JSON/shape errors are ordinary parse failures.  Keep them
            # distinct from a crashed worker so the caller can show an actionable
            # parse error without falling back to an in-process parser.
            if config["extension"] == "ipynb":
                outcome, text, truncated = "no_text", None, False
            else:
                outcome, text, truncated = "worker_failed", None, False
        else:
            outcome, text, truncated = "worker_failed", None, False
    payload = {
        "version": 1,
        "phase": "result",
        "outcome": outcome,
        "text": text,
        "truncated": truncated,
    }
    if notebook is not None:
        payload["notebook"] = notebook
    if page_count is not None:
        payload["page_count"] = page_count
    if pages_extracted is not None:
        payload["pages_extracted"] = pages_extracted
    if error is not None:
        payload["error"] = error
    _send(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
