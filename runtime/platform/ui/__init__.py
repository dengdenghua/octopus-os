"""Built-in Web UI entry points."""

from __future__ import annotations

from typing import Any


def create_app(*args: Any, **kwargs: Any) -> Any:
    """Import FastAPI only when callers actually create the app."""
    try:
        from .app import create_app as _create_app
    except ModuleNotFoundError as exc:
        if exc.name == "fastapi":
            raise RuntimeError("FastAPI is required to create the Web UI app") from exc
        raise
    return _create_app(*args, **kwargs)


__all__ = ["create_app"]
