from __future__ import annotations

import json

from runtime.execution.suckers import computer_api_skills
from runtime.platform.process.session import Session, session_scope


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_computer_api_base_url_defaults_to_local_gateway(monkeypatch) -> None:
    for key in computer_api_skills._BASE_URL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    assert computer_api_skills._computer_api_base_url() == ("http://127.0.0.1:8000/api/computer")
    assert computer_api_skills._computer_api_diagnostics()["configured_by"] == "default"


def test_computer_api_base_url_accepts_gateway_or_api_paths(monkeypatch) -> None:
    cases = {
        "http://localhost:8123": "http://localhost:8123/api/computer",
        "http://localhost:8123/api": "http://localhost:8123/api/computer",
        "http://localhost:8123/api/computer": "http://localhost:8123/api/computer",
        "https://example.test/root": "https://example.test/root/api/computer",
    }

    for raw, expected in cases.items():
        monkeypatch.setenv("ECHO_COMPUTER_API_BASE_URL", raw)
        assert computer_api_skills._computer_api_base_url() == expected


def test_computer_api_call_reports_resolved_bridge(monkeypatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setenv("ECHO_COMPUTER_API_BASE_URL", "http://localhost:8123/api")

    def fake_urlopen(req: object, timeout: int) -> _FakeResponse:
        seen["url"] = req.full_url  # type: ignore[attr-defined]
        seen["timeout"] = timeout
        return _FakeResponse({"ok": True, "status": {"ok": True}})

    monkeypatch.setattr(computer_api_skills.urllib_request, "urlopen", fake_urlopen)

    data = computer_api_skills._call("GET", "/status")

    assert data["ok"] is True
    assert seen == {
        "url": "http://localhost:8123/api/computer/status",
        "timeout": computer_api_skills._TIMEOUT_SECONDS,
    }
    assert data["computer_api"] == {
        "schema": "echo.computer_api_bridge.v1",
        "base_url": "http://localhost:8123/api/computer",
        "configured_by": "ECHO_COMPUTER_API_BASE_URL",
        "env_keys": list(computer_api_skills._BASE_URL_ENV_KEYS),
        "default_gateway_base_url": "http://127.0.0.1:8000",
        "error": "",
    }


def test_computer_api_call_includes_bridge_diagnostics_on_unreachable(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ECHO_COMPUTER_API_BASE_URL", "http://localhost:8123")

    def fake_urlopen(_req: object, timeout: int) -> object:
        assert timeout == computer_api_skills._TIMEOUT_SECONDS
        raise computer_api_skills.urllib_error.URLError("offline")

    monkeypatch.setattr(computer_api_skills.urllib_request, "urlopen", fake_urlopen)

    data = computer_api_skills._call("GET", "/status")

    assert data["ok"] is False
    assert "http://localhost:8123/api/computer" in data["error"]
    assert data["computer_api"]["base_url"] == "http://localhost:8123/api/computer"


def test_computer_api_call_carries_selected_desktop_target(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_urlopen(req: object, timeout: int) -> _FakeResponse:
        seen["body"] = json.loads(req.data.decode("utf-8"))  # type: ignore[attr-defined]
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(computer_api_skills.urllib_request, "urlopen", fake_urlopen)
    with session_scope(
        Session(
            metadata={
                "automation_target": {
                    "kind": "desktop_window",
                    "source": "computer",
                    "id": "42-1",
                    "title": "Inbox",
                    "app_id": "com.example.App",
                    "app_name": "Example",
                }
            }
        )
    ):
        result = computer_api_skills._call(
            "POST", "/actions/preview", {"action": "click", "x": 10, "y": 20}
        )

    assert result["ok"] is True
    assert seen["body"]["automation_target"]["id"] == "42-1"  # type: ignore[index]


def test_computer_observe_exposes_bridge_without_capture(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_COMPUTER_API_BASE_URL", "http://localhost:8123")

    def fake_call(
        method: str, path: str, body: dict[str, object] | None = None
    ) -> dict[str, object]:
        assert method == "GET"
        assert path == "/status"
        assert body is None
        return {"ok": True, "screen": {"width": 1, "height": 1}}

    monkeypatch.setattr(computer_api_skills, "_call", fake_call)

    data = computer_api_skills._computer_observe(capture=False)

    assert data["ok"] is True
    assert data["computer_api"]["schema"] == "echo.computer_api_bridge.v1"
    assert data["computer_api"]["base_url"] == "http://localhost:8123/api/computer"


