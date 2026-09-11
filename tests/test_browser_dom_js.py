"""Shared browser DOM perception JS (browser_dom_js).

The snippet runs inside the page, so Python tests pin its structure
and — when Node is available — validate the syntax for real. The two
consumers (Electron bridge IIFE, Playwright evaluate function) must
both embed the same helpers.
"""

import shutil
import subprocess

import pytest

from runtime.execution.suckers.browser_dom_js import (
    DOM_HELPERS_JS,
    dom_snapshot_function_js,
    dom_state_iife_js,
)


class TestSelectorGeneration:
    def test_priority_candidates_present(self):
        # data-testid outranks id outranks aria-label outranks name —
        # measured within selectorFor's candidate section only (textOf
        # above it also mentions aria-label).
        body = DOM_HELPERS_JS[DOM_HELPERS_JS.index("const selectorFor") :]
        order = [
            body.index("data-testid"),
            body.index("CSS.escape(el.id)"),
            body.index("aria-label"),
            body.index("'name'"),
        ]
        assert order == sorted(order)

    def test_uniqueness_is_verified_not_assumed(self):
        assert "uniqueMatch" in DOM_HELPERS_JS
        assert "querySelectorAll" in DOM_HELPERS_JS
        # Structural fallback exists for pages without stable hooks.
        assert "nth-of-type" in DOM_HELPERS_JS


class TestPerceptionFields:
    def test_interaction_state_is_exposed(self):
        for field in ("disabled", "checked", "role", "ariaLabel", "value"):
            assert f"{field}:" in DOM_HELPERS_JS

    def test_password_gate_present_in_value_line(self):
        # Cheap structural guard (the behavioural test below proves it
        # actually works, and catches a !== -> === inversion).
        value_line = next(line for line in DOM_HELPERS_JS.splitlines() if "value:" in line)
        assert "password" in value_line


class TestWrappers:
    def test_iife_carries_limit_and_page_meta(self):
        code = dom_state_iife_js(25)
        assert "const limit = 25;" in code
        assert "location.href" in code
        assert code.startswith("(() => {") and code.endswith("})()")

    def test_iife_clamps_to_int(self):
        assert "const limit = 7;" in dom_state_iife_js(7)

    def test_playwright_function_takes_limit_param(self):
        code = dom_snapshot_function_js()
        assert code.startswith("(limit) => {")
        assert "pickElements" in code

    def test_both_wrappers_embed_the_same_helpers(self):
        iife = dom_state_iife_js(10)
        fn = dom_snapshot_function_js()
        for marker in ("uniqueMatch", "selectorFor", "describe", "pickElements"):
            assert marker in iife
            assert marker in fn


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestJsSyntax:
    def _check(self, code: str) -> None:
        proc = subprocess.run(
            ["node", "-e", "new Function(process.argv[1]); console.log('ok')", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr

    def test_iife_parses(self):
        self._check(dom_state_iife_js(30))

    def test_playwright_function_parses(self):
        self._check("(" + dom_snapshot_function_js() + ")(10)")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestDescribeBehaviour:
    """Run describe() under Node with stubbed DOM globals — proves the
    redaction/uniqueness LOGIC, not just token presence."""

    def _run(self, snippet: str) -> str:
        harness = (
            "const el0 = null;\n"
            "global.window = { getComputedStyle: () => ({}), innerWidth: 800, innerHeight: 600, scrollX: 0, scrollY: 0 };\n"
            "global.CSS = { escape: (s) => s };\n"
            "let LAST = [];\n"
            "global.document = { querySelectorAll: () => LAST, title: 't', body: { innerText: '' } };\n"
            "const setMatches = (arr) => { LAST = arr; };\n" + DOM_HELPERS_JS + "\n" + snippet
        )
        proc = subprocess.run(
            ["node", "-e", harness],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    def test_password_value_redacted_when_type_is_property(self):
        # type set via the DOM PROPERTY (el.type), attribute returns
        # null — the exact case a getAttribute-only gate would leak.
        out = self._run(
            "const pw = { tagName:'INPUT', type:'password', value:'s3cret',"
            " getAttribute:()=>null, getBoundingClientRect:()=>({width:1,height:1}) };\n"
            "setMatches([pw]);\n"
            "const d = describe(pw);\n"
            "if (d.value !== null) { console.error('LEAKED:'+d.value); process.exit(1); }\n"
            "console.log('redacted');\n"
        )
        assert out == "redacted"

    def test_text_value_preserved(self):
        out = self._run(
            "const tx = { tagName:'INPUT', type:'text', value:'hello',"
            " getAttribute:()=>null, getBoundingClientRect:()=>({width:1,height:1}) };\n"
            "setMatches([tx]);\n"
            "const d = describe(tx);\n"
            "console.log(d.value);\n"
        )
        assert out == "hello"

    def test_non_unique_selector_flagged(self):
        # Two identical attribute-less buttons → selectorFor cannot
        # produce a unique selector → selectorUnique must be false.
        out = self._run(
            "const mk = () => ({ tagName:'BUTTON', getAttribute:()=>null,"
            " getBoundingClientRect:()=>({width:1,height:1}), parentElement:null });\n"
            "const a = mk(), b = mk();\n"
            "setMatches([a, b]);\n"  # querySelectorAll always returns both → never unique
            "const d = describe(a);\n"
            "console.log(String(d.selectorUnique));\n"
        )
        assert out == "false"
