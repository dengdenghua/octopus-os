# Echo MX cloud session bridge

This directory is the tracked deployment source for the loopback-only MX
Viewer bridge running on the Alibaba Cloud host. It deliberately remains
separate from Echo accounts, billing, points, and administration.

## Components

- `mx_session_bridge.py`: same-origin read proxy. The browser only receives the
  `echo-server-session` placeholder; the real upstream token stays in a
  mode-0600 server file.
- `mx_session_guardian.py`: security, state, backoff, and session-verification
  primitives shared by the deployment.
- `mx_ops_agent.py`: a small standalone operations agent. It does not run the
  Echo runtime. Automatic login is disabled in the production unit. When an
  operator explicitly arms it, the agent can temporarily open the official
  login page, use Agnes vision for the CAPTCHA, verify the session, and close
  the browser.
- `mx_viewer_collector.py`: keeps one Viewer/Socket.IO session alive and turns
  pushed room-summary changes into bounded, deduplicated captures. It never
  periodically reloads the page, enumerates rooms, or performs history
  backfill.
- `mx_viewer.html`: Apple-style grouping shell with visible session state and
  automatic iframe refresh after recovery.

## Secret files

Secrets never belong in Git, service units, command-line arguments, URLs, or
logs. On the server only:

```text
/var/lib/echo-mx/credentials.json  0600 echo-mx:echo-mx
/var/lib/echo-mx/session.json      0600 echo-mx:echo-mx
/var/lib/echo-mx/vision.json       0600 echo-mx:echo-mx
```

The credential and vision files follow their `.example` counterparts.
`MX_AUTO_LOGIN_ENABLED=false` is the production default, so an expired session
requires explicit operator authorization rather than CAPTCHA retries. A
password/account rejection is a persistent local lock evaluated before any
upstream health request; transient 5xx responses cannot overwrite or re-arm
it. Even when an operator temporarily enables recovery, CAPTCHA failures are
bounded and use exponential backoff.

The upstream business response `账号异常` is also a hard stop. It represents an
account-side authorization or risk-control condition, not a CAPTCHA failure;
automatically retrying it could worsen the restriction.

Agnes is called through its OpenAI-compatible HTTPS endpoint. Its bearer key is
read only at call time from `vision.json`; it is never placed in a unit file,
command line, URL, browser context, state response, or log. Local Tesseract is a
bounded fallback only when the vision provider is temporarily unavailable.
Agnes output is accepted only when it contains one unique four-digit answer;
otherwise the attempt is abandoned and a fresh CAPTCHA is requested.
SVG CAPTCHAs retain their original interference lines and near-native display
scale for Agnes; aggressive line removal and enlargement can turn the ornate
digits into a logo-like shape and reduce model accuracy.

The tracked macOS LaunchAgent adds a remote dynamic SOCKS forward bound only to
server loopback (`127.0.0.1:18084`). It is dormant while automatic login is
disabled. Local bridge health/status endpoints never call the MX upstream;
the guardian performs at most one upstream session verification every 15
minutes while it is not hard-locked.

The secret-free state is written to:

```text
/var/lib/echo-mx/session-state.json  0600 echo-mx:echo-mx
```

## Runtime dependencies

The existing Python environment needs `httpx`, `fastapi`, `uvicorn`,
`websockets`, and `playwright`. Automatic CAPTCHA recovery additionally uses:

```text
/usr/bin/rsvg-convert
/usr/bin/tesseract
```

On Ubuntu these are supplied by `librsvg2-bin` and `tesseract-ocr`.

## Recovery states

- `healthy`: authenticated and collecting.
- `restoring`: one bounded login recovery is running.
- `captcha_failed`: OCR failed or the upstream rejected the CAPTCHA; retry is
  delayed.
- `credentials_rejected`: hard stop until an operator updates credentials.
- `login_required`: the secure credential file is missing or invalid.
- `upstream_unavailable`: network/upstream error; credentials are not submitted.

`GET /healthz` and `GET /echo/session-status` read only this local secret-free
state and never probe the upstream. Login, registration, logout, account
updates, and room mutations remain blocked in the browser-facing proxy.
