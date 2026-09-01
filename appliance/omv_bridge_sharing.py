"""Shared-folder, privilege, SMB, and NFS controls for the host OMV bridge."""

from __future__ import annotations

from typing import Any

from appliance.omv_bridge_contract import (
    _NFS_MANAGED_EXTRA_OPTIONS,
    _OMV_UUID_PATTERN,
    _PERMS_TO_PRIVILEGE,
    _PLAN_ID_PATTERN,
    _PRIVILEGE_TO_PERMS,
    _SHARE_LIST_PARAMS,
    NFS_PLAN_SCHEMA,
    OMV_CONFIGOBJECT_NEW_UUID,
    SHARE_PRIVILEGE_PLAN_SCHEMA,
    SHARED_FOLDER_PLAN_SCHEMA,
    SMB_PLAN_SCHEMA,
    _boolean,
    _canonical_hash,
    _integer,
    _privilege_sort_key,
    _uuid_from_plan,
    _validated_nfs_desired,
    _validated_nfs_share,
    _validated_share_privilege_desired,
    _validated_shared_folder_config,
    _validated_shared_folder_desired,
    _validated_smb_desired,
    _validated_smb_share,
)
from appliance.omv_bridge_errors import (
    OmvBridgeConflict,
    OmvBridgeError,
    OmvBridgeValidationError,
)


class OmvSharingControlMixin:
    """Sharing control methods composed into the bridge service facade."""

    def _privilege_inventory(self, share_uuid: str) -> list[dict[str, Any]]:
        if _OMV_UUID_PATTERN.fullmatch(share_uuid) is None:
            raise OmvBridgeError("shared folder UUID is invalid")
        allowed = {share["uuid"].lower() for share in self.shared_folders()}
        normalized_uuid = share_uuid.lower()
        if normalized_uuid not in allowed:
            raise OmvBridgeError("shared folder is not enumerated")
        payload = self._runner("ShareMgmt", "getPrivileges", {"uuid": normalized_uuid})
        if not isinstance(payload, list) or len(payload) > 2048:
            raise OmvBridgeError("OMV privilege response must be a bounded list")
        result: list[dict[str, Any]] = []
        identities: set[tuple[str, str]] = set()
        for item in payload:
            if not isinstance(item, dict):
                raise OmvBridgeError("OMV privilege entry is invalid")
            role_type = item.get("type")
            name = item.get("name")
            identifier = _integer(item.get("id"), maximum=2**31 - 1)
            perms = item.get("perms")
            if (
                role_type not in {"user", "group"}
                or not isinstance(name, str)
                or not name
                or name != name.strip()
                or len(name) > 255
                or any(character < " " for character in name)
                or identifier is None
                or isinstance(perms, bool)
                or perms not in _PERMS_TO_PRIVILEGE
            ):
                raise OmvBridgeError("OMV privilege entry is invalid")
            identity = (role_type, name)
            if identity in identities:
                raise OmvBridgeError("OMV returned duplicate privilege principals")
            identities.add(identity)
            result.append(
                {
                    "type": role_type,
                    "id": identifier,
                    "name": name,
                    "perms": perms,
                }
            )
        return sorted(result, key=_privilege_sort_key)

    def share_privileges(self, share_uuid: str) -> list[dict[str, Any]]:
        return [
            {
                "type": item["type"],
                "id": item["id"],
                "name": item["name"],
                "permission": _PERMS_TO_PRIVILEGE[item["perms"]],
            }
            for item in self._privilege_inventory(share_uuid)
        ]

    @staticmethod
    def _configured_privileges(inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"type": item["type"], "name": item["name"], "perms": item["perms"]}
            for item in inventory
            if item["perms"] is not None
        ]

    def _assert_privilege_modules_clean(self) -> None:
        for module in ("samba", "rsyncd"):
            dirty = self._runner("Config", "isDirty", {"modules": [module]})
            if not isinstance(dirty, bool):
                raise OmvBridgeError("OMV dirty-state response must be boolean")
            if dirty:
                raise OmvBridgeConflict(
                    f"OMV has unapplied {module} changes; apply or revert them in OMV first"
                )

    def _apply_privilege_modules(self) -> list[str]:
        deployed: list[str] = []
        for module in ("samba", "rsyncd"):
            dirty = self._runner("Config", "isDirty", {"modules": [module]})
            if not isinstance(dirty, bool):
                raise OmvBridgeError("OMV dirty-state response must be boolean")
            if not dirty:
                continue
            result = self._runner(
                "Config",
                "applyChanges",
                {"modules": [module], "force": False},
            )
            if (
                not isinstance(result, list)
                or any(not isinstance(item, str) for item in result)
                or module not in result
            ):
                raise OmvBridgeError(f"OMV did not deploy the {module} configuration")
            deployed.append(module)
        return deployed

    def _share_privilege_context(
        self,
        desired: dict[str, str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        folder = next(
            (
                item
                for item in self.shared_folders()
                if item["uuid"].lower() == desired["sharedFolderRef"]
            ),
            None,
        )
        if folder is None:
            raise OmvBridgeValidationError("shared folder is not enumerated by OMV")
        if folder["status"].casefold() not in {"ok", "online"}:
            raise OmvBridgeConflict("shared folder is not online")
        inventory = self._privilege_inventory(desired["sharedFolderRef"])
        matching = [
            item
            for item in inventory
            if item["type"] == desired["principalType"] and item["name"] == desired["principalName"]
        ]
        if len(matching) != 1:
            raise OmvBridgeValidationError("principal is not enumerated by OMV")
        return folder, inventory, matching[0]

    def plan_share_privilege(self, desired_state: Any) -> dict[str, Any]:
        desired = _validated_share_privilege_desired(desired_state)
        with self._control_lock:
            self._assert_privilege_modules_clean()
            folder, inventory, principal = self._share_privilege_context(desired)
            configured = self._configured_privileges(inventory)
            current = _PERMS_TO_PRIVILEGE[principal["perms"]]
            base_revision = _canonical_hash(
                {
                    "sharedFolder": {
                        "uuid": folder["uuid"].lower(),
                        "name": folder["name"],
                        "status": folder["status"],
                    },
                    "configuredPrivileges": configured,
                }
            )
            plan_id = _canonical_hash(
                {
                    "schema": SHARE_PRIVILEGE_PLAN_SCHEMA,
                    "baseRevision": base_revision,
                    "desired": desired,
                }
            )
            operation = "none" if current == desired["permission"] else "update"
            return {
                "schema": SHARE_PRIVILEGE_PLAN_SCHEMA,
                "planId": plan_id,
                "baseRevision": base_revision,
                "operation": operation,
                "requiresApproval": operation != "none",
                "sharedFolder": {
                    "uuid": folder["uuid"].lower(),
                    "name": folder["name"],
                    "status": folder["status"],
                },
                "principal": {
                    "type": principal["type"],
                    "id": principal["id"],
                    "name": principal["name"],
                    "before": current,
                    "after": desired["permission"],
                },
                "desired": desired,
                "changes": (
                    []
                    if operation == "none"
                    else [
                        {
                            "field": "permission",
                            "before": current,
                            "after": desired["permission"],
                        }
                    ]
                ),
                "safety": {
                    "scope": "sharedFolderConfigPrivilege",
                    "principal": "existingOmvUserOrGroup",
                    "filesystemAcl": "notModified",
                    "recursive": "never",
                    "serviceDeploy": "sambaAndRsyncdWhenDirty",
                    "delete": "notManaged",
                },
            }

    def apply_share_privilege(self, desired_state: Any, plan_id: Any) -> dict[str, Any]:
        desired = _validated_share_privilege_desired(desired_state)
        if not isinstance(plan_id, str) or _PLAN_ID_PATTERN.fullmatch(plan_id) is None:
            raise OmvBridgeValidationError("share privilege plan ID is invalid")
        with self._control_lock:
            plan = self.plan_share_privilege(desired)
            if plan["planId"] != plan_id:
                raise OmvBridgeConflict("share privilege plan is stale; preview the change again")
            if plan["operation"] == "none":
                return {
                    **plan,
                    "applied": False,
                    "verified": True,
                    "deployedServices": [],
                }

            folder, inventory, principal = self._share_privilege_context(desired)
            original = self._configured_privileges(inventory)
            observed_revision = _canonical_hash(
                {
                    "sharedFolder": {
                        "uuid": folder["uuid"].lower(),
                        "name": folder["name"],
                        "status": folder["status"],
                    },
                    "configuredPrivileges": original,
                }
            )
            if observed_revision != plan["baseRevision"]:
                raise OmvBridgeConflict(
                    "share privilege changed during apply; preview the change again"
                )
            self._assert_privilege_modules_clean()
            wanted = [
                item
                for item in original
                if (item["type"], item["name"])
                != (desired["principalType"], desired["principalName"])
            ]
            wanted_perms = _PRIVILEGE_TO_PERMS[desired["permission"]]
            if wanted_perms is not None:
                wanted.append(
                    {
                        "type": principal["type"],
                        "name": principal["name"],
                        "perms": wanted_perms,
                    }
                )
            wanted.sort(key=_privilege_sort_key)

            mutation_started = False
            try:
                mutation_started = True
                self._runner(
                    "ShareMgmt",
                    "setPrivileges",
                    {"uuid": desired["sharedFolderRef"], "privileges": wanted},
                )
                deployed = self._apply_privilege_modules()
                observed = self._configured_privileges(
                    self._privilege_inventory(desired["sharedFolderRef"])
                )
                if observed != wanted:
                    raise OmvBridgeError("OMV did not persist the requested share privilege")
            except Exception as exc:
                try:
                    if mutation_started:
                        self._runner(
                            "ShareMgmt",
                            "setPrivileges",
                            {"uuid": desired["sharedFolderRef"], "privileges": original},
                        )
                        self._apply_privilege_modules()
                        restored = self._configured_privileges(
                            self._privilege_inventory(desired["sharedFolderRef"])
                        )
                        if restored != original:
                            raise OmvBridgeError("share privilege rollback was not verified")
                except Exception as rollback_exc:
                    raise OmvBridgeError(
                        "OMV share privilege update failed and rollback also failed; inspect OMV immediately"
                    ) from rollback_exc
                if isinstance(exc, OmvBridgeError):
                    raise
                raise OmvBridgeError("OMV share privilege update failed") from exc
            return {
                **plan,
                "applied": True,
                "verified": True,
                "deployedServices": deployed,
            }

    def _shared_folder_creation_context(
        self,
        desired: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str] | None]:
        target = next(
            (
                item
                for item in self.shared_folder_targets()
                if item["mountPointRef"] == desired["mountPointRef"]
            ),
            None,
        )
        if target is None:
            raise OmvBridgeConflict(
                "shared folder target must be an existing mounted writable OMV filesystem"
            )
        wanted_path = f"{desired['name']}/"
        exact: dict[str, str] | None = None
        for item in self._raw_shared_folders():
            if not isinstance(item, dict):
                continue
            raw_name = item.get("name")
            raw_mount = item.get("mntentref")
            raw_path = item.get("reldirpath")
            if not isinstance(raw_name, str) or not isinstance(raw_mount, str):
                continue
            same_name = raw_name.casefold() == desired["name"].casefold()
            same_path = raw_mount.lower() == desired["mountPointRef"] and raw_path == wanted_path
            if not same_name and not same_path:
                continue
            existing = _validated_shared_folder_config(item)
            if (
                existing["name"] == desired["name"]
                and existing["mntentref"] == desired["mountPointRef"]
                and existing["reldirpath"] == wanted_path
                and existing["comment"] == desired["comment"]
                and str(item.get("status", "")).casefold() in {"ok", "online"}
            ):
                if exact is not None and exact["uuid"] != existing["uuid"]:
                    raise OmvBridgeConflict("OMV returned duplicate matching shared folders")
                exact = existing
                continue
            raise OmvBridgeConflict(
                "shared folder name or derived directory already exists with different settings"
            )
        return target, exact

    def plan_shared_folder(self, desired_state: Any) -> dict[str, Any]:
        desired = _validated_shared_folder_desired(desired_state)
        with self._control_lock:
            target, existing = self._shared_folder_creation_context(desired)
            base_revision = _canonical_hash({"target": target, "existing": existing})
            plan_id = _canonical_hash(
                {
                    "schema": SHARED_FOLDER_PLAN_SCHEMA,
                    "baseRevision": base_revision,
                    "desired": desired,
                }
            )
            operation = "none" if existing is not None else "create"
            changes = (
                []
                if existing is not None
                else [
                    {"field": "name", "before": None, "after": desired["name"]},
                    {
                        "field": "comment",
                        "before": None,
                        "after": desired["comment"],
                    },
                ]
            )
            return {
                "schema": SHARED_FOLDER_PLAN_SCHEMA,
                "planId": plan_id,
                "baseRevision": base_revision,
                "operation": operation,
                "requiresApproval": operation == "create",
                "shareUuid": existing["uuid"] if existing else _uuid_from_plan(plan_id),
                "target": target,
                "desired": desired,
                "changes": changes,
                "safety": {
                    "filesystem": "existingMountedWritableOnly",
                    "relativePath": "derivedFromPortableName",
                    "directoryMode": "2770UsersGroup",
                    "acl": "notManaged",
                    "update": "notManaged",
                    "delete": "notManaged",
                },
            }

    def apply_shared_folder(self, desired_state: Any, plan_id: Any) -> dict[str, Any]:
        desired = _validated_shared_folder_desired(desired_state)
        if not isinstance(plan_id, str) or _PLAN_ID_PATTERN.fullmatch(plan_id) is None:
            raise OmvBridgeValidationError("shared folder plan ID is invalid")
        with self._control_lock:
            plan = self.plan_shared_folder(desired)
            if plan["planId"] != plan_id:
                raise OmvBridgeConflict("shared folder plan is stale; preview the change again")
            if plan["operation"] == "none":
                return {**plan, "applied": False, "verified": True}

            created_uuid: str | None = None
            try:
                result = self._runner(
                    "ShareMgmt",
                    "set",
                    {
                        "uuid": OMV_CONFIGOBJECT_NEW_UUID,
                        "name": desired["name"],
                        "reldirpath": desired["name"],
                        "comment": desired["comment"],
                        "mntentref": desired["mountPointRef"],
                        "mode": "770",
                    },
                )
                created = _validated_shared_folder_config(result)
                created_uuid = created["uuid"]
                if created_uuid == OMV_CONFIGOBJECT_NEW_UUID:
                    raise OmvBridgeError("OMV did not allocate a shared folder UUID")
                observed = _validated_shared_folder_config(
                    self._runner("ShareMgmt", "get", {"uuid": created_uuid})
                )
                if observed != {
                    "uuid": created_uuid,
                    "mntentref": desired["mountPointRef"],
                    "name": desired["name"],
                    "reldirpath": f"{desired['name']}/",
                    "comment": desired["comment"],
                }:
                    raise OmvBridgeError("OMV did not persist the requested shared folder")
                listed = [
                    item
                    for item in self._raw_shared_folders()
                    if isinstance(item, dict)
                    and isinstance(item.get("uuid"), str)
                    and item["uuid"].lower() == created_uuid
                ]
                if len(listed) != 1 or str(listed[0].get("status", "")).casefold() not in {
                    "ok",
                    "online",
                }:
                    raise OmvBridgeError("OMV did not create an online shared folder directory")
            except Exception as exc:
                try:
                    rollback_uuid = created_uuid
                    if rollback_uuid is not None:
                        self._runner(
                            "ShareMgmt",
                            "delete",
                            {"uuid": rollback_uuid, "recursive": False},
                        )
                        if any(
                            isinstance(item, dict)
                            and isinstance(item.get("uuid"), str)
                            and item["uuid"].lower() == rollback_uuid
                            for item in self._raw_shared_folders()
                        ):
                            raise OmvBridgeError("shared folder rollback was not verified")
                except Exception as rollback_exc:
                    raise OmvBridgeError(
                        "OMV shared folder creation failed and rollback also failed; inspect OMV immediately"
                    ) from rollback_exc
                if isinstance(exc, OmvBridgeError):
                    raise
                raise OmvBridgeError("OMV shared folder creation failed") from exc
            return {
                **plan,
                "shareUuid": created_uuid,
                "applied": True,
                "verified": True,
            }

    def _smb_share_list(self) -> list[dict[str, Any]]:
        payload = self._runner("SMB", "getShareList", dict(_SHARE_LIST_PARAMS))
        result: list[dict[str, Any]] = []
        for item in self._share_list_data(payload, "SMB share list"):
            if not isinstance(item, dict):
                continue
            share_uuid = item.get("uuid")
            folder_ref = item.get("sharedfolderref")
            if (
                isinstance(share_uuid, str)
                and _OMV_UUID_PATTERN.fullmatch(share_uuid)
                and isinstance(folder_ref, str)
                and _OMV_UUID_PATTERN.fullmatch(folder_ref)
            ):
                result.append(
                    {
                        "uuid": share_uuid.lower(),
                        "sharedFolderRef": folder_ref.lower(),
                    }
                )
        return result

    def _assert_smb_clean(self) -> None:
        dirty = self._runner("Config", "isDirty", {"modules": ["samba"]})
        if not isinstance(dirty, bool):
            raise OmvBridgeError("OMV dirty-state response must be boolean")
        if dirty:
            raise OmvBridgeConflict(
                "OMV has unapplied SMB changes; apply or revert them in OMV first"
            )

    @staticmethod
    def _controlled_values(share: dict[str, Any] | None) -> dict[str, Any] | None:
        if share is None:
            return None
        return {
            "enabled": share["enable"],
            "readOnly": share["readonly"],
            "browseable": share["browseable"],
            "recycleBin": share["recyclebin"],
            "comment": share["comment"],
        }

    @staticmethod
    def _desired_values(desired: dict[str, Any]) -> dict[str, Any]:
        return {
            "enabled": desired["enabled"],
            "readOnly": desired["readOnly"],
            "browseable": desired["browseable"],
            "recycleBin": desired["recycleBin"],
            "comment": desired["comment"],
        }

    @staticmethod
    def _assert_simple_private_share(share: dict[str, Any]) -> None:
        if (
            share["guest"] != "no"
            or share["hostsallow"]
            or share["hostsdeny"]
            or share["extraoptions"]
        ):
            raise OmvBridgeConflict(
                "existing SMB share uses advanced or guest settings; manage it in OMV"
            )

    def _smb_context(
        self,
        desired: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        settings = self._runner("SMB", "getSettings", {})
        if not isinstance(settings, dict) or not _boolean(settings.get("enable")):
            raise OmvBridgeConflict("the OMV SMB service must be enabled first")

        folders = self.shared_folders()
        folder = next(
            (item for item in folders if item["uuid"].lower() == desired["sharedFolderRef"]),
            None,
        )
        if folder is None:
            raise OmvBridgeValidationError("shared folder is not enumerated by OMV")
        if folder["status"].casefold() not in {"ok", "online"}:
            raise OmvBridgeConflict("shared folder is not online")

        matching = [
            item
            for item in self._smb_share_list()
            if item["sharedFolderRef"] == desired["sharedFolderRef"]
        ]
        if len(matching) > 1:
            raise OmvBridgeConflict("OMV returned duplicate SMB shares for one folder")
        existing = None
        if matching:
            payload = self._runner("SMB", "getShare", {"uuid": matching[0]["uuid"]})
            existing = _validated_smb_share(payload)
            if existing["sharedfolderref"] != desired["sharedFolderRef"]:
                raise OmvBridgeConflict("OMV SMB share changed during planning")
            self._assert_simple_private_share(existing)
        return folder, existing

    def plan_smb_share(self, desired_state: Any) -> dict[str, Any]:
        desired = _validated_smb_desired(desired_state)
        with self._control_lock:
            self._assert_smb_clean()
            folder, existing = self._smb_context(desired)
            current = self._controlled_values(existing)
            wanted = self._desired_values(desired)
            base_revision = _canonical_hash(
                {
                    "folder": {
                        "uuid": folder["uuid"].lower(),
                        "name": folder["name"],
                        "status": folder["status"],
                    },
                    "share": existing,
                }
            )
            plan_id = _canonical_hash(
                {
                    "schema": SMB_PLAN_SCHEMA,
                    "baseRevision": base_revision,
                    "desired": desired,
                }
            )
            changes = [
                {
                    "field": field,
                    "before": None if current is None else current[field],
                    "after": after,
                }
                for field, after in wanted.items()
                if current is None or current[field] != after
            ]
            operation = "create" if existing is None else ("update" if changes else "none")
            return {
                "schema": SMB_PLAN_SCHEMA,
                "planId": plan_id,
                "baseRevision": base_revision,
                "operation": operation,
                "requiresApproval": operation != "none",
                "shareUuid": existing["uuid"] if existing else _uuid_from_plan(plan_id),
                "sharedFolder": {
                    "uuid": folder["uuid"].lower(),
                    "name": folder["name"],
                    "status": folder["status"],
                },
                "desired": desired,
                "changes": changes,
                "safety": {
                    "guestAccess": "disabled",
                    "advancedOptions": "notManaged",
                    "acl": "notManaged",
                },
            }

    @staticmethod
    def _new_smb_share(plan: dict[str, Any]) -> dict[str, Any]:
        desired = plan["desired"]
        return {
            # OMV does not accept a caller-selected UUID for new config
            # objects. Its well-known sentinel selects the create path and
            # OMV returns the generated UUID from SMB.setShare.
            "uuid": OMV_CONFIGOBJECT_NEW_UUID,
            "enable": desired["enabled"],
            "sharedfolderref": desired["sharedFolderRef"],
            "comment": desired["comment"],
            "guest": "no",
            "readonly": desired["readOnly"],
            "browseable": desired["browseable"],
            "recyclebin": desired["recycleBin"],
            "recyclemaxsize": 0,
            "recyclemaxage": 0,
            "hidedotfiles": True,
            "inheritacls": True,
            "inheritpermissions": False,
            "easupport": True,
            "storedosattributes": True,
            "hostsallow": "",
            "hostsdeny": "",
            "audit": False,
            "timemachine": False,
            "extraoptions": "",
        }

    def _apply_smb_config(self) -> None:
        result = self._runner(
            "Config",
            "applyChanges",
            {"modules": ["samba"], "force": False},
        )
        if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
            raise OmvBridgeError("OMV apply response must be a module list")
        if "samba" not in result:
            raise OmvBridgeError("OMV did not deploy the Samba configuration")

    def apply_smb_share(self, desired_state: Any, plan_id: Any) -> dict[str, Any]:
        desired = _validated_smb_desired(desired_state)
        if not isinstance(plan_id, str) or _PLAN_ID_PATTERN.fullmatch(plan_id) is None:
            raise OmvBridgeValidationError("SMB plan ID is invalid")
        with self._control_lock:
            plan = self.plan_smb_share(desired)
            if plan["planId"] != plan_id:
                raise OmvBridgeConflict("SMB plan is stale; preview the change again")
            if plan["operation"] == "none":
                return {**plan, "applied": False, "verified": True}

            original = None
            if plan["operation"] == "update":
                original = _validated_smb_share(
                    self._runner("SMB", "getShare", {"uuid": plan["shareUuid"]})
                )
                self._assert_simple_private_share(original)
                desired_share = dict(original)
                desired_share.update(
                    {
                        "enable": desired["enabled"],
                        "comment": desired["comment"],
                        "readonly": desired["readOnly"],
                        "browseable": desired["browseable"],
                        "recyclebin": desired["recycleBin"],
                    }
                )
            else:
                desired_share = self._new_smb_share(plan)
            desired_share = _validated_smb_share(desired_share)

            mutation_started = False
            created_share_uuid: str | None = None
            try:
                set_result = self._runner("SMB", "setShare", desired_share)
                mutation_started = True
                if original is None:
                    created_share = _validated_smb_share(set_result)
                    self._assert_simple_private_share(created_share)
                    created_share_uuid = created_share["uuid"]
                    if created_share_uuid == OMV_CONFIGOBJECT_NEW_UUID:
                        raise OmvBridgeError("OMV did not allocate an SMB share UUID")
                self._apply_smb_config()
                observed_share_uuid = created_share_uuid or plan["shareUuid"]
                observed = _validated_smb_share(
                    self._runner("SMB", "getShare", {"uuid": observed_share_uuid})
                )
                if (
                    observed["sharedfolderref"] != desired["sharedFolderRef"]
                    or self._controlled_values(observed) != self._desired_values(desired)
                    or observed["guest"] != "no"
                ):
                    raise OmvBridgeError("OMV did not persist the requested SMB state")
            except Exception as exc:
                if mutation_started:
                    try:
                        if original is None:
                            rollback_uuid = created_share_uuid
                            if rollback_uuid is None:
                                matches = [
                                    item["uuid"]
                                    for item in self._smb_share_list()
                                    if item["sharedFolderRef"] == desired["sharedFolderRef"]
                                ]
                                if len(matches) != 1:
                                    raise OmvBridgeError(
                                        "the newly created SMB share could not be identified"
                                    )
                                rollback_uuid = matches[0]
                            self._runner("SMB", "deleteShare", {"uuid": rollback_uuid})
                        else:
                            self._runner("SMB", "setShare", original)
                        self._apply_smb_config()
                    except Exception as rollback_exc:
                        raise OmvBridgeError(
                            "OMV SMB apply failed and rollback also failed; inspect OMV immediately"
                        ) from rollback_exc
                if isinstance(exc, OmvBridgeError):
                    raise
                raise OmvBridgeError("OMV SMB apply failed") from exc
            return {
                **plan,
                "shareUuid": observed["uuid"],
                "applied": True,
                "verified": True,
            }

    def _nfs_share_list(self) -> list[dict[str, str]]:
        payload = self._runner("NFS", "getShareList", dict(_SHARE_LIST_PARAMS))
        result: list[dict[str, str]] = []
        for item in self._share_list_data(payload, "NFS share list"):
            if not isinstance(item, dict):
                continue
            share_uuid = item.get("uuid")
            folder_ref = item.get("sharedfolderref")
            client = item.get("client")
            if (
                isinstance(share_uuid, str)
                and _OMV_UUID_PATTERN.fullmatch(share_uuid)
                and isinstance(folder_ref, str)
                and _OMV_UUID_PATTERN.fullmatch(folder_ref)
                and isinstance(client, str)
                and len(client) <= 512
            ):
                result.append(
                    {
                        "uuid": share_uuid.lower(),
                        "sharedFolderRef": folder_ref.lower(),
                        "client": client,
                    }
                )
        return result

    def _assert_nfs_clean(self) -> None:
        dirty = self._runner("Config", "isDirty", {"modules": ["nfs"]})
        if not isinstance(dirty, bool):
            raise OmvBridgeError("OMV NFS dirty-state response must be boolean")
        if dirty:
            raise OmvBridgeConflict(
                "OMV has unapplied NFS changes; apply or revert them in OMV first"
            )

    @staticmethod
    def _assert_managed_nfs_share(share: dict[str, Any], desired: dict[str, Any]) -> None:
        if (
            share["client"] != desired["clientCidr"]
            or share["options"] not in {"ro", "rw"}
            or share["extraoptions"] != _NFS_MANAGED_EXTRA_OPTIONS
        ):
            raise OmvBridgeConflict(
                "existing NFS share uses a different client or advanced options; manage it in OMV"
            )

    def _nfs_context(
        self,
        desired: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        settings = self._runner("NFS", "getSettings", {})
        if not isinstance(settings, dict) or not _boolean(settings.get("enable")):
            raise OmvBridgeConflict("the OMV NFS service must be enabled first")
        folder = next(
            (
                item
                for item in self.shared_folders()
                if item["uuid"].lower() == desired["sharedFolderRef"]
            ),
            None,
        )
        if folder is None:
            raise OmvBridgeValidationError("shared folder is not enumerated by OMV")
        if folder["status"].casefold() not in {"ok", "online"}:
            raise OmvBridgeConflict("shared folder is not online")
        if " " in folder["relativePath"]:
            raise OmvBridgeConflict("NFS cannot publish a shared-folder path containing spaces")

        matching = [
            item
            for item in self._nfs_share_list()
            if item["sharedFolderRef"] == desired["sharedFolderRef"]
            and item["client"] == desired["clientCidr"]
        ]
        if len(matching) > 1:
            raise OmvBridgeConflict("OMV returned duplicate NFS shares for one client network")
        existing = None
        if matching:
            existing = _validated_nfs_share(
                self._runner("NFS", "getShare", {"uuid": matching[0]["uuid"]})
            )
            if existing["sharedfolderref"] != desired["sharedFolderRef"]:
                raise OmvBridgeConflict("OMV NFS share changed during planning")
            self._assert_managed_nfs_share(existing, desired)
        return folder, existing

    @staticmethod
    def _nfs_controlled_values(share: dict[str, Any] | None) -> dict[str, Any] | None:
        if share is None:
            return None
        return {
            "readOnly": share["options"] == "ro",
            "comment": share["comment"],
        }

    def plan_nfs_share(self, desired_state: Any) -> dict[str, Any]:
        desired = _validated_nfs_desired(desired_state)
        with self._control_lock:
            self._assert_nfs_clean()
            folder, existing = self._nfs_context(desired)
            current = self._nfs_controlled_values(existing)
            wanted = {"readOnly": desired["readOnly"], "comment": desired["comment"]}
            base_revision = _canonical_hash(
                {
                    "folder": {
                        "uuid": folder["uuid"].lower(),
                        "name": folder["name"],
                        "relativePath": folder["relativePath"],
                        "status": folder["status"],
                    },
                    "share": existing,
                }
            )
            plan_id = _canonical_hash(
                {
                    "schema": NFS_PLAN_SCHEMA,
                    "baseRevision": base_revision,
                    "desired": desired,
                }
            )
            changes = [
                {
                    "field": field,
                    "before": None if current is None else current[field],
                    "after": after,
                }
                for field, after in wanted.items()
                if current is None or current[field] != after
            ]
            operation = "create" if existing is None else ("update" if changes else "none")
            return {
                "schema": NFS_PLAN_SCHEMA,
                "planId": plan_id,
                "baseRevision": base_revision,
                "operation": operation,
                "requiresApproval": operation != "none",
                "shareUuid": existing["uuid"] if existing else _uuid_from_plan(plan_id),
                "sharedFolder": {
                    "uuid": folder["uuid"].lower(),
                    "name": folder["name"],
                    "status": folder["status"],
                },
                "desired": desired,
                "changes": changes,
                "safety": {
                    "clientScope": "privateCidrOnly",
                    "rootSquash": "required",
                    "syncWrites": "required",
                    "advancedOptions": "notManaged",
                    "delete": "notManaged",
                },
            }

    @staticmethod
    def _new_nfs_share(plan: dict[str, Any]) -> dict[str, Any]:
        desired = plan["desired"]
        return {
            "uuid": OMV_CONFIGOBJECT_NEW_UUID,
            "sharedfolderref": desired["sharedFolderRef"],
            "mntentref": OMV_CONFIGOBJECT_NEW_UUID,
            "client": desired["clientCidr"],
            "options": "ro" if desired["readOnly"] else "rw",
            "extraoptions": _NFS_MANAGED_EXTRA_OPTIONS,
            "comment": desired["comment"],
        }

    def _apply_nfs_config(self) -> None:
        result = self._runner(
            "Config",
            "applyChanges",
            {"modules": ["nfs"], "force": False},
        )
        if (
            not isinstance(result, list)
            or any(not isinstance(item, str) for item in result)
            or "nfs" not in result
        ):
            raise OmvBridgeError("OMV did not deploy the NFS configuration")

    def apply_nfs_share(self, desired_state: Any, plan_id: Any) -> dict[str, Any]:
        desired = _validated_nfs_desired(desired_state)
        if not isinstance(plan_id, str) or _PLAN_ID_PATTERN.fullmatch(plan_id) is None:
            raise OmvBridgeValidationError("NFS plan ID is invalid")
        with self._control_lock:
            plan = self.plan_nfs_share(desired)
            if plan["planId"] != plan_id:
                raise OmvBridgeConflict("NFS plan is stale; preview the change again")
            if plan["operation"] == "none":
                return {**plan, "applied": False, "verified": True}

            original = None
            if plan["operation"] == "update":
                original = _validated_nfs_share(
                    self._runner("NFS", "getShare", {"uuid": plan["shareUuid"]})
                )
                self._assert_managed_nfs_share(original, desired)
                desired_share = dict(original)
                desired_share.update(
                    {
                        "options": "ro" if desired["readOnly"] else "rw",
                        "comment": desired["comment"],
                    }
                )
            else:
                desired_share = self._new_nfs_share(plan)
            desired_share = _validated_nfs_share(desired_share)

            mutation_started = False
            created_share_uuid: str | None = None
            try:
                set_result = self._runner("NFS", "setShare", desired_share)
                mutation_started = True
                if original is None:
                    created_share = _validated_nfs_share(set_result)
                    self._assert_managed_nfs_share(created_share, desired)
                    created_share_uuid = created_share["uuid"]
                    if created_share_uuid == OMV_CONFIGOBJECT_NEW_UUID:
                        raise OmvBridgeError("OMV did not allocate an NFS share UUID")
                self._apply_nfs_config()
                observed_uuid = created_share_uuid or plan["shareUuid"]
                observed = _validated_nfs_share(
                    self._runner("NFS", "getShare", {"uuid": observed_uuid})
                )
                self._assert_managed_nfs_share(observed, desired)
                if observed["sharedfolderref"] != desired[
                    "sharedFolderRef"
                ] or self._nfs_controlled_values(observed) != {
                    "readOnly": desired["readOnly"],
                    "comment": desired["comment"],
                }:
                    raise OmvBridgeError("OMV did not persist the requested NFS state")
            except Exception as exc:
                if mutation_started:
                    try:
                        if original is None:
                            rollback_uuid = created_share_uuid
                            if rollback_uuid is None:
                                matches = [
                                    item["uuid"]
                                    for item in self._nfs_share_list()
                                    if item["sharedFolderRef"] == desired["sharedFolderRef"]
                                    and item["client"] == desired["clientCidr"]
                                ]
                                if len(matches) != 1:
                                    raise OmvBridgeError(
                                        "the newly created NFS share could not be identified"
                                    )
                                rollback_uuid = matches[0]
                            self._runner("NFS", "deleteShare", {"uuid": rollback_uuid})
                        else:
                            self._runner("NFS", "setShare", original)
                        self._apply_nfs_config()
                    except Exception as rollback_exc:
                        raise OmvBridgeError(
                            "OMV NFS apply failed and rollback also failed; inspect OMV immediately"
                        ) from rollback_exc
                if isinstance(exc, OmvBridgeError):
                    raise
                raise OmvBridgeError("OMV NFS apply failed") from exc
            return {
                **plan,
                "shareUuid": observed["uuid"],
                "applied": True,
                "verified": True,
            }


__all__ = ["OmvSharingControlMixin"]
