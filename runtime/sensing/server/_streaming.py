"""Mantle-local re-export of the platform streaming helper.

Kept so existing mantle tests that patch
``runtime.sensing.server._streaming.stream_run`` continue to work, and
so mantle-side imports stay short. The implementation lives in
``runtime.platform.process.streaming``.
"""

from __future__ import annotations

from runtime.platform.process.streaming import stream_run

__all__ = ["stream_run"]
