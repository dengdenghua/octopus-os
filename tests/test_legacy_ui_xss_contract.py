"""Stored-XSS regression contracts for the zero-build legacy HTML pages."""

from html import escape

from runtime.platform.ui._chat_page_html import _CHAT_HTML
from runtime.platform.ui.pages import _INDEX_HTML, _REFLEX_PANEL_HTML

_PAYLOAD = '<img src=x onerror="sessionStorage.clear()">'


def _assert_standard_escape_helper(page: str) -> None:
    """The inline helper must encode text and attribute-breaking characters."""

    assert "replace(/[&<>\"']/g" in page
    for encoded in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert encoded in page

    # Concrete attacker payload documents what these contracts protect.  The
    # dynamic paths below must feed it through the JS helper (or textContent),
    # yielding text rather than a live image/error handler.
    escaped = escape(_PAYLOAD, quote=True).replace("&#x27;", "&#39;")
    assert escaped == "&lt;img src=x onerror=&quot;sessionStorage.clear()&quot;&gt;"
    assert "<img" not in escaped


def test_dashboard_external_fields_are_escaped_before_inner_html() -> None:
    _assert_standard_escape_helper(_INDEX_HTML)

    # Remote registry skill metadata flows through metric(); metric owns the
    # escaping so every status/journal/reflection caller inherits it.
    assert "metric(s.name, s.cost_profile)" in _INDEX_HTML
    assert "${escapeHtml(label)}" in _INDEX_HTML
    assert "${escapeHtml(value)}" in _INDEX_HTML
    assert "${escapeHtml(r.error)}" in _INDEX_HTML

    # Task/node fields may originate in model-driven execution state.
    for expression in (
        "escapeHtml(t.status)",
        "escapeHtml(String(t.task_id ?? '').slice(0, 8))",
        "escapeHtml(t.strategy)",
        "escapeHtml(t.current_node_id)",
        "escapeHtml(t.nodes_completed)",
        "escapeHtml(t.total_nodes)",
    ):
        assert expression in _INDEX_HTML


def test_reflex_panel_escapes_rule_variant_action_and_error_fields() -> None:
    _assert_standard_escape_helper(_REFLEX_PANEL_HTML)

    for expression in (
        "escapeHtml(a)",
        "escapeHtml(v.variant_id)",
        "escapeHtml(v.preview)",
        "escapeHtml(r.rule_id)",
        "escapeHtml(r.kind)",
        "escapeHtml(pat)",
    ):
        assert expression in _REFLEX_PANEL_HTML

    assert "function setReloadMessage(message, kind)" in _REFLEX_PANEL_HTML
    assert "element.textContent = String(message ?? '')" in _REFLEX_PANEL_HTML
    assert "msg.innerHTML" not in _REFLEX_PANEL_HTML
    assert "reload-msg').innerHTML" not in _REFLEX_PANEL_HTML


def test_chat_and_login_dynamic_labels_icons_and_errors_are_not_html() -> None:
    _assert_standard_escape_helper(_CHAT_HTML)

    for expression in (
        "${escapeHtml(routeTxt)}",
        "${escapeHtml(agentIcon)}",
        "${escapeHtml(icon)}",
        "${escapeHtml(a.icon || '🤖')}",
        'placeholder="${escapeHtml(isOct ?',
    ):
        assert expression in _CHAT_HTML

    # API error bodies use a created text node, including both SMS and local
    # login flows; they never become an executable innerHTML fragment.
    assert "function renderErrorMessage(container, message)" in _CHAT_HTML
    assert "error.textContent = String(message)" in _CHAT_HTML
    assert "renderErrorMessage(errBox, explainErr(e))" in _CHAT_HTML
    assert '<div class="err">${e.message}</div>' not in _CHAT_HTML
    assert '<div class="err">${explainErr(e)}</div>' not in _CHAT_HTML

