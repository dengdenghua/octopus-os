"""
Ambient Suggestions Scheduler · periodically regenerate suggestions
for active agents.

Mirrors the lifecycle pattern of
``runtime.safety.recovery.scheduler.RegenerationScheduler`` and
``runtime.safety.experiments.scheduler.CamouflageScheduler``: a single
daemon thread, ``threading.Event`` cancellable sleep, thread-safe
singleton, and per-tick error isolation.

Each tick:
  1. Bails immediately if ``ui.ambient_suggestions`` is OFF.
  2. Discovers active agents by scanning
     ``<repo>/agents/*/agent-core/.scores.jsonl`` and keeping any
     agent with at least 3 turns in the last 7 days.
  3. For each active agent (capped at ``max_agents_per_tick``),
     calls ``ambient_suggestions.generate_suggestions`` with the
     current project root. Per-agent failures are caught so one
     bad agent never kills the loop.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.ambient_suggestions.scheduler")


# ═══════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════


@dataclass
class AmbientSchedulerConfig:
    """Tunables for the ambient-suggestions background scheduler."""

    interval_sec: int = 21600  # 6 hours; mirrors the flag default
    initial_delay_sec: int = 300  # 5 minutes after boot
    max_agents_per_tick: int = 10
    enabled: bool = True


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════


_ACTIVE_LOOKBACK_DAYS = 7
_MIN_TURNS_FOR_ACTIVE = 3
# Cheap upper bound on lines we'll inspect per scores file. The most
# recent N lines are the only ones a 7-day window can possibly include
# at any reasonable turn rate. Mirrors the trim behaviour in
# turn_scoring._MAX_LINES_KEEP without needing to read the whole file.
_TAIL_LINES_TO_SCAN = 200


def _project_root() -> Path:
    """Resolve the project root the same way the rest of memory/ does.

    Honors ``ECHO_HOME`` (so test fixtures pointing at a tmp dir
    isolate from the real repo's ``agents/`` tree) before falling back
    to the shared project-root discovery contract.
    """
    import os

    from runtime.platform.process.paths import project_root

    home = os.environ.get("ECHO_HOME")
    if home:
        return Path(home).expanduser().resolve()
    return project_root()


def _agents_dir(project_root: Path) -> Path:
    return project_root / "agents"


def _parse_ts(raw: str) -> datetime | None:
    """Parse a TurnScore.ts string defensively. Returns None on
    malformed input."""
    if not raw:
        return None
    try:
        # ``record_turn_score`` writes ``isoformat(timespec='seconds')``
        # which produces a naive local timestamp like
        # ``2026-05-08T14:35:00`` (no tz). Treat naive values as local
        # time and compare in UTC.
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(UTC)


def _count_recent_turns(scores_path: Path, *, lookback_days: int) -> int:
    """Count turns in the last ``lookback_days`` by scanning the
    tail of ``.scores.jsonl``. Returns 0 on any read failure."""
    try:
        with scores_path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:  # noqa: BLE001
        return 0
    if not lines:
        return 0
    tail = lines[-_TAIL_LINES_TO_SCAN:]
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    count = 0
    for raw in tail:
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):  # noqa: BLE001
            # Malformed lines must NOT crash the scheduler.
            continue
        ts_str = d.get("ts") if isinstance(d, dict) else None
        ts = _parse_ts(str(ts_str) if ts_str is not None else "")
        if ts is None:
            continue
        if ts >= cutoff:
            count += 1
    return count


def _discover_active_agents(project_root: Path) -> list[str]:
    """Return agent_ids whose ``.scores.jsonl`` shows at least
    ``_MIN_TURNS_FOR_ACTIVE`` turns within the last
    ``_ACTIVE_LOOKBACK_DAYS`` days. Stable sorted by agent_id for
    deterministic ordering across ticks/tests.
    """
    agents = _agents_dir(project_root)
    if not agents.is_dir():
        return []
    out: list[str] = []
    try:
        candidates = sorted(p for p in agents.iterdir() if p.is_dir())
    except OSError:
        return []
    for agent_path in candidates:
        agent_id = agent_path.name
        if agent_id.startswith("_"):
            # Convention: ``_shared`` is not a real agent.
            continue
        scores_path = agent_path / "agent-core" / ".scores.jsonl"
        if not scores_path.is_file():
            continue
        if (
            _count_recent_turns(
                scores_path,
                lookback_days=_ACTIVE_LOOKBACK_DAYS,
            )
            >= _MIN_TURNS_FOR_ACTIVE
        ):
            out.append(agent_id)
    return out


# ═══════════════════════════════════════════════════════════
# Scheduler
# ═══════════════════════════════════════════════════════════


class AmbientScheduler:
    """Singleton scheduler. Use ``get_ambient_scheduler()`` to
    obtain the shared instance."""

    _instance: AmbientScheduler | None = None
    _instance_lock = threading.Lock()

    @classmethod
    def get(cls) -> AmbientScheduler:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Stop the worker thread (if running) and drop the singleton.

        For test isolation only — production keeps a single
        AmbientScheduler alive for the process lifetime.
        """
        with cls._instance_lock:
            inst = cls._instance
            cls._instance = None
        if inst is not None:
            try:  # noqa: SIM105
                inst.stop(timeout=2.0)
            except Exception:  # noqa: BLE001 — reset must never raise
                pass

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._config = AmbientSchedulerConfig()
        self._lock = threading.RLock()
        self._tick_count = 0
        self._last_summary: dict[str, Any] = {}
        self._last_error: str = ""

    # ─── lifecycle ───────────────────────────────────────────

    def start(self, config: AmbientSchedulerConfig | None = None) -> None:
        """Spin up the daemon thread. No-op if already running or
        ``config.enabled`` is False."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                _LOG.info(
                    "ambient suggestions scheduler already running · skip start",
                )
                return
            if config is not None:
                self._config = config
            if not self._config.enabled:
                self._last_error = "disabled by config"
                _LOG.info(
                    "ambient suggestions scheduler disabled by config · skip",
                )
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="ambient-suggestions-scheduler",
                daemon=True,
            )
            self._thread.start()
            self._last_error = ""
            _LOG.info(
                "💡 ambient suggestions scheduler started · "
                "interval=%ds initial_delay=%ds max_agents_per_tick=%d",
                self._config.interval_sec,
                self._config.initial_delay_sec,
                self._config.max_agents_per_tick,
            )

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the loop to exit and join the thread. Safe to call
        repeatedly."""
        with self._lock:
            self._stop_event.set()
            t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
        with self._lock:
            if t is not None and not t.is_alive():
                self._thread = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "tick_count": self._tick_count,
                "last_summary": dict(self._last_summary),
                "last_error": self._last_error,
                "interval_sec": self._config.interval_sec,
            }

    # ─── work ────────────────────────────────────────────────

    def tick_once(self) -> dict[str, Any]:
        """Run a single synchronous tick. Designed for unit tests
        and manual invocation. Returns a result dict::

            {
                "agents_processed": int,
                "errors":           list[dict],
                "suggestions_added": int,
            }

        ``agents_processed`` counts agents the scheduler ATTEMPTED
        (not just successes), so callers verifying the loop iterates
        over every active agent can rely on it.
        """
        result: dict[str, Any] = {
            "agents_processed": 0,
            "errors": [],
            "suggestions_added": 0,
        }

        # Feature-flag gate. Imported lazily so tests can monkeypatch
        # the flag values via env vars before this module reads them.
        try:
            from runtime.platform import feature_flags as _ff
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(
                {"agent_id": None, "error": f"feature_flags import: {exc}"},
            )
            return result

        if not _ff.is_on("ui.ambient_suggestions"):
            return result

        with self._lock:
            self._tick_count += 1
            tick_no = self._tick_count
            cap = max(0, int(self._config.max_agents_per_tick))

        project = _project_root()
        try:
            agent_ids = _discover_active_agents(project)
        except OSError as exc:
            _LOG.warning("ambient: agent discovery failed: %s", exc)
            agent_ids = []
            result["errors"].append(
                {"agent_id": None, "error": f"discover: {exc}"},
            )

        agent_ids = agent_ids[:cap]

        # Lazy-import the generator so feature-flag gate truly avoids
        # the LLM cost path when the flag is OFF.
        try:
            from runtime.memory import ambient_suggestions as _amb
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(
                {"agent_id": None, "error": f"ambient import: {exc}"},
            )
            return result

        for agent_id in agent_ids:
            result["agents_processed"] += 1
            try:
                payload = _amb.generate_suggestions(project, agent_id)
            except Exception as exc:
                _LOG.warning(
                    "ambient: generate_suggestions failed for %s: %s",
                    agent_id,
                    exc,
                )
                result["errors"].append(
                    {"agent_id": agent_id, "error": str(exc)},
                )
                continue
            if isinstance(payload, dict):
                added = payload.get("added")
                if isinstance(added, int):
                    result["suggestions_added"] += added
                err = payload.get("error")
                if err:
                    result["errors"].append(
                        {"agent_id": agent_id, "error": str(err)},
                    )

        with self._lock:
            self._last_summary = {
                "tick": tick_no,
                "ts": time.time(),
                "agents_processed": result["agents_processed"],
                "suggestions_added": result["suggestions_added"],
                "error_count": len(result["errors"]),
            }
        _LOG.info(
            "💡 ambient tick #%d · agents=%d added=%d errors=%d",
            tick_no,
            result["agents_processed"],
            result["suggestions_added"],
            len(result["errors"]),
        )
        return result

    def _run_loop(self) -> None:
        # Honor initial-delay first (cancellable wait).
        if self._stop_event.wait(timeout=self._config.initial_delay_sec):
            return
        while not self._stop_event.is_set():
            try:
                # Read the live interval each tick so flag changes
                # (via reload) take effect without restart.
                interval = self._current_interval()
                self.tick_once()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("ambient suggestions tick failed: %s", exc)
                with self._lock:
                    self._last_error = f"tick_failed: {type(exc).__name__}: {exc}"
                interval = self._current_interval()
            if self._stop_event.wait(timeout=interval):
                return

    def _current_interval(self) -> int:
        """Resolve the live interval from feature flags, falling back
        to the configured value. Always >= 60 seconds to keep a
        runaway misconfiguration from busy-looping."""
        try:
            from runtime.platform import feature_flags as _ff

            raw = _ff.value(
                "ui.ambient_suggestions_interval_sec",
                self._config.interval_sec,
            )
            return max(60, int(raw))
        except (ImportError, TypeError, ValueError):
            return max(60, int(self._config.interval_sec))


# ═══════════════════════════════════════════════════════════
# Public accessor
# ═══════════════════════════════════════════════════════════


def get_ambient_scheduler() -> AmbientScheduler:
    """Return the shared singleton scheduler."""
    return AmbientScheduler.get()


__all__ = [
    "AmbientScheduler",
    "AmbientSchedulerConfig",
    "get_ambient_scheduler",
]
