"""Stack wiring for ``create_app``: schedulers, subagents, thread store, cowork.

Extracted from ``app.py`` during the god-file reduction (§2.1 of the
navigation map). Wires the execution stack onto the shared journal,
starts the optional regeneration / camouflage / auto-trigger
schedulers, registers ephemeral subagent runners, loads the
project-scoped subagent registry, and builds the thread store + cowork
runtime.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from runtime.platform import feature_flags
from runtime.platform.process.paths import app_paths

from ._app_context import AppContext


def _regeneration_scheduler_config() -> Any:
    """Build scheduler config with a cwd-independent runtime data root."""

    from runtime.safety.recovery.scheduler import SchedulerConfig

    return SchedulerConfig(
        interval_sec=max(60, int(feature_flags.value("regeneration.interval_sec", 600))),
        initial_delay_sec=30,
        output_dir=str(app_paths().data_dir),
        enabled=feature_flags.is_on("regeneration.enabled"),
    )


def wire_stack(
    ctx: AppContext,
    *,
    journal_path: Any,
    subagent_registry: Any,
    agent_registry: Any = None,
) -> None:
    """Populate ctx.thread_store / cowork / subagent / mcp-servers."""
    stack = ctx.stack
    state = ctx.state
    _paths = ctx.paths
    app = ctx.app

    thread_store = None
    thread_upload_root: Path | None = None
    thread_workspace_root: Path | None = None
    cowork_runtime = None
    _stack_mcp_servers: Any = None
    if stack is not None:
        # Keep the execution stack on the same streaming journal the API layer
        # subscribes to, so runtime observers see live task events.
        if getattr(stack, "journal", None) is not state.journal:
            stack.journal = state.journal
        if getattr(getattr(stack, "executor", None), "journal", None) is not state.journal:
            stack.executor.journal = state.journal
        if getattr(getattr(stack, "runtime", None), "journal", None) is not state.journal:
            stack.runtime.journal = state.journal
        if hasattr(getattr(stack, "executor", None), "configure_effect_store"):
            stack.executor.configure_effect_store(_paths.tool_effects_path)

        try:
            from runtime.core.cerebrum.pause_control import get_pause_controller

            _recovered = get_pause_controller().recover_from_journal(state.journal)
            if _recovered:
                logging.getLogger(__name__).info(
                    "pause_control: %d stale task(s) recovered from journal",
                    _recovered,
                )
        except (
            ImportError,
            AttributeError,
            TypeError,
        ):  # best-effort · stale-task recovery is optional, startup proceeds either way
            pass

        # Wire the feature-flag registry to the on-disk override
        # file. Subsequent ``feature_flags.is_on(...)`` calls will
        # honor edits to ``data/feature_flags.json`` after a
        # ``POST /api/feature-flags/reload`` (or process restart).
        with contextlib.suppress(ImportError, AttributeError, TypeError, OSError):
            feature_flags.configure(app_paths().feature_flags_path)

        try:
            from runtime.safety.recovery.scheduler import get_scheduler

            cfg = _regeneration_scheduler_config()
            get_scheduler().start(
                stack,
                config=cfg,
                agent_registry=agent_registry,
            )
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "regeneration scheduler failed to start: %s",
                exc,
            )

        # ─── Camouflage scheduler · LLM-driven prompt evolution (opt-in) ──
        try:
            from runtime.safety.experiments.scheduler import (
                CamouflageConfig,
                get_camouflage_scheduler,
            )

            cam_cfg = CamouflageConfig(
                enabled=feature_flags.is_on("camouflage.enabled"),
                interval_sec=max(60, int(feature_flags.value("camouflage.interval_sec", 600))),
                initial_delay_sec=60,
            )
            get_camouflage_scheduler().start(stack, config=cam_cfg)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "camouflage scheduler failed to start: %s",
                exc,
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
                            router,
                            default_model=default_model,
                        )
                    except (
                        ImportError,
                        AttributeError,
                        TypeError,
                    ):  # best-effort · deep_reflect / deep_evolve will return clean error
                        pass
                    # ─── Evolution auto-trigger · fitness-driven self-evolution ──
                    try:
                        from runtime.safety.evolution.auto_trigger import (
                            AutoTriggerConfig,
                            get_auto_trigger,
                        )

                        # Honour the evolution.auto_trigger flag (default True =
                        # current behaviour) instead of a hardcoded enabled=True,
                        # so the registered flag actually controls the trigger.
                        get_auto_trigger().start(
                            stack,
                            AutoTriggerConfig(
                                enabled=feature_flags.is_on("evolution.auto_trigger"),
                            ),
                            agent_registry=agent_registry,
                        )
                    except Exception as _at_exc:
                        logging.getLogger(__name__).debug(
                            "evolution auto-trigger not started: %s",
                            _at_exc,
                        )
                    # Same router for the Kimi-style skill library
                    # (learn_skill_from_text / apply_skill).
                    try:
                        from runtime.memory.skills_lib.skill_library import (
                            set_skill_router,
                        )

                        set_skill_router(
                            router,
                            default_model=default_model,
                        )
                    except (
                        ImportError,
                        AttributeError,
                        TypeError,
                    ):  # best-effort · skills will return clean "router not wired" error
                        pass
                    # web_fetch performs a focused LLM pass over extracted
                    # page text. Share the active planner router, while using
                    # separate provider keys so deployments may later select
                    # a cheaper default model independently.
                    try:
                        from runtime.execution.suckers.web_skills import (
                            set_web_fetch_router,
                        )

                        set_web_fetch_router(
                            router,
                            default_model=default_model,
                        )
                    except (
                        ImportError,
                        AttributeError,
                        TypeError,
                    ):  # best-effort · web_fetch returns a clean error
                        pass
                    # ─── Computer-use vision loop · autonomous desktop ───
                    # register_computer_use_loop needs a VisionPlanner built
                    # from the router, so it can't go through the _CATALOG
                    # group registrars (which take only a registry). Wire it
                    # here, mirroring the other router consumers. The skill is
                    # non-atomic (agents opt in via allowlist, e.g. the
                    # desktop_operator_arm) and pyautogui-gated at exec time;
                    # without this it was registered only by the demo server,
                    # so the arm's ``computer_use_loop`` reference never resolved.
                    try:
                        from runtime.execution.suckers.computer_use_loop import (
                            ModelRouterVisionPlanner,
                            register_computer_use_loop,
                        )
                        from runtime.execution.suckers.desktop_grounding import (
                            combined_grounding,
                        )

                        register_computer_use_loop(
                            stack.executor.registry,
                            ModelRouterVisionPlanner(
                                router=router,
                                model=default_model or "claude-sonnet-4-6",
                                # Best-effort semantic grounding: on-screen
                                # window list + frontmost-app actionable AX
                                # controls (role/label @ center). "" on
                                # non-macOS / no perms — pure-pixel fallback.
                                grounding=combined_grounding,
                            ),
                            journal=stack.journal,
                        )
                    except Exception as _cul_exc:  # noqa: BLE001
                        logging.getLogger(__name__).debug(
                            "computer_use_loop not wired: %s",
                            _cul_exc,
                        )
        except (ImportError, AttributeError, TypeError, OSError) as exc:
            # Non-fatal · sub-agent delegation stays in
            # "not configured" state · rest of app boots normally.
            logging.getLogger("runtime.platform.ui.app").warning(
                "ephemeral-role runner wiring failed (%s: %s) · "
                "ephemeral subagent roles will return not-configured",
                type(exc).__name__,
                exc,
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
                project_root=ctx.project_root,
            )
            set_subagent_registry(_sa_registry)
            if _sa_registry.all_names():
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
                logging.getLogger("runtime.platform.ui.app").warning(
                    "subagent delegation skill refresh failed (%s: %s) · "
                    "continuing with the existing registry",
                    type(exc).__name__,
                    exc,
                )
        except (ImportError, AttributeError, TypeError, OSError) as exc:
            logging.getLogger("runtime.platform.ui.app").warning(
                "subagent registry load failed (%s: %s) · "
                "user-defined subagents will be unavailable",
                type(exc).__name__,
                exc,
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
        # Session-layout feature flags. Defaults (dated_layout off,
        # index_enabled on) match ThreadStateStore's own defaults, so this is
        # behaviour-preserving — it just lets the flags actually reach the
        # store instead of being registered-but-inert.
        try:
            _dated_layout = feature_flags.is_on("sessions.dated_layout")
            _index_enabled = feature_flags.is_on("sessions.index_enabled")
        except Exception:  # noqa: BLE001 — flags optional; keep store defaults
            _dated_layout, _index_enabled = False, True
        thread_store = ThreadStateStore(
            per_agent_base=_per_agent_base,
            dated_layout=_dated_layout,
            index_enabled=_index_enabled,
        )
        app.state.thread_store = thread_store
        # Hand the live store to the history_search / history_read skills so
        # they query the same in-memory state the gateway serves, instead of
        # building a second read-only store off disk.
        try:
            from runtime.execution.suckers.history_skill import set_default_thread_store

            set_default_thread_store(thread_store)
        except Exception:  # noqa: BLE001 — skill module optional
            pass
        # Defer: feed stack.config.mcp_servers into the mcp_router
        # factory so the router owns the initial-seed logic instead
        # of doing it twice (once here, once inside the factory).
        _stack_mcp_servers = getattr(
            getattr(stack, "config", None),
            "mcp_servers",
            None,
        )

    # Claude-style subagents · independent from SkillRegistry.
    if subagent_registry is None:
        try:
            from runtime.execution.subagents import load_subagent_registry

            subagent_registry = load_subagent_registry(project_root=ctx.project_root)
        except (ImportError, AttributeError, TypeError, OSError):
            subagent_registry = None
    try:
        from runtime.execution.subagents import set_subagent_registry

        set_subagent_registry(subagent_registry)
    except (ImportError, AttributeError, TypeError):  # best-effort · subagent dispatch is optional
        pass

    try:
        from runtime.memory.cowork.runtime import create_cowork_runtime

        cowork_runtime = create_cowork_runtime(
            thread_store=thread_store,
            enable_runner=stack is not None,
        )
        app.state.cowork_runtime = cowork_runtime
        app.state.cowork_group_store = cowork_runtime.group_store
        app.state.cowork_async_store = cowork_runtime.async_store
        app.state.collaboration_store = cowork_runtime.collaboration_store
        if (
            stack is not None
            and cowork_runtime.runner_enabled
            and cowork_runtime.runner is not None
        ):

            def _start_cowork_runner() -> None:
                try:
                    recovered = cowork_runtime.async_store.recover_stale_working()
                    if recovered.get("requeued") or recovered.get("failed"):
                        logging.getLogger(__name__).info(
                            "cowork async recovered stale tasks: %s",
                            recovered,
                        )
                    cowork_runtime.start()
                except Exception as exc:  # noqa: BLE001
                    logging.getLogger(__name__).warning(
                        "cowork async runner failed to start: %s",
                        exc,
                    )

            def _stop_cowork_runner() -> None:
                with contextlib.suppress(Exception):
                    cowork_runtime.stop()

            app.router.add_event_handler("startup", _start_cowork_runner)
            app.router.add_event_handler("shutdown", _stop_cowork_runner)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "cowork runtime failed to initialize: %s",
            exc,
        )

    ctx.thread_store = thread_store
    ctx.thread_upload_root = thread_upload_root
    ctx.thread_workspace_root = thread_workspace_root
    ctx.cowork_runtime = cowork_runtime
    ctx.stack_mcp_servers = _stack_mcp_servers
    ctx.subagent_registry = subagent_registry


def wire_persistent_subagent_runner(ctx: AppContext) -> None:
    """Bind Claude-style persistent subagents to the live execution stack.

    ``wire_stack`` runs before :func:`mount_agents`, so the application agent
    registry is not available there yet.  Project OS and other persistent
    dispatchers use ``runtime.execution.subagents.call_subagent`` and therefore
    need a runner only *after* the agent registry has been mounted. Keeping this
    as a second, explicit wiring phase avoids silently falling back to the
    bridge's ``runner not configured`` error at execution time.

    The runner is retained on this app's context and passed explicitly by
    Project OS. The bridge's process-global slot remains a compatibility
    fallback for older callers. The shutdown hook only clears that fallback
    when it still owns the installed instance, so overlapping app factories
    cannot tear down a newer app's runner.
    """

    stack = ctx.stack
    app = ctx.app
    ctx.subagent_runner = None
    app.state.subagent_runner_ready = False
    if stack is None:
        return

    try:
        from runtime.execution.parallel_agents.stack_runner import (
            make_stack_subagent_runner,
        )
        from runtime.execution.subagents import (
            get_sub_agent_runner,
            set_sub_agent_runner,
        )

        runner = make_stack_subagent_runner(
            stack=stack,
            agent_registry=ctx.agent_registry,
        )
        set_sub_agent_runner(runner)
        ctx.subagent_runner = runner
        app.state.subagent_runner = runner
        app.state.subagent_runner_ready = True

        def _clear_persistent_subagent_runner() -> None:
            if get_sub_agent_runner() is runner:
                set_sub_agent_runner(None)

        app.router.add_event_handler("shutdown", _clear_persistent_subagent_runner)
        logging.getLogger(__name__).info("persistent subagent runner wired to execution stack")
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        logging.getLogger(__name__).error(
            "persistent subagent runner wiring failed (%s: %s) · "
            "Project OS execution is unavailable",
            type(exc).__name__,
            exc,
        )
