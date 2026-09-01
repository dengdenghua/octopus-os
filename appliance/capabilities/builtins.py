"""Capability adapters for the appliance APIs that already exist today."""

from __future__ import annotations

from typing import Any

from appliance.approval import APPROVAL_TTL_SECONDS
from appliance.capabilities.model import (
    ApprovalMode,
    CapabilityAudit,
    CapabilityAuthorization,
    CapabilityDefinition,
    CapabilityEffect,
    CapabilityOperation,
    CapabilityProvider,
    CapabilityRisk,
    CapabilityScope,
)
from appliance.capabilities.registry import CapabilityRegistry

_EMPTY_OBJECT: dict[str, Any] = {"type": "object", "additionalProperties": False}


def _provider(provider_id: str, method: str, path: str) -> CapabilityProvider:
    return CapabilityProvider(
        id=provider_id,
        transport="internal-http",
        operation=CapabilityOperation(method=method, path=path),
    )


def _none() -> CapabilityAuthorization:
    return CapabilityAuthorization()


def _step_up(action: str) -> CapabilityAuthorization:
    return CapabilityAuthorization(
        approval=ApprovalMode.PASSWORD_STEP_UP,
        approval_action=action,
        ttl_seconds=APPROVAL_TTL_SECONDS,
        single_use=True,
    )


def _definition(
    capability_id: str,
    title: str,
    description: str,
    *,
    provider: CapabilityProvider,
    request_schema: dict[str, Any],
    effect_type: str,
    risk: CapabilityRisk,
    reversible: bool,
    scope: CapabilityScope,
    authorization: CapabilityAuthorization,
    audit_action: str,
    audit_required: bool,
) -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id,
        version=1,
        title=title,
        description=description,
        provider=provider,
        request_schema=request_schema,
        effect=CapabilityEffect(type=effect_type, risk=risk, reversible=reversible),
        scope=scope,
        authorization=authorization,
        audit=CapabilityAudit(action=audit_action, required=audit_required),
    )


def _app_capabilities() -> tuple[CapabilityDefinition, ...]:
    container_scope = CapabilityScope(
        resource_kind="container-app",
        validation="container-id",
    )
    container_schema = {
        "type": "object",
        "required": ["containerId"],
        "properties": {"containerId": {"type": "string", "pattern": "^[0-9a-f]{12,64}$"}},
        "additionalProperties": False,
    }
    return (
        _definition(
            "apps.list",
            "列出应用",
            "读取当前 Docker 应用目录及运行状态。",
            provider=_provider("echo-os.apps", "GET", "/api/appliance/apps"),
            request_schema=_EMPTY_OBJECT,
            effect_type="read",
            risk=CapabilityRisk.LOW,
            reversible=True,
            scope=CapabilityScope(
                resource_kind="application-catalog",
                validation="fixed",
                target_required=False,
                fixed_target="application-catalog",
            ),
            authorization=_none(),
            audit_action="apps.list",
            audit_required=False,
        ),
        _definition(
            "apps.start",
            "启动应用",
            "启动一个经过目录枚举的 Docker 应用。",
            provider=_provider("echo-os.apps", "POST", "/api/appliance/apps/{containerId}/start"),
            request_schema=container_schema,
            effect_type="system-control",
            risk=CapabilityRisk.HIGH,
            reversible=True,
            scope=container_scope,
            authorization=_step_up("app.start"),
            audit_action="app.start",
            audit_required=True,
        ),
        _definition(
            "apps.stop",
            "停止应用",
            "停止一个经过目录枚举的 Docker 应用。",
            provider=_provider("echo-os.apps", "POST", "/api/appliance/apps/{containerId}/stop"),
            request_schema=container_schema,
            effect_type="system-control",
            risk=CapabilityRisk.HIGH,
            reversible=True,
            scope=container_scope,
            authorization=_step_up("app.stop"),
            audit_action="app.stop",
            audit_required=True,
        ),
    )


def _hub_capabilities() -> tuple[CapabilityDefinition, ...]:
    app_id_schema = {
        "type": "object",
        "required": ["appId"],
        "properties": {
            "appId": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
            }
        },
        "additionalProperties": False,
    }
    plan_apply_schema = {
        "type": "object",
        "required": ["appId", "planId"],
        "properties": {
            "appId": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
            },
            "planId": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
        "additionalProperties": False,
    }
    control_labels = {
        "start": ("启动", "按依赖顺序启动全部受管服务"),
        "stop": ("停止", "按反向依赖顺序停止全部受管服务并保留数据"),
        "restart": ("安全重启", "整组重启全部受管服务并等待健康检查"),
    }
    control_capabilities: list[CapabilityDefinition] = []
    for operation, (label, description) in control_labels.items():
        control_capabilities.extend(
            (
                _definition(
                    f"hub.{operation}.plan",
                    f"生成应用{label}计划",
                    f"根据受信服务图和当前运行状态生成无副作用的{label}计划。",
                    provider=_provider(
                        "echo-os.hub",
                        "POST",
                        f"/api/appliance/hub/plans/{operation}",
                    ),
                    request_schema=app_id_schema,
                    effect_type="read",
                    risk=CapabilityRisk.LOW,
                    reversible=True,
                    scope=CapabilityScope(resource_kind="hub-app", validation="hub-app-id"),
                    authorization=_none(),
                    audit_action=f"hub.{operation}.plan",
                    audit_required=False,
                ),
                _definition(
                    f"hub.{operation}.queue",
                    f"{label} Echo Hub 应用",
                    f"{description}；任务由 Echo 持久化执行，计划漂移时自动停止。",
                    provider=_provider(
                        "echo-os.hub",
                        "POST",
                        f"/api/appliance/hub/plans/{operation}/queue",
                    ),
                    request_schema=plan_apply_schema,
                    effect_type="system-control",
                    risk=CapabilityRisk.HIGH,
                    reversible=True,
                    scope=CapabilityScope(
                        resource_kind=f"hub-{operation}-plan",
                        validation="plan-id",
                    ),
                    authorization=_step_up(f"hub.app.{operation}"),
                    audit_action=f"hub.app.{operation}",
                    audit_required=True,
                ),
            )
        )
    return (
        _definition(
            "hub.catalog.list",
            "浏览 Echo Hub",
            "读取 Echo Hub 的精选应用、安装状态与当前设备兼容性。",
            provider=_provider("echo-os.hub", "GET", "/api/appliance/hub/catalog"),
            request_schema={
                "type": "object",
                "properties": {
                    "search": {"type": "string", "maxLength": 120},
                    "category": {"type": "string", "maxLength": 32},
                },
                "additionalProperties": False,
            },
            effect_type="read",
            risk=CapabilityRisk.LOW,
            reversible=True,
            scope=CapabilityScope(
                resource_kind="application-market",
                validation="fixed",
                target_required=False,
                fixed_target="echo-hub",
            ),
            authorization=_none(),
            audit_action="hub.catalog.list",
            audit_required=False,
        ),
        _definition(
            "hub.install.plan",
            "生成应用安装计划",
            "根据受信目录、设备架构和当前容器状态生成无副作用安装计划。",
            provider=_provider("echo-os.hub", "POST", "/api/appliance/hub/plans/install"),
            request_schema={
                "type": "object",
                "required": ["appId"],
                "properties": {
                    "appId": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
                    }
                },
                "additionalProperties": False,
            },
            effect_type="read",
            risk=CapabilityRisk.LOW,
            reversible=True,
            scope=CapabilityScope(resource_kind="hub-app", validation="hub-app-id"),
            authorization=_none(),
            audit_action="hub.install.plan",
            audit_required=False,
        ),
        _definition(
            "hub.install.apply",
            "安装 Echo Hub 应用",
            "执行一份刚刚复核且未发生漂移的受信应用安装计划。",
            provider=_provider("echo-os.hub", "POST", "/api/appliance/hub/plans/install/apply"),
            request_schema={
                "type": "object",
                "required": ["appId", "planId"],
                "properties": {
                    "appId": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
                    },
                    "planId": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                },
                "additionalProperties": False,
            },
            effect_type="system-control",
            risk=CapabilityRisk.HIGH,
            reversible=True,
            scope=CapabilityScope(resource_kind="hub-install-plan", validation="plan-id"),
            authorization=_step_up("hub.app.install"),
            audit_action="hub.app.install",
            audit_required=True,
        ),
        _definition(
            "hub.update.plan",
            "生成应用更新计划",
            "比较当前受管容器与受信目录包，生成保留数据和运行状态的更新计划。",
            provider=_provider("echo-os.hub", "POST", "/api/appliance/hub/plans/update"),
            request_schema={
                "type": "object",
                "required": ["appId"],
                "properties": {
                    "appId": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
                    }
                },
                "additionalProperties": False,
            },
            effect_type="read",
            risk=CapabilityRisk.LOW,
            reversible=True,
            scope=CapabilityScope(resource_kind="hub-app", validation="hub-app-id"),
            authorization=_none(),
            audit_action="hub.update.plan",
            audit_required=False,
        ),
        _definition(
            "hub.update.apply",
            "更新 Echo Hub 应用",
            "以候选容器替换旧容器；失败时恢复旧容器，保留应用数据卷与 NAS 文件。",
            provider=_provider("echo-os.hub", "POST", "/api/appliance/hub/plans/update/apply"),
            request_schema={
                "type": "object",
                "required": ["appId", "planId"],
                "properties": {
                    "appId": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
                    },
                    "planId": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                },
                "additionalProperties": False,
            },
            effect_type="system-control",
            risk=CapabilityRisk.HIGH,
            reversible=True,
            scope=CapabilityScope(resource_kind="hub-update-plan", validation="plan-id"),
            authorization=_step_up("hub.app.update"),
            audit_action="hub.app.update",
            audit_required=True,
        ),
        _definition(
            "hub.uninstall.plan",
            "生成应用卸载计划",
            "生成只移除受管容器、保留应用数据卷和 NAS 数据的无副作用卸载计划。",
            provider=_provider("echo-os.hub", "POST", "/api/appliance/hub/plans/uninstall"),
            request_schema={
                "type": "object",
                "required": ["appId"],
                "properties": {
                    "appId": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
                    }
                },
                "additionalProperties": False,
            },
            effect_type="read",
            risk=CapabilityRisk.LOW,
            reversible=True,
            scope=CapabilityScope(resource_kind="hub-app", validation="hub-app-id"),
            authorization=_none(),
            audit_action="hub.uninstall.plan",
            audit_required=False,
        ),
        _definition(
            "hub.uninstall.apply",
            "卸载 Echo Hub 应用",
            "移除一份已复核计划中的受管容器，保留应用数据卷与 NAS 文件。",
            provider=_provider("echo-os.hub", "POST", "/api/appliance/hub/plans/uninstall/apply"),
            request_schema={
                "type": "object",
                "required": ["appId", "planId"],
                "properties": {
                    "appId": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
                    },
                    "planId": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                },
                "additionalProperties": False,
            },
            effect_type="system-control",
            risk=CapabilityRisk.HIGH,
            reversible=True,
            scope=CapabilityScope(resource_kind="hub-uninstall-plan", validation="plan-id"),
            authorization=_step_up("hub.app.uninstall"),
            audit_action="hub.app.uninstall",
            audit_required=True,
        ),
        *control_capabilities,
    )


def _file_capabilities() -> tuple[CapabilityDefinition, ...]:
    relative_path_scope = CapabilityScope(
        resource_kind="nas-path",
        validation="relative-path",
    )
    return (
        _definition(
            "files.list",
            "列出文件",
            "读取 NAS 根目录内一个相对路径的直接子项。",
            provider=_provider("echo-os.files", "GET", "/api/appliance/files/list"),
            request_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "default": ""}},
                "additionalProperties": False,
            },
            effect_type="read",
            risk=CapabilityRisk.LOW,
            reversible=True,
            scope=CapabilityScope(
                resource_kind="nas-path",
                validation="relative-path",
                target_required=False,
            ),
            authorization=_none(),
            audit_action="files.list",
            audit_required=False,
        ),
        _definition(
            "files.upload",
            "上传文件",
            "通过原子临时文件提交一个 NAS 文件。",
            provider=_provider("echo-os.files", "POST", "/api/appliance/files/upload"),
            request_schema={
                "type": "object",
                "required": ["path", "file"],
                "properties": {
                    "path": {"type": "string"},
                    "file": {"type": "string", "contentEncoding": "binary"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
            effect_type="write",
            risk=CapabilityRisk.MEDIUM,
            reversible=True,
            scope=relative_path_scope,
            authorization=_none(),
            audit_action="files.upload",
            audit_required=True,
        ),
        _definition(
            "files.trash.move",
            "移入回收站",
            "将 NAS 文件或目录移动到可恢复回收站。",
            provider=_provider("echo-os.files", "POST", "/api/appliance/files/trash"),
            request_schema={
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string", "minLength": 1}},
                "additionalProperties": False,
            },
            effect_type="recoverable-delete",
            risk=CapabilityRisk.MEDIUM,
            reversible=True,
            scope=relative_path_scope,
            authorization=_none(),
            audit_action="files.trash",
            audit_required=True,
        ),
        _definition(
            "files.trash.restore",
            "恢复回收站文件",
            "按回收站记录标识恢复一个文件或目录。",
            provider=_provider("echo-os.files", "POST", "/api/appliance/files/trash/restore"),
            request_schema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}},
                "additionalProperties": False,
            },
            effect_type="write",
            risk=CapabilityRisk.MEDIUM,
            reversible=True,
            scope=CapabilityScope(
                resource_kind="trash-entry",
                validation="trash-entry-id",
            ),
            authorization=_none(),
            audit_action="files.trash.restore",
            audit_required=True,
        ),
        _definition(
            "files.trash.empty",
            "清空回收站",
            "永久删除回收站全部内容。",
            provider=_provider("echo-os.files", "POST", "/api/appliance/files/trash/empty"),
            request_schema=_EMPTY_OBJECT,
            effect_type="destructive",
            risk=CapabilityRisk.HIGH,
            reversible=False,
            scope=CapabilityScope(
                resource_kind="nas-trash",
                validation="fixed",
                fixed_target="recycle-bin",
            ),
            authorization=_step_up("files.trash.empty"),
            audit_action="files.trash.empty",
            audit_required=True,
        ),
    )


def _storage_capabilities() -> tuple[CapabilityDefinition, ...]:
    return (
        _definition(
            "storage.health.read",
            "读取存储健康",
            "读取 OMV 桥维护的脱敏存储健康快照。",
            provider=_provider("echo-os.storage.omv", "GET", "/api/appliance/omv/health"),
            request_schema=_EMPTY_OBJECT,
            effect_type="read",
            risk=CapabilityRisk.LOW,
            reversible=True,
            scope=CapabilityScope(
                resource_kind="storage-system",
                validation="fixed",
                target_required=False,
                fixed_target="storage-system",
            ),
            authorization=_none(),
            audit_action="storage.health.read",
            audit_required=False,
        ),
    )


def _photo_capabilities() -> tuple[CapabilityDefinition, ...]:
    fixed_library_scope = CapabilityScope(
        resource_kind="photo-library",
        validation="fixed",
        target_required=False,
        fixed_target="photo-library",
    )
    return (
        _definition(
            "photos.library.list",
            "浏览照片",
            "只读浏览 NAS 中经过路径安全检查的图片和缩略图元数据。",
            provider=_provider("echo-os.photos", "GET", "/api/appliance/photos/library"),
            request_schema={
                "type": "object",
                "properties": {
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    "search": {"type": "string", "maxLength": 120},
                },
                "additionalProperties": False,
            },
            effect_type="read",
            risk=CapabilityRisk.LOW,
            reversible=True,
            scope=fixed_library_scope,
            authorization=_none(),
            audit_action="photos.library.list",
            audit_required=False,
        ),
        _definition(
            "photos.search",
            "搜索照片",
            "使用本地 Agent 语义索引搜索照片；索引不可用时退化为文件名搜索。",
            provider=_provider("echo-os.photos", "POST", "/api/appliance/photos/search"),
            request_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 120},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "additionalProperties": False,
            },
            effect_type="read",
            risk=CapabilityRisk.LOW,
            reversible=True,
            scope=fixed_library_scope,
            authorization=_none(),
            audit_action="photos.search",
            audit_required=False,
        ),
        _definition(
            "photos.index.plan",
            "预览照片智能索引",
            "检查图片数量、Agent 能力和当前索引状态，生成无副作用计划。",
            provider=_provider("echo-os.photos", "POST", "/api/appliance/photos/plans/index"),
            request_schema={
                "type": "object",
                "properties": {"includeFaces": {"type": "boolean", "default": False}},
                "additionalProperties": False,
            },
            effect_type="read",
            risk=CapabilityRisk.LOW,
            reversible=True,
            scope=CapabilityScope(
                resource_kind="photo-index",
                validation="fixed",
                target_required=False,
                fixed_target="photo-index",
            ),
            authorization=_none(),
            audit_action="photos.index.plan",
            audit_required=False,
        ),
        _definition(
            "photos.index.apply",
            "建立照片智能索引",
            "在设备本地读取照片并写入可重建的 Agent 语义索引。",
            provider=_provider(
                "echo-os.photos",
                "POST",
                "/api/appliance/photos/plans/index/apply",
            ),
            request_schema={
                "type": "object",
                "required": ["planId"],
                "properties": {
                    "planId": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "includeFaces": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
            effect_type="local-index-write",
            risk=CapabilityRisk.HIGH,
            reversible=True,
            scope=CapabilityScope(resource_kind="photo-index-plan", validation="plan-id"),
            authorization=_step_up("photos.index.build"),
            audit_action="photos.index.build",
            audit_required=True,
        ),
    )


def build_builtin_registry(
    *,
    include_files: bool = True,
    include_photos: bool | None = None,
) -> CapabilityRegistry:
    if include_photos is None:
        include_photos = include_files
    capabilities = [*_app_capabilities(), *_hub_capabilities(), *_storage_capabilities()]
    if include_files:
        capabilities.extend(_file_capabilities())
    if include_photos:
        capabilities.extend(_photo_capabilities())
    return CapabilityRegistry(capabilities)


__all__ = ["build_builtin_registry"]
