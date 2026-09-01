from __future__ import annotations

import contextlib
import email
import imaplib
import logging
import smtplib
import threading
from datetime import datetime
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import parseaddr, parsedate_to_datetime

from .base import Channel, InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)


class EmailError(RuntimeError):
    pass


class EmailChannel(Channel):
    channel_id: str = "email"

    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int = 587,
        imap_host: str,
        username: str,
        password: str,
        from_address: str,
        allowed_senders: list[str] | None = None,
        channel_id: str = "email",
    ) -> None:
        if not smtp_host:
            raise ValueError("smtp_host required")
        if not imap_host:
            raise ValueError("imap_host required")
        if not username:
            raise ValueError("username required")
        if not password:
            raise ValueError("password required")
        if not from_address:
            raise ValueError("from_address required")
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._imap_host = imap_host
        self._username = username
        self._password = password
        self._from_address = from_address
        self._allowed_senders = allowed_senders
        self.channel_id = channel_id
        self.send_log: list[OutboundMessage] = []
        self._imap: imaplib.IMAP4_SSL | None = None
        self._poll_thread: threading.Thread | None = None
        self._poll_stop = threading.Event()
        self._poll_interval: float = 30.0

    def start(self) -> None:
        self._connect_imap()
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
        )
        self._poll_thread.start()

    def stop(self) -> None:
        if hasattr(self, "_bare_client") and self._bare_client is not None:
            self._bare_client.close()
            self._bare_client = None
        self._poll_stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=10.0)
            self._poll_thread = None
        self._disconnect_imap()

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

        mime = MIMEText(content, "plain", "utf-8")
        mime["From"] = self._from_address
        mime["To"] = msg.metadata.get("recipient", self._username)
        mime["Subject"] = msg.metadata.get("subject", "Re:")
        if msg.thread_id:
            mime["In-Reply-To"] = msg.thread_id
            mime["References"] = msg.thread_id
        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self._username, self._password)
                server.send_message(mime)
        except Exception as e:
            raise EmailError(
                f"smtp send failed: {type(e).__name__}: {e}",
            ) from e

    def poll(self) -> list[InboundMessage]:
        if self._imap is None:
            self._connect_imap()
        messages: list[InboundMessage] = []
        try:
            self._imap.select("INBOX")
            status, data = self._imap.search(None, "UNSEEN")
        except Exception as e:
            raise EmailError(
                f"imap search failed: {type(e).__name__}: {e}",
            ) from e
        if status != "OK" or not data or not data[0]:
            return messages
        msg_ids = data[0].split()
        for mid in msg_ids:
            try:
                status, msg_data = self._imap.fetch(mid, "(RFC822)")
            except Exception as e:
                raise EmailError(
                    f"imap fetch failed: {type(e).__name__}: {e}",
                ) from e
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            parsed = email.message_from_bytes(raw)
            inbound = self._parse_email(parsed)
            if inbound is None:
                continue
            if self._allowed_senders is not None:  # noqa: SIM102
                if inbound.sender_id not in self._allowed_senders:
                    continue
            messages.append(inbound)
            with contextlib.suppress(Exception):
                self._imap.store(mid, "+FLAGS", "\\Seen")
        return messages

    def _connect_imap(self) -> None:
        try:
            self._imap = imaplib.IMAP4_SSL(self._imap_host)
            self._imap.login(self._username, self._password)
        except Exception as e:
            raise EmailError(
                f"imap connect failed: {type(e).__name__}: {e}",
            ) from e

    def _disconnect_imap(self) -> None:
        if self._imap is not None:
            with contextlib.suppress(Exception):
                self._imap.close()
            with contextlib.suppress(Exception):
                self._imap.logout()
            self._imap = None

    def _poll_loop(self) -> None:
        while not self._poll_stop.is_set():
            try:
                msgs = self.poll()
                for m in msgs:
                    self._dispatch(m)
            except Exception:  # noqa: BLE001 — poll failure shouldn't kill the whole loop
                pass
            self._poll_stop.wait(timeout=self._poll_interval)

    def _parse_email(self, parsed: email.message.Message) -> InboundMessage | None:
        message_id = parsed.get("Message-ID", "")
        if not message_id:
            return None
        from_header = parsed.get("From", "")
        sender_name, sender_addr = parseaddr(from_header)
        if not sender_addr:
            return None
        body = self._extract_text_body(parsed)
        if not body:
            return None
        date_str = parsed.get("Date", "")
        received_at = _parse_email_ts(date_str)
        subject = parsed.get("Subject", "")
        decoded_subject_parts = decode_header(subject)
        subject_text = ""
        for part, charset in decoded_subject_parts:
            if isinstance(part, bytes):
                subject_text += part.decode(charset or "utf-8", errors="replace")
            else:
                subject_text += part
        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=message_id,
            sender_id=sender_addr,
            content=body,
            metadata={
                "platform": "email",
                "message_id": message_id,
                "subject": subject_text,
                "from_name": sender_name,
                "from_address": sender_addr,
            },
            received_at=received_at,
        )

    @staticmethod
    def _extract_text_body(parsed: email.message.Message) -> str:
        if parsed.is_multipart():
            for part in parsed.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        continue
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
            return ""
        content_type = parsed.get_content_type()
        if content_type == "text/plain":
            payload = parsed.get_payload(decode=True)
            if payload is None:
                return ""
            charset = parsed.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        return ""


def _parse_email_ts(raw: str) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return parsedate_to_datetime(raw)
    except (ValueError, TypeError):
        return None
