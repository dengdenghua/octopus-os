from __future__ import annotations

from uuid import uuid4

from runtime.core.hearts.gill_pump import GillCache
from runtime.execution.suckers import SkillRegistry
from runtime.memory.hemolymph import ContextComposer
from runtime.memory.journal import InMemoryJournal, journal_context
from runtime.memory.knowledge_graph import KnowledgeGraph
from runtime.platform.models import (
    ArmId,
    CostEntry,
    ExecutionResult,
    ParsedIntent,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.platform.models.execution import ExecutionStatus
from runtime.platform.models.llm import ModelRequest, ModelResponse, ModelRouter
from runtime.protocol.items import TurnParams
from runtime.safety.auth.scope import TenantScope
from runtime.safety.evolution.proposal_ledger import ProposalLedger
from runtime.safety.experiments.prompt_mutator import PromptMutator
from runtime.safety.experiments.prompt_optimizer import PromptVariant
from runtime.safety.recovery.evolution_dataset import EvolutionDatasetBuilder
from runtime.safety.recovery.kg_updater import KGUpdater
from runtime.safety.recovery.recipe_evaluator import (
    RecipeEvaluator,
    RecipeEvaluatorConfig,
)
from runtime.safety.recovery.tenant_scope import (
    AUTHORITATIVE_SCOPE_CONTEXT_KEY,
    authoritative_scope_context,
    trusted_scope_from_user_context,
)
from runtime.safety.recovery.variant_evaluator import collect_variant_stats
from runtime.sensing.gateway.realtime_turn_input import _build_intent

TENANT_A = TenantScope(tenant_id="tenant-a", actor_id="alice")
TENANT_B = TenantScope(tenant_id="tenant-b", actor_id="bob")


class _CaptureRouter(ModelRouter):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def call(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            text="<suffix>verify missing tools before continuing</suffix>",
            input_tokens=1,
            output_tokens=1,
            cost=CostEntry(),
            model=request.model,
            provider="test",
        )


def _step(
    *,
    canary: str,
    status: ExecutionStatus = "failed",
    sucker_id: str = "missing_tool",
    output: object = None,
) -> Step:
    call = ToolCall(
        caller="arms/test",
        sucker_id=sucker_id,
        args={"canary": canary},
    )
    return Step(
        step_id=0,
        node_id="n0",
        action=call,
        result=ExecutionResult(
            call_id=call.call_id,
            status=status,
            output=output,
            error_type="missing_tool" if status != "success" else None,
        ),
    )


def _trajectory(
    *,
    arm: str,
    recipe_id: str = "recipe#v1",
    success: bool,
    degraded: bool = False,
    canary: str,
    task_id: TaskId | None = None,
    step_status: ExecutionStatus | None = None,
    sucker_id: str = "missing_tool",
    output: object = None,
) -> Trajectory:
    status = step_status or ("success" if success and not degraded else "failed")
    return Trajectory(
        task_id=task_id or TaskId(uuid4()),
        arm_id=ArmId(arm),
        strategy_id="native",
        recipe_id=recipe_id,
        steps=[
            _step(
                canary=canary,
                status=status,
                sucker_id=sucker_id,
                output=output,
            )
        ],
        outcome=TrajectoryOutcome(success=success, degraded=degraded),
    )


def _write(
    journal: InMemoryJournal,
    trajectory: Trajectory,
    scope: TenantScope | None,
) -> None:
    if scope is None:
        journal.write_trajectory(trajectory)
        return
    with journal_context(
        tenant_id=scope.tenant_id,
        owner_actor_id=scope.actor_id,
    ):
        journal.write_trajectory(trajectory)


def _mixed_journal() -> InMemoryJournal:
    journal = InMemoryJournal()
    _write(
        journal,
        _trajectory(arm="A-clean", success=True, canary="A_CLEAN"),
        TENANT_A,
    )
    _write(
        journal,
        _trajectory(
            arm="A-degraded",
            success=True,
            degraded=True,
            canary="TENANT_A_MISSING_CANARY",
        ),
        TENANT_A,
    )
    _write(
        journal,
        _trajectory(arm="A-failed", success=False, canary="TENANT_A_FAIL_CANARY"),
        TENANT_A,
    )
    _write(
        journal,
        _trajectory(arm="B-failed", success=False, canary="TENANT_B_SECRET_CANARY"),
        TENANT_B,
    )
    _write(
        journal,
        _trajectory(arm="legacy-failed", success=False, canary="LEGACY_CANARY"),
        None,
    )
    return journal


def _user_prompt(router: _CaptureRouter) -> str:
    request = router.requests[-1]
    return next(message.content for message in request.messages if message.role == "user")


def test_prompt_mutator_never_sends_other_tenant_args_to_model() -> None:
    journal = _mixed_journal()
    router = _CaptureRouter()
    proposal = PromptMutator(router=router, model="test/model").propose(
        PromptVariant(name="base"),
        journal,
        max_samples=10,
        scope=TENANT_A,
    )

    assert proposal is not None
    prompt = _user_prompt(router)
    assert "TENANT_A_MISSING_CANARY" in prompt
    assert "TENANT_A_FAIL_CANARY" in prompt
    assert "TENANT_B_SECRET_CANARY" not in prompt
    assert "LEGACY_CANARY" not in prompt
    assert "A_CLEAN" not in prompt


def test_process_global_mutator_reads_only_legacy_failures() -> None:
    journal = _mixed_journal()
    router = _CaptureRouter()
    proposal = PromptMutator(router=router, model="test/model").propose(
        PromptVariant(name="base"),
        journal,
        max_samples=10,
    )

    assert proposal is not None
    prompt = _user_prompt(router)
    assert "LEGACY_CANARY" in prompt
    assert "TENANT_A_MISSING_CANARY" not in prompt
    assert "TENANT_B_SECRET_CANARY" not in prompt


def test_context_composer_cache_and_history_are_tenant_isolated() -> None:
    journal = _mixed_journal()
    composer = ContextComposer(
        registry=SkillRegistry(),
        journal=journal,
        gill_cache=GillCache(),
        gill_max_age_s=60,
    )
    intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="x")

    packet_a = composer.compose(intent, relevant_skills=[], scope=TENANT_A)
    packet_b = composer.compose(intent, relevant_skills=[], scope=TENANT_B)
    packet_legacy = composer.compose(intent, relevant_skills=[])
    memory_a = "\n".join(s.content for s in packet_a.segments if s.bucket == "memory")
    memory_b = "\n".join(s.content for s in packet_b.segments if s.bucket == "memory")
    memory_legacy = "\n".join(s.content for s in packet_legacy.segments if s.bucket == "memory")

    assert "arm=A-clean" in memory_a
    assert "arm=B-failed" not in memory_a
    assert "arm=B-failed" in memory_b
    assert "arm=A-clean" not in memory_b
    assert "arm=legacy-failed" in memory_legacy
    assert "arm=A-clean" not in memory_legacy
    assert "arm=A-degraded" in memory_a
    assert "ok=degraded" in memory_a


def test_only_private_server_marker_can_supply_context_scope() -> None:
    assert (
        trusted_scope_from_user_context(
            {"tenant_id": TENANT_A.tenant_id, "owner_actor_id": TENANT_A.actor_id}
        )
        is None
    )
    trusted = trusted_scope_from_user_context(
        {
            AUTHORITATIVE_SCOPE_CONTEXT_KEY: authoritative_scope_context(TENANT_A),
            "tenant_id": TENANT_B.tenant_id,
            "owner_actor_id": TENANT_B.actor_id,
        }
    )
    assert trusted == TENANT_A
    assert trusted is not None and trusted.allow_cross_tenant is False


def test_realtime_boundary_strips_spoofed_private_scope_and_injects_principal() -> None:
    spoofed_context = {
        AUTHORITATIVE_SCOPE_CONTEXT_KEY: authoritative_scope_context(TENANT_B),
    }
    anonymous = _build_intent(
        "x",
        TurnParams(
            threadId="anonymous",
            input=[{"type": "text", "text": "x", "metadata": {"context": spoofed_context}}],
        ),
    )
    assert trusted_scope_from_user_context(anonymous.user_context) is None

    authenticated = _build_intent(
        "x",
        TurnParams(
            threadId="authenticated",
            input=[{"type": "text", "text": "x", "metadata": {"context": spoofed_context}}],
            tenant_id=TENANT_A.tenant_id,
            owner_actor_id=TENANT_A.actor_id,
        ),
        allow_local_workspace_access=True,
    )
    assert trusted_scope_from_user_context(authenticated.user_context) == TENANT_A


def test_positive_consumers_count_only_clean_success_for_one_scope() -> None:
    journal = _mixed_journal()

    dataset = EvolutionDatasetBuilder().build_from_journal_successes(
        journal,
        scope=TENANT_A,
    )
    assert len(dataset.all_examples) == 1
    assert dataset.all_examples[0].metadata["action_chain"] == ["missing_tool"]

    report = RecipeEvaluator(
        journal,
        RecipeEvaluatorConfig(min_uses_to_score=1),
        scope=TENANT_A,
    ).evaluate()
    [score] = report.scores
    assert score.uses == 3
    assert score.successes == 1
    assert score.success_rate == 1 / 3

    [comparison] = collect_variant_stats(journal, scope=TENANT_A)
    [stat] = comparison.variants
    assert stat.uses == 3
    assert stat.successes == 1

    legacy_report = RecipeEvaluator(
        journal,
        RecipeEvaluatorConfig(min_uses_to_score=1),
    ).evaluate()
    [legacy_score] = legacy_report.scores
    assert legacy_score.uses == 1
    assert legacy_score.successes == 0


def test_degraded_task_cannot_publish_step_facts_or_completed_strategy() -> None:
    journal = InMemoryJournal()
    degraded_task = TaskId(uuid4())
    degraded = _trajectory(
        arm="degraded-arm",
        success=True,
        degraded=True,
        canary="KG_DEGRADED_SECRET",
        task_id=degraded_task,
        step_status="success",
        sucker_id="web_search",
        output={
            "query": "KG_DEGRADED_SECRET",
            "backend": "test",
            "results": [{"title": "secret", "url": "https://tenant-a.invalid/secret"}],
        },
    )
    with journal_context(
        tenant_id=TENANT_A.tenant_id,
        owner_actor_id=TENANT_A.actor_id,
    ):
        journal.write_step(
            task_id=degraded_task,
            arm_id=degraded.arm_id,
            step=degraded.steps[0],
        )
        journal.write_trajectory(degraded)
        journal.write_trajectory(
            _trajectory(
                arm="clean-arm",
                success=True,
                canary="clean",
                recipe_id="clean-recipe",
            )
        )

    kg = KnowledgeGraph()
    report = KGUpdater(journal, kg, scope=TENANT_A).update()

    assert report.triples_proposed == 1
    assert kg.query(subject="query:KG_DEGRADED_SECRET") == []
    assert kg.query(subject="degraded-arm", predicate="completed_strategy") == []
    assert len(kg.query(subject="clean-arm", predicate="completed_strategy")) == 1


def test_ledger_degraded_success_is_not_a_positive_example(tmp_path) -> None:
    ledger_path = tmp_path / "proposal-ledger.jsonl"
    ledger = ProposalLedger(ledger_path)
    ledger.propose(
        kind="turn_success",
        description="clean",
        metadata={"goal": "clean goal", "degraded": False},
        scope=TENANT_A,
    )
    ledger.propose(
        kind="turn_success",
        description="degraded",
        metadata={"goal": "degraded goal", "outcome": "pass_degraded"},
        scope=TENANT_A,
    )

    dataset = EvolutionDatasetBuilder().build_from_ledger_successes(
        ledger_path=ledger_path,
        scope=TENANT_A,
    )
    assert [example.task_input for example in dataset.all_examples] == ["clean goal"]

