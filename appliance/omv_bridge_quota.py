"""Filesystem quota controls for the host OMV bridge."""

from __future__ import annotations

from typing import Any

from appliance.omv_bridge_contract import (
    _PLAN_ID_PATTERN,
    QUOTA_PLAN_SCHEMA,
    _canonical_hash,
    _quota_limit_bytes,
    _safe_text,
    _validated_quota_desired,
)
from appliance.omv_bridge_errors import (
    OmvBridgeConflict,
    OmvBridgeError,
    OmvBridgeValidationError,
)


class OmvQuotaControlMixin:
    """Quota control methods composed into the bridge service facade."""

    def _assert_quota_clean(self) -> None:
        dirty = self._runner("Config", "isDirty", {"modules": ["quota"]})
        if not isinstance(dirty, bool):
            raise OmvBridgeError("OMV quota dirty-state response must be boolean")
        if dirty:
            raise OmvBridgeConflict(
                "OMV has unapplied quota changes; apply or revert them in OMV first"
            )

    def _quota_context(
        self,
        desired: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self._assert_quota_clean()
        filesystem = next(
            (
                item
                for item in self.filesystems()
                if isinstance(item.get("uuid"), str)
                and item["uuid"].lower() == desired["filesystemUuid"]
            ),
            None,
        )
        if filesystem is None:
            raise OmvBridgeValidationError("quota filesystem is not mounted by OMV")
        if filesystem["readOnly"]:
            raise OmvBridgeConflict("quota filesystem is read-only")
        if not filesystem["supportsQuota"]:
            raise OmvBridgeConflict("quota is not supported by this filesystem")

        payload = self._runner("Quota", "get", {"uuid": desired["filesystemUuid"]})
        if not isinstance(payload, list) or len(payload) > 2048:
            raise OmvBridgeError("OMV quota response must be a bounded list")
        matching: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            subject_type = item.get("type")
            subject_name = item.get("name")
            if subject_type != desired["subjectType"] or subject_name != desired["subjectName"]:
                continue
            hard_limit_bytes = _quota_limit_bytes(
                item.get("bhardlimit"),
                item.get("bunit"),
            )
            matching.append(
                {
                    "type": subject_type,
                    "name": subject_name,
                    "hardLimitBytes": hard_limit_bytes,
                    "used": _safe_text(item.get("bused"), maximum=64) or "unknown",
                }
            )
        if len(matching) != 1:
            raise OmvBridgeValidationError("quota subject is not uniquely enumerated by OMV")
        return filesystem, matching[0]

    def plan_filesystem_quota(self, desired_state: Any) -> dict[str, Any]:
        desired = _validated_quota_desired(desired_state)
        with self._control_lock:
            filesystem, current = self._quota_context(desired)
            base_revision = _canonical_hash(
                {
                    "filesystem": {
                        "uuid": desired["filesystemUuid"],
                        "devicefile": filesystem["devicefile"],
                        "mountpoint": filesystem["mountpoint"],
                        "type": filesystem["type"],
                        "readOnly": filesystem["readOnly"],
                        "supportsQuota": filesystem["supportsQuota"],
                    },
                    "subject": current,
                }
            )
            plan_id = _canonical_hash(
                {
                    "schema": QUOTA_PLAN_SCHEMA,
                    "baseRevision": base_revision,
                    "desired": desired,
                }
            )
            changed = current["hardLimitBytes"] != desired["hardLimitBytes"]
            return {
                "schema": QUOTA_PLAN_SCHEMA,
                "planId": plan_id,
                "baseRevision": base_revision,
                "operation": "update" if changed else "none",
                "requiresApproval": changed,
                "filesystem": {
                    "uuid": desired["filesystemUuid"],
                    "label": filesystem["label"],
                    "type": filesystem["type"],
                    "readOnly": filesystem["readOnly"],
                    "supportsQuota": filesystem["supportsQuota"],
                },
                "subject": current,
                "desired": desired,
                "changes": (
                    [
                        {
                            "field": "hardLimitBytes",
                            "before": current["hardLimitBytes"],
                            "after": desired["hardLimitBytes"],
                        }
                    ]
                    if changed
                    else []
                ),
                "safety": {
                    "scope": "filesystemUserOrGroup",
                    "protocolCoverage": ["local", "SMB", "NFS"],
                    "sharedFolderQuota": "notSupportedByOmvQuotaRpc",
                    "minimumUnitBytes": 1024,
                },
            }

    def _set_filesystem_quota(self, desired: dict[str, Any], hard_limit_bytes: int) -> None:
        result = self._runner(
            "Quota",
            "setByTypeName",
            {
                "uuid": desired["filesystemUuid"],
                "type": desired["subjectType"],
                "name": desired["subjectName"],
                "bhardlimit": hard_limit_bytes // 1024,
                "bunit": "KiB",
            },
        )
        if not isinstance(result, dict):
            raise OmvBridgeError("OMV quota mutation response must be an object")

    def _apply_quota_config(self) -> None:
        result = self._runner(
            "Config",
            "applyChanges",
            {"modules": ["quota"], "force": False},
        )
        if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
            raise OmvBridgeError("OMV quota apply response must be a module list")

    def apply_filesystem_quota(self, desired_state: Any, plan_id: Any) -> dict[str, Any]:
        desired = _validated_quota_desired(desired_state)
        if not isinstance(plan_id, str) or _PLAN_ID_PATTERN.fullmatch(plan_id) is None:
            raise OmvBridgeValidationError("quota plan ID is invalid")
        with self._control_lock:
            plan = self.plan_filesystem_quota(desired)
            if plan["planId"] != plan_id:
                raise OmvBridgeConflict("quota plan is stale; preview the change again")
            if plan["operation"] == "none":
                return {**plan, "applied": False, "verified": True}

            original_limit = int(plan["subject"]["hardLimitBytes"])
            mutation_started = False
            try:
                mutation_started = True
                self._set_filesystem_quota(desired, desired["hardLimitBytes"])
                self._apply_quota_config()
                _filesystem, observed = self._quota_context(desired)
                if observed["hardLimitBytes"] != desired["hardLimitBytes"]:
                    raise OmvBridgeError("OMV did not persist the requested quota state")
            except Exception as exc:
                if mutation_started:
                    try:
                        self._set_filesystem_quota(desired, original_limit)
                        self._apply_quota_config()
                        _filesystem, restored = self._quota_context(desired)
                        if restored["hardLimitBytes"] != original_limit:
                            raise OmvBridgeError(
                                "OMV quota rollback did not restore the original state"
                            )
                    except Exception as rollback_exc:
                        raise OmvBridgeError(
                            "OMV quota apply failed and rollback also failed; inspect OMV immediately"
                        ) from rollback_exc
                if isinstance(exc, OmvBridgeError):
                    raise
                raise OmvBridgeError("OMV quota apply failed") from exc
            return {**plan, "applied": True, "verified": True}


__all__ = ["OmvQuotaControlMixin"]
