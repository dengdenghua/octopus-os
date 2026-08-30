"""Internal argparse helpers for the ``echo-agent`` CLI.

Extracted from ``runtime/cli.py`` (pure structural refactor — no logic
changes). Holds the subcommand parser construction, the set of known
commands, and the product-first argv normalization.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from runtime.platform.i18n import _

from . import __version__

_CLI_COMMANDS = frozenset(
    {
        "demo",
        "code",
        "mcp",
        "tour",
        "bugfix-demo",
        "bugfix-demo-v2",
        "reflection-demo",
        "evolution-demo",
        "run",
        "bench",
        "intel",
        "status",
        "quickstart",
        "reflect",
        "ui",
        "migrate",
        "optimize",
        "resume",
        "serve",
        "loop",
        "kg",
        "project",
        "backup",
        "restore",
        "export",
        "wiki",
        "setup",
        "doctor",
        "skills",
        "bb",
        "plugins",
        "guard-health",
    }
)


def _normalize_cli_argv(argv: list[str]) -> list[str]:
    """Make the product-first path be a coding session.

    ``echo-agent code ...`` remains explicit. If the user omits a
    subcommand, route the remaining arguments to ``code`` while preserving
    global flags such as ``--no-color`` and ``--lang``.
    """

    if not argv or any(token in {"-h", "--help", "--version"} for token in argv):
        return argv

    idx = 0
    while idx < len(argv):
        token = argv[idx]
        if token in _CLI_COMMANDS:
            return argv
        if token == "--no-color":
            idx += 1
            continue
        if token == "--lang":
            idx += 2
            continue
        if token.startswith("--lang="):
            idx += 1
            continue
        return [*argv[:idx], "code", *argv[idx:]]
    return argv


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="echo-agent",
        description=_("Biomimetic self-evolving agent OS (MVP demo runner)."),
    )
    parser.add_argument("--version", action="version", version=f"echo-agent {__version__}")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    parser.add_argument(
        "--lang",
        default="auto",
        choices=["auto", "en", "zh", "zh-CN", "ja", "ko"],
        help=_("cli.help.lang"),
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help=_("cli.help.demo"))

    codep = sub.add_parser("code", help="Run an agentic coding session.")
    codep.add_argument("prompt", nargs="*", help="coding task prompt; omit to read stdin")
    codep.add_argument(
        "--cwd", type=Path, default=None, help="workspace root (default: current directory)"
    )
    codep.add_argument("--model", default=None, help="model name for the ReAct planner")
    codep.add_argument(
        "--permission-mode",
        choices=["default", "acceptEdits", "bypassPermissions", "plan"],
        default="default",
        help="approval policy for tool use",
    )
    codep.add_argument(
        "--continue",
        dest="continue_session",
        action="store_true",
        help="resume latest coding session",
    )
    codep.add_argument("--resume", default=None, help="resume a specific coding session id")
    codep.add_argument("--print", action="store_true", help="print only the final answer")
    codep.add_argument(
        "--output-format",
        choices=["text", "json", "stream-json"],
        default="text",
        help="output renderer",
    )
    codep.add_argument(
        "--add-dir", action="append", default=[], help="additional readable workspace directory"
    )
    codep.add_argument(
        "--worktree", action="store_true", help="run with Echo sandbox isolation metadata"
    )
    codep.add_argument("--list-sessions", action="store_true", help="list saved coding sessions")
    codep.add_argument("--max-iterations", type=int, default=30)
    codep.add_argument("--max-tokens", type=int, default=50_000)
    codep.add_argument("--max-usd", type=float, default=0.50)
    codep.add_argument("--mock-response", default=None, help=argparse.SUPPRESS)

    mcpp = sub.add_parser("mcp", help="Manage MCP servers and trust.")
    mcp_sub = mcpp.add_subparsers(dest="mcp_op", required=True)
    mcp_add = mcp_sub.add_parser("add", help="Add an MCP stdio server.")
    mcp_add.add_argument("name", help="server name")
    mcp_add.add_argument(
        "--env", action="append", default=[], help="environment variable KEY=VALUE"
    )
    mcp_add.add_argument(
        "--trust-level", default="custom", choices=["public", "custom", "external"]
    )
    mcp_add.add_argument("--timeout-ms", type=int, default=30_000)
    mcp_add.add_argument("--disabled", action="store_true", help="save disabled")
    mcp_add.add_argument("--trust", action="store_true", help="also mark this server trusted")
    mcp_add.add_argument("server_command", nargs=argparse.REMAINDER, help="-- command args...")
    mcp_list = mcp_sub.add_parser("list", help="List MCP servers.")
    mcp_list.add_argument("--output-format", choices=["text", "json"], default="text")
    mcp_remove = mcp_sub.add_parser("remove", help="Remove an MCP server.")
    mcp_remove.add_argument("name", help="server name")
    mcp_remove.add_argument(
        "--keep-trust", dest="forget_trust", action="store_false", help="keep trust entry"
    )
    mcp_trust = mcp_sub.add_parser("trust", help="Trust an MCP server.")
    mcp_trust.add_argument("name", help="server name")
    mcp_trust.add_argument(
        "--tool", dest="tools", action="append", default=[], help="pin an exposed tool name"
    )
    mcp_trust.add_argument("--note", default="")
    mcp_revoke = mcp_sub.add_parser("revoke", help="Revoke MCP server trust.")
    mcp_revoke.add_argument("name", help="server name")

    mcp_serve = mcp_sub.add_parser("serve", help="Start Tentacle MCP Server (stdio or SSE mode).")
    mcp_serve.add_argument(
        "--stdio", action="store_true", help="Run in stdio mode (for Claude Desktop command)"
    )
    mcp_serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for SSE mode (default: 127.0.0.1; pass 0.0.0.0 to expose on the network)",
    )
    mcp_serve.add_argument(
        "--port", type=int, default=8766, help="Port for SSE mode (default: 8766)"
    )
    mcp_serve.add_argument(
        "--log-level",
        default="warning",
        choices=["debug", "info", "warning", "error"],
        help="Log level",
    )

    # tour · 5-minute interactive walkthrough
    tourp = sub.add_parser(
        "tour",
        help=_("cli.help.tour"),
    )
    tourp.add_argument(
        "--chapters",
        type=int,
        default=0,
        help="run only the first N chapters (0 = all)",
    )
    tourp.add_argument(
        "--no-pause",
        action="store_true",
        help="don't pause for Enter between chapters (for CI / screenshots)",
    )
    bugfixp = sub.add_parser(
        "bugfix-demo",
        help=_("cli.help.bugfix_demo"),
    )
    bugfixp.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="project root (default: tempdir)",
    )
    bugfixp.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="disable ANSI colors",
    )

    bugfixp_v2 = sub.add_parser(
        "bugfix-demo-v2",
        help="Bug fix + MiniMax-style evolution loop · fix → update_soul → apply learned lesson on a different bug",
    )
    bugfixp_v2.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="project root (default: tempdir)",
    )
    bugfixp_v2.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="disable ANSI colors",
    )

    reflectp_demo = sub.add_parser(
        "reflection-demo",
        help=_("cli.help.reflection_demo"),
    )
    reflectp_demo.add_argument("--workdir", type=Path, default=None)
    reflectp_demo.add_argument(
        "--runs",
        type=int,
        default=3,
        help="how many bugfix runs to populate the journal (default: 3)",
    )
    reflectp_demo.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS)

    evolp_demo = sub.add_parser(
        "evolution-demo",
        help=_("cli.help.evolution_demo"),
    )
    evolp_demo.add_argument("--workdir", type=Path, default=None)
    evolp_demo.add_argument("--runs", type=int, default=3)
    evolp_demo.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS)

    runp = sub.add_parser("run", help=_("cli.help.run"))
    runp.add_argument("goal", type=str, help="natural-language goal")
    runp.add_argument("--intent", default="task", help="intent type hint")
    runp.add_argument("--max-tokens", type=int, default=50_000)
    runp.add_argument("--max-usd", type=float, default=0.50)
    runp.add_argument(
        "--planner",
        choices=["static", "llm"],
        default="static",
        help="planning strategy (static=rule-based, llm=LLM-driven)",
    )
    runp.add_argument(
        "--model",
        default="mock/planner",
        help="LLM model (e.g. claude-haiku-4-5-20251001, or mock/X)",
    )
    runp.add_argument(
        "--mock-response",
        default=None,
        help="use MockModelRouter with this literal JSON response",
    )
    runp.add_argument(
        "--journal-file",
        type=Path,
        default=None,
        help="persist all events to a JSON Lines file (append-only)",
    )
    runp.add_argument(
        "--show-cost",
        action="store_true",
        help="print a per-step cost breakdown after execution",
    )
    runp.add_argument(
        "--swarm",
        action="store_true",
        help="dispatch TaskGraph nodes concurrently to multiple Arm workers",
    )
    runp.add_argument(
        "--max-workers",
        type=int,
        default=3,
        help="max concurrent arms when --swarm (default 3)",
    )
    runp.add_argument(
        "--learn-from",
        type=Path,
        default=None,
        help="JSONL journal file · preload LearnedRules into LLMPlanner before planning",
    )
    runp.add_argument(
        "--config",
        type=Path,
        default=None,
        help="YAML config file (overrides planner/budget/immunity/learn when set)",
    )

    # bench subcommand · real-sleep concurrency benchmark
    benchp = sub.add_parser("bench", help=_("cli.help.bench"))
    benchp.add_argument("--tasks", type=int, default=4, help="number of parallel tasks")
    benchp.add_argument("--delay-ms", type=int, default=80, help="per-task sleep delay")
    benchp.add_argument("--workers", type=int, default=4, help="swarm max_workers")

    # intel subcommand · active learning · run IntelCollector once
    intelp = sub.add_parser("intel", help=_("cli.help.intel"))
    intelp.add_argument("queries", nargs="+", help="one or more search queries")
    intelp.add_argument(
        "--fetch-top",
        type=int,
        default=0,
        help="for each query, fetch_url top N results (default 0)",
    )
    intelp.add_argument(
        "--max-results",
        type=int,
        default=5,
        help="per-query max search results (default 5)",
    )
    intelp.add_argument(
        "--journal-file",
        type=Path,
        default=None,
        help="persist intel events to JSONL (for --learn-from later)",
    )

    sub.add_parser(
        "status",
        help=_("cli.help.status"),
    )

    quickstartp = sub.add_parser(
        "quickstart",
        help="Bootstrap config, run doctor, and print the local start command.",
    )
    quickstartp.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("config.yaml"),
        help="config path to create or reuse (default: ./config.yaml)",
    )
    quickstartp.add_argument(
        "--non-interactive",
        action="store_true",
        help="generate a minimal static config without prompts when config is missing",
    )
    quickstartp.add_argument(
        "--force",
        action="store_true",
        help="overwrite the config file instead of reusing it",
    )
    quickstartp.add_argument("--host", default="127.0.0.1")
    quickstartp.add_argument("--port", type=int, default=8000)
    quickstartp.add_argument(
        "--serve",
        action="store_true",
        help="start the FastAPI service after setup and doctor checks",
    )
    quickstartp.add_argument(
        "--learn-interval",
        type=int,
        default=0,
        help="periodic learn_from_journal interval in seconds when --serve is set",
    )

    reflectp = sub.add_parser(
        "reflect",
        help=_("cli.help.reflect"),
    )
    reflectp.add_argument(
        "--from-journal", type=Path, required=True, help="JSONL journal to analyze"
    )
    reflectp.add_argument(
        "--verbose", "-v", action="store_true", help="print full details per producer"
    )
    reflectp.add_argument(
        "--skip",
        nargs="*",
        default=[],
        choices=[
            "skill_forge",
            "rule_extractor",
            "kg_updater",
            "memory",
            "workflow_rewriter",
            "recipe",
        ],
        help="skip specific producers",
    )

    uip = sub.add_parser("ui", help=_("cli.help.ui"))
    uip.add_argument("--host", default="127.0.0.1")
    uip.add_argument("--port", type=int, default=8000)
    uip.add_argument(
        "--uds",
        type=str,
        default=None,
        metavar="PATH",
        help="Listen on a Unix domain socket instead of TCP.",
    )
    uip.add_argument(
        "--journal",
        type=Path,
        default=None,
        help="JSONL journal file to visualize (default: in-memory)",
    )

    migratep = sub.add_parser(
        "migrate",
        help="Migrate plugins/memory/MCP from Codex/Claude into echo.",
    )
    migratep.add_argument(
        "--source",
        default=None,
        help="Comma list of sources: codex,claude (default: all installed).",
    )
    migratep.add_argument(
        "--apply",
        action="store_true",
        help="Stage into .echo/imported/ (default: preview only).",
    )
    migratep.add_argument(
        "--activate",
        action="store_true",
        help="Also activate memory + emit MCP config snippets (implies --apply).",
    )
    migratep.add_argument(
        "--kinds",
        default=None,
        help="Comma list to limit kinds: skill,memory,rule,mcp_server,agent,command.",
    )

    optp = sub.add_parser(
        "optimize",
        help=_("cli.help.optimize"),
    )
    optp.add_argument("goal", type=str, help="natural-language goal")
    optp.add_argument(
        "--config", type=Path, required=True, help="YAML config (LLM planner required)"
    )
    optp.add_argument(
        "--variants",
        type=Path,
        default=None,
        help="initial variants YAML · omit to start with baseline",
    )
    optp.add_argument(
        "--journal", type=Path, required=True, help="JSONL journal file · accumulates trajectories"
    )
    optp.add_argument("--rounds", type=int, default=10, help="evolution rounds (default 10)")
    optp.add_argument(
        "--tasks-per-round",
        type=int,
        default=5,
        help="tasks between each evolution step (default 5)",
    )
    optp.add_argument(
        "--mutator-model",
        type=str,
        default="mock/mutator",
        help="LLM for PromptMutator (real: claude-haiku-4-5-20251001)",
    )
    optp.add_argument(
        "--mutator-response",
        type=str,
        default=None,
        help="canned mutator response (for mock/* models)",
    )
    optp.add_argument("--max-variants", type=int, default=8, help="max pool size (default 8)")
    optp.add_argument(
        "--retire-min-uses", type=int, default=5, help="don't retire until ≥N uses (default 5)"
    )
    optp.add_argument("--export", type=Path, default=None, help="export winning variants to YAML")

    resumep = sub.add_parser(
        "resume",
        help=_("cli.help.resume"),
    )
    resumep.add_argument("--task-id", required=True, help="original task UUID as seen in journal")
    resumep.add_argument(
        "--journal", type=Path, required=True, help="JSONL journal file with prior events"
    )
    resumep.add_argument(
        "--goal", type=str, required=True, help="original natural-language goal (to re-plan graph)"
    )
    resumep.add_argument(
        "--config", type=Path, required=True, help="YAML config · must match original planner setup"
    )
    resumep.add_argument("--intent", default="task")
    resumep.add_argument(
        "--dry-run", action="store_true", help="print resume diagnostic · do not execute"
    )

    servep = sub.add_parser(
        "serve",
        help=_("cli.help.serve"),
    )
    servep.add_argument(
        "--config",
        type=Path,
        required=True,
        help="YAML config (defines planner/journal/intel_sources)",
    )
    servep.add_argument("--host", default="127.0.0.1")
    servep.add_argument("--port", type=int, default=8000)
    servep.add_argument(
        "--uds",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "Listen on a Unix domain socket instead of TCP "
            "(e.g. /tmp/echo.sock). "
            "When set, --host and --port are ignored. "
            "Electron / desktop clients connect via ws+unix:///PATH."
        ),
    )
    servep.add_argument(
        "--learn-interval",
        type=int,
        default=0,
        help="periodic learn_from_journal interval in seconds (0=off)",
    )
    servep.add_argument(
        "--prompt-variants",
        type=Path,
        default=None,
        help="variants YAML · enables live A/B on /v1/chat/completions",
    )
    servep.add_argument(
        "--evolve-interval",
        type=int,
        default=0,
        help="run evolver.step() every N seconds (0=off; needs --prompt-variants)",
    )
    servep.add_argument(
        "--mutator-model",
        type=str,
        default="mock/mutator",
        help="LLM for PromptMutator (default mock · real: claude-haiku-4-5-20251001)",
    )

    loopp = sub.add_parser(
        "loop",
        help=_("cli.help.loop"),
    )
    loopp.add_argument("goal", type=str, help="natural-language goal")
    loopp.add_argument(
        "--config", type=Path, required=True, help="YAML config (defines planner/budget/etc)"
    )
    loopp.add_argument(
        "--journal",
        type=Path,
        required=True,
        help="JSONL journal file · reads for reflection, appends each iter",
    )
    loopp.add_argument("--iterations", type=int, default=3, help="number of run cycles (default 3)")
    loopp.add_argument("--intent", default="task")

    kgp = sub.add_parser("kg", help="Build a KnowledgeGraph from a JSONL journal, then query.")
    kgp.add_argument(
        "--from-journal", type=Path, required=True, help="JSONL journal to load events from"
    )

    projectp = sub.add_parser("project", help="Run milestone-driven projects (Project OS).")
    project_sub = projectp.add_subparsers(dest="project_op", required=True)
    _pp_plan = project_sub.add_parser("plan", help="Plan a project from a goal.")
    _pp_plan.add_argument("--goal", required=True, help="one-line project goal")
    _pp_plan.add_argument("--name", default="", help="project name (default: from goal)")
    _pp_run = project_sub.add_parser("run", help="Run a project to completion.")
    _pp_run.add_argument("--id", default=None, help="existing project id")
    _pp_run.add_argument("--goal", default=None, help="goal to plan+run if no --id")
    _pp_run.add_argument("--name", default="", help="project name when planning")
    _pp_run.add_argument("--max-ticks", type=int, default=50, dest="max_ticks")
    _pp_report = project_sub.add_parser("report", help="Show a project's milestone report.")
    _pp_report.add_argument("--id", required=True, help="project id")
    project_sub.add_parser("list", help="List all projects.")

    backupp = sub.add_parser("backup", help="Create a tar.gz backup of all Echo data.")
    backupp.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help=("output tar.gz path (default: backup-<timestamp>.tar.gz below the active Echo home)"),
    )
    backupp.add_argument(
        "--base-dir",
        type=str,
        default=None,
        help=("Echo data root (default: runtime environment; ECHO_DATA_DIR/ECHO_HOME or ~/.echo)"),
    )
    backupp.add_argument(
        "--components",
        nargs="*",
        default=None,
        choices=[
            "journal",
            "kg",
            "config",
            "hot_cache",
            "skills",
            "agents",
            "narrative_studio",
        ],
        help="components to include (default: all)",
    )

    restorep = sub.add_parser("restore", help="Restore Echo data from a tar.gz backup.")
    restorep.add_argument(
        "input",
        type=Path,
        help="backup tar.gz file to restore from",
    )
    restorep.add_argument(
        "--base-dir",
        type=str,
        default=None,
        help=("Echo data root (default: runtime environment; ECHO_DATA_DIR/ECHO_HOME or ~/.echo)"),
    )
    restorep.add_argument(
        "--components",
        nargs="*",
        default=None,
        choices=[
            "journal",
            "kg",
            "config",
            "hot_cache",
            "skills",
            "agents",
            "narrative_studio",
        ],
        help="components to restore (default: all)",
    )
    restorep.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite existing files",
    )

    exportp = sub.add_parser("export", help="Export Echo data as human-readable JSON.")
    exportp.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        help="output JSON path",
    )
    exportp.add_argument(
        "--base-dir",
        type=str,
        default=None,
        help=("Echo data root (default: runtime environment; ECHO_DATA_DIR/ECHO_HOME or ~/.echo)"),
    )
    exportp.add_argument(
        "--components",
        nargs="*",
        default=None,
        choices=[
            "journal",
            "kg",
            "config",
            "hot_cache",
            "skills",
            "agents",
            "narrative_studio",
        ],
        help="components to export (default: all)",
    )

    wikip = sub.add_parser(
        "wiki", help="Compile reflection outputs into a human-readable Markdown Wiki."
    )
    wikip.add_argument(
        "--from-journal",
        type=Path,
        required=True,
        help="JSONL journal to compile from",
    )
    wikip.add_argument(
        "--output-dir",
        type=str,
        default="~/.echo/wiki",
        help="Wiki output directory (default: ~/.echo/wiki)",
    )

    kgp.add_argument("--subject", default=None, help="filter by subject (exact match)")
    kgp.add_argument("--predicate", default=None, help="filter by predicate (exact match)")
    kgp.add_argument(
        "--object", dest="object_", default=None, help="filter by object (exact match)"
    )
    kgp.add_argument("--neighbors", default=None, help="print neighbors of this entity (1 hop)")
    kgp.add_argument("--limit", type=int, default=20, help="max triples to print")

    setupp = sub.add_parser(
        "setup", help="Interactive setup wizard · generate config.yaml in 3 minutes."
    )
    setupp.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="output config path (default: ./config.yaml)",
    )
    setupp.add_argument(
        "--non-interactive",
        action="store_true",
        help="generate minimal static config without prompts",
    )

    doctorp = sub.add_parser(
        "doctor", help="Check environment health · dependencies, API keys, config."
    )
    doctorp.add_argument(
        "--config",
        type=Path,
        default=None,
        help="config file to validate (optional)",
    )

    skillsp = sub.add_parser("skills", help="Manage skills · list, search, install, publish.")
    skills_sub = skillsp.add_subparsers(dest="skills_op")
    skills_sub.add_parser("list", help="List installed skills.")
    skills_search = skills_sub.add_parser("search", help="Search skill marketplace.")
    skills_search.add_argument("query", help="search query")
    skills_search.add_argument("--limit", type=int, default=20)
    skills_install = skills_sub.add_parser(
        "install",
        help="Install a skill from the marketplace, a local dir, or a GitHub URL.",
    )
    skills_install.add_argument(
        "name",
        help="marketplace skill name, OR a local skill dir / GitHub URL (agentskills.io standard)",
    )
    skills_install.add_argument(
        "--allow-dangerous",
        action="store_true",
        help="Install an agentskills.io skill even if the safety scan flags it.",
    )
    skills_install.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing skill of the same name.",
    )
    skills_uninstall = skills_sub.add_parser("uninstall", help="Uninstall a skill.")
    skills_uninstall.add_argument("name", help="skill name to uninstall")
    skills_info = skills_sub.add_parser("info", help="Show skill details.")
    skills_info.add_argument("name", help="skill name")
    skills_publish = skills_sub.add_parser("publish", help="Prepare a skill for publishing.")
    skills_publish.add_argument("path", type=Path, help="path to skill directory")
    skills_lint = skills_sub.add_parser(
        "lint",
        help="Check a skill folder against the agentskills.io standard + safety scan.",
    )
    skills_lint.add_argument("path", type=Path, help="path to skill directory")

    bbp = sub.add_parser(
        "bb",
        help="Cross-process blackboard · let separate agent processes share a "
        "live workspace (needs ECHO_BLACKBOARD_DB).",
    )
    bb_sub = bbp.add_subparsers(dest="bb_op")
    bb_set = bb_sub.add_parser("set", help="Write key=value to the shared board.")
    bb_set.add_argument("key")
    bb_set.add_argument("value")
    bb_set.add_argument("--turn", default=None, help="turn id (or ECHO_TURN_ID)")
    bb_get = bb_sub.add_parser("get", help="Read a key (exit 1 if absent).")
    bb_get.add_argument("key")
    bb_get.add_argument("--turn", default=None)
    bb_keys = bb_sub.add_parser("keys", help="List keys on the shared board.")
    bb_keys.add_argument("--turn", default=None)
    bb_snap = bb_sub.add_parser("snapshot", help="Dump the whole shared board as JSON.")
    bb_snap.add_argument("--turn", default=None)

    pluginsp = sub.add_parser("plugins", help="Manage plugins · list, discover, load.")
    plugins_sub = pluginsp.add_subparsers(dest="plugins_op")
    plugins_sub.add_parser("list", help="List loaded plugins.")
    plugins_sub.add_parser("discover", help="Discover available plugins.")
    plugins_load = plugins_sub.add_parser("load", help="Load a plugin by name.")
    plugins_load.add_argument("name", help="plugin name")

    guardp = sub.add_parser("guard-health", help="Diagnose guard system health and precision.")
    guardp.add_argument("--telemetry", default=None, help="path to guard_hits.jsonl")
    guardp.add_argument("--top", type=int, default=10, help="show top N guards by hit count")
    guardp.add_argument(
        "--noisy", action="store_true", help="show only noisy guards (precision < 50%%)"
    )
    guardp.add_argument(
        "--unjudged", action="store_true", help="show guards with ≥20 hits but no verdicts"
    )
    guardp.add_argument("--recommend", action="store_true", help="show actionable recommendations")
    guardp.add_argument(
        "--min-precision", type=float, default=0.5, help="precision threshold for noise"
    )
    guardp.add_argument(
        "--tuning-threshold", type=int, default=20, help="min hits to consider for tuning"
    )

    return parser
