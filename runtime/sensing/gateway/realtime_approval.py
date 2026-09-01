"""Approval bridge between the blocking react loop and the async gateway.

Split out of ``realtime_cerebrum.py``. The react loop is iterator-based
and runs on a worker thread, but the gateway's ``request_approval`` is a
coroutine; ``GatewayApprovalProvider`` bridges the two with
:func:`asyncio.run_coroutine_threadsafe`. This is the only place where
async↔sync handoff happens for approvals.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import CancelledError
from typing import Any

from runtime.protocol import JsonRpcErrorCode, ServerMethod
from runtime.safety.approval.approval_gate import (
    ApprovalDecision,
    ApprovalProvider,
    ApprovalRequest,
)
from runtime.sensing.gateway.realtime_gateway import EventEmitter, _ApprovalError

_logger = logging.getLogger(__name__)


class GatewayApprovalProvider(ApprovalProvider):
    """Blocking provider that delegates to a running asyncio gateway.

    The react loop is iterator-based and runs on a worker thread, but
    the gateway's ``request_approval`` is a coroutine. We bridge with
    :func:`asyncio.run_coroutine_threadsafe` against the loop the
    ``CerebrumRuntime.start_turn`` coroutine was scheduled on.
    """

    def __init__(
        self,
        emitter: EventEmitter,
        loop: asyncio.AbstractEventLoop,
        *,
        thread_id: str,
        turn_id: str,
        trace_store: Any = None,
    ) -> None:
        self._emitter = emitter
        self._loop = loop
        self._thread_id = thread_id
        self._turn_id = turn_id
        self._trace_store = trace_store

    def request(self, req: ApprovalRequest, *, timeout: float = 120.0) -> ApprovalDecision:
        coro = self._emitter.request_approval(
            ServerMethod.REQ_COMMAND_APPROVAL,
            {
                "threadId": self._thread_id,
                "turnId": self._turn_id,
                "itemId": req.tool_call_id,
                "tool": req.tool_name,
                "argsPreview": req.args_preview,
                "detail": req.detail,
                # Lets the client run its own countdown and expire the
                # dialog in lockstep instead of leaving a zombie prompt
                # after the server has already given up.
                "timeoutMs": int(timeout * 1000),
            },
            timeout=timeout,
        )
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        # Every failure denies, but the *reason* is part of the contract:
        # "timeout" / "connection_lost" are machine-readable so the UI
        # and journal can distinguish "user said no" from "nobody was
        # there to answer".
        try:
            decision = future.result(timeout=timeout + 5.0)
        except _ApprovalError as exc:
            code = getattr(exc.error, "code", None)
            timed_out = code == JsonRpcErrorCode.APPROVAL_TIMEOUT
            label = "timeout" if timed_out else "error"
            return self._deny(req, reason=label if timed_out else f"error: {exc}", label=label)
        except CancelledError:
            # ApprovalManager.cancel_all() on connection close cancels the
            # pending future. CancelledError is a BaseException — without
            # this clause it would crash the react worker thread.
            return self._deny(req, reason="connection_lost", label="connection_lost")
        except TimeoutError:
            return self._deny(req, reason="timeout", label="timeout")
        except (ConnectionError, OSError, RuntimeError) as exc:
            return self._deny(req, reason=f"error: {exc}", label="error")
        action = (decision or {}).get("action", "decline")
        result = ApprovalDecision(
            approved=(action == "accept"),
            reason=action,
        )
        self._record_approval(req, result)
        return result

    def _deny(self, req: ApprovalRequest, *, reason: str, label: str) -> ApprovalDecision:
        _logger.warning("approval bridge: %s denied (%s)", req.tool_name, label)
        result = ApprovalDecision(approved=False, reason=reason)
        self._record_approval(req, result, decision_label=label)
        return result

    def _record_approval(
        self,
        req: ApprovalRequest,
        decision: ApprovalDecision,
        *,
        decision_label: str | None = None,
    ) -> None:
        if self._trace_store is None:
            return
        label = decision_label or ("approved" if decision.approved else "rejected")
        try:
            from runtime.safety.audit.trust_gateway import trace_metadata_for_tool

            self._trace_store.record_approval(
                thread_id=self._thread_id or req.thread_id,
                turn_id=self._turn_id,
                tool_name=req.tool_name,
                tool_call_id=req.tool_call_id,
                args_preview=req.args_preview,
                decision=label,
                reason=decision.reason or "",
                metadata=trace_metadata_for_tool(req, detail=req.detail),
            )
        except Exception:  # noqa: BLE001
            _logger.debug("approval bridge: trace record failed", exc_info=True)
