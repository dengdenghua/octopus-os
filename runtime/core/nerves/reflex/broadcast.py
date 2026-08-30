from __future__ import annotations

import json
import logging
import time
from typing import Any

from runtime.platform.process.service_provider import get_provider

_LOG = logging.getLogger("echo.reflex.broadcast")
_PAHO_WARNED = False  # only log "paho missing" once per process


class ReflexBroadcaster:
    """Holds the list of broadcast destinations + fires events on
    demand. Each destination is a dict with at least a ``kind`` key
    (``mqtt`` or ``webhook``) plus the connection details for that
    transport.

    Multiple destinations are fired SEQUENTIALLY in declaration
    order · slow destinations stack their latency. Move the
    high-latency ones to the end of the list (or, for production,
    swap publish_hit for an async/queued variant). Each destination
    is wrapped in its own try/except so one failure can't poison
    the others.
    """

    def __init__(self, destinations: list[dict[str, Any]] | None) -> None:
        # Filter out invalid entries up-front so the hot path doesn't
        # need to revalidate per call.
        self._destinations: list[dict[str, Any]] = []
        for d in destinations or []:
            if isinstance(d, dict) and d.get("kind"):
                self._destinations.append(d)
        self._enabled = bool(self._destinations)

    @classmethod
    def from_yaml_top_level(cls, top: Any) -> ReflexBroadcaster:
        """Build from the parsed yaml file's top-level dict.

        Accepts both the legacy single-mqtt shorthand and the new
        ``destinations:`` list. When both are present, ``destinations``
        wins · the shorthand is silently appended to give it a clear
        upgrade path:
          1. start with ``broadcast.mqtt``
          2. add ``broadcast.destinations`` for fanout
          3. drop the shorthand once the list covers everything
        """
        if not isinstance(top, dict):
            return cls(None)
        b = top.get("broadcast")
        if not isinstance(b, dict):
            return cls(None)
        dests: list[dict[str, Any]] = []
        # Legacy single-mqtt shorthand.
        legacy_mqtt = b.get("mqtt")
        if isinstance(legacy_mqtt, dict) and legacy_mqtt.get("broker"):
            d = dict(legacy_mqtt)
            d.setdefault("kind", "mqtt")
            dests.append(d)
        # New multi-destination form.
        raw_dests = b.get("destinations")
        if isinstance(raw_dests, list):
            for d in raw_dests:
                if isinstance(d, dict) and d.get("kind"):
                    dests.append(dict(d))
        return cls(dests)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def describe(self) -> dict[str, Any]:
        """Sanitised view for the admin UI · drops credentials so the
        endpoint is safe to expose."""
        if not self._enabled:
            return {"enabled": False, "destinations": []}
        out: list[dict[str, Any]] = []
        for d in self._destinations:
            sanitized = {
                k: v for k, v in d.items() if k not in ("password", "client_secret", "token")
            }
            out.append(sanitized)
        return {"enabled": True, "destinations": out}

    def publish_hit(
        self,
        *,
        rule_id: str,
        kind: str,
        latency_ms: float,
        intent_goal: str,
        actor: str | None = None,
    ) -> None:
        """Fan out the hit to every destination · never raises.

        Each destination's failure is logged but doesn't stop the
        others · matches the per-rule action layer's contract.
        Payload shape is identical for all destinations so subscribers
        on different transports see the same JSON event.
        """
        if not self._enabled:
            return
        payload_obj = {
            "rule_id": rule_id,
            "kind": kind,
            "latency_ms": float(latency_ms or 0),
            "intent_goal": intent_goal,
            "ts": time.time(),
            "actor": actor,
        }
        for dest in self._destinations:
            d_kind = str(dest.get("kind") or "").lower()
            try:
                if d_kind == "mqtt":
                    self._publish_mqtt(dest, rule_id, payload_obj)
                elif d_kind == "webhook":
                    self._publish_webhook(dest, payload_obj)
                else:
                    _LOG.warning(
                        "reflex broadcast: unknown destination kind %r · skipped",
                        d_kind,
                    )
            except (KeyError, TypeError, ValueError) as exc:
                # Defensive · individual transports already swallow
                # their own errors, but a malformed config could raise
                # before that. Don't let a broadcaster crash the
                # reflex hot path.
                _LOG.warning(
                    "reflex broadcast destination %s failed: %s",
                    d_kind,
                    exc,
                )

    def _publish_mqtt(
        self,
        cfg: dict[str, Any],
        rule_id: str,
        payload_obj: dict[str, Any],
    ) -> None:
        try:
            import paho.mqtt.publish as mqtt_publish  # type: ignore[import]
        except ImportError:
            global _PAHO_WARNED
            if not _PAHO_WARNED:
                _LOG.warning(
                    "reflex broadcast (mqtt) configured but paho-mqtt "
                    "not installed · run `pip install paho-mqtt`",
                )
                _PAHO_WARNED = True
            return
        broker = str(cfg.get("broker") or "").strip()
        if not broker:
            return
        host = broker
        port = int(cfg.get("port") or 1883)
        if ":" in broker:
            host, _, port_str = broker.rpartition(":")
            try:
                port = int(port_str)
            except ValueError:
                host = broker
        prefix = str(cfg.get("topic_prefix") or "echo/reflex").strip("/")
        topic = f"{prefix}/{rule_id}"
        qos = int(cfg.get("qos") or 0)
        if qos not in (0, 1, 2):
            qos = 0
        retain = bool(cfg.get("retain"))
        auth = None
        if cfg.get("username"):
            auth = {
                "username": cfg["username"],
                "password": cfg.get("password", ""),
            }
        try:
            mqtt_publish.single(
                topic=topic,
                payload=json.dumps(payload_obj, ensure_ascii=False),
                qos=qos,
                retain=retain,
                hostname=host,
                port=port,
                auth=auth,
                keepalive=2,
                client_id="echo-reflex-broadcast",
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "reflex mqtt publish failed (%s:%s topic=%s): %s",
                host,
                port,
                topic,
                exc,
            )

    def _publish_webhook(
        self,
        cfg: dict[str, Any],
        payload_obj: dict[str, Any],
    ) -> None:
        """Fire a one-shot HTTP request · stdlib urllib so no extra
        deps. Reuses the same JSON envelope as the MQTT path · only
        the transport differs."""
        import urllib.error as _ue
        import urllib.request as _u

        url = str(cfg.get("url") or "").strip()
        if not url:
            return
        # SSRF guard (audit C4): block private/internal webhook targets
        # unless the config explicitly opts in via ``allow_private: true``.
        from runtime.safety.auth.url_guard import check_url

        verdict = check_url(url, allow_private=bool(cfg.get("allow_private", False)))
        if not verdict.allow:
            _LOG.warning(
                "reflex webhook %s rejected by url_guard: %s",
                url,
                verdict.reason,
            )
            return
        method = str(cfg.get("method") or "POST").upper()
        timeout_s = float(cfg.get("timeout_ms") or 1000) / 1000.0
        body = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")
        req = _u.Request(
            url,
            data=body if method != "GET" else None,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with _u.urlopen(req, timeout=timeout_s) as resp:  # nosec B310 — audited HTTP webhook endpoint
                status = getattr(resp, "status", 0)
                if not (200 <= status < 300):
                    _LOG.warning(
                        "reflex webhook publish %s → HTTP %s",
                        url,
                        status,
                    )
        except _ue.HTTPError as exc:
            _LOG.warning("reflex webhook %s HTTP error: %s", url, exc)
        except OSError as exc:
            _LOG.warning("reflex webhook %s failed: %s", url, exc)


def get_default_broadcaster() -> ReflexBroadcaster:
    """Return the active broadcaster · lazy-init to disabled."""
    provider = get_provider()
    b = provider.get("broadcast_default")
    if b is None:
        b = ReflexBroadcaster(None)
        provider.register_instance("broadcast_default", b)
    return b


def set_default_broadcaster(b: ReflexBroadcaster) -> None:
    """Swap the active broadcaster · called after a yaml reload."""
    get_provider().register_instance("broadcast_default", b)


__all__ = [
    "ReflexBroadcaster",
    "get_default_broadcaster",
    "set_default_broadcaster",
]
