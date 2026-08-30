from __future__ import annotations

import time

from .coordinator import Lease, _default_holder_id

try:
    import redis  # type: ignore[import-untyped]

    REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    REDIS_AVAILABLE = False
    redis = None  # type: ignore[assignment]


_RENEW_LUA = """
local v = redis.call('GET', KEYS[1])
if v == ARGV[1] then
  redis.call('PEXPIRE', KEYS[1], ARGV[2])
  return 1
else
  return 0
end
"""

_RELEASE_LUA = """
local v = redis.call('GET', KEYS[1])
if v == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
else
  return 0
end
"""


class RedisCoordinator:
    def __init__(
        self,
        client,
        *,
        key_prefix: str = "echo:lease:",
        holder_id: str | None = None,
        counter_key: str | None = None,
    ) -> None:
        if not REDIS_AVAILABLE and not _quacks_like_redis(client):
            raise ImportError(
                "redis not installed AND custom client does not look redis-like · "
                "pip install redis",
            )
        self.client = client
        self.key_prefix = key_prefix
        self.holder_id = holder_id or _default_holder_id()
        self.counter_key = counter_key or f"{key_prefix}__counter"
        self._renew_script = None
        self._release_script = None

    def _key(self, scope: str) -> str:
        return f"{self.key_prefix}{scope}"

    def _encode_value(self, holder_id: str, token: int) -> str:
        return f"{holder_id}|{token}"

    def _parse_value(self, raw: str | bytes | None) -> tuple[str, int] | None:
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if "|" not in raw:
            return None
        parts = raw.rsplit("|", 1)
        try:
            return (parts[0], int(parts[1]))
        except ValueError:
            return None

    def _next_token(self) -> int:
        return int(self.client.incr(self.counter_key))

    def acquire_lease(self, scope: str, *, ttl: float) -> Lease | None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        key = self._key(scope)
        ttl_ms = max(1, int(ttl * 1000))

        existing = self.client.get(key)
        parsed = self._parse_value(existing)
        if parsed is not None and parsed[0] == self.holder_id:
            ok = self.client.pexpire(key, ttl_ms)
            if ok:
                now = time.time()
                return Lease(
                    scope=scope,
                    holder_id=self.holder_id,
                    acquired_at=now,
                    expires_at=now + ttl,
                    fencing_token=parsed[1],
                )

        token = self._next_token()
        value = self._encode_value(self.holder_id, token)
        ok = self.client.set(key, value, nx=True, px=ttl_ms)
        if not ok:
            return None
        now = time.time()
        return Lease(
            scope=scope,
            holder_id=self.holder_id,
            acquired_at=now,
            expires_at=now + ttl,
            fencing_token=token,
        )

    def renew_lease(self, lease: Lease, *, ttl: float) -> Lease | None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        if lease.holder_id != self.holder_id:
            return None  # Implementation note.
        key = self._key(lease.scope)
        ttl_ms = max(1, int(ttl * 1000))
        value = self._encode_value(self.holder_id, lease.fencing_token)

        if self._renew_script is None:
            self._renew_script = self.client.register_script(_RENEW_LUA)
        result = self._renew_script(keys=[key], args=[value, ttl_ms])
        if not result:
            return None
        now = time.time()
        return Lease(
            scope=lease.scope,
            holder_id=self.holder_id,
            acquired_at=lease.acquired_at,
            expires_at=now + ttl,
            fencing_token=lease.fencing_token,
        )

    def release_lease(self, lease: Lease) -> bool:
        if lease.holder_id != self.holder_id:
            return False
        key = self._key(lease.scope)
        value = self._encode_value(self.holder_id, lease.fencing_token)

        if self._release_script is None:
            self._release_script = self.client.register_script(_RELEASE_LUA)
        result = self._release_script(keys=[key], args=[value])
        return bool(result)

    def current_lease(self, scope: str) -> Lease | None:
        key = self._key(scope)
        raw = self.client.get(key)
        parsed = self._parse_value(raw)
        if parsed is None:
            return None
        holder, token = parsed
        pttl_ms = self.client.pttl(key)
        if pttl_ms is None or pttl_ms < 0:
            return None
        now = time.time()
        return Lease(
            scope=scope,
            holder_id=holder,
            acquired_at=now - 0.0,  # Implementation note.
            expires_at=now + pttl_ms / 1000.0,
            fencing_token=token,
        )


def _quacks_like_redis(client) -> bool:
    needed = ("get", "set", "incr", "pexpire", "pttl", "register_script")
    return all(hasattr(client, m) for m in needed)
