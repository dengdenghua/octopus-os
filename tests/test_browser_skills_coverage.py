"""Dense coverage for browser_skills pure helpers (audit Q-05)."""

from __future__ import annotations

from runtime.execution.suckers import browser_skills as bs


def test_check_url_safe(monkeypatch) -> None:
    from types import SimpleNamespace

    import runtime.safety.auth.url_guard as ug

    # Deterministic real-DNS case: link-local metadata is always blocked.
    blocked = bs._check_url_safe("http://169.254.169.254/latest/meta-data/", allow_private=False)
    assert blocked is not None and "ssrf_blocked" in blocked
    assert bs._check_url_safe("", False) == "missing url"

    # Wrapper logic with a stubbed guard (real DNS is env-dependent).
    monkeypatch.setattr(
        ug, "check_url", lambda url, allow_private: SimpleNamespace(allow=True, reason="")
    )
    assert bs._check_url_safe("https://example.com/", False) is None
    monkeypatch.setattr(
        ug,
        "check_url",
        lambda url, allow_private: SimpleNamespace(allow=False, reason="private_ip"),
    )
    denied = bs._check_url_safe("http://127.0.0.1/", allow_private=True)
    assert denied is not None and "private_ip" in denied


def test_has_agent_browser_session(monkeypatch) -> None:
    monkeypatch.setattr(bs, "PLAYWRIGHT_AVAILABLE", False)
    assert bs._has_agent_browser_session() is False
    monkeypatch.setattr(bs, "PLAYWRIGHT_AVAILABLE", True)
    import runtime.platform.process.session as sess

    monkeypatch.setattr(sess, "current_session", lambda: object())
    assert bs._has_agent_browser_session() is True
    monkeypatch.setattr(sess, "current_session", lambda: None)
    assert bs._has_agent_browser_session() is False

