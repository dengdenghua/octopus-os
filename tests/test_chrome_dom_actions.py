from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from runtime.execution.suckers.browser_launch import (
    launch_chromium,
)

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
DOM_ACTIONS = (ROOT / "extensions" / "echo-browser-relay" / "dom-actions.js").read_text(
    encoding="utf-8"
)


@pytest.fixture(scope="module")
def browser_page() -> Iterator[Any]:
    with playwright.sync_playwright() as runtime:
        try:
            browser = launch_chromium(runtime.chromium, headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Chromium is unavailable: {exc}")
        page = browser.new_page()
        yield page
        browser.close()


def load_actions(page: Any, html: str) -> None:
    page.set_content(html)
    page.add_script_tag(content=DOM_ACTIONS)


def run_action(page: Any, action: str, params: dict[str, Any]) -> dict[str, Any]:
    return page.evaluate(
        "([action, params]) => globalThis.__ECHO_DOM_ACTIONS__.run(action, params)",
        [action, params],
    )


def test_dom_actions_edit_contenteditable_and_select(browser_page: Any) -> None:
    load_actions(
        browser_page,
        """
        <div id="editor" contenteditable="true">old</div>
        <select id="priority">
          <option value="low">Low</option>
          <option value="high">High</option>
        </select>
        <script>
          window.seenEvents = [];
          for (const type of ["beforeinput", "input", "change"]) {
            document.addEventListener(type, event => {
              window.seenEvents.push(`${event.target.id}:${type}`);
            });
          }
        </script>
        """,
    )

    replaced = run_action(
        browser_page,
        "type",
        {"selector": "#editor", "text": "new", "clear": True},
    )
    appended = run_action(
        browser_page,
        "type",
        {"selector": "#editor", "text": " text", "clear": False},
    )
    selected = run_action(
        browser_page,
        "type",
        {"selector": "#priority", "text": "High", "clear": True},
    )

    assert replaced["value"] == "new"
    assert appended["value"] == "new text"
    assert selected["value"] == "high"
    events = browser_page.evaluate("window.seenEvents")
    assert "editor:beforeinput" in events
    assert "editor:input" in events
    assert "priority:change" in events


def test_dom_actions_wait_for_visible_text_and_detachment(browser_page: Any) -> None:
    load_actions(
        browser_page,
        """
        <div id="result" style="display:none">Loading</div>
        <script>
          setTimeout(() => {
            const result = document.querySelector("#result");
            result.textContent = "Ready";
            result.style.display = "block";
          }, 80);
          setTimeout(() => document.querySelector("#result")?.remove(), 220);
        </script>
        """,
    )

    visible = run_action(
        browser_page,
        "wait",
        {"selector": "#result", "state": "visible", "text": "Ready", "timeout": 1_000},
    )
    detached = run_action(
        browser_page,
        "wait",
        {"selector": "#result", "state": "detached", "timeout": 1_000},
    )

    assert visible["ok"] is True
    assert detached["ok"] is True


def test_dom_actions_reject_hidden_or_disabled_targets(browser_page: Any) -> None:
    load_actions(
        browser_page,
        """
        <button id="hidden" style="display:none">Hidden</button>
        <input id="disabled" disabled>
        """,
    )

    with pytest.raises(playwright.Error, match="click timed out.*not visible"):
        run_action(browser_page, "click", {"selector": "#hidden", "timeout": 100})
    with pytest.raises(playwright.Error, match="type timed out.*disabled"):
        run_action(
            browser_page,
            "type",
            {
                "selector": "#disabled",
                "text": "unsafe",
                "clear": True,
                "timeout": 100,
            },
        )


def test_dom_actions_auto_wait_for_enabled_uncovered_stable_target(browser_page: Any) -> None:
    load_actions(
        browser_page,
        """
        <button id="delayed" disabled>Continue</button>
        <div id="cover" style="position:fixed;inset:0;z-index:9999;background:white"></div>
        <output id="clicks">0</output>
        <script>
          document.querySelector("#delayed").addEventListener("click", () => {
            const clicks = document.querySelector("#clicks");
            clicks.textContent = String(Number(clicks.textContent) + 1);
          });
          setTimeout(() => {
            document.querySelector("#delayed").disabled = false;
            document.querySelector("#cover").remove();
          }, 120);
        </script>
        """,
    )

    result = run_action(
        browser_page,
        "click",
        {"selector": "#delayed", "timeout": 1_000},
    )

    assert result["selector"] == "#delayed"
    assert browser_page.locator("#clicks").text_content() == "1"


def test_dom_actions_recovers_unique_semantic_target_after_dom_replacement(
    browser_page: Any,
) -> None:
    load_actions(
        browser_page,
        """
        <button id="old-save">Save changes</button>
        <output id="clicks">0</output>
        """,
    )
    run_action(browser_page, "state", {"max_items": 10})
    browser_page.evaluate(
        """() => {
          const replacement = document.createElement("button");
          replacement.id = "new-save";
          replacement.textContent = "Save changes";
          replacement.addEventListener("click", () => {
            document.querySelector("#clicks").textContent = "1";
          });
          document.querySelector("#old-save").replaceWith(replacement);
        }"""
    )

    result = run_action(
        browser_page,
        "click",
        {"selector": "#old-save", "timeout": 500},
    )

    assert result["selector"] == "#new-save"
    assert result["recoveredFromSelector"] == "#old-save"
    assert browser_page.locator("#clicks").text_content() == "1"


def test_dom_actions_refuses_ambiguous_semantic_recovery(browser_page: Any) -> None:
    load_actions(
        browser_page,
        """
        <button id="old-save">Save changes</button>
        <output id="clicks">0</output>
        """,
    )
    run_action(browser_page, "state", {"max_items": 10})
    browser_page.evaluate(
        """() => {
          document.querySelector("#old-save").remove();
          for (let index = 0; index < 2; index += 1) {
            const button = document.createElement("button");
            button.textContent = "Save changes";
            button.addEventListener("click", () => {
              document.querySelector("#clicks").textContent = "1";
            });
            document.body.append(button);
          }
        }"""
    )

    with pytest.raises(playwright.Error, match="semantic recovery is ambiguous"):
        run_action(
            browser_page,
            "click",
            {"selector": "#old-save", "timeout": 100},
        )

    assert browser_page.locator("#clicks").text_content() == "0"


def test_dom_actions_state_matches_extension_backend_contract(browser_page: Any) -> None:
    load_actions(
        browser_page,
        """
        <h1>Settings</h1>
        <button data-testid="save">Save</button>
        <input aria-label="Email" value="person@example.test">
        <input aria-label="Password" type="password" value="secret">
        """,
    )

    state = run_action(browser_page, "state", {"max_items": 10})

    assert state["ok"] is True
    assert state["headings"][0]["name"] == "Settings"
    assert state["buttons"][0]["selector"] == '[data-testid="save"]'
    assert state["buttons"][0]["selectorUnique"] is True
    assert state["inputs"][0]["value"] == "person@example.test"
    assert state["inputs"][1]["value"] is None


