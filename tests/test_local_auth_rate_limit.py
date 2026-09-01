"""Brute-force resistance contracts for the built-in local login."""

from __future__ import annotations

import concurrent.futures
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.adapters.integrations.local_auth import router as local_auth_router
from runtime.adapters.integrations.local_auth.config import LocalAuthConfig

_TEST_BCRYPT_HASH = "bcrypt:$2b$04$mB5pmRT25Iva1xCEXLZNTuwD4q56bnOQkCMBglAl2p5XPHWNTgxci"


class _Clock:
    def __init__(self) -> None:
        self._now = 1_000.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += seconds


def _client(
    clock: _Clock,
    *,
    users: dict[str, str] | None = None,
    max_failures: int = 3,
    ip_max_failures: int = 20,
    window_seconds: float = 10.0,
    lockout_seconds: float = 20.0,
    max_entries: int = 100,
    allow_any_username: bool = False,
) -> tuple[TestClient, object]:
    config = LocalAuthConfig(
        enabled=True,
        users=users or {},
        allow_any_username=allow_any_username,
        login_max_failures=max_failures,
        login_ip_max_failures=ip_max_failures,
        login_failure_window_seconds=window_seconds,
        login_lockout_seconds=lockout_seconds,
        login_rate_limit_max_entries=max_entries,
    )
    router = local_auth_router.create_local_auth_router(config=config, clock=clock)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), router


def _login(
    client: TestClient,
    username: str,
    password: str | None,
    *,
    headers: dict[str, str] | None = None,
):
    body = {"username": username}
    if password is not None:
        body["password"] = password
    return client.post("/api/auth/local/login", json=body, headers=headers or {})


def test_failure_window_lockout_and_retry_after(monkeypatch) -> None:
    monkeypatch.setattr(local_auth_router, "_DUMMY_BCRYPT_HASH", _TEST_BCRYPT_HASH)
    clock = _Clock()
    client, _router = _client(
        clock,
        users={"admin": _TEST_BCRYPT_HASH},
        max_failures=2,
        window_seconds=10,
        lockout_seconds=20,
    )

    assert _login(client, "admin", "wrong-1").status_code == 401
    clock.advance(10)
    assert _login(client, "admin", "wrong-2").status_code == 401
    locked = _login(client, "admin", "wrong-3")
    assert locked.status_code == 429
    assert locked.headers["Retry-After"] == "20"

    clock.advance(20)
    assert _login(client, "admin", "wrong-after-lock").status_code == 401


def test_success_clears_corresponding_failure_state() -> None:
    clock = _Clock()
    client, _router = _client(
        clock,
        users={"admin": _TEST_BCRYPT_HASH},
        max_failures=2,
    )

    assert _login(client, "admin", "wrong").status_code == 401
    assert _login(client, "admin", "correct-password").status_code == 200
    assert _login(client, "admin", "wrong-again").status_code == 401


def test_success_does_not_clear_ip_wide_spray_failures(monkeypatch) -> None:
    monkeypatch.setattr(local_auth_router, "_DUMMY_BCRYPT_HASH", _TEST_BCRYPT_HASH)
    clock = _Clock()
    client, _router = _client(
        clock,
        users={"admin": _TEST_BCRYPT_HASH},
        max_failures=5,
        ip_max_failures=3,
    )

    assert _login(client, "unknown-a", "wrong").status_code == 401
    assert _login(client, "admin", "correct-password").status_code == 200
    assert _login(client, "unknown-b", "wrong").status_code == 401
    assert _login(client, "unknown-c", "wrong").status_code == 429


def test_direct_ip_and_normalized_username_ignore_forwarded_headers(monkeypatch) -> None:
    monkeypatch.setattr(local_auth_router, "_DUMMY_BCRYPT_HASH", _TEST_BCRYPT_HASH)
    clock = _Clock()
    client, _router = _client(
        clock,
        users={"admin": _TEST_BCRYPT_HASH},
        max_failures=2,
    )

    first = _login(
        client,
        "ADMIN",
        "wrong",
        headers={"X-Forwarded-For": "198.51.100.1"},
    )
    second = _login(
        client,
        "admin",
        "wrong",
        headers={"X-Forwarded-For": "203.0.113.99", "X-Real-IP": "203.0.113.99"},
    )

    assert first.status_code == 401
    assert second.status_code == 429


def test_unknown_user_runs_fixed_dummy_bcrypt(monkeypatch) -> None:
    monkeypatch.setattr(local_auth_router, "_DUMMY_BCRYPT_HASH", _TEST_BCRYPT_HASH)
    calls: list[tuple[str, str]] = []
    real_verify = local_auth_router.verify_password

    def recording_verify(plaintext: str, hashed: str) -> bool:
        calls.append((plaintext, hashed))
        return real_verify(plaintext, hashed)

    monkeypatch.setattr(local_auth_router, "verify_password", recording_verify)
    clock = _Clock()
    client, _router = _client(clock, users={"admin": _TEST_BCRYPT_HASH})

    response = _login(client, "unknown", "guess")

    assert response.status_code == 401
    assert len(calls) == 1
    assert calls[0][1] == _TEST_BCRYPT_HASH
    assert calls[0][0] != "guess"


def test_username_spray_hits_ip_limit_before_more_dummy_bcrypt(monkeypatch) -> None:
    monkeypatch.setattr(local_auth_router, "_DUMMY_BCRYPT_HASH", _TEST_BCRYPT_HASH)
    call_count = 0
    real_verify = local_auth_router.verify_password

    def recording_verify(plaintext: str, hashed: str) -> bool:
        nonlocal call_count
        call_count += 1
        return real_verify(plaintext, hashed)

    monkeypatch.setattr(local_auth_router, "verify_password", recording_verify)
    clock = _Clock()
    client, router = _client(
        clock,
        users={"admin": _TEST_BCRYPT_HASH},
        max_failures=5,
        ip_max_failures=20,
    )

    statuses = [_login(client, f"unknown-{index}", "guess").status_code for index in range(21)]

    assert statuses[:19] == [401] * 19
    assert statuses[19:] == [429, 429]
    assert call_count == 20
    assert router.login_ip_failure_limiter.entry_count == 1

    # Once the aggregate IP bucket is locked, even a fresh username is
    # rejected before another cost-bearing dummy bcrypt invocation.
    locked = _login(client, "brand-new-user", "guess")
    assert locked.status_code == 429
    assert locked.headers["Retry-After"] == "20"
    assert call_count == 20


def test_concurrent_failures_produce_one_consistent_lockout() -> None:
    clock = _Clock()
    client, router = _client(
        clock,
        users={"admin": _TEST_BCRYPT_HASH},
        max_failures=4,
    )

    def fail_once(index: int) -> int:
        return _login(client, "admin", f"wrong-{index}").status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        statuses = list(executor.map(fail_once, range(24)))

    assert 429 in statuses
    assert _login(client, "admin", "still-wrong").status_code == 429
    assert router.login_failure_limiter.entry_count == 1


def test_failure_state_is_bounded_and_evicts_lru_entry() -> None:
    clock = _Clock()
    users = {name: _TEST_BCRYPT_HASH for name in ("user-a", "user-b", "user-c")}
    client, router = _client(
        clock,
        users=users,
        max_failures=2,
        max_entries=2,
    )

    for username in users:
        assert _login(client, username, "wrong").status_code == 401
    assert router.login_failure_limiter.entry_count == 2

    # user-a was the least-recently-used unlocked entry, so it starts a new
    # window instead of inheriting an evicted failure.
    assert _login(client, "user-a", "wrong-again").status_code == 401
    assert _login(client, "user-a", "wrong-third").status_code == 429
    assert router.login_failure_limiter.entry_count == 2


def test_passwordless_dev_mode_is_not_rate_limited() -> None:
    clock = _Clock()
    client, router = _client(
        clock,
        allow_any_username=True,
        max_failures=1,
        max_entries=1,
    )

    for _ in range(10):
        assert _login(client, "developer", None).status_code == 200
    assert router.login_failure_limiter.entry_count == 0
    assert router.login_ip_failure_limiter.entry_count == 0

