from __future__ import annotations

import base64
import contextlib
import json
import logging
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import Channel, InboundMessage, OutboundMessage, _sanitize_url

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════

DEFAULT_API_BASE = "https://ilinkai.weixin.qq.com"
ENDPOINT_QR_GET = "/ilink/bot/get_bot_qrcode"
ENDPOINT_QR_STATUS = "/ilink/bot/get_qrcode_status"
ENDPOINT_GET_UPDATES = "/ilink/bot/getupdates"
ENDPOINT_SEND_MESSAGE = "/ilink/bot/sendmessage"

CHANNEL_VERSION = "1.0.2"
LONGPOLL_TIMEOUT_MS = 35_000  # Implementation note.
QR_POLL_INTERVAL_S = 2.0
QR_POLL_TIMEOUT_S = 300.0  # Implementation note.
BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 30.0

ITEM_TYPE_TEXT = 1
MESSAGE_TYPE_TEXT = 2
MESSAGE_STATE_NORMAL = 2


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class WeixinBotError(RuntimeError):
    pass


class QRLoginTimeout(WeixinBotError):
    pass


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class WeixinBotChannel(Channel):
    channel_id: str = "weixin_bot"

    def __init__(
        self,
        *,
        api_base_url: str = DEFAULT_API_BASE,
        token_path: str | Path | None = None,
        qr_png_path: str | Path | None = None,
        channel_id: str = "weixin_bot",
        http_client: Any = None,
        bot_token: str | None = None,
        bot_type: int = 3,
        qr_poll_interval_s: float = QR_POLL_INTERVAL_S,
        qr_poll_timeout_s: float = QR_POLL_TIMEOUT_S,
        longpoll_request_timeout_s: float = 40.0,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.channel_id = channel_id
        self._token_path = Path(token_path).expanduser() if token_path else None
        self._qr_png_path = Path(qr_png_path).expanduser() if qr_png_path else None
        self._http = http_client
        self._bot_token: str | None = bot_token or self._load_token()
        self._bot_type = bot_type
        self._qr_poll_interval_s = qr_poll_interval_s
        self._qr_poll_timeout_s = qr_poll_timeout_s
        self._longpoll_timeout_s = longpoll_request_timeout_s
        self._uin_b64 = base64.b64encode(
            str(secrets.randbits(32)).encode("ascii"),
        ).decode("ascii")
        self._cursor: str = ""
        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.sent_log: list[OutboundMessage] = []

    def start(self) -> None:
        if self._bot_token is None:
            self._qr_login_flow()
        if self._bot_token is None:
            raise WeixinBotError("no bot_token after QR login")
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name=f"weixin-{self.channel_id}",
            daemon=True,
        )
        self._poll_thread.start()

    def stop(self) -> None:
        if hasattr(self, "_bare_client") and self._bare_client is not None:
            self._bare_client.close()
            self._bare_client = None
        self._stop_event.set()
        t = self._poll_thread
        if t is not None and t.is_alive():
            t.join(timeout=self._longpoll_timeout_s + 2.0)
        self._poll_thread = None

    def send(self, msg: OutboundMessage) -> None:
        self.sent_log.append(msg)

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

        if self._bot_token is None:
            raise WeixinBotError("not logged in")
        to_user = msg.metadata.get("to_user_id") or msg.thread_id
        context_token = msg.metadata.get("context_token", "")
        body = {
            "msg": {
                "to_user_id": to_user,
                "message_type": MESSAGE_TYPE_TEXT,
                "message_state": MESSAGE_STATE_NORMAL,
                "context_token": context_token,
                "item_list": [
                    {
                        "type": ITEM_TYPE_TEXT,
                        "text_item": {"text": content},
                    },
                ],
            },
        }
        self._request("POST", ENDPOINT_SEND_MESSAGE, body=body)

    def request_qr_code(self) -> dict[str, str]:
        data = self._request(
            "GET",
            ENDPOINT_QR_GET,
            params={"bot_type": self._bot_type},
            auth=False,
        )
        qrcode = data.get("qrcode")
        png_b64 = data.get("qrcode_img_content")
        if not isinstance(qrcode, str) or not isinstance(png_b64, str):
            raise WeixinBotError("bad get_bot_qrcode response shape")
        return {"qrcode": qrcode, "qrcode_img_content": png_b64}

    def poll_qr_status(self, qrcode: str) -> dict[str, Any]:
        status_resp = self._request(
            "GET",
            ENDPOINT_QR_STATUS,
            params={"qrcode": qrcode},
            auth=False,
            request_timeout=30.0,
        )
        if status_resp.get("status") == "confirmed":
            token = status_resp.get("bot_token")
            if isinstance(token, str) and token:
                self._bot_token = token
                self._save_token(token, status_resp.get("baseurl", ""))
        return dict(status_resp)

    def _qr_login_flow(self) -> None:
        logger.info("WeChat ClawBot: starting QR login flow")
        data = self._request(
            "GET",
            ENDPOINT_QR_GET,
            params={"bot_type": self._bot_type},
            auth=False,
        )
        qrcode = data.get("qrcode")
        png_b64 = data.get("qrcode_img_content")
        if not isinstance(qrcode, str) or not isinstance(png_b64, str):
            raise WeixinBotError("bad get_bot_qrcode response shape")

        self._present_qr(png_b64)

        deadline = time.monotonic() + self._qr_poll_timeout_s
        while time.monotonic() < deadline:
            status_resp = self._request(
                "GET",
                ENDPOINT_QR_STATUS,
                params={"qrcode": qrcode},
                auth=False,
            )
            status = status_resp.get("status")
            if status == "confirmed":
                token = status_resp.get("bot_token")
                if not isinstance(token, str) or not token:
                    raise WeixinBotError(
                        "confirmed but no bot_token in response",
                    )
                self._bot_token = token
                self._save_token(token, status_resp.get("baseurl", ""))
                logger.info("WeChat ClawBot: login confirmed")
                return
            if status in ("expired", "rejected"):
                raise WeixinBotError(f"QR login {status}")
            if self._stop_event.wait(self._qr_poll_interval_s):
                raise WeixinBotError("stop requested during QR login")
        raise QRLoginTimeout(
            f"QR login timeout after {self._qr_poll_timeout_s}s",
        )

    def _present_qr(self, png_b64: str) -> None:
        try:
            png_bytes = base64.b64decode(png_b64)
        except Exception as e:  # noqa: BLE001
            raise WeixinBotError(f"bad QR base64: {e}") from e
        if self._qr_png_path is None:
            self._qr_png_path = Path("./weixin_qr.png").resolve()
        self._qr_png_path.parent.mkdir(parents=True, exist_ok=True)
        self._qr_png_path.write_bytes(png_bytes)
        print(
            f"[weixin_bot] QR saved to {self._qr_png_path}\n"
            f"           · open it and scan with WeChat to login",
            flush=True,
        )

    def _load_token(self) -> str | None:
        if self._token_path is None or not self._token_path.exists():
            return None
        try:
            obj = json.loads(self._token_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        tok = obj.get("bot_token")
        return tok if isinstance(tok, str) and tok else None

    def _save_token(self, token: str, baseurl: str = "") -> None:
        if self._token_path is None:
            return
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(
            json.dumps(
                {"bot_token": token, "baseurl": baseurl},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _poll_loop(self) -> None:
        backoff = BACKOFF_MIN_S
        while not self._stop_event.is_set():
            try:
                resp = self._request(
                    "POST",
                    ENDPOINT_GET_UPDATES,
                    body={
                        "get_updates_buf": self._cursor,
                        "base_info": {"channel_version": CHANNEL_VERSION},
                    },
                    request_timeout=self._longpoll_timeout_s,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "weixin getupdates failed: %s · backoff %.1fs",
                    type(e).__name__,
                    backoff,
                )
                if self._stop_event.wait(backoff):
                    return
                backoff = min(backoff * 2, BACKOFF_MAX_S)
                continue
            backoff = BACKOFF_MIN_S

            if resp.get("ret", 0) != 0:
                logger.warning(
                    "weixin getupdates ret=%s · body=%s",
                    resp.get("ret"),
                    resp,
                )
                if self._stop_event.wait(BACKOFF_MIN_S):
                    return
                continue

            new_cursor = resp.get("get_updates_buf")
            if isinstance(new_cursor, str):
                self._cursor = new_cursor

            msgs = resp.get("msgs")
            if not isinstance(msgs, list):
                continue

            for raw in msgs:
                parsed = self._parse_inbound(raw)
                if parsed is None:
                    continue
                try:
                    self._dispatch(parsed)
                except Exception as e:  # noqa: BLE001
                    logger.exception(
                        "weixin dispatch failed for msg=%s: %s",
                        raw.get("msg_id") if isinstance(raw, dict) else "?",
                        e,
                    )

    def _parse_inbound(self, raw: Any) -> InboundMessage | None:
        if not isinstance(raw, dict):
            return None

        text = ""
        items = raw.get("item_list") or []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == ITEM_TYPE_TEXT:
                    ti = item.get("text_item") or {}
                    if isinstance(ti, dict):
                        t = ti.get("text")
                        if isinstance(t, str) and t:
                            text = t
                            break

        if not text:
            return None

        from_user = raw.get("from_user_id") or raw.get("sender_id") or ""
        if not isinstance(from_user, str):
            from_user = str(from_user)

        thread_id = raw.get("conversation_id") or raw.get("group_id") or from_user
        if not isinstance(thread_id, str) or not thread_id:
            return None

        context_token = raw.get("context_token") or ""
        msg_id = raw.get("msg_id") or raw.get("message_id") or ""
        ts_field = raw.get("create_time") or raw.get("timestamp")
        received_at: datetime | None = None
        if isinstance(ts_field, (int, float)):
            ts = ts_field / 1000.0 if ts_field > 1e12 else float(ts_field)
            try:
                received_at = datetime.fromtimestamp(ts)
            except (OverflowError, OSError, ValueError):
                received_at = None

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=thread_id,
            sender_id=from_user,
            content=text,
            metadata={
                "platform": "weixin_bot",
                "context_token": str(context_token),
                "to_user_id": from_user,  # Implementation note.
                "msg_id": str(msg_id),
                "raw": raw,
            },
            received_at=received_at,
        )

    def _headers(self, *, auth: bool = True) -> dict[str, str]:
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": self._uin_b64,
        }
        if auth:
            if not self._bot_token:
                raise WeixinBotError("missing bot_token for authed request")
            h["Authorization"] = f"Bearer {self._bot_token}"
        return h

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = True,
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self.api_base_url}{path}"
        headers = self._headers(auth=auth)

        if self._http is not None:
            client = self._http
        else:
            if not hasattr(self, "_bare_client") or self._bare_client is None:
                self._bare_client = httpx.Client(timeout=15.0)
            client = self._bare_client
        for attempt in range(4):
            try:
                if method == "GET":
                    resp = client.get(url, params=params, headers=headers)
                else:
                    resp = client.post(
                        url,
                        params=params,
                        json=body,
                        headers=headers,
                    )
            except Exception as e:
                raise WeixinBotError(
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
            if status == 401:
                self._bot_token = None
                if self._token_path and self._token_path.exists():
                    with contextlib.suppress(OSError):
                        self._token_path.unlink()
            raise WeixinBotError(
                f"HTTP {status} from {_sanitize_url(url)}: {getattr(resp, 'text', '')[:200]}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise WeixinBotError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise WeixinBotError("response not an object")
        return data
