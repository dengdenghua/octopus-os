from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

from .base import Channel, InboundMessage, OutboundMessage, _sanitize_url

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_SERVER_URL = "https://ntfy.sh"


class NtfyError(RuntimeError):
    pass


class NtfyChannel(Channel):
    channel_id: str = "ntfy"

    def __init__(
        self,
        *,
        server_url: str = DEFAULT_SERVER_URL,
        topic: str,
        channel_id: str = "ntfy",
        http_client: Any = None,
    ) -> None:
        if not topic:
            raise ValueError("topic required")
        self._server_url = server_url.rstrip("/")
        self._topic = topic
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

        url = f"{self._server_url}/{self._topic}"
        headers = {
            "Title": "Echo Agent",
            "Priority": "default",
        }
        if self._http is not None:
            for attempt in range(4):
                try:
                    resp = self._http.post(url, content=content, headers=headers)
                except Exception as e:  # noqa: BLE001
                    raise NtfyError(
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
                    resp = self._bare_client.post(url, content=content, headers=headers)
                except Exception as e:  # noqa: BLE001
                    raise NtfyError(
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
            raise NtfyError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"bad json body: {e}") from e
        if not isinstance(payload, dict):
            raise ValueError("payload not an object")

        text = payload.get("message") or payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return None

        topic = payload.get("topic", self._topic)
        title = payload.get("title", "")
        time_raw = payload.get("time")

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"ntfy:{topic}",
            sender_id="",
            content=text,
            metadata={
                "platform": "ntfy",
                "topic": topic,
                "title": title if isinstance(title, str) else "",
            },
            received_at=_parse_ntfy_ts(time_raw),
        )


def _parse_ntfy_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(raw))
    except (ValueError, OverflowError, OSError):
        return None
