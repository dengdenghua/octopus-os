"""Server-owned exception contracts for bounded Loop recovery."""

from __future__ import annotations


class SafeRepairableAttemptError(RuntimeError):
    """An attempt failed before any tool or external effect was dispatched.

    Only trusted runtime integration code should raise this exact class.  The
    Loop controller checks exact type identity (subclasses and look-alike
    exception attributes are rejected) and still applies the normal bounded
    attempt limit.  Unknown exceptions remain indeterminate and fail closed.
    """


__all__ = ["SafeRepairableAttemptError"]
