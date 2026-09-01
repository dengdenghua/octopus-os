"""Pure helper functions for the reflex / gene-locks / forge admin routes.

These were extracted from ``reflex_admin_router.py`` so the router
module stays small. They carry no routing state and are safe to
call from any endpoint builder.
"""

from __future__ import annotations


def snapshot_rules(rules: list) -> dict[str, dict]:
    """Capture the comparable shape of each rule · we only
    include fields that, if changed, make the rule semantically
    different (pattern, priority, intent_type, ttl, action
    presence, variant count). Hit counts deliberately excluded
    so a "no real change" reload doesn't show as a modify."""
    snap: dict[str, dict] = {}
    for r in rules:
        pat = getattr(r, "_regex", None)
        spec = getattr(r, "_action_spec", None)
        vars_ = getattr(r, "_variants", None)
        snap[r.rule_id] = {
            "kind": r.kind,
            "priority": r.priority,
            "pattern": pat.pattern if pat is not None else None,
            "intent_type": getattr(r, "_intent_type", None),
            "ttl_seconds": getattr(r, "_ttl_seconds", None),
            "actions": sorted(
                k
                for k in ("webhook", "mqtt", "exec")
                if spec is not None and getattr(spec, k, None) is not None
            )
            if spec
            else [],
            "variant_count": len(vars_) if vars_ else 0,
        }
    return snap


def gate_forge_mutation(
    kind: str,
    target: str,
    *,
    approver: str | None,
    bypass_cooldown: bool = False,
) -> dict:
    """Thin wrapper around gene_locks.gate_mutation that
    turns ``LockViolation`` into a consistent dict the
    endpoint returns verbatim (HTTP 200 with
    ``ok=False + gene_lock_violation=True``). Easier for
    the frontend than parsing a 403."""
    from runtime.safety.gene_locks import LockViolation, gate_mutation

    try:
        return gate_mutation(
            kind=kind,
            target=target,
            autonomous=approver is None,
            approver=approver,
            bypass_cooldown=bypass_cooldown,
        )
    except LockViolation as lv:
        return lv.as_dict()


def new_reload_state() -> dict:
    """Fresh container for the most recent reload diff."""
    return {
        "ts": None,
        "added": [],
        "removed": [],
        "modified": [],
        "unchanged_count": 0,
    }
