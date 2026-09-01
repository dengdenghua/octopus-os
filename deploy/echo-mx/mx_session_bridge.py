"""Isolated, server-session-backed same-origin bridge for the MX web client.

This service is intentionally separate from the Echo account API.  It binds to
loopback, keeps the real MX token in a mode-0600 server file, gives the browser
only a non-secret placeholder token, and injects the real token while proxying
approved MX traffic.  Running third-party MX JavaScript on this origin can
therefore never grant it same-origin access to Echo account, billing, or admin
routes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

try:
    import websockets
except ImportError:  # pragma: no cover - uvicorn[standard] installs this in production
    websockets = None  # type: ignore[assignment]


LOGGER = logging.getLogger("echo.mx_bridge")
UPSTREAM = os.environ.get("MX_UPSTREAM_URL", "https://mx2025.hhhuu.com").rstrip("/")
SOCKET_UPSTREAM = os.environ.get("MX_SOCKET_UPSTREAM_URL", "https://boke.52lvin.cn").rstrip("/")
SESSION_FILE = Path(
    os.environ.get("MX_SESSION_FILE", "/var/lib/echo-mx/session.json")
).expanduser()
SESSION_STATE_FILE = Path(
    os.environ.get("MX_SESSION_STATE_FILE", "/var/lib/echo-mx/session-state.json")
).expanduser()
GROUP_FILE = Path(
    os.environ.get("MX_GROUP_FILE", "/var/lib/echo-mx/groups.json")
).expanduser()
MESSAGE_FILE = Path(
    os.environ.get("MX_MESSAGE_FILE", "/var/lib/echo-mx/messages.sqlite3")
).expanduser()
VIEWER_FILE = Path(
    os.environ.get("MX_VIEWER_FILE", "/opt/echo-mx/mx_viewer.html")
).expanduser()
try:
    INACTIVE_DAYS = min(max(int(os.environ.get("MX_INACTIVE_DAYS", "7")), 1), 365)
except ValueError:
    INACTIVE_DAYS = 7
DUMMY_TOKEN = "echo-server-session"
MAX_BODY = 1024 * 1024
ALLOWED_ROOTS = frozenset(
    {
        "assets",
        "static",
        "api",
        "img",
        "uni",
        "pages",
        "3",
        "5",
        "msg",
        "socket.io",
    }
)
ALLOWED_ROOT_FILES = frozenset({"favicon.ico", "index.html", "manifest.json"})
BLOCKED_API_SUFFIXES = (
    "/api/login",
    "/api/register",
    "/api/logout",
    "/api/user/update",
    "/api/room/quit",
)
REQUEST_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "content-type",
        "if-modified-since",
        "if-none-match",
        "range",
        "user-agent",
        "ad",
        "version",
        "i",
    }
)
RESPONSE_HEADERS = frozenset(
    {
        "accept-ranges",
        "cache-control",
        "content-encoding",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "last-modified",
    }
)


def _validated_frame_ancestor(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("MX frame ancestor must be a plain HTTP(S) origin")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


FRAME_ANCESTOR = _validated_frame_ancestor(
    os.environ.get("MX_FRAME_ANCESTOR", "http://127.0.0.1:8000")
)
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self' data:; connect-src 'self'; media-src 'self'; "
        "worker-src 'self' blob:; object-src 'none'; base-uri 'self'; "
        f"form-action 'self'; frame-ancestors 'self' {FRAME_ANCESTOR}"
    ),
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _validated_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("MX upstream must be a plain HTTPS origin")
    return urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))


UPSTREAM = _validated_origin(UPSTREAM)
SOCKET_UPSTREAM = _validated_origin(SOCKET_UPSTREAM)


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        try:
            mode = stat.S_IMODE(self.path.stat().st_mode)
            if mode & 0o077:
                raise RuntimeError("MX session file permissions must be 0600 or stricter")
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RuntimeError("MX session has not been provisioned") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("MX session file is invalid") from exc
        token = str(payload.get("token") or "").strip()
        hosturl = str(payload.get("hosturl") or "/5").strip()
        if len(token) < 16 or hosturl not in {"/3", "/5"}:
            raise RuntimeError("MX session is incomplete")
        return {"token": token, "hosturl": hosturl, "logged_in_at": payload.get("logged_in_at")}


SESSION = SessionStore(SESSION_FILE)


class SessionStateStore:
    """Read the guardian's secret-free state without trusting arbitrary JSON."""

    _STATES = frozenset(
        {
            "unknown",
            "healthy",
            "restoring",
            "waiting_retry",
            "login_required",
            "captcha_failed",
            "credentials_rejected",
            "login_failed",
            "upstream_unavailable",
        }
    )

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        fallback = {
            "state": "unknown",
            "authenticated": False,
            "checked_at": None,
            "last_success_at": None,
            "last_login_attempt_at": None,
            "failure_count": 0,
            "next_retry_at": None,
        }
        try:
            mode = stat.S_IMODE(self.path.stat().st_mode)
            if mode & 0o077:
                return fallback
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return fallback
        if not isinstance(payload, dict):
            return fallback
        state = str(payload.get("state") or "unknown")
        if state not in self._STATES:
            state = "unknown"
        result = dict(fallback)
        result.update(
            {
                "state": state,
                "authenticated": bool(payload.get("authenticated")),
                "checked_at": payload.get("checked_at"),
                "last_success_at": payload.get("last_success_at"),
                "last_login_attempt_at": payload.get("last_login_attempt_at"),
                "failure_count": max(0, int(payload.get("failure_count") or 0)),
                "next_retry_at": payload.get("next_retry_at"),
            }
        )
        return result


SESSION_STATE = SessionStateStore(SESSION_STATE_FILE)

MAX_GROUPS = 30
MAX_GROUP_NAME = 24
MAX_ASSIGNMENTS = 5000
_GROUP_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


class ConversationGroupStore:
    """Persist viewer-only grouping without exposing any Echo control APIs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._groups: list[dict[str, str]] = []
        self._assignments: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            if not self.path.exists():
                return
            mode = stat.S_IMODE(self.path.stat().st_mode)
            if mode & 0o077:
                raise RuntimeError("MX group file permissions must be 0600 or stricter")
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            groups = payload.get("groups") if isinstance(payload, dict) else []
            assignments = payload.get("assignments") if isinstance(payload, dict) else {}
            if isinstance(groups, list):
                for item in groups[:MAX_GROUPS]:
                    if not isinstance(item, dict):
                        continue
                    group_id = str(item.get("id") or "")
                    name = str(item.get("name") or "").strip()
                    if _GROUP_ID_RE.fullmatch(group_id) and name:
                        self._groups.append(
                            {"id": group_id, "name": name[:MAX_GROUP_NAME]}
                        )
            valid_ids = {item["id"] for item in self._groups}
            if isinstance(assignments, dict):
                self._assignments = {
                    str(room_id): str(group_id)
                    for room_id, group_id in list(assignments.items())[:MAX_ASSIGNMENTS]
                    if _GROUP_ID_RE.fullmatch(str(room_id))
                    and str(group_id) in valid_ids
                }
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            LOGGER.warning("MX groups could not be loaded (%s)", type(exc).__name__)
            self._groups = []
            self._assignments = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"groups": self._groups, "assignments": self._assignments},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.path)

    @staticmethod
    def _name(value: Any) -> str:
        return str(value or "").strip()[:MAX_GROUP_NAME]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "groups": [dict(item) for item in self._groups],
                "assignments": dict(self._assignments),
            }

    def create(self, name: Any) -> dict[str, str]:
        clean_name = self._name(name)
        if not clean_name:
            raise ValueError("分组名不能为空")
        with self._lock:
            if len(self._groups) >= MAX_GROUPS:
                raise ValueError(f"最多创建 {MAX_GROUPS} 个分组")
            group = {"id": uuid.uuid4().hex[:12], "name": clean_name}
            self._groups.append(group)
            self._save()
            return dict(group)

    def rename(self, group_id: str, name: Any) -> dict[str, str] | None:
        clean_name = self._name(name)
        if not clean_name:
            raise ValueError("分组名不能为空")
        with self._lock:
            group = next(
                (item for item in self._groups if item["id"] == group_id), None
            )
            if group is None:
                return None
            group["name"] = clean_name
            self._save()
            return dict(group)

    def delete(self, group_id: str) -> bool:
        with self._lock:
            original_length = len(self._groups)
            self._groups = [item for item in self._groups if item["id"] != group_id]
            if len(self._groups) == original_length:
                return False
            self._assignments = {
                room_id: assigned
                for room_id, assigned in self._assignments.items()
                if assigned != group_id
            }
            self._save()
            return True

    def assign(self, room_id: Any, group_id: Any) -> dict[str, Any]:
        clean_room_id = str(room_id or "").strip()
        if not _GROUP_ID_RE.fullmatch(clean_room_id):
            raise ValueError("对话 ID 无效")
        with self._lock:
            clean_group_id = str(group_id or "").strip()
            if clean_group_id:
                if not any(item["id"] == clean_group_id for item in self._groups):
                    raise KeyError("分组不存在")
                if (
                    clean_room_id not in self._assignments
                    and len(self._assignments) >= MAX_ASSIGNMENTS
                ):
                    raise ValueError(f"最多保存 {MAX_ASSIGNMENTS} 条分组关系")
                self._assignments[clean_room_id] = clean_group_id
            else:
                self._assignments.pop(clean_room_id, None)
            self._save()
            return {"room_id": clean_room_id, "group_id": clean_group_id or None}


class GroupPayload(BaseModel):
    name: str | None = None
    group_id: str | None = None


class CaptureMessage(BaseModel):
    source: str = Field(default="mx2025", pattern=r"^mx2025$")
    source_room_id: str = Field(min_length=1, max_length=128)
    source_message_id: str = Field(min_length=1, max_length=160)
    title: str = Field(default="", max_length=240)
    content: str = Field(min_length=1, max_length=50_000)
    published_at: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class CaptureBatch(BaseModel):
    messages: list[CaptureMessage] = Field(min_length=1, max_length=100)


class MessageCache:
    """Persist normalized Viewer evidence without ever storing the MX token."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self._initialize()
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.path.parent.chmod(0o700)
            connection = sqlite3.connect(self.path, timeout=10)
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mx_captured_messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_room_id TEXT NOT NULL,
                    source_message_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    published_at TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    captured_at INTEGER NOT NULL,
                    UNIQUE(source_room_id, source_message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_mx_captured_seq
                    ON mx_captured_messages(seq DESC);
                CREATE INDEX IF NOT EXISTS idx_mx_captured_room_seq
                    ON mx_captured_messages(source_room_id, seq DESC);
                """
            )
            connection.commit()
            connection.close()
            self.path.chmod(0o600)
            self._initialized = True

    @staticmethod
    def _normalized(message: CaptureMessage) -> dict[str, Any]:
        kind = str(message.payload.get("kind") or "unknown")[:64]
        return {
            "source_room_id": message.source_room_id.strip(),
            "source_message_id": message.source_message_id.strip(),
            "title": message.title.strip(),
            "content": message.content.strip(),
            "published_at": message.published_at,
            "payload_json": json.dumps({"kind": kind}, ensure_ascii=False),
        }

    def capture(self, messages: list[CaptureMessage]) -> dict[str, int]:
        now = int(time.time() * 1000)
        accepted = 0
        duplicate = 0
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for message in messages:
                item = self._normalized(message)
                if not item["source_room_id"] or not item["source_message_id"] or not item["content"]:
                    continue
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO mx_captured_messages
                    (source_room_id, source_message_id, title, content, published_at, payload_json, captured_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item["source_room_id"],
                        item["source_message_id"],
                        item["title"],
                        item["content"],
                        item["published_at"],
                        item["payload_json"],
                        now,
                    ),
                )
                if cursor.rowcount:
                    accepted += 1
                else:
                    duplicate += 1
            connection.commit()
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) AS latest_seq FROM mx_captured_messages"
            ).fetchone()
        return {
            "accepted": accepted,
            "duplicate": duplicate,
            "latest_seq": int(row["latest_seq"] if row else 0),
        }

    def messages(self, *, after_seq: int = 0, limit: int = 500) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 1000))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT seq, source_room_id, source_message_id, title, content,
                published_at, payload_json, captured_at
                FROM mx_captured_messages WHERE seq>? ORDER BY seq ASC LIMIT ?""",
                (max(0, int(after_seq)), safe_limit),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(str(item.pop("payload_json") or "{}"))
            except json.JSONDecodeError:
                item["payload"] = {}
            items.append(item)
        return {
            "messages": items,
            "latest_seq": items[-1]["seq"] if items else max(0, after_seq),
        }

    def status(self) -> dict[str, int | None]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS message_count, COALESCE(MAX(seq), 0) AS latest_seq,
                MAX(captured_at) AS last_capture_at FROM mx_captured_messages"""
            ).fetchone()
        return {
            "message_count": int(row["message_count"] if row else 0),
            "latest_seq": int(row["latest_seq"] if row else 0),
            "last_capture_at": int(row["last_capture_at"])
            if row and row["last_capture_at"]
            else None,
        }


GROUPS = ConversationGroupStore(GROUP_FILE)
MESSAGES = MessageCache(MESSAGE_FILE)


def _safe_path(raw: str) -> str | None:
    decoded = str(raw or "")
    for _ in range(6):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    else:
        return None
    clean = decoded.strip().lstrip("/")
    if not clean:
        return ""
    trailing_slash = clean.endswith("/")
    if trailing_slash:
        clean = clean.rstrip("/")
        if not clean:
            return ""
    if "\\" in clean or "?" in clean or "#" in clean or any(ord(ch) < 32 for ch in clean):
        return None
    segments = clean.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        return None
    if len(segments) == 1 and segments[0] in ALLOWED_ROOT_FILES:
        return clean + ("/" if trailing_slash else "")
    return clean + ("/" if trailing_slash else "") if segments[0] in ALLOWED_ROOTS else None


async def _bounded_body(request: Request) -> bytes:
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise HTTPException(400, "invalid content-length") from exc
        if length < 0 or length > MAX_BODY:
            raise HTTPException(413, "request body too large")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise HTTPException(413, "request body too large")
    return bytes(body)


def _request_headers(request: Request, target_origin: str, *, token: str | None) -> dict[str, str]:
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in REQUEST_HEADERS
    }
    parsed = urlsplit(target_origin)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    headers["Origin"] = origin
    headers["Referer"] = origin + "/"
    if token:
        headers["token"] = token
        headers.setdefault("AD", "true")
        headers.setdefault("version", "4.2.3")
        headers.setdefault("i", "qq")
    return headers


def _response_headers(response: httpx.Response) -> dict[str, str]:
    headers = {
        name: value
        for name, value in response.headers.items()
        if name.lower() in RESPONSE_HEADERS
    }
    headers.update(SECURITY_HEADERS)
    return headers


def _render_viewer(template: str, *, inactive_days: int = INACTIVE_DAYS) -> str:
    replacements = {
        "__MX_INACTIVE_DAYS__": str(inactive_days),
        "__MX_GROUP_API__": "/echo",
        "__MX_VIEWER_MODE__": "bridge",
        "__MX_OPEN_URL__": "/viewer",
        "__MX_PROXY_PATH__": "/",
        "__MX_FRAME_SRC__": "/?echo_viewer=1#/",
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def _bootstrap_html(body: bytes, hosturl: str) -> bytes:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    bootstrap = (
        "<script>(function(){try{"
        f"localStorage.setItem('token','{DUMMY_TOKEN}');"
        f"localStorage.setItem('hosturl','{hosturl}');"
        "}catch(_e){}})();</script>"
    )
    marker = "</head>"
    return text.replace(marker, bootstrap + marker, 1).encode("utf-8")


def _rewrite_javascript(body: bytes) -> bytes:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    # The upstream bundle special-cases shard /5 by sending Socket.IO to a
    # second host.  Keep it on this isolated origin so the WebSocket bridge can
    # replace the public placeholder with the server-side token.
    return text.replace("//boke.52lvin.cn", "").encode("utf-8")


def _inject_socket_token(payload: str | bytes, token: str) -> str | bytes:
    if isinstance(payload, bytes):
        return payload.replace(DUMMY_TOKEN.encode(), token.encode())
    return payload.replace(DUMMY_TOKEN, token)


app = FastAPI(title="Echo MX Session Bridge", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    """Return only local guardian state; never turn UI health polls into MX traffic."""

    guardian = SESSION_STATE.load()
    try:
        session = SESSION.load()
    except RuntimeError as exc:
        return {
            "ok": False,
            "authenticated": False,
            "state": guardian["state"],
            "guardian": guardian,
            "detail": str(exc),
        }
    state = str(guardian.get("state") or "unknown")
    authenticated = state == "healthy" and bool(guardian.get("authenticated"))
    return {
        "ok": authenticated,
        "authenticated": authenticated,
        "state": state,
        "guardian": guardian,
        "hosturl": session["hosturl"],
        "session_age_seconds": max(
            0, int(time.time()) - int(session.get("logged_in_at") or time.time())
        ),
        "checked_at": int(guardian.get("checked_at") or time.time()),
        "check_source": "local_guardian_state",
    }


@app.get("/echo/session-status")
async def session_status() -> dict[str, Any]:
    """Expose only secret-free health for the Viewer and collector."""

    return await healthz()


@app.get("/viewer", response_class=HTMLResponse)
def viewer_page() -> HTMLResponse:
    try:
        if VIEWER_FILE.stat().st_size > MAX_BODY:
            raise OSError("viewer file is too large")
        template = VIEWER_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("MX viewer unavailable (%s)", type(exc).__name__)
        return HTMLResponse(
            "<h1>萌侠界面尚未部署</h1><p>代理会话仍在运行，请稍后重试。</p>",
            status_code=503,
            headers=SECURITY_HEADERS,
        )
    return HTMLResponse(_render_viewer(template), headers=SECURITY_HEADERS)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin", "").rstrip("/")
    host = request.headers.get("host", "")
    return bool(host) and origin in {f"http://{host}", f"https://{host}"}


@app.get("/echo/groups")
def list_groups() -> dict[str, Any]:
    return GROUPS.snapshot()


@app.post("/echo/groups")
def create_group(payload: GroupPayload) -> dict[str, Any]:
    try:
        group = GROUPS.create(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "group": group}


@app.patch("/echo/groups/{group_id}")
def rename_group(group_id: str, payload: GroupPayload) -> dict[str, Any]:
    try:
        group = GROUPS.rename(group_id, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if group is None:
        raise HTTPException(status_code=404, detail="分组不存在")
    return {"ok": True, "group": group}


@app.delete("/echo/groups/{group_id}")
def delete_group(group_id: str) -> dict[str, bool]:
    if not GROUPS.delete(group_id):
        raise HTTPException(status_code=404, detail="分组不存在")
    return {"ok": True}


@app.put("/echo/group-assignments/{room_id}")
def assign_group(room_id: str, payload: GroupPayload) -> dict[str, Any]:
    try:
        assignment = GROUPS.assign(room_id, payload.group_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    return {"ok": True, "assignment": assignment}


@app.post("/echo/capture")
def capture_messages(payload: CaptureBatch, request: Request) -> dict[str, Any]:
    if request.headers.get("x-echo-viewer") != "1" or not _same_origin(request):
        raise HTTPException(status_code=403, detail="viewer capture is same-origin only")
    result = MESSAGES.capture(payload.messages)
    return {"ok": True, **result}


@app.get("/echo/capture/status")
def capture_status() -> dict[str, Any]:
    return {"ok": True, **MESSAGES.status()}


@app.get("/echo/mobile-snapshot")
def mobile_snapshot(request: Request) -> dict[str, Any]:
    if request.headers.get("x-echo-internal") != "1":
        raise HTTPException(status_code=403, detail="internal endpoint")
    return {"groups": GROUPS.snapshot(), "collector": MESSAGES.status()}


@app.get("/echo/messages")
def cached_messages(
    request: Request, after_seq: int = 0, limit: int = 500
) -> dict[str, Any]:
    if request.headers.get("x-echo-internal") != "1":
        raise HTTPException(status_code=403, detail="internal endpoint")
    return MESSAGES.messages(after_seq=after_seq, limit=limit)


@app.websocket("/socket.io/")
@app.websocket("/msg/socket.io/")
async def socket_bridge(websocket: WebSocket) -> None:
    if websockets is None:
        await websocket.close(code=1011)
        return
    local_origin = websocket.headers.get("origin", "")
    local_host = websocket.headers.get("host", "")
    if local_origin not in {f"http://{local_host}", f"https://{local_host}"}:
        await websocket.close(code=1008)
        return
    try:
        token = SESSION.load()["token"]
    except RuntimeError:
        await websocket.close(code=1011)
        return
    query = websocket.url.query
    target = SOCKET_UPSTREAM.replace("https://", "wss://") + "/socket.io/"
    if query:
        target += "?" + query
    try:
        upstream = await websockets.connect(
            target,
            origin=SOCKET_UPSTREAM,
            open_timeout=10,
            close_timeout=5,
            max_size=2 * MAX_BODY,
            proxy=None,
        )
    except Exception:  # noqa: BLE001
        await websocket.close(code=1013)
        return
    await websocket.accept()

    async def browser_to_upstream() -> None:
        while True:
            packet = await websocket.receive()
            if packet.get("type") == "websocket.disconnect":
                return
            if packet.get("text") is not None:
                await upstream.send(_inject_socket_token(packet["text"], token))
            elif packet.get("bytes") is not None:
                await upstream.send(_inject_socket_token(packet["bytes"], token))

    async def upstream_to_browser() -> None:
        async for packet in upstream:
            if isinstance(packet, bytes):
                await websocket.send_bytes(packet)
            else:
                await websocket.send_text(packet)

    tasks = {asyncio.create_task(browser_to_upstream()), asyncio.create_task(upstream_to_browser())}
    try:
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await upstream.close()
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=1000)


@app.api_route("/{upstream_path:path}", methods=["GET", "HEAD", "POST"])
async def proxy(request: Request, upstream_path: str) -> Response:
    safe_path = _safe_path(upstream_path)
    if safe_path is None:
        raise HTTPException(404, "upstream path not available")
    if request.method not in {"GET", "HEAD", "POST"}:
        raise HTTPException(405, "method not available")
    lowered_path = "/" + safe_path.lower()
    if any(lowered_path.endswith(suffix) for suffix in BLOCKED_API_SUFFIXES):
        raise HTTPException(403, "account mutation is not available through this bridge")
    try:
        session = SESSION.load()
    except RuntimeError:
        return HTMLResponse(
            "<h1>萌侠云端会话尚未就绪</h1><p>请重新完成一次验证码登录。</p>",
            status_code=503,
            headers=SECURITY_HEADERS,
        )

    body = await _bounded_body(request)
    is_socket_http = safe_path.startswith(("socket.io", "msg/socket.io"))
    target_origin = SOCKET_UPSTREAM if is_socket_http else UPSTREAM
    target_path = safe_path.removeprefix("msg/") if is_socket_http else safe_path
    target = f"{target_origin}/{target_path}"
    inject_token = not safe_path.endswith("/api/code") and not safe_path.endswith("/api/login")
    if is_socket_http and body:
        body = _inject_socket_token(body, session["token"])  # type: ignore[assignment]
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=6, read=65, write=30, pool=6),
        follow_redirects=False,
        trust_env=False,
    )
    try:
        upstream_response = await client.send(
            client.build_request(
                request.method,
                target,
                params=list(request.query_params.multi_items()),
                headers=_request_headers(
                    request,
                    target_origin,
                    token=session["token"] if inject_token else None,
                ),
                content=body or None,
            ),
            stream=True,
            auth=None,
        )
    except (httpx.HTTPError, OSError, ValueError) as exc:
        await client.aclose()
        LOGGER.warning("MX upstream request failed (%s)", type(exc).__name__)
        return JSONResponse(
            {"detail": "MX upstream temporarily unavailable"},
            status_code=503,
            headers={"Retry-After": "10", **SECURITY_HEADERS},
        )

    content_type = upstream_response.headers.get("content-type", "")
    if "text/html" in content_type.lower() or "javascript" in content_type.lower():
        try:
            content = await upstream_response.aread()
        finally:
            await upstream_response.aclose()
            await client.aclose()
        if "text/html" in content_type.lower():
            content = _bootstrap_html(content, session["hosturl"])
        else:
            content = _rewrite_javascript(content)
        headers = _response_headers(upstream_response)
        headers.pop("content-encoding", None)
        headers.pop("content-length", None)
        headers["Cache-Control"] = "no-store"
        return Response(content, status_code=upstream_response.status_code, headers=headers)

    buffered = upstream_response.content if upstream_response.is_stream_consumed else None

    async def stream_body() -> AsyncIterator[bytes]:
        try:
            if buffered is not None:
                if buffered:
                    yield buffered
            else:
                async for chunk in upstream_response.aiter_raw():
                    if chunk:
                        yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_body(),
        status_code=upstream_response.status_code,
        headers=_response_headers(upstream_response),
    )


__all__ = [
    "app",
    "ConversationGroupStore",
    "MessageCache",
    "SessionStore",
    "SessionStateStore",
    "_bootstrap_html",
    "_inject_socket_token",
    "_render_viewer",
    "_safe_path",
]

