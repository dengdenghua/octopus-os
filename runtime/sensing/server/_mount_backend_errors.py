"""Error types raised by the mount backends.

Extracted from ``mount_backend.py`` (god-file reduction) into a leaf
module so the adapter modules can import them without any circular
dependency.
"""

from __future__ import annotations


class BackendUnavailableError(RuntimeError):
    """Raised when an optional backend dependency is missing."""


class BackendError(RuntimeError):
    """Generic backend error (transport failure, malformed response, …)."""


__all__ = ["BackendError", "BackendUnavailableError"]
