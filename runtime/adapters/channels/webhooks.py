from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from .base import Channel, InboundMessage, OutboundMessage, _sanitize_url

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class WebhooksError(RuntimeError):
    pass


class WebhooksSignatureError(ValueError):
    pass


class WebhooksChannel(Channel):
    channel_id: str = "webhooks"

    def __init__(
        self,
        *,
        webhook_secret: str = "",
        outbound_url: str = "",
        channel_id: str = "webhooks",
        http_client: Any = None,
    ) -> None:
        self._webhook_secret = webhook_secret
        self._outbound_url = outbound_url
        self.channel_id = channel_id
        self._http = http_client
        self.send_log: list[OutboundMessage] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        if hasattr(self, "_bare_client") and self._bare_client is not None:
            self._bare_client.close()
            self._bare_client = None

    def send(self, msg: OutboundMessage) -> None:
        self.send_log.append(msg)

        # Constitution gate · LINT-11 requires this before any network call.
        verdict = self.safe_send(msg)
        if verdict.action == "block":
            logger.warning(
                "channel.send.blocked",
                extra={"channel": self.channel_id, "reason": verdict.reason},
            )
            return
        # Use sanitized text if the gate rewrote PII · otherwise original.
        content = verdict.sanitized if verdict.action == "rewrite" else msg.content

        if not self._outbound_url:
            return
        now_iso = datetime.now(UTC).isoformat()
        body: dict[str, Any] = {
            "text": content,
            "source": "echo-agent",
            "timestamp": now_iso,
        }
        raw_body = json.dumps(body, separators=(",", ":")).encode("utf-8")
        sig = self._sign(raw_body)
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": sig,
        }
        if self._http is not None:
            for attempt in range(4):
                try:
                    resp = self._http.post(self._outbound_url, content=raw_body, headers=headers)
                except Exception as e:  # noqa: BLE001
                    raise WebhooksError(
                        f"network: {type(e).__name__}: {e}",
                    ) from e
                if resp.status_code < 500 and resp.status_code != 429:
                    logger.info(
                        "channel.send",
                        extra={"channel": self.channel_id, "status": resp.status_code},
                    )
                    break
                if attempt == 3:
                    logger.error(
                        "channel.send.failed",
                        extra={"channel": self.channel_id, "status": resp.status_code},
                    )
                    break
                logger.warning(
                    "channel.send.retry",
                    extra={
                        "channel": self.channel_id,
                        "status": resp.status_code,
                        "attempt": attempt,
                    },
                )
                time.sleep(2**attempt)
        else:
            if not hasattr(self, "_bare_client") or self._bare_client is None:
                self._bare_client = httpx.Client(timeout=15.0)
            for attempt in range(4):
                try:
                    resp = self._bare_client.post(
                        self._outbound_url, content=raw_body, headers=headers
                    )
                except Exception as e:  # noqa: BLE001
                    raise WebhooksError(
                        f"network: {type(e).__name__}: {e}",
                    ) from e
                if resp.status_code < 500 and resp.status_code != 429:
                    logger.info(
                        "channel.send",
                        extra={"channel": self.channel_id, "status": resp.status_code},
                    )
                    break
                if attempt == 3:
                    logger.error(
                        "channel.send.failed",
                        extra={"channel": self.channel_id, "status": resp.status_code},
                    )
                    break
                logger.warning(
                    "channel.send.retry",
                    extra={
                        "channel": self.channel_id,
                        "status": resp.status_code,
                        "attempt": attempt,
                    },
                )
                time.sleep(2**attempt)
        status = getattr(resp, "status_code", 200)
        if status >= 400:
            raise WebhooksError(
                f"POST {_sanitize_url(self._outbound_url)} failed: HTTP {status}",
            )

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        if self._webhook_secret:
            lowered = {k.lower(): v for k, v in headers.items()}
            recv = lowered.get("x-webhook-signature", "")
            expected = self._sign(body)
            if not hmac.compare_digest(recv, expected):
                raise WebhooksSignatureError(
                    "X-Webhook-Signature mismatch",
                )

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"bad json body: {e}") from e
        if not isinstance(payload, dict):
            raise ValueError("payload not an object")

        text = None
        for key in ("text", "content", "message"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                text = val
                break
        if text is None:
            return None

        sender = payload.get("sender") or payload.get("from") or {}
        if isinstance(sender, str):
            sender = {"id": sender}
        if not isinstance(sender, dict):
            sender = {}

        source_id = payload.get("id")
        if source_id is None:
            source_id = ""

        sender_id = str(sender.get("id", "")) if isinstance(sender, dict) else ""

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"webhook:{source_id}",
            sender_id=sender_id,
            content=text,
            metadata={
                "platform": "webhooks",
                "source_id": source_id,
                "sender_name": sender.get("name", "") if isinstance(sender, dict) else "",
            },
            received_at=_parse_webhook_ts(payload.get("timestamp")),
        )

    def _sign(self, data: bytes) -> str:
        if not self._webhook_secret:
            return ""
        return hmac.new(
            self._webhook_secret.encode("utf-8"),
            data,
            hashlib.sha256,
        ).hexdigest()


def _parse_webhook_ts(raw: Any) -> datetime | None:
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw))
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except (ValueError, TypeError):  # noqa: BLE001 — try multiple formats before giving up
            pass
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                return datetime.strptime(raw, fmt)
            except (ValueError, TypeError):  # noqa: BLE001 — try multiple formats before giving up
                pass
        return None
    return None
