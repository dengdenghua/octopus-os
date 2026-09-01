from __future__ import annotations

import sys
import time
from pathlib import Path

from runtime.platform.i18n import _


def _color(s: str, code: str, enabled: bool) -> str:
    if not enabled:
        return s
    return f"\033[{code}m{s}\033[0m"


def _header(i: int, title: str, total: int, color: bool) -> None:
    line = "═" * 60
    print(_color(line, "36", color))
    print(_color(_("cli.tour.chapter_fmt", i=i, total=total, title=title), "1;36", color))
    print(_color(line, "36", color))


def _pause(color: bool) -> None:
    try:
        input(_color(_("cli.tour.pause"), "33", color))
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)


def _ch_suckers(color: bool) -> None:
    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.builtins import register_all

    reg = SkillRegistry()
    register_all(reg)
    names = sorted(reg.all_names())
    print(_("cli.tour.skill_count", n=len(names)))
    print(_("cli.tour.skill_preview", names=", ".join(names[:10])))
    print(
        _color(
            _("cli.tour.suckers_conclusion"),
            "32",
            color,
        )
    )


def _ch_cerebrum(color: bool) -> None:
    from runtime.core.cerebrum import StaticPlanner
    from runtime.core.cerebrum.planner import Rule
    from runtime.platform.models import ParsedIntent, SkillId

    planner = StaticPlanner(
        rules=[
            Rule(
                name="describe",
                intent_types=["task"],
                keywords=["describe"],
                skill_sequence=[
                    SkillId("list_cwd"),
                    SkillId("read_file"),
                    SkillId("count_words"),
                ],
            ),
        ]
    )
    intent = ParsedIntent(
        raw="describe README",
        intent_type="task",
        normalized_goal="describe README",
    )
    graph = planner.plan(intent)
    print(_("cli.tour.cerebrum_nodes", n=len(graph.nodes)))
    for n in graph.nodes:
        print(_("cli.tour.cerebrum_node_detail", node_id=n.node_id, skill_ref=n.skill_ref))
    print(
        _color(
            _("cli.tour.cerebrum_conclusion"),
            "32",
            color,
        )
    )


def _ch_ganglia(color: bool) -> None:
    print(_("cli.tour.ganglia_desc1"))
    print(_("cli.tour.ganglia_desc2"))
    print(_("cli.tour.ganglia_desc3"))
    print(
        _color(
            _("cli.tour.ganglia_conclusion"),
            "32",
            color,
        )
    )


def _ch_journal(color: bool) -> None:
    from runtime.memory.journal import InMemoryJournal
    from runtime.memory.journal.journal_context import journal_context

    InMemoryJournal()
    with journal_context(agent_id="coder", conversation_id="c-1"):
        print(_("cli.tour.journal_desc1"))
    print(_("cli.tour.journal_desc2"))
    print(_("cli.tour.journal_desc3"))
    print(
        _color(
            _("cli.tour.journal_conclusion"),
            "32",
            color,
        )
    )


def _ch_hemolymph(color: bool) -> None:
    from runtime.execution.suckers import SkillRegistry
    from runtime.memory.hemolymph import ContextComposer

    ContextComposer(registry=SkillRegistry())
    print(_("cli.tour.hemolymph_desc1"))
    print(_("cli.tour.hemolymph_desc2"))
    print(
        _color(
            _("cli.tour.hemolymph_conclusion"),
            "32",
            color,
        )
    )


def _ch_immunity(color: bool) -> None:
    from runtime.safety.auth import TrustEngine

    TrustEngine()
    print(_("cli.tour.immunity_desc1"))
    print(_("cli.tour.immunity_desc2"))
    print(
        _color(
            _("cli.tour.immunity_conclusion"),
            "32",
            color,
        )
    )


def _ch_ink(color: bool) -> None:
    from runtime.safety.budget_breaker import CircuitBreaker

    CircuitBreaker(max_errors_per_window=3, cooldown_seconds=30.0)
    print(_("cli.tour.ink_desc1"))
    print(_("cli.tour.ink_desc2"))
    print(_("cli.tour.ink_desc3"))
    print(
        _color(
            _("cli.tour.ink_conclusion"),
            "32",
            color,
        )
    )


def _ch_hearts(color: bool) -> None:
    import tempfile

    from runtime.core.hearts import FileLockCoordinator

    with tempfile.TemporaryDirectory() as d:
        coord = FileLockCoordinator(lock_dir=d, holder_id="demo-node")
        lease = coord.acquire_lease("reflection", ttl=5)
        print(_("cli.tour.hearts_desc1", holder=lease.holder_id, ttl=5, token=lease.fencing_token))
        print(_("cli.tour.hearts_desc2"))
        coord.release_lease(lease)
    print(
        _color(
            _("cli.tour.hearts_conclusion"),
            "32",
            color,
        )
    )


def _ch_camouflage(color: bool) -> None:
    from runtime.safety.experiments import ABSplitter, Variant

    splitter = ABSplitter(
        [
            Variant(name="baseline", weight=1.0, payload=None),
            Variant(name="aggressive", weight=2.0, payload=None),
        ]
    )
    counts: dict[str, int] = {"baseline": 0, "aggressive": 0}
    for i in range(300):
        v = splitter.assign_for(f"t-{i}")
        counts[v.name] += 1
    print(
        _("cli.tour.camouflage_desc1", baseline=counts["baseline"], aggressive=counts["aggressive"])
    )
    print(_("cli.tour.camouflage_desc2"))
    print(
        _color(
            _("cli.tour.camouflage_conclusion"),
            "32",
            color,
        )
    )


def _ch_skin(color: bool) -> None:
    import tempfile

    from runtime.core.nerves.bus import TypedEventBus
    from runtime.sensing.normalize import FileChanged, FileWatcherSensor, SensorManager

    bus = TypedEventBus()
    events: list[FileChanged] = []
    bus.subscribe(FileChanged, events.append)

    with tempfile.TemporaryDirectory() as d:
        mgr = SensorManager(bus=bus)
        mgr.register(
            FileWatcherSensor(
                paths=[d],
                poll_interval_seconds=0.1,
                debounce_ms=0,
                force_polling=True,
            )
        )
        mgr.start_all()
        try:
            time.sleep(0.2)
            (Path(d) / "hello.txt").write_text("world")
            deadline = time.time() + 2
            while time.time() < deadline and not events:
                time.sleep(0.1)
        finally:
            mgr.stop_all()
    print(_("cli.tour.skin_desc1", n=len(events)))
    if events:
        print(
            _("cli.tour.skin_desc2", change=events[0].change_type, name=Path(events[0].path).name)
        )
    print(
        _color(
            _("cli.tour.skin_conclusion"),
            "32",
            color,
        )
    )


CHAPTERS: list[tuple[str, callable]] = [
    ("Suckers · 技能池", _ch_suckers),
    ("Cerebrum · 规划器", _ch_cerebrum),
    ("Ganglia · DAG 执行", _ch_ganglia),
    ("Journal · 事件记录", _ch_journal),
    ("Hemolymph · 上下文编排", _ch_hemolymph),
    ("Immunity · 安全防御", _ch_immunity),
    ("Ink · 断路保护", _ch_ink),
    ("Hearts · HA 协调", _ch_hearts),
    ("Camouflage · A/B 进化", _ch_camouflage),
    ("Skin · 环境感知", _ch_skin),
]


def run_tour(
    *,
    chapters: int | None = None,
    pause: bool = True,
    color: bool = True,
) -> int:
    all_ch = CHAPTERS if chapters is None else CHAPTERS[:chapters]
    total = len(all_ch)
    print()
    print(
        _color(
            _("cli.tour.title"),
            "1;36",
            color,
        )
    )
    print(
        _color(
            _("cli.tour.subtitle", total=total),
            "2",
            color,
        )
    )
    if pause:
        _pause(color)

    for i, (title, fn) in enumerate(all_ch, start=1):
        _header(i, title, total, color)
        try:
            fn(color)
        except KeyboardInterrupt:
            print()
            return 0
        except Exception as e:  # noqa: BLE001
            print(
                _color(_("cli.tour.chapter_failed", error=f"{type(e).__name__}: {e}"), "31", color)
            )
        if pause and i < total:
            _pause(color)

    print()
    print(_color("═" * 60, "32", color))
    print(
        _color(
            _("cli.tour.end"),
            "1;32",
            color,
        )
    )
    print(_color("═" * 60, "32", color))
    return 0
