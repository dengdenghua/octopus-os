"""Echo OS appliance —— 以 agent 官方扩展 API 挂载,不再 fork app.py。

经 ``ECHO_APP_EXTENSIONS=appliance.extension`` 在 agent 组装末尾被调用,
把 NAS 桌面启动器(单用户认证 + 应用注册器 + 文件管理器)挂到 FastAPI app 上。
这是「消费而非 fork」的落地:appliance 逻辑住在 os 自己的包里,与母体 app.py
零冲突(见 docs/OS_DIFFERENTIATION.md P0)。

语义与原 fork 块一致:仅 ``ECHO_APPLIANCE=1`` 时挂载,否则 no-op。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

_alog = logging.getLogger("echo.appliance")


def _route_paths(routes: Any, prefix: str = "") -> list[str]:
    """Return effective paths for both flattened and nested FastAPI routers.

    FastAPI 0.141/Starlette 1.6 retain included routers as lightweight nested
    wrappers instead of copying every route into ``app.routes``.  Older Agent
    releases still expose a flat list, so the appliance must understand both
    layouts while deciding whether Agent already mounted local auth.
    """
    paths: list[str] = []
    for route in routes or ():
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.append(f"{prefix}{path}")

        original_router = getattr(route, "original_router", None)
        nested_routes = getattr(original_router, "routes", None)
        if nested_routes is None:
            continue
        context = getattr(route, "include_context", None)
        included_prefix = getattr(context, "prefix", "")
        if not isinstance(included_prefix, str):
            included_prefix = ""
        paths.extend(_route_paths(nested_routes, f"{prefix}{included_prefix}"))
    return paths


def register_app(app: Any, context: Any) -> None:
    """agent 扩展入口:挂载 appliance 路由。context.identity_store 来自 create_app。"""
    if os.environ.get("ECHO_APPLIANCE") != "1":
        return

    from appliance.agent_api.contract import require_agent_api_contract

    app.state.echo_agent_api_contract = require_agent_api_contract()

    from appliance.account_security import (
        ApplianceAccountSecurity,
        ApplianceLocalAuthMiddleware,
        ApplianceSessionRevocationMiddleware,
        create_account_security_router,
    )
    from appliance.agent_api.auth import create_local_auth_router
    from appliance.agent_api.capabilities import AgentCapabilityBridge
    from appliance.agent_api.devices import create_device_coordinator
    from appliance.agent_assets import (
        AgentAssetCatalogService,
        create_agent_assets_router,
    )
    from appliance.agent_capabilities import create_agent_capabilities_router
    from appliance.app_registry.docker_client import DockerClient
    from appliance.app_registry.router import create_appliance_router
    from appliance.approval import HighRiskApprovalService, create_approval_router
    from appliance.audit import ApplianceAudit, create_audit_router
    from appliance.auth import ADMIN_USERNAME, load_or_bootstrap_auth
    from appliance.capabilities import build_builtin_registry, create_capabilities_router
    from appliance.desktop_root import ApplianceDesktopRootMiddleware
    from appliance.hub import HubCatalog, HubService, create_hub_router
    from appliance.hub.operations import HubOperationService, HubOperationStore
    from appliance.security import ApplianceAuthenticator
    from appliance.state_lock import StateDirectoryLock
    from appliance.state_schema import ensure_state_schema
    from appliance.task_projection import create_task_projection_router
    from appliance.web_security import ApplianceWebSecurityMiddleware

    data_dir = os.environ.get("ECHO_DATA_DIR") or os.environ.get("ECHO_DATA") or "."
    nas_root = os.environ.get("ECHO_NAS_ROOT") or os.path.join(
        os.environ.get("ECHO_DATA_DIR", "/data"), "nas"
    )
    if not getattr(app.state, "echo_appliance_state_lock", None):
        app.state.echo_appliance_state_lock = StateDirectoryLock.acquire(
            data_dir,
            exclusive=True,
            create=True,
            purpose="appliance runtime",
        )
    app.state.echo_appliance_state_schema = ensure_state_schema(data_dir)

    # Agent mounts ECHO_WEBUI_DIST under /ui/ but intentionally keeps its
    # historical dashboard at /.  The NAS entry URL is /#/desktop, whose hash
    # never reaches the server, so Echo OS must own exactly the two index paths.
    # Add this before the browser-security middleware so CSP still wraps it.
    if not getattr(app.state, "echo_appliance_desktop_root", False):
        app.add_middleware(ApplianceDesktopRootMiddleware)
        app.state.echo_appliance_desktop_root = True

    auth_cfg, generated_pw = load_or_bootstrap_auth()
    if generated_pw:
        _alog.warning(
            "appliance admin password generated (set ECHO_ADMIN_PASSWORD to "
            "choose your own): username=%s password=%s",
            ADMIN_USERNAME,
            generated_pw,
        )
    login_auth_cfg = auth_cfg
    if os.environ.get("ECHO_APPLIANCE_DEV_PASSWORDLESS") == "1":
        # The Vite all-in-one launcher is loopback-only and advertises a
        # passwordless local login. Keep the persistent device password for
        # high-risk approvals, while allowing only already-provisioned local
        # accounts (normally ``admin``) through the development login gate.
        login_auth_cfg = auth_cfg.model_copy(
            update={
                "allow_any_username": False,
                "allowed_usernames": list(auth_cfg.users),
                "users": {},
            }
        )
        _alog.warning(
            "development passwordless appliance login enabled for provisioned local accounts"
        )
    # 容器入口已把同一份凭据注入 Agent config 时，Agent 会先挂本地认证路由；开发环境
    # 直接调用扩展时仍由此处补挂。始终只保留一套路由和一套设备密码。
    has_local_auth = "/api/auth/local/login" in _route_paths(getattr(app, "routes", ()))
    if not has_local_auth:
        app.include_router(
            create_local_auth_router(config=auth_cfg, identity_store=context.identity_store)
        )

    # 高风险控制面必须同时具备持久审计和人类密码复核。bootstrap 形态必定有
    # jwt_secret；若未来 Agent 配置意外改变，宁可拒绝启动控制面也不降级成
    # 只有确认框的假审批。
    if not auth_cfg.jwt_secret:
        raise RuntimeError("appliance auth requires a JWT secret")
    authenticator = ApplianceAuthenticator(auth_cfg.jwt_secret)
    audit = ApplianceAudit.from_data_dir(data_dir, jwt_secret=auth_cfg.jwt_secret)
    approval = HighRiskApprovalService(
        password_hash=auth_cfg.users[ADMIN_USERNAME],
        jwt_secret=auth_cfg.jwt_secret,
        audit=audit,
    )
    account_security = ApplianceAccountSecurity(
        auth_config=auth_cfg,
        approval=approval,
        audit=audit,
    )
    app.state.echo_appliance_auth_config = auth_cfg
    app.state.echo_appliance_authenticator = authenticator
    app.state.echo_appliance_audit = audit
    app.state.echo_appliance_approval = approval
    app.state.echo_appliance_account_security = account_security
    app.include_router(create_approval_router(approval, authenticator=authenticator))
    app.include_router(
        create_audit_router(
            audit,
            authenticator=authenticator,
            approval=approval,
        )
    )
    app.include_router(
        create_account_security_router(
            account_security,
            approval=approval,
            authenticator=authenticator,
        )
    )

    # System-owned phone/device entry.  Development or NAS configurations may
    # already have Agent's Tentacle coordinator, in which case Echo projects it
    # without taking over the listener.  The native image disables that
    # convenience listener; Echo then creates it only after an authenticated,
    # password-approved opt-in and uses per-device, revocable credentials.
    from appliance.device_link import (
        DEFAULT_TENTACLE_PORT,
        DeviceLinkService,
        create_device_link_router,
    )
    from appliance.remote_access import RemoteAccessService

    existing_tentacle = getattr(app.state, "tentacle_coordinator", None)
    remote_access = RemoteAccessService.from_environment()
    device_link_port = int(os.environ.get("ECHO_DEVICE_LINK_PORT", str(DEFAULT_TENTACLE_PORT)))

    def _device_coordinator() -> Any:
        return create_device_coordinator(context, port=device_link_port)

    device_link = DeviceLinkService(
        data_dir=data_dir,
        jwt_secret=auth_cfg.jwt_secret,
        coordinator=existing_tentacle,
        coordinator_factory=None if existing_tentacle is not None else _device_coordinator,
        ws_port=device_link_port,
        device_sync_port=(
            int(os.environ["ECHO_DEVICE_SYNC_PORT"])
            if os.environ.get("ECHO_DEVICE_SYNC_PORT")
            else None
        ),
        public_host=os.environ.get("ECHO_DEVICE_LINK_HOST", ""),
        allow_host_resolver_fallback=(
            os.environ.get("ECHO_DEVICE_LINK_AUTO_HOST_FALLBACK", "1") == "1"
        ),
        remote_access=remote_access,
    )
    app.state.echo_remote_access = remote_access
    app.state.echo_device_link = device_link
    app.include_router(
        create_device_link_router(
            device_link,
            authenticator=authenticator,
            approval=approval,
            audit=audit,
        )
    )
    app.router.add_event_handler("startup", remote_access.start)
    app.router.add_event_handler("shutdown", remote_access.stop)
    app.router.add_event_handler("startup", device_link.startup)
    app.router.add_event_handler("shutdown", device_link.shutdown)
    app.include_router(
        create_appliance_router(
            authenticator=authenticator,
            approval=approval,
            audit=audit,
        )
    )
    # Echo and Agent may have separate JWT signing domains.  Reuse Agent's
    # catalog abstraction behind the authenticated Echo boundary instead of
    # forwarding browser credentials or opening Agent's private state files.
    agent_assets = AgentAssetCatalogService()
    app.state.echo_agent_assets = agent_assets
    app.include_router(create_agent_assets_router(agent_assets, authenticator=authenticator))
    agent_capabilities = AgentCapabilityBridge()
    app.state.echo_agent_capabilities = agent_capabilities
    app.include_router(
        create_agent_capabilities_router(
            agent_capabilities,
            authenticator=authenticator,
            approval=approval,
            audit=audit,
        )
    )
    # Echo Hub owns the device-facing catalog projection.  The first slice is
    # intentionally read-only: it can browse curated apps and build a
    # deterministic install plan, but entries without a trusted immutable
    # package stay blocked instead of falling back to arbitrary Compose input.
    hub_docker = DockerClient()
    hub_service = HubService(HubCatalog.load(), docker=hub_docker, nas_root=nas_root)
    hub_operations = HubOperationService(
        HubOperationStore(data_dir, encryption_secret=auth_cfg.jwt_secret),
        executor=hub_docker,
        audit=audit,
    )
    app.state.echo_hub_service = hub_service
    app.state.echo_hub_operations = hub_operations
    app.router.add_event_handler("shutdown", hub_operations.shutdown)
    app.include_router(
        create_hub_router(
            hub_service,
            installer=hub_docker,
            authenticator=authenticator,
            approval=approval,
            audit=audit,
            operations=hub_operations,
        )
    )

    # OpenMediaVault stays the storage authority. Echo reads storage health,
    # topology and redacted sharing/account inventory through a fixed host
    # bridge. Its constrained writes cover create-only simple shared folders,
    # private SMB/NFS rules and filesystem owner quotas; every mutation is
    # previewed, approval-bound, audited and verified without arbitrary RPC.
    from appliance.omv_client import OmvClient
    from appliance.omv_health import HEALTH_STATE_FILENAME, OmvHealthMonitor
    from appliance.omv_router import create_omv_router

    omv_client = OmvClient()
    from appliance.accounts import (
        ApplianceAccountDirectory,
        create_account_directory_router,
    )

    account_directory = ApplianceAccountDirectory(
        auth_config=auth_cfg,
        omv=omv_client,
        jwt_secret=auth_cfg.jwt_secret,
        account_security=account_security,
    )
    app.state.echo_appliance_account_directory = account_directory
    app.include_router(
        create_account_directory_router(
            account_directory,
            authenticator=authenticator,
            approval=approval,
            audit=audit,
        )
    )
    omv_health = OmvHealthMonitor.from_environment(
        omv_client,
        Path(data_dir) / HEALTH_STATE_FILENAME,
    )
    app.state.echo_appliance_omv_health = omv_health
    app.router.add_event_handler("startup", omv_health.start)
    app.router.add_event_handler("shutdown", omv_health.stop)
    app.include_router(
        create_omv_router(
            omv_client,
            monitor=omv_health,
            authenticator=authenticator,
            approval=approval,
            audit=audit,
        )
    )

    # Publish the verified Agent runtime/config identity. Echo OS owns the only
    # browser workbench and deliberately does not mount a second Agent WebUI.
    from appliance.agent_ui import mount_agent_ui

    mount_agent_ui(app)

    # NAS 文件管理器(回收站语义)。root 默认对齐 compose 的存储挂载点;
    # 路径不可用时跳过挂载,不影响其余功能。
    files_mounted = False
    photos_mounted = False
    try:
        from appliance.data_access import OmvDataAccessPolicy
        from appliance.files import FileManager, create_files_router

        file_manager = FileManager(nas_root)
        family_data_access = OmvDataAccessPolicy(
            accounts=account_directory,
            omv=omv_client,
            root=file_manager.root,
            mounted_share_uuid=os.environ.get(
                "ECHO_NAS_OMV_SHARED_FOLDER_REF",
                "",
            ),
            cache_seconds=2.0,
        )
        app.state.echo_family_data_access = family_data_access
        app.include_router(
            create_files_router(
                file_manager,
                authenticator=authenticator,
                approval=approval,
                audit=audit,
                data_access=family_data_access,
            )
        )
        files_mounted = True
    except OSError as fs_exc:
        _alog.warning("NAS file manager not mounted (%s): %s", nas_root, fs_exc)

    if files_mounted:
        photo_service = None
        try:
            from appliance.photos import PhotoLibraryService, create_photos_router

            photo_service = PhotoLibraryService(file_manager.root, data_dir)
            app.state.echo_photo_service = photo_service
            app.include_router(
                create_photos_router(
                    photo_service,
                    authenticator=authenticator,
                    approval=approval,
                    audit=audit,
                    data_access=family_data_access,
                )
            )
            photos_mounted = True
        except OSError as photo_exc:
            _alog.warning("NAS photos not mounted (%s): %s", nas_root, photo_exc)

        try:
            from appliance.sync import DeviceSyncService, create_device_sync_router

            sync_service = DeviceSyncService(
                data_dir=data_dir,
                files=file_manager,
                device_link=device_link,
                photos=photo_service,
            )
            app.state.echo_device_sync = sync_service
            app.include_router(
                create_device_sync_router(
                    sync_service,
                    authenticator=authenticator,
                    approval=approval,
                    audit=audit,
                )
            )
            remote_access.set_sync_available(True)
        except OSError as sync_exc:
            _alog.warning("Device sync not mounted (%s): %s", nas_root, sync_exc)

    # One contract spans the current OMV provider and future native Echo
    # storage provider. Only advertise file capabilities when their concrete
    # router mounted successfully; application and storage-health providers
    # already expose explicit unavailable states at execution time.
    capability_registry = build_builtin_registry(
        include_files=files_mounted,
        include_photos=photos_mounted,
    )
    app.state.echo_appliance_capabilities = capability_registry
    app.include_router(
        create_capabilities_router(
            capability_registry,
            authenticator=authenticator,
            audit=audit,
        )
    )
    app.include_router(
        create_task_projection_router(
            supervisor=getattr(app.state, "task_supervisor", None),
            realtime_gateway=getattr(app.state, "realtime_gateway", None),
            audit=audit,
            authenticator=authenticator,
        )
    )

    # Echo Storage 同机协同启动:autostart 启用时后台拉起 sibling 项目,
    # 失败只记录日志,不阻塞其余功能。
    try:
        from appliance.storage_spawner import start_storage_service

        result = start_storage_service()
        if result["error"]:
            _alog.warning(
                "Echo Storage autostart status: started=%s already_running=%s error=%s",
                result["started"],
                result["already_running"],
                result["error"],
            )
        elif result["started"]:
            _alog.info("Echo Storage autostarted at %s", result["url"])
    except Exception as exc:  # pragma: no cover - defensive
        _alog.warning("Echo Storage autostart failed: %s", exc)

    # Agent can mount local auth before this extension with an immutable
    # boot-time config. Exact-prefix dispatch keeps the official router but
    # makes this live device config authoritative after password rotation.
    if not getattr(app.state, "echo_appliance_live_local_auth", False):
        app.add_middleware(
            ApplianceLocalAuthMiddleware,
            auth_config=login_auth_cfg,
            identity_store=context.identity_store,
            account_security=account_security,
        )
        app.state.echo_appliance_live_local_auth = True

    # Remove stale appliance JWTs before any Agent or Echo route resolves a
    # principal. Root/static requests still render, and public endpoints remain
    # public; revoked credentials simply no longer identify an actor.
    if not getattr(app.state, "echo_appliance_session_revocation", False):
        app.add_middleware(
            ApplianceSessionRevocationMiddleware,
            account_security=account_security,
        )
        app.state.echo_appliance_session_revocation = True

    # Browser boundary is added last so it wraps desktop, auth interception,
    # session revocation and every NAS control surface.
    if not getattr(app.state, "echo_appliance_web_security", False):
        app.add_middleware(ApplianceWebSecurityMiddleware)
        app.state.echo_appliance_web_security = True
