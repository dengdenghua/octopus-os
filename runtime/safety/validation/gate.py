"""The constitution gate · the single entry point channel adapters
and outbound-path code call before sending anything externally.

Contract
--------

    verdict = check_outbound(message, destination, session=None)

    verdict.action ∈ {"allow", "rewrite", "block", "human_gate"}
    verdict.sanitized_text · safe to send when action == "rewrite"
    verdict.violations · list of clause IDs hit
    verdict.reason · human-readable summary (PII-scrubbed)

Gate does NOT raise. It returns a verdict · the caller decides
(send / replace / drop / surface to user). This separation keeps
gate testable in isolation and keeps caller control flow linear.

Today this is **rule-layer only** · regex PII + secret patterns.
The LLM-judge layer (for semantic violations like "asked to help
write ransomware" or "asked to scrape a login-walled site") is
a later iteration that will plug in behind the same Verdict
interface.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

from .rules import RuleHit, scan_pii, scan_secrets, scrub_pii

ActionKind = Literal["allow", "rewrite", "block", "human_gate"]


@dataclass(frozen=True)
class Verdict:
    """Result of a ``check_outbound`` call.

    * ``allow``      — nothing detected, forward the original text.
    * ``rewrite``    — PII detected but scrubbable; caller SHOULD
                       send ``sanitized_text`` instead of the input.
    * ``block``      — secret / unscrubbable violation; caller MUST
                       NOT send.
    * ``human_gate`` — reserved for future escalation path; today
                       never returned by the rule layer (will come
                       from LLM-judge layer).
    """

    action: ActionKind
    sanitized_text: str
    violations: list[RuleHit] = field(default_factory=list)
    reason: str = ""
    # sha256 of the original input · journal may log this
    original_text_hash: str = ""
    # sha256 of the sanitized output · if different
    sanitized_text_hash: str | None = None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _resolve_overrides(
    session: Any,
    overrides: dict[str, str] | None,
) -> dict[str, str]:
    """Merge overrides from explicit arg + session.agent metadata.

    Explicit ``overrides`` kwarg wins over agent-level config · lets
    tests pass targeted maps without needing a fake Session.

    Agent-level shape (read from ``agent.capabilities`` for now ·
    future: dedicated ``agent.constitution_overrides`` attr)::

        agent.capabilities = {
            "constitution_overrides": {"DGNT-4": "journal"},
        }

    Values must be ``"journal"`` or ``"block"``. Unknown values
    are dropped silently so a typo can't weaken the gate.
    """
    merged: dict[str, str] = {}
    if session is not None:
        agent = getattr(session, "agent", None)
        if agent is not None:
            caps = getattr(agent, "capabilities", None) or {}
            raw = caps.get("constitution_overrides") if isinstance(caps, dict) else None
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(k, str) and v in ("journal", "block"):
                        merged[k] = v
    if overrides:
        for k, v in overrides.items():
            if isinstance(k, str) and v in ("journal", "block"):
                merged[k] = v
    return merged


def _trust_signal_enabled(explicit: bool | None) -> bool:
    """Resolve the trust-signal opt-in flag with a 3-layer precedence:

    1. ``explicit`` kwarg (``True`` / ``False``) — caller wins.
    2. ``ECHO_ENABLE_TRUST_SIGNAL`` env var (``"1"``/``"true"`` on,
       ``"0"``/``"false"`` off — case-insensitive).
    3. ``safety.enable_trust_signal`` in config.local.yaml /
       config.yaml / config.example.yaml.

    Defaults to ``False`` when none of the layers say anything.
    Failures (yaml broken, env malformed) silently fall to next layer.
    """
    if explicit is not None:
        return explicit
    import os

    raw_env = os.environ.get("ECHO_ENABLE_TRUST_SIGNAL", "").strip().lower()
    if raw_env in ("1", "true", "yes", "on"):
        return True
    if raw_env in ("0", "false", "no", "off"):
        return False
    # Fall through to yaml.
    for path in ("config.local.yaml", "config.yaml", "config.example.yaml"):
        try:
            if not os.path.exists(path):
                continue
            import yaml  # type: ignore[import-untyped]

            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh.read()) or {}
            if not isinstance(data, dict):
                continue
            safety = data.get("safety") or {}
            if not isinstance(safety, dict):
                continue
            value = safety.get("enable_trust_signal")
            if isinstance(value, bool):
                return value
        except Exception:  # noqa: BLE001 — settings must not crash gate
            continue
    return False


def check_outbound(
    message: str,
    destination: str,
    session: Any = None,
    *,
    owner_destinations: tuple[str, ...] = ("owner", "owner:"),
    overrides: dict[str, str] | None = None,
    enable_trust_signal: bool | None = None,
) -> Verdict:
    """Apply the rule-layer gate to ``message``.

    Parameters
    ----------
    message :
        The text about to be sent out.
    destination :
        Where it's going. Shape ``"<kind>:<subkind>:<id>"`` for
        channels (``"channels:discord:ch-123"``) or ``"owner"``
        for the current user's own surfaces (own IDE / own email
        reply thread / own log). Owner destinations bypass PII
        Rewrite · the owner seeing their own email address in
        their own IDE isn't a leak.
    session :
        Current Session (optional) · used for audit tag + per-agent
        clause overrides read from ``session.agent.capabilities
        .constitution_overrides``.
    owner_destinations :
        Prefix set that marks "this is not a leak destination" ·
        injectable for tests.
    overrides :
        Per-call clause override map · ``{clause_id: "journal" | "block"}``.
        ``"journal"`` downgrades a hit on that clause to audit-only
        (let through · record violations) · ``"block"`` upgrades
        it to an unconditional block even if the profile / secret
        rules would have just scrubbed. Per-call map merges on top
        of the agent-level map.
    enable_trust_signal :
        Tri-state opt-in. ``True`` / ``False`` force the behaviour
        for this call. ``None`` (default) defers to the env var
        ``ECHO_ENABLE_TRUST_SIGNAL`` and then to
        ``safety.enable_trust_signal`` in config.local.yaml /
        config.yaml / config.example.yaml. When effectively True,
        after all hard rules pass, the gate checks the guard-
        telemetry-derived trust score. If trust is "suspect"
        (≤ 0.20 — agent has been actively tripping real security
        guards), the gate escalates a clean ``allow`` to
        ``human_gate`` to surface the message for human review.
        Trust signal NEVER relaxes a block or rewrite — it only
        tightens an allow. Telemetry / scoring failures degrade
        to no-op, not to allow. Owner destinations are exempt
        (no human review needed for the owner's own surfaces).

    Returns
    -------
    Verdict · never raises. Caller branches on ``verdict.action``.
    """
    orig_hash = _hash(message)
    clause_overrides = _resolve_overrides(session, overrides)

    is_owner = destination in owner_destinations or any(
        destination.startswith(p) for p in owner_destinations
    )

    # ── Pass 1: secrets (Block · never Rewrite) ─────────────
    # Secret leaks aren't "oopsie-then-scrub" · once an API key
    # reaches the network the token is compromised. Block is the
    # only safe action · even for owner destinations (the owner
    # might paste the log into Slack later).
    secret_hits = scan_secrets(message)
    if secret_hits:
        # Secrets are hard-floor · ``journal`` override is NOT
        # honored (would let credentials on the wire · hard no).
        # We explicitly log an attempt rather than silently
        # promoting · so misconfiguration gets noticed.
        journal_attempts = [
            h for h in secret_hits if clause_overrides.get(h.clause_id) == "journal"
        ]
        if journal_attempts:
            import logging

            logging.getLogger(
                "runtime.safety.validation.gate",
            ).warning(
                "override 'journal' for %d secret hit(s) IGNORED · "
                "secrets always block (hard floor)",
                len(journal_attempts),
            )
        reason = f"blocked: {len(secret_hits)} secret pattern(s) · " + ", ".join(
            sorted({h.description for h in secret_hits})
        )
        return Verdict(
            action="block",
            sanitized_text="",
            violations=list(secret_hits),
            reason=reason,
            original_text_hash=orig_hash,
        )

    # ── Pass 2: PII (Rewrite to owner-safe · Block to external)
    # For owner destinations · PII is allowed (their own data).
    # For external · Rewrite with placeholders so the message
    # still lands with the intent but without the identifiers.
    if is_owner:
        pii_hits = scan_pii(message)  # still record for audit
        return Verdict(
            action="allow",
            sanitized_text=message,
            violations=list(pii_hits),
            reason="owner destination · PII allowed",
            original_text_hash=orig_hash,
        )

    from .profiles import enforces_pii_rewrite

    sanitized, pii_hits = scrub_pii(message)
    if pii_hits:
        # Clause-level "block" override · any hit on an overridden
        # clause forces a block (even if profile would just scrub).
        blocking_hits = [h for h in pii_hits if clause_overrides.get(h.clause_id) == "block"]
        if blocking_hits:
            return Verdict(
                action="block",
                sanitized_text="",
                violations=list(pii_hits),
                reason=(
                    "blocked: override block on clause(s) "
                    + ", ".join(sorted({h.clause_id for h in blocking_hits}))
                ),
                original_text_hash=orig_hash,
            )
        # Clause-level "journal" override · if ALL hits are journal-
        # overridden, downgrade to audit-only allow. Mixed case
        # (some journal + some not) falls through to the profile's
        # rewrite behavior on the non-overridden hits.
        non_journal_hits = [h for h in pii_hits if clause_overrides.get(h.clause_id) != "journal"]
        if not non_journal_hits and pii_hits:
            return Verdict(
                action="allow",
                sanitized_text=message,
                violations=list(pii_hits),
                reason=(
                    f"audit_only_override: {len(pii_hits)} PII match(es) "
                    "on journal-overridden clause(s)"
                ),
                original_text_hash=orig_hash,
            )
        if enforces_pii_rewrite():
            sanitized_hash = _hash(sanitized)
            reason = f"rewrote: {len(pii_hits)} PII match(es) · " + ", ".join(
                sorted({h.description for h in pii_hits})
            )
            return Verdict(
                action="rewrite",
                sanitized_text=sanitized,
                violations=list(pii_hits),
                reason=reason,
                original_text_hash=orig_hash,
                sanitized_text_hash=sanitized_hash,
            )
        # lax profile · PII allowed through · violations still
        # recorded so the journal can surface audit reports.
        return Verdict(
            action="allow",
            sanitized_text=message,
            violations=list(pii_hits),
            reason=(f"audit_only: {len(pii_hits)} PII match(es) · profile=lax · not rewritten"),
            original_text_hash=orig_hash,
        )

    # ── Pass 3: LLM judge (semantic gate for cases regex misses)
    # Default judge is null · if no judge wired at bootstrap, this
    # is a no-op. A real judge can upgrade ``allow`` to ``block``
    # or ``human_gate`` based on content the regex can't reason
    # about (e.g. ransomware-authoring requests phrased in prose).
    try:
        from .judge import get_judge
        from .profiles import enforces_judge_verdict

        jv = get_judge()(message, destination, session)
        judge_hard = enforces_judge_verdict()
        if jv.action == "block":
            if judge_hard:
                return Verdict(
                    action="block",
                    sanitized_text="",
                    violations=[],
                    reason=f"judge_block: {jv.reason}",
                    original_text_hash=orig_hash,
                )
            # normal/lax · audit-only · let the message through
            # but tag the reason so downstream log pipelines can
            # surface the would-be block for human review.
            return Verdict(
                action="allow",
                sanitized_text=message,
                violations=[],
                reason=f"audit_only_judge_block: {jv.reason}",
                original_text_hash=orig_hash,
            )
        if jv.action == "human_gate":
            if judge_hard:
                return Verdict(
                    action="human_gate",
                    sanitized_text=message,
                    violations=[],
                    reason=f"judge_escalate: {jv.reason}",
                    original_text_hash=orig_hash,
                )
            return Verdict(
                action="allow",
                sanitized_text=message,
                violations=[],
                reason=f"audit_only_judge_escalate: {jv.reason}",
                original_text_hash=orig_hash,
            )
    except Exception:  # noqa: BLE001 — judge must never crash gate
        pass

    # ── Pass 4: trust signal (opt-in escalation) ─────────────
    # When enabled and the destination isn't owner-only, consult
    # the guard-telemetry-derived trust score. If the agent has
    # been tripping real security guards repeatedly (suspect
    # bucket), upgrade ``allow`` to ``human_gate`` so a human
    # reviews this message. The signal NEVER relaxes — it only
    # tightens. Owner destinations are exempt because the owner
    # doesn't need to gate their own surfaces.
    if _trust_signal_enabled(enable_trust_signal) and not is_owner:
        try:
            from .trust_signal import (
                classify_trust_score,
                fetch_current_trust_score,
            )

            score = fetch_current_trust_score()
            if classify_trust_score(score) == "suspect":
                return Verdict(
                    action="human_gate",
                    sanitized_text=message,
                    violations=[],
                    reason=(
                        f"trust_signal_escalate: guard trust {score:.2f} "
                        "(suspect) — recent security guards firing on "
                        "real catches; human review recommended"
                    ),
                    original_text_hash=orig_hash,
                )
        except Exception:  # noqa: BLE001 — trust signal must not crash gate
            pass

    # Nothing hit · allow through unchanged.
    return Verdict(
        action="allow",
        sanitized_text=message,
        violations=[],
        reason="",
        original_text_hash=orig_hash,
    )
