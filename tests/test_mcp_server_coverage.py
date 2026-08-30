"""Dense coverage for tentacle mcp_server (audit Q-05)."""

from __future__ import annotations

from runtime.tentacle.mobile.mcp_server import (
    SseSessionManager,
    TentacleMcpServer,
)


def _server() -> TentacleMcpServer:
    return TentacleMcpServer()


def test_initialize_and_error_result() -> None:
    s = _server()
    init = s._handle_initialize({"clientInfo": {"name": "cursor", "version": "1"}})
    assert init["protocolVersion"] == "2024-11-05"
    assert init["serverInfo"]["name"] == "echo-tentacle"
    err = s._error_result("boom")
    assert err["isError"] is True
    assert "boom" in err["content"][0]["text"]


def test_list_tools_and_skill_name_resolution() -> None:
    s = _server()
    tools = s.list_tools()
    assert len(tools) >= 1
    # Direct capability name resolves as-is.
    assert s._skill_name_for_mcp_tool("android.tap") == "android.tap"
    # Legacy underscore form falls back to a dot conversion.
    assert s._skill_name_for_mcp_tool("android_tap") == "android.tap"
    # Unknown name -> None.
    assert s._skill_name_for_mcp_tool("completely.unknown") is None


def test_auto_detect_vlm_config(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_VLM_MODEL", raising=False)
    monkeypatch.delenv("ECHO_VLM_BASE_URL", raising=False)
    cfg = TentacleMcpServer._auto_detect_vlm_config()
    assert cfg is None or hasattr(cfg, "model")


def test_sse_session_manager() -> None:
    s = _server()
    mgr = SseSessionManager(s)
    sess = mgr.create_session(actor_id="alice")
    assert sess.session_id
    assert mgr.get_session(sess.session_id) is sess
    mgr.remove_session(sess.session_id)
    assert mgr.get_session(sess.session_id) is None

