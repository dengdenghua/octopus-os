"""The existing bounded document extractor, exposed to the file provider."""

from __future__ import annotations

import contextvars
import importlib.util
from contextlib import contextmanager
from typing import Any

from runtime.execution.misc.document_extraction import (
    DocumentExtractionBudget,
    DocumentWorkerCleanupError,
)

_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "invoice_extraction_context", default=None
)


@contextmanager
def invoice_extraction_context(*, budget: Any, deadline: float, cancel: Any):
    """Service-owned request settings; never persisted in model/session metadata."""
    token = _context.set({"budget": budget, "deadline": deadline, "cancel": cancel})
    try:
        yield
    finally:
        _context.reset(token)


def extract_invoice_document(data: bytes, extension: str) -> dict[str, Any]:
    from runtime.execution.misc.document_extraction import extract_document_isolated

    if extension.lower().lstrip(".") == "pdf":
        try:
            available = any(
                importlib.util.find_spec(name) is not None for name in ("pypdf", "pdfplumber")
            )
        except (ImportError, ValueError, AttributeError):
            available = False
        if not available:
            return {"text": None, "truncated": False, "available": False, "outcome": "unavailable"}
    return extract_document_isolated(data, extension, **(_context.get() or {}))


__all__ = [
    "DocumentExtractionBudget",
    "DocumentWorkerCleanupError",
    "extract_invoice_document",
    "invoice_extraction_context",
]
