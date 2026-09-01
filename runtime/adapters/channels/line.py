from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from typing import Any

from .base import Attachment, Channel, InboundMessage, OutboundMessage, _sanitize_url

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.line.me"


class LineError(RuntimeError):
    pass


class LineSignatureError(ValueError):
    pass


class LineChannel(Channel):
    channel_id: str = "line"

    def __init__(
        self,
        *,
        channel_access_token: str,
        channel_secret: str,
        channel_id: str = "line",
        api_base_url: str = DEFAULT_API_BASE,
        http_client: Any = None,
    ) -> None:
        if not channel_access_token:
            raise ValueError("channel_access_token required")
        if not channel_secret:
            raise ValueError("channel_secret required")
        self._channel_access_token = channel_access_token
        self._channel_secret = channel_secret
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
            url = f"{self.api_base_url}/v2/bot/info"
            headers = {
                "Authorization": f"Bearer {self._channel_access_token}",
            }
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

        source_id, message_id = self._split_thread_id(msg.thread_id)
        reply_token = msg.metadata.get("replyToken") if msg.metadata else None
        to = msg.metadata.get("to") if msg.metadata else None

        messages: list[dict[str, Any]] = []
        if msg.content:
            messages.append({"type": "text", "text": content})

        for att in msg.attachments:
            if att.content_type.startswith("image/"):
                messages.append(
                    {
                        "type": "image",
                        "originalContentUrl": att.url or "",
                        "previewImageUrl": att.url or "",
                    }
                )

        if not messages:
            return

        if reply_token:
            body: dict[str, Any] = {
                "replyToken": reply_token,
                "messages": messages,
            }
            url = f"{self.api_base_url}/v2/bot/message/reply"
        else:
            target = to or source_id
            if not target:
                raise LineError("no target for push message")
            body = {
                "to": target,
                "messages": messages,
            }
            url = f"{self.api_base_url}/v2/bot/message/push"

        self._post_json(url, body=body)

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._channel_access_token}",
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
                raise LineError(
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
            raise LineError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise LineError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise LineError("response not an object")
        return data

    def _verify_signature(self, *, body: bytes, signature: str) -> None:
        if not signature:
            raise LineSignatureError("missing X-Line-Signature header")
        expected = hmac.new(
            self._channel_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        expected_b64 = base64.b64encode(expected).decode("utf-8")
        if not hmac.compare_digest(expected_b64, signature):
            raise LineSignatureError("signature mismatch")

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        lowered = {k.lower(): v for k, v in headers.items()}
        signature = lowered.get("x-line-signature", "")
        self._verify_signature(body=body, signature=signature)

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"bad json body: {e}") from e
        if not isinstance(payload, dict):
            raise ValueError("payload not an object")

        events = payload.get("events")
        if not isinstance(events, list):
            return None

        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") != "message":
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue

            msg_type = message.get("type", "text")
            text = ""
            attachments: list[Attachment] = []

            if msg_type == "text":
                text = message.get("text", "")
                if not isinstance(text, str) or not text.strip():
                    continue
            elif msg_type == "image":
                content_provider = message.get("contentProvider", {})
                if isinstance(content_provider, dict):
                    orig_url = content_provider.get("originalContentUrl", "")
                    prev_url = content_provider.get("previewImageUrl", "")
                    if orig_url:
                        attachments.append(
                            Attachment(
                                content_type="image",
                                url=orig_url,
                                metadata={"previewImageUrl": prev_url},
                            )
                        )
            elif msg_type == "video":
                content_provider = message.get("contentProvider", {})
                if isinstance(content_provider, dict):
                    orig_url = content_provider.get("originalContentUrl", "")
                    prev_url = content_provider.get("previewImageUrl", "")
                    if orig_url:
                        attachments.append(
                            Attachment(
                                content_type="video",
                                url=orig_url,
                                metadata={"previewImageUrl": prev_url},
                            )
                        )
            elif msg_type == "audio":
                content_provider = message.get("contentProvider", {})
                if isinstance(content_provider, dict):
                    orig_url = content_provider.get("originalContentUrl", "")
                    if orig_url:
                        attachments.append(
                            Attachment(
                                content_type="audio",
                                url=orig_url,
                            )
                        )
            else:
                continue

            if not text and not attachments:
                continue

            source = event.get("source") or {}
            if not isinstance(source, dict):
                continue

            source_id = source.get("groupId") or source.get("roomId") or source.get("userId") or ""
            user_id = source.get("userId") or ""
            message_id = message.get("id") or ""
            timestamp = event.get("timestamp")

            return InboundMessage(
                channel_id=self.channel_id,
                thread_id=f"{source_id}:{message_id}",
                sender_id=str(user_id),
                content=text,
                metadata={
                    "platform": "line",
                    "source_id": source_id,
                    "message_id": message_id,
                    "replyToken": event.get("replyToken", ""),
                    "source_type": source.get("type", ""),
                    "user_id": user_id,
                },
                received_at=_parse_line_ts(timestamp),
                attachments=attachments,
            )

        return None

    @staticmethod
    def _split_thread_id(thread_id: str) -> tuple[str, str]:
        if ":" in thread_id:
            sid, _, mid = thread_id.partition(":")
            return sid, mid
        return thread_id, ""


def _parse_line_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(raw) / 1000.0)
    except (ValueError, OverflowError, OSError):
        return None
