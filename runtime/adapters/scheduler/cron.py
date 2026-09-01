from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


class CronParseError(ValueError):
    pass


_FIELD_RANGES = (
    (0, 59),  # Implementation note.
    (0, 23),  # Implementation note.
    (1, 31),  # Implementation note.
    (1, 12),  # Implementation note.
    (0, 6),  # Implementation note.
)
_FIELD_NAMES = ("minute", "hour", "day", "month", "weekday")


@dataclass(frozen=True)
class CronExpression:
    expr: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]

    @classmethod
    def parse(cls, expr: str) -> CronExpression:
        parts = expr.strip().split()
        if len(parts) != 5:
            raise CronParseError(f"cron expression must have 5 fields (got {len(parts)}): {expr!r}")
        fields = [_parse_field(parts[i], _FIELD_RANGES[i], _FIELD_NAMES[i]) for i in range(5)]
        return cls(
            expr=expr.strip(),
            minutes=frozenset(fields[0]),
            hours=frozenset(fields[1]),
            days=frozenset(fields[2]),
            months=frozenset(fields[3]),
            weekdays=frozenset(fields[4]),
        )

    def matches(self, dt: datetime) -> bool:
        return (
            dt.minute in self.minutes
            and dt.hour in self.hours
            and dt.day in self.days
            and dt.month in self.months
            and dt.weekday() % 7 in self._weekday_py_set()
        )

    def _weekday_py_set(self) -> set[int]:
        # cron 0=Sun 1=Mon 2=Tue ... 6=Sat
        # py   0=Mon 1=Tue ... 5=Sat 6=Sun
        mapping = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
        return {mapping[w] for w in self.weekdays}

    def next_after(self, now: datetime) -> datetime:
        candidate = (now + timedelta(minutes=1)).replace(
            second=0,
            microsecond=0,
        )
        max_iters = 4 * 366 * 24 * 60  # Implementation note.
        for _ in range(max_iters):
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise CronParseError(f"no match found within 4 years of {now!r} for expr={self.expr!r}")


# ═══════════════════════════════════════════════════════════
# field parsing
# ═══════════════════════════════════════════════════════════


def _parse_field(
    token: str,
    field_range: tuple[int, int],
    field_name: str,
) -> set[int]:
    lo, hi = field_range

    if "," in token:
        result: set[int] = set()
        for sub in token.split(","):
            result.update(_parse_field(sub, field_range, field_name))
        return result

    if "/" in token:
        base_token, _, step_str = token.partition("/")
        try:
            step = int(step_str)
        except ValueError:
            raise CronParseError(f"{field_name}: bad step {step_str!r} in {token!r}") from None
        if step <= 0:
            raise CronParseError(f"{field_name}: step must be > 0 in {token!r}")
        base_set = _parse_field(base_token, field_range, field_name)
        base_sorted = sorted(base_set)
        if base_token == "*":
            return {v for v in range(lo, hi + 1) if (v - lo) % step == 0}
        if "-" in base_token:
            start = min(base_sorted)
            end = max(base_sorted)
            return {v for v in range(start, end + 1) if (v - start) % step == 0}
        start = min(base_sorted)
        return {v for v in range(start, hi + 1, step)}

    if token == "*":
        return set(range(lo, hi + 1))

    if "-" in token:
        a_str, _, b_str = token.partition("-")
        try:
            a, b = int(a_str), int(b_str)
        except ValueError:
            raise CronParseError(f"{field_name}: bad range {token!r}") from None
        if a > b:
            raise CronParseError(f"{field_name}: inverted range {token!r}")
        _check_range(a, field_range, field_name)
        _check_range(b, field_range, field_name)
        return set(range(a, b + 1))

    try:
        v = int(token)
    except ValueError:
        raise CronParseError(
            f"{field_name}: unsupported token {token!r} "
            "(named months/weekdays not supported · use numeric)"
        ) from None
    _check_range(v, field_range, field_name)
    return {v}


def _check_range(v: int, field_range: tuple[int, int], field_name: str) -> None:
    lo, hi = field_range
    if v < lo or v > hi:
        raise CronParseError(f"{field_name}: value {v} out of range [{lo}, {hi}]")
