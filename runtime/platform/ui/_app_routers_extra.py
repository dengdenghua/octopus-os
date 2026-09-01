"""Remaining / realtime / plugin / extension router wiring (part 2).

Extracted from ``app.py`` during the god-file reduction (§2.9 of the
navigation map). Mounts ambient-suggestions, remote-backends,
workspace-api, prompts, ambient-scheduler, invariants, journal,
agent-trace, memory, cron, organizations, the realtime WebSocket
gateway, evolution, plugins, plugin-hub, stub, teach-repeat, and
anthropic-compat routers, then loads app extensions.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import app_paths

from ._app_context import AppContext


def _register_plugin_hub_lifecycle(app: Any, hub: Any) -> None:
    """Start loaded plugins with the app and stop their background work cleanly."""

    def _start_plugins() -> None:
        started = hub.start_all()
        logging.getLogger(__name__).info(
            "PluginHub auto-started %d plugins: %s",
            len(started),
            started,
        )

    def _stop_plugins() -> None:
        stopped = hub.stop_all()
        logging.getLogger(__name__).info(
            "PluginHub stopped %d plugins: %s",
            len(stopped),
            stopped,
        )

    app.router.add_event_handler("startup", _start_plugins)
    app.router.add_event_handler("shutdown", _stop_plugins)


def mount_routers_b(
    ctx: AppContext,
    *,
    journal_path: Any,
) -> None:
    """Mount the remaining routers + realtime gateway + extensions."""
    app = ctx.app
    stack = ctx.stack
    state = ctx.state

    # ─── Ambient Suggestions (feature-flag gated) ──────────────────────────
    # Entirely surface-level; if the router fails to mount, the rest
    # of the app is fine.
    try:
        from runtime.sensing.gateway.ambient_suggestions_router import (
            create_ambient_suggestions_router,
        )

        app.include_router(
            create_ambient_suggestions_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _amb_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "ambient_suggestions_router failed to mount: %s",
            _amb_exc,
        )

    # ─── Remote backends (feature-flag gated) ──────────────────────────────
    try:
        from runtime.sensing.gateway.remote_backends_router import (
            create_remote_backends_router,
        )

        app.include_router(
            create_remote_backends_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _rb_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "remote_backends_router failed to mount: %s",
            _rb_exc,
        )

    # ─── Workspace HTTP API (feature-flag gated) ─────────────────────────
    # Mount + membership + file-lease CRUD on top of WorkspaceStore /
    # MountBackendRegistry / LeaseStore. Registered AFTER the per-thread
    # ``workspaces_router`` so that router's ``GET /api/workspaces/{thread_id}/outputs``
    # continues to win for output listing; the new router owns the
    # create / list / members / lease / health endpoints. The new
    # ``GET /api/workspaces/{workspace_id}`` is shadowed by the thread
    # router's ``GET /api/workspaces/{thread_id}`` when both are
    # mounted — callers that need the Workspace entity by id should
    # filter the ``GET /api/workspaces?user_id=...`` list response.
    try:
        from runtime.sensing.gateway.workspace_api_router import (
            create_workspace_api_router,
        )

        app.include_router(
            create_workspace_api_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _wsa_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "workspace_api_router failed to mount: %s",
            _wsa_exc,
        )

    # ─── Prompts hot-reload (feature-flag gated) ───────────────────────────
    # Lives at <data>/prompt_templates/ to stay out of the way of the
    # YAML-backed PromptLoader at <repo>/prompts/. New Markdown-based
    # editable templates land here; legacy callers keep using the
    # original loader.
    try:
        from runtime.platform.prompts.registry import PromptRegistry
        from runtime.platform.prompts.seed import seed_if_empty
        from runtime.sensing.gateway.prompts_router import (
            create_prompts_router,
        )

        _prompts_dir = app_paths().data_dir / "prompt_templates"
        _prompt_registry = PromptRegistry(_prompts_dir)
        app.state.prompt_registry = _prompt_registry
        # Auto-install the default templates the first time the
        # server boots against an empty directory. Safe to call on
        # every boot — it's a no-op once any .md exists.
        with contextlib.suppress(Exception):
            seed_if_empty(_prompt_registry)
        # dsh ctx.sessionTitle surface: expose the current session title as
        # the {{ session_title }} prompt variable (best-effort; never fails
        # assembly when no ambient session exists).
        with contextlib.suppress(Exception):
            from runtime.memory.threads.session_title import register_session_title_variable

            register_session_title_variable(
                _prompt_registry,
                store_getter=lambda: getattr(ctx, "thread_store", None),
            )
        app.include_router(
            create_prompts_router(
                _prompt_registry,
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _pr_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "prompts_router failed to mount: %s",
            _pr_exc,
        )

    # ─── Ambient suggestions scheduler (feature-flag gated) ───────────────
    # Periodic LLM-backed regeneration. No-op when flag is off;
    # honors interval from ``ui.ambient_suggestions_interval_sec``.
    try:
        from runtime.memory.skills_lib.ambient_suggestions_scheduler import (
            AmbientSchedulerConfig,
            get_ambient_scheduler,
        )
        from runtime.platform import feature_flags

        get_ambient_scheduler().start(
            AmbientSchedulerConfig(
                enabled=feature_flags.is_on("ui.ambient_suggestions"),
                interval_sec=max(
                    60,
                    int(feature_flags.value("ui.ambient_suggestions_interval_sec", 21600)),
                ),
            )
        )
    except Exception as _ambs_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "ambient_suggestions_scheduler failed to start: %s",
            _ambs_exc,
        )

    # ─── Invariants catalog ────────────────────────────────────────────────
    # Read-only constitution surface: which rule_ids are enforced
    # by which functions. Walks sys.modules — safe to mount even
    # before subsystems are warmed up.
    try:
        from runtime.sensing.gateway.invariants_router import (
            create_invariants_router,
        )

        app.include_router(
            create_invariants_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _inv_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "invariants_router failed to mount: %s",
            _inv_exc,
        )

    # ─── Journal query index (read-only SQLite view) ───────────────────────
    # Pure read-side optimization — the JSONL writer in journal.py is
    # untouched; this surface lets the UI filter events by type/time/
    # session without scanning gigabytes of jsonl.
    try:
        from runtime.sensing.gateway.journal_router import create_journal_router

        _journal_jsonl: Path | None = None
        _journal_backing = getattr(state.journal, "_path", None)
        if isinstance(_journal_backing, Path):
            _journal_jsonl = _journal_backing
        elif isinstance(journal_path, Path):
            _journal_jsonl = journal_path
        app.include_router(
            create_journal_router(
                default_jsonl_path=_journal_jsonl,
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            ),
        )
    except Exception as _jr_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "journal_router failed to mount: %s",
            _jr_exc,
        )

    # ─── Cron settings compatibility API ───────────────────────────────
    # Local memory compatibility API. Keep it before the broad stub router so
    # manual memory editing/search/export is backed by the real local store.
    try:
        from runtime.sensing.gateway.agent_trace_router import (
            create_agent_trace_router,
        )

        _trace_registry = None
        if stack is not None:
            _trace_registry = getattr(
                getattr(stack, "executor", None),
                "registry",
                None,
            )
        _trace_registry = _trace_registry or getattr(state, "registry", None)
        app.include_router(
            create_agent_trace_router(
                store=getattr(state, "trace_store", None),
                db_path=ctx.trace_store_path,
                journal=getattr(state, "journal", None),
                registry=_trace_registry,
                auto_persist_dir=app_paths().data_dir / "forged_skills",
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _trace_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "agent_trace_router failed to mount: %s",
            _trace_exc,
        )

    from runtime.sensing.gateway.memory_router import create_memory_router

    app.include_router(
        create_memory_router(
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # Cron settings compatibility API. Keep it before the broad stub router
    # so /api/cron/* is backed by the real local settings store.
    from runtime.sensing.gateway.cron_router import create_cron_router

    app.include_router(
        create_cron_router(
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # Organization-level evolution endpoints (team topologies).
    # Fail-soft mount: a missing organization package or a route
    # collision degrades to a warning, never breaks the whole app.
    try:
        from runtime.sensing.gateway.organizations_router import (
            create_organizations_router,
        )

        app.include_router(
            create_organizations_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
                agent_registry=ctx.agent_registry,
            )
        )
    except Exception as _org_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "organizations router failed to mount: %s",
            _org_exc,
        )

    # Realtime WebSocket gateway — JSON-RPC 2.0 + item-oriented protocol.
    # The backing runtime depends on what's wired into this process:
    # CerebrumRuntime when ``stack`` is available (production), and
    # EchoRuntime when running headless (minimal demos, unit tests,
    # ``python -m runtime ui`` with no planner). The wire contract is
    # the same either way — clients never branch on which is live.
    try:
        # Per-thread JSONL logs live under the same data root as every
        # other persisted runtime file, so an ``ECHO_HOME`` or
        # ``ECHO_DATA_DIR`` override relocates them together. Falls
        # back to ``./data/threads`` when no override is set.
        from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

        _realtime_logs_root = app_paths().data_dir / "threads"

        # Whether a client may set approvalPolicy="never" to skip the
        # human approval gate. SECURE default: off unless the operator
        # explicitly enables safety.allow_client_approval_bypass in the
        # loaded config. Was previously hardcoded True, letting any WS
        # client disable approvals.
        _safety_cfg = getattr(getattr(stack, "config", None), "safety", None)
        _allow_approval_bypass = bool(
            getattr(_safety_cfg, "allow_client_approval_bypass", None) or False
        )
        from runtime.sensing.gateway.thread_access import ThreadAccessResolver

        _realtime_thread_access = ThreadAccessResolver(
            thread_store=ctx.thread_store,
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
            identity_store=ctx.identity_store,
            # Auth-off is the local single-user compatibility surface. Older
            # and benchmark-created ThreadState rows have no owner/tenant; the
            # resolver grants those rows only when they are also unlinked.
            allow_anonymous_ownerless=not ctx.require_auth,
        )

        if stack is not None:
            from runtime.memory.threads.compaction import (
                CompactionPolicy,
                compaction_trigger_tokens,
            )

            # Compaction kicks in once a thread accrues ~24 turns OR an
            # estimated volume at ~90% of the active model's advertised
            # context window — whichever first; we summarise down to the
            # last 12. The trigger is derived from ``planner.model``
            # (operator window config → models.dev snapshot → name
            # heuristics → 256k convention for unresolvable ids like
            # ``auto``), so a 1M-window relay compacts ~8x later than a
            # 128k one instead of sharing a flat guess. The token path
            # catches few-turns-huge-content threads (a couple of 20k-
            # token tool dumps would otherwise blow a 128k window well
            # before turn 24).
            _chat_model = getattr(getattr(stack, "planner", None), "model", None)
            _compaction_policy = CompactionPolicy(
                trigger_at=24,
                keep_recent=12,
                trigger_tokens=compaction_trigger_tokens(_chat_model),
                max_summary_chars=4_000,
            )
            _summary_router = getattr(getattr(stack, "planner", None), "router", None)
            _project_os_hooks: dict[str, Any] = {}
            if ctx.project_model_router is not None:
                try:
                    from runtime.projectos.llm_hooks import create_llm_hooks

                    _project_os_hooks = create_llm_hooks(
                        ctx.project_model_router,
                        subagent_runner=ctx.subagent_runner,
                    )
                except Exception as exc:  # noqa: BLE001
                    logging.getLogger(__name__).warning(
                        "projectos llm hooks unavailable for realtime: %s",
                        exc,
                    )

            # dsh auto-title: first-completed-turn title regeneration.
            # A SessionTitleService rides the same thread store as the
            # sidebar; when a model router exists we register an LLM
            # provider so the first turn upgrades "New chat" to a real
            # summary. Failures keep the fallback title and are logged —
            # title generation must never break the turn lifecycle.
            _session_titles: Any = None
            if ctx.thread_store is not None:
                try:
                    from runtime.sensing.gateway.thread_state_router import (
                        build_auto_title_service,
                    )

                    _session_titles = build_auto_title_service(
                        ctx.thread_store,
                        model_router=ctx.project_model_router,
                    )
                except Exception as exc:  # noqa: BLE001
                    logging.getLogger(__name__).warning(
                        "session title auto-refresh unavailable: %s",
                        exc,
                    )
                    _session_titles = None

            _realtime_runtime_kwargs: dict[str, Any] = {
                "stack": stack,
                "agent": None,  # resolved per turn from the registry
                "agent_registry": ctx.agent_registry,
                "logs_root": str(_realtime_logs_root),
                "policy_path": app_paths().permissions_path,
                "workspace_root": str(
                    ctx.thread_workspace_root or (app_paths().data_dir / "workspaces")
                ),
                "compaction_policy": _compaction_policy,
                "summary_router": _summary_router,
                "thread_store": ctx.thread_store,
                "reflex_router": ctx.reflex_router,
                "trace_store": getattr(state, "trace_store", None),
                "task_supervisor": getattr(state, "task_supervisor", None),
                "allow_client_auto_approve": _allow_approval_bypass,
                "allow_local_workspace_access": ctx.allow_local_workspace_access,
                "cowork_group_store": (
                    getattr(ctx.cowork_runtime, "group_store", None)
                    if ctx.cowork_runtime is not None
                    else None
                ),
                "collaboration_store": (
                    getattr(ctx.cowork_runtime, "collaboration_store", None)
                    if ctx.cowork_runtime is not None
                    else None
                ),
                "project_store": ctx.project_store,
                "project_os_hooks": _project_os_hooks,
                "subagent_runner": ctx.subagent_runner,
                "session_titles": _session_titles,
            }
            if ctx.kernel is not None:
                _realtime_runtime = ctx.kernel.create_realtime_runtime(**_realtime_runtime_kwargs)
            else:
                from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

                _realtime_runtime = CerebrumRuntime(**_realtime_runtime_kwargs)
        else:
            from runtime.sensing.gateway.realtime_echo import EchoRuntime

            _realtime_runtime = EchoRuntime(logs_root=str(_realtime_logs_root))

        # Runtime request handlers perform their own thread checks after the
        # gateway handshake. Share the same dynamic resolver so resume/list,
        # steering and turn execution observe room removal immediately.
        _realtime_runtime._thread_access_resolver = _realtime_thread_access  # noqa: SLF001
        # Echo/custom runtimes do not receive these stores in their
        # constructors, but the claimed gateway boundary and late background
        # writer guard must still consult the same durable deletion fences.
        _realtime_runtime._thread_store = ctx.thread_store  # noqa: SLF001
        _realtime_runtime._project_store = ctx.project_store  # noqa: SLF001

        _realtime_gateway = RealtimeGateway(
            runtime=_realtime_runtime,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            allow_local_workspace_access=ctx.allow_local_workspace_access,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
            allow_client_approval_bypass=_allow_approval_bypass,
            thread_access_resolver=_realtime_thread_access,
        )
        app.include_router(_realtime_gateway.router)
        # Exposed for introspection/tests (e.g. asserting the secure
        # default for client approval bypass).
        app.state.realtime_gateway = _realtime_gateway
        ctx.realtime_runtime = _realtime_runtime
        ctx.allow_approval_bypass = _allow_approval_bypass

        async def _drain_realtime_runtime() -> None:
            drain = getattr(_realtime_runtime, "drain_active_turns_for_shutdown", None)
            if drain is None:
                return
            result = await drain(timeout_seconds=3.0)
            if result.get("requested"):
                logging.getLogger(__name__).info(
                    "realtime shutdown drain requested=%s drained=%s remaining=%s",
                    result.get("requested"),
                    result.get("drained"),
                    result.get("remaining"),
                )

        app.router.add_event_handler("shutdown", _drain_realtime_runtime)

        # Static permission policy ("always trust" rules) shares the
        # same JSON file as the realtime gateway uses for filtering, so
        # mount the management router right alongside it.
        from runtime.platform.ui.permissions_router import (
            create_permissions_router,
        )

        app.include_router(
            create_permissions_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _rt_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "realtime gateway failed to mount: %s",
            _rt_exc,
        )

    # ─── Evolution API · fitness / drift / ledger / canary ──────
    try:
        from runtime.sensing.gateway.evolution_router import create_evolution_router

        app.include_router(
            create_evolution_router(
                stack=stack,
                agent_registry=ctx.agent_registry,
                project_root=ctx.project_root,
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _evo_exc:
        logging.getLogger(__name__).warning(
            "evolution router failed to mount: %s",
            _evo_exc,
        )

    # Codex-compatible plugin catalog. Keep it before the broad stub router
    # so the frontend /plugins page shows copied .codex-plugin manifests.
    try:
        from runtime.sensing.gateway.plugins_router import create_plugins_router

        app.include_router(
            create_plugins_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _plugins_exc:
        logging.getLogger(__name__).warning(
            "plugins router failed to mount: %s",
            _plugins_exc,
        )

    # ─── PluginHub (pluggable module architecture) ────────────────
    # Auto-discovers and loads plugins from ~/.echo/plugins/.
    # Each plugin can register skills, channels, routes, and a
    # frontend config UI via plugin.yaml + ModulePlugin subclass.
    try:
        from runtime.execution.arms.tool_registry import get_tool_registry
        from runtime.execution.suckers.jobs_skills import get_jobs_registry
        from runtime.platform.plugins.plugin_hub import (
            PluginHub,
            default_bundled_plugin_dir,
        )
        from runtime.platform.process.composition import build_default_service_bus
        from runtime.safety.hooks import get_global_registry
        from runtime.sensing.gateway.plugin_hub_router import (
            create_plugin_hub_router,
        )

        # Composition layer: bind kernel services (journal / memory) and let
        # plugins declare provides/consumes against them. Exposed on app.state
        # so future blocks (arms, model router) register here too.
        _service_bus = build_default_service_bus(
            journal=getattr(state, "journal", None),
            event_bus=None,
        )
        app.state.service_bus = _service_bus

        _hub = PluginHub(
            # Cloud workbench packages are installed below the runtime data
            # root.  Appliance deployments set ``ECHO_DATA_DIR`` to an
            # isolated writable volume, so PluginHub must discover external
            # packages there instead of falling back to the developer's
            # ``~/.echo/plugins`` directory.
            plugin_dir=app_paths().data_dir / "plugins"
            if os.environ.get("ECHO_DATA_DIR")
            else None,
            # A custom writable plugin root must not hide Echo's read-only
            # factory plugins. This distinction is essential in packaged
            # desktop/appliance runs, where ECHO_DATA_DIR is always isolated.
            bundled_plugin_dir=default_bundled_plugin_dir(),
            skill_registry=state.registry,
            channel_manager=ctx.channel_manager,
            fastapi_app=app,
            service_bus=_service_bus,
            tool_registry=get_tool_registry(),
            prompt_registry=getattr(app.state, "prompt_registry", None),
            hook_registry=get_global_registry(),
            jobs_registry=get_jobs_registry(),
        )
        _loaded = _hub.load_all()
        if _loaded:
            logging.getLogger(__name__).info(
                "PluginHub auto-loaded %d plugins: %s",
                len(_loaded),
                _loaded,
            )

        app.include_router(
            create_plugin_hub_router(
                hub=_hub,
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
        app.state.plugin_hub = _hub
        _register_plugin_hub_lifecycle(app, _hub)
    except Exception as _hub_exc:
        logging.getLogger(__name__).warning(
            "PluginHub failed to initialize: %s",
            _hub_exc,
        )

    # Installed workbench UI packages are independent, versioned assets. The
    # host frontend keeps only the loader; an uninstalled package has no entry
    # file to execute and a broken package fails within its own surface.
    try:
        from runtime.sensing.gateway.workbench_packages_router import (
            create_workbench_packages_router,
        )

        app.include_router(
            create_workbench_packages_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _workbench_packages_exc:
        logging.getLogger(__name__).warning(
            "workbench packages router failed to mount: %s",
            _workbench_packages_exc,
        )

    # ─── Design Studio / local ComfyUI bridge ──────────────────
    try:
        from runtime.sensing.gateway.design_studio_router import (
            create_design_studio_router,
        )

        app.include_router(
            create_design_studio_router(
                project_store=ctx.project_store,
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _design_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "design studio router failed to mount: %s",
            _design_exc,
        )

    from runtime.sensing.gateway.stub_router import create_stub_router

    app.include_router(
        create_stub_router(
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # ─── A2A remote agent registry (a2a-agents-panel) ─────────────
    # Frontend panel shipped earlier without backend routes; mount the
    # protocol relay so registered remote agents can be listed, probed,
    # and delegated tasks over the A2A wire protocol.
    try:
        from runtime.sensing.gateway.a2a_router import create_a2a_router

        app.include_router(
            create_a2a_router(
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
            )
        )
    except Exception as _a2a_exc:  # noqa: BLE001 — optional surface
        logging.getLogger(__name__).warning(
            "A2A router failed to initialize: %s",
            _a2a_exc,
        )

    from runtime.sensing.gateway.teach_repeat_router import create_teach_repeat_router

    # Wire the live journal + skill registry so REC stop forges a reusable
    # skill from the conversation's trajectory (active single-demo forge).
    _tr_registry = None
    if stack is not None:
        _tr_registry = getattr(getattr(stack, "executor", None), "registry", None)
    _tr_registry = _tr_registry or getattr(state, "registry", None)
    from runtime.platform.capabilities.capability_registry import CapabilityRegistry

    app.include_router(
        create_teach_repeat_router(
            journal=getattr(state, "journal", None),
            registry=_tr_registry,
            auto_persist_dir=app_paths().data_dir / "forged_skills",
            capability_registry=CapabilityRegistry(),
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # ─── Anthropic Managed Agents compat layer ──────────────
    # Exposes /v1/sessions REST + SSE so the official ``anthropic``
    # SDK (``client.beta.sessions.*``) can connect to echo-agent
    # as a self-hosted backend. Beta header required:
    #   anthropic-beta: managed-agents-2026-04-01
    try:
        from runtime.sensing.gateway.anthropic_compat import (
            create_anthropic_compat_router,
        )

        # Attach the realtime CerebrumRuntime to the stack so the
        # anthropic compat layer can reuse it without re-instantiating.
        if ctx.realtime_runtime is not None and stack is not None:
            try:  # noqa: SIM105
                stack._realtime_runtime = ctx.realtime_runtime  # noqa: SLF001
            except (AttributeError, TypeError):
                pass

        app.include_router(
            create_anthropic_compat_router(
                stack=stack,
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
                agent_registry=ctx.agent_registry,
            )
        )
    except Exception as _anth_exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "anthropic compat router failed to mount: %s",
            _anth_exc,
        )

    # 扩展点:消费者(企业版/echo-os/mobile)经 ECHO_APP_EXTENSIONS 在此挂
    # 自定义路由,无需 fork agent。未配置则 no-op。见 runtime/platform/extensions.py。
    from runtime.platform.extensions import (
        AppExtensionContext,
        load_app_extensions,
    )

    load_app_extensions(
        app,
        AppExtensionContext(
            identity_store=ctx.identity_store,
            stack=stack,
            agent_registry=ctx.agent_registry,
        ),
    )
