from __future__ import annotations

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

DEFAULT_API_BASE = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


class TelegramSecretMismatch(ValueError):
    pass


class TelegramChannel(Channel):
    channel_id: str = "telegram"
    supports_edit: bool = True
    supports_typing: bool = True
    supports_reactions: bool = True

    def __init__(
        self,
        *,
        bot_token: str,
        webhook_secret: str = "",
        channel_id: str = "telegram",
        api_base_url: str = DEFAULT_API_BASE,
        http_client: Any = None,
    ) -> None:
        if not bot_token:
            raise ValueError("bot_token required")
        self._bot_token = bot_token
        self._webhook_secret = webhook_secret
        self.channel_id = channel_id
        self.api_base_url = api_base_url.rstrip("/")
        self._http = http_client
        self.send_log: list[OutboundMessage] = []

    # ─── Channel ABC ────────────────────────────

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

        chat_id, reply_to = self._split_thread_id(msg.thread_id)
        if "chat_id" in msg.metadata:
            chat_id = str(msg.metadata["chat_id"])
        if "reply_to_message_id" in msg.metadata:
            reply_to = str(msg.metadata["reply_to_message_id"])

        if not msg.attachments:
            body: dict[str, Any] = {
                "chat_id": chat_id,
                "text": content,
            }
            if reply_to:
                body["reply_to_message_id"] = int(reply_to)

            url = f"{self.api_base_url}/bot{self._bot_token}/sendMessage"
            resp_data = self._post_json(url, body=body)
            if not resp_data.get("ok", False):
                raise TelegramError(
                    f"telegram sendMessage failed: {resp_data.get('description', 'unknown')[:200]}",
                )
            return

        for i, attachment in enumerate(msg.attachments):
            caption = content if i == 0 else ""
            if attachment.content_type.startswith("image"):
                api = "sendPhoto"
                file_key = "photo"
            else:
                api = "sendDocument"
                file_key = "document"
            body: dict[str, Any] = {
                "chat_id": chat_id,
                file_key: attachment.url,
                "caption": caption,
            }
            if reply_to:
                body["reply_to_message_id"] = int(reply_to)
            url = f"{self.api_base_url}/bot{self._bot_token}/{api}"
            resp_data = self._post_json(url, body=body)
            if not resp_data.get("ok", False):
                raise TelegramError(
                    f"telegram {api} failed: {resp_data.get('description', 'unknown')[:200]}",
                )

    def edit(self, msg: OutboundMessage, original_message_id: str) -> None:
        chat_id, _ = self._split_thread_id(msg.thread_id)
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": int(original_message_id),
            "text": msg.content,
        }
        url = f"{self.api_base_url}/bot{self._bot_token}/editMessageText"
        try:
            resp_data = self._post_json(url, body=body)
        except TelegramError as exc:
            if "message is not modified" in str(exc).lower():
                return
            raise
        if not resp_data.get("ok", False):
            desc = resp_data.get("description", "unknown")
            if "message is not modified" in desc.lower():
                return
            raise TelegramError(
                f"telegram editMessageText failed: {desc[:200]}",
            )

    def send_typing(self, thread_id: str) -> None:
        chat_id, _ = self._split_thread_id(thread_id)
        url = f"{self.api_base_url}/bot{self._bot_token}/sendChatAction"
        self._post_json(url, body={"chat_id": chat_id, "action": "typing"})

    def add_reaction(self, thread_id: str, message_id: str, emoji: str) -> None:
        chat_id, _ = self._split_thread_id(thread_id)
        url = f"{self.api_base_url}/bot{self._bot_token}/setMessageReaction"
        self._post_json(
            url,
            body={
                "chat_id": chat_id,
                "message_id": int(message_id),
                "reaction": [{"type": "emoji", "emoji": emoji}],
            },
        )

    def health_check(self) -> bool:
        try:
            url = f"{self.api_base_url}/bot{self._bot_token}/getMe"
            data = self._post_json(url, body={})
            return data.get("ok", False)
        except Exception:
            return False

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
                resp = client.post(
                    url,
                    json=body,
                    headers=headers,
                )
            except Exception as e:  # noqa: BLE001
                raise TelegramError(
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
            raise TelegramError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise TelegramError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise TelegramError("response not an object")
        return data

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        if self._webhook_secret:
            lowered = {k.lower(): v for k, v in headers.items()}
            recv = lowered.get("x-telegram-bot-api-secret-token", "")
            if recv != self._webhook_secret:
                raise TelegramSecretMismatch(
                    "X-Telegram-Bot-Api-Secret-Token mismatch",
                )

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"bad json body: {e}") from e
        if not isinstance(payload, dict):
            raise ValueError("payload not an object")

        message = payload.get("message")
        if not isinstance(message, dict):
            return None

        text = message.get("text") or ""

        attachments: list[Attachment] = []
        photos = message.get("photo")
        if isinstance(photos, list) and photos:
            largest = photos[-1]
            file_id = largest.get("file_id", "")
            attachments.append(
                Attachment(
                    content_type="image/jpeg",
                    url=file_id,
                    metadata={"file_id": file_id},
                )
            )
        document = message.get("document")
        if isinstance(document, dict):
            file_id = document.get("file_id", "")
            attachments.append(
                Attachment(
                    content_type=document.get("mime_type", "application/octet-stream"),
                    url=file_id,
                    filename=document.get("file_name", ""),
                    metadata={"file_id": file_id},
                )
            )

        if (not isinstance(text, str) or not text.strip()) and not attachments:
            return None

        sender = message.get("from") or {}
        if isinstance(sender, dict) and sender.get("is_bot"):
            return None

        chat = message.get("chat") or {}
        if not isinstance(chat, dict):
            return None
        chat_id = chat.get("id")
        if chat_id is None:
            return None

        message_id = message.get("message_id", 0)
        sender_id = str(sender.get("id", "")) if isinstance(sender, dict) else ""

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"{chat_id}:{message_id}",
            sender_id=sender_id,
            content=text,
            metadata={
                "platform": "telegram",
                "chat_id": chat_id,
                "chat_type": chat.get("type", "private"),
                "message_id": message_id,
                "reply_to_message_id": message_id,
                "sender_first_name": sender.get("first_name", "")
                if isinstance(sender, dict)
                else "",
                "sender_username": sender.get("username", "") if isinstance(sender, dict) else "",
            },
            received_at=_parse_telegram_ts(message.get("date")),
            attachments=attachments,
        )

    @staticmethod
    def _split_thread_id(thread_id: str) -> tuple[str, str]:
        """`{chat_id}:{message_id}` → (chat_id, message_id)."""
        if ":" in thread_id:
            cid, _, mid = thread_id.partition(":")
            return cid, mid
        return thread_id, ""


def _parse_telegram_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(raw))
    except (ValueError, OverflowError, OSError):
        return None
