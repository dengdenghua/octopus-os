from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from datetime import UTC, datetime
from typing import Any

from .base import Attachment, Channel, InboundMessage, OutboundMessage, _sanitize_url

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


class DingTalkError(RuntimeError):
    pass


class DingTalkSignatureError(ValueError):
    pass


class DingTalkChannel(Channel):
    def __init__(
        self,
        *,
        webhook_url: str,
        secret: str | None = None,
        channel_id: str = "dingtalk",
        http_client: Any = None,
        max_age_seconds: int = 3600,  # Implementation note.
    ) -> None:
        if not webhook_url:
            raise ValueError("webhook_url required")
        if not webhook_url.startswith(("http://", "https://")):
            raise ValueError(f"webhook_url must be http(s): {webhook_url!r}")
        self._webhook_url = webhook_url
        self._secret = secret
        self.channel_id = channel_id
        self._http = http_client
        self._max_age_seconds = max_age_seconds
        self.send_log: list[OutboundMessage] = []

    # ─── Channel ABC ────────────────────────────

    def start(self) -> None:
        pass

    def stop(self) -> None:
        if hasattr(self, "_bare_client") and self._bare_client is not None:
            self._bare_client.close()
            self._bare_client = None

    def sign(self, timestamp_ms: str | int) -> str:
        if self._secret is None:
            raise DingTalkError("secret not configured · cannot sign")
        ts = str(timestamp_ms)
        string_to_sign = f"{ts}\n{self._secret}"
        h = hmac.new(
            self._secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        )
        return urllib.parse.quote_plus(base64.b64encode(h.digest()))

    def verify_signature(
        self,
        *,
        body: bytes,
        timestamp: str,
        signature: str,
    ) -> None:
        if self._secret is None:
            return
        if not timestamp:
            raise DingTalkSignatureError("missing timestamp")
        if not signature:
            raise DingTalkSignatureError("missing signature")

        try:
            ts_ms = int(timestamp)
        except ValueError as e:
            raise DingTalkSignatureError(f"invalid timestamp: {timestamp!r}") from e
        drift_seconds = abs(time.time() * 1000 - ts_ms) / 1000
        if drift_seconds > self._max_age_seconds:
            raise DingTalkSignatureError(
                f"timestamp drift too large: {drift_seconds:.0f}s",
            )

        expected = self.sign(timestamp)
        if not hmac.compare_digest(expected, signature):
            h = hmac.new(
                self._secret.encode("utf-8"),
                f"{timestamp}\n{self._secret}".encode(),
                hashlib.sha256,
            )
            raw_b64 = base64.b64encode(h.digest()).decode("utf-8")
            if not hmac.compare_digest(raw_b64, signature):
                raise DingTalkSignatureError("signature mismatch")

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        headers_norm = {k.lower(): v for k, v in headers.items()}
        self.verify_signature(
            body=body,
            timestamp=headers_norm.get("timestamp", ""),
            signature=headers_norm.get("sign", ""),
        )

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise DingTalkSignatureError(f"invalid JSON body: {e}") from e

        msgtype = payload.get("msgtype", "")
        content = ""
        attachments: list[Attachment] = []

        if msgtype == "text":
            content = payload.get("text", {}).get("content", "").strip()
        elif msgtype == "picture":
            pic_data = payload.get("content", {})
            if isinstance(pic_data, dict):
                image_code = pic_data.get("imageCode", "")
                download_code = pic_data.get("downloadCode", "")
                if image_code:
                    attachments.append(
                        Attachment(
                            content_type="image",
                            metadata={"imageCode": image_code, "downloadCode": download_code},
                        )
                    )
            content = (
                payload.get("text", {}).get("content", "").strip()
                if isinstance(payload.get("text"), dict)
                else ""
            )
        elif msgtype == "file":
            file_data = payload.get("content", {})
            if isinstance(file_data, dict):
                file_code = file_data.get("fileCode", "")
                download_code = file_data.get("downloadCode", "")
                file_name = file_data.get("fileName", "")
                if file_code:
                    attachments.append(
                        Attachment(
                            content_type="application/octet-stream",
                            filename=file_name,
                            metadata={"fileCode": file_code, "downloadCode": download_code},
                        )
                    )
            content = (
                payload.get("text", {}).get("content", "").strip()
                if isinstance(payload.get("text"), dict)
                else ""
            )
        else:
            if msgtype != "text":
                return None

        if not content and not attachments:
            return None

        conv_id = payload.get("conversationId", "")
        sender_id = payload.get("senderId", "")
        create_at_ms = payload.get("createAt")

        received_at = None
        if create_at_ms is not None:
            try:
                received_at = datetime.fromtimestamp(int(create_at_ms) / 1000, UTC)
            except (TypeError, ValueError):
                received_at = None

        metadata: dict[str, Any] = {
            "sender_nick": payload.get("senderNick", ""),
            "conversation_title": payload.get("conversationTitle", ""),
            "conversation_type": payload.get("conversationType", ""),
            "chatbot_user_id": payload.get("chatbotUserId", ""),
            "raw_msg_type": msgtype,
        }

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=conv_id,
            sender_id=sender_id,
            content=content,
            metadata=metadata,
            received_at=received_at,
            attachments=attachments,
        )

    def send(self, msg: OutboundMessage) -> None:
        # Empty messages are a no-op: don't POST, don't even log.
        # Channel adapters should reject upstream junk silently rather
        # than spam the audit trail with placeholder rows.
        if not msg.content:
            return
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

        url = self._webhook_url
        if self._secret is not None:
            ts = str(int(time.time() * 1000))
            sign = self.sign(ts)
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}timestamp={ts}&sign={sign}"

        if msg.content:
            body: dict[str, Any] = {
                "msgtype": "text",
                "text": {"content": content},
            }
            at = msg.metadata.get("at")
            if at is not None:
                body["at"] = at

            if not HTTPX_AVAILABLE and self._http is None:
                logger.warning(
                    "DingTalkChannel.send: httpx not installed · logged only",
                )
                return

            if self._http is not None:
                client = self._http
            else:
                if not hasattr(self, "_bare_client") or self._bare_client is None:
                    self._bare_client = httpx.Client(timeout=15.0)
                client = self._bare_client
            for attempt in range(4):
                try:
                    r = client.post(
                        url,
                        json=body,
                        headers={"Content-Type": "application/json"},
                    )
                except Exception as e:
                    if isinstance(e, DingTalkError):
                        raise
                    raise DingTalkError(f"dingtalk webhook send failed: {e}") from e
                if r.status_code < 500 and r.status_code != 429:
                    logger.info(
                        "channel.send", extra={"channel": self.channel_id, "status": r.status_code}
                    )
                    break
                if attempt == 3:
                    logger.error(
                        "channel.send.failed",
                        extra={"channel": self.channel_id, "status": r.status_code},
                    )
                    break
                logger.warning(
                    "channel.send.retry",
                    extra={"channel": self.channel_id, "status": r.status_code, "attempt": attempt},
                )
                time.sleep(2**attempt)
            if getattr(r, "status_code", 0) >= 400:
                raise DingTalkError(
                    f"POST {_sanitize_url(url)} failed: HTTP {r.status_code}",
                )
            try:
                data = r.json()
                if isinstance(data, dict) and data.get("errcode", 0) != 0:
                    raise DingTalkError(
                        f"dingtalk errcode={data.get('errcode')}: {data.get('errmsg', '')[:200]}",
                    )
            except ValueError:  # noqa: BLE001 — non-JSON response is acceptable, ignore errcode check
                pass

        for att in msg.attachments:
            is_image = att.content_type.startswith("image/")
            if is_image:
                upload_url = (
                    url.replace("/robot/send", "/media/upload") if "/robot/send" in url else url
                )
                files = {
                    "media": (
                        att.filename or "image",
                        att.data if isinstance(att.data, bytes) else att.data.encode(),
                        att.content_type,
                    )
                }
                try:
                    if self._http is not None:
                        upload_resp = self._http.post(upload_url, files=files)
                    else:
                        if not hasattr(self, "_bare_client") or self._bare_client is None:
                            self._bare_client = httpx.Client(timeout=15.0)
                        upload_resp = self._bare_client.post(upload_url, files=files, timeout=30.0)
                    upload_data = upload_resp.json()
                    media_id = upload_data.get("media_id", "")
                except Exception:
                    media_id = ""
                att_body: dict[str, Any] = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": att.filename or "image",
                        "text": f"![{att.filename or 'image'}]({att.url or media_id})",
                    },
                }
                at = msg.metadata.get("at")
                if at is not None:
                    att_body["at"] = at
                try:
                    if self._http is not None:
                        self._http.post(
                            url,
                            json=att_body,
                            headers={"Content-Type": "application/json"},
                        )
                    else:
                        if not hasattr(self, "_bare_client") or self._bare_client is None:
                            self._bare_client = httpx.Client(timeout=15.0)
                        self._bare_client.post(
                            url,
                            json=att_body,
                            headers={"Content-Type": "application/json"},
                        )
                except Exception as e:
                    raise DingTalkError(f"dingtalk send image failed: {e}") from e
            else:
                upload_url = (
                    url.replace("/robot/send", "/media/upload") if "/robot/send" in url else url
                )
                files = {
                    "media": (
                        att.filename or "file",
                        att.data if isinstance(att.data, bytes) else att.data.encode(),
                        att.content_type,
                    )
                }
                try:
                    if self._http is not None:
                        upload_resp = self._http.post(upload_url, files=files)
                    else:
                        if not hasattr(self, "_bare_client") or self._bare_client is None:
                            self._bare_client = httpx.Client(timeout=15.0)
                        upload_resp = self._bare_client.post(upload_url, files=files, timeout=30.0)
                    upload_data = upload_resp.json()
                    media_id = upload_data.get("media_id", "")
                except Exception:
                    media_id = ""
                att_body = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": att.filename or "file",
                        "text": f"[{att.filename or 'file'}]({att.url or media_id})",
                    },
                }
                at = msg.metadata.get("at")
                if at is not None:
                    att_body["at"] = at
                try:
                    if self._http is not None:
                        self._http.post(
                            url,
                            json=att_body,
                            headers={"Content-Type": "application/json"},
                        )
                    else:
                        if not hasattr(self, "_bare_client") or self._bare_client is None:
                            self._bare_client = httpx.Client(timeout=15.0)
                        self._bare_client.post(
                            url,
                            json=att_body,
                            headers={"Content-Type": "application/json"},
                        )
                except Exception as e:
                    raise DingTalkError(f"dingtalk send file failed: {e}") from e
