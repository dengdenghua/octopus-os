"""Dual-layer output buffer for shell command output.

Provides two buffering strategies:

1. ByteStreamBuffer — caps total size in bytes (default 1 MB).
   When the limit is exceeded, the oldest bytes are discarded while
   preserving UTF-16 surrogate pair boundaries so emoji and other
   4-byte characters are never corrupted.

2. LineBuffer — caps total line count (default 1000 lines).
   Handles incomplete trailing lines across chunks by deferring
   them until the next chunk arrives.
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

_DEFAULT_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
_DEFAULT_MAX_LINES = 1000


class ByteStreamBuffer:
    """Byte-oriented buffer with a hard size cap.

    When the buffer exceeds *max_bytes*, the oldest bytes are dropped.
    If the first remaining byte is a UTF-16 low surrogate (0xDC00-0xDFFF)
    it is also dropped to avoid splitting a surrogate pair.
    """

    def __init__(self, max_bytes: int = _DEFAULT_MAX_BYTES) -> None:
        if max_bytes < 1:
            raise ValueError(f"max_bytes must be >= 1, got {max_bytes}")
        self._max_bytes = max_bytes
        self._buffer = bytearray()
        self._bytes_dropped = 0

    def append(self, data: bytes) -> None:
        """Append raw bytes, enforcing the size cap."""
        self._buffer.extend(data)
        self._trim()

    def get(self) -> bytes:
        """Return current buffer contents."""
        return bytes(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()
        self._bytes_dropped = 0

    @property
    def size(self) -> int:
        return len(self._buffer)

    @property
    def bytes_dropped(self) -> int:
        return self._bytes_dropped

    def _trim(self) -> None:
        if len(self._buffer) <= self._max_bytes:
            return

        drop_count = len(self._buffer) - self._max_bytes
        self._bytes_dropped += drop_count

        new_start = drop_count
        if new_start < len(self._buffer):
            first_byte = self._buffer[new_start]
            if 0xDC <= first_byte <= 0xDF:
                new_start += 1
                self._bytes_dropped += 1
                _logger.debug("ByteStreamBuffer: dropped trailing low surrogate")

        del self._buffer[:new_start]
        _logger.debug(
            "ByteStreamBuffer: trimmed %d bytes, dropped total=%d",
            new_start,
            self._bytes_dropped,
        )


class LineBuffer:
    """Line-oriented buffer with a hard line cap.

    Splits incoming data on ``\\n`` or ``\\r\\n``. An incomplete
    trailing line is held in ``_partial_line`` and prepended to the
    next chunk so that multi-chunk lines are reconstructed
    correctly.

    When the line count exceeds *max_lines*, the oldest lines are
    discarded and ``_lines_dropped`` is incremented.
    """

    def __init__(self, max_lines: int = _DEFAULT_MAX_LINES) -> None:
        if max_lines < 1:
            raise ValueError(f"max_lines must be >= 1, got {max_lines}")
        self._max_lines = max_lines
        self._lines: list[str] = []
        self._partial_line = ""
        self._lines_dropped = 0

    def append(self, data: str) -> list[str]:
        """Append text data, returning any complete new lines.

        Returns a list of complete lines produced by this append.
        These may be fewer than all buffered lines if the buffer
        is still accumulating a trailing partial line.
        """
        if not data:
            return []

        combined = self._partial_line + data
        parts = combined.split("\n")
        self._partial_line = parts.pop()

        new_lines = []
        for part in parts:
            clean = part.rstrip("\r")
            new_lines.append(clean)

        self._lines.extend(new_lines)
        self._trim()

        return new_lines

    def get_all(self) -> str:
        """Return all buffered lines joined with newlines."""
        return "\n".join(self._lines)

    def get_recent(self, n: int | None = None) -> list[str]:
        """Return the last *n* lines (or all if *n* is None)."""
        if n is None:
            return list(self._lines)
        return self._lines[-n:] if n > 0 else []

    def clear(self) -> None:
        self._lines.clear()
        self._partial_line = ""
        self._lines_dropped = 0

    @property
    def line_count(self) -> int:
        return len(self._lines)

    @property
    def lines_dropped(self) -> int:
        return self._lines_dropped

    @property
    def has_partial(self) -> bool:
        return bool(self._partial_line)

    def _trim(self) -> None:
        if len(self._lines) <= self._max_lines:
            return

        drop_count = len(self._lines) - self._max_lines
        del self._lines[:drop_count]
        self._lines_dropped += drop_count
        _logger.debug(
            "LineBuffer: dropped %d lines, total_dropped=%d",
            drop_count,
            self._lines_dropped,
        )
