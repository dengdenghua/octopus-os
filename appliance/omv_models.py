"""Validated request models for the Echo storage-compatible API.

These Pydantic models are shared by the native routes and the optional OMV
router. Keep their validators on ``omv_protocol`` so importing the native
surface never loads the bridge client's HTTP transport.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from appliance.omv_protocol import (
    BTRFS_RAID1_DESIRED_SCHEMA,
    BTRFS_REPLACE_DESIRED_SCHEMA,
    BTRFS_SCRUB_DESIRED_SCHEMA,
    EXT4_CHECK_DESIRED_SCHEMA,
    EXT4_VOLUME_DESIRED_SCHEMA,
    GROUP_DESIRED_SCHEMA,
    MDRAID1_DESIRED_SCHEMA,
    MDRAID1_REPLACE_DESIRED_SCHEMA,
    MDRAID_CHECK_DESIRED_SCHEMA,
    NFS_DESIRED_SCHEMA,
    NFS_REMOVE_DESIRED_SCHEMA,
    QUOTA_DESIRED_SCHEMA,
    SHARE_PRIVILEGE_DESIRED_SCHEMA,
    SHARED_FOLDER_DELETE_DESIRED_SCHEMA,
    SHARED_FOLDER_DESIRED_SCHEMA,
    SHARED_FOLDER_DETACH_DESIRED_SCHEMA,
    SHARED_FOLDER_RENAME_DESIRED_SCHEMA,
    SMART_SELF_TEST_DESIRED_SCHEMA,
    SMB_DESIRED_SCHEMA,
    USER_DESIRED_SCHEMA,
    USER_PASSWORD_DESIRED_SCHEMA,
    ZFS_MIRROR_DESIRED_SCHEMA,
    ZFS_MIRROR_REPLACE_DESIRED_SCHEMA,
    ZFS_POOL_EXPORT_DESIRED_SCHEMA,
    ZFS_POOL_IMPORT_DESIRED_SCHEMA,
    ZFS_SCRUB_DESIRED_SCHEMA,
    validate_btrfs_raid1_desired,
    validate_btrfs_replace_desired,
    validate_btrfs_scrub_desired,
    validate_ext4_check_desired,
    validate_ext4_volume_desired,
    validate_group_desired,
    validate_mdraid1_desired,
    validate_mdraid1_replace_desired,
    validate_mdraid_check_desired,
    validate_nfs_remove_desired,
    validate_omv_uuid,
    validate_share_privilege_desired,
    validate_shared_folder_delete_desired,
    validate_shared_folder_desired,
    validate_shared_folder_detach_desired,
    validate_shared_folder_rename_desired,
    validate_smart_self_test_desired,
    validate_user_desired,
    validate_user_password_desired,
    validate_zfs_mirror_desired,
    validate_zfs_mirror_replace_desired,
    validate_zfs_pool_export_desired,
    validate_zfs_pool_import_desired,
    validate_zfs_scrub_desired,
)


class SmbDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.smb-share-desired.v1"] = Field(
        default=SMB_DESIRED_SCHEMA,
        alias="schema",
    )
    shared_folder_ref: str = Field(min_length=36, max_length=36, alias="sharedFolderRef")
    enabled: bool
    read_only: bool = Field(alias="readOnly")
    browseable: bool
    recycle_bin: bool = Field(alias="recycleBin")
    comment: str = Field(max_length=512)


class GroupDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.group-desired.v1"] = Field(
        default=GROUP_DESIRED_SCHEMA,
        alias="schema",
    )
    name: str = Field(min_length=1, max_length=32)
    comment: str = Field(max_length=65)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        try:
            return validate_group_desired(
                {"schema": GROUP_DESIRED_SCHEMA, "name": value, "comment": ""}
            )["name"]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class GroupApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: GroupDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class UserDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.user-desired.v1"] = Field(
        default=USER_DESIRED_SCHEMA,
        alias="schema",
    )
    name: str = Field(min_length=1, max_length=32)
    display_name: str = Field(min_length=1, max_length=65, alias="displayName")
    password: SecretStr = Field(min_length=12, max_length=128)
    groups: list[str] = Field(max_length=32)

    def to_wire(self) -> dict[str, Any]:
        return validate_user_desired(
            {
                "schema": self.schema_name,
                "name": self.name,
                "displayName": self.display_name,
                "password": self.password.get_secret_value(),
                "groups": self.groups,
            }
        )


class UserApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: UserDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class UserPasswordDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.user-password-desired.v1"] = Field(
        default=USER_PASSWORD_DESIRED_SCHEMA,
        alias="schema",
    )
    name: str = Field(min_length=1, max_length=32)
    password: SecretStr = Field(min_length=12, max_length=128)

    def to_wire(self) -> dict[str, str]:
        return validate_user_password_desired(
            {
                "schema": self.schema_name,
                "name": self.name,
                "password": self.password.get_secret_value(),
            }
        )


class UserPasswordApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: UserPasswordDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class SharedFolderDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.shared-folder-desired.v1"] = Field(
        default=SHARED_FOLDER_DESIRED_SCHEMA,
        alias="schema",
    )
    mount_point_ref: str = Field(min_length=36, max_length=36, alias="mountPointRef")
    name: str = Field(min_length=1, max_length=64)
    comment: str = Field(max_length=512)

    @field_validator("mount_point_ref")
    @classmethod
    def validate_mount_point_ref(cls, value: str) -> str:
        try:
            return validate_omv_uuid(value).lower()
        except ValueError as exc:
            raise ValueError("mountPointRef must be an OMV UUID") from exc

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        try:
            return validate_shared_folder_desired(
                {
                    "schema": SHARED_FOLDER_DESIRED_SCHEMA,
                    "mountPointRef": "11111111-2222-4333-8444-555555555555",
                    "name": value,
                    "comment": "",
                }
            )["name"]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class SharedFolderApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: SharedFolderDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class SharedFolderDetachDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.shared-folder-detach-desired.v1"] = Field(
        default=SHARED_FOLDER_DETACH_DESIRED_SCHEMA,
        alias="schema",
    )
    shared_folder_ref: str = Field(min_length=36, max_length=36, alias="sharedFolderRef")
    preserve_data: Literal[True] = Field(alias="preserveData")

    @field_validator("shared_folder_ref")
    @classmethod
    def validate_shared_folder_ref(cls, value: str) -> str:
        try:
            return validate_shared_folder_detach_desired(
                {
                    "schema": SHARED_FOLDER_DETACH_DESIRED_SCHEMA,
                    "sharedFolderRef": value,
                    "preserveData": True,
                }
            )["sharedFolderRef"]
        except ValueError as exc:
            raise ValueError("sharedFolderRef must be an OMV UUID") from exc


class SharedFolderDetachApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: SharedFolderDetachDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class SharedFolderDeleteDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.shared-folder-delete-desired.v1"] = Field(
        default=SHARED_FOLDER_DELETE_DESIRED_SCHEMA,
        alias="schema",
    )
    shared_folder_ref: str = Field(min_length=36, max_length=36, alias="sharedFolderRef")
    empty_only: Literal[True] = Field(alias="emptyOnly")

    @field_validator("shared_folder_ref")
    @classmethod
    def validate_shared_folder_ref(cls, value: str) -> str:
        try:
            return validate_shared_folder_delete_desired(
                {
                    "schema": SHARED_FOLDER_DELETE_DESIRED_SCHEMA,
                    "sharedFolderRef": value,
                    "emptyOnly": True,
                }
            )["sharedFolderRef"]
        except ValueError as exc:
            raise ValueError("sharedFolderRef must be an OMV UUID") from exc


class SharedFolderDeleteApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: SharedFolderDeleteDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class SharedFolderRenameDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.shared-folder-rename-desired.v1"] = Field(
        default=SHARED_FOLDER_RENAME_DESIRED_SCHEMA,
        alias="schema",
    )
    shared_folder_ref: str = Field(min_length=36, max_length=36, alias="sharedFolderRef")
    name: str = Field(min_length=1, max_length=64)

    @field_validator("shared_folder_ref", "name")
    @classmethod
    def validate_rename_field(cls, value: str, info: Any) -> str:
        payload = {
            "schema": SHARED_FOLDER_RENAME_DESIRED_SCHEMA,
            "sharedFolderRef": (
                value
                if info.field_name == "shared_folder_ref"
                else "11111111-2222-4333-8444-555555555555"
            ),
            "name": value if info.field_name == "name" else "share",
        }
        try:
            normalized = validate_shared_folder_rename_desired(payload)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return normalized["sharedFolderRef" if info.field_name == "shared_folder_ref" else "name"]


class SharedFolderRenameApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: SharedFolderRenameDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class SharePrivilegeDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.share-privilege-desired.v1"] = Field(
        default=SHARE_PRIVILEGE_DESIRED_SCHEMA,
        alias="schema",
    )
    shared_folder_ref: str = Field(min_length=36, max_length=36, alias="sharedFolderRef")
    principal_type: Literal["user", "group"] = Field(alias="principalType")
    principal_name: str = Field(min_length=1, max_length=255, alias="principalName")
    permission: Literal["inherit", "none", "read", "readWrite"]

    @field_validator("shared_folder_ref")
    @classmethod
    def validate_shared_folder_ref(cls, value: str) -> str:
        try:
            return validate_omv_uuid(value).lower()
        except ValueError as exc:
            raise ValueError("sharedFolderRef must be an OMV UUID") from exc

    @field_validator("principal_name")
    @classmethod
    def validate_principal_name(cls, value: str) -> str:
        try:
            return validate_share_privilege_desired(
                {
                    "schema": SHARE_PRIVILEGE_DESIRED_SCHEMA,
                    "sharedFolderRef": "11111111-2222-4333-8444-555555555555",
                    "principalType": "user",
                    "principalName": value,
                    "permission": "inherit",
                }
            )["principalName"]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class SharePrivilegeApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: SharePrivilegeDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class SmbApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: SmbDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class NfsDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.nfs-share-desired.v1"] = Field(
        default=NFS_DESIRED_SCHEMA,
        alias="schema",
    )
    shared_folder_ref: str = Field(min_length=36, max_length=36, alias="sharedFolderRef")
    client_cidr: str = Field(min_length=4, max_length=64, alias="clientCidr")
    read_only: bool = Field(alias="readOnly")
    comment: str = Field(max_length=512)


class NfsApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: NfsDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class NfsRemoveDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.nfs-share-remove-desired.v1"] = Field(
        default=NFS_REMOVE_DESIRED_SCHEMA,
        alias="schema",
    )
    shared_folder_ref: str = Field(min_length=36, max_length=36, alias="sharedFolderRef")
    client_cidr: str = Field(min_length=1, max_length=64, alias="clientCidr")

    @field_validator("shared_folder_ref")
    @classmethod
    def validate_shared_folder_ref(cls, value: str) -> str:
        try:
            return validate_nfs_remove_desired(
                {
                    "schema": NFS_REMOVE_DESIRED_SCHEMA,
                    "sharedFolderRef": value,
                    "clientCidr": "192.168.1.0/24",
                }
            )["sharedFolderRef"]
        except ValueError as exc:
            raise ValueError("sharedFolderRef must be an OMV UUID") from exc

    @field_validator("client_cidr")
    @classmethod
    def validate_client_cidr(cls, value: str) -> str:
        try:
            return validate_nfs_remove_desired(
                {
                    "schema": NFS_REMOVE_DESIRED_SCHEMA,
                    "sharedFolderRef": "11111111-2222-4333-8444-555555555555",
                    "clientCidr": value,
                }
            )["clientCidr"]
        except ValueError as exc:
            raise ValueError("clientCidr must be one private network in CIDR form") from exc


class NfsRemoveApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: NfsRemoveDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class QuotaDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.filesystem-quota-desired.v1"] = Field(
        default=QUOTA_DESIRED_SCHEMA,
        alias="schema",
    )
    filesystem_uuid: str = Field(min_length=36, max_length=36, alias="filesystemUuid")
    subject_type: Literal["user", "group"] = Field(alias="subjectType")
    subject_name: str = Field(min_length=1, max_length=255, alias="subjectName")
    hard_limit_bytes: int = Field(
        ge=0,
        le=2**63 - 1,
        strict=True,
        alias="hardLimitBytes",
    )

    @field_validator("filesystem_uuid")
    @classmethod
    def validate_filesystem_uuid(cls, value: str) -> str:
        try:
            return validate_omv_uuid(value).lower()
        except ValueError as exc:
            raise ValueError("filesystemUuid must be an OMV UUID") from exc

    @field_validator("subject_name")
    @classmethod
    def validate_subject_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(character < " " for character in normalized):
            raise ValueError("subjectName must be a valid OMV account name")
        return normalized

    @field_validator("hard_limit_bytes")
    @classmethod
    def validate_hard_limit_bytes(cls, value: int) -> int:
        if value != 0 and (value < 1024 or value % 1024 != 0):
            raise ValueError("hardLimitBytes must be zero or a positive multiple of 1024")
        return value


class QuotaApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: QuotaDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class ZfsMirrorDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.zfs-mirror-desired.v1"] = Field(
        default=ZFS_MIRROR_DESIRED_SCHEMA,
        alias="schema",
    )
    name: str = Field(min_length=1, max_length=32)
    devices: list[str] = Field(min_length=2, max_length=2)
    data_loss_confirmed: Literal[True] = Field(alias="dataLossConfirmed")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        try:
            return validate_zfs_mirror_desired(
                {
                    "schema": ZFS_MIRROR_DESIRED_SCHEMA,
                    "name": value,
                    "devices": ["/dev/sdb", "/dev/sdc"],
                    "dataLossConfirmed": True,
                }
            )["name"]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("devices")
    @classmethod
    def validate_devices(cls, value: list[str]) -> list[str]:
        try:
            return validate_zfs_mirror_desired(
                {
                    "schema": ZFS_MIRROR_DESIRED_SCHEMA,
                    "name": "tank",
                    "devices": value,
                    "dataLossConfirmed": True,
                }
            )["devices"]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class ZfsMirrorApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: ZfsMirrorDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class BtrfsRaid1DesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.btrfs-raid1-desired.v1"] = Field(
        default=BTRFS_RAID1_DESIRED_SCHEMA,
        alias="schema",
    )
    name: str = Field(min_length=1, max_length=16)
    devices: list[str] = Field(min_length=2, max_length=2)
    data_loss_confirmed: Literal[True] = Field(alias="dataLossConfirmed")

    @field_validator("name", "devices")
    @classmethod
    def validate_btrfs_field(cls, value: Any, info: Any) -> Any:
        payload = {
            "schema": BTRFS_RAID1_DESIRED_SCHEMA,
            "name": value if info.field_name == "name" else "family",
            "devices": value if info.field_name == "devices" else ["/dev/sdb", "/dev/sdc"],
            "dataLossConfirmed": True,
        }
        try:
            return validate_btrfs_raid1_desired(payload)[info.field_name]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class BtrfsRaid1ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: BtrfsRaid1DesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class BtrfsScrubDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.btrfs-scrub-desired.v1"] = Field(
        default=BTRFS_SCRUB_DESIRED_SCHEMA,
        alias="schema",
    )
    filesystem_uuid: str = Field(alias="filesystemUuid")
    operation: Literal["start"] = "start"

    @field_validator("filesystem_uuid")
    @classmethod
    def validate_filesystem_uuid(cls, value: str) -> str:
        try:
            return validate_btrfs_scrub_desired(
                {
                    "schema": BTRFS_SCRUB_DESIRED_SCHEMA,
                    "filesystemUuid": value,
                    "operation": "start",
                }
            )["filesystemUuid"]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class BtrfsScrubApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: BtrfsScrubDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class BtrfsScrubSchedulePolicyDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.btrfs-scrub-schedule-desired.v1"] = Field(
        default="echo.btrfs-scrub-schedule-desired.v1",
        alias="schema",
    )
    enabled: bool = Field(strict=True)


class BtrfsScrubSchedulePolicyApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: BtrfsScrubSchedulePolicyDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class BtrfsReplaceDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.btrfs-replace-desired.v1"] = Field(
        default=BTRFS_REPLACE_DESIRED_SCHEMA,
        alias="schema",
    )
    filesystem_uuid: str = Field(alias="filesystemUuid")
    missing_devid: int = Field(alias="missingDevid", strict=True, ge=1, le=2**32 - 1)
    replacement_device: str = Field(alias="replacementDevice")
    data_preserved: Literal[True] = Field(alias="dataPreserved")

    @field_validator("filesystem_uuid", "missing_devid", "replacement_device")
    @classmethod
    def validate_replacement_field(cls, value: Any, info: Any) -> Any:
        payload = {
            "schema": BTRFS_REPLACE_DESIRED_SCHEMA,
            "filesystemUuid": (
                value
                if info.field_name == "filesystem_uuid"
                else "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            ),
            "missingDevid": value if info.field_name == "missing_devid" else 2,
            "replacementDevice": (value if info.field_name == "replacement_device" else "/dev/sdc"),
            "dataPreserved": True,
        }
        try:
            normalized = validate_btrfs_replace_desired(payload)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        aliases = {
            "filesystem_uuid": "filesystemUuid",
            "missing_devid": "missingDevid",
            "replacement_device": "replacementDevice",
        }
        return normalized[aliases[info.field_name]]


class BtrfsReplaceApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: BtrfsReplaceDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class MdRaid1DesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.mdraid1-desired.v1"] = Field(
        default=MDRAID1_DESIRED_SCHEMA,
        alias="schema",
    )
    name: str = Field(min_length=1, max_length=27)
    devices: list[str] = Field(min_length=2, max_length=2)
    data_loss_confirmed: Literal[True] = Field(alias="dataLossConfirmed")

    @field_validator("name", "devices")
    @classmethod
    def validate_mdraid_field(cls, value: Any, info: Any) -> Any:
        payload = {
            "schema": MDRAID1_DESIRED_SCHEMA,
            "name": value if info.field_name == "name" else "data",
            "devices": value if info.field_name == "devices" else ["/dev/sdb", "/dev/sdc"],
            "dataLossConfirmed": True,
        }
        try:
            return validate_mdraid1_desired(payload)[info.field_name]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class MdRaid1ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: MdRaid1DesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class MdRaid1ReplaceDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.mdraid1-replace-desired.v1"] = Field(
        default=MDRAID1_REPLACE_DESIRED_SCHEMA,
        alias="schema",
    )
    name: str = Field(min_length=1, max_length=27)
    array_uuid: str = Field(alias="arrayUuid")
    replacement_device: str = Field(min_length=8, max_length=128, alias="replacementDevice")
    data_preserved: Literal[True] = Field(alias="dataPreserved")

    @field_validator("name", "array_uuid", "replacement_device")
    @classmethod
    def validate_mdraid_replace_field(cls, value: str, info: Any) -> str:
        payload = {
            "schema": MDRAID1_REPLACE_DESIRED_SCHEMA,
            "name": value if info.field_name == "name" else "data",
            "arrayUuid": (
                value if info.field_name == "array_uuid" else "11111111:22222222:33333333:44444444"
            ),
            "replacementDevice": (value if info.field_name == "replacement_device" else "/dev/sdb"),
            "dataPreserved": True,
        }
        try:
            normalized = validate_mdraid1_replace_desired(payload)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        field = {
            "name": "name",
            "array_uuid": "arrayUuid",
            "replacement_device": "replacementDevice",
        }[info.field_name]
        return normalized[field]


class MdRaid1ReplaceApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: MdRaid1ReplaceDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class MdRaidCheckDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.mdraid-check-desired.v1"] = Field(
        default=MDRAID_CHECK_DESIRED_SCHEMA,
        alias="schema",
    )
    name: str = Field(min_length=1, max_length=27)
    array_uuid: str = Field(alias="arrayUuid")
    operation: Literal["start"] = "start"

    @field_validator("name", "array_uuid")
    @classmethod
    def validate_mdraid_check_field(cls, value: str, info: Any) -> str:
        payload = {
            "schema": MDRAID_CHECK_DESIRED_SCHEMA,
            "name": value if info.field_name == "name" else "data",
            "arrayUuid": (
                value if info.field_name == "array_uuid" else "11111111:22222222:33333333:44444444"
            ),
            "operation": "start",
        }
        try:
            normalized = validate_mdraid_check_desired(payload)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return normalized["name" if info.field_name == "name" else "arrayUuid"]


class MdRaidCheckApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: MdRaidCheckDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class MdRaidCheckSchedulePolicyDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.mdraid-check-schedule-desired.v1"] = Field(
        default="echo.mdraid-check-schedule-desired.v1",
        alias="schema",
    )
    enabled: bool


class MdRaidCheckSchedulePolicyApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: MdRaidCheckSchedulePolicyDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class Ext4VolumeDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.ext4-volume-desired.v1"] = Field(
        default=EXT4_VOLUME_DESIRED_SCHEMA,
        alias="schema",
    )
    array_uuid: str = Field(alias="arrayUuid")
    name: str = Field(min_length=1, max_length=16)
    data_loss_confirmed: Literal[True] = Field(alias="dataLossConfirmed")

    @field_validator("array_uuid", "name")
    @classmethod
    def validate_ext4_volume_field(cls, value: Any, info: Any) -> Any:
        payload = {
            "schema": EXT4_VOLUME_DESIRED_SCHEMA,
            "arrayUuid": (
                value if info.field_name == "array_uuid" else "11111111:22222222:33333333:44444444"
            ),
            "name": value if info.field_name == "name" else "family",
            "dataLossConfirmed": True,
        }
        try:
            return validate_ext4_volume_desired(payload)[
                "arrayUuid" if info.field_name == "array_uuid" else "name"
            ]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class Ext4VolumeApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: Ext4VolumeDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class Ext4CheckDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.ext4-check-desired.v1"] = Field(
        default=EXT4_CHECK_DESIRED_SCHEMA,
        alias="schema",
    )
    filesystem_uuid: str = Field(min_length=36, max_length=36, alias="filesystemUuid")
    operation: Literal["check"] = "check"

    @field_validator("filesystem_uuid")
    @classmethod
    def validate_filesystem_uuid(cls, value: str) -> str:
        try:
            return validate_ext4_check_desired(
                {
                    "schema": EXT4_CHECK_DESIRED_SCHEMA,
                    "filesystemUuid": value,
                    "operation": "check",
                }
            )["filesystemUuid"]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class Ext4CheckApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: Ext4CheckDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class ZfsMirrorReplaceDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.zfs-mirror-replace-desired.v1"] = Field(
        default=ZFS_MIRROR_REPLACE_DESIRED_SCHEMA,
        alias="schema",
    )
    name: str = Field(min_length=1, max_length=32)
    pool_guid: str = Field(min_length=1, max_length=20, alias="poolGuid")
    old_vdev_guid: str = Field(min_length=1, max_length=20, alias="oldVdevGuid")
    replacement_device: str = Field(min_length=8, max_length=128, alias="replacementDevice")
    data_preserved: Literal[True] = Field(alias="dataPreserved")

    @field_validator("name", "pool_guid", "old_vdev_guid", "replacement_device")
    @classmethod
    def validate_identity_field(cls, value: str, info: Any) -> str:
        payload = {
            "schema": ZFS_MIRROR_REPLACE_DESIRED_SCHEMA,
            "name": value if info.field_name == "name" else "tank",
            "poolGuid": value if info.field_name == "pool_guid" else "1",
            "oldVdevGuid": value if info.field_name == "old_vdev_guid" else "2",
            "replacementDevice": (value if info.field_name == "replacement_device" else "/dev/sdb"),
            "dataPreserved": True,
        }
        try:
            normalized = validate_zfs_mirror_replace_desired(payload)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        field = {
            "name": "name",
            "pool_guid": "poolGuid",
            "old_vdev_guid": "oldVdevGuid",
            "replacement_device": "replacementDevice",
        }[info.field_name]
        return normalized[field]


class ZfsMirrorReplaceApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: ZfsMirrorReplaceDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class ZfsPoolExportDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.zfs-pool-export-desired.v1"] = Field(
        default=ZFS_POOL_EXPORT_DESIRED_SCHEMA,
        alias="schema",
    )
    name: str = Field(min_length=1, max_length=32)
    pool_guid: str = Field(min_length=1, max_length=20, alias="poolGuid")
    data_preserved: Literal[True] = Field(alias="dataPreserved")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        try:
            return validate_zfs_pool_export_desired(
                {
                    "schema": ZFS_POOL_EXPORT_DESIRED_SCHEMA,
                    "name": value,
                    "poolGuid": "1",
                    "dataPreserved": True,
                }
            )["name"]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("pool_guid")
    @classmethod
    def validate_pool_guid(cls, value: str) -> str:
        try:
            return validate_zfs_pool_export_desired(
                {
                    "schema": ZFS_POOL_EXPORT_DESIRED_SCHEMA,
                    "name": "tank",
                    "poolGuid": value,
                    "dataPreserved": True,
                }
            )["poolGuid"]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class ZfsPoolExportApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: ZfsPoolExportDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class ZfsPoolImportDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.zfs-pool-import-desired.v1"] = Field(
        default=ZFS_POOL_IMPORT_DESIRED_SCHEMA,
        alias="schema",
    )
    name: str = Field(min_length=1, max_length=32)
    pool_guid: str = Field(min_length=1, max_length=20, alias="poolGuid")
    mount_policy: Literal["echoDataRootOnly"] = Field(alias="mountPolicy")

    @field_validator("name", "pool_guid")
    @classmethod
    def validate_identity_field(cls, value: str, info: Any) -> str:
        payload = {
            "schema": ZFS_POOL_IMPORT_DESIRED_SCHEMA,
            "name": value if info.field_name == "name" else "tank",
            "poolGuid": value if info.field_name == "pool_guid" else "1",
            "mountPolicy": "echoDataRootOnly",
        }
        try:
            normalized = validate_zfs_pool_import_desired(payload)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return normalized["name" if info.field_name == "name" else "poolGuid"]


class ZfsPoolImportApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: ZfsPoolImportDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class ZfsScrubDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.zfs-scrub-desired.v1"] = Field(
        default=ZFS_SCRUB_DESIRED_SCHEMA,
        alias="schema",
    )
    name: str = Field(min_length=1, max_length=32)
    pool_guid: str = Field(min_length=1, max_length=20, alias="poolGuid")
    operation: Literal["start"] = "start"

    @field_validator("name", "pool_guid")
    @classmethod
    def validate_identity_field(cls, value: str, info: Any) -> str:
        payload = {
            "schema": ZFS_SCRUB_DESIRED_SCHEMA,
            "name": value if info.field_name == "name" else "tank",
            "poolGuid": value if info.field_name == "pool_guid" else "1",
            "operation": "start",
        }
        try:
            normalized = validate_zfs_scrub_desired(payload)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return normalized["name" if info.field_name == "name" else "poolGuid"]


class ZfsScrubApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: ZfsScrubDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class UpsShutdownPolicyDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.ups-shutdown-policy-desired.v1"] = Field(
        default="echo.ups-shutdown-policy-desired.v1",
        alias="schema",
    )
    enabled: bool
    required_consecutive_samples: int = Field(
        default=3,
        ge=2,
        le=12,
        alias="requiredConsecutiveSamples",
    )


class UpsShutdownPolicyApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: UpsShutdownPolicyDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class NutLocalUpsDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.nut-local-ups-desired.v1"] = Field(
        default="echo.nut-local-ups-desired.v1",
        alias="schema",
    )
    enabled: bool
    driver: Literal[
        "usbhid-ups",
        "blazer_usb",
        "nutdrv_qx",
        "bcmxcp_usb",
        "richcomm_usb",
        "tripplite_usb",
    ] = "usbhid-ups"


class NutLocalUpsApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: NutLocalUpsDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class SmartSelfTestDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.omv.smart-self-test-desired.v1"] = Field(
        default=SMART_SELF_TEST_DESIRED_SCHEMA,
        alias="schema",
    )
    devicefile: str = Field(min_length=5, max_length=256)
    test: Literal["short", "long"]

    @field_validator("devicefile")
    @classmethod
    def validate_device(cls, value: str) -> str:
        try:
            return validate_smart_self_test_desired(
                {
                    "schema": SMART_SELF_TEST_DESIRED_SCHEMA,
                    "devicefile": value,
                    "test": "short",
                }
            )["devicefile"]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class SmartSelfTestApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: SmartSelfTestDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class SmartSchedulePolicyDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.smart-self-test-schedule-desired.v1"] = Field(
        default="echo.smart-self-test-schedule-desired.v1",
        alias="schema",
    )
    enabled: bool


class SmartSchedulePolicyApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: SmartSchedulePolicyDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class DiskIdlePolicyDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.disk-idle-policy-desired.v1"] = Field(
        default="echo.disk-idle-policy-desired.v1",
        alias="schema",
    )
    idle_minutes: int = Field(strict=True, alias="idleMinutes")

    @field_validator("idle_minutes")
    @classmethod
    def validate_idle_minutes(cls, value: int) -> int:
        if value not in {0, 30, 60, 120, 240}:
            raise ValueError("idleMinutes must be one of 0, 30, 60, 120, or 240")
        return value


class DiskIdlePolicyApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: DiskIdlePolicyDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


__all__ = [
    "BtrfsRaid1ApplyRequest",
    "BtrfsRaid1DesiredState",
    "BtrfsScrubApplyRequest",
    "BtrfsScrubDesiredState",
    "BtrfsScrubSchedulePolicyApplyRequest",
    "BtrfsScrubSchedulePolicyDesiredState",
    "DiskIdlePolicyApplyRequest",
    "DiskIdlePolicyDesiredState",
    "Ext4CheckApplyRequest",
    "Ext4CheckDesiredState",
    "Ext4VolumeApplyRequest",
    "Ext4VolumeDesiredState",
    "GroupApplyRequest",
    "GroupDesiredState",
    "MdRaid1ApplyRequest",
    "MdRaid1DesiredState",
    "MdRaidCheckApplyRequest",
    "MdRaidCheckDesiredState",
    "MdRaidCheckSchedulePolicyApplyRequest",
    "MdRaidCheckSchedulePolicyDesiredState",
    "NfsApplyRequest",
    "NfsDesiredState",
    "NfsRemoveApplyRequest",
    "NfsRemoveDesiredState",
    "NutLocalUpsApplyRequest",
    "NutLocalUpsDesiredState",
    "QuotaApplyRequest",
    "QuotaDesiredState",
    "SharePrivilegeApplyRequest",
    "SharePrivilegeDesiredState",
    "SharedFolderApplyRequest",
    "SharedFolderDeleteApplyRequest",
    "SharedFolderDeleteDesiredState",
    "SharedFolderDetachApplyRequest",
    "SharedFolderDetachDesiredState",
    "SharedFolderDesiredState",
    "SmartSelfTestApplyRequest",
    "SmartSelfTestDesiredState",
    "SmartSchedulePolicyApplyRequest",
    "SmartSchedulePolicyDesiredState",
    "SmbApplyRequest",
    "SmbDesiredState",
    "UserApplyRequest",
    "UserDesiredState",
    "UserPasswordApplyRequest",
    "UserPasswordDesiredState",
    "UpsShutdownPolicyApplyRequest",
    "UpsShutdownPolicyDesiredState",
    "ZfsMirrorApplyRequest",
    "ZfsMirrorDesiredState",
    "ZfsMirrorReplaceApplyRequest",
    "ZfsMirrorReplaceDesiredState",
    "ZfsPoolExportApplyRequest",
    "ZfsPoolExportDesiredState",
    "ZfsPoolImportApplyRequest",
    "ZfsPoolImportDesiredState",
    "ZfsScrubApplyRequest",
    "ZfsScrubDesiredState",
]
