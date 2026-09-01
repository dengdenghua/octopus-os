"""Redis-backed, cross-host tool-effect receipts.

The existing :class:`RedisCoordinator` owns lease acquisition and renewal.
This adapter adds durable receipt payloads and uses small Lua compare-and-set
scripts for the two boundaries that must be atomic:

* mark ``started`` only while the caller still owns its fenced lease;
* publish the final result and release the lease in one Redis operation.

That prevents a paused/stale pod from overwriting a result after another pod
has acquired a newer fencing token.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from runtime.core.hearts.coordinator import Lease
from runtime.core.hearts.redis_coordinator import RedisCoordinator
from runtime.execution.tool_engine.effect_store import EffectReceipt, StoreDecision
from runtime.platform.models import Step

_FENCED_SET_RETAIN_LUA = """
-- echo_effect_fenced_set_retain_v1
local current = redis.call('GET', KEYS[1])
if current ~= ARGV[1] then
  return 0
end
redis.call('SET', KEYS[2], ARGV[2])
redis.call('PEXPIRE', KEYS[1], ARGV[3])
return 1
"""

_FENCED_SET_RELEASE_LUA = """
-- echo_effect_fenced_set_release_v1
local current = redis.call('GET', KEYS[1])
if current ~= ARGV[1] then
  return 0
end
redis.call('SET', KEYS[2], ARGV[2])
redis.call('DEL', KEYS[1])
return 1
"""

_FENCED_DELETE_RELEASE_LUA = """
-- echo_effect_fenced_delete_release_v1
local current = redis.call('GET', KEYS[1])
if current ~= ARGV[1] then
  return 0
end
redis.call('DEL', KEYS[2])
redis.call('DEL', KEYS[1])
return 1
"""

_REPAIR_COMMITTED_LUA = """
-- echo_effect_repair_committed_v1
if redis.call('EXISTS', KEYS[1]) == 1 then
  return 0
end
redis.call('SET', KEYS[2], ARGV[1])
return 1
"""

_AUTHORIZE_RETRY_LUA = """
-- echo_effect_authorize_retry_v1
if redis.call('EXISTS', KEYS[1]) == 1 then
  return 0
end
local raw = redis.call('GET', KEYS[2])
if not raw then
  return 0
end
local ok, receipt = pcall(cjson.decode, raw)
if not ok or receipt['state'] ~= 'indeterminate' then
  return 0
end
if tonumber(receipt['fencing_token'] or 0) ~= tonumber(ARGV[1]) then
  return 0
end
receipt['state'] = 'retry_authorized'
receipt['holder_id'] = ARGV[2]
receipt['reason'] = ARGV[3]
receipt['updated_at'] = tonumber(ARGV[4])
redis.call('SET', KEYS[2], cjson.encode(receipt))
return 1
"""


class RedisEffectStore:
    backend_name = "redis"
    shared_across_hosts = True

    def __init__(
        self,
        client: Any,
        *,
        key_prefix: str = "echo:tool-effect:",
    ) -> None:
        if not key_prefix:
            raise ValueError("key_prefix required")
        self.client = client
        self.key_prefix = key_prefix
        self._coordinator = RedisCoordinator(
            client,
            key_prefix=f"{key_prefix}lease:",
            counter_key=f"{key_prefix}fencing-counter",
        )
        self._holder_id: str | None = None
        self._active: dict[str, Lease] = {}
        self._lock = threading.RLock()
        self._set_retain = client.register_script(_FENCED_SET_RETAIN_LUA)
        self._set_release = client.register_script(_FENCED_SET_RELEASE_LUA)
        self._delete_release = client.register_script(_FENCED_DELETE_RELEASE_LUA)
        self._repair_committed = client.register_script(_REPAIR_COMMITTED_LUA)
        self._authorize_retry = client.register_script(_AUTHORIZE_RETRY_LUA)

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        key_prefix: str = "echo:tool-effect:",
        connect_timeout_s: float = 2.0,
    ) -> RedisEffectStore:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ImportError(
                "Redis tool-effect backend requires `pip install redis` or the `hearts-redis` extra"
            ) from exc
        client = redis.Redis.from_url(
            url,
            socket_connect_timeout=max(0.1, float(connect_timeout_s)),
            socket_timeout=max(0.1, float(connect_timeout_s)),
            health_check_interval=15,
        )
        client.ping()
        return cls(client, key_prefix=key_prefix)

    def _receipt_key(self, effect_key: str) -> str:
        return f"{self.key_prefix}receipt:{effect_key}"

    def _bind_holder(self, holder_id: str) -> None:
        if self._holder_id is None:
            self._holder_id = holder_id
            self._coordinator.holder_id = holder_id
        elif self._holder_id != holder_id:
            raise ValueError("one RedisEffectStore instance cannot serve multiple holders")

    def _lease_key(self, effect_key: str) -> str:
        return self._coordinator._key(effect_key)

    @staticmethod
    def _expected(holder_id: str, fencing_token: int) -> str:
        return f"{holder_id}|{fencing_token}"

    def _read_receipt(self, effect_key: str) -> dict[str, Any] | None:
        raw = self.client.get(self._receipt_key(effect_key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {"state": "indeterminate", "reason": "invalid Redis receipt payload"}
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _payload(
        *,
        effect_key: str,
        task_id: str,
        step_id: int,
        sucker_id: str,
        args_fingerprint: str,
        side_effecting: bool,
        state: str,
        holder_id: str,
        fencing_token: int,
        call_id: str = "",
        step_json: str | None = None,
        reason: str = "",
    ) -> str:
        return json.dumps(
            {
                "effect_key": effect_key,
                "task_id": task_id,
                "step_id": step_id,
                "sucker_id": sucker_id,
                "args_fingerprint": args_fingerprint,
                "side_effecting": side_effecting,
                "state": state,
                "holder_id": holder_id,
                "fencing_token": fencing_token,
                "call_id": call_id,
                "step_json": step_json,
                "reason": reason,
                "updated_at": time.time(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def claim(
        self,
        *,
        effect_key: str,
        task_id: str,
        step_id: int,
        sucker_id: str,
        args_fingerprint: str,
        side_effecting: bool,
        holder_id: str,
        lease_ttl_s: float,
        observed_durable_intent: bool,
    ) -> StoreDecision:
        with self._lock:
            self._bind_holder(holder_id)
            lease = self._coordinator.acquire_lease(effect_key, ttl=lease_ttl_s)
            if lease is None:
                receipt = self._read_receipt(effect_key)
                replay = _decision_from_terminal_receipt(receipt)
                if replay is not None:
                    return replay
                current = self._coordinator.current_lease(effect_key)
                return StoreDecision(
                    "busy",
                    fencing_token=(current.fencing_token if current else 0),
                    lease_expires_at=(current.expires_at if current else 0.0),
                    reason="another host owns the live tool-effect lease",
                )

            self._active[effect_key] = lease
            receipt = self._read_receipt(effect_key)
            terminal = _decision_from_terminal_receipt(receipt)
            if terminal is not None:
                self._coordinator.release_lease(lease)
                self._active.pop(effect_key, None)
                return terminal

            prior_state = str((receipt or {}).get("state") or "")
            prior_side_effecting = bool((receipt or {}).get("side_effecting"))
            retry_authorized = prior_state == "retry_authorized"
            unsafe = (
                not retry_authorized
                and (prior_state == "started" or observed_durable_intent)
                and (side_effecting or prior_side_effecting)
            )
            if unsafe:
                reason = _dangling_intent_reason()
                payload = self._payload(
                    effect_key=effect_key,
                    task_id=task_id,
                    step_id=step_id,
                    sucker_id=sucker_id,
                    args_fingerprint=args_fingerprint,
                    side_effecting=True,
                    state="indeterminate",
                    holder_id=holder_id,
                    fencing_token=lease.fencing_token,
                    reason=reason,
                )
                self._fenced_set_release(effect_key, lease, payload)
                self._active.pop(effect_key, None)
                return StoreDecision(
                    "indeterminate",
                    fencing_token=lease.fencing_token,
                    reason=reason,
                )

            payload = self._payload(
                effect_key=effect_key,
                task_id=task_id,
                step_id=step_id,
                sucker_id=sucker_id,
                args_fingerprint=args_fingerprint,
                side_effecting=side_effecting,
                state="claimed",
                holder_id=holder_id,
                fencing_token=lease.fencing_token,
            )
            if not self._fenced_set_retain(effect_key, lease, payload, lease_ttl_s):
                self._active.pop(effect_key, None)
                return StoreDecision(
                    "busy",
                    reason="tool-effect lease changed while publishing the claim",
                )
            return StoreDecision(
                "execute",
                fencing_token=lease.fencing_token,
                lease_expires_at=lease.expires_at,
            )

    def mark_started(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
        call_id: str,
        lease_ttl_s: float,
    ) -> bool:
        with self._lock:
            receipt = self._read_receipt(effect_key) or {}
            lease = self._active.get(effect_key)
            if (
                lease is None
                or lease.holder_id != holder_id
                or lease.fencing_token != fencing_token
            ):
                return False
            payload = self._payload(
                effect_key=effect_key,
                task_id=str(receipt.get("task_id") or ""),
                step_id=int(receipt.get("step_id") or 0),
                sucker_id=str(receipt.get("sucker_id") or ""),
                args_fingerprint=str(receipt.get("args_fingerprint") or ""),
                side_effecting=bool(receipt.get("side_effecting")),
                state="started",
                holder_id=holder_id,
                fencing_token=fencing_token,
                call_id=call_id,
            )
            return self._fenced_set_retain(effect_key, lease, payload, lease_ttl_s)

    def renew(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
        lease_ttl_s: float,
    ) -> bool:
        with self._lock:
            lease = self._active.get(effect_key)
            if (
                lease is None
                or lease.holder_id != holder_id
                or lease.fencing_token != fencing_token
            ):
                return False
            renewed = self._coordinator.renew_lease(lease, ttl=lease_ttl_s)
            if renewed is None:
                self._active.pop(effect_key, None)
                return False
            self._active[effect_key] = renewed
            return True

    def commit(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
        step: Step,
    ) -> bool:
        with self._lock:
            lease = self._active.get(effect_key)
            if (
                lease is None
                or lease.holder_id != holder_id
                or lease.fencing_token != fencing_token
            ):
                return False
            receipt = self._read_receipt(effect_key) or {}
            payload = self._payload(
                effect_key=effect_key,
                task_id=str(receipt.get("task_id") or ""),
                step_id=int(receipt.get("step_id") or step.step_id),
                sucker_id=str(receipt.get("sucker_id") or step.action.sucker_id),
                args_fingerprint=str(receipt.get("args_fingerprint") or ""),
                side_effecting=bool(receipt.get("side_effecting")),
                state="committed",
                holder_id=holder_id,
                fencing_token=fencing_token,
                call_id=str(step.action.call_id),
                step_json=step.model_dump_json(),
            )
            ok = self._fenced_set_release(effect_key, lease, payload)
            self._active.pop(effect_key, None)
            return ok

    def record_committed(self, *, effect_key: str, step: Step) -> None:
        with self._lock:
            receipt = self._read_receipt(effect_key) or {}
            payload = self._payload(
                effect_key=effect_key,
                task_id=str(receipt.get("task_id") or ""),
                step_id=int(receipt.get("step_id") or step.step_id),
                sucker_id=str(receipt.get("sucker_id") or step.action.sucker_id),
                args_fingerprint=str(receipt.get("args_fingerprint") or ""),
                side_effecting=bool(receipt.get("side_effecting")),
                state="committed",
                holder_id=str(receipt.get("holder_id") or "journal-repair"),
                fencing_token=int(receipt.get("fencing_token") or 0),
                call_id=str(step.action.call_id),
                step_json=step.model_dump_json(),
            )
            self._repair_committed(
                keys=[self._lease_key(effect_key), self._receipt_key(effect_key)],
                args=[payload],
            )

    def finish_failed(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
        side_effecting: bool,
        reason: str,
    ) -> None:
        with self._lock:
            lease = self._active.get(effect_key)
            if (
                lease is None
                or lease.holder_id != holder_id
                or lease.fencing_token != fencing_token
            ):
                return
            if not side_effecting:
                self._fenced_delete_release(effect_key, lease)
                self._active.pop(effect_key, None)
                return
            receipt = self._read_receipt(effect_key) or {}
            payload = self._payload(
                effect_key=effect_key,
                task_id=str(receipt.get("task_id") or ""),
                step_id=int(receipt.get("step_id") or 0),
                sucker_id=str(receipt.get("sucker_id") or ""),
                args_fingerprint=str(receipt.get("args_fingerprint") or ""),
                side_effecting=True,
                state="indeterminate",
                holder_id=holder_id,
                fencing_token=fencing_token,
                reason=reason,
            )
            self._fenced_set_release(effect_key, lease, payload)
            self._active.pop(effect_key, None)

    def release_unstarted(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
    ) -> None:
        with self._lock:
            lease = self._active.get(effect_key)
            if (
                lease is None
                or lease.holder_id != holder_id
                or lease.fencing_token != fencing_token
            ):
                return
            self._fenced_delete_release(effect_key, lease)
            self._active.pop(effect_key, None)

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def list_receipts(
        self,
        *,
        state: str | None = None,
        limit: int = 100,
    ) -> list[EffectReceipt]:
        safe_limit = max(1, min(int(limit), 500))
        rows: list[EffectReceipt] = []
        pattern = f"{self.key_prefix}receipt:*"
        scanned = 0
        max_scan = max(2_000, safe_limit * 20)
        for key in self.client.scan_iter(match=pattern, count=100):
            scanned += 1
            if scanned > max_scan or len(rows) >= safe_limit * 4:
                break
            if isinstance(key, bytes):
                key = key.decode("utf-8", errors="replace")
            raw = self.client.get(key)
            receipt = self._decode_receipt(raw)
            if receipt is None or (state is not None and receipt.state != state):
                continue
            rows.append(receipt)
        priority = {
            "indeterminate": 0,
            "started": 1,
            "claimed": 2,
            "retry_authorized": 3,
            "committed": 4,
        }
        rows.sort(
            key=lambda item: (
                priority.get(item.state, 5),
                -item.updated_at,
            )
        )
        return rows[:safe_limit]

    def authorize_retry(
        self,
        *,
        effect_key: str,
        expected_fencing_token: int,
        actor: str,
        reason: str,
    ) -> bool:
        with self._lock:
            result = self._authorize_retry(
                keys=[self._lease_key(effect_key), self._receipt_key(effect_key)],
                args=[
                    expected_fencing_token,
                    actor,
                    reason,
                    time.time(),
                ],
            )
            return bool(result)

    @staticmethod
    def _decode_receipt(raw: object) -> EffectReceipt | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            return None
        try:
            receipt = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(receipt, dict):
            return None
        return EffectReceipt(
            effect_key=str(receipt.get("effect_key") or ""),
            task_id=str(receipt.get("task_id") or ""),
            step_id=int(receipt.get("step_id") or 0),
            sucker_id=str(receipt.get("sucker_id") or ""),
            side_effecting=bool(receipt.get("side_effecting")),
            state=str(receipt.get("state") or ""),
            holder_id=str(receipt.get("holder_id") or ""),
            fencing_token=int(receipt.get("fencing_token") or 0),
            lease_expires_at=0.0,
            call_id=str(receipt.get("call_id") or ""),
            reason=str(receipt.get("reason") or ""),
            updated_at=float(receipt.get("updated_at") or 0.0),
            has_result=bool(receipt.get("step_json")),
        )

    def _fenced_set_retain(
        self,
        effect_key: str,
        lease: Lease,
        payload: str,
        lease_ttl_s: float,
    ) -> bool:
        result = self._set_retain(
            keys=[self._lease_key(effect_key), self._receipt_key(effect_key)],
            args=[
                self._expected(lease.holder_id, lease.fencing_token),
                payload,
                max(1, int(lease_ttl_s * 1000)),
            ],
        )
        return bool(result)

    def _fenced_set_release(
        self,
        effect_key: str,
        lease: Lease,
        payload: str,
    ) -> bool:
        result = self._set_release(
            keys=[self._lease_key(effect_key), self._receipt_key(effect_key)],
            args=[self._expected(lease.holder_id, lease.fencing_token), payload],
        )
        return bool(result)

    def _fenced_delete_release(self, effect_key: str, lease: Lease) -> bool:
        result = self._delete_release(
            keys=[self._lease_key(effect_key), self._receipt_key(effect_key)],
            args=[self._expected(lease.holder_id, lease.fencing_token)],
        )
        return bool(result)


def _decision_from_terminal_receipt(
    receipt: dict[str, Any] | None,
) -> StoreDecision | None:
    if not receipt:
        return None
    state = str(receipt.get("state") or "")
    token = int(receipt.get("fencing_token") or 0)
    if state == "committed":
        raw = receipt.get("step_json")
        if isinstance(raw, str):
            try:
                return StoreDecision(
                    "replay",
                    fencing_token=token,
                    step=Step.model_validate_json(raw),
                )
            except (TypeError, ValueError):
                raw = None
        return StoreDecision(
            "indeterminate",
            fencing_token=token,
            reason="committed Redis receipt is missing a valid structured result",
        )
    if state == "indeterminate":
        return StoreDecision(
            "indeterminate",
            fencing_token=token,
            reason=str(receipt.get("reason") or _dangling_intent_reason()),
        )
    return None


def _dangling_intent_reason() -> str:
    return "a previous host entered this side-effecting tool but did not durably record its result"


__all__ = ["RedisEffectStore"]
