"""Canonical permission and approval-reviewer semantics.

The persisted ``acceptEdits`` spelling is retained for compatibility with
existing clients. Its execution meaning is Codex-style automatic review:
routine work stays inside the workspace sandbox and boundary crossings are
reviewed by a separate model. Keeping this mapping in one server-owned
module prevents each execution engine from interpreting aliases differently.
"""

from __future__ import annotations

from typing import Literal

ApprovalReviewer = Literal["user", "auto_review"]


def canonical_permission_mode(value: object) -> str:
    """Normalize client aliases to the four supported permission modes."""

    raw = str(value or "").strip().replace("_", "-").casefold()
    compact = raw.replace("-", "")
    if compact in {"acceptedits", "autoreview", "approveforme"}:
        return "acceptEdits"
    if compact in {"bypasspermissions", "bypass", "yolo", "full", "fullaccess"}:
        return "bypassPermissions"
    if compact == "plan":
        return "plan"
    return "default"


def approval_reviewer_for_mode(value: object) -> ApprovalReviewer:
    """Return the server-selected reviewer for a permission mode."""

    return "auto_review" if canonical_permission_mode(value) == "acceptEdits" else "user"


def is_auto_review_mode(value: object) -> bool:
    return approval_reviewer_for_mode(value) == "auto_review"


__all__ = [
    "ApprovalReviewer",
    "approval_reviewer_for_mode",
    "canonical_permission_mode",
    "is_auto_review_mode",
]
