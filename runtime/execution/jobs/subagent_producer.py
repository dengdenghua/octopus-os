"""Background subagent producer (dsh ``subagent`` one-shot background path).

Runs one ``call_subagent`` on a worker thread and settles the job when the
child run ends. Final-output only: ``read()`` returns the terminal output
idempotently after settlement (dsh one-shot background has no stream).
Cancellation bridges to the child run (audit T-03): ``cancel()`` fires a
CancellationSource installed around ``call_subagent``, so the in-flight
subagent stops promptly and the job settles as ``killed``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from typing import Any

from runtime.execution.subagents.bridge import call_subagent

from .types import JobHooks, JobOutcome, JobSnapshot, JobStart

_log = logging.getLogger("runtime.execution.jobs.subagent")

DEFAULT_SUBAGENT_JOB_TIMEOUT_S = 600
_NOTICE_MAX_CHARS = 600


def _outcome_from_result(result: dict[str, Any]) -> JobOutcome:
    """Map a child result to the task outcome (dsh ``runOutcome``):
    success carries final text, every failure is ``failed`` with its reason."""
    if result.get("success"):
        return JobOutcome(status="completed", output=str(result.get("output") or ""))
    reason = result.get("error_type") or result.get("error") or "failed"
    return JobOutcome(status="failed", detail=str(reason))


def completion_notice(snapshot: JobSnapshot) -> str:
    """One-line model-facing completion notice (dsh ``fitCompletionNotice``)."""
    detail = f", {snapshot.detail}" if snapshot.detail else ""
    text = (
        f"[后台任务] {snapshot.id} ({snapshot.kind}) {snapshot.status}{detail}: "
        f"{snapshot.label}。用 job_output 读取结果。"
    )
    if len(text) <= _NOTICE_MAX_CHARS:
        return text
    return text[: _NOTICE_MAX_CHARS - 1] + "…"


def build_subagent_job_start(
    *,
    agent_id: str,
    prompt: str,
    label: str | None = None,
    context: dict[str, Any] | None = None,
    timeout_s: int = DEFAULT_SUBAGENT_JOB_TIMEOUT_S,
    owner: str | None = None,
    **call_kwargs: Any,
) -> JobStart:
    """Build the registry declaration for one background subagent run.

    ``owner`` is the parent's opaque job key (thread/session id). The
    producer registers a lightweight report session on the parent thread at
    start so the completion notice can ride the durable report lane
    (``append_report`` → parent wake / busy inject).
    """
    if not agent_id:
        raise ValueError("background subagent job requires a non-empty agent_id")
    if not prompt:
        raise ValueError("background subagent job requires a non-empty prompt")
    if not isinstance(timeout_s, int) or timeout_s <= 0:
        raise ValueError(f"invalid timeout_s: expected a positive integer, got {timeout_s!r}")
    kind = "subagent"
    first_line = prompt.splitlines()[0].strip() if prompt.splitlines() else prompt
    effective_label = (label or first_line or agent_id)[:160]

    holder: dict[str, Any] = {"session_id": None}

    def notify(snapshot: JobSnapshot) -> None:
        """Deliver one unreported completion to the parent via the durable
        report lane (dsh ``tool-jobs`` completion notice)."""
        if snapshot.owner_session is None or snapshot.reported:
            return
        session_id = holder.get("session_id")
        if not session_id:
            return
        try:
            from runtime.execution.subagents.sessions import (
                get_subagent_session_store,
            )

            store = get_subagent_session_store()
            if store is None:
                return
            store.append_report(
                session_id,
                content=completion_notice(snapshot),
                delivery="wakeup",
            )
        except Exception:  # noqa: BLE001 — notice delivery is best-effort
            _log.debug("background job completion notice failed", exc_info=True)

    def run() -> JobHooks:
        from runtime.memory.journal.activity import (
            capture_attribution,
        )
        from runtime.platform.process.session import current_session, session_scope

        parent = current_session()
        # The settle observer runs outside this turn (possibly on the
        # registry's settle path); snapshot the attribution here while the
        # session is live and hand it over via the shared holder.
        holder["attribution"] = capture_attribution()
        thread_id = getattr(parent, "thread_id", None) or ""
        if owner and thread_id:
            try:
                from runtime.execution.subagents.sessions import (
                    get_subagent_session_store,
                )

                store = get_subagent_session_store()
                if store is not None:
                    job_session = store.create(agent_id=f"job:{kind}", thread_id=thread_id)
                    holder["session_id"] = job_session.session_id
            except Exception:  # noqa: BLE001 — notice lane is best-effort
                _log.debug("background job report session create failed", exc_info=True)

        loop = asyncio.get_running_loop()
        done: asyncio.Future[JobOutcome] = loop.create_future()
        cancelled = threading.Event()
        # Audit T-03: cancellation must reach the in-flight child run, not
        # just label the outcome after the fact. ``call_subagent`` links its
        # own child source to the AMBIENT cancellation token, so we install
        # our own source around the call and let ``cancel()`` fire it — the
        # child run stops promptly and the job settles as ``killed``.
        from runtime.safety.approval.cancellation import (
            CancellationSource,
            scoped_cancellation,
        )

        cancel_source = CancellationSource()
        cancel_reason: list[str] = []

        def _work() -> None:
            scope = None
            try:
                if parent is not None:
                    scope = session_scope(parent)
                    scope.__enter__()
                with scoped_cancellation(cancel_source.token):
                    result = call_subagent(
                        agent_id=agent_id,
                        prompt=prompt,
                        context=context,
                        timeout_s=timeout_s,
                        session=parent,
                        **call_kwargs,
                    )
            except Exception as error:  # noqa: BLE001 — producer containment
                outcome = JobOutcome(status="failed", detail=f"{type(error).__name__}: {error}")
            else:
                if cancelled.is_set():
                    outcome = JobOutcome(
                        status="killed",
                        detail=cancel_reason[-1] if cancel_reason else "cancelled",
                    )
                else:
                    outcome = _outcome_from_result(result)
            finally:
                if scope is not None:
                    with contextlib.suppress(Exception):
                        scope.__exit__(None, None, None)
            try:
                loop.call_soon_threadsafe(_set_done, done, outcome)
            except RuntimeError:  # pragma: no cover — loop closed mid-run
                _log.warning(
                    "jobs: subagent job %s could not settle (loop closed)",
                    agent_id,
                )

        threading.Thread(target=_work, name=f"job-{kind}", daemon=True).start()

        def cancel(reason: str | None = None) -> None:
            cancelled.set()
            # Bridge the kill to the child run's cancellation source (audit
            # T-03): the in-flight subagent sees the cancel via the ambient
            # token and stops instead of running to completion in the
            # background. The reason rides through to the terminal outcome.
            cancel_source.cancel(reason=reason or "cancelled")
            cancel_reason.append(reason or "cancelled")

        return JobHooks(cancel=cancel, done=done, read_output=None)

    def on_start(snapshot: JobSnapshot) -> None:
        """Journal the start immediately.

        ``on_settle`` only fires at terminal states, so a crash mid-run
        used to leave zero journal trace of a live job - it vanished
        silently on restart. A ``running`` row written at start lets the
        startup sweep close it as interrupted instead."""
        from runtime.memory.journal.activity import write_job_change

        attribution = holder.get("attribution") or {}
        write_job_change(
            job_id=snapshot.id,
            kind=snapshot.kind,
            label=snapshot.label,
            status=snapshot.status,
            detail="started",
            **attribution,
        )

    def on_settle(snapshot: JobSnapshot) -> None:
        """Journal the terminal transition (durable timeline row)."""
        from runtime.memory.journal.activity import write_job_change

        attribution = holder.get("attribution") or {}
        write_job_change(
            job_id=snapshot.id,
            kind=snapshot.kind,
            label=snapshot.label,
            status=snapshot.status,
            detail=snapshot.detail or "",
            **attribution,
        )

    return JobStart(
        kind=kind,
        label=effective_label,
        owner=owner,
        run=run,
        notify=notify,
        on_start=on_start,
        on_settle=on_settle,
        # The producer's own timeout_s stays authoritative; the registry
        # backstop sits above it so a stuck worker thread that never
        # settles cannot pin the owner's concurrency slot forever.
        watchdog_timeout_s=timeout_s + 60,
    )


def _set_done(done: asyncio.Future[JobOutcome], outcome: JobOutcome) -> None:
    if not done.done():
        done.set_result(outcome)


__all__ = [
    "DEFAULT_SUBAGENT_JOB_TIMEOUT_S",
    "build_subagent_job_start",
    "completion_notice",
]
