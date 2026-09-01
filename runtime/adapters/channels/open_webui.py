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


class OpenWebUIError(RuntimeError):
    pass


class OpenWebUIChannel(Channel):
    channel_id: str = "open_webui"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        channel_id: str = "open_webui",
        http_client: Any = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url required")
        if not api_key:
            raise ValueError("api_key required")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
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
            url = f"{self._base_url}/api/models"
            headers = {"Authorization": f"Bearer {self._api_key}"}
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

        url = f"{self._base_url}/api/chat/completions"
        body: dict[str, Any] = {
            "model": "echo-agent",
            "messages": [{"role": "assistant", "content": content}],
            "stream": False,
        }
        self._post_json(url, body=body)

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
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
                raise OpenWebUIError(
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
            raise OpenWebUIError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise OpenWebUIError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise OpenWebUIError("response not an object")
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

        messages = payload.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            if not isinstance(last, dict):
                return None
            text = last.get("text") or last.get("content") or ""
        else:
            message = payload.get("message")
            if isinstance(message, dict):
                text = message.get("text") or message.get("content") or ""
            else:
                text = payload.get("text") or payload.get("content") or ""

        if not isinstance(text, str) or not text.strip():
            return None

        user_info = payload.get("user") or {}
        if isinstance(user_info, dict):
            sender_id = str(user_info.get("id", ""))
        else:
            sender_id = str(user_info) if user_info else ""

        chat_id = payload.get("chat_id") or payload.get("id") or ""
        if not isinstance(chat_id, str):
            chat_id = str(chat_id)

        timestamp = payload.get("timestamp")

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"webui:{chat_id}",
            sender_id=sender_id,
            content=text,
            metadata={
                "platform": "open_webui",
                "chat_id": chat_id,
                "user": user_info,
            },
            received_at=_parse_webui_ts(timestamp),
        )


def _parse_webui_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, (int, float, str)):
        return None
    try:
        ts = float(raw)
        if ts > 1e12:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts)
    except (ValueError, OverflowError, OSError):
        return None
