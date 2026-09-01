from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime
from typing import Any

from .base import Attachment, Channel, InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


SLACK_API_POST_MESSAGE = "https://slack.com/api/chat.postMessage"


class SlackSignatureError(ValueError):
    pass


class SlackChannel(Channel):
    def __init__(
        self,
        *,
        bot_token: str,
        signing_secret: str,
        channel_id: str = "slack",
        http_client: Any = None,
        max_age_seconds: int = 300,
    ) -> None:
        if not bot_token:
            raise ValueError("bot_token required")
        if not signing_secret:
            raise ValueError("signing_secret required")
        self._bot_token = bot_token
        self._signing_secret = signing_secret
        self.channel_id = channel_id
        self._http = http_client  # Implementation note.
        self._max_age_seconds = max_age_seconds
        self.send_log: list[OutboundMessage] = []  # Implementation note.

    supports_edit: bool = True
    supports_typing: bool = True
    supports_reactions: bool = True

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

        channel, thread_ts = self._split_thread_id(msg.thread_id)
        payload: dict[str, Any] = {
            "channel": channel,
            "text": content,
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts

        image_attachments = [a for a in msg.attachments if a.content_type.startswith("image")]
        file_attachments = [a for a in msg.attachments if not a.content_type.startswith("image")]

        if image_attachments:
            blocks = payload.get("blocks", [])
            for att in image_attachments:
                if att.url:
                    blocks.append(
                        {
                            "type": "image",
                            "image_url": att.url,
                            "alt_text": att.filename or "image",
                        }
                    )
            payload["blocks"] = blocks

        if self._http is not None:
            for attempt in range(4):
                try:
                    resp = self._http.post(
                        SLACK_API_POST_MESSAGE,
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {self._bot_token}",
                            "Content-Type": "application/json; charset=utf-8",
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(f"slack_send_failed: {type(e).__name__}: {e}") from e
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
                        SLACK_API_POST_MESSAGE,
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {self._bot_token}",
                            "Content-Type": "application/json; charset=utf-8",
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(f"slack_send_failed: {type(e).__name__}: {e}") from e
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

        if getattr(resp, "status_code", 200) >= 400:
            raise RuntimeError(
                f"slack HTTP {resp.status_code}: {getattr(resp, 'text', '')[:200]}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"slack JSON parse: {e}") from e
        if not data.get("ok"):
            raise RuntimeError(
                f"slack error: {data.get('error', 'unknown')}",
            )

        for att in file_attachments:
            file_data = att.data if isinstance(att.data, bytes) and att.data else b""
            if not file_data and att.url:
                if self._http is not None:
                    dl_resp = self._http.get(att.url, timeout=15.0)
                else:
                    if not hasattr(self, "_bare_client") or self._bare_client is None:
                        self._bare_client = httpx.Client(timeout=15.0)
                    dl_resp = self._bare_client.get(att.url)
                file_data = dl_resp.content
            upload_data: dict[str, Any] = {
                "channels": channel,
                "filename": att.filename or "file",
            }
            if thread_ts:
                upload_data["thread_ts"] = thread_ts
            try:
                if self._http is not None:
                    resp = self._http.post(
                        "https://slack.com/api/files.upload",
                        data=upload_data,
                        files={
                            "file": (
                                att.filename or "file",
                                file_data,
                                att.content_type or "application/octet-stream",
                            )
                        },
                        headers={"Authorization": f"Bearer {self._bot_token}"},
                    )
                else:
                    if not hasattr(self, "_bare_client") or self._bare_client is None:
                        self._bare_client = httpx.Client(timeout=15.0)
                    resp = self._bare_client.post(
                        "https://slack.com/api/files.upload",
                        data=upload_data,
                        files={
                            "file": (
                                att.filename or "file",
                                file_data,
                                att.content_type or "application/octet-stream",
                            )
                        },
                        headers={"Authorization": f"Bearer {self._bot_token}"},
                        timeout=30.0,
                    )
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"slack_upload_failed: {type(e).__name__}: {e}") from e
            if getattr(resp, "status_code", 200) >= 400:
                raise RuntimeError(
                    f"slack upload HTTP {resp.status_code}: {getattr(resp, 'text', '')[:200]}",
                )

    def edit(self, msg: OutboundMessage, original_message_id: str) -> None:
        channel, _ = self._split_thread_id(msg.thread_id)
        payload: dict[str, Any] = {
            "channel": channel,
            "ts": original_message_id,
            "text": msg.content,
        }

        if self._http is not None:
            for attempt in range(4):
                try:
                    resp = self._http.post(
                        "https://slack.com/api/chat.update",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {self._bot_token}",
                            "Content-Type": "application/json; charset=utf-8",
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(f"slack_edit_failed: {type(e).__name__}: {e}") from e
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
                        "https://slack.com/api/chat.update",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {self._bot_token}",
                            "Content-Type": "application/json; charset=utf-8",
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(f"slack_edit_failed: {type(e).__name__}: {e}") from e
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

        if getattr(resp, "status_code", 200) >= 400:
            raise RuntimeError(
                f"slack HTTP {resp.status_code}: {getattr(resp, 'text', '')[:200]}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"slack JSON parse: {e}") from e
        if not data.get("ok"):
            raise RuntimeError(
                f"slack error: {data.get('error', 'unknown')}",
            )

    def send_typing(self, thread_id: str) -> None:
        return

    def add_reaction(self, thread_id: str, message_id: str, emoji: str) -> None:
        channel, _ = self._split_thread_id(thread_id)
        payload: dict[str, Any] = {
            "channel": channel,
            "timestamp": message_id,
            "name": emoji,
        }

        if self._http is not None:
            for attempt in range(4):
                try:
                    resp = self._http.post(
                        "https://slack.com/api/reactions.add",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {self._bot_token}",
                            "Content-Type": "application/json; charset=utf-8",
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(f"slack_reaction_failed: {type(e).__name__}: {e}") from e
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
                        "https://slack.com/api/reactions.add",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {self._bot_token}",
                            "Content-Type": "application/json; charset=utf-8",
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(f"slack_reaction_failed: {type(e).__name__}: {e}") from e
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

        if getattr(resp, "status_code", 200) >= 400:
            raise RuntimeError(
                f"slack HTTP {resp.status_code}: {getattr(resp, 'text', '')[:200]}",
            )

    def health_check(self) -> bool:
        try:
            headers = {
                "Authorization": f"Bearer {self._bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            if self._http is not None:
                client = self._http
            else:
                if not hasattr(self, "_bare_client") or self._bare_client is None:
                    self._bare_client = httpx.Client(timeout=15.0)
                client = self._bare_client
            resp = client.post("https://slack.com/api/auth.test", json={}, headers=headers)
            if resp.status_code != 200:
                return False
            data = resp.json()
            return data.get("ok", False)
        except Exception:
            return False

    def verify_signature(
        self,
        *,
        body: bytes,
        timestamp: str,
        signature: str,
        now: int | None = None,
    ) -> None:
        if not timestamp or not signature:
            raise SlackSignatureError("missing timestamp or signature header")
        try:
            ts_int = int(timestamp)
        except ValueError as e:
            raise SlackSignatureError(f"bad timestamp: {timestamp!r}") from e

        current = now if now is not None else int(time.time())
        if abs(current - ts_int) > self._max_age_seconds:
            raise SlackSignatureError(
                f"request too old: {current - ts_int}s > {self._max_age_seconds}s",
            )

        base = f"v0:{timestamp}:".encode() + body
        expected = (
            "v0="
            + hmac.new(
                self._signing_secret.encode("utf-8"),
                base,
                hashlib.sha256,
            ).hexdigest()
        )

        if not hmac.compare_digest(expected, signature):
            raise SlackSignatureError("signature mismatch")

    def parse_event(self, payload: dict[str, Any]) -> InboundMessage | None:
        event_type = payload.get("type")
        if event_type == "url_verification":
            return None

        if event_type != "event_callback":
            return None

        event = payload.get("event") or {}
        if not isinstance(event, dict):
            return None
        if event.get("type") != "message":
            return None
        if event.get("bot_id"):
            return None
        if event.get("subtype") == "bot_message":
            return None

        text = event.get("text")
        channel = event.get("channel")
        ts = event.get("ts") or ""
        thread_ts = event.get("thread_ts") or ts
        sender = event.get("user") or ""

        attachments: list[Attachment] = []
        files = event.get("files")
        if isinstance(files, list):
            for f in files:
                if isinstance(f, dict):
                    url = f.get("url_private", "") or f.get("url_private_download", "")
                    attachments.append(
                        Attachment(
                            content_type=f.get("mimetype", "application/octet-stream"),
                            url=url,
                            filename=f.get("name", ""),
                            metadata={"filetype": f.get("filetype", ""), "id": f.get("id", "")},
                        )
                    )

        if (not isinstance(text, str) or not text.strip()) and not attachments:
            return None
        if not isinstance(channel, str) or not channel:
            return None

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"{channel}:{thread_ts}",
            sender_id=str(sender),
            content=text or "",
            metadata={
                "platform": "slack",
                "slack_channel": channel,
                "slack_ts": ts,
                "slack_thread_ts": thread_ts,
            },
            received_at=datetime.fromtimestamp(float(ts)) if ts else None,
            attachments=attachments,
        )

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        lowered = {k.lower(): v for k, v in headers.items()}
        timestamp = lowered.get("x-slack-request-timestamp", "")
        signature = lowered.get("x-slack-signature", "")
        self.verify_signature(
            body=body,
            timestamp=timestamp,
            signature=signature,
        )

        import json

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"bad json body: {e}") from e
        if not isinstance(payload, dict):
            raise ValueError("payload not an object")

        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge", "")
            return {"challenge": str(challenge)}

        return self.parse_event(payload)

    @staticmethod
    def _split_thread_id(thread_id: str) -> tuple[str, str]:
        """`"{slack_channel}:{thread_ts}"` → (channel, thread_ts)。"""
        if ":" in thread_id:
            ch, _, ts = thread_id.partition(":")
            return ch, ts
        return thread_id, ""
