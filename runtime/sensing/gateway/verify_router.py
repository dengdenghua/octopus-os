"""Verification router · ``/api/verify/*``.

Exposes project detection and verification check execution to the
frontend. The code page calls these after AI edits to confirm the
workspace still builds/passes.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

from runtime.execution.suckers.browser_launch import (
    launch_chromium,
)

try:
    from fastapi import APIRouter, HTTPException, Request
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    BaseModel = object  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

if FASTAPI_AVAILABLE:

    class VerifyDetectRequest(BaseModel):
        workspace: str

    class VerifyRunRequest(BaseModel):
        workspace: str
        checks: list[str] | None = None
        timeout: float = 60.0
        browser_regression_enabled: bool = False
        browser_regression_mode: str | None = None
        browser_regression_preview_url: str | None = None
        browser_regression_requires_visible_cursor: bool = False


def create_verify_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(tags=["verify"])

    def _auth(request: Request) -> str | None:
        if require_auth and identity_store is None:
            raise HTTPException(401, "auth required")
        from runtime.sensing.gateway.openai_gateway_router import _resolve_actor

        return _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    @router.post("/api/verify/detect")
    def api_verify_detect(
        request: Request,
        body: VerifyDetectRequest,
    ) -> dict[str, Any]:
        _auth(request)
        from runtime.execution.suckers.verify_skills import detect_project

        profile = detect_project(body.workspace)
        return {
            "kind": profile.kind,
            "root": profile.root,
            "checks": profile.checks,
        }

    @router.post("/api/verify/run")
    def api_verify_run(request: Request, body: VerifyRunRequest) -> dict[str, Any]:
        _auth(request)
        from runtime.execution.suckers.verify_skills import (
            detect_project,
            run_checks,
        )

        profile = detect_project(body.workspace)
        if body.checks:
            profile.checks = [c for c in profile.checks if c["name"] in body.checks]
        results = run_checks(profile, timeout_per_check=body.timeout)
        results.extend(_run_browser_regression_checks(body))
        passed_all = all(r.passed for r in results)
        return {
            "kind": profile.kind,
            "passed": passed_all,
            "results": [
                {
                    "name": r.name,
                    "command": r.command,
                    "passed": r.passed,
                    "exit_code": r.exit_code,
                    "stdout": r.stdout,
                    "stderr": r.stderr,
                    "duration_ms": r.duration_ms,
                }
                for r in results
            ],
        }

    return router


def _run_browser_regression_checks(body: Any) -> list[Any]:
    if not getattr(body, "browser_regression_enabled", False):
        return []

    from runtime.execution.suckers.verify_skills import CheckResult

    url = (getattr(body, "browser_regression_preview_url", None) or "").strip()
    mode = (getattr(body, "browser_regression_mode", None) or "human_cursor").strip()
    visible_cursor = bool(getattr(body, "browser_regression_requires_visible_cursor", False))
    if mode == "human_cursor":
        visible_cursor = True
    command = f"browser regression: {url or '<missing preview url>'}"
    started = time.monotonic()

    def done(passed: bool, stdout: str = "", stderr: str = "", exit_code: int = 0) -> CheckResult:
        return CheckResult(
            name="browser-regression",
            command=command,
            passed=passed,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    if not url:
        return [
            done(
                False,
                stderr="browser regression is enabled but no preview URL was provided",
                exit_code=-2,
            )
        ]

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return [done(False, stderr=f"unsupported preview URL: {url}", exit_code=-2)]
    if not _is_local_preview_host(parsed.hostname):
        return [
            done(
                False,
                stderr="browser regression only accepts localhost/loopback preview URLs",
                exit_code=-2,
            ),
        ]

    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError:
        return [
            done(
                False,
                stderr="playwright is not installed; install the browser extra and chromium runtime",
                exit_code=-3,
            ),
        ]

    console_errors: list[str] = []
    page_errors: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = launch_chromium(pw.chromium, headless=not visible_cursor)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                page.on(
                    "console",
                    lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
                )
                page.on("pageerror", lambda exc: page_errors.append(str(exc)))
                response = page.goto(
                    url,
                    timeout=max(1000, int(float(getattr(body, "timeout", 60.0)) * 1000)),
                    wait_until="domcontentloaded",
                )
                page.wait_for_timeout(500)
                if visible_cursor:
                    _exercise_page_with_visible_cursor(page)
                title = page.title()
                body_text = page.locator("body").inner_text(timeout=2000).strip()
                status = response.status if response else None
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        return [
            done(
                False,
                stderr=f"browser regression failed: {type(exc).__name__}: {exc}",
                exit_code=-1,
            )
        ]

    details = [
        f"url: {url}",
        f"status: {status}",
        f"title: {title}",
        f"body_chars: {len(body_text)}",
        f"mode: {mode}",
    ]
    stderr_parts: list[str] = []
    if status is not None and status >= 400:
        stderr_parts.append(f"HTTP status {status}")
    if not body_text:
        stderr_parts.append("page body is empty")
    if console_errors:
        stderr_parts.append("console errors:\n" + "\n".join(console_errors[:10]))
    if page_errors:
        stderr_parts.append("page errors:\n" + "\n".join(page_errors[:10]))
    return [
        done(
            not stderr_parts,
            stdout="\n".join(details),
            stderr="\n\n".join(stderr_parts),
            exit_code=1 if stderr_parts else 0,
        )
    ]


def _is_local_preview_host(host: str) -> bool:
    normalized = host.strip("[]").lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    if normalized.startswith("127."):
        return True
    return normalized in {"0.0.0.0"}  # nosec B104 — string comparison, not a bind


def _exercise_page_with_visible_cursor(page: Any) -> None:
    page.evaluate(
        """() => {
            const existing = document.getElementById("__echo_regression_cursor");
            if (existing) existing.remove();
            const cursor = document.createElement("div");
            cursor.id = "__echo_regression_cursor";
            Object.assign(cursor.style, {
                position: "fixed",
                left: "20px",
                top: "20px",
                width: "14px",
                height: "14px",
                borderRadius: "999px",
                border: "2px solid #2563eb",
                background: "rgba(37, 99, 235, 0.22)",
                pointerEvents: "none",
                zIndex: "2147483647",
                transform: "translate(-50%, -50%)",
            });
            document.body.appendChild(cursor);
        }"""
    )
    for x, y in [(120, 120), (360, 180), (640, 260), (900, 360), (1100, 460)]:
        page.mouse.move(x, y, steps=8)
        page.evaluate(
            """([x, y]) => {
                const cursor = document.getElementById("__echo_regression_cursor");
                if (cursor) {
                    cursor.style.left = `${x}px`;
                    cursor.style.top = `${y}px`;
                }
            }""",
            [x, y],
        )
        page.wait_for_timeout(80)
    page.mouse.wheel(0, 420)
    page.wait_for_timeout(120)
    page.mouse.wheel(0, -220)


__all__ = ["create_verify_router"]
