"""Validated request models for the Echo OMV API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from appliance.omv_client import (
    GROUP_DESIRED_SCHEMA,
    NFS_DESIRED_SCHEMA,
    QUOTA_DESIRED_SCHEMA,
    SHARE_PRIVILEGE_DESIRED_SCHEMA,
    SHARED_FOLDER_DESIRED_SCHEMA,
    SMB_DESIRED_SCHEMA,
    USER_DESIRED_SCHEMA,
    USER_PASSWORD_DESIRED_SCHEMA,
    validate_group_desired,
    validate_omv_uuid,
    validate_share_privilege_desired,
    validate_shared_folder_desired,
    validate_user_desired,
    validate_user_password_desired,
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


__all__ = [
    "GroupApplyRequest",
    "GroupDesiredState",
    "NfsApplyRequest",
    "NfsDesiredState",
    "QuotaApplyRequest",
    "QuotaDesiredState",
    "SharePrivilegeApplyRequest",
    "SharePrivilegeDesiredState",
    "SharedFolderApplyRequest",
    "SharedFolderDesiredState",
    "SmbApplyRequest",
    "SmbDesiredState",
    "UserApplyRequest",
    "UserDesiredState",
    "UserPasswordApplyRequest",
    "UserPasswordDesiredState",
]
