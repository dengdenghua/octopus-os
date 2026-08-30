"""Account, user, and credential controls for the host OMV bridge."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from appliance.omv_bridge_contract import (
    _PLAN_ID_PATTERN,
    GROUP_PLAN_SCHEMA,
    HMAC_SAFETY_CONTRACT,
    USER_DESIRED_SCHEMA,
    USER_PASSWORD_DESIRED_SCHEMA,
    USER_PASSWORD_PLAN_SCHEMA,
    USER_PLAN_SCHEMA,
    _account_name,
    _canonical_hash,
    _integer,
    _safe_text,
    _validated_group_desired,
    _validated_user_desired,
    _validated_user_password_desired,
)
from appliance.omv_bridge_errors import (
    OmvBridgeConflict,
    OmvBridgeError,
    OmvBridgeValidationError,
)


class OmvAccountControlMixin:
    """Account control methods composed into the bridge service facade."""

    def _normal_users(self) -> list[dict[str, Any]]:
        payload = self._runner("UserMgmt", "enumerateUsers", {"detail": "basic"})
        if not isinstance(payload, list):
            raise OmvBridgeError("OMV user inventory response must be a list")
        users: list[dict[str, Any]] = []
        for item in payload[:1024]:
            if not isinstance(item, dict):
                continue
            name = _safe_text(item.get("name"), maximum=255)
            uid = _integer(item.get("uid"), maximum=2**31 - 1)
            gid = _integer(item.get("gid"), maximum=2**31 - 1)
            groups = item.get("groups")
            if not name or uid is None or gid is None or not isinstance(groups, list):
                continue
            normalized_groups = sorted(
                {group for value in groups[:64] if (group := _safe_text(value, maximum=255))}
            )
            users.append(
                {
                    "name": name,
                    "uid": uid,
                    "gid": gid,
                    "comment": _safe_text(item.get("comment"), maximum=65),
                    "shell": _safe_text(item.get("shell"), maximum=255),
                    "groups": normalized_groups,
                }
            )
        return sorted(users, key=lambda item: item["name"])

    def _normal_groups(self) -> list[dict[str, Any]]:
        payload = self._runner("UserMgmt", "enumerateGroups", {})
        if not isinstance(payload, list):
            raise OmvBridgeError("OMV group inventory response must be a list")
        groups: list[dict[str, Any]] = []
        for item in payload[:1024]:
            if not isinstance(item, dict):
                continue
            name = _safe_text(item.get("name"), maximum=255)
            gid = _integer(item.get("gid"), maximum=2**31 - 1)
            members = item.get("members")
            if not name or gid is None or not isinstance(members, list):
                continue
            normalized_members = sorted(
                {member for value in members[:1024] if (member := _safe_text(value, maximum=255))}
            )
            groups.append({"name": name, "gid": gid, "members": normalized_members})
        return sorted(groups, key=lambda item: item["name"])

    def plan_group(self, desired_state: Any) -> dict[str, Any]:
        desired = _validated_group_desired(desired_state)
        with self._control_lock:
            groups = self._normal_groups()
            if any(group["name"] == desired["name"] for group in groups):
                raise OmvBridgeConflict("OMV group already exists; Echo only creates new groups")
            base_revision = _canonical_hash(
                {"groups": groups, "users": [user["name"] for user in self._normal_users()]}
            )
            plan_id = _canonical_hash(
                {"schema": GROUP_PLAN_SCHEMA, "baseRevision": base_revision, "desired": desired}
            )
            return {
                "schema": GROUP_PLAN_SCHEMA,
                "planId": plan_id,
                "baseRevision": base_revision,
                "operation": "create",
                "requiresApproval": True,
                "desired": desired,
                "changes": [
                    {"field": "name", "before": None, "after": desired["name"]},
                    {"field": "comment", "before": None, "after": desired["comment"]},
                ],
                "safety": {
                    "scope": "newNormalOmvGroup",
                    "initialMembers": "empty",
                    "systemGroups": "never",
                    "update": "notManaged",
                    "delete": "rollbackOnlyBeforeUse",
                },
            }

    def apply_group(self, desired_state: Any, plan_id: Any) -> dict[str, Any]:
        desired = _validated_group_desired(desired_state)
        if not isinstance(plan_id, str) or _PLAN_ID_PATTERN.fullmatch(plan_id) is None:
            raise OmvBridgeValidationError("group plan ID is invalid")
        with self._control_lock:
            plan = self.plan_group(desired)
            if plan["planId"] != plan_id:
                raise OmvBridgeConflict("group plan is stale; preview the change again")
            mutation_started = False
            try:
                mutation_started = True
                self._runner(
                    "UserMgmt",
                    "setGroup",
                    {"name": desired["name"], "comment": desired["comment"], "members": []},
                )
                created = self._runner("UserMgmt", "getGroup", {"name": desired["name"]})
                if (
                    not isinstance(created, dict)
                    or created.get("name") != desired["name"]
                    or _safe_text(created.get("comment"), maximum=65) != desired["comment"]
                    or created.get("members") != []
                ):
                    raise OmvBridgeError("OMV did not persist the requested empty group")
            except Exception as exc:
                try:
                    if mutation_started:
                        self._runner("UserMgmt", "deleteGroup", {"name": desired["name"]})
                        if any(group["name"] == desired["name"] for group in self._normal_groups()):
                            raise OmvBridgeError("group rollback was not verified")
                except Exception as rollback_exc:
                    raise OmvBridgeError(
                        "OMV group creation failed and rollback also failed; inspect OMV immediately"
                    ) from rollback_exc
                raise OmvBridgeError("OMV group creation failed and was rolled back") from exc
            return {**plan, "applied": True, "verified": True}

    def _user_plan_id(self, base_revision: str, desired: dict[str, Any]) -> str:
        encoded = json.dumps(
            {"schema": USER_PLAN_SCHEMA, "baseRevision": base_revision, "desired": desired},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._plan_secret, encoded, hashlib.sha256).hexdigest()

    def plan_user(self, desired_state: Any) -> dict[str, Any]:
        desired = _validated_user_desired(desired_state)
        with self._control_lock:
            settings = self._runner("UserMgmt", "getSettings", {})
            if not isinstance(settings, dict) or not isinstance(settings.get("enable"), bool):
                raise OmvBridgeError("OMV user home-directory settings are invalid")
            if settings["enable"]:
                raise OmvBridgeConflict(
                    "OMV automatic user home directories are enabled; create this account in OMV until rollback-safe home handling is available"
                )
            users = self._normal_users()
            if any(user["name"] == desired["name"] for user in users):
                raise OmvBridgeConflict("OMV user already exists; Echo only creates new users")
            groups = self._normal_groups()
            available_groups = {group["name"] for group in groups}
            missing = [group for group in desired["groups"] if group not in available_groups]
            if missing:
                raise OmvBridgeValidationError("one or more requested OMV groups do not exist")
            base_revision = _canonical_hash(
                {
                    "users": users,
                    "groups": groups,
                    "homeDirectoriesEnabled": False,
                }
            )
            plan_id = self._user_plan_id(base_revision, desired)
            safe_desired = {
                "schema": USER_DESIRED_SCHEMA,
                "name": desired["name"],
                "displayName": desired["displayName"],
                "groups": desired["groups"],
                "passwordBound": True,
            }
            return {
                "schema": USER_PLAN_SCHEMA,
                "planId": plan_id,
                "baseRevision": base_revision,
                "operation": "create",
                "requiresApproval": True,
                "desired": safe_desired,
                "changes": [
                    {"field": "name", "before": None, "after": desired["name"]},
                    {
                        "field": "displayName",
                        "before": None,
                        "after": desired["displayName"],
                    },
                    {"field": "groups", "before": [], "after": desired["groups"]},
                ],
                "safety": {
                    "scope": "newNormalOmvUser",
                    "password": HMAC_SAFETY_CONTRACT,
                    "loginShell": "nologin",
                    "sshKeys": "none",
                    "homeDirectory": "automaticHomesMustBeDisabled",
                    "systemGroups": "notEnumeratedNotSelectable",
                    "update": "notManaged",
                    "delete": "rollbackOnlyBeforeUse",
                },
            }

    def apply_user(self, desired_state: Any, plan_id: Any) -> dict[str, Any]:
        desired = _validated_user_desired(desired_state)
        if not isinstance(plan_id, str) or _PLAN_ID_PATTERN.fullmatch(plan_id) is None:
            raise OmvBridgeValidationError("user plan ID is invalid")
        with self._control_lock:
            plan = self.plan_user(desired)
            if not hmac.compare_digest(plan["planId"], plan_id):
                raise OmvBridgeConflict("user plan is stale; preview the change again")
            if self._secret_runner is None:
                raise OmvBridgeError("OMV secret user creation transport is unavailable")
            mutation_started = False
            try:
                mutation_started = True
                self._secret_runner(
                    {
                        "name": desired["name"],
                        "groups": desired["groups"],
                        "shell": "/usr/sbin/nologin",
                        "password": desired["password"],
                        "email": "",
                        "comment": desired["displayName"],
                        "disallowusermod": True,
                        "sshpubkeys": [],
                    }
                )
                matches = [user for user in self._normal_users() if user["name"] == desired["name"]]
                if len(matches) != 1:
                    raise OmvBridgeError("OMV did not enumerate the created user")
                observed = matches[0]
                supplemental_groups = [group for group in observed["groups"] if group != "users"]
                if (
                    observed["comment"] != desired["displayName"]
                    or supplemental_groups != desired["groups"]
                    or observed["shell"] != "/usr/sbin/nologin"
                ):
                    raise OmvBridgeError("OMV did not persist the constrained user settings")
            except Exception as exc:
                try:
                    if mutation_started:
                        self._runner("UserMgmt", "deleteUser", {"name": desired["name"]})
                        if any(user["name"] == desired["name"] for user in self._normal_users()):
                            raise OmvBridgeError("user rollback was not verified")
                except Exception as rollback_exc:
                    raise OmvBridgeError(
                        "OMV user creation failed and rollback also failed; inspect OMV immediately"
                    ) from rollback_exc
                raise OmvBridgeError("OMV user creation failed and was rolled back") from exc
            return {**plan, "applied": True, "verified": True}

    def _password_reset_account(self, name: str) -> dict[str, Any]:
        if not any(user["name"] == name for user in self._normal_users()):
            raise OmvBridgeConflict("OMV user does not exist")
        payload = self._runner("UserMgmt", "getUser", {"name": name})
        if not isinstance(payload, dict) or payload.get("name") != name:
            raise OmvBridgeConflict("OMV user does not exist")
        uid = _integer(payload.get("uid"), maximum=2**31 - 1)
        gid = _integer(payload.get("gid"), maximum=2**31 - 1)
        comment = _safe_text(payload.get("comment"), maximum=65)
        shell = _safe_text(payload.get("shell"), maximum=255)
        email = _safe_text(payload.get("email"), maximum=320)
        groups_value = payload.get("groups")
        ssh_keys = payload.get("sshpubkeys")
        if (
            uid is None
            or gid is None
            or not comment
            or shell != "/usr/sbin/nologin"
            or email != ""
            or payload.get("disallowusermod") is not True
            or ssh_keys != []
            or not isinstance(groups_value, list)
        ):
            raise OmvBridgeConflict(
                "OMV user is not an Echo-constrained family member; reset it in OMV"
            )
        groups = sorted(
            {
                group
                for value in groups_value[:64]
                if (group := _safe_text(value, maximum=255)) and group != "users"
            }
        )
        if len(groups) != len([value for value in groups_value if value != "users"]):
            raise OmvBridgeConflict("OMV user groups are not safe for Echo password reset")
        try:
            validated_groups = [_account_name(group, "group") for group in groups]
        except OmvBridgeValidationError as exc:
            raise OmvBridgeConflict("OMV user groups are not safe for Echo password reset") from exc
        return {
            "name": name,
            "uid": uid,
            "gid": gid,
            "comment": comment,
            "shell": shell,
            "email": "",
            "groups": validated_groups,
            "disallowusermod": True,
            "sshpubkeys": [],
        }

    def _user_password_plan_id(self, base_revision: str, desired: dict[str, str]) -> str:
        encoded = json.dumps(
            {
                "schema": USER_PASSWORD_PLAN_SCHEMA,
                "baseRevision": base_revision,
                "desired": desired,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._plan_secret, encoded, hashlib.sha256).hexdigest()

    def plan_user_password(self, desired_state: Any) -> dict[str, Any]:
        desired = _validated_user_password_desired(desired_state)
        with self._control_lock:
            account = self._password_reset_account(desired["name"])
            base_revision = _canonical_hash(account)
            plan_id = self._user_password_plan_id(base_revision, desired)
            return {
                "schema": USER_PASSWORD_PLAN_SCHEMA,
                "planId": plan_id,
                "baseRevision": base_revision,
                "operation": "resetPassword",
                "requiresApproval": True,
                "desired": {
                    "schema": USER_PASSWORD_DESIRED_SCHEMA,
                    "name": desired["name"],
                    "passwordBound": True,
                },
                "changes": [
                    {
                        "field": "password",
                        "before": "currentCredential",
                        "after": "replacementCredential",
                    }
                ],
                "safety": {
                    "scope": "existingConstrainedNormalOmvUser",
                    "password": HMAC_SAFETY_CONTRACT,
                    "accountFields": "preservedAndVerified",
                    "loginShell": "nologin",
                    "sshKeys": "none",
                    "rollback": "notAvailableAfterAcceptedSecretRpc",
                },
            }

    def apply_user_password(self, desired_state: Any, plan_id: Any) -> dict[str, Any]:
        desired = _validated_user_password_desired(desired_state)
        if not isinstance(plan_id, str) or _PLAN_ID_PATTERN.fullmatch(plan_id) is None:
            raise OmvBridgeValidationError("user password plan ID is invalid")
        with self._control_lock:
            plan = self.plan_user_password(desired)
            if not hmac.compare_digest(plan["planId"], plan_id):
                raise OmvBridgeConflict("user password plan is stale; preview the change again")
            if self._secret_runner is None:
                raise OmvBridgeError("OMV secret user password transport is unavailable")
            account = self._password_reset_account(desired["name"])
            try:
                self._secret_runner(
                    {
                        "name": account["name"],
                        "groups": account["groups"],
                        "shell": account["shell"],
                        "password": desired["password"],
                        "email": account["email"],
                        "comment": account["comment"],
                        "disallowusermod": account["disallowusermod"],
                        "sshpubkeys": account["sshpubkeys"],
                    }
                )
            except Exception as exc:
                raise OmvBridgeError(
                    "OMV password reset did not complete; credential state may be indeterminate, preview and retry"
                ) from exc
            observed = self._password_reset_account(desired["name"])
            if observed != account:
                raise OmvBridgeError(
                    "OMV password reset changed account constraints; inspect OMV immediately"
                )
            return {**plan, "applied": True, "verified": True}


__all__ = ["OmvAccountControlMixin"]
