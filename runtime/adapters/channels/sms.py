from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs

from .base import Channel, InboundMessage, OutboundMessage, _sanitize_url

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.twilio.com"


class SmsError(RuntimeError):
    pass


class SmsSignatureError(ValueError):
    pass


class SmsChannel(Channel):
    channel_id: str = "sms"

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        channel_id: str = "sms",
        api_base_url: str = DEFAULT_API_BASE,
        http_client: Any = None,
        webhook_url: str = "",
    ) -> None:
        if not account_sid:
            raise ValueError("account_sid required")
        if not auth_token:
            raise ValueError("auth_token required")
        if not from_number:
            raise ValueError("from_number required")
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number
        self.channel_id = channel_id
        self.api_base_url = api_base_url.rstrip("/")
        self._http = http_client
        self._webhook_url = webhook_url
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

        to_number, _ = self._split_thread_id(msg.thread_id)
        if "to_number" in msg.metadata:
            to_number = str(msg.metadata["to_number"])

        url = f"{self.api_base_url}/2010-04-01/Accounts/{self._account_sid}/Messages.json"
        form_data: dict[str, str] = {
            "From": self._from_number,
            "To": to_number,
            "Body": content,
        }

        if self._http is not None:
            for attempt in range(4):
                try:
                    resp = self._http.post(url, data=form_data)
                except Exception as e:  # noqa: BLE001
                    raise SmsError(
                        f"network: {type(e).__name__}: {e}",
                    ) from e
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
                    resp = self._bare_client.post(url, data=form_data)
                except Exception as e:  # noqa: BLE001
                    raise SmsError(
                        f"network: {type(e).__name__}: {e}",
                    ) from e
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

        status = getattr(resp, "status_code", 200)
        if status >= 400:
            raise SmsError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )

    def _verify_signature(
        self,
        *,
        url: str,
        body: bytes,
        signature: str,
    ) -> None:
        if not signature:
            raise SmsSignatureError("missing X-Twilio-Signature header")

        params = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        sorted_params = sorted(params.items())
        data = url
        for key, values in sorted_params:
            for value in values:
                data += key + value

        expected = base64.b64encode(
            hmac.new(
                self._auth_token.encode("utf-8"),
                data.encode("utf-8"),
                hashlib.sha1,
            ).digest(),
        ).decode("utf-8")

        if not hmac.compare_digest(expected, signature):
            raise SmsSignatureError("signature mismatch")

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        lowered = {k.lower(): v for k, v in headers.items()}
        signature = lowered.get("x-twilio-signature", "")

        if self._webhook_url:
            self._verify_signature(
                url=self._webhook_url,
                body=body,
                signature=signature,
            )

        params = parse_qs(body.decode("utf-8"), keep_blank_values=True)

        text_list = params.get("Body", [])
        text = text_list[0] if text_list else ""
        if not text or not text.strip():
            return None

        from_list = params.get("From", [])
        sender_phone = from_list[0] if from_list else ""

        sid_list = params.get("MessageSid", [])
        message_sid = sid_list[0] if sid_list else ""

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"{sender_phone}:{message_sid}",
            sender_id=sender_phone,
            content=text,
            metadata={
                "platform": "sms",
                "from_number": sender_phone,
                "message_sid": message_sid,
            },
            received_at=_parse_sms_ts(params.get("MessageDate", [None])[0]),
        )

    @staticmethod
    def _split_thread_id(thread_id: str) -> tuple[str, str]:
        if ":" in thread_id:
            phone, _, sid = thread_id.partition(":")
            return phone, sid
        return thread_id, ""


def _parse_sms_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
