from __future__ import annotations

import json
import logging
import threading
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

DEFAULT_API_BASE = "https://open.feishu.cn/open-apis"
TOKEN_REFRESH_MARGIN_SECONDS = 120  # Implementation note.


class FeishuError(RuntimeError):
    pass


class FeishuSignatureError(ValueError):
    pass


class FeishuChannel(Channel):
    channel_id: str = "feishu"
    supports_edit: bool = True
    supports_typing: bool = False
    supports_reactions: bool = True

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        verification_token: str,
        channel_id: str = "feishu",
        api_base_url: str = DEFAULT_API_BASE,
        http_client: Any = None,
    ) -> None:
        if not app_id:
            raise ValueError("app_id required")
        if not app_secret:
            raise ValueError("app_secret required")
        if not verification_token:
            raise ValueError("verification_token required")
        self._app_id = app_id
        self._app_secret = app_secret
        self._verification_token = verification_token
        self.channel_id = channel_id
        self.api_base_url = api_base_url.rstrip("/")
        self._http = http_client

        self._tat: str | None = None
        self._tat_expires_at: float = 0.0
        self._tat_lock = threading.Lock()

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

        token = self._get_tenant_access_token()
        receive_id_type = msg.metadata.get("receive_id_type", "chat_id")
        receive_id = msg.metadata.get("receive_id") or msg.metadata.get("chat_id") or msg.thread_id
        if not receive_id:
            raise FeishuError("missing receive_id for send")

        url = f"{self.api_base_url}/im/v1/messages?receive_id_type={receive_id_type}"

        if content:
            body = {
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": content}, ensure_ascii=False),
            }
            resp = self._post_json(url, body=body, bearer=token)
            if resp.get("code", 0) != 0:
                raise FeishuError(
                    f"feishu send failed: code={resp.get('code')} msg={resp.get('msg', '')[:200]}",
                )

        for att in msg.attachments:
            is_image = att.content_type.startswith("image/")
            if is_image:
                upload_url = f"{self.api_base_url}/im/v1/images"
                form_fields = {
                    "image_type": (None, "message"),
                    "image": (
                        att.filename or "image",
                        att.data if isinstance(att.data, bytes) else att.data.encode(),
                        att.content_type,
                    ),
                }
            else:
                upload_url = f"{self.api_base_url}/im/v1/files"
                form_fields = {
                    "file_type": (None, "stream"),
                    "file_name": (None, att.filename or "file"),
                    "file": (
                        att.filename or "file",
                        att.data if isinstance(att.data, bytes) else att.data.encode(),
                        att.content_type,
                    ),
                }
            headers = {"Authorization": f"Bearer {token}"}
            if self._http is not None:
                upload_resp = self._http.post(upload_url, headers=headers, files=form_fields)
            else:
                if not hasattr(self, "_bare_client") or self._bare_client is None:
                    self._bare_client = httpx.Client(timeout=15.0)
                upload_resp = self._bare_client.post(
                    upload_url, headers=headers, files=form_fields, timeout=30.0
                )
            upload_data = upload_resp.json()
            if upload_data.get("code", 0) != 0:
                raise FeishuError(f"feishu upload failed: {upload_data.get('msg', '')[:200]}")
            if is_image:
                key = upload_data.get("data", {}).get("image_key", "")
                att_body = {
                    "receive_id": receive_id,
                    "msg_type": "image",
                    "content": json.dumps({"image_key": key}),
                }
            else:
                key = upload_data.get("data", {}).get("file_key", "")
                att_body = {
                    "receive_id": receive_id,
                    "msg_type": "file",
                    "content": json.dumps({"file_key": key}),
                }
            att_resp = self._post_json(url, body=att_body, bearer=token)
            if att_resp.get("code", 0) != 0:
                raise FeishuError(
                    f"feishu send attachment failed: code={att_resp.get('code')} "
                    f"msg={att_resp.get('msg', '')[:200]}",
                )

    def edit(self, msg: OutboundMessage, original_message_id: str) -> None:
        # Param order must match Channel.edit (base.py) — the manager calls
        # ``ch.edit(msg, original_message_id)`` positionally. This was inverted.
        token = self._get_tenant_access_token()
        url = f"{self.api_base_url}/im/v1/messages/{original_message_id}"
        body = {
            "content": json.dumps({"text": msg.content}, ensure_ascii=False),
        }
        resp = self._patch_json(url, body=body, bearer=token)
        if resp.get("code", 0) != 0:
            raise FeishuError(
                f"feishu edit failed: code={resp.get('code')} msg={resp.get('msg', '')[:200]}",
            )

    def add_reaction(self, thread_id: str, message_id: str, emoji: str) -> None:
        token = self._get_tenant_access_token()
        url = f"{self.api_base_url}/im/v1/messages/{message_id}/reactions"
        body: dict[str, Any] = {"reaction_type": "emoji", "emoji": emoji}
        resp = self._post_json(url, body=body, bearer=token)
        if resp.get("code", 0) != 0:
            raise FeishuError(
                f"feishu add_reaction failed: code={resp.get('code')} "
                f"msg={resp.get('msg', '')[:200]}",
            )

    def health_check(self) -> bool:
        try:
            self._get_tenant_access_token()
            return True
        except Exception:
            return False

    def _get_tenant_access_token(self) -> str:
        now = time.time()
        with self._tat_lock:
            if self._tat and self._tat_expires_at > now + TOKEN_REFRESH_MARGIN_SECONDS:
                return self._tat
            # refresh
            url = f"{self.api_base_url}/auth/v3/tenant_access_token/internal"
            body = {"app_id": self._app_id, "app_secret": self._app_secret}
            resp = self._post_json(url, body=body, bearer=None)
            if resp.get("code", 0) != 0:
                raise FeishuError(
                    f"tenant_access_token refresh failed: "
                    f"code={resp.get('code')} msg={resp.get('msg')}",
                )
            tat = resp.get("tenant_access_token")
            expire_in = int(resp.get("expire", 7200))
            if not isinstance(tat, str) or not tat:
                raise FeishuError("no tenant_access_token in response")
            self._tat = tat
            self._tat_expires_at = now + expire_in
            return tat

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        bearer: str | None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"

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
                raise FeishuError(
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
            raise FeishuError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise FeishuError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise FeishuError("response not an object")
        return data

    def _patch_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        bearer: str | None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"

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
                raise FeishuError(
                    f"network: {type(e).__name__}: {e}",
                ) from e
            if resp.status_code < 500 and resp.status_code != 429:
                break
            if attempt == 3:
                break
            time.sleep(2**attempt)

        status = getattr(resp, "status_code", 200)
        if status >= 400:
            raise FeishuError(
                f"PATCH {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise FeishuError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise FeishuError("response not an object")
        return data

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

        if payload.get("type") == "url_verification":
            token = payload.get("token")
            if token != self._verification_token:
                raise FeishuSignatureError(
                    "verification_token mismatch on url_verification",
                )
            challenge = payload.get("challenge", "")
            return {"challenge": str(challenge)}

        header = payload.get("header")
        event = payload.get("event")
        if not isinstance(header, dict):
            raise ValueError("missing event header")

        recv_token = header.get("token")
        if recv_token != self._verification_token:
            raise FeishuSignatureError(
                "verification_token mismatch on event",
            )

        if not isinstance(event, dict):
            return None  # Implementation note.

        event_type = header.get("event_type", "")
        if event_type != "im.message.receive_v1":
            return None

        return self._parse_message_event(event)

    def _parse_message_event(
        self,
        event: dict[str, Any],
    ) -> InboundMessage | None:
        msg = event.get("message") or {}
        sender = event.get("sender") or {}
        if not isinstance(msg, dict):
            return None

        msg_type = msg.get("message_type", "text")
        content_raw = msg.get("content", "")
        if not isinstance(content_raw, str):
            return None
        try:
            content_obj = json.loads(content_raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(content_obj, dict):
            return None

        text = ""
        attachments: list[Attachment] = []
        if msg_type == "text":
            text = content_obj.get("text", "")
            if not isinstance(text, str) or not text.strip():
                return None
        elif msg_type == "image":
            image_key = content_obj.get("image_key", "")
            if image_key:
                attachments.append(
                    Attachment(
                        content_type="image",
                        metadata={"image_key": image_key},
                    )
                )
            text = content_obj.get("text", "")
        elif msg_type == "file":
            file_key = content_obj.get("file_key", "")
            file_name = content_obj.get("file_name", "")
            if file_key:
                attachments.append(
                    Attachment(
                        content_type="application/octet-stream",
                        filename=file_name,
                        metadata={"file_key": file_key},
                    )
                )
            text = content_obj.get("text", "")
        else:
            return None

        chat_id = msg.get("chat_id", "")
        message_id = msg.get("message_id", "")
        root_id = msg.get("root_id") or message_id
        sender_info = sender.get("sender_id", {}) or {}
        sender_open_id = sender_info.get("open_id") or sender_info.get("user_id") or ""

        if sender.get("sender_type") == "app":
            return None

        if not isinstance(chat_id, str) or not chat_id:
            return None

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"{chat_id}:{root_id}",
            sender_id=str(sender_open_id),
            content=text,
            metadata={
                "platform": "feishu",
                "chat_id": chat_id,
                "message_id": message_id,
                "root_id": root_id,
                "chat_type": msg.get("chat_type", ""),
                "receive_id": chat_id,
                "receive_id_type": "chat_id",
            },
            received_at=_parse_feishu_ts(msg.get("create_time")),
            attachments=attachments,
        )


def _parse_feishu_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, (int, str)):
        return None
    try:
        ms = int(raw)
        return datetime.fromtimestamp(ms / 1000.0)
    except (ValueError, OverflowError, OSError):
        return None
