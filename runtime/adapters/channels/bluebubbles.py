from __future__ import annotations

import json
import logging
import time
import uuid
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


class BlueBubblesError(RuntimeError):
    pass


class BlueBubblesChannel(Channel):
    channel_id: str = "bluebubbles"

    def __init__(
        self,
        *,
        server_url: str,
        api_key: str,
        password: str = "",
        channel_id: str = "bluebubbles",
        http_client: Any = None,
    ) -> None:
        if not server_url:
            raise ValueError("server_url required")
        if not api_key:
            raise ValueError("api_key required")
        self._server_url = server_url.rstrip("/")
        self._api_key = api_key
        self._password = password
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

        chat_guid, _ = self._split_thread_id(msg.thread_id)
        if "chat_guid" in msg.metadata:
            chat_guid = str(msg.metadata["chat_guid"])

        body: dict[str, Any] = {
            "chatGuid": chat_guid,
            "tempGuid": str(uuid.uuid4()),
            "message": content,
            "method": "apple-script",
        }

        url = f"{self._server_url}/api/v1/message/text"
        params: dict[str, str] = {}
        if self._password:
            params["password"] = self._password

        resp_data = self._post_json(url, body=body, params=params)
        status = resp_data.get("status", "")
        if status not in (200, "200", "success"):
            raise BlueBubblesError(
                f"bluebubbles send failed: "
                f"{resp_data.get('message', resp_data.get('error', 'unknown'))[:200]}",
            )

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._http is not None:
            client = self._http
        else:
            if not hasattr(self, "_bare_client") or self._bare_client is None:
                self._bare_client = httpx.Client(timeout=15.0)
            client = self._bare_client
        for attempt in range(4):
            try:
                resp = client.post(url, json=body, headers=headers, params=params)
            except Exception as e:  # noqa: BLE001
                raise BlueBubblesError(
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
            raise BlueBubblesError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise BlueBubblesError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise BlueBubblesError("response not an object")
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

        data = payload.get("data")
        if not isinstance(data, dict):
            return None

        message = data.get("message")
        if not isinstance(message, dict):
            return None

        is_from_me = message.get("isFromMe")
        if is_from_me is True:
            return None

        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return None

        message_guid = message.get("guid")
        if not message_guid:
            return None

        chats = message.get("chats")
        chat_guid = ""
        if isinstance(chats, list) and len(chats) > 0:
            first_chat = chats[0]
            if isinstance(first_chat, dict):
                chat_guid = first_chat.get("guid", "")

        if not chat_guid:
            return None

        sender_id = str(message.get("sender", "")) if message.get("sender") else ""

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"{chat_guid}:{message_guid}",
            sender_id=sender_id,
            content=text,
            metadata={
                "platform": "bluebubbles",
                "chat_guid": chat_guid,
                "message_guid": message_guid,
                "is_from_me": False,
            },
            received_at=_parse_bluebubbles_ts(message.get("dateCreated")),
        )

    @staticmethod
    def _split_thread_id(thread_id: str) -> tuple[str, str]:
        if ":" in thread_id:
            cid, _, mid = thread_id.partition(":")
            return cid, mid
        return thread_id, ""


def _parse_bluebubbles_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, (int, float)):
        return None
    try:
        ts = float(raw)
        if ts > 1e12:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts)
    except (ValueError, OverflowError, OSError):
        return None
