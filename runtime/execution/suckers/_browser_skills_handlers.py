"""Registrar for browser_skills · extracted from browser_skills.py.

Contains only ``register_browser_skills`` and ``BROWSER_SKILL_NAMES``.
All handler functions remain in ``browser_skills``.

Import order: ``browser_skills`` defines all handlers first, THEN imports
``register_browser_skills`` from this submodule at the bottom of the file.
When this module is loaded, ``browser_skills`` is already in
``sys.modules`` with all handlers defined, so the imports below succeed.
"""

from __future__ import annotations

from . import browser_skills as _bs
from .browser_skills import (
    _browser_click,
    _browser_extract,
    _browser_find,
    _browser_get,
    _browser_navigate,
    _browser_screenshot,
    _browser_scroll,
    _browser_state,
    _browser_type,
    _browser_upload,
    _browser_wait,
)
from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

BROWSER_SKILL_NAMES = [
    "browser_get",
    "browser_extract",
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_upload",
    "browser_scroll",
    "browser_wait",
    "browser_screenshot",
    "browser_find",
    "browser_state",
]


def register_browser_skills(
    registry: SkillRegistry,
    *,
    verify_tests: bool = True,
) -> int:
    if not _bs.PLAYWRIGHT_AVAILABLE:
        return 0

    def _register(skill: Skill) -> None:
        registry.register(skill, verify_tests=verify_tests)

    _register(
        Skill(
            name="browser_get",
            description=(
                "Read rendered title, body text, and child-frame text. Pass url to navigate "
                "first, or omit url to read the current persistent agent browser page; use "
                "wait_ms before reading delayed UI or iframe confirmations."
            ),
            affinity=["web", "browser", "io"],
            cost_profile="high",
            trusted_source="skill://public/browser_get",
            handler=_browser_get,
            tests=[
                SkillTestCase(
                    name="missing_url_returns_error",
                    tier="golden",
                    args={"url": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    _register(
        Skill(
            name="browser_extract",
            description=(
                "Extract CSS matches (text or attr). Pass url to navigate, or omit it to "
                "use the current persistent agent browser page."
            ),
            affinity=["web", "browser", "scrape"],
            cost_profile="high",
            trusted_source="skill://public/browser_extract",
            handler=_browser_extract,
            tests=[
                SkillTestCase(
                    name="missing_selector_returns_error",
                    tier="golden",
                    args={"url": "https://example.com", "selector": ""},
                    expect=SkillExpect(schema_keys=["error", "items"]),
                ),
            ],
        )
    )
    _register(
        Skill(
            name="browser_navigate",
            description=(
                "Navigate the persistent agent browser page to a URL and return final URL + status."
            ),
            affinity=["web", "browser", "nav"],
            cost_profile="high",
            trusted_source="skill://public/browser_navigate",
            handler=_browser_navigate,
            tests=[
                SkillTestCase(
                    name="missing_url_error",
                    tier="golden",
                    args={"url": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    _register(
        Skill(
            name="browser_click",
            description=(
                "Click a CSS selector on the current persistent page; url is optional and "
                "navigates first when supplied."
            ),
            affinity=["web", "browser", "interact"],
            cost_profile="high",
            trusted_source="skill://public/browser_click",
            handler=_browser_click,
            tests=[
                SkillTestCase(
                    name="missing_selector_error",
                    tier="golden",
                    args={"url": "https://example.com", "selector": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    _register(
        Skill(
            name="browser_type",
            description=(
                "Fill an input/contenteditable or choose a native select option by visible "
                "label/value on the current persistent page; url is optional and navigates "
                "first when supplied. Pass content as text (value and option_label are also "
                "accepted aliases)."
            ),
            affinity=["web", "browser", "interact"],
            cost_profile="high",
            trusted_source="skill://public/browser_type",
            handler=_browser_type,
            tests=[
                SkillTestCase(
                    name="missing_selector_error",
                    tier="golden",
                    args={
                        "url": "https://example.com",
                        "selector": "",
                        "text": "x",
                    },
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    _register(
        Skill(
            name="browser_upload",
            description=(
                "Upload a workspace file into a CSS-selected file input on the current "
                "persistent page. Pass selector and path; url is optional after navigation."
            ),
            affinity=["web", "browser", "interact", "read"],
            cost_profile="high",
            trusted_source="skill://public/browser_upload",
            handler=_browser_upload,
            tests=[
                SkillTestCase(
                    name="missing_path_error",
                    tier="golden",
                    args={"selector": "#file", "path": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    _register(
        Skill(
            name="browser_scroll",
            description="Navigate and scroll to element or Y coordinate.",
            affinity=["web", "browser", "interact"],
            cost_profile="high",
            trusted_source="skill://public/browser_scroll",
            handler=_browser_scroll,
            tests=[
                SkillTestCase(
                    name="missing_target_error",
                    tier="golden",
                    args={"url": "https://example.com"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    _register(
        Skill(
            name="browser_wait",
            description=(
                "Wait on the current persistent page for a CSS selector state; url is optional."
            ),
            affinity=["web", "browser", "interact"],
            cost_profile="high",
            trusted_source="skill://public/browser_wait",
            handler=_browser_wait,
            tests=[
                SkillTestCase(
                    name="bad_state_error",
                    tier="golden",
                    args={
                        "url": "https://example.com",
                        "selector": "body",
                        "state": "floating",
                    },
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    _register(
        Skill(
            name="browser_screenshot",
            description="Navigate and save a page screenshot (path_guard enforced).",
            affinity=["web", "browser", "capture"],
            cost_profile="high",
            trusted_source="skill://public/browser_screenshot",
            handler=_browser_screenshot,
            tests=[
                SkillTestCase(
                    name="missing_path_error",
                    tier="golden",
                    args={"url": "https://example.com", "path": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    _register(
        Skill(
            name="browser_find",
            description="Navigate to a page and find text, returning match snippets.",
            affinity=["web", "browser", "find"],
            cost_profile="high",
            trusted_source="skill://public/browser_find",
            handler=_browser_find,
            tests=[
                SkillTestCase(
                    name="missing_text_error",
                    tier="golden",
                    args={"url": "https://example.com", "text": ""},
                    expect=SkillExpect(schema_keys=["error", "matches"]),
                ),
            ],
        )
    )
    _register(
        Skill(
            name="browser_state",
            description=(
                "Return persistent browser state (optionally navigate with url): title, URL, "
                "viewport, scroll, child-frame text, and visible links/buttons/inputs/headings."
            ),
            affinity=["web", "browser", "observe"],
            cost_profile="high",
            trusted_source="skill://public/browser_state",
            handler=_browser_state,
            tests=[
                SkillTestCase(
                    name="missing_url_error",
                    tier="golden",
                    args={"url": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    return 11
