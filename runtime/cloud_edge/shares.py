"""Public thread-share input contract and defensive snapshot projection."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|secret)\s*[=:]\s*)[^\s,;]+"),
)
_LOCAL_PATH_PATTERNS = (
    re.compile(r"(?<![\w:])/(?:Users|home)/[^\s<>'\"]+"),
    re.compile(r"(?i)(?<!\w)(?:[a-z]:[\\/]|\\\\)[^\s<>'\"]+"),
)
_MAX_MESSAGE_CHARS = 60_000
_MAX_TOTAL_CHARS = 1_000_000


class PublicShareMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=_MAX_MESSAGE_CHARS)


class PublicShareSnapshot(BaseModel):
    title: str = Field(default="", max_length=160)
    messages: list[PublicShareMessage] = Field(min_length=1, max_length=200)
    artifacts: list[str] = Field(default_factory=list, max_length=50)


class CreateThreadShareBody(BaseModel):
    snapshot: PublicShareSnapshot
    source_thread_id: str | None = Field(default=None, min_length=1, max_length=128)
    ttl_seconds: int | None = Field(default=None, ge=60, le=365 * 24 * 60 * 60)


class ResolveThreadShareBody(BaseModel):
    token: str = Field(min_length=24, max_length=128)


def _sanitise_text(value: Any, *, limit: int) -> str:
    text = _CONTROL_RE.sub("", str(value or "")).strip()
    for pattern in _SECRET_PATTERNS:
        text = (
            pattern.sub(r"\1[redacted]", text)
            if pattern.groups
            else pattern.sub("[redacted]", text)
        )
    for pattern in _LOCAL_PATH_PATTERNS:
        text = pattern.sub("[local path redacted]", text)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... (shared content truncated)"


def _public_basename(value: Any) -> str:
    raw = str(value or "").strip().rstrip("/\\")
    return re.split(r"[/\\]", raw)[-1] if raw else ""


def normalise_public_snapshot(value: PublicShareSnapshot | dict[str, Any]) -> dict[str, Any]:
    """Return the strict public DTO, redacting secrets and local paths again.

    The desktop is expected to send an already-sanitised snapshot, but the
    public relay is a separate trust boundary and never relies on that alone.
    """

    source = value.model_dump() if isinstance(value, PublicShareSnapshot) else value
    messages: list[dict[str, str]] = []
    total_chars = 0
    for raw in source.get("messages", []):
        if not isinstance(raw, dict) or raw.get("role") not in {"user", "assistant"}:
            continue
        content = _sanitise_text(raw.get("content"), limit=_MAX_MESSAGE_CHARS)
        if not content:
            continue
        if total_chars + len(content) > _MAX_TOTAL_CHARS:
            break
        messages.append({"role": str(raw["role"]), "content": content})
        total_chars += len(content)
    if not messages:
        raise ValueError("share snapshot has no public messages")

    title = _sanitise_text(source.get("title"), limit=160)
    if not title:
        title = next(
            (item["content"][:80].strip() for item in messages if item["role"] == "user"),
            "Shared EchoAI task",
        )

    artifacts: list[str] = []
    seen: set[str] = set()
    for raw in source.get("artifacts", []):
        name = _sanitise_text(_public_basename(raw), limit=160)
        if name and name not in seen:
            artifacts.append(name)
            seen.add(name)
        if len(artifacts) >= 50:
            break

    return {
        "title": title,
        "messages": messages,
        "artifacts": artifacts,
        "stats": {
            "turns": sum(item["role"] == "user" for item in messages),
            "messages": len(messages),
            "artifacts": len(artifacts),
        },
    }


__all__ = [
    "CreateThreadShareBody",
    "PublicShareMessage",
    "PublicShareSnapshot",
    "ResolveThreadShareBody",
    "normalise_public_snapshot",
]
