from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

from .base import Attachment, Channel, InboundMessage, OutboundMessage, _sanitize_url

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class MatrixError(RuntimeError):
    pass


class MatrixSignatureError(ValueError):
    pass


class MatrixChannel(Channel):
    channel_id: str = "matrix"
    supports_edit: bool = True
    supports_typing: bool = True
    supports_reactions: bool = True

    def __init__(
        self,
        *,
        homeserver_url: str,
        access_token: str,
        channel_id: str = "matrix",
        http_client: Any = None,
    ) -> None:
        if not homeserver_url:
            raise ValueError("homeserver_url required")
        if not access_token:
            raise ValueError("access_token required")
        self._homeserver_url = homeserver_url.rstrip("/")
        self._access_token = access_token
        self.channel_id = channel_id
        self._http = http_client
        self.send_log: list[OutboundMessage] = []
        self._txn_counter: int = 0
        self._user_id: str | None = None

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

        room_id, event_id = self._split_thread_id(msg.thread_id)

        if content:
            self._txn_counter += 1
            txn_id = f"{self._txn_counter}-{uuid4().hex[:8]}"
            url = (
                f"{self._homeserver_url}"
                f"/_matrix/client/v3/rooms/{room_id}"
                f"/send/m.room.message/{txn_id}"
            )
            body: dict[str, Any] = {
                "msgtype": "m.text",
                "body": content,
            }
            self._put_json(url, body=body)

        for att in msg.attachments:
            is_image = att.content_type.startswith("image/")
            if att.url and att.url.startswith("mxc://"):
                mxc_url = att.url
            elif att.data:
                upload_url = f"{self._homeserver_url}/_matrix/media/v3/upload"
                headers = {
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": att.content_type,
                }
                data = att.data if isinstance(att.data, bytes) else att.data.encode()
                if self._http is not None:
                    upload_resp = self._http.post(upload_url, content=data, headers=headers)
                else:
                    if not hasattr(self, "_bare_client") or self._bare_client is None:
                        self._bare_client = httpx.Client(timeout=15.0)
                    upload_resp = self._bare_client.post(
                        upload_url, content=data, headers=headers, timeout=30.0
                    )
                upload_data = upload_resp.json()
                mxc_url = upload_data.get("content_uri", "")
            else:
                mxc_url = att.url

            if not mxc_url:
                continue

            self._txn_counter += 1
            txn_id = f"{self._txn_counter}-{uuid4().hex[:8]}"
            att_url = (
                f"{self._homeserver_url}"
                f"/_matrix/client/v3/rooms/{room_id}"
                f"/send/m.room.message/{txn_id}"
            )
            if is_image:
                att_body = {
                    "msgtype": "m.image",
                    "body": att.filename or "image",
                    "url": mxc_url,
                }
            else:
                att_body = {
                    "msgtype": "m.file",
                    "body": att.filename or "file",
                    "url": mxc_url,
                }
            self._put_json(att_url, body=att_body)

    def edit(self, msg: OutboundMessage, original_message_id: str) -> None:
        # Param order must match Channel.edit (base.py) — the manager calls
        # ``ch.edit(msg, original_message_id)`` positionally. This was inverted.
        room_id, event_id = self._split_thread_id(original_message_id)
        url = f"{self._homeserver_url}/_matrix/client/v3/rooms/{room_id}/messages/{event_id}"
        body: dict[str, Any] = {
            "msgtype": "m.text",
            "body": msg.content,
        }
        self._put_json(url, body=body)

    def send_typing(self, thread_id: str) -> None:
        room_id, _ = self._split_thread_id(thread_id)
        user_id = self._get_user_id()
        url = f"{self._homeserver_url}/_matrix/client/v3/rooms/{room_id}/typing/{user_id}"
        self._put_json(url, body={"typing": True, "timeout": 30000})

    def add_reaction(self, thread_id: str, message_id: str, emoji: str) -> None:
        room_id, _ = self._split_thread_id(thread_id)
        self._txn_counter += 1
        txn_id = f"{self._txn_counter}-{uuid4().hex[:8]}"
        url = f"{self._homeserver_url}/_matrix/client/v3/rooms/{room_id}/send/m.reaction/{txn_id}"
        body: dict[str, Any] = {
            "m.relates_to": {
                "rel_type": "m.annotation",
                "event_id": message_id,
                "key": emoji,
            },
        }
        self._put_json(url, body=body)

    def health_check(self) -> bool:
        try:
            url = f"{self._homeserver_url}/_matrix/client/v3/account/whoami"
            headers = {
                "Authorization": f"Bearer {self._access_token}",
            }
            if self._http is not None:
                resp = self._http.get(url, headers=headers)
            else:
                if not hasattr(self, "_bare_client") or self._bare_client is None:
                    self._bare_client = httpx.Client(timeout=15.0)
                resp = self._bare_client.get(url, headers=headers)
            return resp.status_code == 200
        except Exception:
            return False

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

        if self._access_token:
            as_token = payload.get("as_token", "")
            if as_token != self._access_token:
                raise MatrixSignatureError("as_token mismatch")

        events = payload.get("events")
        if not isinstance(events, list):
            return None

        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") != "m.room.message":
                continue
            content = event.get("content")
            if not isinstance(content, dict):
                continue
            msgtype = content.get("msgtype", "m.text")
            body_text = content.get("body")
            if not isinstance(body_text, str) or not body_text.strip():
                continue
            sender = event.get("sender", "")
            if not isinstance(sender, str):
                sender = str(sender)
            room_id = event.get("room_id", "")
            if not isinstance(room_id, str) or not room_id:
                continue
            event_id = event.get("event_id", "")
            if not isinstance(event_id, str) or not event_id:
                continue

            attachments: list[Attachment] = []
            if msgtype == "m.image":
                url = content.get("url", "")
                if url:
                    attachments.append(
                        Attachment(
                            content_type="image",
                            url=url,
                            filename=body_text,
                        )
                    )
            elif msgtype == "m.file":
                url = content.get("url", "")
                if url:
                    attachments.append(
                        Attachment(
                            content_type="application/octet-stream",
                            url=url,
                            filename=body_text,
                        )
                    )
            elif msgtype != "m.text":
                continue

            return InboundMessage(
                channel_id=self.channel_id,
                thread_id=f"{room_id}:{event_id}",
                sender_id=sender,
                content=body_text,
                metadata={
                    "platform": "matrix",
                    "room_id": room_id,
                    "event_id": event_id,
                    "sender": sender,
                },
                received_at=_parse_matrix_ts(event.get("origin_server_ts")),
                attachments=attachments,
            )

        return None

    def _get_user_id(self) -> str:
        if self._user_id is not None:
            return self._user_id
        url = f"{self._homeserver_url}/_matrix/client/v3/account/whoami"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
        }
        if self._http is not None:
            resp = self._http.get(url, headers=headers)
        else:
            if not hasattr(self, "_bare_client") or self._bare_client is None:
                self._bare_client = httpx.Client(timeout=15.0)
            resp = self._bare_client.get(url, headers=headers)
        status = getattr(resp, "status_code", 200)
        if status >= 400:
            raise MatrixError(
                f"PUT {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise MatrixError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise MatrixError("response not an object")
        user_id = data.get("user_id")
        if not isinstance(user_id, str) or not user_id:
            raise MatrixError("no user_id in whoami response")
        self._user_id = user_id
        return user_id

    def _put_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._access_token}",
        }
        if self._http is not None:
            client = self._http
        else:
            if not hasattr(self, "_bare_client") or self._bare_client is None:
                self._bare_client = httpx.Client(timeout=15.0)
            client = self._bare_client
        for attempt in range(4):
            try:
                resp = client.put(url, json=body, headers=headers)
            except Exception as e:  # noqa: BLE001
                raise MatrixError(
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
            raise MatrixError(
                f"HTTP {status}: {getattr(resp, 'text', '')[:200]}",
            )
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise MatrixError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise MatrixError("response not an object")
        return data

    @staticmethod
    def _split_thread_id(thread_id: str) -> tuple[str, str]:
        if ":" in thread_id:
            rid, _, eid = thread_id.partition(":")
            return rid, eid
        return thread_id, ""


def _parse_matrix_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(raw) / 1000.0)
    except (ValueError, OverflowError, OSError):
        return None
