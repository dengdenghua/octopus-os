"""Trusted execution context for persisted Cron prompt subprocesses.

The scheduler runs prompt jobs in a fresh ``python -m runtime run`` process.
ContextVars do not cross that boundary, so a scoped job would otherwise become
an anonymous turn.  The parent places a compact base64url payload in a private
environment variable; the child consumes (and immediately removes) it before
CLI dispatch and binds the resulting :class:`Session`.

Tenant and actor values never appear in argv, a URL, or a filename.  The only
externally visible identifiers are fixed-length SHA-256 references.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from collections.abc import MutableMapping
from datetime import datetime
from typing import Any

from runtime.execution.cron_store import cron_job_effective_scope
from runtime.platform.process.session import Session
from runtime.safety.recovery.tenant_scope import (
    AUTHORITATIVE_SCOPE_CONTEXT_KEY,
    authoritative_scope_context,
)

CRON_CONTEXT_ENV = "ECHO_INTERNAL_CRON_CONTEXT_B64_V1"
_OPAQUE_REF_RE = re.compile(r"^[a-f0-9]{24,64}$")
_MAX_ENCODED_CONTEXT = 16_384


class CronContextError(ValueError):
    """Raised when an inherited Cron context is malformed or incomplete."""


def _opaque_task_ref(job: dict[str, Any]) -> str:
    scope = cron_job_effective_scope(job)
    material = "\x00".join(
        (
            scope.tenant_id if scope is not None else "legacy-unowned",
            scope.actor_id if scope is not None else "legacy-unowned",
            str(job.get("name") or ""),
        )
    )
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()[:32]


def cron_session_for_job(
    job: dict[str, Any],
    *,
    fired_at: datetime,
    run_id: str,
) -> Session:
    """Build the authoritative Session used by runner and delivery hooks."""

    scope = cron_job_effective_scope(job)
    task_ref = _opaque_task_ref(job)
    metadata: dict[str, Any] = {
        "automation_trigger": "cron",
        "cron_task_ref": task_ref,
        "cron_run_id": run_id,
        "cron_fired_at": fired_at.isoformat(),
    }
    actor: str | None = None
    if scope is not None:
        actor = scope.actor_id
        metadata["tenant_id"] = scope.tenant_id
        metadata["owner_actor_id"] = scope.actor_id
        metadata[AUTHORITATIVE_SCOPE_CONTEXT_KEY] = authoritative_scope_context(scope)
    return Session(
        actor=actor,
        thread_id=f"cron-{task_ref}",
        conversation_id=f"cron-{task_ref}",
        turn_id=run_id,
        metadata=metadata,
    )


def encode_cron_session(session: Session) -> str:
    """Serialize a Cron Session for one child process environment."""

    metadata = session.metadata or {}
    tenant_id = str(metadata.get("tenant_id") or "").strip()
    owner_actor_id = str(metadata.get("owner_actor_id") or "").strip()
    if bool(tenant_id) != bool(owner_actor_id):
        raise CronContextError("cron execution scope is incomplete")
    task_ref = str(metadata.get("cron_task_ref") or "").strip()
    if not _OPAQUE_REF_RE.fullmatch(task_ref):
        raise CronContextError("cron task reference is invalid")
    payload = {
        "v": 1,
        "tenant_id": tenant_id,
        "owner_actor_id": owner_actor_id,
        "thread_id": str(session.thread_id or f"cron-{task_ref}"),
        "turn_id": str(session.turn_id or ""),
        "task_ref": task_ref,
        "fired_at": str(metadata.get("cron_fired_at") or ""),
    }
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def cron_child_environment(
    session: Session,
    *,
    base: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an environment copy containing one consumable Cron context."""

    env = dict(os.environ if base is None else base)
    env[CRON_CONTEXT_ENV] = encode_cron_session(session)
    return env


def consume_cron_session_from_environment(
    environ: MutableMapping[str, str] | None = None,
) -> Session | None:
    """Pop and validate the inherited context; absence means normal CLI."""

    source = os.environ if environ is None else environ
    encoded = source.pop(CRON_CONTEXT_ENV, None)
    if encoded is None:
        return None
    if not encoded or len(encoded) > _MAX_ENCODED_CONTEXT:
        raise CronContextError("cron execution context has invalid size")
    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode((encoded + padding).encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise CronContextError("cron execution context is malformed") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise CronContextError("cron execution context version is unsupported")

    tenant_id = str(payload.get("tenant_id") or "").strip()
    owner_actor_id = str(payload.get("owner_actor_id") or "").strip()
    if bool(tenant_id) != bool(owner_actor_id):
        raise CronContextError("cron execution context scope is incomplete")
    task_ref = str(payload.get("task_ref") or "").strip()
    if not _OPAQUE_REF_RE.fullmatch(task_ref):
        raise CronContextError("cron execution context task reference is invalid")
    thread_id = str(payload.get("thread_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    if thread_id != f"cron-{task_ref}" or not turn_id:
        raise CronContextError("cron execution context identifiers are invalid")

    metadata: dict[str, Any] = {
        "automation_trigger": "cron",
        "cron_task_ref": task_ref,
        "cron_run_id": turn_id,
        "cron_fired_at": str(payload.get("fired_at") or ""),
    }
    actor: str | None = None
    if tenant_id:
        from runtime.safety.auth.scope import TenantScope

        scope = TenantScope(tenant_id=tenant_id, actor_id=owner_actor_id)
        actor = owner_actor_id
        metadata["tenant_id"] = tenant_id
        metadata["owner_actor_id"] = owner_actor_id
        metadata[AUTHORITATIVE_SCOPE_CONTEXT_KEY] = authoritative_scope_context(scope)
    return Session(
        actor=actor,
        thread_id=thread_id,
        conversation_id=thread_id,
        turn_id=turn_id,
        metadata=metadata,
    )


__all__ = [
    "CRON_CONTEXT_ENV",
    "CronContextError",
    "consume_cron_session_from_environment",
    "cron_child_environment",
    "cron_session_for_job",
    "encode_cron_session",
]
