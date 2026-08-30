"""Echo/OMV family identity mapping and multi-user login boundaries."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.account_security import (
    ApplianceAccountSecurity,
    ApplianceSessionRevocationMiddleware,
    create_account_security_router,
)
from appliance.accounts import (
    ACCOUNT_LINK_ACTION,
    ACCOUNT_PASSWORD_ACTION,
    ACCOUNT_STATUS_ACTION,
    ACCOUNT_UNLINK_ACTION,
    ApplianceAccountDirectory,
    create_account_directory_router,
)
from appliance.agent_api.auth import create_local_auth_router, verify_password
from appliance.approval import APPROVAL_HEADER, HighRiskApprovalService, create_approval_router
from appliance.audit import ApplianceAudit
from appliance.auth import load_or_bootstrap_auth
from appliance.data_access import OmvDataAccessPolicy
from appliance.files.manager import FileManager
from appliance.files.router import create_files_router
from appliance.photos import PhotoLibraryService, create_photos_router
from appliance.security import ApplianceAuthenticator
from runtime.safety.auth.identity import IdentityStore, encode_jwt_hs256

ADMIN_PASSWORD = "Device-admin-password-42"


class _Omv:
    def __init__(self) -> None:
        self.users = [
            {"name": "alice", "uid": 1001, "comment": "Alice"},
            {"name": "bob", "uid": 1002, "comment": "Bob"},
        ]

    def sharing_overview(self) -> dict:
        return {"users": list(self.users)}


class _FamilyDataOmv:
    def sharing_overview(self) -> dict:
        return {
            "users": [
                {"name": "alice", "groups": ["family"]},
                {"name": "bob", "groups": ["family"]},
            ],
            "sharedFolders": [
                {"uuid": "shared", "relativePath": "Shared", "status": "OK"},
                {"uuid": "alice", "relativePath": "Alice", "status": "OK"},
                {"uuid": "bob", "relativePath": "Bob", "status": "OK"},
            ],
        }

    def share_privileges(self, share_uuid: str) -> list[dict]:
        if share_uuid == "shared":
            return [
                {
                    "type": "group",
                    "id": 100,
                    "name": "family",
                    "permission": "read",
                }
            ]
        owner = share_uuid
        other = "bob" if owner == "alice" else "alice"
        return [
            {
                "type": "user",
                "id": 1001,
                "name": owner,
                "permission": "readWrite",
            },
            {
                "type": "user",
                "id": 1002,
                "name": other,
                "permission": "none",
            },
        ]


class _OfflinePhotoBackend:
    def available(self) -> bool:
        return False


def _configured_client(tmp_path, monkeypatch) -> tuple[TestClient, object, ApplianceAudit]:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", ADMIN_PASSWORD)
    auth_config, _generated = load_or_bootstrap_auth()
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=auth_config.jwt_secret)
    approval = HighRiskApprovalService(
        password_hash=auth_config.users["admin"],
        jwt_secret=auth_config.jwt_secret,
        audit=audit,
        boot_nonce=b"family-account-tests",
    )
    authenticator = ApplianceAuthenticator(auth_config.jwt_secret)
    account_security = ApplianceAccountSecurity(
        auth_config=auth_config,
        approval=approval,
        audit=audit,
    )
    directory = ApplianceAccountDirectory(
        auth_config=auth_config,
        omv=_Omv(),
        jwt_secret=auth_config.jwt_secret,
        account_security=account_security,
    )
    app = FastAPI()
    app.include_router(create_local_auth_router(config=auth_config, identity_store=IdentityStore()))
    app.include_router(create_approval_router(approval, authenticator=authenticator))
    app.include_router(
        create_account_security_router(
            account_security,
            approval=approval,
            authenticator=authenticator,
        )
    )
    app.include_router(
        create_account_directory_router(
            directory,
            authenticator=authenticator,
            approval=approval,
            audit=audit,
        )
    )
    app.add_middleware(
        ApplianceSessionRevocationMiddleware,
        account_security=account_security,
    )
    client = TestClient(app)
    admin_token = encode_jwt_hs256(
        {"sub": "local:admin", "iat": 0, "exp": 9_999_999_999},
        secret=auth_config.jwt_secret,
    )
    client.cookies.set("echo_session", admin_token)
    return client, auth_config, audit


def _link(client: TestClient, *, username: str, display_name: str, password: str) -> None:
    desired = {
        "omvUsername": username,
        "displayName": display_name,
        "password": password,
    }
    plan_response = client.post("/api/appliance/accounts/link/plan", json=desired)
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert password not in json.dumps(plan)
    approval_response = client.post(
        "/api/appliance/approvals",
        json={
            "action": ACCOUNT_LINK_ACTION,
            "target": plan["planId"],
            "password": ADMIN_PASSWORD,
        },
    )
    assert approval_response.status_code == 200, approval_response.text
    applied = client.post(
        "/api/appliance/accounts/link/apply",
        headers={APPROVAL_HEADER: approval_response.json()["approvalToken"]},
        json={"planId": plan["planId"], "desired": desired},
    )
    assert applied.status_code == 200, applied.text
    assert password not in applied.text


def _approved_apply(
    client: TestClient,
    *,
    action: str,
    plan: dict,
    endpoint: str,
    desired: dict,
) -> object:
    approval = client.post(
        "/api/appliance/approvals",
        json={"action": action, "target": plan["planId"], "password": ADMIN_PASSWORD},
    )
    assert approval.status_code == 200, approval.text
    return client.post(
        endpoint,
        headers={APPROVAL_HEADER: approval.json()["approvalToken"]},
        json={"planId": plan["planId"], "desired": desired},
    )


def test_legacy_admin_store_migrates_without_changing_credentials(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", ADMIN_PASSWORD)
    first, _generated = load_or_bootstrap_auth()
    path = tmp_path / "appliance-auth.json"
    legacy = json.loads(path.read_text())
    legacy.pop("accounts")
    path.write_text(json.dumps(legacy), encoding="utf-8")

    second, generated = load_or_bootstrap_auth()
    migrated = json.loads(path.read_text())

    assert generated is None
    assert second.users["admin"] == first.users["admin"]
    assert second.jwt_secret == first.jwt_secret
    assert migrated["accounts"]["admin"]["password_hash"] == legacy["password_hash"]


def test_admin_links_two_omv_members_into_separate_live_agent_principals(
    tmp_path,
    monkeypatch,
) -> None:
    client, auth_config, audit = _configured_client(tmp_path, monkeypatch)
    alice_password = "Alice-independent-Echo-42!"
    bob_password = "Bob-independent-Echo-84!"

    _link(
        client,
        username="alice",
        display_name="Alice",
        password=alice_password,
    )
    _link(client, username="bob", display_name="Bob", password=bob_password)

    store_text = (tmp_path / "appliance-auth.json").read_text()
    store = json.loads(store_text)
    assert alice_password not in store_text
    assert bob_password not in store_text
    assert verify_password(alice_password, store["accounts"]["alice"]["password_hash"])
    assert verify_password(bob_password, store["accounts"]["bob"]["password_hash"])
    assert set(auth_config.users) == {"admin", "alice", "bob"}

    client.cookies.clear()
    alice_login = client.post(
        "/api/auth/local/login",
        headers={"Origin": "http://testserver"},
        json={"username": "alice", "password": alice_password},
    )
    assert alice_login.status_code == 200, alice_login.text
    assert alice_login.json()["actor_id"] == "local:alice"
    alice_directory = client.get("/api/appliance/accounts").json()
    assert alice_directory["canManage"] is False
    assert [account["username"] for account in alice_directory["accounts"]] == ["alice"]
    assert client.post("/api/appliance/sessions/revoke").status_code == 403
    assert (
        client.post(
            "/api/appliance/credentials/rotate",
            json={"newPassword": "Member-must-not-rotate-admin-42!"},
        ).status_code
        == 403
    )

    client.cookies.clear()
    bob_login = client.post(
        "/api/auth/local/login",
        headers={"Origin": "http://testserver"},
        json={"username": "bob", "password": bob_password},
    )
    assert bob_login.status_code == 200, bob_login.text
    assert bob_login.json()["actor_id"] == "local:bob"
    denied = client.post(
        "/api/appliance/accounts/link/plan",
        json={
            "omvUsername": "alice",
            "displayName": "Alice",
            "password": "Another-independent-password-1!",
        },
    )
    assert denied.status_code == 403

    audit_text = audit.path.read_text(encoding="utf-8")
    assert ACCOUNT_LINK_ACTION in audit_text
    assert alice_password not in audit_text
    assert bob_password not in audit_text


def test_logged_in_family_members_follow_live_omv_file_and_photo_boundaries(
    tmp_path,
    monkeypatch,
) -> None:
    client, auth_config, audit = _configured_client(tmp_path / "state", monkeypatch)
    alice_password = "Alice-independent-Echo-42!"
    bob_password = "Bob-independent-Echo-84!"
    _link(client, username="alice", display_name="Alice", password=alice_password)
    _link(client, username="bob", display_name="Bob", password=bob_password)

    nas = tmp_path / "nas"
    for folder in ("Shared", "Alice", "Bob"):
        (nas / folder).mkdir(parents=True)
        (nas / folder / f"{folder.casefold()}.jpg").write_bytes(b"photo")
    omv = _FamilyDataOmv()
    directory = ApplianceAccountDirectory(
        auth_config=auth_config,
        omv=omv,
        jwt_secret=auth_config.jwt_secret,
    )
    access = OmvDataAccessPolicy(accounts=directory, omv=omv, root=nas)
    authenticator = ApplianceAuthenticator(auth_config.jwt_secret)
    client.app.include_router(
        create_files_router(
            FileManager(nas),
            authenticator=authenticator,
            audit=audit,
            data_access=access,
        )
    )
    client.app.include_router(
        create_photos_router(
            PhotoLibraryService(
                nas,
                tmp_path / "photo-state",
                backend=_OfflinePhotoBackend(),
            ),
            authenticator=authenticator,
            data_access=access,
        )
    )

    def login(username: str, password: str) -> None:
        client.cookies.clear()
        response = client.post(
            "/api/auth/local/login",
            headers={"Origin": "http://testserver"},
            json={"username": username, "password": password},
        )
        assert response.status_code == 200, response.text

    login("alice", alice_password)
    alice_files = client.get("/api/appliance/files/list").json()["entries"]
    alice_photos = client.get("/api/appliance/photos/library").json()["items"]
    assert {entry["name"] for entry in alice_files} == {"Alice", "Shared"}
    assert {photo["path"] for photo in alice_photos} == {
        "Alice/alice.jpg",
        "Shared/shared.jpg",
    }
    assert (
        client.post(
            "/api/appliance/files/upload",
            data={"path": "Alice"},
            files={"file": ("new.txt", b"alice", "text/plain")},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/appliance/photos/original",
            params={"path": "Bob/bob.jpg"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/appliance/files/upload",
            data={"path": "Shared"},
            files={"file": ("blocked.txt", b"blocked", "text/plain")},
        ).status_code
        == 403
    )

    login("bob", bob_password)
    bob_files = client.get("/api/appliance/files/list").json()["entries"]
    bob_photos = client.get("/api/appliance/photos/library").json()["items"]
    assert {entry["name"] for entry in bob_files} == {"Bob", "Shared"}
    assert {photo["path"] for photo in bob_photos} == {
        "Bob/bob.jpg",
        "Shared/shared.jpg",
    }
    assert (
        client.get(
            "/api/appliance/files/download",
            params={"path": "Alice/new.txt"},
        ).status_code
        == 403
    )


def test_member_disable_reactivate_and_password_reset_revoke_only_that_member(
    tmp_path,
    monkeypatch,
) -> None:
    client, auth_config, audit = _configured_client(tmp_path, monkeypatch)
    alice_password = "Alice-independent-Echo-42!"
    replacement = "Alice-replacement-Echo-84!"
    bob_password = "Bob-independent-Echo-84!"
    _link(client, username="alice", display_name="Alice", password=alice_password)
    _link(client, username="bob", display_name="Bob", password=bob_password)

    def token_for(username: str, password: str) -> str:
        client.cookies.clear()
        response = client.post(
            "/api/auth/local/login",
            headers={"Origin": "http://testserver"},
            json={"username": username, "password": password},
        )
        assert response.status_code == 200, response.text
        return str(response.json()["access_token"])

    alice_token = token_for("alice", alice_password)
    bob_token = token_for("bob", bob_password)
    admin_token = token_for("admin", ADMIN_PASSWORD)
    client.cookies.set("echo_session", admin_token)

    disable_desired = {"username": "alice", "active": False}
    disable = client.post("/api/appliance/accounts/status/plan", json=disable_desired)
    assert disable.status_code == 200, disable.text
    disabled = _approved_apply(
        client,
        action=ACCOUNT_STATUS_ACTION,
        plan=disable.json(),
        endpoint="/api/appliance/accounts/status/apply",
        desired=disable_desired,
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["sessionsRevoked"] is True
    client.cookies.clear()
    assert (
        client.get(
            "/api/appliance/accounts",
            headers={"Authorization": f"Bearer {alice_token}"},
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/api/appliance/accounts",
            headers={"Authorization": f"Bearer {bob_token}"},
        ).status_code
        == 200
    )
    client.cookies.clear()
    assert (
        client.post(
            "/api/auth/local/login",
            headers={"Origin": "http://testserver"},
            json={"username": "alice", "password": alice_password},
        ).status_code
        == 401
    )

    client.cookies.set("echo_session", admin_token)
    enable_desired = {"username": "alice", "active": True}
    enable = client.post("/api/appliance/accounts/status/plan", json=enable_desired)
    assert enable.status_code == 200, enable.text
    assert (
        _approved_apply(
            client,
            action=ACCOUNT_STATUS_ACTION,
            plan=enable.json(),
            endpoint="/api/appliance/accounts/status/apply",
            desired=enable_desired,
        ).status_code
        == 200
    )
    alice_after_enable = token_for("alice", alice_password)

    client.cookies.set("echo_session", admin_token)
    password_desired = {"username": "alice", "newPassword": replacement}
    stale_desired = {
        "username": "alice",
        "newPassword": "Alice-stale-preview-Echo-63!",
    }
    stale_plan = client.post(
        "/api/appliance/accounts/password/plan",
        json=stale_desired,
    )
    assert stale_plan.status_code == 200, stale_plan.text
    password_plan = client.post(
        "/api/appliance/accounts/password/plan",
        json=password_desired,
    )
    assert password_plan.status_code == 200, password_plan.text
    assert replacement not in password_plan.text
    reset = _approved_apply(
        client,
        action=ACCOUNT_PASSWORD_ACTION,
        plan=password_plan.json(),
        endpoint="/api/appliance/accounts/password/apply",
        desired=password_desired,
    )
    assert reset.status_code == 200, reset.text
    assert replacement not in reset.text
    stale_apply = client.post(
        "/api/appliance/accounts/password/apply",
        json={"planId": stale_plan.json()["planId"], "desired": stale_desired},
    )
    assert stale_apply.status_code == 409
    client.cookies.clear()
    assert (
        client.get(
            "/api/appliance/accounts",
            headers={"Authorization": f"Bearer {alice_after_enable}"},
        ).status_code
        == 401
    )
    client.cookies.clear()
    assert (
        client.post(
            "/api/auth/local/login",
            headers={"Origin": "http://testserver"},
            json={"username": "alice", "password": alice_password},
        ).status_code
        == 401
    )
    assert token_for("alice", replacement)

    client.cookies.set("echo_session", admin_token)
    assert (
        client.post(
            "/api/appliance/accounts/unlink/plan",
            json={"username": "alice"},
        ).status_code
        == 409
    )
    disable_again = client.post(
        "/api/appliance/accounts/status/plan",
        json=disable_desired,
    )
    assert disable_again.status_code == 200
    assert (
        _approved_apply(
            client,
            action=ACCOUNT_STATUS_ACTION,
            plan=disable_again.json(),
            endpoint="/api/appliance/accounts/status/apply",
            desired=disable_desired,
        ).status_code
        == 200
    )
    unlink_desired = {"username": "alice"}
    unlink = client.post("/api/appliance/accounts/unlink/plan", json=unlink_desired)
    assert unlink.status_code == 200, unlink.text
    removed = _approved_apply(
        client,
        action=ACCOUNT_UNLINK_ACTION,
        plan=unlink.json(),
        endpoint="/api/appliance/accounts/unlink/apply",
        desired=unlink_desired,
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["unlinked"] is True
    assert "alice" not in auth_config.users
    assert "bob" in auth_config.users

    store_text = (tmp_path / "appliance-auth.json").read_text()
    audit_text = audit.path.read_text()
    assert replacement not in store_text
    assert replacement not in audit_text
    assert ACCOUNT_STATUS_ACTION in audit_text
    assert ACCOUNT_PASSWORD_ACTION in audit_text
    assert ACCOUNT_UNLINK_ACTION in audit_text


def test_link_plan_is_bound_to_member_password_and_existing_omv_user(
    tmp_path,
    monkeypatch,
) -> None:
    client, _auth_config, _audit = _configured_client(tmp_path, monkeypatch)
    desired = {
        "omvUsername": "alice",
        "displayName": "Alice",
        "password": "Alice-independent-Echo-42!",
    }
    first = client.post("/api/appliance/accounts/link/plan", json=desired)
    changed = client.post(
        "/api/appliance/accounts/link/plan",
        json={**desired, "password": "Alice-different-Echo-84!"},
    )
    missing = client.post(
        "/api/appliance/accounts/link/plan",
        json={
            "omvUsername": "charlie",
            "displayName": "Charlie",
            "password": "Charlie-independent-Echo-42!",
        },
    )

    assert first.status_code == 200
    assert changed.status_code == 200
    assert first.json()["planId"] != changed.json()["planId"]
    assert missing.status_code == 409


def test_invalid_credential_request_never_echoes_password(tmp_path, monkeypatch) -> None:
    client, _auth_config, _audit = _configured_client(tmp_path, monkeypatch)
    invalid_password = "secret-that-must-not-echo"

    response = client.post(
        "/api/appliance/accounts/link/plan",
        json={
            "omvUsername": "alice",
            "displayName": "Alice",
            "password": invalid_password,
            "unexpected": True,
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid account request"}
    assert invalid_password not in response.text


def test_link_plan_enforces_password_strength_and_utf8_byte_limit(
    tmp_path,
    monkeypatch,
) -> None:
    client, _auth_config, _audit = _configured_client(tmp_path, monkeypatch)

    for rejected_password in (
        "aaaaaaaaaaaa",
        "AliceAliceaa",
        "安全口令" * 10,
        "Valid-Password-42!\n",
    ):
        response = client.post(
            "/api/appliance/accounts/link/plan",
            json={
                "omvUsername": "alice",
                "displayName": "Alice",
                "password": rejected_password,
            },
        )

        assert response.status_code == 422
        assert rejected_password not in response.text
