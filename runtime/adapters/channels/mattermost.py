from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

from .base import Attachment, Channel, InboundMessage, OutboundMessage, _sanitize_url

logger = logging.getLogger(__name__)

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


class MattermostError(RuntimeError):
    pass


class MattermostSignatureError(ValueError):
    pass


class MattermostChannel(Channel):
    channel_id: str = "mattermost"
    supports_edit: bool = True
    supports_typing: bool = True
    supports_reactions: bool = True

    def __init__(
        self,
        *,
        bot_token: str,
        server_url: str,
        channel_id: str = "mattermost",
        http_client: Any = None,
    ) -> None:
        if not bot_token:
            raise ValueError("bot_token required")
        if not server_url:
            raise ValueError("server_url required")
        self._bot_token = bot_token
        self._server_url = server_url.rstrip("/")
        self.channel_id = channel_id
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

        channel_id, post_id = self._split_thread_id(msg.thread_id)
        file_ids: list[str] = []

        for att in msg.attachments:
            upload_url = f"{self._server_url}/api/v4/files"
            files = {
                "file": (
                    att.filename or "file",
                    att.data if isinstance(att.data, bytes) else att.data.encode(),
                    att.content_type,
                ),
            }
            form_data = {"channel_id": channel_id}
            headers = {"Authorization": f"Bearer {self._bot_token}"}
            if self._http is not None:
                upload_resp = self._http.post(
                    upload_url, headers=headers, files=files, data=form_data
                )
            else:
                if not hasattr(self, "_bare_client") or self._bare_client is None:
                    self._bare_client = httpx.Client(timeout=15.0)
                upload_resp = self._bare_client.post(
                    upload_url, headers=headers, files=files, data=form_data, timeout=30.0
                )
            upload_data = upload_resp.json()
            fid = upload_data.get("id", "")
            if fid:
                file_ids.append(fid)

        payload: dict[str, Any] = {
            "channel_id": channel_id,
            "message": content,
        }
        if post_id:
            payload["root_id"] = post_id
        if file_ids:
            payload["file_ids"] = file_ids

        url = f"{self._server_url}/api/v4/posts"
        self._post_json(
            url,
            body=payload,
            authorization=f"Bearer {self._bot_token}",
        )

    def edit(self, msg: OutboundMessage, original_message_id: str) -> None:
        # Param order must match Channel.edit (base.py) — the manager calls
        # ``ch.edit(msg, original_message_id)`` positionally. This was inverted.
        url = f"{self._server_url}/api/v4/posts/{original_message_id}"
        body: dict[str, Any] = {
            "message": msg.content,
        }
        self._put_json(
            url,
            body=body,
            authorization=f"Bearer {self._bot_token}",
        )

    def send_typing(self, thread_id: str) -> None:
        return

    def add_reaction(self, thread_id: str, message_id: str, emoji: str) -> None:
        url = f"{self._server_url}/api/v4/reactions"
        body: dict[str, Any] = {
            "user_id": "me",
            "post_id": message_id,
            "emoji_name": emoji,
        }
        self._post_json(
            url,
            body=body,
            authorization=f"Bearer {self._bot_token}",
        )

    def health_check(self) -> bool:
        try:
            url = f"{self._server_url}/api/v4/users/me"
            headers = {"Authorization": f"Bearer {self._bot_token}"}
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

    # ─── HTTP ────────────────────────────────

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        authorization: str,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": authorization,
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
                raise MattermostError(
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
            raise MattermostError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise MattermostError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise MattermostError("response not an object")
        return data

    def _put_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        authorization: str,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": authorization,
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
                raise MattermostError(
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
            raise MattermostError(
                f"PUT {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise MattermostError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise MattermostError("response not an object")
        return data

    # ─── Webhook ────────────────────────────────

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

        token = payload.get("token") or ""
        if token != self._bot_token:
            raise MattermostSignatureError("token mismatch")

        text = payload.get("text")
        if (not isinstance(text, str) or not text.strip()) and not payload.get("file_ids"):
            return None

        attachments: list[Attachment] = []
        file_ids = payload.get("file_ids")
        if isinstance(file_ids, list):
            for fid in file_ids:
                if isinstance(fid, str) and fid:
                    attachments.append(
                        Attachment(
                            content_type="application/octet-stream",
                            metadata={"file_id": fid},
                        )
                    )

        user_name = payload.get("user_name") or ""
        user_id = payload.get("user_id") or ""
        channel_name = payload.get("channel_name") or ""
        channel_id = payload.get("channel_id") or ""
        post_id = payload.get("post_id") or ""
        timestamp = payload.get("timestamp")

        if not channel_id:
            return None

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"{channel_id}:{post_id}",
            sender_id=str(user_id),
            content=text if isinstance(text, str) else "",
            metadata={
                "platform": "mattermost",
                "mattermost_channel_id": channel_id,
                "mattermost_channel_name": channel_name,
                "mattermost_user_id": user_id,
                "mattermost_user_name": user_name,
                "mattermost_post_id": post_id,
            },
            received_at=_parse_mattermost_ts(timestamp),
            attachments=attachments,
        )

    @staticmethod
    def _split_thread_id(thread_id: str) -> tuple[str, str]:
        if ":" in thread_id:
            cid, _, pid = thread_id.partition(":")
            return cid, pid
        return thread_id, ""


def _parse_mattermost_ts(raw: Any) -> datetime | None:
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw) / 1000.0)
        except (ValueError, OverflowError, OSError):
            return None
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromtimestamp(float(raw) / 1000.0)
        except (ValueError, OverflowError, OSError):
            return None
