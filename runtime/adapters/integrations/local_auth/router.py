from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import Response
    from pydantic import BaseModel, Field

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    BaseModel = object  # type: ignore[assignment, misc]
    Response = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

from .config import LocalAuthConfig, verify_password

logger = logging.getLogger(__name__)

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@\-]{1,64}$")
_DUMMY_BCRYPT_HASH = "bcrypt:$2b$12$i97XS4XVBLbe1Ipw01D4C.KRRk3TznyetM5gIwNVmEc5gb8LS2Nzi"


@dataclass
class _LoginFailureState:
    failures: deque[float] = field(default_factory=deque)
    locked_until: float = 0.0


class _LoginFailureLimiter:
    """Thread-safe, bounded failure windows keyed by direct client + username."""

    def __init__(
        self,
        *,
        max_failures: int,
        window_seconds: float,
        lockout_seconds: float,
        max_entries: int,
        clock: Callable[[], float],
    ) -> None:
        self._max_failures = max(1, int(max_failures))
        self._window_seconds = max(float(window_seconds), 0.001)
        self._lockout_seconds = max(float(lockout_seconds), 0.001)
        self._max_entries = max(1, int(max_entries))
        self._clock = clock
        self._entries: OrderedDict[tuple[str, str], _LoginFailureState] = OrderedDict()
        self._lock = threading.Lock()

    def _prune(self, state: _LoginFailureState, now: float) -> None:
        cutoff = now - self._window_seconds
        while state.failures and state.failures[0] <= cutoff:
            state.failures.popleft()
        if state.locked_until and state.locked_until <= now:
            # A completed lockout starts a fresh failure window instead of
            # immediately re-locking on the next typo.
            state.failures.clear()
            state.locked_until = 0.0

    @staticmethod
    def _retry_after(state: _LoginFailureState, now: float) -> int:
        if state.locked_until <= now:
            return 0
        return max(1, math.ceil(state.locked_until - now))

    def retry_after(self, key: tuple[str, str]) -> int:
        now = self._clock()
        with self._lock:
            state = self._entries.get(key)
            if state is None:
                return 0
            self._prune(state, now)
            retry = self._retry_after(state, now)
            if retry or state.failures:
                self._entries.move_to_end(key)
            else:
                self._entries.pop(key, None)
            return retry

    def record_failure(self, key: tuple[str, str]) -> int:
        now = self._clock()
        with self._lock:
            state = self._entries.get(key)
            if state is None:
                state = _LoginFailureState()
                self._entries[key] = state
            else:
                self._prune(state, now)
            retry = self._retry_after(state, now)
            if not retry:
                state.failures.append(now)
                if len(state.failures) >= self._max_failures:
                    state.locked_until = now + self._lockout_seconds
                retry = self._retry_after(state, now)
            self._entries.move_to_end(key)
            self._evict(now)
            return retry

    def clear(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def _evict(self, now: float) -> None:
        while len(self._entries) > self._max_entries:
            # Prefer evicting an unlocked LRU entry so active lockouts remain
            # effective under a username-spraying memory pressure attack.
            victim = next(
                (key for key, state in self._entries.items() if state.locked_until <= now),
                next(iter(self._entries)),
            )
            self._entries.pop(victim, None)

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)


if FASTAPI_AVAILABLE:

    class LoginRequest(BaseModel):
        username: str = Field(..., min_length=1, max_length=64)
        password: str | None = Field(default=None, max_length=256)
        display_name: str | None = Field(default=None, max_length=128)

    class LoginResponse(BaseModel):
        success: bool
        actor_id: str
        access_token: str | None = None
        token_type: str = "Bearer"
        expires_in: int | None = None
        user: dict[str, Any] = Field(default_factory=dict)

    class WhoamiResponse(BaseModel):
        actor_id: str
        roles: list[str] = Field(default_factory=list)
        metadata: dict[str, Any] = Field(default_factory=dict)


def create_local_auth_router(
    *,
    config: LocalAuthConfig,
    identity_store: Any = None,
    clock: Callable[[], float] | None = None,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(prefix="/api/auth/local", tags=["auth", "local"])
    limiter_clock = clock or time.monotonic
    failure_limiter = _LoginFailureLimiter(
        max_failures=getattr(config, "login_max_failures", 5),
        window_seconds=getattr(config, "login_failure_window_seconds", 300.0),
        lockout_seconds=getattr(config, "login_lockout_seconds", 60.0),
        max_entries=getattr(config, "login_rate_limit_max_entries", 10_000),
        clock=limiter_clock,
    )
    ip_failure_limiter = _LoginFailureLimiter(
        max_failures=getattr(config, "login_ip_max_failures", 20),
        window_seconds=getattr(config, "login_failure_window_seconds", 300.0),
        lockout_seconds=getattr(config, "login_lockout_seconds", 60.0),
        max_entries=getattr(config, "login_rate_limit_max_entries", 10_000),
        clock=limiter_clock,
    )

    def _require_enabled() -> None:
        if not config.enabled:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Local auth disabled · set config.local_auth.enabled=true "
                    "（注意：无密码 · 不对外开放）"
                ),
            )

    def _check_username(username: str) -> None:
        if not _USERNAME_RE.match(username):
            raise HTTPException(
                status_code=400,
                detail="username 只能含字母/数字/._@- · 长度 1-64",
            )

    def _normalized_username(username: str) -> str:
        return username.strip().casefold()

    def _direct_client_ip(request: Request) -> str:
        # Deliberately ignore X-Forwarded-For/X-Real-IP. Trusting those here
        # lets an attacker rotate a client-controlled header to bypass limits.
        client = getattr(request, "client", None)
        host = str(getattr(client, "host", "") or "").strip()
        return host or "unknown"

    def _failure_key(request: Request, username: str) -> tuple[str, str]:
        return _direct_client_ip(request), _normalized_username(username)

    def _ip_failure_key(pair_key: tuple[str, str]) -> tuple[str, str]:
        return pair_key[0], "*"

    def _dummy_password_check(password: str | None) -> None:
        # Hashing first keeps the bcrypt input within its portable 72-byte
        # bound while preserving a fixed-cost check for arbitrary input sizes.
        candidate = hashlib.sha256((password or "").encode("utf-8")).hexdigest()
        verify_password(candidate, _DUMMY_BCRYPT_HASH)

    def _password_matches(username: str, password: str | None) -> bool:
        expected_hash = config.users.get(username)
        if expected_hash is None:
            _dummy_password_check(password)
            return False

        candidate = password or ""
        try:
            matches = verify_password(candidate, expected_hash)
        except (TypeError, ValueError):
            # A malformed configured hash is never an authentication bypass,
            # and it must not create a fast username-enumeration oracle.
            _dummy_password_check(password)
            return False
        if not expected_hash.startswith("bcrypt:"):
            # Legacy SHA-256 remains readable during migration, but pad it with
            # one bcrypt check so known legacy users are not the fast path.
            _dummy_password_check(password)
        return password is not None and matches

    def _raise_rate_limited(retry_after: int) -> None:
        raise HTTPException(
            status_code=429,
            detail="登录失败过多 · 请稍后重试",
            headers={"Retry-After": str(retry_after)},
        )

    def _check_credentials(username: str, password: str | None, request: Request) -> None:
        _check_username(username)

        if config.password_required:
            key = _failure_key(request, username)
            ip_key = _ip_failure_key(key)
            retry_after = max(
                failure_limiter.retry_after(key),
                ip_failure_limiter.retry_after(ip_key),
            )
            if retry_after:
                _raise_rate_limited(retry_after)
            if not _password_matches(username, password):
                retry_after = max(
                    failure_limiter.record_failure(key),
                    ip_failure_limiter.record_failure(ip_key),
                )
                if retry_after:
                    _raise_rate_limited(retry_after)
                raise HTTPException(status_code=401, detail="用户名或密码错误")
            # A valid credential clears only this account/IP pair. The IP-wide
            # spray counter deliberately survives, otherwise rotating through
            # one known-good account would reset failures for every username.
            failure_limiter.clear(key)
            return

        if not config.allow_any_username:
            if username not in config.allowed_usernames:
                raise HTTPException(status_code=403, detail="用户名不在白名单")
            return

    @router.post("/login", response_model=LoginResponse)
    def login(
        body: LoginRequest,
        request: Request,
        response: Response,
    ) -> LoginResponse:
        _require_enabled()
        _check_credentials(body.username, body.password, request)

        actor_id = f"{config.actor_prefix}{body.username}"
        created = False

        if identity_store is not None:
            from runtime.safety.auth.identity import Identity

            existing = identity_store.get(actor_id) if hasattr(identity_store, "get") else None
            if existing is None:
                meta: dict[str, Any] = {
                    "provider": "local",
                    "username": body.username,
                    "created_at": time.time(),
                }
                if body.display_name:
                    meta["display_name"] = body.display_name
                try:
                    identity_store.add(
                        Identity(
                            actor_id=actor_id,
                            roles=tuple(config.default_roles),
                            metadata=meta,
                        ),
                    )
                    created = True
                except ValueError:  # noqa: BLE001 — duplicate identity silently skipped
                    pass

        access_token: str | None = None
        expires_in: int | None = None
        if config.jwt_secret:
            from runtime.safety.auth.identity import encode_jwt_hs256

            now = int(time.time())
            claims: dict[str, Any] = {
                "sub": actor_id,
                "iat": now,
                "exp": now + config.jwt_expire_seconds,
                "provider": "local",
                "username": body.username,
            }
            if config.jwt_issuer:
                claims["iss"] = config.jwt_issuer
            if config.jwt_audience:
                claims["aud"] = config.jwt_audience
            access_token = encode_jwt_hs256(claims, secret=config.jwt_secret)
            expires_in = config.jwt_expire_seconds

        if access_token and expires_in:
            from runtime.safety.auth.principal import set_session_cookie

            set_session_cookie(
                response,
                request,
                access_token,
                max_age=expires_in,
            )

        logger.info(
            "local_auth login · actor=%s created=%s",
            actor_id,
            created,
        )
        return LoginResponse(
            success=True,
            actor_id=actor_id,
            access_token=access_token,
            expires_in=expires_in,
            user={
                "actor_id": actor_id,
                "username": body.username,
                "display_name": body.display_name,
                "provider": "local",
                "created": created,
            },
        )

    @router.post("/logout", status_code=204, response_class=Response, response_model=None)
    def logout(request: Request, response: Response):
        from runtime.safety.auth.principal import clear_session_cookie

        clear_session_cookie(response, request)

    @router.get("/whoami", response_model=WhoamiResponse)
    def whoami(request: Request) -> WhoamiResponse:
        _require_enabled()
        if identity_store is None:
            raise HTTPException(
                status_code=501,
                detail="no identity_store configured · cannot verify token",
            )
        from runtime.adapters.web_auth import _resolve_actor

        actor = _resolve_actor(
            request,
            identity_store,
            require_auth=True,
            jwt_secret=config.jwt_secret,
            jwt_issuer=config.jwt_issuer,
            jwt_audience=config.jwt_audience,
        )
        if not actor:
            raise HTTPException(401, "not authenticated")
        identity = identity_store.get(actor)
        if identity is None:
            raise HTTPException(401, "unknown actor")
        return WhoamiResponse(
            actor_id=identity.actor_id,
            roles=list(identity.roles),
            metadata=dict(identity.metadata),
        )

    router.login_failure_limiter = failure_limiter  # type: ignore[attr-defined]
    router.login_ip_failure_limiter = ip_failure_limiter  # type: ignore[attr-defined]
    return router
