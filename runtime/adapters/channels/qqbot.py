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

DEFAULT_API_BASE = "https://api.sgroup.qq.com"


class QQBotError(RuntimeError):
    pass


class QQBotSignatureError(ValueError):
    pass


class QQBotChannel(Channel):
    channel_id: str = "qqbot"
    supports_edit: bool = True

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        channel_id_param: str = "qqbot",
        api_base_url: str = DEFAULT_API_BASE,
        http_client: Any = None,
    ) -> None:
        if not app_id:
            raise ValueError("app_id required")
        if not app_secret:
            raise ValueError("app_secret required")
        self._app_id = app_id
        self._app_secret = app_secret
        self.channel_id = channel_id_param
        self.api_base_url = api_base_url.rstrip("/")
        self._http = http_client
        self.send_log: list[OutboundMessage] = []
        self._access_token: str = ""
        self._token_expires_at: float = 0.0

    def _ensure_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        url = f"{self.api_base_url}/app/getAppAccessToken"
        body = {"appId": self._app_id, "appSecret": self._app_secret}
        resp_data = self._post_json(url, body=body)
        access_token = resp_data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise QQBotError(
                f"failed to obtain access_token: {str(resp_data)[:200]}",
            )
        expires_in = resp_data.get("expires_in", 7200)
        try:
            expires_in = int(expires_in)
        except (ValueError, TypeError):
            expires_in = 7200
        self._access_token = access_token
        self._token_expires_at = time.time() + expires_in - 60
        return self._access_token

    def health_check(self) -> bool:
        try:
            token = self._ensure_token()
            return bool(token)
        except Exception:
            return False

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

        channel_id = msg.metadata.get("qq_channel_id") or msg.thread_id
        if not channel_id:
            raise QQBotError("missing qq channel_id for send")
        msg_id = msg.metadata.get("message_id", "")
        access_token = self._ensure_token()
        url = f"{self.api_base_url}/channels/{channel_id}/messages"

        if msg.content:
            body: dict[str, Any] = {
                "content": content,
                "msg_type": 0,
                "msg_id": msg_id,
            }
            resp_data = self._post_json(
                url,
                body=body,
                authorization=f"QQBot {access_token}",
            )
            if "id" not in resp_data:
                raise QQBotError(
                    f"unexpected send response shape: {str(resp_data)[:200]}",
                )

        for att in msg.attachments:
            is_image = att.content_type.startswith("image/")
            if is_image:
                att_body = {
                    "content": "",
                    "msg_type": 2,
                    "msg_id": msg_id,
                    "media": {
                        "file_info": att.url or "",
                    },
                }
            else:
                att_body = {
                    "content": "",
                    "msg_type": 3,
                    "msg_id": msg_id,
                    "attachment": {
                        "file_info": att.url or "",
                    },
                }
            att_resp = self._post_json(
                url,
                body=att_body,
                authorization=f"QQBot {access_token}",
            )
            if "id" not in att_resp:
                raise QQBotError(
                    f"unexpected attachment send response shape: {str(att_resp)[:200]}",
                )

    def edit(self, msg: OutboundMessage, original_message_id: str) -> None:
        channel_id = msg.thread_id.split(":")[0] if msg.thread_id else ""
        if not channel_id:
            raise QQBotError("missing qq channel_id for edit")
        access_token = self._ensure_token()
        url = f"{self.api_base_url}/channels/{channel_id}/messages/{original_message_id}"
        body: dict[str, Any] = {
            "content": msg.content,
            "msg_type": 0,
        }
        resp_data = self._patch_json(
            url,
            body=body,
            authorization=f"QQBot {access_token}",
        )
        if "id" not in resp_data:
            raise QQBotError(
                f"unexpected edit response shape: {str(resp_data)[:200]}",
            )

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        authorization: str = "",
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
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
                raise QQBotError(
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
            raise QQBotError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise QQBotError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise QQBotError("response not an object")
        return data

    def _patch_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        authorization: str = "",
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        if self._http is not None:
            client = self._http
        else:
            if not hasattr(self, "_bare_client") or self._bare_client is None:
                self._bare_client = httpx.Client(timeout=15.0)
            client = self._bare_client
        for attempt in range(4):
            try:
                resp = client.patch(url, json=body, headers=headers)
            except Exception as e:  # noqa: BLE001
                raise QQBotError(
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
            raise QQBotError(
                f"PATCH {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise QQBotError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise QQBotError("response not an object")
        return data

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        lowered = {k.lower(): v for k, v in headers.items()}
        sig = lowered.get("x-signature", "")
        ts = lowered.get("x-timestamp", "")
        if sig and ts:
            pass

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"bad json body: {e}") from e
        if not isinstance(payload, dict):
            raise ValueError("payload not an object")

        opcode = payload.get("op")
        if opcode == 13:
            return {"op": 0, "d": {}}

        d = payload.get("d")
        if not isinstance(d, dict):
            return None

        content = d.get("content")
        if (not isinstance(content, str) or not content.strip()) and not d.get("attachments"):
            return None

        attachments: list[Attachment] = []
        raw_attachments = d.get("attachments")
        if isinstance(raw_attachments, list):
            for raw_att in raw_attachments:
                if not isinstance(raw_att, dict):
                    continue
                att_url = raw_att.get("url", "")
                att_content_type = raw_att.get("content_type", "")
                att_filename = raw_att.get("filename", "")
                if att_url:
                    attachments.append(
                        Attachment(
                            content_type=att_content_type or "application/octet-stream",
                            url=att_url,
                            filename=att_filename,
                            metadata=raw_att,
                        )
                    )

        author = d.get("author") or {}
        if not isinstance(author, dict):
            return None
        sender_id = str(author.get("user_openid", ""))

        message_id = str(d.get("id", ""))
        qq_channel_id = str(d.get("channel_id", ""))

        if not qq_channel_id:
            return None

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"{qq_channel_id}:{message_id}",
            sender_id=sender_id,
            content=content if isinstance(content, str) else "",
            metadata={
                "platform": "qqbot",
                "qq_channel_id": qq_channel_id,
                "message_id": message_id,
                "guild_id": d.get("guild_id", ""),
            },
            received_at=_parse_qqbot_ts(d.get("timestamp")),
            attachments=attachments,
        )


def _parse_qqbot_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
