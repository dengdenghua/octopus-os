"""Canonical Echo session URI and inline mention encoding.

The codec originated in DeepSeek Harness' session-reference implementation,
but the runtime protocol belongs to Echo Native. New references use the
host-neutral ``echo-session:`` scheme. The historical ``dsh-session:``
scheme remains decode-only compatible so persisted conversations and links do
not break. The URI is base64url of the JSON-encoded session id — lossless for
any opaque id — and decoding re-encodes the payload to verify canonical form.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any

from runtime.execution.tool_engine.session_reference import SessionReferenceError

SESSION_REFERENCE_SCHEME = "echo-session:"
LEGACY_SESSION_REFERENCE_SCHEMES = ("dsh-session:",)
SUPPORTED_SESSION_REFERENCE_SCHEMES = (
    SESSION_REFERENCE_SCHEME,
    *LEGACY_SESSION_REFERENCE_SCHEMES,
)

_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SCHEME_PATTERN = (
    "(?:" + "|".join(re.escape(scheme) for scheme in SUPPORTED_SESSION_REFERENCE_SCHEMES) + ")"
)
# ``@[label](uri)`` or a bare canonical/legacy URI in text.
_MENTION_PATTERN = re.compile(
    rf"@\[((?:\\.|[^\\\]])*)\]\(({_SCHEME_PATTERN}[^\s)]*)\)"
    rf"|({_SCHEME_PATTERN}[A-Za-z0-9_-]+)"
)
_LABEL_ESCAPE_RE = re.compile(r"[\\\]]")
_LABEL_UNESCAPE_RE = re.compile(r"\\(.)")


@dataclass(frozen=True, slots=True)
class ParsedSessionReferenceText:
    """Result of extracting canonical mentions from plain text."""

    text: str
    references: list[dict[str, Any]] = field(default_factory=list)


def encode_session_reference_uri(session_id: str) -> str:
    """Encode any session id as a canonical lossless Echo session URI."""
    payload = base64.urlsafe_b64encode(json.dumps(session_id).encode("utf-8")).rstrip(b"=")
    return f"{SESSION_REFERENCE_SCHEME}{payload.decode('ascii')}"


def decode_session_reference_uri(uri: str) -> str:
    """Decode a current or legacy session URI with strict payload checks."""
    scheme = next(
        (
            candidate
            for candidate in SUPPORTED_SESSION_REFERENCE_SCHEMES
            if uri.startswith(candidate)
        ),
        None,
    )
    if scheme is None:
        raise _invalid_uri(uri)
    payload = uri[len(scheme) :]
    if not _PAYLOAD_RE.match(payload):
        raise _invalid_uri(uri)
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        parsed: Any = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, str) or not parsed:
            raise TypeError("decoded session id is not a non-empty string")
        canonical_payload = encode_session_reference_uri(parsed)[len(SESSION_REFERENCE_SCHEME) :]
        if canonical_payload != payload:
            raise TypeError("URI is not canonical")
        return parsed
    except Exception as exc:  # noqa: BLE001 — wrap every decode failure
        raise _invalid_uri(uri, exc) from exc


def format_session_reference_mention(session_id: str, label: str | None = None) -> str:
    """Render a host-neutral Markdown mention carrying the canonical URI."""
    safe_label = _LABEL_ESCAPE_RE.sub(lambda m: f"\\{m.group(0)}", label or session_id)
    return f"@[{safe_label}]({encode_session_reference_uri(session_id)})"


def parse_session_reference_text(text: str) -> ParsedSessionReferenceText:
    """Extract Markdown mentions and bare canonical URIs from one text value.

    Explicit Markdown mentions fail on any malformed supported URI;
    bare text is treated as a reference only when its payload matches the
    base64url shape and is canonical. Returns the readable text (opaque
    tokens replaced by ``@label`` spans) plus structured references in
    first-appearance order, before service deduplication.
    """
    references: list[dict[str, Any]] = []

    def _replace(match: re.Match[str]) -> str:
        markdown_uri = match.group(2)
        bare_uri = match.group(3)
        uri = markdown_uri if markdown_uri is not None else bare_uri
        if uri is None:
            raise SessionReferenceError(
                "session reference URI is missing",
                "SESSION_REFERENCE_INVALID_REFERENCE",
            )
        session_id = decode_session_reference_uri(uri)
        raw_label = match.group(1)
        label = session_id if raw_label is None else _LABEL_UNESCAPE_RE.sub(r"\1", raw_label)
        references.append({"session_id": session_id, "label": label})
        return f"@{label}"

    rendered = _MENTION_PATTERN.sub(_replace, text or "")
    return ParsedSessionReferenceText(text=rendered, references=references)


def _invalid_uri(uri: str, cause: BaseException | None = None) -> SessionReferenceError:
    return SessionReferenceError(
        f"invalid session reference URI {json.dumps(uri)}",
        "SESSION_REFERENCE_INVALID_REFERENCE",
        cause=cause,
    )


__all__ = [
    "ParsedSessionReferenceText",
    "LEGACY_SESSION_REFERENCE_SCHEMES",
    "SESSION_REFERENCE_SCHEME",
    "SUPPORTED_SESSION_REFERENCE_SCHEMES",
    "decode_session_reference_uri",
    "encode_session_reference_uri",
    "format_session_reference_mention",
    "parse_session_reference_text",
]
