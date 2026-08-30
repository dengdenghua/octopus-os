"""Health guardian and bounded login recovery for the isolated MX bridge.

The bridge deliberately never exposes the real upstream token to browser code.
This companion process owns the only write path for ``session.json``.  It
checks the upstream session, performs a tightly bounded CAPTCHA login when a
mode-0600 credential file has been provisioned, and publishes a secret-free
state file consumed by the bridge UI and collector.

No trading, purchase, subscription, point-spending, account mutation, or room
mutation endpoint is reachable from this module.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import stat
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

LOGGER = logging.getLogger("echo.mx_guardian")
DEFAULT_UPSTREAM = "https://mx2025.hhhuu.com"
DEFAULT_SESSION_FILE = "/var/lib/echo-mx/session.json"
DEFAULT_CREDENTIAL_FILE = "/var/lib/echo-mx/credentials.json"
DEFAULT_STATE_FILE = "/var/lib/echo-mx/session-state.json"
ALLOWED_HOSTURLS = frozenset({"/3", "/5"})
MAX_CAPTCHA_BYTES = 512 * 1024
MAX_SECRET_BYTES = 16 * 1024
LOGIN_DEVICE = "echo-mx-guardian"


class GuardianError(RuntimeError):
    """Base class for expected, secret-free guardian failures."""


class SessionUnavailable(GuardianError):
    pass


class CaptchaRecognitionError(GuardianError):
    pass


class CredentialsRejected(GuardianError):
    pass


class LoginRejected(GuardianError):
    pass


def _validated_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("MX upstream must be a plain HTTPS origin")
    return urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _strict_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        metadata = path.stat()
    except FileNotFoundError as exc:
        raise GuardianError(f"{label} is not provisioned") from exc
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise GuardianError(f"{label} permissions must be 0600 or stricter")
    if metadata.st_size > MAX_SECRET_BYTES:
        raise GuardianError(f"{label} is too large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardianError(f"{label} is invalid") from exc
    if not isinstance(payload, dict):
        raise GuardianError(f"{label} is invalid")
    return payload


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_session(path: Path) -> dict[str, Any]:
    payload = _strict_json(path, label="MX session")
    token = str(payload.get("token") or "").strip()
    hosturl = str(payload.get("hosturl") or "/5").strip()
    if len(token) < 16 or hosturl not in ALLOWED_HOSTURLS:
        raise GuardianError("MX session is incomplete")
    return {
        "token": token,
        "hosturl": hosturl,
        "logged_in_at": payload.get("logged_in_at"),
    }


def load_credentials(path: Path) -> dict[str, str]:
    payload = _strict_json(path, label="MX credentials")
    user = str(payload.get("user") or "").strip()
    password = str(payload.get("password") or "")
    if not user or len(user) > 128 or not password or len(password) > 256:
        raise GuardianError("MX credentials are incomplete")
    return {"user": user, "password": password}


def credential_revision(path: Path) -> str:
    try:
        metadata = path.stat()
    except OSError:
        return "missing"
    return f"{metadata.st_ino}:{metadata.st_size}:{metadata.st_mtime_ns}"


@dataclass(frozen=True)
class SessionCheck:
    status: str
    code: int | None = None
    detail: str = ""


class CaptchaSolver:
    """Render an upstream data-URI CAPTCHA and return a bounded numeric answer."""

    def __init__(
        self,
        *,
        renderer: str = "/usr/bin/rsvg-convert",
        tesseract: str = "/usr/bin/tesseract",
        timeout: int = 20,
    ) -> None:
        self.renderer = renderer
        self.tesseract = tesseract
        self.timeout = max(5, min(int(timeout), 60))

    @staticmethod
    def _decode(data_uri: str) -> tuple[str, bytes]:
        header, separator, encoded = str(data_uri or "").partition(",")
        if not separator or ";base64" not in header.lower():
            raise CaptchaRecognitionError("CAPTCHA payload is not a base64 data URI")
        mime = header[5:].split(";", 1)[0].lower()
        suffixes = {
            "image/svg+xml": ".svg",
            "image/png": ".png",
            "image/jpeg": ".jpg",
        }
        suffix = suffixes.get(mime)
        if suffix is None:
            raise CaptchaRecognitionError("CAPTCHA image type is not supported")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise CaptchaRecognitionError("CAPTCHA payload is malformed") from exc
        if not raw or len(raw) > MAX_CAPTCHA_BYTES:
            raise CaptchaRecognitionError("CAPTCHA payload size is invalid")
        return suffix, raw

    @staticmethod
    def _clean_svg(raw: bytes) -> bytes:
        """Keep filled glyph paths and discard line-noise paths before OCR."""

        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise CaptchaRecognitionError("CAPTCHA SVG is malformed") from exc
        if root.tag.rsplit("}", 1)[-1].lower() != "svg":
            raise CaptchaRecognitionError("CAPTCHA SVG root is invalid")
        kept = 0
        for parent in root.iter():
            for child in list(parent):
                tag = child.tag.rsplit("}", 1)[-1].lower()
                fill = str(child.attrib.get("fill") or "").strip().lower()
                if tag != "path" or fill in {"", "none", "transparent"}:
                    parent.remove(child)
                    continue
                child.attrib["fill"] = "#000000"
                child.attrib.pop("stroke", None)
                child.attrib.pop("style", None)
                kept += 1
        if kept == 0:
            raise CaptchaRecognitionError("CAPTCHA SVG contains no glyph paths")
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    def solve(self, data_uri: str) -> str:
        suffix, raw = self._decode(data_uri)
        if suffix == ".svg":
            raw = self._clean_svg(raw)
        with tempfile.TemporaryDirectory(prefix="echo-mx-captcha-") as directory:
            root = Path(directory)
            source = root / f"captcha{suffix}"
            source.write_bytes(raw)
            image = source
            if suffix == ".svg":
                image = root / "captcha.png"
                try:
                    subprocess.run(
                        [
                            self.renderer,
                            "--zoom=8",
                            "--background-color=white",
                            "--output",
                            str(image),
                            str(source),
                        ],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=self.timeout,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    raise CaptchaRecognitionError("CAPTCHA renderer failed") from exc
            candidates: list[str] = []
            for page_mode in ("7", "8", "13"):
                try:
                    result = subprocess.run(
                        [
                            self.tesseract,
                            str(image),
                            "stdout",
                            "--psm",
                            page_mode,
                            "-c",
                            "tessedit_char_whitelist=0123456789",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    raise CaptchaRecognitionError("CAPTCHA OCR failed") from exc
                answer = "".join(
                    character for character in result.stdout if character.isdigit()
                )
                if 1 <= len(answer) <= 8:
                    candidates.append(answer)
        if not candidates:
            raise CaptchaRecognitionError("CAPTCHA OCR result is uncertain")
        ranked = sorted(
            ((candidates.count(value), value) for value in set(candidates)),
            reverse=True,
        )
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            raise CaptchaRecognitionError("CAPTCHA OCR candidates disagree")
        return ranked[0][1]


class MXSessionGuardian:
    def __init__(
        self,
        *,
        upstream: str = DEFAULT_UPSTREAM,
        session_file: Path = Path(DEFAULT_SESSION_FILE),
        credential_file: Path = Path(DEFAULT_CREDENTIAL_FILE),
        state_file: Path = Path(DEFAULT_STATE_FILE),
        client: httpx.Client | None = None,
        solver: CaptchaSolver | None = None,
        session_restorer: Callable[[dict[str, str]], dict[str, Any]] | None = None,
        clock: Callable[[], float] = time.time,
        captcha_attempts: int = 2,
        auto_login_enabled: bool = False,
    ) -> None:
        self.upstream = _validated_origin(upstream)
        self.session_file = session_file
        self.credential_file = credential_file
        self.state_file = state_file
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(connect=8, read=20, write=20, pool=8),
            follow_redirects=False,
            trust_env=False,
        )
        self.solver = solver or CaptchaSolver()
        self.session_restorer = session_restorer
        self.clock = clock
        self.captcha_attempts = max(1, min(int(captcha_attempts), 3))
        self.auto_login_enabled = bool(auto_login_enabled)

    def _headers(self, *, token: str | None = None) -> dict[str, str]:
        headers = {
            "AD": "true",
            "version": "4.2.3",
            "i": "qq",
            "Origin": self.upstream,
            "Referer": self.upstream + "/",
            "User-Agent": "Mozilla/5.0 Echo-MX-Guardian/1.0",
        }
        if token:
            headers["token"] = token
        return headers

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise SessionUnavailable("upstream returned an invalid response") from exc
        if not isinstance(payload, dict):
            raise SessionUnavailable("upstream returned an invalid response")
        return payload

    def check_session(self, session: dict[str, Any]) -> SessionCheck:
        try:
            response = self.client.post(
                f"{self.upstream}{session['hosturl']}/api/user/info",
                headers=self._headers(token=str(session["token"])),
                json={"device": LOGIN_DEVICE, "tt": int(self.clock() * 1000)},
            )
            payload = self._json(response)
        except (httpx.HTTPError, OSError, SessionUnavailable) as exc:
            return SessionCheck("unavailable", detail=type(exc).__name__)
        code_value = payload.get("code")
        try:
            code = int(code_value)
        except (TypeError, ValueError):
            code = None
        if response.status_code == 200 and code == 200:
            return SessionCheck("authenticated", code=code)
        if code == 502 or "未登陆" in str(payload.get("msg") or payload.get("message") or ""):
            return SessionCheck("unauthenticated", code=code)
        return SessionCheck("unavailable", code=code, detail="unexpected upstream status")

    def _fetch_captcha(self) -> tuple[str, str]:
        try:
            response = self.client.get(
                f"{self.upstream}/3/api/code",
                headers=self._headers(),
            )
            payload = self._json(response)
        except (httpx.HTTPError, OSError) as exc:
            raise SessionUnavailable("CAPTCHA endpoint is unavailable") from exc
        if response.status_code != 200 or str(payload.get("code")) != "200":
            raise SessionUnavailable("CAPTCHA endpoint rejected the request")
        key = str(payload.get("key") or "").strip()
        captcha = str(payload.get("captcha") or "").strip()
        if not 8 <= len(key) <= 256 or not captcha:
            raise SessionUnavailable("CAPTCHA response is incomplete")
        return key, captcha

    @staticmethod
    def _is_credential_rejection(message: str) -> bool:
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

    def _login_request(
        self,
        credentials: dict[str, str],
        *,
        key: str,
        captcha: str,
    ) -> dict[str, Any]:
        try:
            response = self.client.post(
                f"{self.upstream}/3/api/login",
                headers=self._headers(),
                json={
                    "user": credentials["user"],
                    "password": credentials["password"],
                    "code_key": key,
                    "code": captcha,
                    "device": LOGIN_DEVICE,
                    "cid": None,
                    "ad": True,
                    "h5": True,
                    "tt": int(self.clock() * 1000),
                },
            )
            payload = self._json(response)
        except (httpx.HTTPError, OSError) as exc:
            raise SessionUnavailable("login endpoint is unavailable") from exc
        if response.status_code != 200:
            raise SessionUnavailable("login endpoint returned an HTTP error")
        try:
            code = int(payload.get("code"))
        except (TypeError, ValueError):
            code = None
        if code == 200:
            token = str(payload.get("token") or "").strip()
            hosturl = str(payload.get("hosturl") or "/5").strip()
            if len(token) < 16 or hosturl not in ALLOWED_HOSTURLS:
                raise LoginRejected("login response is incomplete")
            return {"token": token, "hosturl": hosturl}
        message = str(payload.get("msg") or payload.get("message") or "")
        if self._is_credential_rejection(message):
            raise CredentialsRejected("upstream rejected the configured credentials")
        if "验证码" in message:
            raise CaptchaRecognitionError("upstream rejected the CAPTCHA answer")
        raise LoginRejected("upstream rejected the login request")

    def restore_session(self, credentials: dict[str, str]) -> dict[str, Any]:
        last_error: GuardianError | None = None
        for _attempt in range(self.captcha_attempts):
            try:
                if self.session_restorer is not None:
                    session = self.session_restorer(credentials)
                else:
                    key, image = self._fetch_captcha()
                    answer = self.solver.solve(image)
                    session = self._login_request(
                        credentials,
                        key=key,
                        captcha=answer,
                    )
            except CaptchaRecognitionError as exc:
                last_error = exc
                continue
            check = self.check_session(session)
            if check.status != "authenticated":
                raise LoginRejected("new session could not be verified")
            session["logged_in_at"] = int(self.clock())
            _atomic_json(self.session_file, session)
            return session
        raise last_error or CaptchaRecognitionError("CAPTCHA attempts were exhausted")

    def _read_state(self) -> dict[str, Any]:
        try:
            payload = _strict_json(self.state_file, label="MX guardian state")
        except GuardianError:
            return {}
        return payload

    def _write_state(self, **values: Any) -> dict[str, Any]:
        now = int(self.clock())
        previous = self._read_state()
        payload = {
            "state": str(values.pop("state", previous.get("state") or "unknown")),
            "authenticated": bool(values.pop("authenticated", False)),
            "checked_at": now,
            "last_success_at": values.pop("last_success_at", previous.get("last_success_at")),
            "last_login_attempt_at": values.pop(
                "last_login_attempt_at", previous.get("last_login_attempt_at")
            ),
            "login_attempt_count": max(
                0,
                int(
                    values.pop(
                        "login_attempt_count", previous.get("login_attempt_count") or 0
                    )
                ),
            ),
            "last_login_result": str(
                values.pop("last_login_result", previous.get("last_login_result") or "")
            )[:64],
            "failure_count": max(0, int(values.pop("failure_count", 0))),
            "next_retry_at": values.pop("next_retry_at", None),
            "detail": str(values.pop("detail", ""))[:200],
            "credential_revision": str(
                values.pop("credential_revision", credential_revision(self.credential_file))
            )[:160],
        }
        payload.update(values)
        _atomic_json(self.state_file, payload)
        return payload

    @staticmethod
    def _retry_delay(failure_count: int) -> int:
        return min(15 * 60, 60 * (2 ** min(max(failure_count - 1, 0), 4)))

    def run_once(self) -> dict[str, Any]:
        now = int(self.clock())
        previous = self._read_state()
        revision = credential_revision(self.credential_file)

        # A credential/account rejection is a persistent local safety lock.
        # Evaluate it before any upstream health probe so a transient 5xx can
        # never overwrite the lock and accidentally re-arm CAPTCHA login.
        if (
            previous.get("state") == "credentials_rejected"
            and previous.get("credential_revision") == revision
        ):
            return self._write_state(
                state="credentials_rejected",
                authenticated=False,
                failure_count=int(previous.get("failure_count") or 1),
                next_retry_at=None,
                detail="credential update is required before another attempt",
                credential_revision=revision,
                last_login_result="credentials_rejected",
            )

        try:
            session = load_session(self.session_file)
        except GuardianError:
            check = SessionCheck("unauthenticated")
        else:
            check = self.check_session(session)

        if check.status == "authenticated":
            return self._write_state(
                state="healthy",
                authenticated=True,
                last_success_at=now,
                failure_count=0,
                next_retry_at=None,
                detail="",
                credential_revision=revision,
            )
        if check.status == "unavailable":
            failures = int(previous.get("failure_count") or 0) + 1
            return self._write_state(
                state="upstream_unavailable",
                authenticated=False,
                failure_count=failures,
                next_retry_at=now + self._retry_delay(failures),
                detail="upstream health check is unavailable",
                credential_revision=revision,
            )

        if not self.auto_login_enabled:
            return self._write_state(
                state="login_required",
                authenticated=False,
                failure_count=int(previous.get("failure_count") or 0),
                next_retry_at=None,
                detail="automatic login is disabled; manual authorization is required",
                credential_revision=revision,
                last_login_result="disabled",
            )
        next_retry = int(previous.get("next_retry_at") or 0)
        if next_retry > now:
            return self._write_state(
                state=str(previous.get("state") or "waiting_retry"),
                authenticated=False,
                failure_count=int(previous.get("failure_count") or 0),
                next_retry_at=next_retry,
                detail=str(previous.get("detail") or ""),
                credential_revision=revision,
            )
        try:
            credentials = load_credentials(self.credential_file)
        except GuardianError:
            return self._write_state(
                state="login_required",
                authenticated=False,
                failure_count=0,
                next_retry_at=None,
                detail="secure credentials are not provisioned",
                credential_revision=revision,
            )

        self._write_state(
            state="restoring",
            authenticated=False,
            failure_count=int(previous.get("failure_count") or 0),
            next_retry_at=None,
            detail="bounded login recovery is running",
            last_login_attempt_at=now,
            login_attempt_count=int(previous.get("login_attempt_count") or 0) + 1,
            last_login_result="attempting",
            credential_revision=revision,
        )
        try:
            self.restore_session(credentials)
        except CredentialsRejected:
            failures = int(previous.get("failure_count") or 0) + 1
            return self._write_state(
                state="credentials_rejected",
                authenticated=False,
                failure_count=failures,
                next_retry_at=None,
                detail="upstream rejected the configured credentials",
                last_login_attempt_at=now,
                last_login_result="credentials_rejected",
                credential_revision=revision,
            )
        except CaptchaRecognitionError:
            failures = int(previous.get("failure_count") or 0) + 1
            return self._write_state(
                state="captcha_failed",
                authenticated=False,
                failure_count=failures,
                next_retry_at=now + self._retry_delay(failures),
                detail="CAPTCHA recognition needs another bounded attempt",
                last_login_attempt_at=now,
                last_login_result="captcha_failed",
                credential_revision=revision,
            )
        except (LoginRejected, SessionUnavailable, httpx.HTTPError, OSError):
            failures = int(previous.get("failure_count") or 0) + 1
            return self._write_state(
                state="login_failed",
                authenticated=False,
                failure_count=failures,
                next_retry_at=now + self._retry_delay(failures),
                detail="login recovery failed without changing the session",
                last_login_attempt_at=now,
                last_login_result="login_failed",
                credential_revision=revision,
            )
        return self._write_state(
            state="healthy",
            authenticated=True,
            last_success_at=now,
            last_login_attempt_at=now,
            last_login_result="healthy",
            failure_count=0,
            next_retry_at=None,
            detail="session restored and verified",
            credential_revision=revision,
        )

    def close(self) -> None:
        self.client.close()


def build_guardian() -> MXSessionGuardian:
    return MXSessionGuardian(
        upstream=os.environ.get("MX_UPSTREAM_URL", DEFAULT_UPSTREAM),
        session_file=Path(os.environ.get("MX_SESSION_FILE", DEFAULT_SESSION_FILE)),
        credential_file=Path(
            os.environ.get("MX_CREDENTIAL_FILE", DEFAULT_CREDENTIAL_FILE)
        ),
        state_file=Path(os.environ.get("MX_SESSION_STATE_FILE", DEFAULT_STATE_FILE)),
        solver=CaptchaSolver(
            renderer=os.environ.get("MX_CAPTCHA_RENDERER", "/usr/bin/rsvg-convert"),
            tesseract=os.environ.get("MX_CAPTCHA_TESSERACT", "/usr/bin/tesseract"),
            timeout=int(os.environ.get("MX_CAPTCHA_TIMEOUT", "20")),
        ),
        captcha_attempts=int(os.environ.get("MX_CAPTCHA_ATTEMPTS", "2")),
        auto_login_enabled=os.environ.get("MX_AUTO_LOGIN_ENABLED", "false").strip().lower()
        in {"1", "true", "yes", "on"},
    )


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    interval = max(60, min(int(os.environ.get("MX_GUARDIAN_INTERVAL", "900")), 1800))
    guardian = build_guardian()
    last_state = ""
    try:
        while True:
            state = guardian.run_once()
            current = str(state.get("state") or "unknown")
            if current != last_state:
                LOGGER.info(
                    "MX session state changed state=%s authenticated=%s failures=%s",
                    current,
                    bool(state.get("authenticated")),
                    int(state.get("failure_count") or 0),
                )
                last_state = current
            time.sleep(interval)
    finally:
        guardian.close()


if __name__ == "__main__":
    main()

