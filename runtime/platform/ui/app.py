
from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI
    from fastapi.responses import (
        HTMLResponse,
    )

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    FastAPI = None  # type: ignore[assignment]

from runtime.execution.suckers import SkillRegistry
from runtime.memory.journal import Journal
from runtime.platform.ui.pages import (
    _INDEX_HTML,
    _REFLEX_EDITOR_HTML,
    _REFLEX_PANEL_HTML,
)
from runtime.platform.ui.state import AppState
from runtime.platform.ui.webui_static import _find_webui_dist, _mount_webui


def _attach_molili_fallback_router(
    *,
    stack: Any,
    molili_config: Any,
    link_store: Any,
) -> None:
    """Point the live model dispatcher at the same Molili login store."""
    dispatcher = getattr(
        getattr(stack, "planner", None) if stack is not None else None,
        "router",
        None,
    )
    if dispatcher is None or not hasattr(dispatcher, "set_fallback"):
        return
    try:
        from runtime.sensing.model_router.molili_router import MoliliModelRouter

        dispatcher.set_fallback(
            MoliliModelRouter(
                link_store=link_store,
                base_url=molili_config.base_url,
                default_model=getattr(
                    getattr(stack, "planner", None),
                    "planner_model",
                    None,
                )
                or "molili",
                timeout_seconds=molili_config.request_timeout_seconds,
            )
        )
    except (ImportError, AttributeError, TypeError):  # noqa: BLE001
        return


def create_app(
    journal_path: Path | None = None,
    *,
    journal: Journal | None = None,
    registry: SkillRegistry | None = None,
    stack: Any = None,
    cocoloop_install_dir: Path | None = None,
    cocoloop_identity_store: Any = None,
    cocoloop_require_auth: bool = False,
    agent_registry: Any = None,
    group_registry: Any = None,
    channel_manager: Any = None,
    molili_config: Any = None,
    molili_link_store: Any = None,
    molili_jwt_secret: str | None = None,
    local_auth_config: Any = None,
    default_arm: str = "code_arm",
    parallel_agent_orchestrator: Any = None,
    subagent_registry: Any = None,
) -> Any:
    """Build the FastAPI application with all routers wired in.

    ╔════════════════════════════════════════════════════════════════════╗
    ║ app.py · navigation map (1281 lines).                              ║
    ║                                                                    ║
    ║   §1 _attach_molili_fallback_router                  ~L30          ║
    ║   §2 create_app(...) — main wiring                   ~L64          ║
    ║       §2.1 paths + state + auth gate                 ~L88          ║
    ║       §2.2 health + metrics + probe routers          ~L389         ║
    ║       §2.3 agents + team-rooms + team-tasks          ~L471-543     ║
    ║       §2.4 parallel-agents + deep-research + subagents ~L543-575   ║
    ║       §2.5 wiki + channels + openai gateway          ~L576-650     ║
    ║       §2.6 auth (molili + local) + account + proxy   ~L662-700     ║
    ║       §2.7 meta + mcp + config bundles               ~L700-755     ║
    ║       §2.8 system + browser + fs + workspaces        ~L757-800     ║
    ║       §2.9 lsp + verify + remaining routers          ~L801-end     ║
    ╚════════════════════════════════════════════════════════════════════╝
    """
    if not FASTAPI_AVAILABLE:
        raise RuntimeError(
            "fastapi not installed · `pip install 'fastapi[standard]'` 或 `pip install fastapi uvicorn`"
        )

    from runtime.platform.process.paths import app_paths as _app_paths
    from runtime.platform.process.paths import project_root as _project_root

    _paths = _app_paths()
    _project_root_path = _project_root()
    trace_store_path = _paths.agent_trace_path.resolve()
    state = AppState(
        journal_path=journal_path,
        journal=journal,
        registry=registry,
        trace_store_path=trace_store_path,
    )
    app = FastAPI(title="octopus-agent", version="0.1.0")
    app.state.octopus_state = state

    if molili_jwt_secret is None and molili_config is not None:
        molili_jwt_secret = getattr(molili_config, "jwt_secret", None)

    auth_enabled = (
        bool(molili_config is not None and getattr(molili_config, "enabled", False))
        or bool(
            local_auth_config is not None
            and getattr(local_auth_config, "enabled", False)
        )
    )
    if cocoloop_identity_store is None and auth_enabled:
        from runtime.safety.auth.identity import IdentityStore

        cocoloop_identity_store = IdentityStore()
    if (
        local_auth_config is not None
        and getattr(local_auth_config, "enabled", False)
        and getattr(local_auth_config, "allow_any_username", False)
    ):
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "local auth allow_any_username=true accepts arbitrary usernames; "
            "use only for trusted local development"
        )

    thread_store = None
    thread_upload_root: Path | None = None
    thread_workspace_root: Path | None = None
    # mcp_config_state is owned by the mcp_router (created below).
    # Keep a placeholder binding here · it's reassigned to the live
    # router state after ``create_mcp_router`` runs. Any reference
    # made before that wire-up sees the empty placeholder.
    _stack_mcp_servers: Any = None  # seeded from stack.config below
    # Custom-model registry. Each entry maps a user-chosen model id (shown
    # dispatch to it at chat time. Persisted to disk so restarts keep it.
    if stack is not None:
        # Keep the execution stack on the same streaming journal the API layer
        # subscribes to, so runtime observers see live task events.
        if getattr(stack, "journal", None) is not state.journal:
            stack.journal = state.journal
        if getattr(getattr(stack, "executor", None), "journal", None) is not state.journal:
            stack.executor.journal = state.journal
        if getattr(getattr(stack, "runtime", None), "journal", None) is not state.journal:
            stack.runtime.journal = state.journal

        try:
            from runtime.core.cerebrum.pause_control import get_pause_controller
            _recovered = get_pause_controller().recover_from_journal(state.journal)
            if _recovered:
                import logging as _logging
                _logging.getLogger(__name__).info(
                    "pause_control: %d stale task(s) recovered from journal",
                    _recovered,
                )
        except (ImportError, AttributeError, TypeError):  # noqa: BLE001
            pass

        # Wire the feature-flag registry to the on-disk override
        # file. Subsequent ``feature_flags.is_on(...)`` calls will
        # honor edits to ``data/feature_flags.json`` after a
        # ``POST /api/feature-flags/reload`` (or process restart).
        try:
            from runtime.platform import feature_flags as _ff
            from runtime.platform.process.paths import app_paths as _app_paths
            _ff.configure(_app_paths().feature_flags_path)
        except (ImportError, AttributeError, TypeError, OSError):  # noqa: BLE001
            pass

        try:
            from runtime.platform import feature_flags as _ff
            from runtime.safety.recovery.scheduler import (
                SchedulerConfig,
                get_scheduler,
            )
            cfg = SchedulerConfig(
                interval_sec=max(60, int(_ff.value("regeneration.interval_sec", 600))),
                initial_delay_sec=30,
                enabled=_ff.is_on("regeneration.enabled"),
            )
            get_scheduler().start(stack, config=cfg)
        except Exception as exc:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "regeneration scheduler failed to start: %s", exc,
            )

        # ─── Camouflage scheduler · LLM-driven prompt evolution (opt-in) ──
        try:
            from runtime.platform import feature_flags as _ff
            from runtime.safety.experiments.scheduler import (
                CamouflageConfig,
                get_camouflage_scheduler,
            )
            cam_cfg = CamouflageConfig(
                enabled=_ff.is_on("camouflage.enabled"),
                interval_sec=max(60, int(_ff.value("camouflage.interval_sec", 600))),
                initial_delay_sec=60,
            )
            get_camouflage_scheduler().start(stack, config=cam_cfg)
        except Exception as exc:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "camouflage scheduler failed to start: %s", exc,
            )

        # ─── Ephemeral subagent runner · chat/code mode ───
        # Ephemeral roles (reviewer / researcher / debugger /
        # architect / security-review / explorer) are isolated
        # subagent turns, not SkillRegistry entries. This runner
        # composes a system prompt from role persona + caller
        # conversation + caller memory and runs one LLM turn.
        try:
            if getattr(stack, "is_llm_planner", False):
                router = getattr(stack.planner, "router", None)
                default_model = getattr(stack.planner, "planner_model", None)
                if router is not None:
                    from runtime.execution.suckers.ephemeral_agents import (
                        set_ephemeral_role_runner,
                    )
                    from runtime.execution.suckers.ephemeral_runner import (
                        make_llm_ephemeral_runner,
                    )
                    # Pass `registry` so ephemeral sub-agents get a
                    # mini agentic tool loop (web_search / bb_write /
                    # read_file etc.) instead of being single-shot
                    # opinion boxes. Required for the parallel-swarm
                    # pattern to actually work — siblings need
                    # `bb_write` to drop findings on the shared
                    # blackboard for lead to synthesize.
                    set_ephemeral_role_runner(
                        make_llm_ephemeral_runner(
                            router,
                            registry=stack.executor.registry,
                            default_model=default_model,
                        ),
                    )
                    # Wire the same router into the deep evolution
                    # module so `deep_reflect` and `deep_evolve`
                    # skills can fire LLM judgments. Same router
                    # works · these are LLM-as-judge calls so any
                    # provider that supports basic chat is fine.
                    try:
                        from runtime.memory.learning.deep_evolution import (
                            set_evolve_router,
                        )
                        set_evolve_router(
                            router, default_model=default_model,
                        )
                    except (ImportError, AttributeError, TypeError):  # noqa: BLE001
                        pass  # deep_reflect / deep_evolve will return clean error
                    # ─── Evolution auto-trigger · fitness-driven self-evolution ──
                    try:
                        from runtime.safety.evolution.auto_trigger import (
                            AutoTriggerConfig,
                            get_auto_trigger,
                        )
                        get_auto_trigger().start(
                            stack,
                            AutoTriggerConfig(enabled=True),
                        )
                    except Exception as _at_exc:
                        import logging as _logging
                        _logging.getLogger(__name__).debug(
                            "evolution auto-trigger not started: %s", _at_exc,
                        )
                    # Same router for the Kimi-style skill library
                    # (learn_skill_from_text / apply_skill).
                    try:
                        from runtime.memory.skills_lib.skill_library import (
                            set_skill_router,
                        )
                        set_skill_router(
                            router, default_model=default_model,
                        )
                    except (ImportError, AttributeError, TypeError):  # noqa: BLE001
                        pass  # skills will return clean "router not wired" error
        except (ImportError, AttributeError, TypeError, OSError) as exc:  # noqa: BLE001
            # Non-fatal · sub-agent delegation stays in
            # "not configured" state · rest of app boots normally.
            import logging
            logging.getLogger("runtime.platform.ui.app").warning(
                "ephemeral-role runner wiring failed (%s: %s) · "
                "ephemeral subagent roles will return not-configured",
                type(exc).__name__, exc,
            )

        # ─── Subagent registry · .claude/agents/*.md ───
        # Project-scoped definitions in
        # ``<project>/.claude/agents/*.md`` and user-scoped in
        # ``~/.claude/agents/*.md``. Each markdown file is one
        # subagent (frontmatter `name` / `description` / `tools` /
        # `model` + body = system prompt). Loaded ONCE at boot ·
        # editing a file requires a restart for now (hot reload is
        # a future improvement).
        try:
            from runtime.execution.subagents import (
                load_subagent_registry,
                set_subagent_registry,
            )
            _sa_registry = load_subagent_registry(
                project_root=_project_root_path,
            )
            set_subagent_registry(_sa_registry)
            if _sa_registry.all_names():
                import logging
                logging.getLogger("runtime.platform.ui.app").info(
                    "loaded %d subagent definition(s) from .claude/agents/: %s",
                    len(_sa_registry.all_names()),
                    ", ".join(_sa_registry.all_names()),
                )
            # Re-register the `call_agent` skill so its description
            # reflects the freshly-loaded user definitions. Without
            # this the catalog would only enumerate the hardcoded
            # BUILTIN_ROLES (call_agent gets registered earlier when
            # the SkillRegistry was first built · before this loader
            # had a chance to run).
            try:
                from runtime.execution.suckers.delegation_skills import (
                    register_delegation_skills,
                )
                register_delegation_skills(stack.executor.registry)
            except Exception as exc:  # noqa: BLE001
                import logging
                logging.getLogger("runtime.platform.ui.app").warning(
                    "subagent delegation skill refresh failed (%s: %s) · "
                    "continuing with the existing registry",
                    type(exc).__name__, exc,
                )
        except (ImportError, AttributeError, TypeError, OSError) as exc:  # noqa: BLE001
            import logging
            logging.getLogger("runtime.platform.ui.app").warning(
                "subagent registry load failed (%s: %s) · "
                "user-defined subagents will be unavailable",
                type(exc).__name__, exc,
            )
        from runtime.memory.threads import ThreadStateStore

        inferred_thread_store_path: Path | None = None
        journal_backing_path = getattr(state.journal, "_path", None)
        if isinstance(journal_backing_path, Path):
            inferred_thread_store_path = journal_backing_path.with_name("threads.jsonl")
            thread_upload_root = journal_backing_path.with_name("thread_uploads")
            thread_workspace_root = journal_backing_path.with_name("workspaces")
        elif isinstance(journal_path, Path):
            inferred_thread_store_path = journal_path.with_name("threads.jsonl")
            thread_upload_root = journal_path.with_name("thread_uploads")
            thread_workspace_root = journal_path.with_name("workspaces")
        else:
            inferred_thread_store_path = _paths.threads_path
            thread_upload_root = _paths.data_dir / "thread_uploads"
            thread_workspace_root = _paths.data_dir / "workspaces"
        # Per-agent thread routing: each thread's history lives under
        # ``agents/<agent_id>/sessions/<thread_id>.jsonl``. Repo root =
        # parent of data/ (same dir as `agents/`, `runtime/`).
        _per_agent_base: Path | None = None
        if isinstance(inferred_thread_store_path, Path):
            _per_agent_base = inferred_thread_store_path.parent.parent
        thread_store = ThreadStateStore(
            per_agent_base=_per_agent_base,
        )
        app.state.thread_store = thread_store
        # Defer: feed stack.config.mcp_servers into the mcp_router
        # factory so the router owns the initial-seed logic instead
        # of doing it twice (once here, once inside the factory).
        _stack_mcp_servers = getattr(
            getattr(stack, "config", None), "mcp_servers", None,
        )

    # Claude-style subagents · independent from SkillRegistry.
    if subagent_registry is None:
        try:
            from runtime.execution.subagents import load_subagent_registry
            subagent_registry = load_subagent_registry(project_root=_project_root_path)
        except (ImportError, AttributeError, TypeError, OSError):  # noqa: BLE001
            subagent_registry = None
    try:
        from runtime.execution.subagents import set_subagent_registry
        set_subagent_registry(subagent_registry)
    except (ImportError, AttributeError, TypeError):  # noqa: BLE001
        pass

    # Health and capability probes.
    from runtime.platform.ui.health_router import create_health_router

    app.include_router(create_health_router(
        state=state,
        agent_registry=agent_registry,
        channel_manager=channel_manager,
        group_registry=group_registry,
    ))

    # Prometheus /metrics scrape endpoint (Round 24 wiring).
    # The metrics registry is the process-wide shared singleton from
    # runtime.platform.observability.metrics so every emitter (Beak skill telemetry,
    # health probes, …) lands here automatically.
    try:
        from runtime.sensing.gateway.metrics_router import create_metrics_router
        app.include_router(create_metrics_router())
    except (ImportError, AttributeError, TypeError):  # noqa: BLE001
        # Metrics module is optional · proceed without /metrics rather
        # than refuse to boot.
        pass

    # K8s liveness / readiness probes (Round 24 wiring).
    # ``/livez`` and ``/readyz`` follow the K8s convention so a
    # standard StatefulSet manifest works out-of-the-box. We seed
    # the registry with a minimal "process is alive" liveness check
    # plus a journal-readability readiness check so the pod doesn't
    # advertise itself before genome storage is reachable.
    try:
        from runtime.platform.observability.health import (
            HealthCheck,
            HealthRegistry,
            create_probe_router,
            journal_check,
        )
        from runtime.platform.observability.metrics import get_registry as _mreg

        _hreg = HealthRegistry(metrics_registry=_mreg())
        _hreg.register(HealthCheck(
            name="process",
            check=lambda: True,
            kind="liveness",
        ))
        if state.journal is not None:
            _hreg.register(journal_check(state.journal))
        app.include_router(create_probe_router(_hreg))
        # Stash on app.state so test clients / operators can probe it
        # programmatically and so other routers can register their
        # own checks (e.g. redis_check at startup).
        app.state.health_registry = _hreg
    except (ImportError, AttributeError, TypeError, OSError):  # noqa: BLE001
        pass

    if cocoloop_install_dir is not None:  # noqa: F841 — parameter kept for back-compat
        # The cocoloop external skill marketplace has been removed.
        # The parameter is preserved so existing callers (cli_serve.py
        # and downstream) still pass it, but no router is mounted.
        pass

    # Auto-load agents if registry was not provided (e.g. cli.py failed
    # to build one due to missing runtime deps). This ensures /api/agents
    # is always available so the frontend agent picker works.
    if agent_registry is None:
        try:
            from runtime.execution.agents.base import AgentRegistry
            from runtime.execution.agents.loader import load_all_agents
            agent_registry = AgentRegistry()
            _runtime = stack.runtime if stack is not None else None
            for agent in load_all_agents(_runtime):
                try:
                    agent_registry.register(agent)
                except (TypeError, ValueError, KeyError):
                    continue
            # Also load admin explicitly (excluded from load_all_agents)
            try:
                from runtime.execution.agents.presets import make_admin_agent
                if _runtime is not None:
                    agent_registry.register(make_admin_agent(_runtime))
            except (ImportError, AttributeError, TypeError, ValueError):  # noqa: BLE001 — optional agent preset; skip if unavailable
                pass
        except (ImportError, AttributeError, TypeError, OSError):  # noqa: BLE001 — optional agent preset group; skip if unavailable
            pass

    if agent_registry is not None:
        from runtime.sensing.gateway.agents_router import create_agents_router
        app.include_router(create_agents_router(
            registry=agent_registry,
            identity_store=cocoloop_identity_store,
            require_auth=cocoloop_require_auth,
            journal=state.journal,         # /api/conversations/*
            group_registry=group_registry,  # /api/groups/*
            runtime=stack.runtime if stack is not None else None,  # /api/agents/{id}/reload
        ))

        # Filesystem watcher · auto-reload agents on disk edits.
        # Saves a SOUL.md → watchdog fires → registry.replace() → next
        # turn uses the new persona. Manual POST /api/agents/<id>/reload
        # still works even without watchdog installed.
        if stack is not None:
            try:
                from runtime.execution.agents.loader import default_agents_root
                from runtime.execution.agents.watcher import start_agent_watcher
                start_agent_watcher(
                    agents_root=default_agents_root(),
                    registry=agent_registry,
                    runtime=stack.runtime,
                )
            except (ImportError, AttributeError, TypeError, OSError) as exc:  # noqa: BLE001
                import logging as _lg
                _lg.getLogger(__name__).warning(
                    "agent watcher failed to start (%s) · manual reload still works",
                    exc,
                )

    from runtime.sensing.gateway.team_rooms_router import create_team_rooms_router
    team_rooms_router = create_team_rooms_router(
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
        jwt_secret=molili_jwt_secret,
        reset_callback=getattr(getattr(app.state, "thread_store", None), "clear", None),
    )
    app.state.team_rooms_router = team_rooms_router
    app.include_router(team_rooms_router)

    # Team tasks: persistent task units inside team rooms (HACO M0).
    # Same auth knobs as team_rooms_router so a single actor flows through.
    from runtime.sensing.gateway.team_tasks_router import create_team_tasks_router

    _broadcast_room = getattr(team_rooms_router, "broadcast", None)
    _resolve_room_members = getattr(team_rooms_router, "list_room_members", None)

    async def _team_event_broadcaster(room_id: str, payload: dict[str, Any]) -> None:
        sync = getattr(
            getattr(app.state, "company_router", None),
            "sync_team_task_event",
            None,
        )
        if callable(sync):
            try:
                sync(payload)
            except Exception:  # noqa: BLE001
                import logging as _lg

                _lg.getLogger(__name__).warning(
                    "company task sync failed for team event",
                    exc_info=True,
                )
        # _broadcast has a kw-only ``exclude`` arg; the team-tasks router
        # uses a 2-arg shape, so we adapt here rather than leaking the
        # rooms-router signature through the tasks router contract.
        if _broadcast_room is None:
            return
        await _broadcast_room(room_id, payload)

    team_tasks_router = create_team_tasks_router(
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
        jwt_secret=molili_jwt_secret,
        team_event_broadcaster=(
            _team_event_broadcaster if _broadcast_room is not None else None
        ),
        room_membership_resolver=_resolve_room_members,
    )
    app.state.team_tasks_router = team_tasks_router
    app.include_router(team_tasks_router)

    # Company Workbench: long-running project planning domain. This sits
    # above team_tasks so milestones/Gantt data can evolve without
    # disturbing existing team-room task execution.
    from runtime.company.api import create_company_router

    async def _company_team_task_dispatcher(
        request: Any,
        payload: dict[str, Any],
        run: bool = False,
    ) -> dict[str, Any]:
        creator = getattr(team_tasks_router, "create_task_from_payload", None)
        if creator is None:
            raise RuntimeError("team task creator is not available")
        team_task = await creator(request, payload)
        if run:
            runner = getattr(team_tasks_router, "run_task_from_request", None)
            if runner is None:
                raise RuntimeError("team task runner is not available")
            team_task = await runner(request, str(team_task["id"]))
        return team_task

    async def _company_team_room_creator(
        request: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        creator = getattr(team_rooms_router, "create_team_from_payload", None)
        if creator is None:
            raise RuntimeError("team room creator is not available")
        return creator(request, payload)

    async def _company_team_room_updater(
        request: Any,
        team_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        updater = getattr(team_rooms_router, "update_team_from_payload", None)
        if updater is None:
            raise RuntimeError("team room updater is not available")
        return await updater(request, team_id, payload)

    company_router = create_company_router(
        team_task_dispatcher=_company_team_task_dispatcher,
        team_room_creator=_company_team_room_creator,
        team_room_updater=_company_team_room_updater,
        agent_registry=agent_registry,
        runtime=stack.runtime if stack is not None else None,
    )
    app.state.company_router = company_router
    app.include_router(company_router)

    if parallel_agent_orchestrator is None:
        from runtime.execution.parallel_agents import ParallelAgentOrchestrator
        parallel_agent_orchestrator = ParallelAgentOrchestrator(
            max_concurrency=4,
        )
    from runtime.sensing.gateway.parallel_agents_router import create_parallel_agents_router
    app.include_router(create_parallel_agents_router(
        orchestrator=parallel_agent_orchestrator,
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
        jwt_secret=molili_jwt_secret,
    ))
    app.state.parallel_agent_orchestrator = parallel_agent_orchestrator

    from runtime.sensing.gateway.deep_research_router import (
        create_deep_research_router,
    )
    app.include_router(create_deep_research_router(
        orchestrator=parallel_agent_orchestrator,
        workspace_root=thread_workspace_root,
        upload_root=thread_upload_root,
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
        jwt_secret=molili_jwt_secret,
    ))

    from runtime.sensing.gateway.subagents_router import create_subagents_router
    app.include_router(create_subagents_router(
        registry=subagent_registry,
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
        jwt_secret=molili_jwt_secret,
    ))
    app.state.subagent_registry = subagent_registry

    # ─── Auto wiki · serves docs/auto/ tree to the Wiki tab ───
    # Generated by ``scripts/gen_wiki.py`` · the router only reads
    # and optionally triggers regeneration · no LLM involved.
    from runtime.sensing.gateway.wiki_router import create_wiki_router
    app.include_router(create_wiki_router())

    # ─── Persistent terminal WebSocket ─────────
    from runtime.sensing.gateway.terminal_router import mount_terminal_routes
    mount_terminal_routes(
        app,
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
        jwt_secret=molili_jwt_secret,
    )

    # ─── IM Channels webhook / dashboard APIs ─────────
    # sessions where the full stack-backed ChannelManager was not created.
    # In that case a small local manager lets the router expose the supported
    # platform list and credential state instead of returning 404.
    from runtime.sensing.gateway.channels_router import create_channels_router
    app.include_router(create_channels_router(
        manager=channel_manager,
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
    ))

    # ─── SpinalCord reflex layer · shared by both gateways ──────
    # Built once and threaded into both /v1/chat/completions AND the
    # Earlier this was only wired into the OpenAI-compat router, AND
    # even that wiring was missing the actual instance · so reflex
    # never fired in production. Building it here makes it a single
    # injection point for future rule additions.
    from runtime.platform.ui.thread_routes import (
        build_reflex_router,
        mount_thread_state_routes,
    )

    _reflex_router = build_reflex_router(stack)
    _realtime_logs_root = _paths.data_dir / "threads"

    # Thread state CRUD for sidebars and scope settings. Live turns use
    # the realtime WebSocket mounted below.
    mount_thread_state_routes(
        app,
        thread_store=thread_store,
        logs_root=_realtime_logs_root,
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
        jwt_secret=molili_jwt_secret,
    )

    if stack is not None:
        from runtime.sensing.gateway.openai_gateway_router import create_openai_router

        app.include_router(create_openai_router(
            stack,
            default_arm=default_arm,
            identity_store=cocoloop_identity_store,
            require_auth=cocoloop_require_auth,
            jwt_secret=molili_jwt_secret,
            agent_registry=agent_registry,
            reflex_router=_reflex_router,
        ))

    # Reflex admin endpoints: stats, hot-reload, gene-locks, and forge APIs.
    from runtime.platform.ui.reflex_admin_router import mount_reflex_admin_routes

    mount_reflex_admin_routes(
        app,
        stack=stack,
        reflex_router=_reflex_router,
        panel_html=_REFLEX_PANEL_HTML,
        editor_html=_REFLEX_EDITOR_HTML,
    )

    if molili_config is not None and getattr(molili_config, "enabled", False):
        from runtime.adapters.integrations.molili import (
            MoliliLinkStore,
            create_molili_routers,
        )

        effective_link_store = molili_link_store or MoliliLinkStore()
        _attach_molili_fallback_router(
            stack=stack,
            molili_config=molili_config,
            link_store=effective_link_store,
        )

        auth_router, account_router, proxy_router = create_molili_routers(
            config=molili_config,
            link_store=effective_link_store,
            identity_store=cocoloop_identity_store,
            require_auth=cocoloop_require_auth,
            jwt_secret=molili_jwt_secret,
        )
        app.include_router(auth_router)
        app.include_router(account_router)
        app.include_router(proxy_router)

    if local_auth_config is not None and getattr(
        local_auth_config, "enabled", False,
    ):
        from runtime.adapters.integrations.local_auth import create_local_auth_router

        app.include_router(create_local_auth_router(
            config=local_auth_config,
            identity_store=cocoloop_identity_store,
        ))

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    from .chat_page import get_chat_html

    @app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/chat.html", response_class=HTMLResponse, include_in_schema=False)
    def chat_page() -> str:
        return get_chat_html()

    #
    #
    _webui_dist = _find_webui_dist()
    if _webui_dist is not None:
        _mount_webui(app, _webui_dist)

    # ─── Meta router · feedback + skills + auth providers ──
    # These three endpoint groups used to live inline here. Extracted
    # to runtime/sensing/siphon/meta_router.py mid-2026 · same split
    # pattern as config_router. Keeps app.py under 2000 lines and
    # makes the meta surface independently testable.
    from runtime.execution.arms.tool_registry import get_tool_registry
    from runtime.sensing.gateway.meta_router import create_meta_router

    app.include_router(create_meta_router(
        registry=state.registry,
        tool_registry=get_tool_registry(),
        skill_library_dirs=[_project_root_path / "skills" / "public"],
        include_default_skill_library=(registry is None or stack is not None),
        molili_config=molili_config,
        local_auth_config=local_auth_config,
        identity_store=cocoloop_identity_store,
        molili_jwt_secret=molili_jwt_secret,
    ))

    # ─── MCP router · declare/enable/disable MCP servers ─────────
    # The entire 220-line block of helpers + endpoints that used to
    # live here (preset dict, _resolve_molili_bridge_env,
    # _register_runtime_mcp, _unregister_runtime_mcp, GET+PUT
    # /api/mcp/config) is now ``runtime/sensing/siphon/mcp_router.py``.
    # The returned bundle carries the live state dicts so future
    # health-endpoint or admin-dashboard code can introspect what's
    # registered without re-doing the spawn bookkeeping.
    from runtime.sensing.gateway.mcp_router import create_mcp_router

    _mcp_bundle = create_mcp_router(
        registry=state.registry,
        initial_mcp_servers=_stack_mcp_servers,
    )
    app.include_router(_mcp_bundle.router)

    # ─── Config router · identity-lock + providers + custom-models ─
    # These endpoints used to live inline here (~260 lines of nested
    # factories + handler defs). Extracted to
    # runtime/sensing/siphon/config_router.py to shrink this file
    # below the "nobody wants to open this" threshold.
    #
    # The wrapper's ``.custom_models`` attribute is a live reference
    # to the in-memory state the router maintains — app.py's
    # /api/llm-models merge endpoint below reads it directly rather
    # than duplicating persistence logic.
    from runtime.sensing.gateway.config_router import create_config_router

    _config_bundle = create_config_router(stack=stack)
    app.include_router(_config_bundle.router)

    # ``/api/llm-models`` (merged molili presets + custom models)
    # moved into config_router.py · it's registered via the
    # ``_config_bundle.router`` include above · FastAPI picks it
    # before the openai_gateway's /api/llm-models because the
    # config router mounts earlier.

    from runtime.sensing.gateway.system_router import create_system_router

    def _reset_runtime_memory() -> None:
        clear_threads = getattr(thread_store, "clear", None)
        if callable(clear_threads):
            clear_threads()
        clear_teams = getattr(team_rooms_router, "reset_state", None)
        if callable(clear_teams):
            clear_teams()

    app.include_router(create_system_router(
        project_root=_project_root_path,
        identity_store=cocoloop_identity_store,
        memory_reset_callback=_reset_runtime_memory,
        require_auth=cocoloop_require_auth,
        jwt_secret=molili_jwt_secret,
        jwt_issuer=getattr(molili_config, "jwt_issuer", None) if molili_config else None,
        jwt_audience=getattr(molili_config, "jwt_audience", None) if molili_config else None,
    ))

    # Browser session and relay APIs.
    from runtime.platform.ui.browser_router import create_browser_router

    app.include_router(create_browser_router())

    # ─── FS router · extracted to fs_router.py ─────────
    # The 3 endpoints + 2 helpers that used to live here inline now
    # sit in runtime/sensing/siphon/fs_router.py. Same contract ·
    # no auth / no scope (these serve the UI file-browser which
    # opens user-chosen directories) · test coverage in
    # tests/test_app_fs_endpoints.py.
    from runtime.sensing.gateway.fs_router import create_fs_router

    app.include_router(create_fs_router(
        thread_store=thread_store,
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
        jwt_secret=molili_jwt_secret,
    ))

    try:
        from runtime.platform.process.paths import app_paths as _workspace_app_paths
        from runtime.sensing.gateway.workspaces_router import create_workspaces_router

        app.include_router(create_workspaces_router(
            workspace_root=_workspace_app_paths().data_dir / "workspaces",
        ))
    except Exception as _workspace_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "workspaces router failed to mount: %s", _workspace_exc,
        )

    from runtime.sensing.gateway.lsp_router import create_lsp_router

    app.include_router(create_lsp_router(state.registry))

    from runtime.sensing.gateway.verify_router import create_verify_router

    app.include_router(create_verify_router())

    from runtime.sensing.gateway.deployments_router import create_deployments_router

    app.include_router(create_deployments_router())

    from runtime.sensing.gateway.debug_router import create_debug_router

    app.include_router(create_debug_router(
        store=thread_store,
        stack=stack,
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
        jwt_secret=molili_jwt_secret,
        jwt_issuer=getattr(molili_config, "jwt_issuer", None) if molili_config else None,
        jwt_audience=getattr(molili_config, "jwt_audience", None) if molili_config else None,
    ))

    from runtime.sensing.gateway.index_router import create_index_router

    app.include_router(create_index_router())

    from runtime.sensing.gateway.computer_router import create_computer_router

    app.include_router(create_computer_router())

    from runtime.sensing.gateway.completion_router import create_completion_router

    app.include_router(create_completion_router())

    from runtime.execution.misc.parallel_runner import create_parallel_task_router

    app.include_router(create_parallel_task_router())

    # ─── Uploads/artifacts router · extracted to uploads_router.py
    # The 4 endpoints + 2 helpers that used to live here inline now
    # sit in runtime/sensing/siphon/uploads_router.py. The factory
    # accepts ``thread_store=None`` · in that case every endpoint
    # returns 503 (preserves the demo-app-without-a-stack contract).
    from runtime.sensing.gateway.uploads_router import create_uploads_router

    app.include_router(create_uploads_router(
        thread_store=thread_store,
        workspace_root=thread_workspace_root,
        legacy_upload_root=thread_upload_root,
    ))

    # ─── Observability router · extracted to observability_router.py
    # The 6 endpoints (journal / reflect / kg / progress / stream /
    # run) that used to live inline now sit in
    # runtime/sensing/siphon/observability_router.py. Same wire
    # contract · preserves progress_tracker-as-singleton semantics
    # (one tracker per app, incremental O(N_tasks) snapshots).
    from runtime.sensing.gateway.observability_router import (
        create_observability_router,
    )

    app.include_router(create_observability_router(
        journal=state.journal,
        registry=state.registry,
        planner=getattr(stack, "planner", None) if stack is not None else None,
    ))

    # Local account usage is derived from the same journal cost events that
    # power /api/budget/summary. Mount before the broad compatibility stub.
    from runtime.sensing.gateway.account_usage_router import (
        create_account_usage_router,
    )

    app.include_router(create_account_usage_router(journal=state.journal))

    # Evolution operator-console fallback routes. The real Reflex/RecipeForge
    # admin router registers many of these paths earlier when available; this
    # router gives lightweight backend runs explicit empty/disabled snapshots
    # instead of frontend-visible 404s.
    from runtime.sensing.gateway.evolution_ops_router import (
        create_evolution_ops_router,
    )

    app.include_router(create_evolution_ops_router(
        journal=state.journal,
        registry=state.registry,
        planner=getattr(stack, "planner", None) if stack is not None else None,
        forged_skill_dir=Path("data/forged_skills"),
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
        jwt_secret=molili_jwt_secret,
        jwt_issuer=getattr(molili_config, "jwt_issuer", None) if molili_config else None,
        jwt_audience=getattr(molili_config, "jwt_audience", None) if molili_config else None,
    ))

    try:
        from runtime.sensing.gateway.dag_debugger_router import (
            create_dag_debugger_router,
        )
        app.include_router(create_dag_debugger_router(
            journal=state.journal,
            planner=getattr(stack, "planner", None) if stack is not None else None,
        ))
    except Exception as _dag_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning("dag_debugger_router failed to mount: %s", _dag_exc)

    # ─── Skill Marketplace Web API ──────────────
    try:
        from runtime.sensing.gateway.skill_market_router import (
            create_skill_market_router,
        )
        app.include_router(create_skill_market_router())
    except Exception as _sm_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning("skill_market_router failed to mount: %s", _sm_exc)

    # ─── MetaSkill (能力包) Web API ──────────────
    try:
        from runtime.sensing.gateway.meta_skill_router import (
            create_meta_skill_router,
        )
        app.include_router(create_meta_skill_router())
    except Exception as _ms_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning("meta_skill_router failed to mount: %s", _ms_exc)

    try:
        from runtime.sensing.gateway.apps_router import create_apps_router
        app.include_router(create_apps_router())
    except Exception as _apps_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning("apps_router failed to mount: %s", _apps_exc)

    # ─── Agent Market Web API ──────────────
    try:
        from runtime.sensing.gateway.agent_world_router import (
            create_agent_world_router,
        )
        app.include_router(create_agent_world_router(
            registry=agent_registry,
            runtime=stack.runtime if stack is not None else None,
            skill_registry=state.registry,
        ))
    except Exception as _aw_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning("agent_world_router failed to mount: %s", _aw_exc)

    # ─── Intelligence Web API ──────────────────────────────────────────────
    try:
        from runtime.sensing.gateway.intelligence_router import (
            create_intelligence_router,
        )
        app.include_router(create_intelligence_router())
    except Exception as _intel_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning("intelligence_router failed to mount: %s", _intel_exc)

    # ─── Ambient Suggestions (feature-flag gated) ──────────────────────────
    # Entirely surface-level; if the router fails to mount, the rest
    # of the app is fine.
    try:
        from runtime.sensing.gateway.ambient_suggestions_router import (
            create_ambient_suggestions_router,
        )
        app.include_router(create_ambient_suggestions_router())
    except Exception as _amb_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "ambient_suggestions_router failed to mount: %s", _amb_exc,
        )

    # ─── Remote backends (feature-flag gated) ──────────────────────────────
    try:
        from runtime.sensing.gateway.remote_backends_router import (
            create_remote_backends_router,
        )
        app.include_router(create_remote_backends_router())
    except Exception as _rb_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "remote_backends_router failed to mount: %s", _rb_exc,
        )

    # ─── Prompts hot-reload (feature-flag gated) ───────────────────────────
    # Lives at <data>/prompt_templates/ to stay out of the way of the
    # YAML-backed PromptLoader at <repo>/prompts/. New Markdown-based
    # editable templates land here; legacy callers keep using the
    # original loader.
    try:
        from runtime.platform.process.paths import app_paths as _app_paths_p
        from runtime.platform.prompts.registry import PromptRegistry
        from runtime.platform.prompts.seed import seed_if_empty
        from runtime.sensing.gateway.prompts_router import (
            create_prompts_router,
        )
        _prompts_dir = _app_paths_p().data_dir / "prompt_templates"
        _prompt_registry = PromptRegistry(_prompts_dir)
        # Auto-install the default templates the first time the
        # server boots against an empty directory. Safe to call on
        # every boot — it's a no-op once any .md exists.
        with contextlib.suppress(Exception):
            seed_if_empty(_prompt_registry)
        app.include_router(create_prompts_router(_prompt_registry))
    except Exception as _pr_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "prompts_router failed to mount: %s", _pr_exc,
        )

    # ─── Ambient suggestions scheduler (feature-flag gated) ───────────────
    # Periodic LLM-backed regeneration. No-op when flag is off;
    # honors interval from ``ui.ambient_suggestions_interval_sec``.
    try:
        from runtime.memory.skills_lib.ambient_suggestions_scheduler import (
            get_ambient_scheduler,
        )
        get_ambient_scheduler().start()
    except Exception as _ambs_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning(
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
        app.include_router(create_invariants_router())
    except Exception as _inv_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "invariants_router failed to mount: %s", _inv_exc,
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
            create_journal_router(default_jsonl_path=_journal_jsonl),
        )
    except Exception as _jr_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "journal_router failed to mount: %s", _jr_exc,
        )

    # ─── Cron settings compatibility API ───────────────────────────────
    # Local memory compatibility API. Keep it before the broad stub router so
    # manual memory editing/search/export is backed by the real local store.
    try:
        from runtime.sensing.gateway.agent_trace_router import (
            create_agent_trace_router,
        )

        app.include_router(create_agent_trace_router(
            store=getattr(state, "trace_store", None),
            db_path=trace_store_path,
        ))
    except Exception as _trace_exc:  # noqa: BLE001
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "agent_trace_router failed to mount: %s", _trace_exc,
        )

    from runtime.sensing.gateway.memory_router import create_memory_router
    app.include_router(create_memory_router())

    # Cron settings compatibility API. Keep it before the broad stub router
    # so /api/cron/* is backed by the real local settings store.
    from runtime.sensing.gateway.cron_router import create_cron_router
    app.include_router(create_cron_router(
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
        jwt_secret=molili_jwt_secret,
        jwt_issuer=getattr(molili_config, "jwt_issuer", None) if molili_config else None,
        jwt_audience=getattr(molili_config, "jwt_audience", None) if molili_config else None,
    ))

    # Organization-level evolution endpoints (team topologies).
    # Fail-soft mount: a missing organization package or a route
    # collision degrades to a warning, never breaks the whole app.
    try:
        from runtime.sensing.gateway.organizations_router import (
            create_organizations_router,
        )

        app.include_router(create_organizations_router(
            identity_store=cocoloop_identity_store,
            require_auth=cocoloop_require_auth,
            jwt_secret=molili_jwt_secret,
            jwt_issuer=getattr(molili_config, "jwt_issuer", None) if molili_config else None,
            jwt_audience=getattr(molili_config, "jwt_audience", None) if molili_config else None,
            agent_registry=agent_registry,
        ))
    except Exception as _org_exc:  # noqa: BLE001
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "organizations router failed to mount: %s", _org_exc,
        )

    # Realtime WebSocket gateway — JSON-RPC 2.0 + item-oriented protocol.
    # The backing runtime depends on what's wired into this process:
    # CerebrumRuntime when ``stack`` is available (production), and
    # EchoRuntime when running headless (minimal demos, unit tests,
    # ``python -m runtime ui`` with no planner). The wire contract is
    # the same either way — clients never branch on which is live.
    try:
        # Per-thread JSONL logs live under the same data root as every
        # other persisted runtime file, so an ``OCTOPUS_HOME`` or
        # ``OCTOPUS_DATA_DIR`` override relocates them together. Falls
        # back to ``./data/threads`` when no override is set.
        from runtime.platform.process.paths import app_paths as _rt_app_paths
        from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

        _realtime_logs_root = _rt_app_paths().data_dir / "threads"

        # Whether a client may set approvalPolicy="never" to skip the
        # human approval gate. SECURE default: off unless the operator
        # explicitly enables safety.allow_client_approval_bypass in the
        # loaded config. Was previously hardcoded True, letting any WS
        # client disable approvals.
        _safety_cfg = getattr(getattr(stack, "config", None), "safety", None)
        _allow_approval_bypass = bool(
            getattr(_safety_cfg, "allow_client_approval_bypass", None) or False
        )

        if stack is not None:
            from runtime.memory.threads.compaction import CompactionPolicy
            from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

            # Compaction kicks in once a thread accrues ~24 turns; we
            # summarise down to the last 12. Thresholds tuned so very
            # short sessions never pay the summariser round-trip.
            _compaction_policy = CompactionPolicy(
                trigger_at=24,
                keep_recent=12,
                max_summary_chars=4_000,
            )
            _summary_router = getattr(getattr(stack, "planner", None), "router", None)

            _realtime_runtime: Any = CerebrumRuntime(
                stack=stack,
                agent=None,  # resolved per turn from the registry
                agent_registry=agent_registry,
                logs_root=str(_realtime_logs_root),
                policy_path=_rt_app_paths().permissions_path,
                workspace_root=str(_rt_app_paths().data_dir / "workspaces"),
                compaction_policy=_compaction_policy,
                summary_router=_summary_router,
                thread_store=thread_store,
                reflex_router=_reflex_router,
                trace_store=getattr(state, "trace_store", None),
                allow_client_auto_approve=_allow_approval_bypass,
            )
        else:
            from runtime.sensing.gateway.realtime_echo import EchoRuntime

            _realtime_runtime = EchoRuntime(logs_root=str(_realtime_logs_root))

        _realtime_gateway = RealtimeGateway(
            runtime=_realtime_runtime,
            identity_store=cocoloop_identity_store,
            require_auth=cocoloop_require_auth,
            jwt_secret=molili_jwt_secret,
            jwt_issuer=getattr(molili_config, "jwt_issuer", None) if molili_config else None,
            jwt_audience=getattr(molili_config, "jwt_audience", None) if molili_config else None,
            allow_client_approval_bypass=_allow_approval_bypass,
        )
        app.include_router(_realtime_gateway.router)
        # Exposed for introspection/tests (e.g. asserting the secure
        # default for client approval bypass).
        app.state.realtime_gateway = _realtime_gateway

        # Static permission policy ("always trust" rules) shares the
        # same JSON file as the realtime gateway uses for filtering, so
        # mount the management router right alongside it.
        from runtime.platform.ui.permissions_router import (
            create_permissions_router,
        )

        app.include_router(create_permissions_router())
    except Exception as _rt_exc:  # noqa: BLE001
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "realtime gateway failed to mount: %s", _rt_exc,
        )

    # ─── Evolution API · fitness / drift / ledger / canary ──────
    try:
        from runtime.sensing.gateway.evolution_router import create_evolution_router
        app.include_router(create_evolution_router())
    except Exception as _evo_exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "evolution router failed to mount: %s", _evo_exc,
        )

    # Codex-compatible plugin catalog. Keep it before the broad stub router
    # so the frontend /plugins page shows copied .codex-plugin manifests.
    try:
        from runtime.sensing.gateway.plugins_router import create_plugins_router
        app.include_router(create_plugins_router())
    except Exception as _plugins_exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "plugins router failed to mount: %s", _plugins_exc,
        )

    # ─── PluginHub (pluggable module architecture) ────────────────
    # Auto-discovers and loads plugins from ~/.octopus/plugins/.
    # Each plugin can register skills, channels, routes, and a
    # frontend config UI via plugin.yaml + ModulePlugin subclass.
    try:
        from runtime.platform.plugins.plugin_hub import PluginHub
        from runtime.sensing.gateway.plugin_hub_router import (
            create_plugin_hub_router,
        )

        _hub = PluginHub(
            skill_registry=state.registry,
            channel_manager=channel_manager,
            fastapi_app=app,
        )
        _loaded = _hub.load_all()
        if _loaded:
            import logging as _logging
            _logging.getLogger(__name__).info(
                "PluginHub auto-loaded %d plugins: %s",
                len(_loaded), _loaded,
            )

        app.include_router(create_plugin_hub_router(hub=_hub))
        app.state.plugin_hub = _hub
    except Exception as _hub_exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "PluginHub failed to initialize: %s", _hub_exc,
        )

    from runtime.sensing.gateway.stub_router import create_stub_router
    app.include_router(create_stub_router(
        jwt_secret=molili_jwt_secret,
        jwt_issuer=getattr(molili_config, "jwt_issuer", None) if molili_config else None,
    ))

    # ─── Anthropic Managed Agents compat layer ──────────────
    # Exposes /v1/sessions REST + SSE so the official ``anthropic``
    # SDK (``client.beta.sessions.*``) can connect to octopus-agent
    # as a self-hosted backend. Beta header required:
    #   anthropic-beta: managed-agents-2026-04-01
    try:
        from runtime.sensing.gateway.anthropic_compat import (
            create_anthropic_compat_router,
        )

        # Attach the realtime CerebrumRuntime to the stack so the
        # anthropic compat layer can reuse it without re-instantiating.
        if "_realtime_runtime" in dir() and stack is not None:
            try:  # noqa: SIM105
                stack._realtime_runtime = _realtime_runtime  # noqa: SLF001
            except (AttributeError, TypeError):
                pass

        app.include_router(create_anthropic_compat_router(
            stack=stack,
            identity_store=cocoloop_identity_store,
            require_auth=cocoloop_require_auth,
            jwt_secret=molili_jwt_secret,
            jwt_issuer=getattr(molili_config, "jwt_issuer", None) if molili_config else None,
            jwt_audience=getattr(molili_config, "jwt_audience", None) if molili_config else None,
            agent_registry=agent_registry,
        ))
    except Exception as _anth_exc:  # noqa: BLE001
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "anthropic compat router failed to mount: %s", _anth_exc,
        )

    # ── Octopus OS appliance profile(octopus-os fork)──────────────
    # NAS 桌面启动器的应用注册器;仅 OCTOPUS_APPLIANCE=1 时挂载,
    # 母体行为零变化。实现放在顶层 appliance/ 包,最小化合并面
    # (docs/OCTOPUS_OS_PLAN.md §4)。
    import os as _os

    if _os.environ.get("OCTOPUS_APPLIANCE") == "1":
        from appliance.app_registry.router import create_appliance_router

        app.include_router(create_appliance_router())

    return app
