"""
Regression tests for forged-skill template replay · review claim ②.

Before this fix
---------------

``_build_meta_handler`` ignored the original trajectory's
``{nX.key}`` templates and just passed the user's kwargs to every
step + an ``_prev`` hint alongside. That broke any forged composite
whose steps had inter-step data dependencies:

    Original trajectory:
        n0 = read_file(path="/sample")
        n1 = parse_content(content="{n0.content}")   # template

    Forged composite call:
        composite(path="/user/file")

    Old behavior — step 1 got {path: "/user/file", _prev: {...}} which
    parse_content didn't know how to handle (no `path` param, no
    `content` arg — a TypeError or wrong output).

After this fix
--------------

``Step`` carries the raw ``args_template``; ``SkillForge``
captures it onto the candidate; ``_build_meta_handler`` replays
templates against the live ``outputs`` dict using
``resolve_templates``.

    Forged composite call:
        composite(path="/user/file")

    New behavior — step 0 gets {path: "/user/file"} (user override);
    step 1 gets {content: <output of n0>} (template resolved); no
    stray path= leaking into parse_content.

Tests below pin both the new strategy and the MVP fallback for
trajectories without templates.
"""

from __future__ import annotations

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.platform.models import (
    CostEntry,
    ExecutionResult,
    SkillId,
    Step,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
    new_id,
)
from runtime.safety.recovery.skill_forge import (
    ForgeConfig,
    ForgedSkillCandidate,
    SkillForge,
)

# ═══════════════════════════════════════════════════════════
# Helpers for building synthetic trajectories
# ═══════════════════════════════════════════════════════════


def _mk_step(
    step_id: int,
    node_id: str,
    skill_ref: str,
    *,
    args: dict,
    args_template: dict | None = None,
    output: dict | None = None,
) -> Step:
    """Synthesize a Step that looks like GraphRuntime wrote it.

    Separate ``args`` (resolved) and ``args_template`` (raw) · just
    like the real pipeline. Tests that want the pre-fix shape pass
    ``args_template=None``.
    """
    call = ToolCall(
        caller="test",
        sucker_id=SkillId(skill_ref),
        args=dict(args),
    )
    result = ExecutionResult(
        call_id=call.call_id,
        status="success",
        output=output or {"ok": True},
        cost=CostEntry(tokens_in=1, tokens_out=1, usd=0.0),
    )
    return Step(
        step_id=step_id,
        node_id=node_id,
        action=call,
        result=result,
        immune_verdict="allow",
        args_template=args_template or {},
    )


def _mk_trajectory(steps: list[Step]) -> Trajectory:
    return Trajectory(
        task_id=new_id(),
        arm_id="test_arm",
        strategy_id="test",
        steps=steps,
        outcome=TrajectoryOutcome(success=True),
    )


# ═══════════════════════════════════════════════════════════
# Template-replay strategy (happy path)
# ═══════════════════════════════════════════════════════════


class TestTemplateReplay:
    def test_step_templates_captured_on_candidate(self):
        """``_make_candidate`` should harvest ``args_template`` from
        the sample's steps. Without this, the downstream handler
        can't know how to wire inter-step data flow."""
        registry = SkillRegistry()
        # Register two tolerant handlers so _make_candidate's
        # golden-test generation doesn't try to validate shapes.
        registry.register(
            Skill(
                name=SkillId("read"),
                description="r",
                handler=lambda **kw: {"content": "x"},
                idempotent=True,
                trusted_source="skill://public/read",
            )
        )
        registry.register(
            Skill(
                name=SkillId("parse"),
                description="p",
                handler=lambda **kw: {"parsed": True},
                idempotent=True,
                trusted_source="skill://public/parse",
            )
        )

        forge = SkillForge(
            journal=_DummyJournal(trajs=[]),
            registry=registry,
            config=ForgeConfig(min_hits=1, min_success_rate=0.0),
        )

        traj = _mk_trajectory(
            [
                _mk_step(
                    0,
                    "n0",
                    "read",
                    args={"path": "/sample"},
                    args_template={"path": "/sample"},
                    output={"content": "original"},
                ),
                _mk_step(
                    1,
                    "n1",
                    "parse",
                    args={"content": "original"},  # resolved
                    args_template={"content": "{n0.content}"},  # raw
                    output={"parsed": True},
                ),
            ]
        )

        cand = forge._make_candidate(
            signature="read→parse",
            cluster=[traj],
            success_rate=1.0,
        )
        assert cand.step_templates == [
            {"path": "/sample"},
            {"content": "{n0.content}"},
        ]

    def test_replay_routes_prior_output_into_later_step(self):
        """End-to-end: a user calling the forged skill with a
        different ``path`` gets step 1 wired to step 0's actual
        output · NOT to the sample trajectory's output and NOT to
        the user's path leaking through."""
        captured_step1_args: dict = {}

        def _read(path: str) -> dict:
            return {"content": f"content-of-{path}"}

        def _parse(content: str) -> dict:
            # If step 1 gets `path=` leaked in, this handler's strict
            # signature blows up with TypeError · that's the old bug.
            captured_step1_args["content"] = content
            return {"parsed_len": len(content)}

        registry = SkillRegistry()
        registry.register(
            Skill(
                name=SkillId("read"),
                description="r",
                handler=_read,
                idempotent=True,
                trusted_source="skill://public/read",
            )
        )
        registry.register(
            Skill(
                name=SkillId("parse"),
                description="p",
                handler=_parse,
                idempotent=True,
                trusted_source="skill://public/parse",
            )
        )

        forge = SkillForge(
            journal=_DummyJournal(trajs=[]),
            registry=registry,
            config=ForgeConfig(min_hits=1, min_success_rate=0.0),
        )

        # Build candidate with explicit templates.
        candidate = ForgedSkillCandidate(
            candidate_id="read→parse",
            name="read_and_parse",
            underlying_sequence=["read", "parse"],
            path_signature="sig",
            source_trajectory_ids=["t1"],
            source_sample_count=1,
            source_success_rate=1.0,
            step_templates=[
                {"path": "/sample/seed"},  # step 0
                {"content": "{n0.content}"},  # step 1
            ],
        )

        meta = forge._build_meta_handler(candidate)
        out = meta(path="/user/file")  # user parameterizes via path

        # Step 0 ran with USER's path (overlaid on sample template).
        assert out["composite_output"]["n0"] == {
            "content": "content-of-/user/file",
        }
        # Step 1 ran with the RESOLVED template — content from n0's
        # actual output. No path=/user/file leaking into parse.
        assert captured_step1_args == {"content": "content-of-/user/file"}

    def test_user_kwargs_only_seed_step_0(self):
        """If user passes an extraneous kwarg, it must not propagate
        into step 1 · that was the pre-fix leak."""
        call_log: list[tuple[str, dict]] = []

        def _producer(**kw):
            call_log.append(("producer", dict(kw)))
            return {"value": 42}

        def _consumer(**kw):
            call_log.append(("consumer", dict(kw)))
            return {"consumed": kw.get("value")}

        registry = SkillRegistry()
        registry.register(
            Skill(
                name=SkillId("prod"),
                description="p",
                handler=_producer,
                idempotent=True,
                trusted_source="skill://public/prod",
            )
        )
        registry.register(
            Skill(
                name=SkillId("cons"),
                description="c",
                handler=_consumer,
                idempotent=True,
                trusted_source="skill://public/cons",
            )
        )

        candidate = ForgedSkillCandidate(
            candidate_id="cand-pc12",
            name="pc",
            underlying_sequence=["prod", "cons"],
            path_signature="s",
            source_trajectory_ids=["t"],
            source_sample_count=1,
            source_success_rate=1.0,
            step_templates=[
                {"seed": "sample"},
                {"value": "{n0.value}"},
            ],
        )
        forge = SkillForge(
            journal=_DummyJournal(trajs=[]),
            registry=registry,
        )
        meta = forge._build_meta_handler(candidate)
        meta(seed="user_seed", unrelated="should_not_propagate")

        prod_args = call_log[0][1]
        cons_args = call_log[1][1]
        # Step 0 saw both user kwargs (they override sample template).
        assert prod_args["seed"] == "user_seed"
        assert prod_args["unrelated"] == "should_not_propagate"
        # Step 1 saw ONLY its resolved template · not the user leak.
        assert cons_args == {"value": 42}


# ═══════════════════════════════════════════════════════════
# MVP fallback · candidate without captured templates
# ═══════════════════════════════════════════════════════════


class TestErrorHaltFidelity:
    """The meta-handler must match GraphRuntime's "catch + record +
    halt" contract · NOT propagate sub-skill exceptions to the
    caller. Pre-fix the exception bubbled up · making the forged
    composite behave differently from the source trajectory it
    was learned from.
    """

    def test_subskill_exception_is_caught_and_recorded(self):
        """A sub-skill handler that raises must NOT crash the meta
        · it should return ``success=False`` with ``failed_at`` and
        ``error_type`` tags so the caller / shadow tester can tell
        the composite is broken."""
        call_log: list[str] = []

        def _ok(**kw):
            call_log.append("step0")
            return {"ok": True}

        def _boom(**kw):
            call_log.append("step1")
            raise RuntimeError("subskill went boom")

        def _never(**kw):
            call_log.append("step2")
            return {"reached": True}

        registry = SkillRegistry()
        registry.register(
            Skill(
                name=SkillId("ok"),
                description="",
                handler=_ok,
                idempotent=True,
                trusted_source="skill://public/ok",
            )
        )
        registry.register(
            Skill(
                name=SkillId("boom"),
                description="",
                handler=_boom,
                idempotent=True,
                trusted_source="skill://public/boom",
            )
        )
        registry.register(
            Skill(
                name=SkillId("never"),
                description="",
                handler=_never,
                idempotent=True,
                trusted_source="skill://public/never",
            )
        )

        forge = SkillForge(
            journal=_DummyJournal(trajs=[]),
            registry=registry,
        )
        candidate = ForgedSkillCandidate(
            candidate_id="ok_boom_never",
            name="broken_composite",
            underlying_sequence=["ok", "boom", "never"],
            path_signature="s",
            source_trajectory_ids=["t"],
            source_sample_count=1,
            source_success_rate=1.0,
        )
        meta = forge._build_meta_handler(candidate)

        # Must NOT raise · caller gets a dict
        result = meta()

        assert result["success"] is False
        assert result["failed_at"] == 1
        assert result["error_type"] == "RuntimeError"
        assert "boom" in result["error"]
        # Step 2 must NOT have run — halt semantics.
        assert "step2" not in call_log
        assert call_log == ["step0", "step1"]
        # Partial outputs preserved up to the failure point.
        assert "n0" in result["composite_output"]
        assert "n1" not in result["composite_output"]

    def test_template_resolution_error_tagged_distinctly(self):
        """A template that references a nonexistent prior output
        (e.g. sub-skill's output schema changed) should surface as
        ``error_type=TemplateResolutionError`` rather than a generic
        error · lets operators diagnose schema drift separately
        from handler bugs."""
        registry = SkillRegistry()
        registry.register(
            Skill(
                name=SkillId("prod"),
                description="",
                handler=lambda **kw: {"wrong_key": "surprise"},  # no ``value``
                idempotent=True,
                trusted_source="skill://public/prod",
            )
        )
        registry.register(
            Skill(
                name=SkillId("cons"),
                description="",
                handler=lambda value=None: {"consumed": value},
                idempotent=True,
                trusted_source="skill://public/cons",
            )
        )

        forge = SkillForge(
            journal=_DummyJournal(trajs=[]),
            registry=registry,
        )
        candidate = ForgedSkillCandidate(
            candidate_id="prod_cons_bad",
            name="bad_template",
            underlying_sequence=["prod", "cons"],
            path_signature="s",
            source_trajectory_ids=["t"],
            source_sample_count=1,
            source_success_rate=1.0,
            step_templates=[
                {},
                # references prior output's ``value`` key that
                # prod never produces → resolves will raise.
                {"value": "{n0.value}"},
            ],
        )
        meta = forge._build_meta_handler(candidate)
        result = meta()

        assert result["success"] is False
        assert result["failed_at"] == 1
        assert result["error_type"] == "TemplateResolutionError"

    def test_successful_run_reports_success_true(self):
        """Counterpart of the failure test · make sure clean runs
        still get tagged ``success=True`` and ``steps`` matches
        ``total_steps``."""
        registry = SkillRegistry()
        registry.register(
            Skill(
                name=SkillId("a"),
                description="",
                handler=lambda **kw: {"ok": 1},
                idempotent=True,
                trusted_source="skill://public/a",
            )
        )
        registry.register(
            Skill(
                name=SkillId("b"),
                description="",
                handler=lambda **kw: {"ok": 2},
                idempotent=True,
                trusted_source="skill://public/b",
            )
        )

        forge = SkillForge(
            journal=_DummyJournal(trajs=[]),
            registry=registry,
        )
        candidate = ForgedSkillCandidate(
            candidate_id="a_b_happy",
            name="happy_composite",
            underlying_sequence=["a", "b"],
            path_signature="s",
            source_trajectory_ids=["t"],
            source_sample_count=1,
            source_success_rate=1.0,
        )
        meta = forge._build_meta_handler(candidate)
        result = meta()

        assert result["success"] is True
        assert result["steps"] == 2
        assert result["total_steps"] == 2
        assert "failed_at" not in result
        assert "error_type" not in result


class TestMVPFallback:
    def test_no_templates_uses_prev_hint(self):
        """When ``step_templates`` is empty, behavior reverts to the
        pre-fix MVP form: kwargs passed to every step + ``_prev``.
        This path exercises back-compat for trajectories written
        before the ``args_template`` field existed on Step."""
        call_log: list[dict] = []

        def _h1(**kw):
            call_log.append(dict(kw))
            return {"out1": True}

        def _h2(**kw):
            call_log.append(dict(kw))
            return {"out2": True}

        registry = SkillRegistry()
        registry.register(
            Skill(
                name=SkillId("h1"),
                description="",
                handler=_h1,
                idempotent=True,
                trusted_source="skill://public/h1",
            )
        )
        registry.register(
            Skill(
                name=SkillId("h2"),
                description="",
                handler=_h2,
                idempotent=True,
                trusted_source="skill://public/h2",
            )
        )

        candidate = ForgedSkillCandidate(
            candidate_id="cand-xyz1",
            name="composite",
            underlying_sequence=["h1", "h2"],
            path_signature="s",
            source_trajectory_ids=["t"],
            source_sample_count=1,
            source_success_rate=1.0,
            step_templates=[],  # no templates
        )
        forge = SkillForge(
            journal=_DummyJournal(trajs=[]),
            registry=registry,
        )
        meta = forge._build_meta_handler(candidate)
        meta(x="user_x")

        # Both calls received user kwargs · step 1 also got _prev.
        assert call_log[0] == {"x": "user_x"}
        assert call_log[1] == {"x": "user_x", "_prev": {"out1": True}}

    def test_empty_templates_treated_as_no_templates(self):
        """``step_templates=[{}, {}]`` (length match but all empty)
        is equivalent to "no templates" — same fallback path."""
        call_log: list[dict] = []

        def _h(**kw):
            call_log.append(dict(kw))
            return {"ok": True}

        registry = SkillRegistry()
        registry.register(
            Skill(
                name=SkillId("a"),
                description="",
                handler=_h,
                idempotent=True,
                trusted_source="skill://public/a",
            )
        )
        registry.register(
            Skill(
                name=SkillId("b"),
                description="",
                handler=_h,
                idempotent=True,
                trusted_source="skill://public/b",
            )
        )

        candidate = ForgedSkillCandidate(
            candidate_id="cand-xyz1",
            name="c",
            underlying_sequence=["a", "b"],
            path_signature="s",
            source_trajectory_ids=["t"],
            source_sample_count=1,
            source_success_rate=1.0,
            step_templates=[{}, {}],
        )
        forge = SkillForge(
            journal=_DummyJournal(trajs=[]),
            registry=registry,
        )
        meta = forge._build_meta_handler(candidate)
        meta(a="b")

        # MVP fallback: _prev injected for step 1.
        assert "_prev" in call_log[1]


# ═══════════════════════════════════════════════════════════
# Test helpers
# ═══════════════════════════════════════════════════════════


class _DummyJournal:
    """Minimal Journal stub · read-only, returns pre-seeded events.

    SkillForge only needs ``read_by_type`` to collect trajectories.
    We instantiate it in tests that don't exercise that path, so the
    stub can just return an empty list.
    """

    def __init__(self, trajs: list[Trajectory]) -> None:
        self._trajs = trajs

    def read_by_type(self, event_type: str):
        return []
