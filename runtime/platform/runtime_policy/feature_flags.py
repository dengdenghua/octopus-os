"""
Feature flag registry · single source of truth for experimental or
stage-gated capabilities.

Why we need this
----------------
Flags are currently scattered across modules as ``os.environ.get``
calls, with individual defaults and no catalog. That makes it
hard to:
  - Know what flags exist without grepping.
  - Test behavior under combinations.
  - Expose the live set to the frontend so UI can gate experimental
    panels.

All experimental surfaces are listed in one place and default
to off. The list lives in
``runtime/platform/feature_flags.py`` next to the runtime so any
operator can audit which features are even available without
grepping the codebase.

Resolution precedence (highest wins)
------------------------------------
1. ``os.environ[<primary_env>]``
2. ``os.environ[<legacy_env>]`` (for each registered alias)
3. ``<data_dir>/feature_flags.json`` (edit-at-runtime overrides)
4. Code-declared ``default`` in the registry

Each level that specifies a value wins outright — we don't merge.

Values
------
Flags are tri-state-capable. Callers who only need booleans should
use ``is_on(name)``. Callers that want structured config (e.g.
"intelligence.poll_interval = 1800") should use ``value(name)``.

Thread-safety
-------------
The registry holds a single process-wide snapshot. ``reload()``
atomically swaps in a new snapshot — callers who read with ``is_on``
during a reload get either the old snapshot or the new one, never a
torn mix.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.platform.io import read_json_with_backup


@dataclass(frozen=True)
class FlagSpec:
    """Static declaration of a single feature flag.

    ``name``:   canonical dotted name (``intelligence.enabled``).
    ``default``: code-level default when no env/file override is set.
    ``env``:    primary env var. If ``None``, derived as
                ``ECHO_FF_<NAME_UPPERCASE>``.
    ``legacy_env``: additional env var names that are consulted AFTER
                the primary env. Maps pre-existing ad-hoc flags
                (``ECHO_REGEN_ENABLED``, etc.) into the registry
                without forcing a ship-breaking rename.
    ``description``: one-liner shown in ``/api/feature-flags``.
    ``experimental``: hint for the UI; no behavior change.
    ``coerce``: function that maps raw strings to the flag's value
                type. Default coerces "1/true/on/yes" → ``True``,
                "0/false/off/no" → ``False``. Override for int/str.
    """

    name: str
    default: Any
    env: str | None = None
    legacy_env: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""
    experimental: bool = False
    coerce: Callable[[str], Any] | None = None

    @property
    def primary_env(self) -> str:
        return self.env or ("ECHO_FF_" + self.name.upper().replace(".", "_"))


def _coerce_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "on", "yes", "y"}


def _coerce_int(raw: str) -> int:
    return int(raw.strip())


def _coerce_str(raw: str) -> str:
    return raw.strip()


# ═══════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════

_SPECS: dict[str, FlagSpec] = {}
_SPECS_LOCK = threading.Lock()


def register(spec: FlagSpec) -> None:
    """Register (or replace) a flag."""
    with _SPECS_LOCK:
        _SPECS[spec.name] = spec


def get_spec(name: str) -> FlagSpec | None:
    with _SPECS_LOCK:
        return _SPECS.get(name)


def all_specs() -> list[FlagSpec]:
    with _SPECS_LOCK:
        return sorted(_SPECS.values(), key=lambda s: s.name)


# ═══════════════════════════════════════════════════════════
# Resolver
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _Snapshot:
    values: dict[str, Any]
    sources: dict[str, str]  # name → "env" | "legacy_env:<name>" | "file" | "default"


def _file_overrides(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    data = read_json_with_backup(path, default={})
    if isinstance(data, dict):
        return data
    return {}


def _resolve(
    specs: dict[str, FlagSpec],
    file_path: Path | None,
) -> _Snapshot:
    file_values = _file_overrides(file_path)
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}

    for name, spec in specs.items():
        coerce = spec.coerce or (
            _coerce_bool
            if isinstance(spec.default, bool)
            else _coerce_int
            if isinstance(spec.default, int)
            else _coerce_str
        )

        raw = os.environ.get(spec.primary_env)
        if raw is not None:
            try:
                values[name] = coerce(raw)
                sources[name] = "env"
                continue
            except (ValueError, TypeError):  # noqa: BLE001 — env-var coercion failed; skip to next source
                pass

        legacy_hit = False
        for alias in spec.legacy_env:
            raw = os.environ.get(alias)
            if raw is not None:
                try:
                    values[name] = coerce(raw)
                    sources[name] = f"legacy_env:{alias}"
                    legacy_hit = True
                    break
                except (ValueError, TypeError):
                    continue
        if legacy_hit:
            continue

        if name in file_values:
            raw_value = file_values[name]
            if isinstance(raw_value, str):
                try:
                    values[name] = coerce(raw_value)
                    sources[name] = "file"
                    continue
                except (ValueError, TypeError):  # noqa: BLE001 — file source coercion failed; skip to next source
                    pass
            else:
                values[name] = raw_value
                sources[name] = "file"
                continue

        values[name] = spec.default
        sources[name] = "default"

    return _Snapshot(values=values, sources=sources)


# ═══════════════════════════════════════════════════════════
# Public API (process-wide singleton)
# ═══════════════════════════════════════════════════════════


_SNAPSHOT: _Snapshot | None = None
_SNAPSHOT_LOCK = threading.Lock()
_FILE_PATH: Path | None = None


def configure(file_path: Path | str | None) -> None:
    """Point the registry at an overrides file. Triggers a reload."""
    global _FILE_PATH
    _FILE_PATH = Path(file_path) if file_path is not None else None
    reload()


def reload() -> None:
    """Re-resolve every flag from env + file + defaults."""
    global _SNAPSHOT
    with _SPECS_LOCK:
        specs = dict(_SPECS)
    snap = _resolve(specs, _FILE_PATH)
    with _SNAPSHOT_LOCK:
        _SNAPSHOT = snap


def _ensure_snapshot() -> _Snapshot:
    with _SNAPSHOT_LOCK:
        if _SNAPSHOT is None:
            # Lazy first load; honors whatever was registered to date.
            pass
    if _SNAPSHOT is None:
        reload()
    assert _SNAPSHOT is not None
    return _SNAPSHOT


def value(name: str, fallback: Any = None) -> Any:
    """Return the resolved value for ``name`` or ``fallback``.

    Unknown names return ``fallback`` without raising — this makes
    the call-site resilient to deleted flags.
    """
    snap = _ensure_snapshot()
    return snap.values.get(name, fallback)


def is_on(name: str) -> bool:
    """Truthy check. Missing flags return ``False``."""
    return bool(value(name, False))


def snapshot() -> dict[str, Any]:
    """Immutable dict of resolved values."""
    return dict(_ensure_snapshot().values)


def source_of(name: str) -> str | None:
    """Which layer won for this flag (``env`` / ``legacy_env:...`` /
    ``file`` / ``default``). Useful for debugging.
    """
    return _ensure_snapshot().sources.get(name)


def resolution(name: str, fallback: Any = None) -> tuple[Any, str | None]:
    """Return one flag's value and source from the same immutable snapshot.

    Security-sensitive gates must not compose :func:`value` and
    :func:`source_of` as two separate reads: an administrative reload between
    them could otherwise pair an old source with a new value.
    """

    snap = _ensure_snapshot()
    return snap.values.get(name, fallback), snap.sources.get(name)


def describe() -> list[dict[str, Any]]:
    """Catalog form used by ``GET /api/feature-flags``.

    Each entry:
      { name, value, source, default, description, experimental,
        primary_env, legacy_env }
    """
    snap = _ensure_snapshot()
    specs = all_specs()
    return [
        {
            "name": spec.name,
            "value": snap.values.get(spec.name, spec.default),
            "source": snap.sources.get(spec.name, "default"),
            "default": spec.default,
            "description": spec.description,
            "experimental": spec.experimental,
            "primary_env": spec.primary_env,
            "legacy_env": list(spec.legacy_env),
        }
        for spec in specs
    ]


# ═══════════════════════════════════════════════════════════
# Built-in flag catalog
# ═══════════════════════════════════════════════════════════
#
# Every flag that used to be a lone ``os.environ.get(...)`` call
# should graduate here. Order: oldest / most established first,
# experimental last.
#
# Each legacy_env entry preserves the exact env name call sites
# already use — migrating a call site is then a one-line change
# (read via ``is_on`` / ``value`` instead of env directly).
#

_BUILTIN: list[FlagSpec] = [
    # ─── Safety · invariant enforcement ────────────────────
    FlagSpec(
        name="safety.invariants_enabled",
        default=True,
        legacy_env=("ECHO_INVARIANTS",),
        description=(
            "Enforce the 34-rule constitution during execution. Disable only for debugging."
        ),
        coerce=lambda raw: raw.strip().lower() != "off",
    ),
    # ─── Self-evolution ────────────────────────────────────
    FlagSpec(
        name="evolution.auto_trigger",
        default=True,
        description="Enable automatic evolution trigger when fitness drops below threshold",
    ),
    FlagSpec(
        name="regeneration.enabled",
        default=True,
        legacy_env=("ECHO_REGEN_ENABLED",),
        description="Background self-repair scheduler (GEPA variants + rollback).",
    ),
    FlagSpec(
        name="regeneration.interval_sec",
        default=600,
        legacy_env=("ECHO_REGEN_INTERVAL_SEC",),
        description="Tick interval for the regeneration scheduler.",
    ),
    FlagSpec(
        name="regeneration.gepa_auto_apply",
        default=False,
        legacy_env=("ECHO_GEPA_AUTO_APPLY",),
        description=("Automatically apply GEPA-proposed prompt variants without human review."),
        experimental=True,
    ),
    # ─── Camouflage · prompt evolution ─────────────────────
    FlagSpec(
        name="camouflage.enabled",
        default=True,
        legacy_env=("ECHO_CAMOUFLAGE_ENABLED",),
        description="LLM-driven prompt optimizer loop (A/B variants + pareto retirement).",
        experimental=True,
    ),
    FlagSpec(
        name="camouflage.interval_sec",
        default=600,
        legacy_env=("ECHO_CAMOUFLAGE_INTERVAL_SEC",),
        description="Tick interval for the camouflage scheduler.",
    ),
    # ─── Intelligence (daily brief) ────────────────────────
    # ─── Session store ─────────────────────────────────────
    FlagSpec(
        name="intelligence.poll_interval_sec",
        default=1800,
        legacy_env=("ECHO_INTELLIGENCE_POLL_SECONDS",),
        description="Polling interval for configured intelligence sources.",
    ),
    FlagSpec(
        name="sessions.dated_layout",
        default=False,
        description=(
            "Shard per-thread jsonl files under "
            "<agent>/sessions/<YYYY>/<MM>/. Off keeps the flat layout."
        ),
    ),
    FlagSpec(
        name="sessions.index_enabled",
        default=True,
        description="Maintain session_index.jsonl for fast thread listing.",
    ),
    # ─── Execution backends ────────────────────────────────
    FlagSpec(
        name="execution.codex_app_server",
        default=True,
        legacy_env=("ECHO_CODEX_APP_SERVER_ENABLED",),
        description=(
            "Use the isolated Codex App Server backend for a selected local "
            "standard agent role. Production-like deployments additionally "
            "require an explicit non-default enablement and a hard sandbox."
        ),
        experimental=True,
    ),
    # ─── Experimental surfaces ────────────────────────────
    FlagSpec(
        name="ui.ambient_suggestions",
        default=True,
        description=(
            "Surface AI-generated follow-up tasks based on recent "
            "conversations. Experimental; enabled by default. Degrades "
            "to an empty bucket until scored turn history exists."
        ),
        experimental=True,
    ),
    FlagSpec(
        name="ui.ambient_suggestions_interval_sec",
        default=6 * 60 * 60,
        description=(
            "Tick interval (seconds) for the ambient-suggestions "
            "background scheduler. Defaults to 6 hours."
        ),
        experimental=True,
    ),
    FlagSpec(
        name="ui.prompts_hot_reload",
        default=False,
        description=(
            "Pick up prompt template edits without a restart. "
            "Pre-req for A/B-testing personalities."
        ),
        experimental=True,
    ),
    FlagSpec(
        name="ui.remote_transport",
        default=False,
        description=(
            "Expose SSH/WebSocket transport so a local desktop can "
            "connect to a remote echo-agent runtime."
        ),
        experimental=True,
    ),
    # EXPERIMENTAL: remote workspace collaboration is delivered but OFF by
    # default. While this flag is off, every /api/workspaces endpoint
    # returns 403 and frontend entry points (e.g. WorkspaceSwitcher) are
    # unavailable. Do NOT enable in production without a staged (灰度)
    # rollout and validation first.
    FlagSpec(
        name="ui.remote_workspace",
        default=False,
        description=(
            "EXPERIMENTAL (default off, requires staged rollout before "
            "enabling): expose the Workspace HTTP API (mount + membership "
            "+ file lease endpoints under /api/workspaces). While off, "
            "all /api/workspaces endpoints return 403 and frontend "
            "workspace entry points such as WorkspaceSwitcher are hidden."
        ),
        experimental=True,
    ),
]

for _spec in _BUILTIN:
    register(_spec)


__all__ = [
    "FlagSpec",
    "all_specs",
    "configure",
    "describe",
    "get_spec",
    "is_on",
    "register",
    "resolution",
    "reload",
    "snapshot",
    "source_of",
    "value",
]
