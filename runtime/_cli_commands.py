"""Concrete ``run_*`` command handlers for the ``echo-agent`` CLI.

Extracted from ``runtime/cli.py`` (pure structural refactor — no logic
changes). Each function carries out one subcommand; the argparse parser
(the ``_build_parser`` dispatcher) lives in ``runtime/_cli_parser.py``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

from runtime.cli_core import _Colors
from runtime.cli_serve import run_serve
from runtime.platform.i18n import _


def run_status(*, color: bool = True) -> int:
    from runtime.adapters.instrumentation import OTEL_AVAILABLE

    from . import __version__

    c = _Colors(color)

    def _check(label: str, import_path: str) -> tuple[str, str]:
        try:
            __import__(import_path)
            return "✓", "installed"
        except ImportError:
            return "✗", "not installed"

    def _line(ok: str, label: str, status: str) -> str:
        color_fn = c.green if ok == "✓" else c.dim
        return f"  {color_fn(ok)} {label:<28} · {status}"

    print(c.bold(_("cli.status.title_fmt", version=__version__)))
    print(c.dim("─" * 60))

    print(c.bold(_("cli.status.check_runtime")))
    ok, status = _check("pydantic", "pydantic")
    print(_line(ok, "pydantic (required)", status))
    print(
        _line(
            "✓" if OTEL_AVAILABLE else "✗",
            "opentelemetry",
            _(
                "cli.status.otel_status",
                status=_("cli.status.otel_installed")
                if OTEL_AVAILABLE
                else _("cli.status.otel_missing"),
            ),
        )
    )

    print()
    print(c.bold(_("cli.status.check_llm")))
    print(_line("✓", "MockModelRouter", _("cli.status.mock_always_available")))
    ok, status = _check("anthropic", "anthropic")
    print(_line(ok, "AnthropicModelRouter", status))

    print()
    print(c.bold(_("cli.status.check_external")))
    ok, status = _check("httpx", "httpx")
    print(_line(ok, _("cli.status.httpx_web_skills"), status))
    ok, status = _check("mcp", "mcp")
    print(_line(ok, _("cli.status.mcp_stdio"), status))
    ok, status = _check("playwright", "playwright")
    print(_line(ok, _("cli.status.playwright_browser"), status))
    ok, status = _check("fastapi", "fastapi")
    print(_line(ok, _("cli.status.fastapi_web_ui"), status))

    from runtime.execution.suckers import SkillRegistry as _SR  # noqa: N814
    from runtime.execution.suckers.builtins import BUILTIN_NAMES, register_all

    reg = _SR()
    try:
        total = register_all(reg)
    except Exception as exc:
        print(c.red(_("cli.status.builtin_failed", error=exc)))
        return 1
    web_skills = [n for n in reg.all_names() if n not in BUILTIN_NAMES]

    print(c.bold(_("cli.status.check_builtin")))
    for name in BUILTIN_NAMES:
        desc = reg.get(name).description[:60]
        print(_("cli.status.skill_fmt", name=c.bold(name), desc=c.dim(desc)))
    if web_skills:
        print(c.bold(_("cli.status.check_web")))
        for name in web_skills:
            desc = reg.get(name).description[:60]
            print(_("cli.status.skill_fmt", name=c.bold(name), desc=c.dim(desc)))

    print(c.bold(_("cli.status.check_reflection")))
    reflection_items = [
        _("cli.status.reflection_list.skillforge"),
        _("cli.status.reflection_list.ruleextractor"),
        _("cli.status.reflection_list.kgupdater"),
        _("cli.status.reflection_list.memoryconsolidator"),
        _("cli.status.reflection_list.workflowrewriter"),
        _("cli.status.reflection_list.recipeevaluator"),
    ]
    for n, label in enumerate(reflection_items, 1):
        print(f"  {_('cli.status.reflection_fmt', n=n, label=label)}")

    print(c.bold(_("cli.status.check_cli")))
    cli_items = [
        ("demo", _("cli.status.cmd.demo")),
        ("run", _("cli.status.cmd.run")),
        ("bench", _("cli.status.cmd.bench")),
        ("intel", _("cli.status.cmd.intel")),
        ("kg", _("cli.status.cmd.kg")),
        ("reflect", _("cli.status.cmd.reflect")),
        ("status", _("cli.status.cmd.status")),
    ]
    for cmd, desc in cli_items:
        print(_("cli.status.cmd_fmt", cmd=c.bold(cmd), desc=c.dim(desc)))

    print(c.dim("\n" + "─" * 30))
    print(c.dim(_("cli.status.total_fmt", n=total)))
    return 0


def run_kg(
    *,
    from_journal: Path,
    subject: str | None = None,
    predicate: str | None = None,
    object_: str | None = None,
    neighbors: str | None = None,
    limit: int = 20,
    color: bool = True,
) -> int:
    from runtime.memory.journal import JSONLJournal
    from runtime.memory.knowledge_graph import KnowledgeGraph
    from runtime.safety.recovery import KGUpdater

    c = _Colors(color)
    if not from_journal.exists():
        print(c.red(_("cli.kg.not_found", path=from_journal)), file=sys.stderr)
        return 2

    journal = JSONLJournal(from_journal)
    kg = KnowledgeGraph()
    report = KGUpdater(journal=journal, kg=kg).update()

    print(c.bold(_("cli.kg.title_fmt", path=from_journal)))
    print(c.dim("─" * 60))
    print(
        _(
            "cli.kg.scanned_fmt",
            scanned=report.events_scanned,
            accepted=report.triples_accepted,
            superseded=report.triples_superseded,
            ignored=report.triples_ignored,
        )
    )
    print(c.dim(_("cli.kg.count_fmt", active=kg.count(), total=len(kg))))
    print()

    if neighbors is not None:
        triples = kg.neighbors(neighbors, hops=1)
        print(c.bold(_("cli.kg.neighbors_fmt", entity=repr(neighbors))))
    else:
        triples = kg.query(subject=subject, predicate=predicate, object=object_)
        filters = []
        if subject:
            filters.append(f"subject={subject!r}")
        if predicate:
            filters.append(f"predicate={predicate!r}")
        if object_:
            filters.append(f"object={object_!r}")
        descr = " · ".join(filters) if filters else _("cli.kg.filter_all")
        print(c.bold(_("cli.kg.matching_fmt", filters=descr, count=len(triples))))

    if not triples:
        print(c.dim(_("cli.kg.empty")))
        return 0

    print()
    for t in triples[:limit]:
        s = t.subject[:40]
        p = t.predicate[:20]
        o = t.object[:60]

        def _identity(x: str) -> str:
            return x

        conf_color = (
            c.green if t.confidence >= 0.8 else (c.dim if t.confidence < 0.5 else _identity)
        )
        print(f"  {conf_color(c.bold(s)):<40}  {c.cyan(p):<20}  {o}")
        print(
            c.dim(_("cli.kg.triple_detail_fmt", confidence=t.confidence, source=t.source.source_id))
        )
    if len(triples) > limit:
        print(c.dim(_("cli.kg.more_fmt", n=len(triples) - limit)))
    return 0


def run_backup(
    *,
    output: Path | None = None,
    base_dir: str | None = None,
    components: list[str] | None = None,
    color: bool = True,
) -> int:
    from runtime.platform.lifecycle.backup import BackupManager

    c = _Colors(color)
    mgr = BackupManager(base_dir=base_dir)
    report = mgr.backup(output=output, components=components)

    if not report.success:
        print(c.red(f"backup failed: {report.error}"), file=sys.stderr)
        return 1

    print(c.bold("backup created"))
    print(c.dim("─" * 60))
    print(f"  path:       {report.output_path}")
    print(f"  components: {', '.join(report.manifest.components)}")
    print(f"  files:      {report.manifest.total_files}")
    print(f"  size:       {report.manifest.total_bytes:,} bytes")
    print(f"  created:    {report.manifest.created_at}")
    return 0


def run_restore(
    *,
    input_path: Path,
    base_dir: str | None = None,
    components: list[str] | None = None,
    overwrite: bool = False,
    color: bool = True,
) -> int:
    from runtime.platform.lifecycle.backup import BackupManager

    c = _Colors(color)
    mgr = BackupManager(base_dir=base_dir)
    report = mgr.restore(input_path=input_path, components=components, overwrite=overwrite)

    if not report.success:
        print(c.red(f"restore failed: {report.error}"), file=sys.stderr)
        return 1

    print(c.bold("restore completed"))
    print(c.dim("─" * 60))
    print(f"  source:     {report.input_path}")
    print(f"  components: {', '.join(report.components_restored)}")
    print(f"  files:      {report.files_restored}")
    return 0


def run_export(
    *,
    output: Path,
    base_dir: str | None = None,
    components: list[str] | None = None,
    color: bool = True,
) -> int:
    from runtime.platform.lifecycle.backup import BackupManager

    c = _Colors(color)
    mgr = BackupManager(base_dir=base_dir)
    path = mgr.export_json(output=output, components=components)

    print(c.bold("export completed"))
    print(c.dim("─" * 60))
    print(f"  path: {path}")
    return 0


def run_wiki(
    *,
    from_journal: Path,
    output_dir: str = "~/.echo/wiki",
    color: bool = True,
) -> int:
    from runtime.memory.diagnostics.wiki_compiler import WikiCompiler
    from runtime.memory.journal import JSONLJournal

    c = _Colors(color)

    if not from_journal.exists():
        print(c.red(f"journal not found: {from_journal}"), file=sys.stderr)
        return 2

    journal = JSONLJournal(from_journal)
    compiler = WikiCompiler(output_dir=output_dir)
    index = compiler.compile_from_journal(journal)

    print(c.bold("wiki compiled"))
    print(c.dim("─" * 60))
    print(f"  output_dir:    {output_dir}")
    print(f"  pages:         {index.total_pages}")
    print(f"  last_compiled: {index.last_compiled}")
    for page in index.pages:
        print(f"  - {page}")
    return 0


def run_ui(
    *,
    host: str,
    port: int,
    journal_path: Path | None,
    uds: str | None = None,
) -> int:
    try:
        import uvicorn  # noqa: F401 - fail early when the optional UI extra is absent

        from runtime.platform.ui.server_options import run_uvicorn
    except ImportError:
        print(_("cli.ui.not_installed"), file=sys.stderr)
        return 2
    try:
        from runtime.platform.ui import create_app
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2

    app = create_app(
        journal_path=journal_path,
        server_host=host,
        server_port=port,
    )

    # Opt-in co-launch of the echo-storage sibling (File Agent backend), so a
    # single-machine user gets one command. Off by default; best-effort.
    with contextlib.suppress(Exception):
        from runtime.sensing.gateway.storage_supervisor import (
            maybe_start_storage,
            start_storage_heartbeat,
        )

        maybe_start_storage()
        # Keep storage up for the whole session: detect late boot / crashes and
        # relaunch, instead of the old one-shot 10s readiness probe.
        start_storage_heartbeat()

    if uds:
        import os

        with contextlib.suppress(FileNotFoundError):
            os.unlink(uds)
        print(f"  unix socket: {uds}  (ws+unix:///{uds})")
        run_uvicorn(app, uds=uds, log_level="info")
    else:
        print(_("cli.ui.starting_fmt", host=host, port=port, journal=journal_path or "in-memory"))
        run_uvicorn(app, host=host, port=port, log_level="info")
    return 0


def run_setup(
    *,
    output: Path | None = None,
    non_interactive: bool = False,
    color: bool = True,
) -> int:
    from runtime.platform.lifecycle.setup_wizard import SetupWizard

    wizard = SetupWizard(output_path=output, non_interactive=non_interactive)
    wizard.run()
    return 0


def run_doctor(
    *,
    config_path: Path | None = None,
    color: bool = True,
) -> int:
    from runtime.platform.observability.doctor import Doctor

    doctor = Doctor(config_path=config_path)
    report = doctor.run()
    print()
    print(report.summary())
    return 0 if report.all_ok else 1


def run_quickstart(
    *,
    output: Path,
    non_interactive: bool,
    force: bool,
    host: str,
    port: int,
    serve: bool,
    learn_interval_s: int,
    color: bool = True,
) -> int:
    c = _Colors(color)
    config_path = output

    print(c.bold("Echo quickstart"))
    print(c.dim("─" * 60))

    if force or not config_path.exists():
        setup_rc = run_setup(
            output=config_path,
            non_interactive=non_interactive,
            color=color,
        )
        if setup_rc != 0:
            return setup_rc
    else:
        print(f"config: reusing {config_path}")

    print()
    print(c.bold("doctor"))
    doctor_rc = run_doctor(config_path=config_path, color=color)
    if doctor_rc != 0:
        print()
        print(c.red("quickstart stopped because doctor reported failures"))
        return doctor_rc

    print()
    print(c.green(f"ready: http://{host}:{port}"))
    if serve:
        return run_serve(
            config_path=config_path,
            host=host,
            port=port,
            learn_interval_s=learn_interval_s,
            color=color,
        )

    print(f"start: python -m runtime serve --config {config_path} --host {host} --port {port}")
    print("tip: add --serve to quickstart to start it immediately")
    return 0


def run_bb(args: argparse.Namespace) -> int:
    """Cross-process blackboard CLI.

    The durable blackboard (``ECHO_BLACKBOARD_DB``) is a file, so a
    separate agent *process* — a worktree worker, a remote runner, even a
    non-Python tool — can read/write the same turn's shared workspace through
    this command. That is the channel that turns ``run_worktree_loop``'s
    already-isolated subprocess workers into *coordinated* distributed agents.
    """
    import os

    db = os.environ.get("ECHO_BLACKBOARD_DB")
    if not db:
        print(
            "  ✗ ECHO_BLACKBOARD_DB not set — the cross-process blackboard "
            "needs a shared DB file path."
        )
        return 2
    turn = getattr(args, "turn", None) or os.environ.get("ECHO_TURN_ID")
    if not turn:
        print("  ✗ turn id required (--turn or ECHO_TURN_ID).")
        return 2

    from runtime.memory.runtime_state.blackboard_store import get_sqlite_blackboard

    bb = get_sqlite_blackboard(db, turn)
    op = getattr(args, "bb_op", None)
    writer = os.environ.get("ECHO_AGENT_ID") or "cli"
    if op == "set":
        bb.write(args.key, args.value, writer=writer)
        print(f"  ✓ {args.key} set")
        return 0
    if op == "get":
        val = bb.read(args.key, None)
        if val is None:
            return 1
        print(val if isinstance(val, str) else json.dumps(val, ensure_ascii=False))
        return 0
    if op == "keys":
        board_keys = bb.keys()  # a SqliteBlackboard method, not a dict
        for key in board_keys:
            print(key)
        return 0
    if op == "snapshot":
        print(json.dumps(bb.snapshot(), ensure_ascii=False, indent=2))
        return 0
    print("  Usage: python -m runtime bb {set|get|keys|snapshot} [--turn ID]")
    return 2


def run_skills(args: argparse.Namespace, *, color: bool = True) -> int:
    from runtime.platform.plugins.skill_market import SkillMarket

    market = SkillMarket()
    op = getattr(args, "skills_op", None)

    if op == "list":
        installed = market.list_installed()
        if not installed:
            print("  No skills installed.")
            print("  Search: python -m runtime skills search <query>")
            return 0
        for s in installed:
            tags = f" [{', '.join(s.tags)}]" if s.tags else ""
            print(f"  · {s.name:<25s} v{s.version:<8s} {s.description[:40]}{tags}")
        return 0

    if op == "search":
        results = market.search(args.query, limit=args.limit)
        if not results:
            print(f"  No skills found for '{args.query}'.")
            return 0
        for r in results:
            status = "✓ installed" if r.installed else "  available"
            print(f"  · {r.name:<25s} {status:<14s} {r.description[:40]}")
        return 0

    if op == "install":
        src = str(args.name)
        # An agentskills.io source (local dir or GitHub URL) installs via the
        # standard installer behind the safety gate; a bare name is a
        # marketplace skill.
        is_standard = (
            src.startswith(("http://", "https://"))
            or src.endswith(".git")
            or Path(src).expanduser().is_dir()
        )
        if is_standard:
            from runtime.memory.skills_lib.agentskills import install_from_source

            r = install_from_source(
                src,
                allow_dangerous=bool(getattr(args, "allow_dangerous", False)),
                overwrite=bool(getattr(args, "overwrite", False)),
            )
            if r.ok:
                note = (
                    f" (⚠ {len(r.findings)} safety finding(s), via --allow-dangerous)"
                    if r.dangerous
                    else ""
                )
                print(f"  ✓ installed '{r.name}'{note}")
                return 0
            print(f"  ✗ {r.error}")
            for f in r.findings:
                print(f"      ! {f.file}:{f.line} — {f.reason}")
            return 1
        result = market.install(args.name)
        icon = "✓" if result.status in ("installed", "updated") else "✗"
        print(f"  {icon} {result.message}")
        return 0 if result.status in ("installed", "updated", "already_installed") else 1

    if op == "uninstall":
        ok = market.uninstall(args.name)
        icon = "✓" if ok else "✗"
        print(f"  {icon} {'Uninstalled ' + args.name if ok else args.name + ' not found'}")
        return 0 if ok else 1

    if op == "info":
        info = market.info(args.name)
        if info is None:
            print(f"  Skill '{args.name}' not found.")
            return 1
        for k, v in info.items():
            if k == "skill_md":
                print(f"  {k}:")
                for line in str(v)[:500].split("\n"):
                    print(f"    {line}")
            elif isinstance(v, dict):
                print(f"  {k}: {json.dumps(v, ensure_ascii=False)}")
            else:
                print(f"  {k}: {v}")
        return 0

    if op == "publish":
        result = market.publish(args.path)
        status = result.get("status", "unknown")
        if status == "ready":
            print(f"  ✓ {result.get('message', '')}")
        else:
            print(f"  ✗ {result.get('message', 'Unknown error')}")
        return 0 if status == "ready" else 1

    if op == "lint":
        from runtime.memory.skills_lib.agentskills import (
            scan_skill_safety,
            validate_skill_dir,
        )

        ok, name, _desc, err = validate_skill_dir(Path(args.path))
        if not ok:
            print(f"  ✗ not agentskills.io-conformant: {err}")
            return 1
        findings = scan_skill_safety(Path(args.path))
        if findings:
            print(f"  ⚠ '{name}' is conformant but has {len(findings)} safety finding(s):")
            for f in findings:
                print(f"      ! {f.file}:{f.line} — {f.reason}: {f.excerpt}")
            return 1
        print(f"  ✓ '{name}' is agentskills.io-conformant and clean")
        return 0

    print("  Usage: python -m runtime skills {list|search|install|uninstall|info|publish|lint}")
    return 2


def run_plugins(args: argparse.Namespace, *, color: bool = True) -> int:
    from runtime.platform.plugins.plugin_loader import PluginLoader

    loader = PluginLoader()
    op = getattr(args, "plugins_op", None)

    if op == "list":
        plugins = loader.plugins
        if not plugins:
            print("  No plugins loaded.")
            print("  Discover: python -m runtime plugins discover")
            return 0
        for name, pi in plugins.items():
            state = pi.state.value
            hooks = ", ".join(pi.manifest.subscribes) if pi.manifest.subscribes else "none"
            print(f"  · {name:<25s} v{pi.manifest.version:<8s} [{state}] hooks=[{hooks}]")
        return 0

    if op == "discover":
        discovered = loader.discover()
        if not discovered:
            print("  No plugins found in ~/.echo/plugins/")
            print(
                "  Create a plugin: https://github.com/dengdenghua/echo-agent/blob/main/docs/plugins.md"
            )
            return 0
        print(f"  Found {len(discovered)} plugin(s):")
        for name in discovered:
            print(f"    · {name}")
        print("  Load: python -m runtime plugins load <name>")
        return 0

    if op == "load":
        pi = loader.load(args.name)
        if pi is None:
            print(f"  ✗ Plugin '{args.name}' not found.")
            return 1
        loader.start(args.name)
        hooks = ", ".join(pi.manifest.subscribes) if pi.manifest.subscribes else "none"
        print(f"  ✓ Loaded {pi.manifest.name} v{pi.manifest.version} · hooks=[{hooks}]")
        return 0

    print("  Usage: python -m runtime plugins {list|discover|load}")
    return 2
