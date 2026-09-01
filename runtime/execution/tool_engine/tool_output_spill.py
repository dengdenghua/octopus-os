"""Session-scoped spill storage for oversized plain-text tool results.

Ported from DeepSeek Harness' spill capability family
(``@deepseek-ai/dsh-spill`` / ``spill-local`` / ``spill-policy``): instead of
only pruning the middle of an over-budget tool result, the FULL text is saved
to a private session-scoped spill file and the model-facing result becomes a
bounded head/tail preview plus a locator and retrieval hint. The model can
still read the full output on demand (``read_file`` with offset/limit), so
errors and final answers at the tail stay reachable, not just visible.

Design mirrors dsh:

- ``save_text_spill`` persists the full text verbatim and returns an opaque
  locator, exact byte length, and model-facing retrieval guidance.
- Storage is scoped by session: ``<root>/session-<sha256-prefix>/<random>-<safeName>``.
- Private directory (0700), exclusive owner-only write (``O_EXCL`` + 0o600),
  unpredictable random prefix (defeats symlink planting in a shared root), and
  ``encode_segment`` is injective and traversal-proof.
- Best-effort policy: any storage failure logs a warning and keeps the inline
  result; a spill failure never turns a successful call into an error.
- Preview mechanics: the notice's byte cost is reserved inside the cap, so the
  replacement (preview + blank line + notice) never exceeds ``max_inline_bytes``;
  UTF-8 boundaries are preserved at each cut; when the notice alone cannot fit,
  the policy keeps the inline result (never emits a replacement over the cap).
- ``read_file`` results are skipped to avoid a ``read -> spill -> read again``
  loop.

The spill policy defaults ON in Echo because long-running agents must be
able to retrieve omitted output instead of rerunning expensive tools. Operators
can set ``ECHO_TOOL_SPILL=0`` for ephemeral/stateless deployments. The cap
defaults to the same budget as the pruner threshold, so when both are on the
spill replacement (preview + notice, at or under the cap) always fits before
the pruner runs.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)

# Master switch for the spill policy. On by default so eligible oversized
# results retain a retrievable full-text owner. The storage root is private and
# session-scoped; set ``ECHO_TOOL_SPILL=0`` to opt out.
TOOL_RESULT_SPILL_ENABLED = os.environ.get("ECHO_TOOL_SPILL", "1") != "0"

DEFAULT_SPILL_MAX_INLINE_BYTES = 8192
_SPILL_CAP_ENV = "ECHO_TOOL_SPILL_MAX_INLINE_BYTES"

# Optional configured spill root; falls back to a lazily created private temp
# dir per process (dsh's ``privateRoot``).
SPILL_ROOT = os.environ.get("ECHO_TOOL_SPILL_ROOT")

# ``read_file`` is precisely the tool that produces huge logs; spilling its
# result would invite a ``read -> spill -> read again`` loop (dsh skips
# ``read`` in the model-facing arm for the same reason).
SKIP_SPILL_TOOLS = frozenset({"read_file"})

_RETRIEVAL_HINT = "Use read_file with offset/limit, or grep_text this path to search within it."

# Characters kept literal by ``encode_segment`` (dsh's safe charset).
_SAFE_SEGMENT_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def _read_max_inline_bytes() -> int:
    raw = os.environ.get(_SPILL_CAP_ENV, "")
    if raw == "":
        return DEFAULT_SPILL_MAX_INLINE_BYTES
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{_SPILL_CAP_ENV} must be an integer (got {raw!r})") from exc
    if value < 0:
        raise ValueError(f"{_SPILL_CAP_ENV} must be non-negative (got {raw!r})")
    return value


DEFAULT_SPILL_MAX_INLINE_BYTES = _read_max_inline_bytes()


@dataclass(frozen=True, slots=True)
class SpillRef:
    """A saved spill artifact: locator, byte length, and retrieval guidance."""

    locator: str
    bytes: int
    retrieval_hint: str


def encode_segment(raw: str) -> str:
    """Encode an arbitrary string as one safe path segment, injectively.

    Neutralizes ``../``, absolute paths, NUL, and separators before any
    filesystem use. Each code unit is kept literal (``[A-Za-z0-9._-]``, minus
    ``~``) or escaped as ``~XXXX``; ``~`` is itself escaped, so the mapping is
    reversible and distinct inputs never collide. The whole-segment tokens
    ``.``/``..`` are escaped so they can never traverse. An empty string
    encodes to ``~`` (never an empty segment). Mirrors dsh's ``encodeSegment``.
    """
    if raw == "":
        return "~"
    if raw == ".":
        return "~002E"
    if raw == "..":
        return "~002E~002E"
    out: list[str] = []
    for ch in raw:
        if ch != "~" and ch in _SAFE_SEGMENT_CHARS:
            out.append(ch)
        else:
            out.append(f"~{ord(ch):04X}")
    return "".join(out)


def session_spill_dir(root: str | Path, session_key: str) -> Path:
    """The session-scoped directory: ``<root>/session-<sha256-prefix>``."""
    digest = hashlib.sha256(session_key.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    return Path(root) / f"session-{digest}"


_default_root: str | None = None


def default_spill_root() -> str:
    """The default spill root: a private (0700) per-process temp directory.

    Predictable world-readable paths would let other local users read spilled
    tool output or pre-create symlinks; ``mkdtemp`` gives an unpredictable
    suffix and 0700 semantics. Mirrors dsh's ``privateRoot``.
    """
    global _default_root
    if _default_root is None:
        _default_root = tempfile.mkdtemp(prefix="echo-spill-")
    return _default_root


def save_text_spill(
    *,
    session_key: str,
    content: str,
    suggested_name: str,
    root: str | None = None,
) -> SpillRef:
    """Persist ``content`` to a session-scoped spill file and return its ref.

    The filename is a random hex prefix plus the sanitized ``suggestedName``,
    so it is unpredictable AND readable. The write is exclusive + owner-only
    (``O_EXCL`` + 0o600): it fails on any pre-existing path — symlink or not —
    so a pre-planted target cannot redirect the write. Raises ``OSError`` on a
    real storage failure (permissions, ENOSPC); the policy caller decides how
    to degrade (best-effort keeps the inline result).
    """
    root_path = Path(root or SPILL_ROOT or default_spill_root())
    directory = session_spill_dir(root_path, session_key)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    safe_name = encode_segment(suggested_name)
    locator = directory / f"{os.urandom(6).hex()}-{safe_name}"
    data = content.encode("utf-8")
    fd = os.open(locator, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except BaseException:
        # Never leave a half-written spill file behind a failed save.
        with suppress(OSError):
            os.unlink(locator)
        raise
    return SpillRef(
        locator=str(locator),
        bytes=len(data),
        retrieval_hint=_RETRIEVAL_HINT,
    )


def head_tail_preview(text: str, budget_bytes: int) -> tuple[str, int]:
    """Return ``(preview, omitted_bytes)`` splitting budget across both ends.

    The head gets ``ceil(budget/2)`` bytes and the tail ``floor(budget/2)``,
    mirroring dsh's ``preview``. UTF-8 boundaries are preserved at each cut:
    the head is trimmed of a trailing partial codepoint and the tail of leading
    continuation bytes, so the preview never carries a replacement char from
    the cut itself. ``omitted_bytes`` is exact (total minus actually returned
    bytes, after boundary trims).
    """
    if budget_bytes <= 0:
        return "", len(text.encode("utf-8"))
    head_bytes = math.ceil(budget_bytes / 2)
    tail_bytes = math.floor(budget_bytes / 2)
    return head_tail_preview_bytes(
        text,
        head_bytes=head_bytes,
        tail_bytes=tail_bytes,
    )


def head_tail_preview_bytes(
    text: str,
    *,
    head_bytes: int,
    tail_bytes: int,
) -> tuple[str, int]:
    """Return ``(preview, omitted_bytes)`` keeping ``head_bytes + tail_bytes``.

    The head keeps the first ``head_bytes`` and the tail the final
    ``tail_bytes``. UTF-8 boundaries are preserved at each cut (the head is
    trimmed of a trailing partial codepoint and the tail of leading
    continuation bytes), so the preview never carries a replacement char from
    the cut itself. ``omitted_bytes`` is exact (total minus actually returned
    bytes, after boundary trims). Shared by the spill preview and the
    session-reference projection (dsh ``output-retention`` TextRetainer).
    """
    if head_bytes <= 0 and tail_bytes <= 0:
        return "", len(text.encode("utf-8"))
    data = text.encode("utf-8")
    total = len(data)
    if total <= head_bytes + tail_bytes:
        return text, 0
    head = data[:head_bytes] if head_bytes else b""
    tail = data[total - tail_bytes :] if tail_bytes else b""
    head_text = head.decode("utf-8", errors="ignore")
    tail_text = _decode_tail_slice(tail)
    preview = head_text + tail_text
    omitted = total - len(preview.encode("utf-8"))
    return preview, omitted


def _decode_tail_slice(data: bytes) -> str:
    """Decode a tail slice after dropping leading continuation bytes."""
    i = 0
    while i < len(data) and data[i] & 0xC0 == 0x80:
        i += 1
    return data[i:].decode("utf-8", errors="ignore")


def spill_notice(omitted_bytes: int, ref: SpillRef) -> str:
    """The one-line spill notice (no preview, no leading blank line)."""
    return (
        f"(Omitted {omitted_bytes} bytes. Full formatted result stored at: "
        f"{ref.locator}. {ref.retrieval_hint})"
    )


def _session_key_from_context() -> str | None:
    """The current turn's session key, or ``None`` outside a session scope."""
    try:
        from runtime.platform.process.session import current_session
    except ImportError:
        return None
    session = current_session()
    if session is None:
        return None
    return session.thread_id or session.conversation_id or session.turn_id or None


def maybe_spill_text(
    text: str,
    *,
    max_inline_bytes: int | None = None,
    session_key: str | None = None,
    tool_name: str = "tool",
    suggested_name: str | None = None,
    root: str | None = None,
    enabled: bool | None = None,
) -> str | None:
    """Spill ``text`` and return a bounded replacement, or ``None`` to keep inline.

    ``None`` means the original text should be used untouched: the policy is
    disabled, the tool is in ``SKIP_SPILL_TOOLS``, the text is within the cap,
    there is no session owner, storage failed (best-effort), or no within-cap
    replacement exists. A successful replacement is at most
    ``max_inline_bytes`` UTF-8 bytes: preview + blank line + notice, with the
    notice's byte cost reserved inside the cap (dsh's invariant).
    """
    if enabled is None:
        enabled = TOOL_RESULT_SPILL_ENABLED
    if not enabled:
        return None
    if tool_name in SKIP_SPILL_TOOLS:
        return None
    if max_inline_bytes is None:
        max_inline_bytes = DEFAULT_SPILL_MAX_INLINE_BYTES
    if (
        not isinstance(max_inline_bytes, int)
        or isinstance(max_inline_bytes, bool)
        or max_inline_bytes < 0
    ):
        raise ValueError(
            f"maybe_spill_text: max_inline_bytes ({max_inline_bytes!r}) must be a non-negative integer"
        )

    total_bytes = len(text.encode("utf-8"))
    if total_bytes <= max_inline_bytes:
        return None
    if session_key is None:
        session_key = _session_key_from_context()
    if not session_key:
        LOGGER.warning("tool spill: no session owner for %s; keeping the inline content", tool_name)
        return None

    try:
        ref = save_text_spill(
            session_key=session_key,
            content=text,
            suggested_name=suggested_name or f"{tool_name}.txt",
            root=root,
        )
    except OSError as exc:
        # Best-effort: a storage failure must never fail the call or hide the
        # content — keep the original inline.
        LOGGER.warning(
            "tool spill: saveText failed for %s: %s; keeping the inline content",
            tool_name,
            exc,
        )
        return None

    # Reserve the notice's byte cost INSIDE the cap. The reservation uses the
    # worst-case omission count (the full byte total): its digit count bounds
    # the real count's, so the reserved size is a safe upper bound and the
    # final notice is never longer than what we reserved. ``\n\n`` is the
    # 2-byte join.
    reserve = len(spill_notice(total_bytes, ref).encode("utf-8")) + 2
    preview_budget = max(0, max_inline_bytes - reserve)
    preview_text, omitted = head_tail_preview(text, preview_budget)
    notice = spill_notice(omitted, ref)
    replaced = f"{preview_text}\n\n{notice}" if preview_text else notice
    # Invariant: the policy NEVER emits a replacement larger than the cap. When
    # the notice alone exceeds max_inline_bytes (a tiny cap or a long spill
    # root), there is no within-cap replacement, so keep the inline content.
    if len(replaced.encode("utf-8")) > max_inline_bytes:
        LOGGER.warning(
            "tool spill: spill notice for %s exceeds max_inline_bytes; keeping the inline content",
            tool_name,
        )
        return None
    return replaced


__all__ = [
    "DEFAULT_SPILL_MAX_INLINE_BYTES",
    "SKIP_SPILL_TOOLS",
    "SPILL_ROOT",
    "SpillRef",
    "TOOL_RESULT_SPILL_ENABLED",
    "default_spill_root",
    "encode_segment",
    "head_tail_preview",
    "maybe_spill_text",
    "save_text_spill",
    "session_spill_dir",
    "spill_notice",
]
