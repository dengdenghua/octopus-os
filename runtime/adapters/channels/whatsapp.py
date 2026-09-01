from __future__ import annotations

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

DEFAULT_API_BASE = "https://graph.facebook.com/v21.0"


class WhatsAppError(RuntimeError):
    pass


class WhatsAppSignatureError(ValueError):
    pass


class WhatsAppChannel(Channel):
    channel_id: str = "whatsapp"

    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        verify_token: str = "",
        app_secret: str = "",
        channel_id: str = "whatsapp",
        api_base_url: str = DEFAULT_API_BASE,
        http_client: Any = None,
    ) -> None:
        if not phone_number_id:
            raise ValueError("phone_number_id required")
        if not access_token:
            raise ValueError("access_token required")
        self._phone_number_id = phone_number_id
        self._access_token = access_token
        self._verify_token = verify_token
        self._app_secret = app_secret
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
            url = f"{self.api_base_url}/{self._phone_number_id}"
            headers = {"Authorization": f"Bearer {self._access_token}"}
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

        recipient, _ = self._split_thread_id(msg.thread_id)
        if "wa_id" in msg.metadata:
            recipient = str(msg.metadata["wa_id"])

        url = f"{self.api_base_url}/{self._phone_number_id}/messages"

        if msg.content:
            body: dict[str, Any] = {
                "messaging_product": "whatsapp",
                "to": recipient,
                "type": "text",
                "text": {"body": content},
            }
            resp_data = self._post_json(url, body=body)
            if "error" in resp_data:
                err = resp_data["error"]
                raise WhatsAppError(
                    f"whatsapp sendMessage failed: {err.get('message', 'unknown')[:200]}",
                )

        for att in msg.attachments:
            is_image = att.content_type.startswith("image/")
            if is_image:
                att_body = {
                    "messaging_product": "whatsapp",
                    "to": recipient,
                    "type": "image",
                    "image": {
                        "link": att.url or "",
                        "caption": att.filename or "",
                    },
                }
            else:
                att_body = {
                    "messaging_product": "whatsapp",
                    "to": recipient,
                    "type": "document",
                    "document": {
                        "link": att.url or "",
                        "filename": att.filename or "file",
                        "caption": att.filename or "",
                    },
                }
            att_resp = self._post_json(url, body=att_body)
            if "error" in att_resp:
                err = att_resp["error"]
                raise WhatsAppError(
                    f"whatsapp send attachment failed: {err.get('message', 'unknown')[:200]}",
                )

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._access_token}",
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
                raise WhatsAppError(
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
            raise WhatsAppError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise WhatsAppError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise WhatsAppError("response not an object")
        return data

    def verify_webhook(
        self,
        *,
        mode: str,
        token: str,
        challenge: str,
    ) -> str:
        if mode != "subscribe":
            raise WhatsAppSignatureError(f"unexpected hub.mode: {mode!r}")
        if token != self._verify_token:
            raise WhatsAppSignatureError("hub.verify_token mismatch")
        return challenge

    def _verify_signature(self, *, body: bytes, signature: str) -> None:
        if not self._app_secret:
            return
        if not signature:
            raise WhatsAppSignatureError("missing X-Hub-Signature-256 header")
        expected = (
            "sha256="
            + hmac.new(
                self._app_secret.encode("utf-8"),
                body,
                hashlib.sha256,
            ).hexdigest()
        )
        if not hmac.compare_digest(expected, signature):
            raise WhatsAppSignatureError("signature mismatch")

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        lowered = {k.lower(): v for k, v in headers.items()}
        signature = lowered.get("x-hub-signature-256", "")
        self._verify_signature(body=body, signature=signature)

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"bad json body: {e}") from e
        if not isinstance(payload, dict):
            raise ValueError("payload not an object")

        if payload.get("object") != "whatsapp_business_account":
            return None

        entries = payload.get("entry")
        if not isinstance(entries, list):
            return None

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            changes = entry.get("changes")
            if not isinstance(changes, list):
                continue
            for change in changes:
                if not isinstance(change, dict):
                    continue
                value = change.get("value")
                if not isinstance(value, dict):
                    continue
                messages = value.get("messages")
                if not isinstance(messages, list):
                    continue
                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    msg_type = message.get("type")
                    text = ""
                    attachments: list[Attachment] = []

                    if msg_type == "text":
                        text_obj = message.get("text")
                        if not isinstance(text_obj, dict):
                            continue
                        text = text_obj.get("body")
                        if not isinstance(text, str) or not text.strip():
                            continue
                    elif msg_type == "image":
                        image_obj = message.get("image")
                        if isinstance(image_obj, dict):
                            image_url = image_obj.get("link", "") or image_obj.get("id", "")
                            caption = image_obj.get("caption", "")
                            if image_url:
                                attachments.append(
                                    Attachment(
                                        content_type="image",
                                        url=image_url,
                                        filename=caption,
                                        metadata=image_obj,
                                    )
                                )
                        text = (
                            (image_obj or {}).get("caption", "")
                            if isinstance(image_obj, dict)
                            else ""
                        )
                    elif msg_type == "document":
                        doc_obj = message.get("document")
                        if isinstance(doc_obj, dict):
                            doc_url = doc_obj.get("link", "") or doc_obj.get("id", "")
                            doc_filename = doc_obj.get("filename", "")
                            caption = doc_obj.get("caption", "")
                            if doc_url:
                                attachments.append(
                                    Attachment(
                                        content_type="application/octet-stream",
                                        url=doc_url,
                                        filename=doc_filename,
                                        metadata=doc_obj,
                                    )
                                )
                        text = caption
                    else:
                        if msg_type != "text":
                            continue

                    if not text and not attachments:
                        continue

                    wa_id = ""
                    contacts = value.get("contacts")
                    if isinstance(contacts, list) and contacts:
                        contact = contacts[0]
                        if isinstance(contact, dict):
                            wa_id = contact.get("wa_id", "")

                    message_id = message.get("id", "")
                    timestamp = message.get("timestamp")
                    from_id = message.get("from", wa_id)

                    return InboundMessage(
                        channel_id=self.channel_id,
                        thread_id=f"{from_id}:{message_id}",
                        sender_id=str(from_id),
                        content=text,
                        metadata={
                            "platform": "whatsapp",
                            "wa_id": wa_id,
                            "message_id": message_id,
                            "from": from_id,
                        },
                        received_at=_parse_whatsapp_ts(timestamp),
                        attachments=attachments,
                    )

        return None

    @staticmethod
    def _split_thread_id(thread_id: str) -> tuple[str, str]:
        if ":" in thread_id:
            wa_id, _, msg_id = thread_id.partition(":")
            return wa_id, msg_id
        return thread_id, ""


def _parse_whatsapp_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, (int, float, str)):
        return None
    try:
        return datetime.fromtimestamp(float(raw))
    except (ValueError, OverflowError, OSError):
        return None
