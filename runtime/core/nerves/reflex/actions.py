from __future__ import annotations

import json
import logging
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

_LOG = logging.getLogger("echo.reflex.action")


@dataclass
class ActionResult:
    """Outcome of executing one action block · journaled for audit."""

    rule_id: str
    kind: str  # "webhook" | "exec"
    success: bool
    elapsed_ms: float
    detail: str = ""  # short status string · e.g. "HTTP 200"
    error: str = ""  # exception class:msg when failed


@dataclass
class ActionSpec:
    """Parsed action config for one rule · None when rule has no action."""

    webhook: dict[str, Any] | None = None
    exec: dict[str, Any] | None = None
    mqtt: dict[str, Any] | None = None

    @classmethod
    def from_entry(cls, entry: Any) -> ActionSpec | None:
        """Build from the YAML entry's ``action`` subdict. Returns None
        when entry has no action block · caller skips execution.
        """
        if not isinstance(entry, dict):
            return None
        wh = entry.get("webhook") if isinstance(entry.get("webhook"), dict) else None
        ex = entry.get("exec") if isinstance(entry.get("exec"), dict) else None
        mq = entry.get("mqtt") if isinstance(entry.get("mqtt"), dict) else None
        if wh is None and ex is None and mq is None:
            return None
        return cls(webhook=wh, exec=ex, mqtt=mq)


def _run_webhook(rule_id: str, cfg: dict[str, Any]) -> ActionResult:
    """Fire one HTTP request · returns ActionResult, never raises.

    The webhook block's ``url`` is the only required field. Body is
    JSON-encoded when set · GET requests skip the body. Status codes
    in the 2xx range count as success; anything else (including DNS
    errors / timeouts / 5xx) is a failed action.
    """
    t0 = time.perf_counter()
    url = str(cfg.get("url") or "").strip()
    if not url:
        return ActionResult(rule_id, "webhook", False, 0.0, error="missing url")
    method = str(cfg.get("method") or "POST").upper()
    timeout_s = float(cfg.get("timeout_ms") or 1000) / 1000.0
    raw_headers = cfg.get("headers")
    headers: dict[Any, Any] = raw_headers if isinstance(raw_headers, dict) else {}
    body = cfg.get("body")

    data: bytes | None = None
    req_headers = {str(k): str(v) for k, v in headers.items()}
    if body is not None and method != "GET":
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    # SSRF guard (audit C4): block private/internal targets unless the rule
    # explicitly opts in via ``allow_private: true``.
    from runtime.safety.auth.url_guard import check_url

    verdict = check_url(url, allow_private=bool(cfg.get("allow_private", False)))
    if not verdict.allow:
        return ActionResult(
            rule_id, "webhook", False, 0.0, error=f"url_guard rejected: {verdict.reason}"
        )

    try:
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers=req_headers,
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # nosec B310 — audited HTTP webhook endpoint
            status = getattr(resp, "status", 0) or 0
            ok = 200 <= status < 300
            elapsed = (time.perf_counter() - t0) * 1000
            return ActionResult(rule_id, "webhook", ok, elapsed, detail=f"HTTP {status}")
    except urllib.error.HTTPError as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        return ActionResult(
            rule_id,
            "webhook",
            False,
            elapsed,
            detail=f"HTTP {exc.code}",
            error=f"HTTPError:{exc.reason}",
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.perf_counter() - t0) * 1000
        return ActionResult(rule_id, "webhook", False, elapsed, error=f"{type(exc).__name__}:{exc}")


def _run_exec(rule_id: str, cfg: dict[str, Any]) -> ActionResult:
    """Run a shell command · returns ActionResult, never raises.

    Default ``shell=False`` for safety · the cmd is split with
    ``shlex.split`` and exec'd directly. Set ``shell: true`` to
    allow pipes / redirection, but the operator owns the security
    implications of that choice (and the YAML file owner is trusted).
    """
    t0 = time.perf_counter()
    cmd = str(cfg.get("cmd") or "").strip()
    if not cmd:
        return ActionResult(rule_id, "exec", False, 0.0, error="missing cmd")
    timeout_s = float(cfg.get("timeout_sec") or 1.0)
    use_shell = bool(cfg.get("shell"))

    try:
        if use_shell:
            args: Any = cmd
        else:
            args = shlex.split(cmd, posix=True)
        proc = subprocess.run(  # nosec B602 — shell is per-action opt-in from the operator's reflex config, not model input
            args,
            shell=use_shell,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            text=True,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        ok = proc.returncode == 0
        # Trim stdout/stderr to keep the journal compact.
        tail = (proc.stdout or proc.stderr or "").strip().replace("\n", " ")
        if len(tail) > 120:
            tail = tail[:120] + "…"
        return ActionResult(rule_id, "exec", ok, elapsed, detail=f"exit={proc.returncode} {tail}")
    except subprocess.TimeoutExpired:
        elapsed = (time.perf_counter() - t0) * 1000
        return ActionResult(rule_id, "exec", False, elapsed, error=f"TimeoutExpired:{timeout_s}s")
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.perf_counter() - t0) * 1000
        return ActionResult(rule_id, "exec", False, elapsed, error=f"{type(exc).__name__}:{exc}")


def _run_mqtt(rule_id: str, cfg: dict[str, Any]) -> ActionResult:
    """Publish one MQTT message · returns ActionResult, never raises.

    Schema::

        mqtt:
          broker: localhost              # required · host or host:port
          port: 1883                      # default 1883 (or 8883 if tls)
          topic: home/light/kitchen       # required
          payload: "on"                   # str → utf-8 bytes
                                          # OR dict/list → JSON-encoded
          qos: 0                          # 0 / 1 / 2 (default 0)
          retain: false                   # default false
          client_id: echo-reflex       # optional, auto-generated
          username: ...                   # optional
          password: ...                   # optional
          tls: false                      # default false
          timeout_ms: 1000                # connect + publish timeout

    paho-mqtt is an optional dependency · install with
    ``pip install paho-mqtt`` if you want the MQTT path. Without it
    this function returns a clear error result instead of crashing
    the reflex layer (graceful degradation matches webhook/exec
    behaviour).
    """
    t0 = time.perf_counter()
    try:
        import paho.mqtt.client as mqtt_client  # type: ignore[import]
        import paho.mqtt.publish as mqtt_publish  # type: ignore[import]
    except ImportError:
        elapsed = (time.perf_counter() - t0) * 1000
        return ActionResult(
            rule_id,
            "mqtt",
            False,
            elapsed,
            error="paho-mqtt not installed (pip install paho-mqtt)",
        )

    broker = str(cfg.get("broker") or "").strip()
    topic = str(cfg.get("topic") or "").strip()
    if not broker:
        return ActionResult(rule_id, "mqtt", False, 0.0, error="missing broker")
    if not topic:
        return ActionResult(rule_id, "mqtt", False, 0.0, error="missing topic")

    # Allow ``broker: "host:port"`` shorthand · split into host + port.
    host = broker
    port = int(cfg.get("port") or 1883)
    if ":" in broker:
        host, _, port_str = broker.rpartition(":")
        try:
            port = int(port_str)
        except ValueError:
            host = broker  # treat as IPv6 / weird host; keep default port

    # Payload coercion · str passes through, structured types JSON.
    payload = cfg.get("payload")
    if payload is None:
        payload_bytes: bytes | str = b""
    elif isinstance(payload, (str, bytes)):
        payload_bytes = payload
    else:
        try:
            payload_bytes = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            return ActionResult(
                rule_id,
                "mqtt",
                False,
                0.0,
                error=f"payload not JSON-serializable: {exc}",
            )

    qos = int(cfg.get("qos") or 0)
    if qos not in (0, 1, 2):
        qos = 0
    retain = bool(cfg.get("retain"))
    auth = None
    if cfg.get("username"):
        auth = {"username": cfg["username"], "password": cfg.get("password", "")}
    tls = {"tls_version": mqtt_client.ssl.PROTOCOL_TLS_CLIENT} if cfg.get("tls") else None
    client_id = str(cfg.get("client_id") or f"echo-reflex-{rule_id}")

    try:
        # ``single`` opens a connection, publishes, disconnects · cheapest
        # path for fire-and-forget reflex actions (no connection pooling
        # but home-IoT volumes don't justify a long-lived client yet).
        mqtt_publish.single(
            topic=topic,
            payload=payload_bytes,
            qos=qos,
            retain=retain,
            hostname=host,
            port=port,
            client_id=client_id,
            auth=auth,
            tls=tls,
            keepalive=int((cfg.get("timeout_ms") or 1000) / 1000) or 1,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        return ActionResult(
            rule_id,
            "mqtt",
            True,
            elapsed,
            detail=f"{host}:{port} topic={topic} qos={qos}",
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.perf_counter() - t0) * 1000
        return ActionResult(
            rule_id,
            "mqtt",
            False,
            elapsed,
            error=f"{type(exc).__name__}:{exc}",
        )


def execute_action(spec: ActionSpec | None, rule_id: str) -> list[ActionResult]:
    """Run the rule's actions in order · returns the list of results.

    Execution order: webhook → mqtt → exec. Webhook first since the
    typical pattern is "tell the cloud / Node-RED, which broadcasts
    via MQTT, but ALSO directly publish MQTT for low-latency local
    paths". Exec last because shell calls are the heaviest. Each
    runs independently · failure in one doesn't skip the others.
    """
    if spec is None:
        return []
    out: list[ActionResult] = []
    if spec.webhook:
        try:
            out.append(_run_webhook(rule_id, spec.webhook))
        except Exception as exc:  # noqa: BLE001
            # Defensive · _run_webhook already swallows internally
            # but we never want this layer to bubble exceptions up
            # into the reflex hot-path.
            _LOG.warning("reflex action webhook unexpected: %s", exc)
    if spec.mqtt:
        try:
            out.append(_run_mqtt(rule_id, spec.mqtt))
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("reflex action mqtt unexpected: %s", exc)
    if spec.exec:
        try:
            out.append(_run_exec(rule_id, spec.exec))
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("reflex action exec unexpected: %s", exc)
    return out


__all__ = ["ActionSpec", "ActionResult", "execute_action"]
