# EchoAI Browser Relay

## MCP OAuth custom-scheme compatibility

The relay injects an early, provider-scoped bridge on TongDaXin's official
OAuth pages. When that page tries to launch a `workbuddy://` callback, the
bridge validates the page origin, OAuth state and callback payload, then
returns the result to EchoOS' loopback callback. Authorization URLs and codes
are never logged. The service worker repeats every validation before allowing
the local navigation; pages outside `https://auth.tdx.com.cn/tdx-oauth/` cannot
use this bridge.

After updating the unpacked relay, reload **EchoAI Browser Relay** once from the
browser extension manager so the new document-start content script is active.

Local unpacked extension for connecting a normal Chromium browser to EchoOS browser automation.
The primary UI is a Chrome Side Panel so the Agent conversation stays visible
without covering the page being operated.

## Install

1. Open `chrome://extensions`.
2. Enable `Developer mode`.
3. Click `Load unpacked`.
4. Select this folder: `extensions/echo-browser-relay`.

The extension keeps a local push connection to
`ws://127.0.0.1:8000/api/browser/relay/ws`, so commands still arrive when
Chrome suspends normal background timers. It falls back to HTTP heartbeats at
`http://127.0.0.1:8000/api/browser/relay/heartbeat` for older runtimes.
Click the `EchoAI Browser Relay` toolbar icon on any page to open the EchoAI side panel.
The side panel talks to the local realtime gateway at `/api/realtime`, prefixes
turns with `@Chrome`, and keeps the active tab available through the relay.

When EchoOS sends a browser action, the extension executes it in the currently active Chromium tab and posts the result back to the local server. If you change these files while the extension is loaded, click `Reload` on the extension card in `chrome://extensions`.

The relay implements the same observe/act contract as the other browser
backends: structured `state`, verified selectors, React-compatible input,
contenteditable/select support, keyboard submission, conditional waits, and
password-value redaction. Interactive actions also auto-wait for targets to
become visible, enabled, stable, and uncovered before acting. After an SPA
re-render, a stale selector can recover only when the prior role/name/type
fingerprint has one unique match; ambiguous matches fail closed. Click-driven
navigation is tracked separately so a destroyed execution context is not
misreported as failure after the page has actually moved.

If the EchoOS gateway has authentication enabled, open the key button in
the side panel and enter the same API key or session token used by the main
app. The credential is stored only in the current Chrome profile and is sent
as a bearer token for HTTP plus a browser-safe WebSocket subprotocol. It is
never placed in the connection URL.
Also set `ECHO_BROWSER_RELAY_TOKEN` for the EchoOS runtime so its internal
ExtensionBackend requests can pass the same authenticated gateway boundary;
diagnostics expose only whether this is configured, never the credential.

## Side panel mode

The Chrome Side Panel is the recommended external-browser experience:

- The real webpage remains fully visible in the main tab.
- The Agent chat, approvals, current task, and action log stay in the side panel.
- Page overlays are avoided by default; use the `页面轻面板` button only as a fallback.
- `@Chrome` turns prefer the extension relay, so signed-in pages and browser extensions stay available.
- While Agent owns a tab lease, the controlled page gets a thin edge light only;
  it is non-interactive, non-blocking, and avoids aurora/gradient effects.

## Bookmarklet mode

EchoOS can also expose a draggable `EchoAI Page Agent` bookmarklet in the browser page. Drag it to the Chrome/Edge bookmarks bar, then click it on any target page to connect that page to EchoAI without installing the unpacked extension.

Bookmarklet mode supports page text extraction, click/type/scroll actions, and `window.__echoPageAgent` semantic actions when the page provides them. Screenshots and cross-tab control still require the unpacked extension.

