"""Trusted local state server for browser behavioral fixtures."""

from __future__ import annotations

import argparse
import json
import secrets
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class FixtureHandler(SimpleHTTPRequestHandler):
    workspace: Path
    case_id: str
    session_token: str

    def log_message(self, _format: str, *args: Any) -> None:
        return

    @property
    def state_path(self) -> Path:
        return self.workspace / ".eval-state.json"

    def _state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        return value if isinstance(value, dict) else {}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

    def _json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        value = json.loads(self.rfile.read(length) or b"{}")
        return value if isinstance(value, dict) else {}

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ui_source(self) -> bool:
        return self.headers.get("X-Eval-UI") == self.session_token

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route == "/api/customers" and self.case_id == "browser.dynamic-crud":
            self._send_json(self._state().get("customers", []))
            return
        if route == "/confirmation.html" and self.case_id == "browser.rich-editor-upload":
            state = self._state()
            state["confirmation_loaded"] = True
            self._write_state(state)
            body = b"<!doctype html><p id='confirmed'>Onboarding complete</p>"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if route in {"/", "/index.html"}:
            html = (self.workspace / "index.html").read_text(encoding="utf-8")
            marker = f'<meta name="eval-session" content="{self.session_token}">'
            html = html.replace("<head>", f"<head>{marker}", 1)
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if self.case_id == "browser.dynamic-crud" and route == "/api/customers":
            state = self._state()
            body = self._json_body()
            row = {"id": "customer-1", "name": body.get("name"), "plan": body.get("plan")}
            state["customers"] = [row]
            state.setdefault("audit", []).append(
                {"action": "create", **row, "ui": self._ui_source()}
            )
            self._write_state(state)
            self._send_json(row, HTTPStatus.CREATED)
            return
        if self.case_id == "browser.dynamic-crud" and route.endswith("/verify"):
            state = self._state()
            body = self._json_body()
            state.setdefault("audit", []).append(
                {"action": "verify", **body, "ui": self._ui_source()}
            )
            self._write_state(state)
            self._send_json({"ok": True})
            return
        if self.case_id == "browser.rich-editor-upload" and route == "/api/onboarding":
            state = self._state()
            state["submissions"] = int(state.get("submissions") or 0) + 1
            state["payload"] = self._json_body()
            state["ui"] = self._ui_source()
            self._write_state(state)
            self._send_json({"ok": True})
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_PATCH(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if self.case_id != "browser.dynamic-crud" or not route.startswith("/api/customers/"):
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        state = self._state()
        body = self._json_body()
        rows = state.get("customers") or []
        if rows:
            rows[0]["plan"] = body.get("plan")
            state.setdefault("audit", []).append(
                {"action": "edit", **rows[0], "ui": self._ui_source()}
            )
        self._write_state(state)
        self._send_json(rows[0] if rows else {})

    def do_DELETE(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if self.case_id != "browser.dynamic-crud" or not route.startswith("/api/customers/"):
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        state = self._state()
        rows = state.get("customers") or []
        row = rows[0] if rows else {}
        state["customers"] = []
        state.setdefault("audit", []).append({"action": "delete", **row, "ui": self._ui_source()})
        self._write_state(state)
        self._send_json({"ok": True})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument("workspace", type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    FixtureHandler.workspace = workspace
    FixtureHandler.case_id = args.case_id
    FixtureHandler.session_token = secrets.token_urlsafe(24)
    handler = partial(FixtureHandler, directory=str(workspace))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    (workspace / ".eval-state.json").write_text("{}", encoding="utf-8")
    (workspace / "EVAL_URL.txt").write_text(
        f"http://127.0.0.1:{server.server_port}/index.html\n",
        encoding="utf-8",
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

