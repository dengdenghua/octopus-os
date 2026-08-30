from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.request import urlopen

import pytest
from playwright.sync_api import expect, sync_playwright

from benchmarks.eval_harness import Trajectory
from benchmarks.fixed_suite_fixtures import prepare_fixture_suite
from benchmarks.fixture_grading import LiveIsolatedFixture
from benchmarks.verifier_sandbox import HARDENED_RUNNER_ENV
from tests.conftest import chromium_launch_kwargs, requires_chromium

REPO_ROOT = Path(__file__).resolve().parents[1]


def _requires_hardened_verifier_runner() -> None:
    """Skip only when the fail-closed hidden-verifier service cannot exist.

    Browser interaction itself is portable, but these two satisfiability tests
    finish by executing evaluator-owned hidden verification against an
    untrusted trial workspace.  Production intentionally authorizes that step
    only through the root-attested Linux runner.  A configured runner that is
    invalid must still fail loudly; only an unsupported platform or an ordinary
    developer environment with no runner configured is a legitimate skip.
    """

    if not sys.platform.startswith("linux"):
        pytest.skip("hidden fixture verification requires the hardened Linux runner")
    if not os.environ.get(HARDENED_RUNNER_ENV, "").strip():
        pytest.skip(f"set {HARDENED_RUNNER_ENV} to run hidden fixture verification")


def test_frontend_fixtures_have_isolated_live_previews(tmp_path) -> None:
    case_ids = {
        "frontend.responsive-settings",
        "frontend.async-form-recovery",
    }
    prepared = prepare_fixture_suite(
        repo_root=REPO_ROOT,
        runs_root=tmp_path / "runs",
        case_ids=case_ids,
    )

    for case in prepared.cases:
        fixture = prepared.fixtures[case.id]
        assert isinstance(fixture, LiveIsolatedFixture)
        assert case.setup is not None and case.teardown is not None
        case.setup()
        try:
            url = fixture.url()
            assert url.startswith("http://127.0.0.1:")
            with urlopen(url, timeout=2) as response:  # noqa: S310 - trusted loopback fixture
                html = response.read().decode("utf-8")
            assert '<meta name="eval-session"' in html
            assert "<!doctype html" in html.lower()
        finally:
            case.teardown()


def test_dynamic_crud_fixture_is_live_and_satisfiable(tmp_path) -> None:
    requires_chromium()
    prepared = prepare_fixture_suite(
        repo_root=REPO_ROOT,
        runs_root=tmp_path / "runs",
        case_ids={"browser.dynamic-crud"},
    )
    case = prepared.cases[0]
    fixture = prepared.fixtures[case.id]
    assert isinstance(fixture, LiveIsolatedFixture)
    assert case.setup is not None and case.teardown is not None
    trajectory = Trajectory(trial_id="test", case_id=case.id)
    case.setup()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**chromium_launch_kwargs())
            page = browser.new_page()
            page.goto(fixture.url())
            trajectory.append("tool_start", tool_name="browser_navigate")
            page.locator("#name").fill("Acme Labs")
            trajectory.append("tool_start", tool_name="browser_type")
            page.locator("#plan").select_option(label="Starter")
            trajectory.append("tool_start", tool_name="browser_type")
            page.get_by_role("button", name="Create").click()
            trajectory.append("tool_start", tool_name="browser_click")
            row = page.locator('[data-customer-id="customer-1"]')
            row.wait_for()
            row.locator("[data-edit-plan]").select_option(label="Enterprise")
            trajectory.append("tool_start", tool_name="browser_type")
            row.locator("[data-save]").click()
            trajectory.append("tool_start", tool_name="browser_click")
            expect(page.locator('[data-customer-id="customer-1"] [data-plan]')).to_have_text(
                "Enterprise"
            )
            trajectory.append("tool_start", tool_name="browser_get")
            page.locator('[data-customer-id="customer-1"] [data-verify]').click()
            trajectory.append("tool_start", tool_name="browser_click")
            page.wait_for_timeout(80)
            page.locator('[data-customer-id="customer-1"] [data-delete]').click()
            trajectory.append("tool_start", tool_name="browser_click")
            page.locator('[data-customer-id="customer-1"]').wait_for(state="detached")
            browser.close()
        _requires_hardened_verifier_runner()
        verdict = case.grader(trajectory)
    finally:
        case.teardown()

    assert verdict.passed is True, verdict.reason


def test_rich_editor_upload_fixture_is_live_and_satisfiable(tmp_path) -> None:
    requires_chromium()
    prepared = prepare_fixture_suite(
        repo_root=REPO_ROOT,
        runs_root=tmp_path / "runs",
        case_ids={"browser.rich-editor-upload"},
    )
    case = prepared.cases[0]
    fixture = prepared.fixtures[case.id]
    assert isinstance(fixture, LiveIsolatedFixture)
    assert case.setup is not None and case.teardown is not None
    trajectory = Trajectory(trial_id="test", case_id=case.id)
    case.setup()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**chromium_launch_kwargs())
            page = browser.new_page()
            page.goto(fixture.url())
            trajectory.append("tool_start", tool_name="browser_navigate")
            page.locator("#role").select_option(label="Administrator")
            trajectory.append("tool_start", tool_name="browser_type")
            page.locator("#bio").fill("Building reliable agents.")
            trajectory.append("tool_start", tool_name="browser_type")
            page.locator("#avatar").set_input_files(fixture.workspace() / "profile.txt")
            trajectory.append("tool_start", tool_name="browser_upload")
            page.locator("#submit").click()
            trajectory.append("tool_start", tool_name="browser_click")
            frame = page.frame_locator("#confirmation")
            frame.locator("#confirmed").wait_for()
            trajectory.append("tool_start", tool_name="browser_wait")
            browser.close()
        _requires_hardened_verifier_runner()
        verdict = case.grader(trajectory)
    finally:
        case.teardown()

    assert verdict.passed is True, verdict.reason

