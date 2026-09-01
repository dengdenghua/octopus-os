from __future__ import annotations

import json
import shutil
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from runtime.execution.suckers.browser_act_skills import register_browser_act_skills
from runtime.execution.suckers.browser_backend import Track
from runtime.execution.suckers.browser_backends import ExtensionBackend
from runtime.execution.suckers.browser_launch import (
    launch_persistent_chromium,
)
from runtime.execution.suckers.registry import SkillRegistry
from runtime.platform.ui.browser_router import create_browser_router
from runtime.safety.auth import Identity, IdentityStore

playwright = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

ROOT = Path(__file__).resolve().parents[1]
SOURCE_EXTENSION = ROOT / "extensions" / "echo-browser-relay"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(
    base_url: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: float = 8,
    token: str = "",
) -> dict[str, Any]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=payload,
        method="GET" if body is None else "POST",
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_until(predicate: Any, timeout: float = 8) -> Any:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except Exception as exc:  # noqa: BLE001 - retry startup races
            last_error = exc
        time.sleep(0.1)
    if last_error:
        raise AssertionError(f"condition did not become true: {last_error}") from last_error
    raise AssertionError("condition did not become true")


def loaded_extension_id(context: Any) -> str:
    if context.service_workers:
        return str(context.service_workers[0].url).split("/")[2]
    page = context.new_page()
    try:
        page.goto("chrome://extensions")
        extension_ids = page.evaluate(
            """() => {
              const manager = document.querySelector('extensions-manager');
              const list = manager?.shadowRoot?.querySelector('extensions-item-list');
              return [...(list?.shadowRoot?.querySelectorAll('extensions-item') || [])]
                .filter(item => item.data?.name?.includes('EchoAI') || item.data?.name === 'Echo Agent')
                .map(item => item.id);
            }"""
        )
    finally:
        page.close()
    if not extension_ids:
        raise AssertionError("Echo extension was not loaded by Chromium")
    return str(extension_ids[0])


@pytest.fixture
def live_extension_runtime(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[str, Any, Path, str]]:
    require_auth = bool(getattr(request, "param", False))
    api_key = "sk-chrome-extension" if require_auth else ""
    port = free_port()
    extension = tmp_path / "extension"
    shutil.copytree(SOURCE_EXTENSION, extension)
    for filename in ("background.js", "manifest.json"):
        path = extension / filename
        path.write_text(
            path.read_text(encoding="utf-8").replace(":8000", f":{port}"),
            encoding="utf-8",
        )

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ECHO_BROWSER_EXTENSION_DIR", str(extension))
    monkeypatch.setenv("ECHO_BROWSER_RELAY_BASE_URL", f"http://127.0.0.1:{port}")
    if api_key:
        monkeypatch.setenv("ECHO_BROWSER_RELAY_TOKEN", api_key)
    app = FastAPI()
    identity_store = None
    if require_auth:
        identity_store = IdentityStore()
        identity_store.add(
            Identity(actor_id="chrome-extension"),
            api_key_plaintext=api_key,
        )
    app.include_router(
        create_browser_router(
            require_auth=require_auth,
            identity_store=identity_store,
        )
    )

    @app.get("/fixture", response_class=HTMLResponse)
    def fixture_page() -> str:
        return """
        <!doctype html>
        <title>Relay fixture</title>
        <form id="search-form">
          <label for="query">Query</label>
          <input id="query" value="before">
          <input id="password" type="password" value="secret">
          <button type="submit">Search</button>
        </form>
        <button id="delayed" disabled>Continue</button>
        <button id="replaceable">Save changes</button>
        <button id="navigate" type="button" onclick="location.href='/destination'">Next page</button>
        <div id="cover" hidden></div>
        <output id="submitted">0</output>
        <output id="clicked">0</output>
        <output id="recovered">0</output>
        <script>
          const query = document.querySelector("#query");
          query.addEventListener("input", () => document.title = `Query: ${query.value}`);
          document.querySelector("#search-form").addEventListener("submit", event => {
            event.preventDefault();
            const output = document.querySelector("#submitted");
            output.textContent = String(Number(output.textContent) + 1);
          });
          document.querySelector("#delayed").addEventListener("click", () => {
            const output = document.querySelector("#clicked");
            output.textContent = String(Number(output.textContent) + 1);
          });
          document.querySelector("#replaceable").addEventListener("click", () => {
            document.querySelector("#recovered").textContent = "1";
          });
        </script>
        """

    @app.get("/destination", response_class=HTMLResponse)
    def destination_page() -> str:
        return """
        <!doctype html>
        <title>Destination</title>
        <h1>Arrived</h1>
        <script>
          function observeRestoredCursor() {
            const host = document.querySelector('#echo-agent-cursor-overlay-host');
            const cursor = host?.shadowRoot?.querySelector('#cursor');
            if (cursor?.dataset.visible === 'true') {
              document.documentElement.dataset.cursorRestored = 'true';
              return;
            }
            requestAnimationFrame(observeRestoredCursor);
          }
          requestAnimationFrame(observeRestoredCursor);
        </script>
        """

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    wait_until(lambda: server.started)

    context = None
    runtime = playwright.sync_playwright().start()
    try:
        context = launch_persistent_chromium(
            runtime.chromium,
            user_data_dir=str(tmp_path / "profile"),
            channel="chromium",
            headless=True,
            args=[
                f"--disable-extensions-except={extension}",
                f"--load-extension={extension}",
            ],
        )
    except playwright.Error as exc:
        runtime.stop()
        server.should_exit = True
        server_thread.join(timeout=5)
        pytest.skip(f"Chromium extension mode is unavailable: {exc}")

    try:
        yield f"http://127.0.0.1:{port}", context, extension, api_key
    finally:
        context.close()
        runtime.stop()
        server.should_exit = True
        server_thread.join(timeout=5)


def test_real_chrome_extension_observes_and_operates_active_tab(
    live_extension_runtime: tuple[str, Any, Path, str],
) -> None:
    base_url, context, _extension, _api_key = live_extension_runtime
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(f"{base_url}/fixture")

    status = wait_until(
        lambda: (
            current
            if (current := request_json(base_url, "/api/browser/relay/status")).get(
                "push_connected"
            )
            and "/fixture" in str(current.get("active_tab", {}).get("url", ""))
            else None
        ),
        timeout=10,
    )
    assert status["connected"] is True
    assert status["push_connected"] is True

    state = request_json(
        base_url,
        "/api/browser/relay/command",
        {"action": "state", "max_items": 10, "timeout_seconds": 5},
    )
    assert state["ok"] is True
    assert state["inputs"][0]["selector"] == "#query"
    assert state["inputs"][0]["value"] == "before"
    assert state["inputs"][1]["value"] is None

    page.evaluate(
        """() => {
          window.__echoCursorPhases = [];
          const attach = () => {
            const host = document.querySelector('#echo-agent-cursor-overlay-host');
            const cursor = host?.shadowRoot?.querySelector('#cursor');
            if (!cursor) {
              requestAnimationFrame(attach);
              return;
            }
            const record = () => {
              const phase = String(cursor.dataset.phase || '');
              if (phase && window.__echoCursorPhases.at(-1) !== phase) {
                window.__echoCursorPhases.push(phase);
              }
            };
            record();
            new MutationObserver(record).observe(cursor, {
              attributes: true,
              attributeFilter: ['data-phase'],
            });
          };
          requestAnimationFrame(attach);
        }"""
    )

    page.evaluate(
        """() => {
          const replacement = document.createElement("button");
          replacement.id = "replacement";
          replacement.textContent = "Save changes";
          replacement.addEventListener("click", () => {
            document.querySelector("#recovered").textContent = "1";
          });
          document.querySelector("#replaceable").replaceWith(replacement);
        }"""
    )
    skill_registry = SkillRegistry()
    register_browser_act_skills(skill_registry)
    recovered = skill_registry.get("live_browser_click").handler(selector="#replaceable")
    assert recovered["ok"] is True
    assert recovered["track"] == "extension"
    assert recovered["live_browser_fallback"]["to"] == "extension"
    assert recovered["selector"] == "#replacement"
    assert recovered["recoveredFromSelector"] == "#replaceable"
    assert page.locator("#recovered").text_content() == "1"

    command_result: dict[str, Any] = {}
    page.evaluate(
        """() => setTimeout(() => {
          const marker = document.createElement('div');
          marker.id = 'cursor-hold';
          document.body.appendChild(marker);
        }, 750)"""
    )

    def run_visible_wait() -> None:
        result = skill_registry.get("live_browser_wait").handler(
            selector="#cursor-hold",
            timeout=2_000,
        )
        command_result.update(result)

    wait_thread = threading.Thread(target=run_visible_wait)
    wait_thread.start()
    wait_until(
        lambda: page.evaluate(
            """() => {
              const host = document.querySelector('#echo-agent-cursor-overlay-host');
              const cursor = host?.shadowRoot?.querySelector('#cursor');
              return cursor?.dataset.visible === 'true' &&
                cursor?.textContent.includes('EchoAI');
            }"""
        ),
        timeout=5,
    )
    wait_thread.join(timeout=5)
    assert command_result["ok"] is True
    wait_until(
        lambda: page.evaluate(
            """() => document
              .querySelector('#echo-agent-cursor-overlay-host')
              ?.shadowRoot?.querySelector('#cursor')?.dataset.visible === 'false'"""
        ),
        timeout=3,
    )

    typed = request_json(
        base_url,
        "/api/browser/relay/command",
        {
            "action": "type",
            "selector": "#query",
            "text": "after",
            "clear": True,
            "timeout_seconds": 5,
        },
    )
    assert typed["ok"] is True
    assert typed["selector"] == "#query"
    assert typed["recoveredFromSelector"] is None
    assert typed["value"] == "after"
    assert page.locator("#query").input_value() == "after"
    assert page.title() == "Query: after"
    phases = wait_until(
        lambda: (
            observed
            if {"start", "move", "click", "type", "end"}
            <= set(observed := page.evaluate("() => window.__echoCursorPhases"))
            else None
        ),
        timeout=3,
    )
    assert {"start", "move", "click", "type", "end"} <= set(phases)
    assert page.evaluate(
        """() => {
          const host = document.querySelector('#echo-agent-cursor-overlay-host');
          const cursor = host?.shadowRoot?.querySelector('#cursor');
          return host?.style.pointerEvents === 'none' &&
            getComputedStyle(cursor).pointerEvents === 'none';
        }"""
    )

    pressed = request_json(
        base_url,
        "/api/browser/relay/command",
        {
            "action": "press",
            "selector": "#query",
            "key": "Enter",
            "timeout_seconds": 5,
        },
    )
    assert pressed["ok"] is True
    assert pressed["key"] == "Enter"
    assert page.locator("#submitted").text_content() == "1"

    page.evaluate(
        """() => {
          const button = document.querySelector("#delayed");
          const cover = document.querySelector("#cover");
          Object.assign(cover.style, {
            display: "block",
            position: "fixed",
            inset: "0",
            zIndex: "9999",
            background: "white",
          });
          cover.hidden = false;
          setTimeout(() => {
            button.disabled = false;
            cover.remove();
          }, 180);
        }"""
    )
    clicked = request_json(
        base_url,
        "/api/browser/relay/command",
        {
            "action": "click",
            "selector": "#delayed",
            "timeout": 2_000,
            "timeout_seconds": 5,
        },
    )
    assert clicked["ok"] is True
    assert clicked["selector"] == "#delayed"
    assert page.locator("#clicked").text_content() == "1"

    navigated = request_json(
        base_url,
        "/api/browser/relay/command",
        {
            "action": "click",
            "selector": "#navigate",
            "timeout": 2_000,
            "timeout_seconds": 5,
        },
    )
    assert navigated["ok"] is True
    assert navigated["navigationObserved"] is True
    assert navigated["url"].endswith("/destination")
    page.wait_for_url("**/destination")
    assert page.title() == "Destination"
    wait_until(
        lambda: page.locator("html").get_attribute("data-cursor-restored") == "true",
        timeout=3,
    )


@pytest.mark.parametrize("live_extension_runtime", [True], indirect=True)
def test_sidepanel_pairing_connects_extension_to_authenticated_gateway(
    live_extension_runtime: tuple[str, Any, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url, context, _extension, api_key = live_extension_runtime
    extension_id = loaded_extension_id(context)

    sidepanel = context.new_page()
    sidepanel.set_viewport_size({"width": 420, "height": 800})
    sidepanel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    sidepanel.get_by_role("button", name="新建对话").click()
    assert sidepanel.locator("#promptInput").evaluate(
        "element => document.activeElement === element"
    )
    auth_toggle = sidepanel.get_by_role("button", name="连接密钥设置")
    assert auth_toggle.get_attribute("aria-expanded") == "false"
    auth_toggle.click()
    assert auth_toggle.get_attribute("aria-expanded") == "true"
    composer_box = sidepanel.locator("#composer").bounding_box()
    prompt_box = sidepanel.locator("#promptInput").bounding_box()
    save_box = sidepanel.locator("#authSaveButton").bounding_box()
    messages_box = sidepanel.locator("#messages").bounding_box()
    assert composer_box is not None and composer_box["height"] <= 160
    assert composer_box["y"] + composer_box["height"] <= 800
    assert prompt_box is not None and prompt_box["height"] <= 100
    assert save_box is not None and save_box["height"] <= 44
    assert messages_box is not None and messages_box["height"] >= 180

    sidepanel.locator("#authTokenInput").fill("invalid-key")
    sidepanel.locator('#authForm button[type="submit"]').click()
    sidepanel.locator("#authStatus").get_by_text(
        "密钥已保存，但未能连接。请检查密钥或确认 EchoOS 主控已启动。"
    ).wait_for(timeout=10_000)
    assert sidepanel.locator("#authStatus").get_attribute("data-tone") == "error"
    assert sidepanel.locator("#authSaveButton").is_enabled()

    sidepanel.locator("#authTokenInput").fill(api_key)
    sidepanel.locator('#authForm button[type="submit"]').click()
    sidepanel.locator("#authStatus").get_by_text(
        "连接密钥已验证，Chrome Relay 已连接。"
    ).wait_for(timeout=10_000)
    assert sidepanel.locator("#authStatus").get_attribute("data-tone") == "success"

    page = context.new_page()
    long_source = "x" * 240
    page.goto(f"{base_url}/fixture?source={long_source}")
    status = wait_until(
        lambda: (
            current
            if (
                current := request_json(
                    base_url,
                    "/api/browser/relay/status",
                    token=api_key,
                )
            ).get("push_connected")
            and "/fixture" in str(current.get("active_tab", {}).get("url", ""))
            else None
        ),
        timeout=10,
    )
    assert status["connected"] is True
    sidepanel.wait_for_function(
        "source => document.querySelector('#tabUrl')?.textContent.includes(source)",
        arg=long_source,
        timeout=5_000,
    )
    assert sidepanel.evaluate(
        "() => document.documentElement.scrollWidth === document.documentElement.clientWidth"
    )

    state = request_json(
        base_url,
        "/api/browser/relay/command",
        {"action": "state", "max_items": 10, "timeout_seconds": 5},
        token=api_key,
    )
    assert state["ok"] is True
    assert state["inputs"][0]["selector"] == "#query"

    monkeypatch.setenv("ECHO_BROWSER_RELAY_BASE_URL", base_url)
    monkeypatch.setenv("ECHO_BROWSER_RELAY_TOKEN", api_key)
    backend = ExtensionBackend()
    assert backend.available() is True
    backend_state = backend.state(max_items=10)
    assert backend_state.ok is True
    assert backend_state.track is Track.EXTENSION
    assert backend_state.data is not None
    assert backend_state.data["inputs"][0]["selector"] == "#query"




