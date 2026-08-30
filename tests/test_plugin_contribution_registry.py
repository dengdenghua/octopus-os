from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from runtime.execution.jobs import JobHooks, JobOutcome, JobStart, LocalJobRegistry
from runtime.platform.plugins.contribution_registry import ContributionRegistry
from runtime.platform.plugins.plugin_base import ModuleContext


def test_contribution_disposer_is_identity_safe() -> None:
    registry = ContributionRegistry()
    dispose = registry.register(
        kind="workflow",
        name="review",
        owner="first",
        value={"steps": []},
    )

    dispose()
    registry.register(
        kind="workflow",
        name="review",
        owner="second",
        value={"steps": ["new"]},
    )
    dispose()

    record = registry.get("workflow", "review")
    assert record is not None
    assert record.owner == "second"


def test_contribution_names_are_unique_and_owner_cleanup_is_scoped() -> None:
    registry = ContributionRegistry()
    registry.register(kind="agent", name="writer", owner="alpha", value=object())
    registry.register(kind="workflow", name="review", owner="alpha", value=object())
    registry.register(kind="agent", name="reviewer", owner="beta", value=object())

    with pytest.raises(ValueError, match="already registered by alpha"):
        registry.register(kind="agent", name="writer", owner="beta", value=object())

    assert registry.unregister_owner("alpha") == 2
    assert [row.name for row in registry.list()] == ["reviewer"]


@pytest.mark.asyncio
async def test_plugin_owned_jobs_are_cancelled_and_drained_on_cleanup() -> None:
    registry = LocalJobRegistry()
    context = ModuleContext(
        plugin_name="jobsplug",
        plugin_dir="/tmp/jobsplug",
        manifest=SimpleNamespace(),
        jobs_registry=registry,
    )
    settled: asyncio.Future[JobOutcome] = asyncio.get_running_loop().create_future()

    def cancel(_reason: str | None) -> None:
        if not settled.done():
            settled.set_result(JobOutcome(status="killed", detail="plugin unloaded"))

    job_id = context.start_job(
        JobStart(
            kind="plugin-work",
            label="Plugin work",
            run=lambda: JobHooks(cancel=cancel, done=settled),
        )
    )
    assert registry.get(job_id, context.job_owner).owner_session == context.job_owner

    context.cleanup_registrations()
    await context.wait_for_cleanup()

    assert registry.list(context.job_owner) == []

