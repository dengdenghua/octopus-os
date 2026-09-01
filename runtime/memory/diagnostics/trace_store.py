"""Durable SQLite trace store for agent runtime facts.

This module is intentionally a sidecar to the existing JSONL journals.
JSONL remains append-only source material; this store gives product
surfaces and recovery code a Marvis-like read model for messages,
events, approvals, checkpoints, and token usage.
"""

from __future__ import annotations

from . import _trace_store_models as _models
from . import _trace_store_recovery as _recovery
from . import _trace_store_schema as _schema
from . import _trace_store_storage as _storage
from ._trace_store_models import ApprovalDecision, TaskRunStatus
from ._trace_store_storage import _TraceStoreStorageMixin

globals().update({name: getattr(_models, name) for name in _models.__all__})
globals().update({name: getattr(_recovery, name) for name in _recovery.__all__})
globals().update({name: getattr(_storage, name) for name in ("_decode_row", "_optional_str")})
_SCHEMA = _schema._SCHEMA


class AgentTraceStore(_TraceStoreStorageMixin):
    """SQLite-backed read model for agent trace facts."""


__all__ = ["AgentTraceStore", "ApprovalDecision", "TaskRunStatus"]
