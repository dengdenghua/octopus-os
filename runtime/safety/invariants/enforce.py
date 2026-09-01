from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T", bound=Callable[..., Any])


def _enabled() -> bool:
    """Resolve the live invariants-enforcement flag.

    Imports lazily so this module stays importable before
    ``runtime.platform.runtime_policy.feature_flags`` (which currently has no
    upstream deps but may grow some).
    """
    try:
        from runtime.platform import feature_flags as _ff

        return _ff.is_on("safety.invariants_enabled")
    except (ImportError, AttributeError, OSError, ValueError, TypeError):
        return os.environ.get("ECHO_INVARIANTS", "on") != "off"


class InvariantViolation(RuntimeError):
    def __init__(self, rule_id: str, message: str) -> None:
        self.rule_id = rule_id
        super().__init__(f"[{rule_id}] {message}")


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def enforces(*rule_ids: str) -> Callable[[T], T]:

    def decorator(fn: T) -> T:
        existing: tuple[str, ...] = getattr(fn, "__enforces__", ())
        fn.__enforces__ = existing + rule_ids  # type: ignore[attr-defined]
        return fn

    return decorator


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def require(
    predicate: Callable[..., bool],
    rule_id: str = "REQUIRE",
    message: str | None = None,
) -> Callable[[T], T]:

    def decorator(fn: T) -> T:
        if not _enabled():
            return fn

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not predicate(*args, **kwargs):
                raise InvariantViolation(
                    rule_id,
                    message or f"precondition failed in {fn.__qualname__}",
                )
            return fn(*args, **kwargs)

        wrapper.__enforces__ = getattr(fn, "__enforces__", ()) + (rule_id,)  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


def ensure(
    predicate: Callable[[Any], bool],
    rule_id: str = "ENSURE",
    message: str | None = None,
) -> Callable[[T], T]:

    def decorator(fn: T) -> T:
        if not _enabled():
            return fn

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = fn(*args, **kwargs)
            if not predicate(result):
                raise InvariantViolation(
                    rule_id,
                    message or f"postcondition failed in {fn.__qualname__}",
                )
            return result

        wrapper.__enforces__ = getattr(fn, "__enforces__", ()) + (rule_id,)  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def monotonic(
    attr: str,
    direction: str = "up",
    rule_id: str = "MONOTONIC",
) -> Callable[[T], T]:
    if direction not in ("up", "down", "nondec", "noninc"):
        raise ValueError(f"invalid direction {direction}")

    def decorator(fn: T) -> T:
        if not _enabled():
            return fn

        @functools.wraps(fn)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            before = getattr(self, attr)
            result = fn(self, *args, **kwargs)
            after = getattr(self, attr)
            ok = {
                "up": after > before,
                "down": after < before,
                "nondec": after >= before,
                "noninc": after <= before,
            }[direction]
            if not ok:
                raise InvariantViolation(
                    rule_id,
                    f"{self.__class__.__name__}.{attr} violated {direction}: "
                    f"before={before}, after={after}",
                )
            return result

        wrapper.__enforces__ = getattr(fn, "__enforces__", ()) + (rule_id,)  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def append_only(
    attr: str,
    rule_id: str = "APPEND_ONLY",
) -> Callable[[T], T]:

    def decorator(fn: T) -> T:
        if not _enabled():
            return fn

        @functools.wraps(fn)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            before_list = list(getattr(self, attr))
            before_len = len(before_list)
            result = fn(self, *args, **kwargs)
            after = getattr(self, attr)
            if len(after) < before_len:
                raise InvariantViolation(
                    rule_id,
                    f"{self.__class__.__name__}.{attr}: length shrank {before_len} → {len(after)}",
                )
            for i, old in enumerate(before_list):
                if after[i] != old:
                    raise InvariantViolation(
                        rule_id,
                        f"{self.__class__.__name__}.{attr}[{i}] mutated",
                    )
            return result

        wrapper.__enforces__ = getattr(fn, "__enforces__", ()) + (rule_id,)  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator
