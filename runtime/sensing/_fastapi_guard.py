"""Shared guard for FastAPI-dependent routers.

Every ``create_*_router`` factory calls ``require_fastapi()`` at the top
so the error message is consistent and actionable.  Before this helper
existed, the same two-line guard was copy-pasted across 40+ router
modules with a bare ``"fastapi not installed"`` message that gave the
user no hint about which extra to install.

Lives under ``runtime/sensing/`` (not ``sensing.gateway``) so that
``execution`` and ``adapters`` packages can import it without violating
the import-direction rules enforced by ``tools/lint/import_direction_check.py``.
"""

from __future__ import annotations

try:
    import fastapi  # noqa: F401

    _FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _FASTAPI_AVAILABLE = False


def require_fastapi(caller: str = "") -> None:
    """Raise ``RuntimeError`` if fastapi is not installed.

    Pass ``__name__`` so the error message tells the user which router
    needs the dependency.
    """
    if not _FASTAPI_AVAILABLE:
        suffix = f" (required by {caller})" if caller else ""
        raise RuntimeError(
            f"fastapi not installed{suffix} — pip install 'echo-os[serve]'"
        )
