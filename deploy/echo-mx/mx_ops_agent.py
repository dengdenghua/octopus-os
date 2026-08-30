"""Small standalone operations agent for the isolated MX session.

This process does not run Echo. It checks the server-side MX session and,
only after expiry, launches a short-lived Playwright browser, asks an
OpenAI-compatible Agnes vision model to read the four-digit CAPTCHA, submits
the official login form, verifies the returned session, and exits the browser.

The agent has no code path for trading, purchasing, points, withdrawals,
subscriptions, room mutation, or account mutation.  Provider credentials,
MX credentials, and the recovered token remain in mode-0600 server files.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from mx_session_guardian import (
    ALLOWED_HOSTURLS,
    DEFAULT_CREDENTIAL_FILE,
    DEFAULT_SESSION_FILE,
    DEFAULT_STATE_FILE,
    DEFAULT_UPSTREAM,
    CaptchaRecognitionError,
    CaptchaSolver,
    CredentialsRejected,
    GuardianError,
    LoginRejected,
    MXSessionGuardian,
    SessionUnavailable,
    _strict_json,
)

LOGGER = logging.getLogger("echo.mx_ops_agent")
DEFAULT_VISION_FILE = "/var/lib/echo-mx/vision.json"
DEFAULT_BROWSER_TIMEOUT_MS = 60_000
MAX_VISION_RESPONSE_BYTES = 2 * 1024 * 1024
ALLOWED_BROWSER_PROXIES = frozenset({"", "socks5://127.0.0.1:18084"})


class VisionProviderUnavailable(GuardianError):
    """The remote vision provider is unavailable; local OCR may still work."""


def _validated_api_base(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise GuardianError("vision base URL must be a plain HTTPS URL")
    return urlunsplit(
        ("https", parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


def load_vision_config(path: Path) -> dict[str, str]:
    payload = _strict_json(path, label="Agnes vision configuration")
    base_url = _validated_api_base(str(payload.get("base_url") or ""))
    api_key = str(payload.get("api_key") or "").strip()
    model = str(payload.get("model") or "agnes-2.5-flash").strip()
    if not api_key or len(api_key) > 4096 or not model or len(model) > 160:
        raise GuardianError("Agnes vision configuration is incomplete")
    return {"base_url": base_url, "api_key": api_key, "model": model}


def _message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict) and item.get("type") in {None, "text"}
    )


def _four_digit_answer(content: str) -> str:
    candidates = set(re.findall(r"(?<!\d)\d{4}(?!\d)", content))
    if len(candidates) != 1:
        raise CaptchaRecognitionError("Agnes CAPTCHA result is uncertain")
    return candidates.pop()


class AgnesCaptchaSolver:
    """Read a CAPTCHA with a narrowly prompted Agnes vision request."""

    def __init__(
        self,
        *,
        config_file: Path = Path(DEFAULT_VISION_FILE),
        renderer: str = "/usr/bin/rsvg-convert",
        timeout: int = 90,
        client: httpx.Client | None = None,
    ) -> None:
        self.config_file = config_file
        self.renderer = renderer
        self.timeout = max(15, min(int(timeout), 180))
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(connect=10, read=self.timeout, write=20, pool=10),
            follow_redirects=False,
            trust_env=False,
        )

    def _png_data_url(self, data_uri: str) -> str:
        suffix, raw = CaptchaSolver._decode(data_uri)
        if suffix == ".svg":
            with tempfile.TemporaryDirectory(prefix="echo-mx-agnes-") as directory:
                source = Path(directory) / "captcha.svg"
                image = Path(directory) / "captcha.png"
                source.write_bytes(raw)
                source.chmod(0o600)
                try:
                    subprocess.run(
                        [
                            self.renderer,
                            # Keep the browser's visual scale. At 8x the ornate
                            # numeral font resembles a wordmark and vision
                            # models can incorrectly classify it as a logo.
                            "--zoom=1.2",
                            "--background-color=white",
                            "--output",
                            str(image),
                            str(source),
                        ],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=30,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    raise CaptchaRecognitionError(
                        "CAPTCHA image normalization failed"
                    ) from exc
                raw = image.read_bytes()
            mime = "image/png"
        else:
            mime = "image/jpeg" if suffix == ".jpg" else "image/png"
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

    def solve(self, data_uri: str) -> str:
        try:
            config = load_vision_config(self.config_file)
        except GuardianError as exc:
            raise VisionProviderUnavailable("Agnes vision is not configured") from exc
        payload = {
            "model": config["model"],
            "temperature": 0,
            # Agnes is a reasoning model. A small cap can consume the whole
            # budget internally and return empty content even for simple OCR.
            "max_tokens": 8192,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": self._png_data_url(data_uri)},
                        },
                        {
                            "type": "text",
                            "text": (
                                "这是一个网页登录用的四位数字验证码，不是文字Logo。"
                                "四个装饰字体数字从左到右排列，可能有多条干扰线。"
                                "请逐个识别四个阿拉伯数字。只输出恰好4位数字，"
                                "不要解释，不要标点。"
                            ),
                        },
                    ],
                }
            ],
        }
        try:
            response = self.client.post(
                f"{config['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            if len(response.content) > MAX_VISION_RESPONSE_BYTES:
                raise VisionProviderUnavailable("Agnes vision response is too large")
            body = response.json()
        except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise VisionProviderUnavailable("Agnes vision request failed") from exc
        if not isinstance(body, dict):
            raise VisionProviderUnavailable("Agnes vision returned an invalid response")
        return _four_digit_answer(_message_content(body))

    def close(self) -> None:
        self.client.close()


class HybridCaptchaSolver:
    """Use Agnes first and local OCR as a bounded outage fallback."""

    def __init__(
        self,
        *,
        vision: AgnesCaptchaSolver,
        ocr: CaptchaSolver,
    ) -> None:
        self.vision = vision
        self.ocr = ocr
        self.last_source = "none"

    def solve(self, data_uri: str) -> str:
        try:
            vision_answer = self.vision.solve(data_uri)
        except (VisionProviderUnavailable, CaptchaRecognitionError):
            vision_answer = ""
        if vision_answer:
            self.last_source = "agnes"
            return vision_answer
        try:
            ocr_answer = self.ocr.solve(data_uri)
        except CaptchaRecognitionError:
            ocr_answer = ""
        if ocr_answer:
            self.last_source = "local-ocr-fallback"
            return ocr_answer
        self.last_source = "none"
        raise CaptchaRecognitionError("CAPTCHA could not be recognized")


class BrowserLoginRestorer:
    """Submit only the official login form inside a real browser."""

    def __init__(
        self,
        *,
        upstream: str,
        solver: HybridCaptchaSolver,
        timeout_ms: int = DEFAULT_BROWSER_TIMEOUT_MS,
        proxy_server: str = "",
    ) -> None:
        self.upstream = upstream.rstrip("/")
        self.solver = solver
        self.timeout_ms = max(20_000, min(int(timeout_ms), 120_000))
        self.proxy_server = str(proxy_server or "").strip()
        if self.proxy_server not in ALLOWED_BROWSER_PROXIES:
            raise RuntimeError("MX browser proxy must use the dedicated loopback port")

    @staticmethod
    def _credential_rejection(message: str) -> bool:
        normalized = message.replace(" ", "")
        return any(
            marker in normalized
            for marker in (
                "账号或密码",
                "密码错误",
                "账户或密码",
                "尝试机会",
                "账号不存在",
                "账号异常",
            )
        )

    def __call__(self, credentials: dict[str, str]) -> dict[str, Any]:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise SessionUnavailable("Playwright is not installed") from exc

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--no-first-run",
                    ],
                    proxy=(
                        {"server": self.proxy_server}
                        if self.proxy_server
                        else None
                    ),
                )
                try:
                    context = browser.new_context(
                        viewport={"width": 1280, "height": 900},
                        locale="zh-CN",
                    )
                    page = context.new_page()
                    page.set_default_timeout(self.timeout_ms)
                    page.goto(
                        f"{self.upstream}/#/",
                        wait_until="domcontentloaded",
                        timeout=self.timeout_ms,
                    )
                    account = page.locator('input[type="text"]').first
                    password = page.locator('input[type="password"]').first
                    captcha_input = page.locator('input[type="number"]').first
                    captcha_image = page.locator(
                        'img[src^="data:image/svg+xml"], img[src^="data:image/png"]'
                    ).last
                    captcha_image.wait_for(state="visible")
                    # The SPA requests several CAPTCHA instances while its
                    # login component mounts. Wait until the displayed image
                    # stops changing so its code_key remains paired with the
                    # image throughout the vision call.
                    image_uri = ""
                    stable_reads = 0
                    for _poll in range(40):
                        current_uri = str(captcha_image.get_attribute("src") or "")
                        if current_uri and current_uri == image_uri:
                            stable_reads += 1
                        else:
                            image_uri = current_uri
                            stable_reads = 0
                        if stable_reads >= 6:
                            break
                        page.wait_for_timeout(500)
                    else:
                        raise SessionUnavailable("CAPTCHA image did not stabilize")
                    answer = self.solver.solve(image_uri)
                    account.fill(credentials["user"])
                    password.fill(credentials["password"])
                    captcha_input.fill(answer)
                    login_button = page.locator(
                        "uni-button.cu-btn.bg-blue", has_text="登录"
                    ).first
                    with page.expect_response(
                        lambda item: urlsplit(item.url).path.endswith("/api/login"),
                        timeout=self.timeout_ms,
                    ) as pending:
                        login_button.click()
                    response = pending.value
                    if response.status != 200:
                        raise SessionUnavailable(
                            "official browser login returned an HTTP error"
                        )
                    payload = response.json()
                finally:
                    browser.close()
        except CaptchaRecognitionError:
            raise
        except (PlaywrightTimeout, PlaywrightError, OSError, ValueError) as exc:
            raise SessionUnavailable("official browser login failed") from exc

        if not isinstance(payload, dict):
            raise LoginRejected("official login returned an invalid response")
        try:
            code = int(payload.get("code"))
        except (TypeError, ValueError):
            code = None
        message = str(payload.get("msg") or payload.get("message") or "")
        if code != 200:
            if self._credential_rejection(message):
                raise CredentialsRejected("upstream rejected the configured credentials")
            if "验证码" in message:
                raise CaptchaRecognitionError("upstream rejected the CAPTCHA answer")
            raise LoginRejected("upstream rejected the official browser login")
        token = str(payload.get("token") or "").strip()
        hosturl = str(payload.get("hosturl") or "/5").strip()
        if len(token) < 16 or hosturl not in ALLOWED_HOSTURLS:
            raise LoginRejected("official login response is incomplete")
        LOGGER.info("official browser login completed solver=%s", self.solver.last_source)
        return {"token": token, "hosturl": hosturl}


def build_ops_agent() -> tuple[MXSessionGuardian, AgnesCaptchaSolver]:
    upstream = os.environ.get("MX_UPSTREAM_URL", DEFAULT_UPSTREAM)
    vision = AgnesCaptchaSolver(
        config_file=Path(os.environ.get("MX_VISION_FILE", DEFAULT_VISION_FILE)),
        renderer=os.environ.get("MX_CAPTCHA_RENDERER", "/usr/bin/rsvg-convert"),
        timeout=int(os.environ.get("MX_VISION_TIMEOUT", "90")),
    )
    hybrid = HybridCaptchaSolver(
        vision=vision,
        ocr=CaptchaSolver(
            renderer=os.environ.get("MX_CAPTCHA_RENDERER", "/usr/bin/rsvg-convert"),
            tesseract=os.environ.get("MX_CAPTCHA_TESSERACT", "/usr/bin/tesseract"),
            timeout=int(os.environ.get("MX_CAPTCHA_TIMEOUT", "20")),
        ),
    )
    browser_login = BrowserLoginRestorer(
        upstream=upstream,
        solver=hybrid,
        timeout_ms=int(
            os.environ.get("MX_OPS_BROWSER_TIMEOUT_MS", str(DEFAULT_BROWSER_TIMEOUT_MS))
        ),
        proxy_server=os.environ.get("MX_BROWSER_PROXY_SERVER", ""),
    )
    guardian = MXSessionGuardian(
        upstream=upstream,
        session_file=Path(os.environ.get("MX_SESSION_FILE", DEFAULT_SESSION_FILE)),
        credential_file=Path(
            os.environ.get("MX_CREDENTIAL_FILE", DEFAULT_CREDENTIAL_FILE)
        ),
        state_file=Path(os.environ.get("MX_SESSION_STATE_FILE", DEFAULT_STATE_FILE)),
        session_restorer=browser_login,
        captcha_attempts=int(os.environ.get("MX_CAPTCHA_ATTEMPTS", "2")),
        auto_login_enabled=os.environ.get("MX_AUTO_LOGIN_ENABLED", "false")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"},
    )
    return guardian, vision


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    interval = max(60, min(int(os.environ.get("MX_GUARDIAN_INTERVAL", "900")), 1800))
    guardian, vision = build_ops_agent()
    last_state = ""
    try:
        while True:
            state = guardian.run_once()
            current = str(state.get("state") or "unknown")
            if current != last_state:
                LOGGER.info(
                    "MX operations state changed state=%s authenticated=%s failures=%s",
                    current,
                    bool(state.get("authenticated")),
                    int(state.get("failure_count") or 0),
                )
                last_state = current
            time.sleep(interval)
    finally:
        guardian.close()
        vision.close()


if __name__ == "__main__":
    main()
