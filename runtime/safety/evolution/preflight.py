"""Production preflight CLI — dress-rehearsal report for ops.

Run before flipping any P0/P1/P2/P3 knob to production. Prints the
current state of every environment variable, YAML setting, and
optional dependency that affects echo's safety / evolution stack,
plus inferred status (on/off/needs-attention) for each.

Usage::

    python -m runtime.safety.evolution.preflight
    python -m runtime.safety.evolution.preflight --json

Exit codes
----------
* 0 — preflight ran (regardless of warnings; ops decides)
* 1 — preflight itself crashed; cron should alert
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.preflight")


@dataclass
class PreflightResult:
    """Structured rehearsal output. Renderable as text or JSON."""

    env: dict[str, str | None] = field(default_factory=dict)
    yaml_settings: dict[str, Any] = field(default_factory=dict)
    optional_deps: dict[str, bool] = field(default_factory=dict)
    feature_status: dict[str, str] = field(default_factory=dict)
    journal: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ── Probes ─────────────────────────────────────────────────────────


_INTEREST_ENV_VARS = (
    "ECHO_DISABLED_GUARDS",
    "ECHO_CHECKPOINT_EVERY_N",
    "ECHO_CHECKPOINT_MIRROR_URL",
    "ECHO_ENABLE_TRUST_SIGNAL",
    "ECHO_DISABLE_GUARD_TELEMETRY",
)


def _probe_env() -> dict[str, str | None]:
    return {name: os.environ.get(name) for name in _INTEREST_ENV_VARS}


def _probe_yaml() -> dict[str, Any]:
    """Best-effort read of safety.* keys from the project's yaml."""
    out: dict[str, Any] = {
        "config_path": None,
        "disabled_guards": [],
        "enable_trust_signal": None,
    }
    for path in ("config.local.yaml", "config.yaml", "config.example.yaml"):
        if not Path(path).exists():
            continue
        try:
            import yaml  # type: ignore[import-untyped]

            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh.read()) or {}
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("yaml read failed for %s: %s", path, exc)
            continue
        if not isinstance(data, dict):
            continue
        out["config_path"] = path
        safety = data.get("safety") or {}
        if isinstance(safety, dict):
            disabled = safety.get("disabled_guards")
            if isinstance(disabled, list):
                out["disabled_guards"] = [
                    str(x).strip() for x in disabled if isinstance(x, str) and str(x).strip()
                ]
            ets = safety.get("enable_trust_signal")
            if isinstance(ets, bool):
                out["enable_trust_signal"] = ets
        break  # first found wins (matches loader precedence)
    return out


def _probe_optional_deps() -> dict[str, bool]:
    """Check whether optional packages are importable."""
    out: dict[str, bool] = {}
    for module in ("yaml", "redis"):
        try:
            __import__(module)
            out[module] = True
        except ImportError:
            out[module] = False
    return out


def _probe_journal() -> dict[str, Any]:
    """Inspect the default guard-hits jsonl for size + recency."""
    out: dict[str, Any] = {"path": None, "size_bytes": 0, "exists": False}
    path = Path("data/guard_hits.jsonl")
    out["path"] = str(path)
    if path.exists():
        out["exists"] = True
        with contextlib.suppress(OSError):
            out["size_bytes"] = path.stat().st_size
    return out


def _classify_features(
    env: dict[str, str | None],
    yaml_settings: dict[str, Any],
    deps: dict[str, bool],
) -> dict[str, str]:
    """Derive per-feature on/off/partial status."""
    out: dict[str, str] = {}

    # P3 — auto-checkpoint
    raw = (env.get("ECHO_CHECKPOINT_EVERY_N") or "").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 0
    out["P3_auto_checkpoint"] = "on" if n > 0 else "off"

    # P3 — distributed mirror
    mirror_url = (env.get("ECHO_CHECKPOINT_MIRROR_URL") or "").strip()
    if mirror_url and deps.get("redis", False):
        out["P3_distributed_mirror"] = "on"
    elif mirror_url and not deps.get("redis", False):
        out["P3_distributed_mirror"] = "needs-attention (redis pkg missing)"
    else:
        out["P3_distributed_mirror"] = "off"

    # P0 — trust gate (kwarg/env/yaml tri-state)
    raw_ets = (env.get("ECHO_ENABLE_TRUST_SIGNAL") or "").strip().lower()
    if raw_ets in ("1", "true", "yes", "on"):
        out["P0_trust_gate"] = "on (env)"
    elif raw_ets in ("0", "false", "no", "off"):
        out["P0_trust_gate"] = "off (env)"
    elif yaml_settings.get("enable_trust_signal") is True:
        out["P0_trust_gate"] = "on (yaml)"
    elif yaml_settings.get("enable_trust_signal") is False:
        out["P0_trust_gate"] = "off (yaml)"
    else:
        out["P0_trust_gate"] = "off (default)"

    # P1 — guard telemetry
    if (env.get("ECHO_DISABLE_GUARD_TELEMETRY") or "").strip() == "1":
        out["P1_guard_telemetry"] = "off (explicitly disabled)"
    else:
        out["P1_guard_telemetry"] = "on (default)"

    # Kill-switch summary
    env_disabled = (env.get("ECHO_DISABLED_GUARDS") or "").strip()
    yaml_disabled = yaml_settings.get("disabled_guards") or []
    total_disabled = len([x for x in env_disabled.split(",") if x.strip()]) + len(yaml_disabled)
    if total_disabled > 0:
        out["kill_switch"] = (
            f"{total_disabled} guard(s) disabled "
            f"(env={bool(env_disabled)} yaml={len(yaml_disabled)})"
        )
    else:
        out["kill_switch"] = "all guards active"

    return out


def _build_warnings(
    env: dict[str, str | None],
    yaml_settings: dict[str, Any],
    deps: dict[str, bool],
    journal: dict[str, Any],
) -> list[str]:
    """Surface common operator footguns."""
    warnings: list[str] = []
    if (env.get("ECHO_CHECKPOINT_MIRROR_URL") or "").strip() and not deps.get("redis", False):
        warnings.append(
            "ECHO_CHECKPOINT_MIRROR_URL is set but `redis` package "
            "isn't importable — distributed mirror will silently degrade. "
            "pip install redis[hiredis] to enable.",
        )
    if not deps.get("yaml", False):
        warnings.append(
            "PyYAML not importable — yaml-driven kill-switch / trust-signal "
            "settings will be ignored. pip install PyYAML.",
        )
    if not journal.get("exists"):
        warnings.append(
            f"Guard hits journal {journal.get('path')!r} doesn't exist yet. "
            "First run will create it.",
        )
    n_raw = (env.get("ECHO_CHECKPOINT_EVERY_N") or "").strip()
    if n_raw:
        try:
            n = int(n_raw)
            if n < 0:
                warnings.append(
                    f"ECHO_CHECKPOINT_EVERY_N={n_raw!r} parses negative — "
                    "treated as off, did you mean a positive integer?",
                )
        except ValueError:
            warnings.append(
                f"ECHO_CHECKPOINT_EVERY_N={n_raw!r} is not an int — auto-checkpoint will be off.",
            )
    return warnings


# ── Top-level orchestration + render ───────────────────────────────


def run_preflight() -> PreflightResult:
    env = _probe_env()
    yaml_settings = _probe_yaml()
    deps = _probe_optional_deps()
    journal = _probe_journal()
    feature_status = _classify_features(env, yaml_settings, deps)
    warnings = _build_warnings(env, yaml_settings, deps, journal)
    return PreflightResult(
        env=env,
        yaml_settings=yaml_settings,
        optional_deps=deps,
        feature_status=feature_status,
        journal=journal,
        warnings=warnings,
    )


def render_text(result: PreflightResult) -> str:
    lines = ["Echo preflight (P0/P1/P2/P3 dress rehearsal)", ""]
    lines.append("Environment:")
    for k, v in result.env.items():
        shown = "(unset)" if v is None else (v if len(v) <= 60 else v[:57] + "...")
        lines.append(f"  {k:36s} = {shown}")
    lines.append("")
    lines.append("YAML settings:")
    yc = result.yaml_settings
    lines.append(
        f"  config_path                          = {yc.get('config_path') or '(none found)'}"
    )
    lines.append(f"  safety.disabled_guards               = {yc.get('disabled_guards')}")
    lines.append(f"  safety.enable_trust_signal           = {yc.get('enable_trust_signal')}")
    lines.append("")
    lines.append("Optional deps:")
    for dep, ok in result.optional_deps.items():
        lines.append(f"  {dep:12s} = {'available' if ok else 'NOT INSTALLED'}")
    lines.append("")
    lines.append("Journal:")
    lines.append(
        f"  {result.journal.get('path')} "
        f"(exists={result.journal.get('exists')}, "
        f"size={result.journal.get('size_bytes')} bytes)",
    )
    lines.append("")
    lines.append("Feature status:")
    for feat, status in result.feature_status.items():
        lines.append(f"  {feat:28s} : {status}")
    lines.append("")
    if result.warnings:
        lines.append("Warnings:")
        for w in result.warnings:
            lines.append(f"  - {w}")
    else:
        lines.append("No warnings.")
    return "\n".join(lines)


def render_json(result: PreflightResult) -> str:
    """Machine-readable form for CI / dashboards."""
    return json.dumps(
        {
            "env": result.env,
            "yaml_settings": result.yaml_settings,
            "optional_deps": result.optional_deps,
            "feature_status": result.feature_status,
            "journal": result.journal,
            "warnings": result.warnings,
        },
        ensure_ascii=False,
        indent=2,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="preflight",
        description=(
            "Pre-deployment dress rehearsal — print env/yaml/dep status "
            "for echo's P0/P1/P2/P3 stack."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of text.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        args = _parse_args(argv)
        result = run_preflight()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("preflight failed: %s", exc)
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(render_json(result))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PreflightResult",
    "main",
    "render_json",
    "render_text",
    "run_preflight",
]
