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

DEFAULT_API_BASE = "https://api.yuanbao.tencent.com"


class YuanbaoError(RuntimeError):
    pass


class YuanbaoSignatureError(ValueError):
    pass


class YuanbaoChannel(Channel):
    channel_id: str = "yuanbao"
    supports_edit: bool = True

    def __init__(
        self,
        *,
        bot_id: str,
        bot_token: str,
        channel_id: str = "yuanbao",
        api_base_url: str = DEFAULT_API_BASE,
        http_client: Any = None,
    ) -> None:
        if not bot_id:
            raise ValueError("bot_id required")
        if not bot_token:
            raise ValueError("bot_token required")
        self._bot_id = bot_id
        self._bot_token = bot_token
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

    def health_check(self) -> bool:
        try:
            url = f"{self.api_base_url}/api/bot/{self._bot_id}/info"
            headers: dict[str, str] = {}
            if self._bot_token:
                headers["Authorization"] = f"Bearer {self._bot_token}"
            if self._http is not None:
                client = self._http
            else:
                if not hasattr(self, "_bare_client") or self._bare_client is None:
                    self._bare_client = httpx.Client(timeout=15.0)
                client = self._bare_client
            resp = client.post(url, json={}, headers=headers)
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

        chat_id = msg.metadata.get("chat_id") or msg.thread_id.split(":")[0]
        url = f"{self.api_base_url}/api/bot/{self._bot_id}/message"
        body = {
            "chat_id": chat_id,
            "content": {"type": "text", "text": content},
            "msg_type": "text",
        }
        resp = self._post_json(url, body=body, bearer=self._bot_token)
        if resp.get("code", 0) != 0:
            raise YuanbaoError(
                f"yuanbao send failed: code={resp.get('code')} msg={resp.get('msg', '')[:200]}",
            )

    def edit(self, msg: OutboundMessage, original_message_id: str) -> None:
        url = f"{self.api_base_url}/api/bot/{self._bot_id}/message/{original_message_id}"
        body = {
            "content": {"type": "text", "text": msg.content},
        }
        resp = self._post_json(url, body=body, bearer=self._bot_token)
        if resp.get("code", 0) != 0:
            raise YuanbaoError(
                f"yuanbao edit failed: code={resp.get('code')} msg={resp.get('msg', '')[:200]}",
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

        token = payload.get("token")
        if token != self._bot_token:
            raise YuanbaoSignatureError("bot_token mismatch on webhook")

        content = payload.get("content", {})
        if isinstance(content, dict):
            text = content.get("text", "")
        elif isinstance(content, str):
            text = content
        else:
            text = ""
        if not isinstance(text, str) or not text.strip():
            return None

        from_user = payload.get("from_user") or {}
        sender_openid = from_user.get("openid", "")
        chat_id = payload.get("chat_id", "")
        message_id = payload.get("message_id", "")
        create_time = payload.get("create_time")

        if not isinstance(chat_id, str) or not chat_id:
            return None

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"{chat_id}:{message_id}",
            sender_id=str(sender_openid),
            content=text,
            metadata={
                "platform": "yuanbao",
                "chat_id": chat_id,
                "message_id": message_id,
            },
            received_at=_parse_yuanbao_ts(create_time),
        )

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        bearer: str | None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"

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
                raise YuanbaoError(
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
            raise YuanbaoError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise YuanbaoError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise YuanbaoError("response not an object")
        return data


def _parse_yuanbao_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, (int, str)):
        return None
    try:
        ms = int(raw)
        return datetime.fromtimestamp(ms / 1000.0)
    except (ValueError, OverflowError, OSError):
        return None
