"""System / browser / project / FS / loop / compute router wiring (part 1).

Extracted from ``app.py`` during the god-file reduction (§2.8-§2.9 of
the navigation map). Mounts the system router, control-sessions, browser,
searxng, cookbook, cowork-group, project-os, fs, agent-modes, workspaces,
lsp, loop, task-runs, verify, deployments, debug, index, computer,
android, completion, parallel-task, uploads, observability, account-usage,
evolution-ops, dag, skill-market, meta-skill, apps, agent-world,
registry-consumer, enterprise-assets, and intelligence routers.
"""

from __future__ import annotations

import logging
from typing import Any

from runtime.platform.process.paths import app_paths

from ._app_context import AppContext


def _workspaces_router_root(ctx: AppContext) -> Any:
    """Use the same workspace root as turn execution and thread storage."""

    return ctx.thread_workspace_root or (app_paths().data_dir / "workspaces")


def mount_routers_a(
    ctx: AppContext,
    *,
    journal_path: Any,
) -> None:
    """Mount the first group of routers; set ctx.project_store / project_model_router."""
    app = ctx.app
    stack = ctx.stack
    state = ctx.state

    from runtime.sensing.gateway.system_router import create_system_router

    def _reset_runtime_memory() -> None:
        clear_threads = getattr(ctx.thread_store, "clear", None)
        if callable(clear_threads):
            clear_threads()
        clear_teams = getattr(ctx.team_rooms_router, "reset_state", None)
        if callable(clear_teams):
            clear_teams()

    app.include_router(
        create_system_router(
            project_root=ctx.project_root,
            identity_store=ctx.identity_store,
            memory_reset_callback=_reset_runtime_memory,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # Unified control plane for browser / Chrome / webview / computer sessions.
    from runtime.sensing.gateway.control_sessions_router import create_control_sessions_router

    app.include_router(
        create_control_sessions_router(
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # Browser session and relay APIs.
    from runtime.platform.ui.browser_router import create_browser_router

    app.include_router(
        create_browser_router(
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # Native multi-platform search/read/collection operations.
    from runtime.platform.ui.reach_router import create_reach_router

    app.include_router(
        create_reach_router(
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # ─── Local-model cookbook · hardware-aware recommendations + pull ──
    # GET /api/cookbook/snapshot (public) + POST /api/cookbook/pull (auth-gated).
    from runtime.platform.ui.cookbook_router import create_cookbook_router

    app.include_router(
        create_cookbook_router(
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # ─── Project OS store · shared by the project-mode auto-bind below and the
    # projects_router, so a project created by switching a thread to project
    # mode is the same store the workbench 项目 tab reads from.
    from runtime.projectos.store import ProjectStore
    from runtime.sensing.gateway.projects_router import create_projects_router

    project_store = ProjectStore()
    app.state.project_store = project_store
    bind_team_project_store = getattr(ctx.team_rooms_router, "bind_project_store", None)
    if callable(bind_team_project_store):
        bind_team_project_store(project_store)
    project_model_router = (
        getattr(getattr(stack, "planner", None), "router", None) if stack is not None else None
    )

    # ─── Cowork thread-group · WeChat-style membership + mode + blackboard ──
    # GET /api/cowork/{thread} (public) + POST/DELETE members/mode/blackboard
    # (auth-gated). A thread IS the group; 1:1 is the N=2 case.
    from runtime.sensing.gateway.cowork_group_router import create_cowork_group_router

    app.include_router(
        create_cowork_group_router(
            store=(
                getattr(ctx.cowork_runtime, "group_store", None)
                if ctx.cowork_runtime is not None
                else None
            ),
            async_store=(
                getattr(ctx.cowork_runtime, "async_store", None)
                if ctx.cowork_runtime is not None
                else None
            ),
            collaboration_store=(
                getattr(ctx.cowork_runtime, "collaboration_store", None)
                if ctx.cowork_runtime is not None
                else None
            ),
            team_rooms_router=ctx.team_rooms_router,
            team_tasks_router=ctx.team_tasks_router,
            runtime=ctx.cowork_runtime,
            project_store=project_store,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # ─── Project OS · milestone-driven project execution ──
    # GET /api/projects/* is authenticated and owner/tenant scoped in shared
    # mode; POST plan/tick/run and other mutations use the same Principal.
    # LLM hooks when a model router is available, else deterministic stubs.
    app.include_router(
        create_projects_router(
            store=project_store,
            group_store=(
                getattr(ctx.cowork_runtime, "group_store", None)
                if ctx.cowork_runtime is not None
                else None
            ),
            collaboration_store=(
                getattr(ctx.cowork_runtime, "collaboration_store", None)
                if ctx.cowork_runtime is not None
                else None
            ),
            team_rooms_router=ctx.team_rooms_router,
            thread_store=ctx.thread_store,
            workspace_root=ctx.thread_workspace_root,
            model_router=project_model_router,
            subagent_runner=ctx.subagent_runner,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )
    ctx.project_store = project_store
    ctx.project_model_router = project_model_router

    # ─── FS router · extracted to fs_router.py ─────────
    # The 3 endpoints + 2 helpers that used to live here inline now
    # sit in runtime/sensing/siphon/fs_router.py. Same contract ·
    # no auth / no scope (these serve the UI file-browser which
    # opens user-chosen directories) · test coverage in
    # tests/test_app_fs_endpoints.py.
    from runtime.sensing.gateway.fs_router import create_fs_router

    app.include_router(
        create_fs_router(
            thread_store=ctx.thread_store,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            allow_local_workspace_access=ctx.allow_local_workspace_access,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
            workspace_root=ctx.thread_workspace_root,
        )
    )

    from runtime.sensing.gateway.agent_modes_router import create_agent_modes_router

    app.include_router(
        create_agent_modes_router(
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            allow_local_workspace_access=ctx.allow_local_workspace_access,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    try:
        from runtime.sensing.gateway.workspaces_router import create_workspaces_router

        app.include_router(
            create_workspaces_router(
                # The execution stack derives this root from the active
                # journal. Artifact listing/preview must read from that exact
                # location; app_paths() may point at an appliance data dir
                # while turns are writing into a project-local journal.
                workspace_root=_workspaces_router_root(ctx),
                thread_store=ctx.thread_store,
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _workspace_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "workspaces router failed to mount: %s",
            _workspace_exc,
        )

    try:
        from runtime.sensing.gateway.org_router import create_org_router

        app.include_router(
            create_org_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _org_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("org router failed to mount: %s", _org_exc)

    from runtime.sensing.gateway.lsp_router import create_lsp_router

    app.include_router(
        create_lsp_router(
            state.registry,
            thread_store=ctx.thread_store,
            workspace_root=ctx.thread_workspace_root,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    try:
        from runtime.execution.loops import (
            LoopController,
            LoopRunDispatcher,
            LoopRunStore,
        )
        from runtime.memory.learning.review_queue import ReviewQueue
        from runtime.platform.runtime_policy.workspaces import WorkspaceManager
        from runtime.sensing.gateway.loop_router import create_loop_router

        _loop_workspace_root = ctx.thread_workspace_root or (app_paths().data_dir / "workspaces")
        _loop_store = LoopRunStore(app_paths().loop_runs_path)
        # Audit R-02: startup reconciliation — runs left ACTIVE by the
        # previous process (crash/upgrade/restart) have nothing driving
        # them; fold them into ``interrupted`` before any request can
        # observe them stuck as "running". Best-effort: a reconciliation
        # failure must not block boot.
        try:
            _reconciled = _loop_store.reconcile_interrupted()
            if _reconciled:
                logging.getLogger(__name__).info(
                    "loop runs reconciled after restart: %d interrupted (%s)",
                    len(_reconciled),
                    ", ".join(_reconciled),
                )
        except Exception as _reconcile_exc:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "loop run startup reconciliation failed: %s", _reconcile_exc
            )
        # Audit T-13: retention — a long-lived loop store must not grow
        # without bound. Best-effort, like reconciliation.
        try:
            _pruned = _loop_store.prune()
            if _pruned:
                logging.getLogger(__name__).info(
                    "loop run store pruned: %d run(s) over retention policy",
                    _pruned,
                )
        except Exception as _prune_exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("loop run retention prune failed: %s", _prune_exc)
        _loop_review_queue = ReviewQueue(app_paths().review_queue_path)
        _loop_controller = (
            LoopController(
                store=_loop_store,
                stack=stack,
                workspace_manager=WorkspaceManager(_loop_workspace_root),
                review_queue=_loop_review_queue,
                candidate_registry_path=app_paths().evolution_candidates_path,
                trace_store=getattr(state, "trace_store", None),
                task_supervisor=getattr(state, "task_supervisor", None),
            )
            if stack is not None
            else None
        )
        _loop_dispatcher = (
            LoopRunDispatcher(
                controller=_loop_controller,
                store=_loop_store,
            )
            if _loop_controller is not None
            else None
        )
        app.include_router(
            create_loop_router(
                store=_loop_store,
                controller=_loop_controller,
                dispatcher=_loop_dispatcher,
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
        app.state.loop_controller = _loop_controller
        app.state.loop_dispatcher = _loop_dispatcher
        app.state.loop_store = _loop_store
        app.state.task_supervisor = getattr(state, "task_supervisor", None)
    except Exception as _loop_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "loop router failed to mount: %s",
            _loop_exc,
        )

    try:
        from runtime.sensing.gateway.task_runs_router import create_task_runs_router

        app.include_router(
            create_task_runs_router(
                supervisor=getattr(state, "task_supervisor", None),
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _task_runs_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "task runs router failed to mount: %s",
            _task_runs_exc,
        )

    from runtime.sensing.gateway.verify_router import create_verify_router

    app.include_router(
        create_verify_router(
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    from runtime.sensing.gateway.deployments_router import create_deployments_router

    app.include_router(
        create_deployments_router(
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    from runtime.sensing.gateway.debug_router import create_debug_router

    app.include_router(
        create_debug_router(
            store=ctx.thread_store,
            stack=stack,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    from runtime.sensing.gateway.index_router import create_index_router

    app.include_router(
        create_index_router(
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    from runtime.sensing.gateway.computer_router import create_computer_router

    app.include_router(
        create_computer_router(
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    from runtime.sensing.gateway.android_router import create_android_router

    app.include_router(
        create_android_router(
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    from runtime.sensing.gateway.completion_router import create_completion_router

    app.include_router(
        create_completion_router(
            stack=stack,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    from runtime.execution.misc.parallel_runner import create_parallel_task_router

    app.include_router(
        create_parallel_task_router(
            stack=stack,
            thread_store=ctx.thread_store,
            workspace_root=ctx.thread_workspace_root,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # ─── Uploads/artifacts router · extracted to uploads_router.py
    # The 4 endpoints + 2 helpers that used to live here inline now
    # sit in runtime/sensing/siphon/uploads_router.py. The factory
    # accepts ``thread_store=None`` · in that case every endpoint
    # returns 503 (preserves the demo-app-without-a-stack contract).
    from runtime.sensing.gateway.uploads_router import create_uploads_router

    app.include_router(
        create_uploads_router(
            thread_store=ctx.thread_store,
            workspace_root=ctx.thread_workspace_root,
            legacy_upload_root=ctx.thread_upload_root,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # ─── Observability router · extracted to observability_router.py
    # The 6 endpoints (journal / reflect / kg / progress / stream /
    # run) that used to live inline now sit in
    # runtime/sensing/siphon/observability_router.py. Same wire
    # contract · preserves progress_tracker-as-singleton semantics
    # (one tracker per app, incremental O(N_tasks) snapshots).
    from runtime.sensing.gateway.observability_router import (
        create_observability_router,
    )

    app.include_router(
        create_observability_router(
            journal=state.journal,
            registry=state.registry,
            planner=getattr(stack, "planner", None) if stack is not None else None,
            effect_store=(
                getattr(stack.executor, "effect_store", None)
                if stack is not None and getattr(stack, "executor", None) is not None
                else None
            ),
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # Local account usage is derived from the same journal cost events that
    # power /api/budget/summary. Mount before the broad compatibility stub.
    from runtime.sensing.gateway.account_usage_router import (
        create_account_usage_router,
    )

    app.include_router(
        create_account_usage_router(
            journal=state.journal,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # Evolution operator-console fallback routes. The real Reflex/RecipeForge
    # admin router registers many of these paths earlier when available; this
    # router gives lightweight backend runs explicit empty/disabled snapshots
    # instead of frontend-visible 404s.
    from runtime.sensing.gateway.evolution_ops_router import (
        create_evolution_ops_router,
    )

    app.include_router(
        create_evolution_ops_router(
            journal=state.journal,
            registry=state.registry,
            planner=getattr(stack, "planner", None) if stack is not None else None,
            thread_store=ctx.thread_store,
            forged_skill_dir=app_paths().data_dir / "forged_skills",
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    try:
        from runtime.sensing.gateway.dag_debugger_router import (
            create_dag_debugger_router,
        )

        app.include_router(
            create_dag_debugger_router(
                journal=state.journal,
                planner=getattr(stack, "planner", None) if stack is not None else None,
            )
        )
    except Exception as _dag_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("dag_debugger_router failed to mount: %s", _dag_exc)

    # ─── Skill Marketplace Web API ──────────────
    try:
        from runtime.sensing.gateway.skill_market_router import (
            create_skill_market_router,
        )

        app.include_router(
            create_skill_market_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _sm_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("skill_market_router failed to mount: %s", _sm_exc)

    # ─── MetaSkill (能力包) Web API ──────────────
    try:
        from runtime.sensing.gateway.meta_skill_router import (
            create_meta_skill_router,
        )

        app.include_router(
            create_meta_skill_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _ms_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("meta_skill_router failed to mount: %s", _ms_exc)

    try:
        from runtime.sensing.gateway.apps_router import create_apps_router

        app.include_router(
            create_apps_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _apps_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("apps_router failed to mount: %s", _apps_exc)

    # ─── Agent Market Web API ──────────────
    try:
        from runtime.sensing.gateway.agent_world_router import (
            create_agent_world_router,
        )

        app.include_router(
            create_agent_world_router(
                registry=ctx.agent_registry,
                runtime=stack.runtime if stack is not None else None,
                skill_registry=state.registry,
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                allow_local_user_plugin_lifecycle=ctx.allow_local_workspace_access,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _aw_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("agent_world_router failed to mount: %s", _aw_exc)

    # ─── 资产 Registry 消费(母体接 registry · echo-runtime SDK)──────────────
    # 浏览公网 registry 技能 + 按需安装(下载→验签→落地 skills/public→运行时热注册)。
    try:
        from runtime.sensing.gateway.registry_consumer_router import (
            create_registry_consumer_router,
        )

        app.include_router(
            create_registry_consumer_router(
                skill_registry=state.registry,
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _reg_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "registry_consumer_router failed to mount: %s", _reg_exc
        )

    # ─── 统一资产仓库(插件/技能/角色 · WorkBuddy+Codex+本地 归一)────────
    # 浏览 ~/.echo/assets/ 统一 index + 显式重建(sync)。
    try:
        from runtime.sensing.gateway.asset_registry_router import (
            create_asset_registry_router,
        )

        app.include_router(
            create_asset_registry_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _asset_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("asset_registry_router failed to mount: %s", _asset_exc)

    # ─── 连接器市场(WorkBuddy 连接器 fork · 认证编排层)──────────────
    # 浏览/安装 108 个连接器 + 认证编排(connect/status/headers 注入)。
    try:
        from runtime.sensing.gateway.connector_router import create_connector_router

        app.include_router(
            create_connector_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _conn_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("connector_router failed to mount: %s", _conn_exc)

    # ─── 统一能力市场(连接器 + Codex 插件归一)──────────────
    # 一个市场统一管理连接器(WorkBuddy 108)与 Codex 插件(我们正在运行的),
    # 统一 install/enable/connect 生命周期,详见 capability_registry.py。
    try:
        from runtime.sensing.gateway.capability_router import (
            create_capability_router,
        )

        app.include_router(
            create_capability_router(
                codex_accounts=getattr(app.state, "codex_account_service", None),
                model_provider_plugins=getattr(app.state, "model_provider_plugins", None),
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                allow_local_user_plugin_lifecycle=ctx.allow_local_workspace_access,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _cap_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("capability_router failed to mount: %s", _cap_exc)

    # ─── 企业版角色资产消费(数字分身归并 C·只读)──────────────
    # 配 ECHO_ENTERPRISE_URL 时,市场可列举企业版托管的角色资产;不配则
    # available=false。消费而非 fork(见 enterprise_assets_router)。
    try:
        from runtime.sensing.gateway.enterprise_assets_router import (
            create_enterprise_assets_router,
        )

        app.include_router(
            create_enterprise_assets_router(
                registry=ctx.agent_registry,
                runtime=stack.runtime if stack is not None else None,
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _ea_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("enterprise_assets_router failed to mount: %s", _ea_exc)

    # ─── Intelligence Web API ──────────────────────────────────────────────
    try:
        from runtime.sensing.gateway.intelligence_router import (
            create_intelligence_router,
        )

        app.include_router(
            create_intelligence_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _intel_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("intelligence_router failed to mount: %s", _intel_exc)

    # ─── Media (video understanding) Web API ───────────────────────────────
    try:
        from runtime.sensing.gateway.media_router import create_media_router

        app.include_router(
            create_media_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            ),
            prefix="/media",
            tags=["media"],
        )
    except Exception as _media_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("media_router failed to mount: %s", _media_exc)
