"""Shared in-page JavaScript for browser perception.

Both automation paths — the Electron live-browser bridge
(``browser_act_skills._h_state``) and the Playwright skills
(``browser_skills``) — inject the same DOM-walking snippet. Keeping it
here prevents the two copies from drifting (they had already diverged
once) and gives the agent identical perception either way.

Design notes:

* ``selectorFor`` VERIFIES uniqueness: every candidate is checked with
  ``querySelectorAll`` and only returned when it matches exactly the
  target element. Priority: data-testid > #id > [aria-label] > [name],
  then a structural ``tag:nth-of-type`` chain walked upwards until it
  becomes unique. The old generator returned bare tag names as a
  fallback — useless on any page with two buttons.
* ``describe`` carries the interaction-critical state the agent was
  blind to: ``disabled``, current ``value`` (NEVER for password
  fields), ``checked``, ``role``, ``ariaLabel``. "Type into the box,
  then re-read state" now actually shows whether the input landed.
"""

from __future__ import annotations

# Plain string (not an f-string) — consumers wrap it, so there is no
# brace-escaping minefield here.
DOM_HELPERS_JS = r"""
const textOf = (el) => (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
const escAttr = (v) => String(v).replaceAll('\\', '\\\\').replaceAll('"', '\\"');
const uniqueMatch = (sel, el) => {
    try {
        const found = document.querySelectorAll(sel);
        return found.length === 1 && found[0] === el;
    } catch (e) {
        return false;
    }
};
const selectorFor = (el) => {
    const tag = el.tagName.toLowerCase();
    const candidates = [];
    const testid = el.getAttribute('data-testid');
    if (testid) candidates.push(`[data-testid="${escAttr(testid)}"]`);
    if (el.id) candidates.push(`#${CSS.escape(el.id)}`);
    const aria = el.getAttribute('aria-label');
    if (aria) candidates.push(`${tag}[aria-label="${escAttr(aria)}"]`);
    const name = el.getAttribute('name');
    if (name) candidates.push(`${tag}[name="${escAttr(name)}"]`);
    for (const sel of candidates) {
        if (uniqueMatch(sel, el)) return sel;
    }
    // Structural fallback: tag:nth-of-type chain, extended upwards
    // until unique. 8 levels is generous enough to disambiguate deep
    // trees while keeping selectors bounded.
    let node = el;
    const path = [];
    while (node && node.nodeType === 1 && path.length < 8) {
        let part = node.tagName.toLowerCase();
        const parent = node.parentElement;
        if (parent) {
            const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
            if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
        }
        path.unshift(part);
        const sel = path.join(' > ');
        if (uniqueMatch(sel, el)) return sel;
        node = parent;
    }
    // Could not produce a verified-unique selector. Return the
    // best-effort string anyway — describe() flags it via
    // selectorUnique:false so the agent knows it may match siblings
    // and should not blindly act on it.
    return candidates[0] || path.join(' > ') || tag;
};
const typeOf = (el) => (el.type || el.getAttribute('type') || '').toLowerCase();
const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
};
const describe = (el) => {
    // typeOf() reads the .type DOM PROPERTY first, then the attribute.
    // A show/hide-password toggle or a JS-built form sets type via the
    // property; getAttribute('type') returns null there, which would
    // leak the password value. Property-first closes that gap.
    const t = typeOf(el);
    const sel = selectorFor(el);
    return {
        tag: el.tagName.toLowerCase(),
        text: textOf(el).slice(0, 120),
        selector: sel,
        selectorUnique: uniqueMatch(sel, el),
        href: el.href || null,
        type: t || null,
        placeholder: el.getAttribute('placeholder') || null,
        disabled: (el.disabled === true || el.getAttribute('aria-disabled') === 'true') ? true : null,
        value: (typeof el.value === 'string' && el.value && t !== 'password') ? el.value.slice(0, 80) : null,
        checked: (typeof el.checked === 'boolean' && ['checkbox', 'radio'].includes(t)) ? el.checked : null,
        role: el.getAttribute('role') || null,
        ariaLabel: el.getAttribute('aria-label') || null
    };
};
const pickElements = (selector, limit) => Array.from(document.querySelectorAll(selector))
    .filter(visible)
    .slice(0, limit)
    .map(describe);
"""

# Body shared by both wrappers: the perception payload itself.
_SNAPSHOT_BODY_JS = r"""
return {
    viewport: { width: window.innerWidth, height: window.innerHeight },
    scroll: { x: window.scrollX, y: window.scrollY },
    links: pickElements('a[href]', limit),
    buttons: pickElements('button,[role="button"],input[type="button"],input[type="submit"]', limit),
    inputs: pickElements('input,textarea,select', limit),
    headings: pickElements('h1,h2,h3', limit)
};
"""


def dom_state_iife_js(max_items: int) -> str:
    """Self-executing snippet for the Electron bridge's execute-JS
    channel. Adds url/title/text_length, which the bridge cannot get
    any other way."""
    return (
        "(() => {\n"
        f"const limit = {int(max_items)};\n"
        + DOM_HELPERS_JS
        + "\nconst bodyText = document.body?.innerText || document.body?.textContent || '';\n"
        + "const snapshot = (() => {"
        + _SNAPSHOT_BODY_JS
        + "})();\n"
        + "return Object.assign({\n"
        + "    url: location.href,\n"
        + "    title: document.title,\n"
        + "    text_length: bodyText.length\n"
        + "}, snapshot);\n"
        + "})()"
    )


def dom_snapshot_function_js() -> str:
    """``(limit) => {...}`` function source for Playwright's
    ``page.evaluate(fn, max_items)``. url/title/status come from the
    Page object on the Python side."""
    return "(limit) => {\n" + DOM_HELPERS_JS + _SNAPSHOT_BODY_JS + "\n}"
