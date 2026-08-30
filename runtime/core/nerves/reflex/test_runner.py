from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.reflex.test")


def _walk_expects(raw_data: Any) -> list[dict[str, Any]]:
    """Pull ``expects`` blocks off every rule entry · returns a flat
    list of test cases, each tagged with the rule_id it came from
    so failure messages can identify the source rule.
    """
    cases: list[dict[str, Any]] = []
    if not isinstance(raw_data, dict):
        return cases
    rules = raw_data.get("rules")
    if not isinstance(rules, list):
        return cases
    for entry in rules:
        if not isinstance(entry, dict):
            continue
        rid = str(entry.get("id") or "?")
        exps = entry.get("expects")
        if not isinstance(exps, list):
            continue
        for case in exps:
            if not isinstance(case, dict):
                continue
            cases.append(
                {
                    "source_rule_id": rid,
                    "input": str(case.get("input") or "").strip(),
                    "should": str(case.get("should") or "hit").lower(),
                    "rule_id": case.get("rule_id"),
                    "actor": case.get("actor"),
                    "time": case.get("time"),
                }
            )
    return cases


def _evaluate_one(
    case: dict[str, Any],
    reflex_router: Any,
) -> dict[str, Any]:
    """Run one test case · returns a dict with pass/fail + diagnostic
    fields. Doesn't raise · CI consumers want a list of all failures,
    not just the first.
    """
    from runtime.core.nerves.reflex.gating import GatingSpec  # noqa: F401
    from runtime.core.nerves.reflex.reflex_router import ReflexMatch
    from runtime.platform.models import ParsedIntent

    text = case["input"]
    if not text:
        return {**case, "passed": False, "reason": "empty input"}

    intent = ParsedIntent(
        raw=text,
        intent_type="task",
        normalized_goal=text,
        user_context={},
    )

    # Override the gating evaluator's "now" by patching the router's
    # snapshot · cleanest path without re-architecting try_match. We
    # temporarily set a synthetic actor/time on each gated rule's
    # spec via a per-call gating context · simpler: just call gating
    # ourselves, then call the matcher only when the gate would pass.
    actor = case.get("actor")
    when_str = case.get("time")
    when = None
    if when_str:
        try:
            hh, mm = str(when_str).split(":", 1)
            now = _dt.datetime.now()
            when = now.replace(hour=int(hh), minute=int(mm), second=0)
        except (ValueError, AttributeError):
            when = None

    # Manually walk the router so we can inject actor/time without
    # depending on ContextVars (the test runner doesn't have a real
    # session). This duplicates ~10 lines of try_match but is
    # acceptable for test-time predictability.
    matched_rule_id: str | None = None
    matched: ReflexMatch | None = None
    for r in getattr(reflex_router, "_reflexes", []):
        gating = getattr(r, "_gating_spec", None)
        if gating is not None:  # noqa: SIM102
            if not gating.is_active(actor=actor, now=when or _dt.datetime.now()):
                continue
        m = r.try_match(intent)
        if m is None:
            continue
        if m.confidence < reflex_router.default_threshold:
            continue
        matched = m
        matched_rule_id = m.rule_id
        break

    expected_should = case["should"]
    actual_should = "hit" if matched is not None else "miss"
    if expected_should != actual_should:
        return {
            **case,
            "passed": False,
            "reason": f"expected {expected_should!r}, got {actual_should!r}"
            + (f" (matched={matched_rule_id})" if matched_rule_id else ""),
        }
    # If a specific rule_id was demanded, verify it.
    expect_rule = case.get("rule_id")
    if expected_should == "hit" and expect_rule and matched_rule_id != expect_rule:
        return {
            **case,
            "passed": False,
            "reason": f"matched rule {matched_rule_id!r}, expected {expect_rule!r}",
        }
    return {**case, "passed": True, "matched_rule_id": matched_rule_id}


def run_tests(
    reflex_router: Any,
    *,
    rules_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run every ``expects`` case from the rules file · returns a
    summary dict with ``{passed, failed, total, failures, cases}``.

    ``rules_path`` defaults to whatever ``find_default_rules_file()``
    returns · pass an explicit path to test a candidate file before
    deploying it.
    """
    from runtime.core.nerves.reflex.rules_loader import find_default_rules_file

    path = Path(rules_path) if rules_path else find_default_rules_file()
    if path is None or not path.is_file():
        return {
            "passed": 0,
            "failed": 0,
            "total": 0,
            "failures": [],
            "cases": [],
            "error": "rules file not found",
        }

    # Re-parse YAML (cheap · already has yaml dep) so we get the raw
    # expects blocks · the loader strips them when building Reflex
    # objects, so we can't read them off the live router.
    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            import yaml  # type: ignore[import]

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        else:
            import json

            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "passed": 0,
            "failed": 0,
            "total": 0,
            "failures": [],
            "cases": [],
            "error": f"parse failed: {exc}",
        }

    cases = _walk_expects(data)
    if not cases:
        return {
            "passed": 0,
            "failed": 0,
            "total": 0,
            "failures": [],
            "cases": [],
            "note": "no expects blocks found in rules file",
        }

    results = [_evaluate_one(c, reflex_router) for c in cases]
    passed = sum(1 for r in results if r["passed"])
    failures = [r for r in results if not r["passed"]]
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(failures),
        "failures": failures,
        "cases": results,
    }


def format_report(summary: dict[str, Any]) -> str:
    """Render a CI-friendly text report · used by the CLI subcommand
    and surfaced in the admin panel's test pane.
    """
    if "error" in summary:
        return f"ERROR: {summary['error']}"
    if summary.get("note"):
        return f"NOTE: {summary['note']}"
    lines = [
        f"Reflex tests · {summary['passed']}/{summary['total']} passed",
    ]
    for f in summary["failures"]:
        lines.append(f"  ✗ rule={f['source_rule_id']} input={f['input']!r} · {f['reason']}")
    if not summary["failures"]:
        lines.append("  ✓ all green")
    return "\n".join(lines)


__all__ = ["run_tests", "format_report"]
