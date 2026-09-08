from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from appliance.approval import APPROVAL_HEADER
from appliance.extension import register_app
from appliance.totp import AdministratorTotp, totp_code, validate_totp_state
from runtime.platform.extensions import AppExtensionContext


def _configured_app(tmp_path, monkeypatch) -> FastAPI:
    monkeypatch.setenv("ECHO_APPLIANCE", "1")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ECHO_NAS_ROOT", str(tmp_path / "nas"))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "administrator-device-pass")
    (tmp_path / "nas").mkdir()
    app = FastAPI()
    register_app(app, AppExtensionContext(identity_store=None))
    return app


def _login(client: TestClient, *, factor: str | None = None):
    body = {"username": "admin", "password": "administrator-device-pass"}
    if factor is not None:
        body["secondFactor"] = factor
    return client.post(
        "/api/auth/local/login",
        headers={"Origin": "http://testserver"},
        json=body,
    )


def _approval(client: TestClient, *, token: str, action: str) -> str:
    response = client.post(
        "/api/appliance/approvals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "action": action,
            "target": "admin",
            "password": "administrator-device-pass",
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["approvalToken"])


def test_rfc6238_sha1_six_digit_vector() -> None:
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp_code(secret, timestamp=59) == "287082"


def test_totp_state_validation_is_strict() -> None:
    state = {
        "format": "echo-appliance-totp-v1",
        "username": "admin",
        "secret": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
        "recovery_code_hashes": ["a" * 64],
        "last_accepted_counter": -1,
        "enabled_at": 1,
    }
    assert validate_totp_state(state) == state
    with pytest.raises(ValueError):
        validate_totp_state({**state, "unexpected": True})
    with pytest.raises(ValueError):
        validate_totp_state({**state, "recovery_code_hashes": ["not-a-hash"]})


def test_enrollment_replay_and_one_time_recovery_codes(tmp_path) -> None:
    now = 1_800_000_000.0
    service = AdministratorTotp(
        jwt_secret="A-valid-device-JWT-secret-with-entropy-42!",
        path=tmp_path / "appliance-totp.json",
        clock=lambda: now,
    )
    enrollment = service.begin_enrollment()
    code = totp_code(enrollment["secret"], timestamp=now)
    service.confirm_enrollment(enrollment_id=enrollment["enrollmentId"], code=code)

    assert service.status() == {"enabled": True, "recoveryCodesRemaining": 8}
    service.verify_login_factor("admin", code, None)
    with pytest.raises(HTTPException) as replay:
        service.verify_login_factor("admin", code, None)
    assert getattr(replay.value, "status_code", None) == 401

    recovery = enrollment["recoveryCodes"][0]
    service.verify_login_factor("admin", recovery, None)
    assert service.status()["recoveryCodesRemaining"] == 7
    with pytest.raises(HTTPException) as reused:
        service.verify_login_factor("admin", recovery, None)
    assert getattr(reused.value, "status_code", None) == 401


def test_administrator_totp_full_http_lifecycle(tmp_path, monkeypatch) -> None:
    app = _configured_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        initial = _login(client)
        assert initial.status_code == 200, initial.text
        initial_token = initial.json()["access_token"]

        enrollment_approval = _approval(
            client,
            token=initial_token,
            action="credentials.totp.enroll",
        )
        enrollment = client.post(
            "/api/appliance/credentials/totp/enroll",
            headers={
                "Authorization": f"Bearer {initial_token}",
                APPROVAL_HEADER: enrollment_approval,
            },
        )
        assert enrollment.status_code == 200, enrollment.text
        setup = enrollment.json()
        assert setup["otpauthUri"].startswith("otpauth://totp/")
        assert len(set(setup["recoveryCodes"])) == 8
        assert "administrator-device-pass" not in enrollment.text

        confirmed = client.post(
            "/api/appliance/credentials/totp/confirm",
            headers={"Authorization": f"Bearer {initial_token}"},
            json={
                "enrollmentId": setup["enrollmentId"],
                "code": totp_code(setup["secret"], timestamp=time.time()),
            },
        )
        assert confirmed.status_code == 200, confirmed.text

        required = _login(client)
        assert required.status_code == 428
        assert required.json()["detail"] == "second_factor_required"
        assert _login(client, factor="000000").status_code == 401

        recovery = setup["recoveryCodes"][0]
        recovered = _login(client, factor=recovery)
        assert recovered.status_code == 200, recovered.text
        recovered_token = recovered.json()["access_token"]
        assert _login(client, factor=recovery).status_code == 401

        status = client.get(
            "/api/appliance/credentials/totp",
            headers={"Authorization": f"Bearer {recovered_token}"},
        )
        assert status.json() == {"enabled": True, "recoveryCodesRemaining": 7}

        disable_approval = _approval(
            client,
            token=recovered_token,
            action="credentials.totp.disable",
        )
        disabled = client.post(
            "/api/appliance/credentials/totp/disable",
            headers={
                "Authorization": f"Bearer {recovered_token}",
                APPROVAL_HEADER: disable_approval,
            },
            json={"factor": setup["recoveryCodes"][1]},
        )
        assert disabled.status_code == 200, disabled.text
        assert _login(client).status_code == 200

    state_path = tmp_path / "data" / "appliance-totp.json"
    assert not state_path.exists()
    audit = (tmp_path / "data" / "appliance-audit.jsonl").read_text(encoding="utf-8")
    assert "credentials.totp.enable" in audit
    assert "credentials.totp.disable" in audit
    assert setup["secret"] not in audit
    assert all(code not in audit for code in setup["recoveryCodes"])
    assert setup["secret"] not in json.dumps(json.loads(audit.splitlines()[0]))
