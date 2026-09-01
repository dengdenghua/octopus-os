from __future__ import annotations

import datetime as _dt
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

_LOG = logging.getLogger("echo.reflex.gating")

_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass
class GatingSpec:
    """Parsed gating config · None when the rule has no enabled_when."""

    actors: set[str] | None = None  # whitelist
    deny_actors: set[str] = field(default_factory=set)
    window_start: _dt.time | None = None  # inclusive
    window_end: _dt.time | None = None  # exclusive
    days_of_week: set[int] | None = None  # 0=Mon, 6=Sun
    rollout_pct: int | None = None  # 0..100

    @classmethod
    def from_entry(cls, raw: Any) -> GatingSpec | None:
        """Build from the YAML rule's ``enabled_when`` subdict.

        Returns None when the block is missing OR fully empty (so the
        common case of "no gates" stays a None check, not a populated
        dataclass with all defaults). Malformed sub-fields are
        skipped with a warning · partial gates better than no gates.
        """
        if not isinstance(raw, dict) or not raw:
            return None
        spec = cls()
        # actors / deny_actors
        a = raw.get("actors")
        if isinstance(a, list) and a:
            spec.actors = {str(x).strip() for x in a if str(x).strip()}
        d = raw.get("deny_actors")
        if isinstance(d, list):
            spec.deny_actors = {str(x).strip() for x in d if str(x).strip()}
        # time_window
        tw = raw.get("time_window")
        if isinstance(tw, dict):
            spec.window_start = _parse_clock(tw.get("start"))
            spec.window_end = _parse_clock(tw.get("end"))
            if spec.window_start is None or spec.window_end is None:
                _LOG.warning(
                    "gating: time_window needs both start + end (HH:MM) · ignored",
                )
                spec.window_start = spec.window_end = None
        # days_of_week
        dow = raw.get("days_of_week")
        if isinstance(dow, list) and dow:
            spec.days_of_week = set()
            for d in dow:
                ds = str(d).strip().lower()[:3]
                if ds in _DAY_NAMES:
                    spec.days_of_week.add(_DAY_NAMES.index(ds))
                else:
                    _LOG.warning("gating: unknown day-of-week %r · skipped", d)
            if not spec.days_of_week:
                spec.days_of_week = None
        # rollout_pct
        rp = raw.get("rollout_pct")
        if rp is not None:
            try:
                pct = int(rp)
                spec.rollout_pct = max(0, min(100, pct))
            except (TypeError, ValueError):
                _LOG.warning("gating: rollout_pct %r isn't an int · ignored", rp)
        if (
            spec.actors is None
            and not spec.deny_actors
            and spec.window_start is None
            and spec.days_of_week is None
            and spec.rollout_pct is None
        ):
            return None
        return spec

    def is_active(self, *, actor: str | None, now: _dt.datetime) -> bool:
        """All gates AND together · returns False as soon as any
        configured gate excludes the request. Missing gates pass."""
        # Deny list wins outright.
        if actor and actor in self.deny_actors:
            return False
        # Allow list (when set) requires a positive match.
        if self.actors is not None and (not actor or actor not in self.actors):
            return False
        # Time window · supports overnight (start > end → wrap midnight).
        if self.window_start is not None and self.window_end is not None:
            t = now.time()
            ws, we = self.window_start, self.window_end
            if ws <= we:
                if not (ws <= t < we):
                    return False
            else:
                # Overnight window: active if t >= start OR t < end.
                if not (t >= ws or t < we):
                    return False
        # Day of week.
        if self.days_of_week is not None and now.weekday() not in self.days_of_week:
            return False
        # Rollout percentage · deterministic per (actor, rule).
        # When actor is None, fall back to per-process random (so we
        # still split traffic, just not stickily). The rule_id isn't
        # available here · the caller injects it via .with_rollout
        # or we use the spec's id-less hash (see _bucket).
        if self.rollout_pct is not None and self.rollout_pct < 100:
            # We don't have rule_id at this scope · use the actor's
            # bucket alone; callers wanting per-rule stickiness should
            # tee their own bucket. For the common "10% rollout per
            # actor" case this is fine: same actor always in or out.
            bucket = _bucket(actor or "anon")
            if bucket >= self.rollout_pct:
                return False
        return True

    def describe(self) -> dict[str, Any]:
        """Sanitised view for the admin UI."""
        out: dict[str, Any] = {}
        if self.actors:
            out["actors"] = sorted(self.actors)
        if self.deny_actors:
            out["deny_actors"] = sorted(self.deny_actors)
        if self.window_start is not None and self.window_end is not None:
            out["time_window"] = {
                "start": self.window_start.strftime("%H:%M"),
                "end": self.window_end.strftime("%H:%M"),
            }
        if self.days_of_week is not None:
            out["days_of_week"] = [_DAY_NAMES[i] for i in sorted(self.days_of_week)]
        if self.rollout_pct is not None:
            out["rollout_pct"] = self.rollout_pct
        return out


def _parse_clock(raw: Any) -> _dt.time | None:
    """Parse "HH:MM" or "H:MM" · returns None on bad input."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    try:
        hh, mm = s.split(":", 1)
        return _dt.time(hour=int(hh), minute=int(mm))
    except (ValueError, AttributeError):
        return None


def _bucket(key: str) -> int:
    """Return ``hash(key) % 100`` using a stable hash · same key always
    in same bucket. Plain Python ``hash()`` is process-randomized;
    use sha1 for cross-process stability."""
    h = hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).digest()
    return h[0] % 100


__all__ = ["GatingSpec"]
