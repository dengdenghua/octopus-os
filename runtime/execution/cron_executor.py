"""cron_executor · the missing igniter for persisted cron jobs.

The cron *store* (``runtime/execution/cron_store.py``), the
``schedule_task`` skill, and the ``/api/cron`` router let users and the
agent *register* jobs into ``cron_jobs.json`` — but nothing ever fired
them: ``last_run`` had no writer outside this module. One tick of
``run_due_cron_jobs`` closes that loop:

1. Read the raw job list (preserving the extra ``prompt`` / ``fire_at``
   / ``recurring`` fields the store's fixed projection would strip).
2. Decide per job whether it is due (see ``_is_due`` — one-shot vs
   recurring, with single-run catch-up after downtime).
3. Enforce the caller's tenant scope (or the serve scheduler's explicit global
   authority), then dispatch: prompt jobs go to the ``prompt_runner`` and UI
   shell jobs to the ``shell_runner``. Both default to subprocess runners.
4. Write back ``last_run`` / ``last_status`` / ``last_output`` in one
   atomic write.

Robustness contract: one job's failure never breaks the tick, and the
tick itself never raises — the scheduler treats callback exceptions as
task errors, and a crashing igniter would take down every other
periodic task sharing the runner.

Concurrency note: a cross-process execution lock prevents double firing, while
short store transactions claim and settle one logical scoped job. Concurrent
settings edits are preserved; replacing/deleting an in-flight job cannot be
undone by a stale whole-file write.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.adapters.scheduler.cron import CronExpression
from runtime.execution.cron_context import cron_child_environment, cron_session_for_job
from runtime.execution.cron_store import (
    _mutate_cron_jobs,
    cron_job_effective_scope,
    cron_job_has_incomplete_scope,
    cron_job_identity,
    cron_job_visible_to_scope,
)
from runtime.platform.io import atomic_write_json
from runtime.platform.process.paths import app_paths
from runtime.safety.auth.scope import TenantScope

_log = logging.getLogger(__name__)

SHELL_JOB_TIMEOUT_S = 300
PROMPT_JOB_TIMEOUT_S = 1800
_OUTPUT_EXCERPT_CHARS = 500

# ``(status, output_excerpt)`` returned by runners.
RunResult = tuple[str, str]
ShellRunner = Callable[[str, dict[str, Any]], RunResult]
PromptRunner = Callable[[str, dict[str, Any]], RunResult]
_CRON_FALLBACK_LOCK = threading.Lock()


# ─── Default runners (subprocess) ────────────────────────────


def _pid_recorder(
    job: dict[str, Any],
    persist: Callable[[], None] | None = None,
) -> Callable[[subprocess.Popen[Any]], None]:
    """Return an ``on_start`` hook that records the child pid on the job.

    Audit T-02: the child runs in its own session (pid == pgid), so the
    recorded pid doubles as the process-group id for startup recovery.
    """

    def _record(proc: subprocess.Popen[Any]) -> None:
        job["pid"] = proc.pid
        if persist is not None:
            # The pre-dispatch marker contains pid=null because the process does
            # not exist yet. Persist the real process-group id immediately after
            # Popen so crash recovery can actually reap an orphan.
            persist()

    return _record


def default_shell_runner(
    command: str,
    job: dict[str, Any],
    *,
    persist_pid: Callable[[], None] | None = None,
    stop_event: threading.Event | None = None,
) -> RunResult:
    """Run a UI-created shell job.

    Creation of these jobs is auth-gated at the router layer, so the
    command is operator-intended; we inherit the server environment.
    """
    if stop_event is not None and stop_event.is_set():
        return "interrupted", "service shutdown before cron process start"
    proc, timed_out, interrupted = _run_process(
        _shell_argv(command),
        timeout=SHELL_JOB_TIMEOUT_S,
        on_start=_pid_recorder(job, persist_pid),
        stop_event=stop_event,
    )
    if interrupted:
        return "interrupted", "service shutdown interrupted cron process"
    if timed_out:
        return "timeout", f"exceeded {SHELL_JOB_TIMEOUT_S}s"
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    status = "ok" if proc.returncode == 0 else "error"
    if proc.returncode != 0:
        output = f"exit={proc.returncode} {output}"
    return status, output


def _shell_argv(command: str) -> list[str]:
    """Return an explicit platform shell invocation for an operator command.

    Scheduled UI jobs intentionally use shell syntax, but keeping the shell
    interpreter in argv makes that trust boundary visible and prevents the
    generic process runner from ever accepting ``shell=True``.
    """
    if sys.platform == "win32":
        return ["cmd.exe", "/d", "/s", "/c", command]
    return ["/bin/sh", "-c", command]


def default_prompt_runner(
    prompt: str,
    job: dict[str, Any],
    *,
    persist_pid: Callable[[], None] | None = None,
    stop_event: threading.Event | None = None,
) -> RunResult:
    """Run an agent-created prompt job as a headless ``runtime run``.

    Subprocess isolation keeps a scheduled turn's state (and failures)
    out of the serving process, and reuses the existing CLI path so the
    job gets the same planner/tools/config as an interactive run.
    """
    if stop_event is not None and stop_event.is_set():
        return "interrupted", "service shutdown before cron process start"
    from runtime.platform.process.session import current_session

    active_session = current_session()
    child_env = (
        cron_child_environment(active_session)
        if active_session is not None
        and (active_session.metadata or {}).get("automation_trigger") == "cron"
        else None
    )
    proc, timed_out, interrupted = _run_process(
        [sys.executable, "-m", "runtime", "run", prompt],
        timeout=PROMPT_JOB_TIMEOUT_S,
        on_start=_pid_recorder(job, persist_pid),
        stop_event=stop_event,
        env=child_env,
    )
    if interrupted:
        return "interrupted", "service shutdown interrupted cron process"
    if timed_out:
        return "timeout", f"exceeded {PROMPT_JOB_TIMEOUT_S}s"
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    status = "ok" if proc.returncode == 0 else "error"
    if proc.returncode != 0:
        output = f"exit={proc.returncode} {output}"
    return status, output


def _run_process(
    argv: list[str],
    *,
    timeout: float,
    on_start: Callable[[subprocess.Popen[Any]], None] | None = None,
    stop_event: threading.Event | None = None,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], bool, bool]:
    """Run a scheduled command in its own session and kill its descendants.

    ``subprocess.run(timeout=...)`` only guarantees that the direct child is
    reaped.  Scheduled commands commonly spawn shells, test runners, or
    agent subprocesses, so a timeout must target the whole process group.

    ``on_start`` (audit T-02) receives the Popen right after launch so the
    caller can persist the child's pid as an in-flight marker before the
    job's own work starts.
    """
    from runtime.platform.process.tree import process_group_kwargs, terminate_process_tree

    proc = subprocess.Popen(  # noqa: S603 — argv is explicit and shell=False
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        **process_group_kwargs(),
    )
    if on_start is not None:
        try:
            on_start(proc)
        except Exception:  # noqa: BLE001 — never leave an untracked child alive
            _log.exception("cron_executor: failed to persist child pid; terminating process")
            terminate_process_tree(proc)
            proc.communicate()
            raise

    deadline = time.monotonic() + max(0.0, timeout)
    last_timeout: subprocess.TimeoutExpired | None = None
    timed_out = False
    interrupted = False
    while True:
        if stop_event is not None and stop_event.is_set():
            interrupted = True
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            stdout, stderr = proc.communicate(timeout=min(0.2, remaining))
            return (
                subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr),
                False,
                False,
            )
        except subprocess.TimeoutExpired as exc:
            # communicate() is safe to retry after TimeoutExpired and retains
            # captured output; the short poll makes lifespan shutdown bounded.
            last_timeout = exc

    terminate_process_tree(proc)
    stdout, stderr = proc.communicate()
    # Preserve output captured before timeout/stop on platforms whose final
    # communicate returns an empty value after the process has been reaped.
    if last_timeout is not None:
        if not stdout:
            captured_stdout = last_timeout.stdout or ""
            stdout = (
                captured_stdout.decode(errors="replace")
                if isinstance(captured_stdout, bytes)
                else captured_stdout
            )
        if not stderr:
            captured_stderr = last_timeout.stderr or ""
            stderr = (
                captured_stderr.decode(errors="replace")
                if isinstance(captured_stderr, bytes)
                else captured_stderr
            )
    return (
        subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr),
        timed_out,
        interrupted,
    )


@contextmanager
def _cron_execution_lock(path: Path):
    """Acquire a non-blocking lock so multiple service replicas don't fire.

    POSIX flock is released by the kernel on crash, which avoids stale lock
    files.  On platforms without ``fcntl`` this remains a process-local
    fallback; the subprocess cleanup contract still applies there.
    """
    # Keep this separate from atomic_write_json's ``<target>.lock``.  The
    # executor holds its lock while persisting last_run; reusing the writer's
    # sidecar would deadlock when the same process opens that second fd.
    lock_path = path.with_name(path.name + ".execution.lock")
    handle = None
    acquired = False
    fallback_acquired = False
    try:
        try:
            import fcntl

            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                acquired = False
        except ImportError:
            # Windows deployments currently run one scheduler per data dir;
            # retain process-local protection when POSIX flock is absent.
            fallback_acquired = _CRON_FALLBACK_LOCK.acquire(blocking=False)
            acquired = fallback_acquired
        yield acquired
    finally:
        if fallback_acquired:
            _CRON_FALLBACK_LOCK.release()
        if handle is not None:
            with contextlib.suppress(Exception):
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            with contextlib.suppress(Exception):
                handle.close()


# ─── Due calculation ─────────────────────────────────────────


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO timestamp, normalizing to an aware local datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    # ``astimezone`` on a naive datetime assumes system-local — which is
    # the same clock ``CronExpression.matches`` is evaluated against.
    return dt.astimezone()


def _is_due(job: dict[str, Any], now: datetime) -> bool:
    """Decide whether one job should fire on this tick.

    One-shot jobs (``recurring=False`` + ``fire_at``): fire once when
    ``now >= fire_at``; the ``last_run`` write-back prevents refiring.

    Recurring jobs: a never-run job fires when the expression matches
    the current minute; a previously-run job fires when the next
    scheduled minute after ``last_run`` has passed — which yields
    exactly one catch-up run after downtime, not a burst.

    In-flight jobs (audit T-02): a persisted ``started_at`` marker means
    the job is either running now or was left in flight by a crash. A
    live run must never double-fire; a stale marker is reclaimed by the
    startup sweep (``recover_interrupted_cron_jobs``), which clears it
    and stamps ``last_run`` so the job does not re-fire either.
    """
    if job.get("started_at"):
        return False
    last_run_dt = _parse_dt(job.get("last_run"))

    fire_at_dt = _parse_dt(job.get("fire_at"))
    if fire_at_dt is not None and job.get("recurring") is False:
        return last_run_dt is None and now >= fire_at_dt

    try:
        ce = CronExpression.parse(str(job.get("cron_expression") or ""))
    except Exception:  # noqa: BLE001 — a corrupt job must not break the tick
        _log.warning("cron_executor: unparseable cron for job %r", job.get("name"))
        return False

    if last_run_dt is None:
        return ce.matches(now)
    try:
        return ce.next_after(last_run_dt) <= now
    except Exception:  # noqa: BLE001 — pathological expression; skip rather than crash
        return False


# ─── Tick ────────────────────────────────────────────────────


def _read_raw_jobs(path: Path) -> list[dict[str, Any]]:
    """Read the job file without the store's fixed projection.

    ``cron_store._read_cron_jobs`` strips ``prompt`` / ``fire_at`` /
    ``recurring``; the executor needs them and must write them back.
    """
    import json

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, ValueError, TypeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("name")]


def run_due_cron_jobs(
    *,
    cron_path: Path | None = None,
    now: datetime | None = None,
    shell_runner: ShellRunner | None = None,
    prompt_runner: PromptRunner | None = None,
    deliver: Callable[[dict[str, Any]], None] | None = None,
    stop_event: threading.Event | None = None,
    scope: TenantScope | None = None,
    allow_cross_tenant: bool = False,
) -> dict[str, Any]:
    """Run due jobs once, serialized across scheduler processes.

    A caller without scope can only execute legacy-unowned rows.  The serve
    scheduler is the one trusted global caller and must pass
    ``allow_cross_tenant=True`` explicitly; request-driven callers pass their
    exact server-resolved ``TenantScope``.
    """
    path = cron_path or app_paths().cron_jobs_path
    with _cron_execution_lock(path) as acquired:
        if not acquired:
            _log.debug("cron_executor: another scheduler owns %s", path)
            return {"ok": True, "fired": 0, "results": [], "skipped": "lock_held"}
        return _run_due_cron_jobs(
            cron_path=path,
            now=now,
            shell_runner=shell_runner,
            prompt_runner=prompt_runner,
            deliver=deliver,
            stop_event=stop_event,
            scope=scope,
            allow_cross_tenant=allow_cross_tenant,
        )


def _run_due_cron_jobs(
    *,
    cron_path: Path | None = None,
    now: datetime | None = None,
    shell_runner: ShellRunner | None = None,
    prompt_runner: PromptRunner | None = None,
    deliver: Callable[[dict[str, Any]], None] | None = None,
    stop_event: threading.Event | None = None,
    scope: TenantScope | None = None,
    allow_cross_tenant: bool = False,
) -> dict[str, Any]:
    """Fire every due job once. Returns a per-tick summary; never raises.

    ``deliver`` is an optional per-run hook called with the run record
    (name/kind/fired_at/duration_ms/status/output_excerpt) after the
    ledger write — the serve layer can use it to push completion
    notifications; failures inside the hook are logged and swallowed.
    """
    path = cron_path or app_paths().cron_jobs_path
    tick_now = (now or datetime.now().astimezone()).astimezone()

    jobs = _read_raw_jobs(path)
    if not jobs:
        return {"ok": True, "fired": 0, "results": []}

    results: list[dict[str, Any]] = []
    run_records: list[dict[str, Any]] = []
    persistence_failed = False
    for snapshot in jobs:
        if stop_event is not None and stop_event.is_set():
            break
        name = str(snapshot.get("name") or "")
        if cron_job_has_incomplete_scope(snapshot):
            _log.error("cron_executor: refusing half-scoped job %r", name)
            continue
        if not allow_cross_tenant and not cron_job_visible_to_scope(snapshot, scope):
            continue
        try:
            due = _is_due(snapshot, tick_now)
        except Exception:  # noqa: BLE001 — paranoid: never let one job break the tick
            _log.exception("cron_executor: due-check failed for job %r", name)
            continue
        if not due:
            continue

        import time as _time

        run_id = uuid4().hex
        logical_key = cron_job_identity(snapshot)

        # Atomically re-read and claim the current row.  This closes the old
        # read/dispatch race where an API update could be overwritten by the
        # executor's stale whole-file write, or a deleted job could reappear.
        def _claim(
            current_jobs: list[dict[str, Any]],
            *,
            _logical_key: tuple[str, str, str] = logical_key,
            _run_id: str = run_id,
        ) -> dict[str, Any] | None:
            for candidate in current_jobs:
                if cron_job_identity(candidate) != _logical_key:
                    continue
                if cron_job_has_incomplete_scope(candidate):
                    return None
                if not allow_cross_tenant and not cron_job_visible_to_scope(candidate, scope):
                    return None
                if not _is_due(candidate, tick_now):
                    return None
                candidate["started_at"] = tick_now.isoformat()
                candidate["active_run_id"] = _run_id
                candidate["pid"] = None
                return dict(candidate)
            return None

        try:
            claimed = _mutate_cron_jobs(
                path,
                _claim,
                persist_if=lambda value: value is not None,
            )
        except Exception:  # noqa: BLE001 — never dispatch without a durable claim
            _log.exception("cron_executor: failed to claim job %r", name)
            persistence_failed = True
            continue
        if claimed is None:
            continue
        job = claimed

        is_agent_job = bool(job.get("prompt")) or job.get("creator_actor") == "agent_self"
        payload = str(job.get("prompt") or job.get("command") or "").strip()
        if not payload:
            results.append({"name": name, "status": "skipped_empty"})
            continue

        execution_session = cron_session_for_job(
            job,
            fired_at=tick_now,
            run_id=run_id,
        )

        def _persist_pid(
            *,
            _job: dict[str, Any] = job,
            _logical_key: tuple[str, str, str] = logical_key,
            _run_id: str = run_id,
        ) -> None:
            pid = _job.get("pid")

            def _update_pid(
                current_jobs: list[dict[str, Any]],
                *,
                _key: tuple[str, str, str] = _logical_key,
                _active_run_id: str = _run_id,
                _pid: Any = pid,
            ) -> bool:
                for candidate in current_jobs:
                    if (
                        cron_job_identity(candidate) == _key
                        and candidate.get("active_run_id") == _active_run_id
                    ):
                        candidate["pid"] = _pid
                        return True
                return False

            persisted = _mutate_cron_jobs(
                path,
                _update_pid,
                persist_if=bool,
            )
            if not persisted:
                raise RuntimeError("cron job was replaced before pid persistence")

        started = _time.monotonic()
        try:
            from runtime.memory.journal.journal_context import journal_context
            from runtime.platform.process.session import session_scope

            effective_scope = cron_job_effective_scope(job)
            with (
                session_scope(execution_session),
                journal_context(
                    conversation_id=execution_session.conversation_id,
                    tenant_id=(effective_scope.tenant_id if effective_scope is not None else None),
                    owner_actor_id=(
                        effective_scope.actor_id if effective_scope is not None else None
                    ),
                ),
            ):
                if is_agent_job:
                    if prompt_runner is None:
                        status, output = default_prompt_runner(
                            payload,
                            job,
                            persist_pid=_persist_pid,
                            stop_event=stop_event,
                        )
                    else:
                        status, output = prompt_runner(payload, job)
                else:
                    if shell_runner is None:
                        status, output = default_shell_runner(
                            payload,
                            job,
                            persist_pid=_persist_pid,
                            stop_event=stop_event,
                        )
                    else:
                        status, output = shell_runner(payload, job)
        except Exception as exc:  # noqa: BLE001 — a runner bug must not kill the tick
            status, output = "error", f"{type(exc).__name__}: {exc}"
        duration_ms = int((_time.monotonic() - started) * 1000)

        def _settle(
            current_jobs: list[dict[str, Any]],
            *,
            _logical_key: tuple[str, str, str] = logical_key,
            _run_id: str = run_id,
            _status: str = status,
            _output: str = output,
        ) -> bool:
            for candidate in current_jobs:
                if (
                    cron_job_identity(candidate) == _logical_key
                    and candidate.get("active_run_id") == _run_id
                ):
                    candidate.pop("started_at", None)
                    candidate.pop("active_run_id", None)
                    candidate.pop("pid", None)
                    candidate["last_run"] = tick_now.isoformat()
                    candidate["last_status"] = _status
                    candidate["last_output"] = (_output or "")[-_OUTPUT_EXCERPT_CHARS:]
                    return True
            return False

        try:
            _mutate_cron_jobs(path, _settle, persist_if=bool)
        except OSError:
            _log.exception("cron_executor: failed to persist result for %r", name)
            persistence_failed = True
        results.append({"name": name, "status": status})
        record: dict[str, Any] = {
            # Opaque identifier: names and ownership never enter a URL/path.
            "run_id": run_id,
            "name": name,
            "kind": "prompt" if is_agent_job else "shell",
            "creator_actor": job.get("creator_actor"),
            "fired_at": tick_now.isoformat(),
            "duration_ms": duration_ms,
            "status": status,
            "output_excerpt": (output or "")[-_OUTPUT_EXCERPT_CHARS:],
            # 订阅推送 · IM delivery target recorded at schedule time.
            "channel_id": str(job.get("channel_id") or ""),
            "thread_id": str(job.get("thread_id") or ""),
        }
        if effective_scope is not None:
            record["tenant_id"] = effective_scope.tenant_id
            record["owner_actor_id"] = effective_scope.actor_id
        run_records.append(record)
        _log.info("cron_executor: fired job %r → %s", name, status)

    if run_records:
        _append_run_ledger(_runs_ledger_path(path), run_records)
        if deliver is not None:
            for record in run_records:
                try:
                    from runtime.memory.journal.journal_context import journal_context
                    from runtime.platform.process.session import session_scope

                    fired_at = _parse_dt(record.get("fired_at")) or tick_now
                    delivery_session = cron_session_for_job(
                        record,
                        fired_at=fired_at,
                        run_id=str(record["run_id"]),
                    )
                    delivery_scope = cron_job_effective_scope(record)
                    with (
                        session_scope(delivery_session),
                        journal_context(
                            conversation_id=delivery_session.conversation_id,
                            tenant_id=(
                                delivery_scope.tenant_id if delivery_scope is not None else None
                            ),
                            owner_actor_id=(
                                delivery_scope.actor_id if delivery_scope is not None else None
                            ),
                        ),
                    ):
                        deliver(record)
                except Exception:  # noqa: BLE001 — delivery must never break the tick
                    _log.exception("cron_executor: deliver hook failed for %r", record["name"])

    return {"ok": not persistence_failed, "fired": len(results), "results": results}


# ─── Run ledger ──────────────────────────────────────────────

_RUNS_LEDGER_NAME = "cron_runs.jsonl"
_RUNS_LEDGER_MAX_BYTES = 2 * 1024 * 1024
# Audit P-08: read/trim only the tail — never the whole ledger.
_LEDGER_TAIL_READ_BYTES = 256 * 1024


def _runs_ledger_path(cron_path: Path) -> Path:
    return cron_path.parent / _RUNS_LEDGER_NAME


def _append_run_ledger(ledger_path: Path, records: list[dict[str, Any]]) -> None:
    """Append run records to the JSONL history ledger (best-effort).

    The ledger is the queryable "what ran and how did it go" surface —
    ``/api/cron/runs`` reads it back. Capped by truncating the oldest half
    (audit P-08: the trim reads only the kept half from the file midpoint,
    never the whole ledger) so a chatty every-minute job can't fill the disk.
    """
    import json

    try:
        lines = [json.dumps(r, ensure_ascii=False) for r in records]
        with ledger_path.open("a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
        if ledger_path.stat().st_size > _RUNS_LEDGER_MAX_BYTES:
            _trim_ledger_oldest_half(ledger_path)
    except OSError:
        _log.exception("cron_executor: failed to append run ledger %s", ledger_path)


def _trim_ledger_oldest_half(ledger_path: Path) -> None:
    """Drop the oldest half of a JSONL ledger without reading it whole (P-08)."""
    try:
        size = ledger_path.stat().st_size
    except OSError:
        return
    with ledger_path.open("rb") as fh:
        fh.seek(size // 2)
        # Advance to a line boundary so the cut never splits a record.
        while True:
            b = fh.read(1)
            if not b or b == b"\n":
                break
        rest = fh.read()
    if not rest.strip():
        return
    try:
        with ledger_path.open("wb") as fh:
            fh.write(rest)
    except OSError:
        _log.exception("cron_executor: failed to trim run ledger %s", ledger_path)


def _process_group_alive(pid: int) -> bool:
    """True when a POSIX process group led by ``pid`` still exists.

    Cron children launch with ``start_new_session=True``, so the child's
    pid IS its process-group id. ``killpg(pid, 0)`` probes the group
    (survives the leader exiting while descendants remain); Windows falls
    back to a plain pid probe.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — treat as alive (do not kill).
        return True


def recover_interrupted_cron_jobs(
    cron_path: Path | None = None,
    *,
    scope: TenantScope | None = None,
    allow_cross_tenant: bool = False,
) -> dict[str, Any]:
    """Startup sweep (audit T-02): reclaim jobs left in-flight by a crash.

    A job whose subprocess died with the server leaves a persisted
    ``started_at``/``pid`` marker but nothing driving it. Without this
    sweep the marker would skip the job forever (``_is_due`` refuses
    in-flight jobs) while the orphaned process group keeps running.

    Recovery, per marked job:
      * kills the surviving process group by pid (``start_new_session``
        makes pid == pgid),
      * clears the marker and records ``last_status=interrupted``,
      * stamps ``last_run`` so the job does NOT re-fire on the next
        catch-up tick — no double execution.

    Never raises; returns ``{"ok", "interrupted", "jobs"}``.
    """
    path = cron_path or app_paths().cron_jobs_path
    try:
        jobs = _read_raw_jobs(path)
    except Exception:  # noqa: BLE001
        _log.exception("cron recovery: cannot read %s", path)
        return {"ok": False, "interrupted": 0, "jobs": [], "error": "read failed"}

    now = datetime.now().astimezone().isoformat()
    touched: list[str] = []
    for job in jobs:
        if not job.get("started_at"):
            continue
        if cron_job_has_incomplete_scope(job):
            _log.error("cron recovery: refusing half-scoped job %r", job.get("name"))
            continue
        if not allow_cross_tenant and not cron_job_visible_to_scope(job, scope):
            continue
        name = str(job.get("name") or "?")
        pid = job.get("pid")
        if isinstance(pid, int) and pid > 0 and _process_group_alive(pid):
            try:
                from runtime.platform.process.tree import terminate_pid_tree

                terminated = terminate_pid_tree(pid)
            except Exception:  # noqa: BLE001 — recovery must never raise
                _log.exception("cron recovery: kill failed for %r pid=%s", name, pid)
                terminated = False
            _log.warning(
                "cron recovery: reaped orphaned process group pid=%s job=%r (%s)",
                pid,
                name,
                "killed" if terminated else "kill-failed",
            )
        job.pop("started_at", None)
        job.pop("active_run_id", None)
        job.pop("pid", None)
        job["last_run"] = now
        job["last_status"] = "interrupted"
        job["last_output"] = "interrupted by process restart (audit T-02)"
        touched.append(name)

    if touched:
        try:
            atomic_write_json(path, jobs)
        except OSError:
            _log.exception("cron recovery: failed to persist %s", path)
            return {
                "ok": False,
                "interrupted": len(touched),
                "jobs": touched,
                "error": "persist failed",
            }
    return {"ok": True, "interrupted": len(touched), "jobs": touched}


def read_run_ledger(ledger_path: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Read the newest ``limit`` run records (newest first).

    Audit P-08: reads only the tail of the file (``_LEDGER_TAIL_READ_BYTES``)
    instead of the whole ledger, so the cost stays O(tail) as the ledger
    grows. A partial first line from the cut is skipped by the JSON parse.
    """
    import json

    try:
        size = ledger_path.stat().st_size
    except OSError:
        return []
    if size <= 0:
        return []
    tail_bytes = min(size, _LEDGER_TAIL_READ_BYTES)
    try:
        with ledger_path.open("rb") as fh:
            fh.seek(size - tail_bytes)
            raw = fh.read(tail_bytes).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return []
    records: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("name"):
            records.append(item)
    return records[-limit:][::-1]


__all__ = [
    "SHELL_JOB_TIMEOUT_S",
    "PROMPT_JOB_TIMEOUT_S",
    "default_shell_runner",
    "default_prompt_runner",
    "read_run_ledger",
    "run_due_cron_jobs",
    "recover_interrupted_cron_jobs",
]
