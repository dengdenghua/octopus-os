"""平台每日签到：窄权限客户端、幂等提交与本地定时器。

签到属于平台账户写操作，但不会涉及交易。这里刻意不复用 ``live.py`` 的
``PlatformClient``：行情/交易客户端坚持只接受 HTTPS，而当前原站代理使用的是
HTTP。这个客户端只允许三个固定签到接口，只读取已经缓存的 JWT，不读取或发送
账号密码，也不跟随重定向。
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .upstream_url import upstream_origin

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment,misc]

_logger = logging.getLogger(__name__)

_INFO_PATH = "/member/signIn/getSignInInfoV4"
_CONFIG_PATH = "/member/signInV2/getSignConfigInfo"
_SIGN_PATH = "/member/signInV2/signIn"
_ALREADY_SIGNED_CODE = 20010

DEFAULT_SIGN_IN_HOUR = 8
DEFAULT_SIGN_IN_MINUTE = 5


def _shanghai_now() -> datetime:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo("Asia/Shanghai"))
        except Exception:  # pragma: no cover
            pass
    return datetime.now()


class SignInError(RuntimeError):
    """可安全展示给本地用户的签到错误。"""


class PlatformSignInService:
    """只访问平台签到接口的最小客户端。

    ``http_client`` 和 ``now`` 仅用于测试；生产环境每次请求使用独立客户端，避免
    把平台 token 或连接池泄漏给插件里的其他功能。
    """

    def __init__(
        self,
        *,
        base_url: str,
        state_dir: str,
        http_client: httpx.Client | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        origin = upstream_origin(base_url)
        if not origin:
            raise ValueError("签到上游不是有效的 HTTP(S) 地址")
        self.api_base = origin + "/api"
        self.state_dir = Path(state_dir).expanduser()
        self._http_client = http_client
        self._now = now or _shanghai_now
        self._lock = threading.Lock()

    @property
    def token_path(self) -> Path:
        return self.state_dir / "token.json"

    def today(self) -> str:
        return self._now().strftime("%Y-%m-%d")

    def _load_token(self) -> str:
        try:
            token = str(json.loads(self.token_path.read_text(encoding="utf-8")).get("token") or "")
        except Exception as exc:  # noqa: BLE001 - 对外统一为可读提示
            raise SignInError("请先在模拟炒股页面登录") from exc
        if not token:
            raise SignInError("请先在模拟炒股页面登录")
        return token

    def _write_token(self, token: str) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.token_path.with_suffix(self.token_path.suffix + ".tmp")
        tmp.write_text(json.dumps({"token": token}), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.token_path)
        self.token_path.chmod(0o600)

    def _restore_token_file(self, previous: bytes | None) -> None:
        if previous is None:
            with contextlib.suppress(FileNotFoundError):
                self.token_path.unlink()
            return
        tmp = self.token_path.with_suffix(self.token_path.suffix + ".restore")
        tmp.write_bytes(previous)
        tmp.chmod(0o600)
        tmp.replace(self.token_path)
        self.token_path.chmod(0o600)

    def _validate_browser_token_shape(self, token: str) -> str:
        token = str(token or "").strip()
        if not 32 <= len(token) <= 16 * 1024 or token.count(".") != 2:
            raise SignInError("平台登录令牌格式无效")
        try:
            payload = token.split(".")[1]
            decoded = base64.urlsafe_b64decode(payload + "=" * ((4 - len(payload) % 4) % 4))
            claims = json.loads(decoded)
        except Exception as exc:  # noqa: BLE001 - 对外只返回安全提示
            raise SignInError("平台登录令牌格式无效") from exc
        if not isinstance(claims, dict):
            raise SignInError("平台登录令牌格式无效")
        expires_at = claims.get("exp")
        if isinstance(expires_at, (int, float)) and expires_at <= self._now().timestamp():
            raise SignInError("平台登录令牌已经过期")
        return token

    def sync_browser_token(self, token: str) -> dict[str, Any]:
        """Cache a same-origin browser token only after the platform accepts it.

        The signature cannot be verified locally because the third-party issuer
        does not publish verification keys.  We therefore validate the JWT
        shape, install it atomically, query the fixed status endpoint, and roll
        the file back if the upstream rejects it.  The token is never returned.
        """

        try:
            candidate = self._validate_browser_token_shape(token)
        except SignInError as exc:
            return {"ok": False, "signed": False, "error": str(exc)}

        with self._lock:
            previous: bytes | None = None
            with contextlib.suppress(FileNotFoundError):
                previous = self.token_path.read_bytes()
            try:
                self._write_token(candidate)
                result = self.status()
            except OSError:
                self._restore_token_file(previous)
                return {"ok": False, "signed": False, "error": "平台登录状态保存失败"}
            if not result.get("ok"):
                self._restore_token_file(previous)
                return result
            return {**result, "session_synced": True}

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path not in {_INFO_PATH, _CONFIG_PATH, _SIGN_PATH}:
            raise SignInError("已拒绝未知签到接口")
        token = self._load_token()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "okhttp/4.9.0",
            # 平台只认这个非标准头；不要把 token 放进 URL 或日志。
            "token": token,
        }

        def _send(client: httpx.Client) -> httpx.Response:
            return client.post(self.api_base + path, json=payload, headers=headers)

        try:
            if self._http_client is not None:
                response = _send(self._http_client)
            else:
                with httpx.Client(
                    timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    response = _send(client)
        except httpx.HTTPError as exc:
            raise SignInError("签到服务暂时不可用") from exc

        if 300 <= response.status_code < 400:
            raise SignInError("签到服务返回了不安全的跳转")
        if response.status_code >= 400:
            raise SignInError(f"签到服务请求失败（HTTP {response.status_code}）")
        try:
            data = response.json()
        except ValueError as exc:
            raise SignInError("签到服务返回了无效数据") from exc
        if not isinstance(data, dict):
            raise SignInError("签到服务返回了无效数据")
        return data

    @staticmethod
    def _message(response: dict[str, Any], fallback: str) -> str:
        return str(response.get("message") or fallback)

    def _normalize_status(self, response: dict[str, Any], today: str) -> dict[str, Any]:
        if response.get("code") != 1:
            raise SignInError(self._message(response, "签到状态查询失败"))
        data = response.get("data") or {}
        if not isinstance(data, dict):
            raise SignInError("签到状态数据格式错误")
        rows = data.get("signInList") or []
        if not isinstance(rows, list):
            rows = []
        today_day = int(today[-2:])
        current: dict[str, Any] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("date") or "") == today:
                current = row
                break
        if not current:
            # 上游偶尔把 date 置空；仅以当月日号作降级展示，提交仍只用服务端当天。
            current = next(
                (row for row in rows if isinstance(row, dict) and row.get("day") == today_day),
                {},
            )
        reward = current.get("totalMoney")
        if reward is None:
            reward = current.get("amount")
        return {
            "ok": True,
            "date": today,
            "signed": bool(current.get("flag")),
            "reward": reward,
            "continuous_days": int(data.get("continuousDays") or 0),
            "coupon_sum": data.get("couponSum"),
            "expires_this_week": data.get("expiryDateToWeekSum"),
        }

    def status(self) -> dict[str, Any]:
        today = self.today()
        try:
            return self._normalize_status(self._post(_INFO_PATH, {}), today)
        except SignInError as exc:
            return {
                "ok": False,
                "date": today,
                "signed": False,
                "error": str(exc),
            }

    def reward_config(self) -> dict[str, Any]:
        try:
            response = self._post(_CONFIG_PATH, {})
            if response.get("code") != 1:
                raise SignInError(self._message(response, "签到规则查询失败"))
            return {"ok": True, "data": response.get("data") or {}}
        except SignInError as exc:
            return {"ok": False, "error": str(exc)}

    def sign_in(self) -> dict[str, Any]:
        """只签到当天；提交前后都查询，避免重复和假成功。"""
        with self._lock:
            before = self.status()
            if not before.get("ok"):
                return before
            if before.get("signed"):
                return {
                    **before,
                    "already_signed": True,
                    "message": "今日已签到",
                }

            today = str(before["date"])
            try:
                response = self._post(_SIGN_PATH, {"signDate": today})
            except SignInError as exc:
                return {
                    "ok": False,
                    "date": today,
                    "signed": False,
                    "error": str(exc),
                }

            code = response.get("code")
            if code not in (1, _ALREADY_SIGNED_CODE):
                return {
                    "ok": False,
                    "date": today,
                    "signed": False,
                    "error": self._message(response, "签到失败"),
                    "upstream_code": code,
                }

            after = self.status()
            if not after.get("ok"):
                return after
            if not after.get("signed"):
                return {
                    **after,
                    "ok": False,
                    "error": "平台已受理签到，但状态尚未更新，请稍后重试",
                }
            return {
                **after,
                "already_signed": code == _ALREADY_SIGNED_CODE,
                "message": "今日已签到" if code == _ALREADY_SIGNED_CODE else "签到成功",
            }


class DailySignInScheduler:
    """在插件进程内每天运行一次，并在临时失败后有限频率重试。"""

    def __init__(
        self,
        service: PlatformSignInService,
        *,
        state_dir: str,
        enabled: bool = False,
        hour: int = DEFAULT_SIGN_IN_HOUR,
        minute: int = DEFAULT_SIGN_IN_MINUTE,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.service = service
        self.state_dir = Path(state_dir).expanduser()
        self.settings_path = self.state_dir / "auto_sign_in.json"
        self.status_path = self.state_dir / "auto_sign_in_status.json"
        self._now = now or _shanghai_now
        self._settings_lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self.enabled = bool(enabled)
        self.hour = self._valid_hour(hour)
        self.minute = self._valid_minute(minute)
        self.last_run_at = ""
        self.last_result: dict[str, Any] = {}
        self._load_persisted()

    @staticmethod
    def _valid_hour(value: int) -> int:
        value = int(value)
        if not 0 <= value <= 23:
            raise ValueError("小时必须在 0 到 23 之间")
        return value

    @staticmethod
    def _valid_minute(value: int) -> int:
        value = int(value)
        if not 0 <= value <= 59:
            raise ValueError("分钟必须在 0 到 59 之间")
        return value

    def _load_persisted(self) -> None:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            self.enabled = data.get("enabled") is True
            self.hour = self._valid_hour(data.get("hour", self.hour))
            self.minute = self._valid_minute(data.get("minute", self.minute))
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            _logger.warning("paper_trading: 自动签到设置读取失败: %s", exc)
        try:
            state = json.loads(self.status_path.read_text(encoding="utf-8"))
            self.last_run_at = str(state.get("last_run_at") or "")
            result = state.get("last_result") or {}
            self.last_result = result if isinstance(result, dict) else {}
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            _logger.warning("paper_trading: 自动签到状态读取失败: %s", exc)

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        with contextlib.suppress(Exception):  # Windows/受限文件系统
            path.chmod(0o600)

    def _persist_settings(self) -> None:
        self._write_json(
            self.settings_path,
            {"enabled": self.enabled, "hour": self.hour, "minute": self.minute},
        )

    def _persist_status(self) -> None:
        self._write_json(
            self.status_path,
            {"last_run_at": self.last_run_at, "last_result": self.last_result},
        )

    def _next_run(self, now: datetime | None = None) -> datetime:
        now = now or self._now()
        target = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    def snapshot(self) -> dict[str, Any]:
        with self._settings_lock:
            thread = self._thread
            next_run = self._next_run().isoformat() if self.enabled else ""
            return {
                "enabled": self.enabled,
                "hour": self.hour,
                "minute": self.minute,
                "running": bool(thread and thread.is_alive()),
                "next_run_at": next_run,
                "last_run_at": self.last_run_at,
                "last_result": dict(self.last_result),
            }

    def configure(self, *, enabled: bool, hour: int, minute: int) -> dict[str, Any]:
        with self._settings_lock:
            self.enabled = bool(enabled)
            self.hour = self._valid_hour(hour)
            self.minute = self._valid_minute(minute)
            self._persist_settings()
            self._wake_event.set()
        if self.enabled:
            self.start()
        else:
            self.stop()
        return self.snapshot()

    def run_once(self) -> dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return {"ok": True, "pending": True, "message": "签到检查正在进行"}
        try:
            result = self.service.sign_in()
            self.last_run_at = self._now().isoformat()
            self.last_result = dict(result)
            try:
                self._persist_status()
            except Exception as exc:  # noqa: BLE001
                _logger.warning("paper_trading: 自动签到状态保存失败: %s", exc)
            return result
        finally:
            self._run_lock.release()

    def _loop(self) -> None:
        # 启动时立即补查一次，避免电脑错过固定时刻后当天不再签到。
        result = self.run_once()
        while not self._stop_event.is_set():
            if not self.enabled:
                return
            now = self._now()
            until_next = max(1.0, (self._next_run(now) - now).total_seconds())
            # 登录态失效或网络失败时每 15 分钟重试；成功后等到次日固定时间。
            delay = until_next if result.get("ok") else min(until_next, 15 * 60)
            woke = self._wake_event.wait(delay)
            self._wake_event.clear()
            if self._stop_event.is_set() or not self.enabled:
                return
            if woke:
                continue
            result = self.run_once()

    def start(self) -> bool:
        with self._settings_lock:
            if not self.enabled:
                return False
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop_event = threading.Event()
            self._wake_event = threading.Event()
            self._thread = threading.Thread(
                target=self._loop,
                name="paper-trading-auto-sign-in",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._settings_lock:
            thread = self._thread
            self._stop_event.set()
            self._wake_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._settings_lock:
            if self._thread is thread:
                self._thread = None


__all__ = [
    "DEFAULT_SIGN_IN_HOUR",
    "DEFAULT_SIGN_IN_MINUTE",
    "DailySignInScheduler",
    "PlatformSignInService",
    "SignInError",
]
