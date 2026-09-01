from __future__ import annotations

import base64
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

DEFAULT_API_BASE = "http://localhost:8080"


class SignalError(RuntimeError):
    pass


class SignalSignatureError(ValueError):
    pass


class SignalChannel(Channel):
    channel_id: str = "signal"

    def __init__(
        self,
        *,
        phone_number: str,
        api_base_url: str = DEFAULT_API_BASE,
        webhook_secret: str = "",
        channel_id: str = "signal",
        http_client: Any = None,
    ) -> None:
        if not phone_number:
            raise ValueError("phone_number required")
        self._phone_number = phone_number
        self._webhook_secret = webhook_secret
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
            url = f"{self.api_base_url}/v1/about"
            if self._http is not None:
                client = self._http
            else:
                if not hasattr(self, "_bare_client") or self._bare_client is None:
                    self._bare_client = httpx.Client(timeout=15.0)
                client = self._bare_client
            resp = client.get(url)
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
        if "recipient" in msg.metadata:
            recipient = str(msg.metadata["recipient"])

        body: dict[str, Any] = {
            "message": content,
            "number": self._phone_number,
            "recipients": [recipient],
        }

        if msg.attachments:
            att_list = []
            for att in msg.attachments:
                if att.data:
                    if isinstance(att.data, bytes):
                        b64 = base64.b64encode(att.data).decode("utf-8")
                    else:
                        b64 = att.data
                    att_list.append(b64)
                elif att.url:
                    att_list.append(att.url)
            if att_list:
                body["base64_attachments"] = att_list

        url = f"{self.api_base_url}/v2/send"
        resp_data = self._post_json(url, body=body)
        if not isinstance(resp_data, dict):
            raise SignalError("unexpected send response shape")

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
                raise SignalError(
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
            raise SignalError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise SignalError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise SignalError("response not an object")
        return data

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        if self._webhook_secret:
            lowered = {k.lower(): v for k, v in headers.items()}
            recv = lowered.get("x-signal-secret", "")
            if recv != self._webhook_secret:
                raise SignalSignatureError(
                    "X-Signal-Secret mismatch",
                )

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"bad json body: {e}") from e
        if not isinstance(payload, dict):
            raise ValueError("payload not an object")

        envelope = payload.get("envelope")
        if not isinstance(envelope, dict):
            return None

        data_message = envelope.get("dataMessage")
        if not isinstance(data_message, dict):
            return None

        text = data_message.get("message")
        if (not isinstance(text, str) or not text.strip()) and not data_message.get("attachments"):
            return None

        attachments: list[Attachment] = []
        raw_attachments = data_message.get("attachments")
        if isinstance(raw_attachments, list):
            for raw_att in raw_attachments:
                if not isinstance(raw_att, dict):
                    continue
                att_url = raw_att.get("url", "")
                att_content_type = raw_att.get("contentType", "")
                att_filename = raw_att.get("filename", "")
                if att_url or att_content_type:
                    attachments.append(
                        Attachment(
                            content_type=att_content_type or "application/octet-stream",
                            url=att_url,
                            filename=att_filename,
                            metadata=raw_att,
                        )
                    )

        source = envelope.get("source") or ""
        if not isinstance(source, str):
            source = str(source)

        timestamp = data_message.get("timestamp")
        source_number = envelope.get("sourceNumber") or source
        source_name = envelope.get("sourceName") or ""
        group_info = data_message.get("groupInfo")

        thread_id = source_number
        if isinstance(group_info, dict):
            group_id = group_info.get("groupId")
            if isinstance(group_id, str) and group_id:
                thread_id = group_id

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=thread_id,
            sender_id=source,
            content=text if isinstance(text, str) else "",
            metadata={
                "platform": "signal",
                "source_number": source_number,
                "source_name": source_name,
                "timestamp": timestamp,
                "group_info": group_info,
            },
            received_at=_parse_signal_ts(timestamp),
            attachments=attachments,
        )

    @staticmethod
    def _split_thread_id(thread_id: str) -> tuple[str, str]:
        if ":" in thread_id:
            recipient, _, extra = thread_id.partition(":")
            return recipient, extra
        return thread_id, ""


def _parse_signal_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(raw) / 1000.0)
    except (ValueError, OverflowError, OSError):
        return None
