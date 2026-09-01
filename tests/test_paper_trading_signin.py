"""每日签到按钮、窄权限客户端与自动任务回归测试。"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from runtime.platform.plugins.bundled.paper_trading.signin import (
    DailySignInScheduler,
    PlatformSignInService,
)


def _token_state(tmp_path: Path) -> Path:
    state = tmp_path / "paper"
    state.mkdir(parents=True)
    (state / "token.json").write_text(json.dumps({"token": "platform-jwt"}), encoding="utf-8")
    return state


def _info(*, signed: bool) -> dict[str, Any]:
    return {
        "code": 1,
        "data": {
            "signInList": [
                {
                    "day": 24,
                    "amount": 3,
                    "totalMoney": 3,
                    "flag": signed,
                    "date": "2026-08-24",
                }
            ],
            "continuousDays": 1 if signed else 0,
            "couponSum": 12.5,
            "expiryDateToWeekSum": 0,
        },
    }


def _jwt(*, exp: int = 2_000_000_000) -> str:
    def segment(value: dict[str, Any]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{segment({'alg': 'RS256'})}.{segment({'exp': exp, 'memberId': 'm1'})}.signature"


def test_sign_in_uses_server_date_token_header_and_verifies_result(tmp_path: Path) -> None:
    state = _token_state(tmp_path)
    seen: list[httpx.Request] = []
    info_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal info_calls
        seen.append(request)
        if request.url.path.endswith("/member/signIn/getSignInInfoV4"):
            info_calls += 1
            return httpx.Response(200, json=_info(signed=info_calls > 1))
        if request.url.path.endswith("/member/signInV2/signIn"):
            return httpx.Response(200, json={"code": 1, "message": "签到成功"})
        raise AssertionError(f"unexpected request: {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = PlatformSignInService(
        base_url="http://up.test:58868/api",
        state_dir=str(state),
        http_client=client,
        now=lambda: datetime(2026, 8, 24, 8, 5),
    )

    result = service.sign_in()

    assert result["ok"] is True
    assert result["signed"] is True
    assert result["reward"] == 3
    assert [request.url.path for request in seen] == [
        "/api/member/signIn/getSignInInfoV4",
        "/api/member/signInV2/signIn",
        "/api/member/signIn/getSignInInfoV4",
    ]
    sign_request = seen[1]
    assert sign_request.headers["token"] == "platform-jwt"
    assert json.loads(sign_request.content) == {"signDate": "2026-08-24"}
    assert "authorization" not in sign_request.headers


def test_sign_in_is_idempotent_when_today_is_already_signed(tmp_path: Path) -> None:
    state = _token_state(tmp_path)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_info(signed=True))

    service = PlatformSignInService(
        base_url="https://up.test/api",
        state_dir=str(state),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: datetime(2026, 8, 24, 23, 59),
    )

    result = service.sign_in()

    assert result["ok"] is True
    assert result["already_signed"] is True
    assert len(seen) == 1
    assert seen[0].url.path.endswith("/member/signIn/getSignInInfoV4")


def test_status_without_cached_login_never_calls_upstream(tmp_path: Path) -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        raise AssertionError("missing token must fail before networking")

    service = PlatformSignInService(
        base_url="https://up.test/api",
        state_dir=str(tmp_path / "missing"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: datetime(2026, 8, 24, 8, 5),
    )

    result = service.status()

    assert result["ok"] is False
    assert "登录" in result["error"]
    assert called is False


def test_browser_session_is_verified_saved_private_and_never_returned(tmp_path: Path) -> None:
    state = _token_state(tmp_path)
    candidate = _jwt()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["token"] == candidate
        return httpx.Response(200, json=_info(signed=True))

    service = PlatformSignInService(
        base_url="https://up.test/api",
        state_dir=str(state),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: datetime(2026, 8, 24, 8, 5),
    )

    result = service.sync_browser_token(candidate)

    assert result["ok"] is True
    assert result["session_synced"] is True
    assert candidate not in json.dumps(result)
    assert json.loads((state / "token.json").read_text())["token"] == candidate
    assert (state / "token.json").stat().st_mode & 0o777 == 0o600


def test_rejected_browser_session_restores_previous_token(tmp_path: Path) -> None:
    state = _token_state(tmp_path)
    previous = json.loads((state / "token.json").read_text())["token"]
    candidate = _jwt()

    service = PlatformSignInService(
        base_url="https://up.test/api",
        state_dir=str(state),
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={"code": 20040, "message": "登录信息已过期"},
                )
            )
        ),
        now=lambda: datetime(2026, 8, 24, 8, 5),
    )

    result = service.sync_browser_token(candidate)

    assert result["ok"] is False
    assert json.loads((state / "token.json").read_text())["token"] == previous


class _FakeSignInService:
    def __init__(self) -> None:
        self.calls = 0

    def sign_in(self) -> dict[str, Any]:
        self.calls += 1
        return {"ok": True, "signed": True, "date": "2026-08-24"}


def test_scheduler_persists_settings_and_last_result(tmp_path: Path) -> None:
    fake = _FakeSignInService()
    scheduler = DailySignInScheduler(
        fake,  # type: ignore[arg-type]
        state_dir=str(tmp_path),
        enabled=False,
        now=lambda: datetime(2026, 8, 24, 8, 0),
    )

    configured = scheduler.configure(enabled=False, hour=7, minute=35)
    result = scheduler.run_once()

    assert configured["enabled"] is False
    assert configured["hour"] == 7
    assert configured["minute"] == 35
    assert result["ok"] is True
    assert fake.calls == 1
    reloaded = DailySignInScheduler(
        fake,  # type: ignore[arg-type]
        state_dir=str(tmp_path),
        enabled=True,
        now=lambda: datetime(2026, 8, 24, 8, 0),
    )
    snapshot = reloaded.snapshot()
    assert snapshot["enabled"] is False
    assert snapshot["hour"] == 7
    assert snapshot["minute"] == 35
    assert snapshot["last_result"]["signed"] is True


def test_platform_page_contains_sign_in_and_auto_sign_in_controls() -> None:
    page = (
        Path(__file__).resolve().parents[1]
        / "runtime/platform/plugins/bundled/paper_trading/page/index.html"
    ).read_text(encoding="utf-8")

    assert 'id="signInBtn"' in page
    assert 'id="autoSignInBtn"' in page
    assert "/api/plugins/paper-trading/check-in" in page
    assert "confirm:true" in page
    assert "localStorage.getItem('userInfo')" in page
    assert "checkInBase+'/session'" in page
    assert "JSON.stringify({token:token})" in page

