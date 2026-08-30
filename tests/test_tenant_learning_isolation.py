from __future__ import annotations

from uuid import uuid4

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.memory.journal import InMemoryJournal, journal_context
from runtime.memory.knowledge_graph import KnowledgeGraph
from runtime.memory.learning.experience_ledger import ExperienceLedger
from runtime.memory.learning.promotion_applier import PromotionApplier
from runtime.memory.learning.review_queue import ReviewQueue
from runtime.platform.models import (
    ArmId,
    ExecutionResult,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path
from runtime.safety.evolution.proposal_ledger import ProposalLedger
from runtime.safety.recovery.kg_updater import KGUpdater
from runtime.safety.recovery.memory_consolidator import (
    ConsolidatorConfig,
    MemoryConsolidator,
)
from runtime.safety.recovery.recipe_evaluator import (
    RecipeEvaluator,
    RecipeEvaluatorConfig,
)
from runtime.safety.recovery.rule_extractor import ExtractorConfig, RuleExtractor
from runtime.safety.recovery.skill_forge import ForgeConfig, SkillForge
from runtime.safety.recovery.variant_evaluator import collect_variant_stats
from runtime.safety.recovery.workflow_rewriter import RewriterConfig, WorkflowRewriter

TENANT_A = TenantScope(tenant_id="tenant-a", actor_id="alice")
TENANT_B = TenantScope(tenant_id="tenant-b", actor_id="bob")
CROSS_TENANT = TenantScope(
    tenant_id="ops",
    actor_id="operator",
    allow_cross_tenant=True,
)


def _step(index: int, skill_name: str, *, success: bool = True) -> Step:
    call = ToolCall(caller="arms/code_arm", sucker_id=skill_name, args={})
    return Step(
        step_id=index,
        node_id=f"n{index}",
        action=call,
        result=ExecutionResult(
            call_id=call.call_id,
            status="success" if success else "failed",
            error_type=None if success else "TenantIsolationFailure",
        ),
    )


def _trajectory(
    sequence: tuple[str, str],
    *,
    recipe_id: str,
    success: bool = True,
) -> Trajectory:
    steps = [_step(index, name, success=success) for index, name in enumerate(sequence)]
    return Trajectory(
        task_id=TaskId(uuid4()),
        arm_id=ArmId("code_arm"),
        strategy_id="tenant_learning",
        recipe_id=recipe_id,
        steps=steps,
        outcome=TrajectoryOutcome(success=success),
    )


def _write_samples(
    journal: InMemoryJournal,
    *,
    scope: TenantScope | None,
    sequence: tuple[str, str],
    recipe_id: str,
    count: int,
) -> list[Trajectory]:
    trajectories = [_trajectory(sequence, recipe_id=recipe_id) for _ in range(count)]
    context = (
        journal_context(
            tenant_id=scope.tenant_id,
            owner_actor_id=scope.actor_id,
        )
        if scope is not None
        else journal_context()
    )
    with context:
        for trajectory in trajectories:
            journal.write_trajectory(trajectory)
    return trajectories


def _registry() -> SkillRegistry:
    registry = SkillRegistry()
    for name in ("a_read", "a_write", "b_read", "b_write", "l_read", "l_write"):
        registry.register(
            Skill(
                name=name,
                trusted_source=f"skill://public/{name}",
                handler=lambda **_kwargs: {"ok": True},
            ),
            verify_tests=False,
        )
    return registry


def _mixed_journal() -> tuple[InMemoryJournal, dict[str, list[Trajectory]]]:
    journal = InMemoryJournal()
    samples = {
        "tenant-a": _write_samples(
            journal,
            scope=TENANT_A,
            sequence=("a_read", "a_write"),
            recipe_id="recipe-a#v1",
            count=3,
        ),
        "tenant-b": _write_samples(
            journal,
            scope=TENANT_B,
            sequence=("b_read", "b_write"),
            recipe_id="recipe-b#v1",
            count=4,
        ),
        "legacy": _write_samples(
            journal,
            scope=None,
            sequence=("l_read", "l_write"),
            recipe_id="recipe-legacy#v1",
            count=5,
        ),
    }
    return journal, samples


def test_explicit_scope_isolates_forge_rewrite_and_consolidation() -> None:
    journal, samples = _mixed_journal()

    [candidate] = SkillForge(
        journal=journal,
        registry=_registry(),
        config=ForgeConfig(min_hits=1),
        scope=TENANT_A,
    ).propose()
    assert candidate.underlying_sequence == ["a_read", "a_write"]
    assert candidate.source_sample_count == 3
    assert set(candidate.source_trajectory_ids) == {
        str(trajectory.trajectory_id) for trajectory in samples["tenant-a"]
    }

    rewrite = WorkflowRewriter(
        journal,
        config=RewriterConfig(new_sequence_min_hits=1),
        scope=TENANT_A,
    ).analyze()
    [proposal] = [item for item in rewrite.proposals if item.kind == "propose_new_rule"]
    assert rewrite.trajectories_scanned == 3
    assert proposal.hit_count == 3
    assert set(proposal.supporting_trajectory_ids) == {
        trajectory.trajectory_id for trajectory in samples["tenant-a"]
    }

    memory_report = MemoryConsolidator(
        journal,
        config=ConsolidatorConfig(min_samples_per_cluster=1),
        scope=TENANT_A,
    ).consolidate()
    [memory] = memory_report.memories_produced
    assert memory_report.trajectories_scanned == 3
    assert memory.trajectories_count == 3
    assert set(memory.source_trajectory_ids) == {
        trajectory.trajectory_id for trajectory in samples["tenant-a"]
    }


def test_unscoped_background_uses_only_legacy_and_cross_tenant_is_explicit() -> None:
    journal, samples = _mixed_journal()

    [legacy_candidate] = SkillForge(journal=journal, registry=_registry()).propose()
    assert legacy_candidate.underlying_sequence == ["l_read", "l_write"]
    assert legacy_candidate.source_sample_count == 5
    assert set(legacy_candidate.source_trajectory_ids) == {
        str(trajectory.trajectory_id) for trajectory in samples["legacy"]
    }

    legacy_rewrite = WorkflowRewriter(
        journal,
        config=RewriterConfig(new_sequence_min_hits=1),
    ).analyze()
    assert legacy_rewrite.trajectories_scanned == 5
    assert {tuple(item.suggested_skill_sequence) for item in legacy_rewrite.proposals} == {
        ("l_read", "l_write")
    }

    legacy_memory = MemoryConsolidator(
        journal,
        config=ConsolidatorConfig(min_samples_per_cluster=1),
    ).consolidate()
    assert legacy_memory.trajectories_scanned == 5
    assert legacy_memory.memories_produced[0].trajectories_count == 5

    cross_report = MemoryConsolidator(
        journal,
        config=ConsolidatorConfig(min_samples_per_cluster=1),
        scope=CROSS_TENANT,
    ).consolidate()
    assert cross_report.trajectories_scanned == 12


def test_rule_recipe_and_variant_helpers_honor_the_same_scope_boundary() -> None:
    journal, _samples = _mixed_journal()
    tenant_a_failure = _trajectory(
        ("a_read", "a_write"),
        recipe_id="recipe-a#v1",
        success=False,
    )
    tenant_b_failure = _trajectory(
        ("b_read", "b_write"),
        recipe_id="recipe-b#v1",
        success=False,
    )
    with journal_context(tenant_id=TENANT_A.tenant_id, owner_actor_id=TENANT_A.actor_id):
        journal.write_trajectory(tenant_a_failure)
    with journal_context(tenant_id=TENANT_B.tenant_id, owner_actor_id=TENANT_B.actor_id):
        journal.write_trajectory(tenant_b_failure)

    scoped_rules = RuleExtractor(
        journal,
        config=ExtractorConfig(min_hits=1),
        scope=TENANT_A,
    ).extract()
    assert scoped_rules.trajectories_scanned == 4
    assert scoped_rules.failure_count == 1
    assert {
        source_id
        for rule in scoped_rules.rules_produced
        for source_id in rule.source_trajectory_ids
    } == {str(tenant_a_failure.trajectory_id)}

    scoped_recipes = RecipeEvaluator(
        journal,
        config=RecipeEvaluatorConfig(min_uses_to_score=1),
        scope=TENANT_A,
    ).evaluate()
    assert scoped_recipes.trajectories_scanned == 4
    assert {score.recipe_id for score in scoped_recipes.scores} == {"recipe-a#v1"}

    scoped_variants = collect_variant_stats(
        journal,
        base_recipe_id="recipe-a",
        scope=TENANT_A,
    )
    assert scoped_variants[0].total_uses == 4
    assert (
        collect_variant_stats(
            journal,
            base_recipe_id="recipe-b",
            scope=TENANT_A,
        )
        == []
    )

    scoped_kg = KGUpdater(journal, KnowledgeGraph(), scope=TENANT_A).update()
    legacy_kg = KGUpdater(journal, KnowledgeGraph()).update()
    assert scoped_kg.events_scanned == 4
    assert scoped_kg.triples_proposed == 3
    assert legacy_kg.events_scanned == 5
    assert legacy_kg.triples_proposed == 5


def test_scoped_forge_blocks_live_promotion_and_partitions_each_tenant(tmp_path) -> None:
    journal = InMemoryJournal()
    _write_samples(
        journal,
        scope=TENANT_A,
        sequence=("a_read", "a_write"),
        recipe_id="recipe-a#v1",
        count=3,
    )
    _write_samples(
        journal,
        scope=TENANT_B,
        sequence=("b_read", "b_write"),
        recipe_id="recipe-b#v1",
        count=3,
    )
    candidate_path = tmp_path / "evolution_candidates.jsonl"
    skill_dir = tmp_path / "forged_skills"
    registry = _registry()
    forge = SkillForge(
        journal=journal,
        registry=registry,
        config=ForgeConfig(
            min_hits=1,
            shadow_runs=1,
            candidate_registry_path=candidate_path,
        ),
        auto_persist_dir=skill_dir,
        scope=TENANT_A,
    )

    result = forge.run()
    tenant_b_result = SkillForge(
        journal=journal,
        registry=registry,
        config=ForgeConfig(
            min_hits=1,
            shadow_runs=1,
            candidate_registry_path=candidate_path,
        ),
        auto_persist_dir=skill_dir,
        scope=TENANT_B,
    ).run()

    assert result.governed
    assert result.promoted == []
    assert tenant_b_result.governed
    assert tenant_b_result.promoted == []
    assert all(not registry.has(name) for name in [*result.governed, *tenant_b_result.governed])
    assert tenant_scoped_path(candidate_path, TENANT_A).is_file()
    assert tenant_scoped_path(candidate_path, TENANT_B).is_file()
    assert tenant_scoped_path(candidate_path, TENANT_A) != tenant_scoped_path(
        candidate_path,
        TENANT_B,
    )
    assert not candidate_path.exists()
    assert forge.auto_persist_dir == tenant_scoped_path(skill_dir, TENANT_A)


def test_promotion_audit_path_is_tenant_partitioned(tmp_path) -> None:
    audit_path = tmp_path / "promotion_audit.json"

    applier = PromotionApplier(
        review_queue=ReviewQueue(tmp_path / "review_queue.json"),
        experience_ledger=ExperienceLedger(tmp_path / "experience.json"),
        proposal_ledger=ProposalLedger(tmp_path / "proposal_ledger.jsonl"),
        audit_path=audit_path,
        scope=TENANT_A,
    )

    assert applier.audit_path == tenant_scoped_path(audit_path, TENANT_A)
    assert applier.audit_path != tenant_scoped_path(audit_path, TENANT_B)

