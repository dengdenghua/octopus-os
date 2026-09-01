from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import stat
import sys
from pathlib import Path
from types import ModuleType

import httpx

ROOT = Path(__file__).resolve().parents[1]
CLOUD_DIR = ROOT / "deploy" / "echo-mx"


def _load_module(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, CLOUD_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guardian_module = _load_module("mx_session_guardian", "mx_session_guardian.py")
ops_module = _load_module("echo_mx_ops_agent", "mx_ops_agent.py")
bridge_module = _load_module("echo_mx_session_bridge", "mx_session_bridge.py")
collector_module = _load_module("echo_mx_push_collector", "mx_viewer_collector.py")


class FixedSolver:
    def __init__(self, answer: str = "42") -> None:
        self.answer = answer
        self.calls = 0

    def solve(self, _data_uri: str) -> str:
        self.calls += 1
        return self.answer


def _secure_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def _captcha_payload() -> str:
    return "data:image/png;base64," + base64.b64encode(b"fake-png").decode()


def test_secret_json_requires_strict_permissions(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text('{"user":"demo","password":"secret"}', encoding="utf-8")
    path.chmod(0o644)

    try:
        guardian_module.load_credentials(path)
    except guardian_module.GuardianError as exc:
        assert "0600" in str(exc)
    else:  # pragma: no cover - fail closed assertion
        raise AssertionError("unsafe credential permissions were accepted")

    path.chmod(0o600)
    assert guardian_module.load_credentials(path) == {
        "user": "demo",
        "password": "secret",
    }


def test_atomic_json_is_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.json"

    guardian_module._atomic_json(path, {"state": "healthy"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"state": "healthy"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_captcha_decoder_is_bounded_and_type_checked() -> None:
    suffix, raw = guardian_module.CaptchaSolver._decode(_captcha_payload())
    assert suffix == ".png"
    assert raw == b"fake-png"

    for invalid in (
        "https://example.test/captcha.png",
        "data:text/plain;base64,Zm9v",
        "data:image/png;base64,not-base64!",
    ):
        try:
            guardian_module.CaptchaSolver._decode(invalid)
        except guardian_module.CaptchaRecognitionError:
            pass
        else:  # pragma: no cover - fail closed assertion
            raise AssertionError(f"unsafe CAPTCHA payload was accepted: {invalid}")


def test_svg_cleaner_removes_interference_paths() -> None:
    raw = b"""<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40">
      <path d="M0 0L100 40" stroke="#666" fill="none"/>
      <path d="M10 10L20 20" fill="#333"/>
    </svg>"""

    cleaned = guardian_module.CaptchaSolver._clean_svg(raw)

    assert b'fill="none"' not in cleaned
    assert b'stroke="#666"' not in cleaned
    assert cleaned.count(b"<ns0:path") == 1
    assert b'fill="#000000"' in cleaned


def test_agnes_solver_accepts_one_four_digit_answer(tmp_path: Path) -> None:
    config = tmp_path / "vision.json"
    _secure_json(
        config,
        {
            "base_url": "https://vision.example.test/v1",
            "api_key": "test-key",
            "model": "agnes-2.5-flash",
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://vision.example.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "agnes-2.5-flash"
        image = body["messages"][0]["content"][0]["image_url"]["url"]
        assert image.startswith("data:image/png;base64,")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "\n\n2992"}}]},
        )

    solver = ops_module.AgnesCaptchaSolver(
        config_file=config,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert solver.solve(_captcha_payload()) == "2992"


def test_agnes_solver_rejects_ambiguous_output(tmp_path: Path) -> None:
    config = tmp_path / "vision.json"
    _secure_json(
        config,
        {
            "base_url": "https://vision.example.test/v1",
            "api_key": "test-key",
            "model": "agnes-2.5-flash",
        },
    )
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "可能是2992，也可能是2997"}}]},
            )
        )
    )
    solver = ops_module.AgnesCaptchaSolver(config_file=config, client=client)

    try:
        solver.solve(_captcha_payload())
    except guardian_module.CaptchaRecognitionError:
        pass
    else:  # pragma: no cover - fail closed assertion
        raise AssertionError("ambiguous Agnes output was accepted")


def test_hybrid_solver_prefers_agnes_without_ocr_veto() -> None:
    vision = FixedSolver("2992")
    ocr = FixedSolver("2997")
    hybrid = ops_module.HybridCaptchaSolver(
        vision=vision,
        ocr=ocr,
    )

    assert hybrid.solve(_captcha_payload()) == "2992"
    assert vision.calls == 1
    assert ocr.calls == 0


def test_guardian_restores_and_atomically_verifies_session(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"
    credential_file = tmp_path / "credentials.json"
    state_file = tmp_path / "state.json"
    _secure_json(
        session_file,
        {"token": "old-token-1234567890", "hosturl": "/5", "logged_in_at": 10},
    )
    _secure_json(
        credential_file,
        {"user": "test-account", "password": "test-password"},
    )
    calls = {"login": 0, "info": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/user/info"):
            calls["info"] += 1
            if request.headers.get("token") == "new-token-1234567890":
                return httpx.Response(200, json={"code": 200})
            return httpx.Response(200, json={"code": 502, "msg": "未登陆"})
        if request.url.path == "/3/api/code":
            return httpx.Response(
                200,
                json={"code": 200, "key": "captcha-key-123", "captcha": _captcha_payload()},
            )
        if request.url.path == "/3/api/login":
            calls["login"] += 1
            assert "token" not in request.headers
            body = json.loads(request.content)
            assert body["user"] == "test-account"
            assert body["password"] == "test-password"
            assert body["code"] == "42"
            return httpx.Response(
                200,
                json={"code": 200, "token": "new-token-1234567890", "hosturl": "/5"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    guardian = guardian_module.MXSessionGuardian(
        upstream="https://upstream.test",
        session_file=session_file,
        credential_file=credential_file,
        state_file=state_file,
        client=client,
        solver=FixedSolver(),
        clock=lambda: 1_000.0,
        auto_login_enabled=True,
    )

    state = guardian.run_once()

    assert state["state"] == "healthy"
    assert state["authenticated"] is True
    assert calls == {"login": 1, "info": 2}
    assert guardian_module.load_session(session_file) == {
        "token": "new-token-1234567890",
        "hosturl": "/5",
        "logged_in_at": 1000,
    }
    assert stat.S_IMODE(session_file.stat().st_mode) == 0o600
    assert "token" not in state_file.read_text(encoding="utf-8")
    assert "password" not in state_file.read_text(encoding="utf-8")


def test_guardian_can_delegate_to_official_browser_restorer(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"
    credential_file = tmp_path / "credentials.json"
    state_file = tmp_path / "state.json"
    _secure_json(
        session_file,
        {"token": "old-token-1234567890", "hosturl": "/5"},
    )
    _secure_json(
        credential_file,
        {"user": "test-account", "password": "test-password"},
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        token = request.headers.get("token")
        if token == "browser-token-1234567890":
            return httpx.Response(200, json={"code": 200})
        return httpx.Response(200, json={"code": 502, "msg": "未登陆"})

    def browser_restorer(credentials: dict[str, str]) -> dict[str, object]:
        assert credentials == {
            "user": "test-account",
            "password": "test-password",
        }
        return {"token": "browser-token-1234567890", "hosturl": "/5"}

    guardian = guardian_module.MXSessionGuardian(
        upstream="https://upstream.test",
        session_file=session_file,
        credential_file=credential_file,
        state_file=state_file,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        session_restorer=browser_restorer,
        clock=lambda: 1_500.0,
        auto_login_enabled=True,
    )

    state = guardian.run_once()

    assert state["state"] == "healthy"
    assert calls == ["/5/api/user/info", "/5/api/user/info"]
    assert guardian_module.load_session(session_file)["token"] == ("browser-token-1234567890")


def test_credential_rejection_hard_stops_until_secret_changes(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"
    credential_file = tmp_path / "credentials.json"
    state_file = tmp_path / "state.json"
    _secure_json(
        session_file,
        {"token": "old-token-1234567890", "hosturl": "/5", "logged_in_at": 10},
    )
    _secure_json(credential_file, {"user": "wrong", "password": "wrong"})
    login_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_calls
        if request.url.path.endswith("/api/user/info"):
            return httpx.Response(200, json={"code": 502, "msg": "未登陆"})
        if request.url.path == "/3/api/code":
            return httpx.Response(
                200,
                json={"code": 200, "key": "captcha-key-123", "captcha": _captcha_payload()},
            )
        if request.url.path == "/3/api/login":
            login_calls += 1
            return httpx.Response(200, json={"code": 1, "msg": "账号或密码错误，还剩4次尝试机会"})
        raise AssertionError(f"unexpected request: {request.url}")

    guardian = guardian_module.MXSessionGuardian(
        upstream="https://upstream.test",
        session_file=session_file,
        credential_file=credential_file,
        state_file=state_file,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        solver=FixedSolver(),
        clock=lambda: 2_000.0,
        auto_login_enabled=True,
    )

    first = guardian.run_once()
    second = guardian.run_once()

    assert first["state"] == "credentials_rejected"
    assert second["state"] == "credentials_rejected"
    assert second["next_retry_at"] is None
    assert login_calls == 1


def test_credential_rejection_lock_precedes_every_upstream_probe(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"
    credential_file = tmp_path / "credentials.json"
    state_file = tmp_path / "state.json"
    _secure_json(
        session_file,
        {"token": "old-token-1234567890", "hosturl": "/5", "logged_in_at": 10},
    )
    _secure_json(credential_file, {"user": "locked", "password": "locked"})
    _secure_json(
        state_file,
        {
            "state": "credentials_rejected",
            "authenticated": False,
            "failure_count": 5,
            "credential_revision": guardian_module.credential_revision(credential_file),
        },
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        raise httpx.ConnectError("must not be called", request=request)

    guardian = guardian_module.MXSessionGuardian(
        upstream="https://upstream.test",
        session_file=session_file,
        credential_file=credential_file,
        state_file=state_file,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        solver=FixedSolver(),
        clock=lambda: 2_100.0,
        auto_login_enabled=True,
    )

    state = guardian.run_once()

    assert state["state"] == "credentials_rejected"
    assert state["next_retry_at"] is None
    assert state["failure_count"] == 5
    assert requests == []


def test_automatic_login_is_disabled_by_default(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"
    credential_file = tmp_path / "credentials.json"
    state_file = tmp_path / "state.json"
    _secure_json(
        session_file,
        {"token": "old-token-1234567890", "hosturl": "/5", "logged_in_at": 10},
    )
    _secure_json(credential_file, {"user": "test", "password": "test"})
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json={"code": 502, "msg": "未登陆"})

    guardian = guardian_module.MXSessionGuardian(
        upstream="https://upstream.test",
        session_file=session_file,
        credential_file=credential_file,
        state_file=state_file,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        solver=FixedSolver(),
        clock=lambda: 2_200.0,
    )

    state = guardian.run_once()

    assert state["state"] == "login_required"
    assert state["last_login_attempt_at"] is None
    assert state["login_attempt_count"] == 0
    assert state["last_login_result"] == "disabled"
    assert requests == ["/5/api/user/info"]


def test_account_abnormal_is_a_hard_stop_marker() -> None:
    assert guardian_module.MXSessionGuardian._is_credential_rejection("账号异常")
    assert ops_module.BrowserLoginRestorer._credential_rejection("账号异常")


def test_upstream_failure_never_submits_credentials(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"
    credential_file = tmp_path / "credentials.json"
    state_file = tmp_path / "state.json"
    _secure_json(
        session_file,
        {"token": "old-token-1234567890", "hosturl": "/5", "logged_in_at": 10},
    )
    _secure_json(credential_file, {"user": "test", "password": "test"})
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        raise httpx.ConnectError("offline", request=request)

    guardian = guardian_module.MXSessionGuardian(
        upstream="https://upstream.test",
        session_file=session_file,
        credential_file=credential_file,
        state_file=state_file,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        solver=FixedSolver(),
        clock=lambda: 3_000.0,
    )

    state = guardian.run_once()

    assert state["state"] == "upstream_unavailable"
    assert requests == ["/5/api/user/info"]


def test_bridge_state_store_exposes_only_normalized_fields(tmp_path: Path) -> None:
    path = tmp_path / "session-state.json"
    _secure_json(
        path,
        {
            "state": "restoring",
            "authenticated": False,
            "failure_count": 2,
            "next_retry_at": 500,
            "token": "must-not-leak",
            "password": "must-not-leak",
        },
    )

    state = bridge_module.SessionStateStore(path).load()

    assert state["state"] == "restoring"
    assert state["failure_count"] == 2
    assert state["next_retry_at"] == 500
    assert "token" not in state
    assert "password" not in state


def test_bridge_health_is_local_only(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"
    state_file = tmp_path / "state.json"
    _secure_json(
        session_file,
        {"token": "must-never-leak-123456", "hosturl": "/5", "logged_in_at": 900},
    )
    _secure_json(
        state_file,
        {
            "state": "healthy",
            "authenticated": True,
            "checked_at": 1_000,
            "last_success_at": 1_000,
            "failure_count": 0,
        },
    )
    original_session = bridge_module.SESSION
    original_state = bridge_module.SESSION_STATE
    bridge_module.SESSION = bridge_module.SessionStore(session_file)
    bridge_module.SESSION_STATE = bridge_module.SessionStateStore(state_file)
    try:
        payload = asyncio.run(bridge_module.healthz())
    finally:
        bridge_module.SESSION = original_session
        bridge_module.SESSION_STATE = original_state

    assert payload["authenticated"] is True
    assert payload["check_source"] == "local_guardian_state"
    assert payload["checked_at"] == 1_000
    assert "token" not in json.dumps(payload)
    assert "upstream_status" not in payload


def test_collector_contract_is_push_first_without_room_sweep() -> None:
    source = (CLOUD_DIR / "mx_viewer_collector.py").read_text(encoding="utf-8")
    service = (CLOUD_DIR / "echo-mx-collector.service").read_text(encoding="utf-8")

    assert source.count("page.goto(") == 1
    assert "_collect_cycle" not in source
    assert "_load_room_ids" not in source
    assert "CYCLE_SECONDS" not in source
    assert "MutationObserver" in collector_module.ROOM_WATCHER_JS
    assert "__echoMxRoomChanged" in collector_module.ROOM_WATCHER_JS
    assert "MX_COLLECTOR_MAX_ROOMS" not in service
    assert "MX_COLLECTOR_CYCLE_SECONDS" not in service
    assert "MX_COLLECTOR_HEARTBEAT_SECONDS=900" in service


def test_viewer_gates_upstream_frame_on_local_session_state() -> None:
    viewer = (CLOUD_DIR / "mx_viewer.html").read_text(encoding="utf-8")

    assert "/session-status" in viewer
    assert "登录会话恢复中" in viewer
    assert "credentials_rejected" in viewer
    assert "data-mx-session-loaded" in viewer
    assert "session.authenticated===true" in viewer
    assert "fr.src='about:blank'" in viewer
    assert "window.setInterval(refreshSyncStatus,30000)" in viewer

