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

DEFAULT_API_BASE = "http://localhost:5225"


class SimpleXError(RuntimeError):
    pass


class SimpleXChannel(Channel):
    channel_id: str = "simplex"

    def __init__(
        self,
        *,
        api_base_url: str = DEFAULT_API_BASE,
        channel_id: str = "simplex",
        http_client: Any = None,
    ) -> None:
        self.channel_id = channel_id
        self.api_base_url = api_base_url.rstrip("/")
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

        contact_id, _ = self._split_thread_id(msg.thread_id)
        if "contact_id" in msg.metadata:
            contact_id = str(msg.metadata["contact_id"])

        body: dict[str, Any] = {
            "chat": {"type": "direct", "id": contact_id},
            "content": {"type": "msg", "msg": {"type": "text", "text": content}},
        }

        url = f"{self.api_base_url}/v1/chat/item"
        resp_data = self._post_json(url, body=body)
        if not isinstance(resp_data, dict):
            raise SimpleXError("unexpected send response shape")

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
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
                raise SimpleXError(
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
            raise SimpleXError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise SimpleXError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise SimpleXError("response not an object")
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

        chat_info = payload.get("chatInfo")
        if not isinstance(chat_info, dict):
            return None

        chat_item = payload.get("chatItem")
        if not isinstance(chat_item, dict):
            return None

        contact = chat_info.get("contact")
        if not isinstance(contact, dict):
            return None

        content_wrapper = chat_item.get("content")
        if not isinstance(content_wrapper, dict):
            return None

        msg_wrapper = content_wrapper.get("msg")
        if not isinstance(msg_wrapper, dict):
            return None

        msg_content = msg_wrapper.get("content")
        if not isinstance(msg_content, dict):
            return None

        text = msg_content.get("text")
        if not isinstance(text, str) or not text.strip():
            return None

        display_name = contact.get("displayName") or ""
        contact_id = contact.get("contactId") or ""

        meta = chat_item.get("meta")
        if not isinstance(meta, dict):
            return None

        item_id = meta.get("itemId") or ""
        created_at = meta.get("createdAt")

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"{contact_id}:{item_id}",
            sender_id=display_name,
            content=text,
            metadata={
                "platform": "simplex",
                "contact_id": contact_id,
                "item_id": item_id,
                "display_name": display_name,
            },
            received_at=_parse_simplex_ts(created_at),
        )

    @staticmethod
    def _split_thread_id(thread_id: str) -> tuple[str, str]:
        if ":" in thread_id:
            contact_id, _, item_id = thread_id.partition(":")
            return contact_id, item_id
        return thread_id, ""


def _parse_simplex_ts(raw: Any) -> datetime | None:
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw))
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw)
        except (ValueError, OverflowError, OSError):
            return None
    return None
