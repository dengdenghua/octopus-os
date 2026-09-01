# ruff: noqa: E402, I001 — module-level imports below intentionally appear after
# the dotenv bootstrap and configure_logging() call so that environment
# variables and logging are wired before any runtime module loads.
from __future__ import annotations

import contextlib
import logging
import sys


def _ensure_utf8_stdio() -> None:
    """Keep Windows packaged builds from crashing on non-ASCII status output."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")


_ensure_utf8_stdio()

# Auto-load .env from cwd · quiet no-op if missing or python-dotenv absent.
# Must run BEFORE any module that reads env at import time (e.g. routers
# that probe ANTHROPIC_API_KEY in their constructor).
#
# Policy: .env values WIN over shell env. Two reasons:
#   1. Windows shells often pre-export empty placeholders
#      (``ANTHROPIC_API_KEY=``) that would otherwise block .env from
#      filling them in if we used ``override=False``.
#   2. ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL must stay paired · if a
#      shell exported the default base URL but .env configures a mirror
#      key, mixing them produces a silent 401. Letting .env fully win
#      keeps the paired credentials internally consistent.
# Operators who prefer shell-wins policy can simply not create .env.
try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(override=True)
    del _load_dotenv
except (ImportError, TypeError, AttributeError):  # noqa: BLE001 — dotenv optional; skip if module/env malformed
    pass

from runtime.platform.observability.logging_config import configure_logging

configure_logging()

_logger = logging.getLogger(__name__)

# Re-exports from sibling CLI modules (backward compatibility — tests
# and downstream callers import from ``runtime.cli``).
from runtime.cli_core import (  # noqa: F401
    _build_reflex_router,
    _build_stack,
    _Colors,
    _graph_has_template_deps,
    _make_router,
    _short_output,
    DEFAULT_RULES,
    print_cost_breakdown,
)
from runtime.cli_reflect import (
    run_intel,
    run_loop,
    run_optimize,
    run_reflect,
)
from runtime.cli_run import (
    run_bench,
    run_goal,
    run_goal_from_config,
    run_resume,
)
from runtime.cli_serve import (  # noqa: F401
    register_reflection_tasks as _register_reflection_tasks,
    run_serve,
)

# Re-exports from the two split submodules (pure structural refactor).
from runtime._cli_commands import (  # noqa: F401
    run_backup,
    run_bb,
    run_doctor,
    run_export,
    run_kg,
    run_plugins,
    run_quickstart,
    run_restore,
    run_setup,
    run_skills,
    run_status,
    run_ui,
    run_wiki,
)
from runtime._cli_parser import _build_parser, _normalize_cli_argv

from runtime.platform.i18n import set_lang


def _main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``echo-agent``.

    Argparse construction lives in ``runtime._cli_parser``; the individual
    ``run_*`` command handlers live in ``runtime._cli_commands``. This file
    wires them together and dispatches the parsed subcommand.
    """
    argv = _normalize_cli_argv(list(sys.argv[1:] if argv is None else argv))
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Apply language setting
    if args.lang is not None:
        set_lang(args.lang)
    color = not args.no_color

    if args.command == "code":
        from runtime.cli_code import list_code_sessions, run_code_command

        if args.list_sessions:
            return list_code_sessions(args)
        return run_code_command(args, color=color)

    if args.command == "mcp":
        from runtime.cli_mcp import run_mcp_command

        return run_mcp_command(args)

    if args.command == "bugfix-demo":
        from demos.bugfix_demo import run_demo as _bugfix

        result = _bugfix(
            workdir=args.workdir,
            color=color and not args.no_color,
            verbose=True,
        )
        return 0 if result["success"] else 1

    if args.command == "bugfix-demo-v2":
        from demos.bugfix_demo_v2 import run_demo_v2 as _bugfix_v2

        result = _bugfix_v2(
            workdir=args.workdir,
            color=color and not args.no_color,
        )
        return 0 if result["success"] else 1

    if args.command == "reflection-demo":
        from demos.reflection_demo import run_demo as _reflect

        result = _reflect(
            workdir=args.workdir,
            runs=args.runs,
            color=color and not args.no_color,
            verbose=True,
        )
        return 0 if result["success"] else 1

    if args.command == "evolution-demo":
        from demos.evolution_demo import run_demo as _evolve

        result = _evolve(
            workdir=args.workdir,
            runs=args.runs,
            color=color and not args.no_color,
            verbose=True,
        )
        return 0 if result["success"] else 1

    if args.command == "demo":
        return run_goal(
            "list files in current dir and read README.md and count its words",
            intent_type="task",
            color=color,
            args_override={
                "intent_goal": "demo",
                "path": ".",
                "text": "demo-words-placeholder",
            },
        )

    if args.command == "run":
        if args.config is not None:
            return run_goal_from_config(
                goal=args.goal,
                config_path=args.config,
                intent_type=args.intent,
                color=color,
                show_cost=args.show_cost,
                swarm=args.swarm,
                max_workers=args.max_workers,
            )
        return run_goal(
            args.goal,
            intent_type=args.intent,
            max_tokens=args.max_tokens,
            max_usd=args.max_usd,
            color=color,
            planner_type=args.planner,
            planner_model=args.model,
            mock_response=args.mock_response,
            journal_file=args.journal_file,
            show_cost=args.show_cost,
            swarm=args.swarm,
            max_workers=args.max_workers,
            learn_from=args.learn_from,
        )

    if args.command == "bench":
        return run_bench(
            tasks=args.tasks,
            delay_ms=args.delay_ms,
            workers=args.workers,
            color=color,
        )

    if args.command == "intel":
        return run_intel(
            queries=args.queries,
            fetch_top=args.fetch_top,
            max_results=args.max_results,
            journal_file=args.journal_file,
            color=color,
        )

    if args.command == "project":
        from runtime.cli_project import run_project_command

        return run_project_command(args, color=color)

    if args.command == "kg":
        return run_kg(
            from_journal=args.from_journal,
            subject=args.subject,
            predicate=args.predicate,
            object_=args.object_,
            neighbors=args.neighbors,
            limit=args.limit,
            color=color,
        )

    if args.command == "backup":
        return run_backup(
            output=args.output,
            base_dir=args.base_dir,
            components=args.components,
            color=color,
        )

    if args.command == "restore":
        return run_restore(
            input_path=args.input,
            base_dir=args.base_dir,
            components=args.components,
            overwrite=args.overwrite,
            color=color,
        )

    if args.command == "export":
        return run_export(
            output=args.output,
            base_dir=args.base_dir,
            components=args.components,
            color=color,
        )

    if args.command == "wiki":
        return run_wiki(
            from_journal=args.from_journal,
            output_dir=args.output_dir,
            color=color,
        )

    if args.command == "reflect":
        return run_reflect(
            from_journal=args.from_journal,
            verbose=args.verbose,
            skip=set(args.skip),
            color=color,
        )

    if args.command == "status":
        return run_status(color=color)

    if args.command == "quickstart":
        return run_quickstart(
            output=args.output,
            non_interactive=args.non_interactive,
            force=args.force,
            host=args.host,
            port=args.port,
            serve=args.serve,
            learn_interval_s=args.learn_interval,
            color=color,
        )

    if args.command == "setup":
        return run_setup(
            output=args.output,
            non_interactive=args.non_interactive,
            color=color,
        )

    if args.command == "doctor":
        return run_doctor(
            config_path=args.config,
            color=color,
        )

    if args.command == "skills":
        return run_skills(args, color=color)

    if args.command == "bb":
        return run_bb(args)

    if args.command == "plugins":
        return run_plugins(args, color=color)

    if args.command == "tour":
        from .tour import run_tour

        return run_tour(
            chapters=args.chapters or None,
            pause=not args.no_pause,
            color=color,
        )

    if args.command == "ui":
        return run_ui(
            host=args.host,
            port=args.port,
            uds=getattr(args, "uds", None),
            journal_path=args.journal,
        )

    if args.command == "migrate":
        from runtime.cli_migrate import run_migrate

        return run_migrate(
            sources=args.source,
            apply=args.apply or args.activate,  # --activate implies --apply
            activate=args.activate,
            kinds=args.kinds,
        )

    if args.command == "loop":
        return run_loop(
            goal=args.goal,
            config_path=args.config,
            journal_path=args.journal,
            iterations=args.iterations,
            intent_type=args.intent,
            color=color,
        )

    if args.command == "serve":
        return run_serve(
            config_path=args.config,
            host=args.host,
            port=args.port,
            uds=getattr(args, "uds", None),
            learn_interval_s=args.learn_interval,
            prompt_variants_path=args.prompt_variants,
            evolve_interval_s=args.evolve_interval,
            mutator_model=args.mutator_model,
            color=color,
        )

    if args.command == "optimize":
        return run_optimize(
            goal=args.goal,
            config_path=args.config,
            variants_path=args.variants,
            journal_path=args.journal,
            rounds=args.rounds,
            tasks_per_round=args.tasks_per_round,
            mutator_model=args.mutator_model,
            mutator_response=args.mutator_response,
            max_variants=args.max_variants,
            retire_min_uses=args.retire_min_uses,
            export_path=args.export,
            color=color,
        )

    if args.command == "resume":
        return run_resume(
            task_id=args.task_id,
            journal_path=args.journal,
            goal=args.goal,
            config_path=args.config,
            intent_type=args.intent,
            dry_run=args.dry_run,
            color=color,
        )

    if args.command == "guard-health":
        from runtime.cli_guard_health import run_guard_health

        return run_guard_health(
            telemetry_path=args.telemetry,
            top=args.top,
            noisy=args.noisy,
            unjudged=args.unjudged,
            recommend=args.recommend,
            min_precision=args.min_precision,
            tuning_threshold=args.tuning_threshold,
        )

    return 2


def main(argv: list[str] | None = None) -> int:
    """Dispatch the CLI, binding a one-shot Cron Session when inherited.

    Only the background Cron executor writes the private environment payload.
    It is consumed before any command runs so nested subprocesses cannot reuse
    another task's authority.
    """

    from runtime.execution.cron_context import (
        CronContextError,
        consume_cron_session_from_environment,
    )

    try:
        cron_session = consume_cron_session_from_environment()
    except CronContextError as exc:
        _logger.error("refusing malformed cron execution context: %s", exc)
        return 2
    if cron_session is None:
        return _main(argv)

    from runtime.platform.process.session import session_scope

    with session_scope(cron_session):
        return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
