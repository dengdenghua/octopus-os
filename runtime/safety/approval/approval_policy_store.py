"""Load / save the user's static approval policy.

Companion to :mod:`runtime.safety.approval.approval_gate`: the gate provides the
``ApprovalPolicy`` data type; this module handles durable persistence
against ``AppPaths.permissions_path`` (defaults to
``<app_paths().data_dir>/permissions.json``).

JSON shape (stable, forward-compatible — unknown keys are ignored)::

    {
      "version": 1,
      "rules": [
        {"effect": "allow", "tool": "read_*"},
        {"effect": "deny",  "tool": "exec_shell", "args_contains": "rm -rf",
         "reason": "destructive"}
      ]
    }

Why a dedicated module:

* Keeps the pure algorithm (rule match) free of IO.
* Gives the API router a single place to call ``append_rule`` from
  when the UI clicks "trust this" during an approval prompt.
* A missing file is a valid empty policy, not an error — the rule
  set is optional by design.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from runtime.safety.approval.approval_gate import ApprovalPolicy, ApprovalRule

_logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

# One lock per path keeps concurrent ``append_rule`` calls from racing
# on the read-modify-write cycle. File paths normalize to the resolved
# absolute form so two callers passing the same file via different
# relative paths still share a lock.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve() if path.exists() else path.absolute())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
    return lock


def _rule_from_json(obj: dict[str, Any]) -> ApprovalRule | None:
    effect = obj.get("effect")
    if effect not in ("allow", "deny"):
        return None
    tool = obj.get("tool") or "*"
    if not isinstance(tool, str):
        return None
    args_contains = obj.get("args_contains") or ""
    reason = obj.get("reason") or ""
    if not isinstance(args_contains, str) or not isinstance(reason, str):
        return None
    return ApprovalRule(
        effect=effect,
        tool=tool,
        args_contains=args_contains,
        reason=reason,
    )


def _rule_to_json(rule: ApprovalRule) -> dict[str, Any]:
    out: dict[str, Any] = {"effect": rule.effect, "tool": rule.tool}
    if rule.args_contains:
        out["args_contains"] = rule.args_contains
    if rule.reason:
        out["reason"] = rule.reason
    return out


def load_policy(path: Path) -> ApprovalPolicy:
    """Read the policy file. Missing file → empty policy.

    Honors the optional ``profile`` field (read_only / workspace_write /
    full_access). An explicit non-default profile replaces the rules with
    the built-in profile rule set; ``workspace_write`` or an absent field
    keeps the rules array exactly as today (byte-identical behavior).
    """
    if not path.exists():
        return ApprovalPolicy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _logger.warning("permissions: failed to read %s (%s); treating as empty", path, exc)
        return ApprovalPolicy()
    if not isinstance(raw, dict):
        return ApprovalPolicy()
    rules_raw = raw.get("rules", [])
    if not isinstance(rules_raw, list):
        return ApprovalPolicy()
    rules: list[ApprovalRule] = []
    for entry in rules_raw:
        if not isinstance(entry, dict):
            continue
        rule = _rule_from_json(entry)
        if rule is not None:
            rules.append(rule)
    from runtime.safety.approval.permission_profiles import resolve_profile

    _profile_name, effective_rules, _auto = resolve_profile(raw.get("profile"), tuple(rules))
    return ApprovalPolicy(rules=effective_rules)


def save_policy(path: Path, policy: ApprovalPolicy) -> None:
    """Write the policy atomically (tmp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _SCHEMA_VERSION,
        "rules": [_rule_to_json(r) for r in policy.rules],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_rule(path: Path, rule: ApprovalRule) -> ApprovalPolicy:
    """Add ``rule`` to the policy at ``path`` and persist.

    Duplicates (identical effect/tool/args_contains) are a no-op — we
    don't want one "trust this" click to grow the rule list forever.
    Returns the resulting policy for the caller's convenience.
    """
    with _lock_for(path):
        policy = load_policy(path)
        for existing in policy.rules:
            if (
                existing.effect == rule.effect
                and existing.tool == rule.tool
                and existing.args_contains == rule.args_contains
            ):
                return policy
        next_policy = ApprovalPolicy(rules=policy.rules + (rule,))
        save_policy(path, next_policy)
        return next_policy


def move_rule(path: Path, from_index: int, to_index: int) -> ApprovalPolicy:
    """Move the rule at ``from_index`` to ``to_index`` and persist.

    Order matters in this rule set: the first match wins, so a narrow
    deny placed after a broad allow never fires. The router exposes
    this via ``POST /api/permissions/rules/{index}/move`` so the UI
    can offer up/down buttons without forcing a delete-and-re-add.

    Both indexes are 0-based and must point at existing rules.
    ``from_index == to_index`` is a no-op (returns the policy
    unchanged without rewriting the file). Out-of-range indexes
    raise ``IndexError`` — the router translates that to 404.
    """
    with _lock_for(path):
        policy = load_policy(path)
        n = len(policy.rules)
        if not (0 <= from_index < n):
            raise IndexError(f"from_index out of range: {from_index} (have {n})")
        if not (0 <= to_index < n):
            raise IndexError(f"to_index out of range: {to_index} (have {n})")
        if from_index == to_index:
            return policy
        rules = list(policy.rules)
        rule = rules.pop(from_index)
        rules.insert(to_index, rule)
        next_policy = ApprovalPolicy(rules=tuple(rules))
        save_policy(path, next_policy)
        return next_policy


__all__ = [
    "append_rule",
    "load_policy",
    "move_rule",
    "save_policy",
]
