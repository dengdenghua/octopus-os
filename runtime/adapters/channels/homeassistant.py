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


class HomeAssistantError(RuntimeError):
    pass


class HomeAssistantChannel(Channel):
    channel_id: str = "homeassistant"

    def __init__(
        self,
        *,
        ha_url: str,
        long_lived_token: str,
        channel_id: str = "homeassistant",
        http_client: Any = None,
    ) -> None:
        if not ha_url:
            raise ValueError("ha_url required")
        if not long_lived_token:
            raise ValueError("long_lived_token required")
        self._ha_url = ha_url.rstrip("/")
        self._long_lived_token = long_lived_token
        self.channel_id = channel_id
        self._http = http_client
        self.send_log: list[OutboundMessage] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        if hasattr(self, "_bare_client") and self._bare_client is not None:
            self._bare_client.close()
            self._bare_client = None

    def health_check(self) -> bool:
        try:
            url = f"{self._ha_url}/api/"
            headers = {"Authorization": f"Bearer {self._long_lived_token}"}
            if self._http is not None:
                client = self._http
            else:
                if not hasattr(self, "_bare_client") or self._bare_client is None:
                    self._bare_client = httpx.Client(timeout=15.0)
                client = self._bare_client
            resp = client.get(url, headers=headers)
            return resp.status_code == 200
        except Exception:
            return False

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

        url = f"{self._ha_url}/api/services/notify/persistent_notification"
        body: dict[str, Any] = {
            "message": content,
            "title": "Echo Agent",
        }

        self._post_json(url, body=body)

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._long_lived_token}",
            "Content-Type": "application/json",
        }
        if self._http is not None:
            client = self._http
        else:
            if not hasattr(self, "_bare_client") or self._bare_client is None:
                self._bare_client = httpx.Client(timeout=15.0)
            client = self._bare_client
        for attempt in range(4):
            try:
                resp = client.post(url, json=body, headers=headers)
            except Exception as e:  # noqa: BLE001
                raise HomeAssistantError(
                    f"network: {type(e).__name__}: {e}",
                ) from e
            if resp.status_code < 500 and resp.status_code != 429:
                logger.info(
                    "channel.send", extra={"channel": self.channel_id, "status": resp.status_code}
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
                extra={"channel": self.channel_id, "status": resp.status_code, "attempt": attempt},
            )
            time.sleep(2**attempt)

        status = getattr(resp, "status_code", 200)
        if status >= 400:
            raise HomeAssistantError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise HomeAssistantError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise HomeAssistantError("response not an object")
        return data

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

        entity_id = payload.get("entity_id", "")
        if not isinstance(entity_id, str):
            entity_id = ""
        user_id = payload.get("user_id", "")
        if not isinstance(user_id, str):
            user_id = str(user_id) if user_id is not None else ""

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"ha:{entity_id}" if entity_id else "ha:unknown",
            sender_id=user_id,
            content=text,
            metadata={
                "platform": "homeassistant",
                "entity_id": entity_id,
                "user_id": user_id,
            },
            received_at=_parse_ha_ts(payload.get("timestamp") or payload.get("created_at")),
        )


def _parse_ha_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, (int, float, str)):
        return None
    try:
        return datetime.fromtimestamp(float(raw))
    except (ValueError, OverflowError, OSError):
        return None
