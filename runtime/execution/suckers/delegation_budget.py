"""Smart per-turn delegation budget.

Extracted from ``delegation_skills.py`` (2026-06) to keep that file under
the god-file threshold. Implements the budget rules documented in the
parent module's ``_call_agent`` docstring:

  * Absolute cap: ``_PER_TURN_ABSOLUTE_LIMIT`` calls per turn.
  * Success counts; first-time failure is FREE (fingerprint recorded);
    repeat failure (same agent + same prompt) counts.
  * Fingerprint normalization (trim + collapse whitespace + lowercase)
    prevents trivial bypass.

Budget state is process-local — there's no persistence. Restarting the
backend resets all turn counters and fingerprints. Turn IDs are scoped
by ``Session.turn_id`` upstream.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

_PER_TURN_ABSOLUTE_LIMIT: int = 5
_MAX_TRACKED_TURNS: int = 1024
_LOG = logging.getLogger(__name__)


def _effective_flat_limit() -> int:
    """Per-turn ad-hoc cap, relaxed in ultracode mode.

    The flat cap (5) guards ad-hoc, model-driven delegation. The soft
    ``audit.ultracode`` mode asks the model to be exhaustive and fan out
    when parallelism helps, so it gets a higher ad-hoc budget (20) — still
    bounded by the operator's ``ECHO_ORCH_TOKEN_BUDGET`` for actual
    orchestration, so a client can never escalate to unlimited spawns.
    """
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
        if sess is not None:
            meta = getattr(sess, "metadata", None) or {}
            if isinstance(meta, dict):
                preset = str(meta.get("workflow_preset") or "").strip().lower()
                if preset == "audit.ultracode":
                    return 20
    except Exception:  # noqa: BLE001 — budget never breaks delegation
        pass
    return _PER_TURN_ABSOLUTE_LIMIT


# Per-turn state. OrderedDict for LRU eviction.
_TURN_DELEGATIONS: OrderedDict[str, int] = OrderedDict()
_TURN_FAILED_FINGERPRINTS: OrderedDict[str, set[str]] = OrderedDict()


# ── opt-in orchestration budget envelope ─────────────────────────
# The flat per-turn cap (5) is the right guardrail for ad-hoc, model-driven
# delegation, but it makes any multi-stage / looping orchestration
# impossible (a 3rd fan-out in the same turn is refused). A deterministic
# orchestration recipe instead runs inside an ``orchestration_budget_scope``:
# for the duration of the scope the flat cap is REPLACED by a single bounded
# total — the recipe may spawn up to ``max_spawns`` sub-agent runs across all
# its stages, hard-stopping when exhausted.
#
# Opt-in by design: with no active scope, behaviour is unchanged. The scope
# is entered by TRUSTED recipe code with a sane bound — it is NOT a knob the
# model sets per call (that would defeat the turn cap). This is the wiring
# the cumulative-ceiling note in ``subagents/bridge.py`` deferred as "a
# deployment decision".


@dataclass
class OrchestrationBudget:
    """A bounded total spawn budget for one orchestration. Thread-safe so
    the parallel fan-out workers can charge it concurrently."""

    max_spawns: int
    _used: int = field(default=0)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_spawns - self._used)

    def has_room(self) -> bool:
        with self._lock:
            return self._used < self.max_spawns

    def try_charge(self, n: int = 1) -> bool:
        """Atomically reserve spawn budget.

        ``charge`` is intentionally kept as a legacy accounting primitive; use
        this method on the hot path before actually spawning work so concurrent
        fan-outs cannot oversubscribe the envelope.
        """
        if n <= 0:
            return True
        with self._lock:
            if self._used + n > self.max_spawns:
                return False
            self._used += n
            return True

    def charge(self, n: int = 1) -> None:
        if n <= 0:
            return
        with self._lock:
            self._used += n


_ORCH_BUDGET: ContextVar[OrchestrationBudget | None] = ContextVar(
    "echo_orchestration_budget",
    default=None,
)


def current_orchestration_budget() -> OrchestrationBudget | None:
    """The orchestration budget for the current context, or None.

    Reads a ``ContextVar`` — visible on the thread that entered the scope,
    NOT auto-propagated into pool workers. The parallel fan-out captures it
    on the calling thread and passes it explicitly into per-spec accounting.
    """
    return _ORCH_BUDGET.get()


@contextmanager
def orchestration_budget_scope(max_spawns: int) -> Iterator[OrchestrationBudget]:
    """Run an orchestration under a bounded total spawn budget, replacing the
    flat per-turn delegation cap for the duration of the scope."""
    budget = OrchestrationBudget(max_spawns=max(1, int(max_spawns)))
    token = _ORCH_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _ORCH_BUDGET.reset(token)


# Rough token cost of one sub-agent run (one bounded ephemeral turn: system +
# tool specs + a few tool rounds). Used to translate an opt-in token budget into
# a spawn count so a deep run scales its parallelism to the budget it was
# actually given, instead of the fixed ``n*rounds`` guess.
#
# Honest caveat: this is a SINGLE high-variance heuristic, not a measured cost.
# A cheap vote may cost ~2k tokens while a full-tool researcher doing 10 tool
# rounds can burn 50k+. There is NO runtime token accounting — the "token
# budget" is converted to a spawn cap ONCE at entry and never re-checked, so it
# bounds fan-out SIZE, not actual token spend. Treat the env vars below as
# "spawn-scale budget", not a token ceiling.
_TOKENS_PER_SPAWN = 8_000


def max_spawns_for_token_budget(
    token_budget: int | float | None,
    *,
    tokens_per_spawn: int = _TOKENS_PER_SPAWN,
    floor: int = 2,
    ceiling: int = 256,
) -> int:
    """Translate a token budget into an orchestration spawn cap.

    A bigger budget buys more sub-agent runs (deeper fan-out + verification).
    Clamped to ``[floor, ceiling]`` so a tiny budget still does *some* work and
    a huge one can't run away; a missing / non-positive budget falls back to
    ``floor``. This is the opt-in lever only — callers that don't supply a
    budget keep the conservative default (``n*rounds`` / 48) behaviour.

    Note the budget is a SPAWN cap derived from a rough per-spawn token
    estimate (``_TOKENS_PER_SPAWN``), not a runtime token ceiling — actual
    token spend is never measured against it after this one conversion."""
    try:
        tokens = int(token_budget or 0)
    except (TypeError, ValueError):
        tokens = 0
    if tokens <= 0 or tokens_per_spawn <= 0:
        return floor
    return max(floor, min(ceiling, tokens // tokens_per_spawn))


_OPERATOR_TOKEN_BUDGET_ENV = "ECHO_ORCH_TOKEN_BUDGET"


def operator_orchestration_token_budget() -> int | None:
    """Operator-set deployment-wide orchestration token budget, or ``None``.

    Reads ``ECHO_ORCH_TOKEN_BUDGET`` — an OPERATOR switch (set on the server,
    NOT by the model or an end user), so turning on deeper orchestration is a
    deliberate, billable deployment decision. When set to a positive int every
    orchestration on this deployment scales its spawn ceiling to it (still
    hard-capped by ``max_spawns_for_token_budget``'s ceiling). Unset / invalid /
    non-positive → ``None`` (conservative default, behaviour unchanged)."""
    raw = os.environ.get(_OPERATOR_TOKEN_BUDGET_ENV, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


_ULTRACODE_TOKEN_BUDGET_ENV = "ECHO_ULTRACODE_TOKEN_BUDGET"
ULTRACODE_TOKEN_BUDGET_DEFAULT = 200_000


def ultracode_token_budget() -> int:
    """Server-side token grant for one ``audit.ultracode`` turn.

    Resolution order: ``ECHO_ULTRACODE_TOKEN_BUDGET`` (preset-specific
    operator override) → ``ECHO_ORCH_TOKEN_BUDGET`` (deployment-wide) →
    ``ULTRACODE_TOKEN_BUDGET_DEFAULT``. Always positive: picking the preset
    is the opt-in, so the bus must actually widen the spawn ceiling instead
    of silently keeping the conservative default. The value is granted by
    the GATEWAY into ``session.metadata`` — clients and models never set it
    (the gateway scrubs any client-supplied copy first).

    Like the operator budget above, this is a fan-out SIZE grant (converted to
    a spawn cap via ``_TOKENS_PER_SPAWN``), not a hard token spend ceiling.
    """
    raw = os.environ.get(_ULTRACODE_TOKEN_BUDGET_ENV, "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            _LOG.warning(
                "invalid %s=%r; using the fallback budget", _ULTRACODE_TOKEN_BUDGET_ENV, raw
            )
    operator = operator_orchestration_token_budget()
    if operator is not None:
        return operator
    return ULTRACODE_TOKEN_BUDGET_DEFAULT


def compute_fingerprint(agent_id: str, prompt: str) -> str:
    """Normalize and hash a delegation spec so repeated identical
    attempts (modulo whitespace / case) share the same fingerprint.

    Prevents trivial bypass: adding a space or changing case won't
    reset the "first failure gets a free pass" counter.
    """
    # Normalize: trim + collapse whitespace + lowercase. We preserve
    # punctuation so semantically different prompts still hash
    # differently. Goal is "same intent" not "same text".
    normalized = " ".join((prompt or "").lower().split())
    key = f"{agent_id}::{normalized}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def check_absolute_cap(
    turn_id: str | None,
    *,
    budget: OrchestrationBudget | None = None,
) -> tuple[int, bool]:
    """Check if we're under the spawn cap.

    Returns ``(current_count, within_cap)``. When ``turn_id`` is None
    (raw unit tests, no Session) the per-turn enforcement is OFF.

    When an orchestration budget is active (passed explicitly, or the
    ambient ``orchestration_budget_scope`` on this thread), the flat
    per-turn cap is REPLACED by the budget's remaining room.

    Does NOT increment the counter — that happens in ``record_delegation``
    after we know whether the call counts (success vs. first-time
    structural failure).
    """
    env = budget if budget is not None else current_orchestration_budget()
    if env is not None:
        return (env.used, env.has_room())
    if not turn_id:
        return (0, True)
    cur = _TURN_DELEGATIONS.get(turn_id, 0)
    return (cur, cur < _effective_flat_limit())


def remaining_flat_delegations(turn_id: str | None) -> int | None:
    """Slots left under the flat per-turn cap (non-orchestration path).

    Returns ``None`` when enforcement is off (``turn_id`` is ``None`` — no
    ambient Session), so callers can distinguish "unlimited" from "zero left".
    This is the batch-side complement to :func:`check_absolute_cap`: a
    parallel fan-out truncates its spec list to this many BEFORE spawning, so
    one big call can't overshoot the cap the way a single pre-check would (the
    pre-check reads the counter once, then every spec runs unconditionally).
    """
    if not turn_id:
        return None
    return max(0, _effective_flat_limit() - _TURN_DELEGATIONS.get(turn_id, 0))


def record_delegation(
    turn_id: str | None,
    fingerprint: str,
    *,
    succeeded: bool,
    budget: OrchestrationBudget | None = None,
) -> None:
    """Record a delegation attempt.

    Under an active orchestration budget the envelope is a hard TOTAL: every
    spawn charges one unit (no smart-budget free retries — the bound is the
    point), and the per-turn counter/fingerprints are left untouched.

    Otherwise the per-turn smart-budget rules apply:

    * Success → bump counter (counts against absolute cap)
    * First-time failure (fingerprint not seen) → record fingerprint,
      DO NOT bump counter (free retry for the LLM to fix the spec)
    * Repeat failure (fingerprint already seen) → bump counter
      (treat as wasted call, prevents infinite loops)
    """
    env = budget if budget is not None else current_orchestration_budget()
    if env is not None:
        env.charge()
        return
    if not turn_id:
        return
    failed_fps = _TURN_FAILED_FINGERPRINTS.setdefault(turn_id, set())
    if succeeded:
        # Counts against budget
        _TURN_DELEGATIONS[turn_id] = _TURN_DELEGATIONS.get(turn_id, 0) + 1
        _TURN_DELEGATIONS.move_to_end(turn_id)
    elif fingerprint in failed_fps:
        # Repeat failure — counts (penalizes infinite-loop attempts)
        _TURN_DELEGATIONS[turn_id] = _TURN_DELEGATIONS.get(turn_id, 0) + 1
        _TURN_DELEGATIONS.move_to_end(turn_id)
    else:
        # First-time failure — fingerprint it, DO NOT count
        failed_fps.add(fingerprint)
        _TURN_FAILED_FINGERPRINTS.move_to_end(turn_id)
    # LRU eviction
    while len(_TURN_DELEGATIONS) > _MAX_TRACKED_TURNS:
        _TURN_DELEGATIONS.popitem(last=False)
    while len(_TURN_FAILED_FINGERPRINTS) > _MAX_TRACKED_TURNS:
        _TURN_FAILED_FINGERPRINTS.popitem(last=False)


def bump_and_check(turn_id: str | None) -> tuple[int, bool]:
    """Legacy compat shim: pre-check the absolute cap.

    Kept so existing callers (and tests) work unchanged. Returns the
    same shape but the count is "would-be after this call" — the
    actual increment depends on the result and happens in
    ``record_delegation``.
    """
    cur, within = check_absolute_cap(turn_id)
    return (cur + 1, within)
