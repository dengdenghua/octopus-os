"""Tests for the persistent thread-affine browser session worker/pool.

The concurrency primitive is exercised with an injected fake page factory (no
browser needed); one end-to-end test uses real Playwright to prove a page's
state survives across submits (the whole point: multi-step agent flows).
"""

from __future__ import annotations

import time

import pytest

from runtime.execution.suckers.browser_session_worker import (
    BrowserSessionPool,
    BrowserSessionWorker,
)
from tests.conftest import requires_chromium


def _counting_factory():
    state = {"created": 0, "closed": 0}

    def factory():
        state["created"] += 1
        page = object()  # opaque stand-in for a Playwright Page

        def _close():
            state["closed"] += 1

        return page, _close

    return factory, state


# ── BrowserSessionWorker ──────────────────────────────────


def test_worker_reuses_one_page_across_submits():
    factory, state = _counting_factory()
    w = BrowserSessionWorker(page_factory=factory)
    try:
        p1 = w.submit(lambda page: page)
        p2 = w.submit(lambda page: page)
        assert p1 is p2  # SAME page across calls (persistence)
        assert state["created"] == 1  # launched exactly once, lazily
    finally:
        w.close()
    assert state["closed"] == 1  # closed on the owner thread
    assert w.closed is True


def test_worker_lazy_no_page_until_first_submit():
    factory, state = _counting_factory()
    w = BrowserSessionWorker(page_factory=factory)
    try:
        assert state["created"] == 0  # constructing the worker does NOT launch
    finally:
        w.close()
    assert state["created"] == 0  # never used → never launched → no close


def test_worker_op_exception_propagates_to_caller():
    factory, _ = _counting_factory()
    w = BrowserSessionWorker(page_factory=factory)

    def _boom(_page):
        raise ValueError("op blew up")

    try:
        with pytest.raises(ValueError, match="op blew up"):
            w.submit(_boom)
        # worker survives a failed op and keeps serving
        assert w.submit(lambda page: "ok") == "ok"
    finally:
        w.close()


def test_worker_submit_after_close_raises():
    factory, _ = _counting_factory()
    w = BrowserSessionWorker(page_factory=factory)
    w.submit(lambda page: page)
    w.close()
    with pytest.raises(RuntimeError, match="closed"):
        w.submit(lambda page: page)


def test_op_timeout_retires_worker():
    # An op that outlives op_timeout_s → caller gets TimeoutError AND the worker
    # is retired (closed) so its busy/half-mutated page is never reused.
    import threading as _t

    factory, _ = _counting_factory()
    w = BrowserSessionWorker(page_factory=factory, op_timeout_s=0.1)
    gate = _t.Event()
    try:
        with pytest.raises(TimeoutError):
            w.submit(lambda page: gate.wait(5))  # blocks worker past the timeout
        assert w.closed is True
    finally:
        gate.set()  # release the worker thread so it can tear down
        w.close()


# ── BrowserSessionPool ────────────────────────────────────


def test_pool_same_key_same_worker():
    factory, _ = _counting_factory()
    pool = BrowserSessionPool(page_factory=factory)
    try:
        a1 = pool.get_or_create("sess-a")
        a2 = pool.get_or_create("sess-a")
        b = pool.get_or_create("sess-b")
        assert a1 is a2
        assert b is not a1
        assert pool.active_count() == 2
    finally:
        pool.close_all()


def test_pool_idle_reaping_closes_workers():
    factory, state = _counting_factory()
    pool = BrowserSessionPool(
        page_factory=factory,
        idle_ttl_s=0.05,
        reap_interval_s=0.05,
    )
    try:
        w = pool.get_or_create("sess")
        w.submit(lambda page: page)  # force a launch so closer is tracked
        # idle past TTL → reaper enqueues close (closer runs async on the
        # owner thread) → poll until the teardown actually completes.
        for _ in range(60):
            if pool.active_count() == 0 and state["closed"] == 1:
                break
            time.sleep(0.05)
        assert pool.active_count() == 0
        assert state["closed"] == 1
    finally:
        pool.close_all()


def test_pool_cap_evicts_when_full():
    factory, _ = _counting_factory()
    pool = BrowserSessionPool(
        page_factory=factory,
        max_sessions=2,
        idle_ttl_s=999,
        reap_interval_s=999,
    )
    try:
        pool.get_or_create("a")
        pool.get_or_create("b")
        pool.get_or_create("c")  # over cap → evicts the most-idle
        assert pool.active_count() == 2
    finally:
        pool.close_all()


# ── end-to-end with real Playwright (skipped if absent) ───


def test_real_browser_state_survives_across_submits():
    requires_chromium()
    w = BrowserSessionWorker()  # real chromium
    try:
        w.submit(
            lambda page: page.set_content(
                "<title>T1</title><div id='x'>hello</div>",
            ),
        )
        # A SECOND submit reads from the SAME page — proves persistence.
        assert w.submit(lambda page: page.inner_text("#x")) == "hello"
        assert w.submit(lambda page: page.title()) == "T1"
    finally:
        w.close()


# ── browser_skills._with_page integration (session-gated persistence) ──


def test_with_page_persists_within_agent_session():
    requires_chromium()
    from runtime.execution.suckers.browser_session_worker import (
        get_browser_session_pool,
    )
    from runtime.execution.suckers.browser_skills import _with_page
    from runtime.platform.process.session import Session, session_scope

    try:
        with session_scope(Session(actor="a", thread_id="thr_persist_test")):
            _with_page(None, lambda p: p.set_content("<div id='x'>persisted</div>"))
            # second page-None call inside the SAME session shares the page
            res = _with_page(None, lambda p: p.inner_text("#x"))
        # _with_page wraps non-dict action results under "result"
        assert res.get("result") == "persisted"
    finally:
        get_browser_session_pool().close_all()


def test_with_page_stateless_without_session():
    requires_chromium()
    from runtime.execution.suckers.browser_skills import _with_page

    # No agent session → original behaviour: a fresh browser per call, so the
    # element from the previous call must NOT be visible (no persistence).
    _with_page(None, lambda p: p.set_content("<div id='x'>gone</div>"))
    res = _with_page(None, lambda p: p.query_selector("#x") is not None)
    # _with_page wraps non-dict action results under "result"
    assert res.get("result") is False


def test_public_browser_actions_reuse_current_page_when_url_is_omitted(tmp_path):
    requires_chromium()
    from runtime.execution.suckers.browser_session_worker import (
        get_browser_session_pool,
    )
    from runtime.execution.suckers.browser_skills import (
        _browser_click,
        _browser_get,
        _browser_type,
        _browser_upload,
        _with_page,
    )
    from runtime.platform.process.session import Session, session_scope

    try:
        upload_file = tmp_path / "profile.txt"
        upload_file.write_text("agent profile", encoding="utf-8")
        with session_scope(Session(actor="a", thread_id="thr_action_persist_test")):
            _with_page(
                None,
                lambda page: page.set_content(
                    "<input id='name'><select id='plan'>"
                    "<option>Free</option><option>Pro</option></select>"
                    "<input id='avatar' type='file'>"
                    "<button id='save' "
                    'onclick="document.body.dataset.saved='
                    "document.querySelector('#name').value\">Save</button>"
                ),
            )
            typed = _browser_type(selector="#name", value="Acme Labs")
            selected = _browser_type(selector="#plan", option_label="Pro")
            uploaded = _browser_upload(selector="#avatar", path=str(upload_file))
            clicked = _browser_click(selector="#save")
            body = _browser_get()

        assert typed["filled"] == "#name"
        assert selected["input_kind"] == "select"
        assert uploaded["file_name"] == "profile.txt"
        assert clicked["clicked"] == "#save"
        assert "Save" in body["content"]
        saved = (
            get_browser_session_pool()
            .get_or_create("thr:thr_action_persist_test")
            .submit(lambda page: page.get_attribute("body", "data-saved"))
        )
        assert saved == "Acme Labs"
        plan = (
            get_browser_session_pool()
            .get_or_create("thr:thr_action_persist_test")
            .submit(lambda page: page.input_value("#plan"))
        )
        assert plan == "Pro"
        file_name = (
            get_browser_session_pool()
            .get_or_create("thr:thr_action_persist_test")
            .submit(
                lambda page: page.locator("#avatar").evaluate("element => element.files[0].name")
            )
        )
        assert file_name == "profile.txt"
    finally:
        get_browser_session_pool().close_all()

