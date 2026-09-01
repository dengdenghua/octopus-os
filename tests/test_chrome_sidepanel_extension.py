from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions" / "echo-browser-relay"


def test_chrome_extension_manifest_declares_side_panel() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 3
    assert manifest["side_panel"]["default_path"] == "sidepanel.html"
    assert {"sidePanel", "storage", "tabs", "scripting"} <= set(manifest["permissions"])
    csp = manifest["content_security_policy"]["extension_pages"]
    assert "connect-src" in csp
    assert "ws://127.0.0.1:8000" in csp
    assert "ws://localhost:8000" in csp
    assert manifest["name"] == "EchoAI Browser Relay"
    assert manifest["description"] == (
        "Connect the active browser tab to EchoOS automation."
    )
    assert manifest["action"]["default_title"] == "Open EchoAI Browser Relay"


def test_extension_visible_copy_uses_echo_brand() -> None:
    readme = (EXTENSION / "README.md").read_text(encoding="utf-8")
    bookmarklet = (EXTENSION / "bookmarklet.js").read_text(encoding="utf-8")
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")

    assert readme.startswith("# EchoAI Browser Relay")
    assert "Echo Agent" not in readme
    assert "Echo Browser Relay" not in readme
    assert "WebSocket token query" not in readme
    assert "never placed in the connection URL" in readme
    assert "Echo Page Agent" not in bookmarklet
    assert "已发送到 Echo 对话" not in bookmarklet
    assert "已发送到 EchoAI 对话" in bookmarklet
    assert "Echo Browser Relay:" not in background


def test_tdx_oauth_bridge_is_early_scoped_and_validated() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    bridge = next(
        entry
        for entry in manifest["content_scripts"]
        if "oauth-deep-link-bridge.js" in entry.get("js", [])
    )
    assert bridge["matches"] == ["https://auth.tdx.com.cn/tdx-oauth/*"]
    assert bridge["run_at"] == "document_start"
    assert bridge["js"] == ["oauth-deep-link-core.js", "oauth-deep-link-bridge.js"]

    node = shutil.which("node")
    if node is None:
        return
    core = EXTENSION / "oauth-deep-link-core.js"
    script = r"""
import { pathToFileURL } from 'node:url';
await import(pathToFileURL(process.argv[1]).href);
const build = globalThis.EchoMcpOAuthDeepLink.buildCallbackURL;
const state = 'd91XSEUHgszEbCXJ882-uowvVte4FAzMsRGihLGahss';
const sourceURL = 'https://auth.tdx.com.cn/tdx-oauth/page_workbuddy_oauth.html';
const good = build({sourceURL, deepLinkURL:`workbuddy://oauth/callback?code=C&state=${state}`, backendBaseURL:'http://127.0.0.1:8000'});
const badSource = build({sourceURL:'https://auth.tdx.com.cn.evil.test/tdx-oauth/', deepLinkURL:`workbuddy://oauth/callback?code=C&state=${state}`, backendBaseURL:'http://127.0.0.1:8000'});
const badBackend = build({sourceURL, deepLinkURL:`workbuddy://oauth/callback?code=C&state=${state}`, backendBaseURL:'https://example.com'});
process.stdout.write(JSON.stringify({good, badSource, badBackend}));
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, str(core)],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(result.stdout)
    assert parsed["good"].startswith("http://127.0.0.1:8000/api/mcp/oauth/callback?")
    assert parsed["badSource"] is None
    assert parsed["badBackend"] is None


def test_tdx_oauth_content_script_intercepts_anchor_before_vendor_launch() -> None:
    node = shutil.which("node")
    if node is None:
        return
    core = EXTENSION / "oauth-deep-link-core.js"
    bridge = EXTENSION / "oauth-deep-link-bridge.js"
    script = r"""
import { pathToFileURL } from 'node:url';
let clickListener = null;
let capture = false;
let prevented = 0;
let stopped = 0;
let replaced = '';
let sent = null;
const state = 'd91XSEUHgszEbCXJ882-uowvVte4FAzMsRGihLGahss';
globalThis.document = {
  addEventListener(type, listener, options) {
    if (type === 'click') { clickListener = listener; capture = options === true; }
  },
};
globalThis.window = {
  location: {
    href: 'https://auth.tdx.com.cn/tdx-oauth/page_workbuddy_oauth.html',
    replace(url) { replaced = url; },
  },
};
globalThis.chrome = {
  runtime: {
    async sendMessage(message) {
      sent = message;
      return {
        ok: true,
        callback_url: `http://127.0.0.1:8000/api/mcp/oauth/callback?code=C&state=${state}`,
      };
    },
  },
};
await import(pathToFileURL(process.argv[1]).href);
await import(pathToFileURL(process.argv[2]).href);
const deepLink = `workbuddy://oauth/callback?code=C&state=${state}`;
clickListener({
  composedPath: () => [{ tagName: 'A', href: deepLink }],
  preventDefault: () => { prevented += 1; },
  stopImmediatePropagation: () => { stopped += 1; },
});
await new Promise((resolve) => setTimeout(resolve, 0));
process.stdout.write(JSON.stringify({capture, prevented, stopped, replaced, sent}));
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, str(core), str(bridge)],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(result.stdout)
    assert parsed["capture"] is True
    assert parsed["prevented"] == 1
    assert parsed["stopped"] == 1
    assert parsed["sent"]["type"] == "echo.mcpOAuthDeepLink"
    assert parsed["replaced"].startswith("http://127.0.0.1:8000/api/mcp/oauth/callback?")


def test_sidepanel_is_extension_native_not_page_overlay() -> None:
    html = (EXTENSION / "sidepanel.html").read_text(encoding="utf-8")
    css = (EXTENSION / "sidepanel.css").read_text(encoding="utf-8")

    assert 'href="sidepanel.css"' in html
    assert 'src="sidepanel.js"' in html
    assert "<script>" not in html
    assert "EchoAI" in html
    assert '<span class="mark" aria-hidden="true">E</span>' in html
    assert ">Echo<" not in html
    assert "页面轻面板" in html
    assert 'id="controlTitle"' in html
    assert 'id="stopButton"' in html
    assert 'id="authPanel"' in html
    assert 'id="authTokenInput"' in html
    assert 'id="authSaveButton"' in html
    assert 'type="password"' in html
    assert 'aria-live="polite"' in html
    assert 'data-tone="muted"' in html
    assert 'aria-label="连接密钥设置"' in html
    assert 'aria-controls="authPanel"' in html
    assert 'aria-expanded="false"' in html
    assert 'aria-label="新建对话"' in html
    assert 'role="log"' in html
    assert 'aria-live="assertive"' in html
    assert 'aria-label="发送给 Agent 的消息"' in html
    assert "正在连接" in html
    assert "尚未连接标签页" in html
    assert "等待 Chrome Relay" in html
    assert "Connecting" not in html
    assert "No active tab" not in html
    assert "Waiting for Chrome relay" not in html
    assert ".shell {\n  display: flex;\n  flex-direction: column;" in css
    assert ".messages {\n  flex: 1 1 auto;" in css
    assert ".composer-actions {\n  display: flex;" in css
    assert "grid-template-rows" not in css
    assert ".control-strip > div {\n  min-width: 0;" in css
    assert "overflow-wrap: anywhere" in css


def test_sidepanel_sends_chrome_turns_over_realtime() -> None:
    js = (EXTENSION / "sidepanel.js").read_text(encoding="utf-8")

    assert "/api/realtime" in js
    assert 'rpc("turn/start"' in js
    assert "@Chrome" in js
    assert 'runtime_surfaces: ["chrome"]' in js
    assert "chrome_operation_mode: true" in js
    assert 'method === "item/agentMessage/delta"' in js
    assert "payload.id !== undefined" in js
    assert "showApprovalRequest" in js
    assert 'action: "accept"' in js
    assert 'action: "decline"' in js
    assert 'type: "echo.control"' in js
    assert "toggleControlStop" in js
    assert 'AUTH_TOKEN_KEY = "echo.gatewayToken"' in js
    assert 'return ["bearer.b64", encoded]' in js
    assert "encodeURIComponent(state.authToken)" not in js
    assert 'type: "echo.authChanged"' in js
    assert "waitForRelayConnection" in js
    assert "status.relay?.push_connected === true" in js
    assert "连接密钥已验证，Chrome Relay 已连接。" in js
    assert "密钥已保存，但未能连接。" in js
    assert "setAuthStatus" in js
    assert 'connected ? "success" : "error"' in js
    assert "实时通道与 Chrome 已连接" in js
    assert "实时通道重连中" in js
    assert "Chrome 助手对话" in js
    assert 'setAttribute("aria-expanded", String(!el.authPanel.hidden))' in js
    assert "el.promptInput.focus()" in js
    for stale_copy in (
        "Realtime connected",
        "Realtime error",
        "Realtime reconnecting",
        "Agent working",
        "Agent turn failed",
        "Chrome Sidecar",
        "unknown error",
        "No active tab",
        "Waiting for Chrome relay",
    ):
        assert stale_copy not in js


def test_background_opens_sidepanel_and_keeps_bookmarklet_fallback() -> None:
    js = (EXTENSION / "background.js").read_text(encoding="utf-8")

    assert "chrome.sidePanel.setPanelBehavior" in js
    assert "openPanelOnActionClick" in js
    assert "openSidePanel" in js
    assert "openPageAgent" in js
    assert 'type === "echo.status"' in js
    assert 'type === "echo.openPageAgent"' in js
    assert 'files: ["dom-actions.js"]' in js
    assert "runDomActionInTab" in js
    assert "watchTabNavigation" in js
    assert "recoveredByNavigation" in js


def test_dom_action_runtime_covers_extension_backend_contract() -> None:
    js = (EXTENSION / "dom-actions.js").read_text(encoding="utf-8")

    assert 'action === "state"' in js
    assert 'action === "type"' in js
    assert 'action === "press"' in js
    assert 'action === "wait"' in js
    assert "setNativeValue" in js
    assert "isContentEditable" in js
    assert '"insertReplacementText"' in js
    assert "selectorUnique" in js
    assert "waitForActionable" in js
    assert "elementFromPoint" in js
    assert "__ECHO_DOM_ACTION_CACHE__" in js
    assert "semantic recovery is ambiguous" in js


def test_background_enforces_tab_control_lease() -> None:
    js = (EXTENSION / "background.js").read_text(encoding="utf-8")

    assert "validateCommandLease" in js
    assert "browser_relay_control_interrupted" in js
    assert "setPageControlIndicator" in js
    assert '"echo.controlIndicator"' in js
    assert "active_tab_changed" in js
    assert "tab_url_changed" in js
    assert 'type === "echo.control"' in js
    assert 'type === "echo.userActivity"' in js
    assert "/api/control-sessions" in js
    assert "ensureControlSessionForCommand" in js
    assert "appendControlAction" in js
    assert "appendControlEvidence" in js
    assert "/takeover" in js
    assert "chrome_human_interrupt" in js
    assert '"state"' in js
    assert 'action === "state"' in js
    assert 'headers.set("Authorization", `Bearer ${token}`)' in js
    assert 'return ["bearer.b64", encoded]' in js
    assert "encodeURIComponent(token)" not in js
    assert 'type === "echo.authChanged"' in js


def test_content_script_reports_trusted_user_activity() -> None:
    js = (EXTENSION / "content.js").read_text(encoding="utf-8")

    assert "reportUserActivity" in js
    assert "event?.isTrusted" in js
    assert 'type: "echo.userActivity"' in js
    assert '"pointerdown"' in js
    assert '"input"' in js


def test_content_script_renders_nonblocking_edge_light_not_aurora_overlay() -> None:
    js = (EXTENSION / "content.js").read_text(encoding="utf-8")

    assert "echo-browser-control-indicator" in js
    assert "pointer-events: none" in js
    assert "position: fixed" in js
    assert "inset: 0" in js
    assert "box-shadow:" in js
    assert "echo-control-edge-pulse" in js
    assert "prefers-reduced-motion" in js
    assert "aurora" not in js.lower()
    assert "linear-gradient" not in js.lower()


def test_cursor_overlay_is_early_nonblocking_and_brackets_each_command() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    cursor_script = next(
        entry for entry in manifest["content_scripts"] if "cursor-overlay.js" in entry.get("js", [])
    )
    overlay = (EXTENSION / "cursor-overlay.js").read_text(encoding="utf-8")
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")

    assert cursor_script["run_at"] == "document_start"
    assert "const store = new Map" in overlay
    assert "function subscribe" in overlay
    assert "requestAnimationFrame(render)" in overlay
    assert 'phase: "start"' in overlay
    assert 'phase: "move"' in overlay
    assert 'return "click"' in overlay
    assert 'return "type"' in overlay
    assert 'phase: "end"' in overlay
    assert "attachShadow" in overlay
    assert "pointer-events:none" in overlay
    assert "prefers-reduced-motion" in overlay
    assert 'type !== "echo.cursorOverlay"' in overlay
    assert 'type: "echo.cursorOverlayReady"' in overlay
    assert "const activeCursorOverlayByTab = new Map" in background
    assert "restorePageCursorOverlay(tabId)" in background
    assert 'type === "echo.cursorOverlayReady"' in background
    assert "activeCursorOverlayByTab.delete(String(tabId))" in background
    assert 'setPageCursorOverlay(tabId, "start", action, params)' in background
    assert 'setPageCursorOverlay(tabId, "end", action)' in background


