from __future__ import annotations

import contextlib
import time

from .coordinator import Lease, _default_holder_id

try:
    import etcd3  # type: ignore[import-untyped]

    ETCD3_AVAILABLE = True
# ``etcd3`` 0.12 can be installed yet fail during import when its generated
# protobuf code is incompatible with the process protobuf runtime. Treat that
# exactly like an unavailable optional backend; unrelated OS features must boot.
except Exception:  # pragma: no cover - dependency/runtime compatibility varies
    ETCD3_AVAILABLE = False
    etcd3 = None  # type: ignore[assignment]


class EtcdCoordinator:
    def __init__(
        self,
        client,
        *,
        key_prefix: str = "/echo/lease/",
        holder_id: str | None = None,
    ) -> None:
        if not _quacks_like_etcd3(client):
            if not ETCD3_AVAILABLE:
                raise ImportError(
                    "etcd3 not installed · pip install etcd3 · 或注入 duck-type 兼容客户端",
                )
            raise TypeError(
                f"client does not look like etcd3.Etcd3Client: {type(client).__name__}",
            )
        self.client = client
        self.key_prefix = key_prefix
        self.holder_id = holder_id or _default_holder_id()
        self._active_leases: dict[str, tuple[int, float]] = {}

    def _key(self, scope: str) -> str:
        return f"{self.key_prefix}{scope}"

    def _encode_value(self, token: int) -> str:
        return f"{self.holder_id}|{token}"

    def _parse_value(self, raw) -> tuple[str, int] | None:
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

    def acquire_lease(self, scope: str, *, ttl: float) -> Lease | None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        key = self._key(scope)

        existing_value, existing_meta = self.client.get(key)
        if existing_value is not None:
            parsed = self._parse_value(existing_value)
            if parsed is not None and parsed[0] == self.holder_id:
                active = self._active_leases.get(scope)
                if active is not None:
                    lease_id, acquired_at = active
                    with contextlib.suppress(Exception):
                        self.client.refresh_lease(lease_id)
                    return Lease(
                        scope=scope,
                        holder_id=self.holder_id,
                        acquired_at=acquired_at,
                        expires_at=time.time() + ttl,
                        fencing_token=parsed[1],
                    )
            else:
                return None  # Implementation note.

        etcd_lease = self.client.lease(int(ttl) if ttl >= 1 else 1)
        success, responses = self.client.transaction(
            compare=[self.client.transactions.create(key) == 0],
            success=[
                self.client.transactions.put(
                    key,
                    self._encode_value(0),
                    etcd_lease,  # Implementation note.
                )
            ],
            failure=[],
        )
        if not success:
            with contextlib.suppress(Exception):
                etcd_lease.revoke()
            return None

        value, meta = self.client.get(key)
        token = int(getattr(meta, "mod_revision", 0)) if meta is not None else 0
        self.client.put(key, self._encode_value(token), lease=etcd_lease)

        now = time.time()
        self._active_leases[scope] = (etcd_lease.id, now)
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
            return None
        active = self._active_leases.get(lease.scope)
        if active is None:
            return None  # Implementation note.
        lease_id, acquired_at = active

        try:
            results = self.client.refresh_lease(lease_id)
        except Exception:  # noqa: BLE001
            return None
        _ = results

        now = time.time()
        return Lease(
            scope=lease.scope,
            holder_id=self.holder_id,
            acquired_at=acquired_at,
            expires_at=now + ttl,
            fencing_token=lease.fencing_token,
        )

    def release_lease(self, lease: Lease) -> bool:
        if lease.holder_id != self.holder_id:
            return False
        active = self._active_leases.pop(lease.scope, None)
        if active is None:
            return False
        lease_id, _ = active
        key = self._key(lease.scope)

        expected = self._encode_value(lease.fencing_token)
        try:
            success, _ = self.client.transaction(
                compare=[self.client.transactions.value(key) == expected],
                success=[self.client.transactions.delete(key)],
                failure=[],
            )
        except Exception:  # noqa: BLE001
            success = False

        with contextlib.suppress(Exception):
            self.client.revoke_lease(lease_id)

        return bool(success)

    def current_lease(self, scope: str) -> Lease | None:
        key = self._key(scope)
        try:
            value, meta = self.client.get(key)
        except Exception:  # noqa: BLE001
            return None
        if value is None:
            return None
        parsed = self._parse_value(value)
        if parsed is None:
            return None
        holder, token = parsed
        now = time.time()
        return Lease(
            scope=scope,
            holder_id=holder,
            acquired_at=now,
            # Placeholder, not a bug: etcd's basic KV get() here doesn't return
            # the attached lease's remaining TTL, and the sole caller of
            # current_lease() — Hearts.is_leader() — compares holder_id only and
            # never reads expires_at. acquire_lease/renew_lease set a real
            # expires_at; don't "fix" this to now+ttl without a real TTL lookup.
            expires_at=now,
            fencing_token=token,
        )


def _quacks_like_etcd3(client) -> bool:
    needed = (
        "get",
        "put",
        "lease",
        "transaction",
        "transactions",
        "refresh_lease",
        "revoke_lease",
    )
    return all(hasattr(client, m) for m in needed)
