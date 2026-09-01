"""Declarative workflow DSL for the parallel-agent orchestrator.

Design doc: ``docs/architecture/blocks.md`` §2 (orchestration) — this closes
the documented DSH gap "缺少声明式 DSL (YAML/JSON 定义 workflow)".

A workflow is a YAML document:

    name: research-and-report
    max_concurrency: 3
    aggregation_strategy: synthesize
    tasks:
      - id: research_a
        agent: researcher
        prompt: "Investigate authentication"
      - id: research_b
        agent: researcher
        prompt: "Investigate cryptography"
      - id: report
        agent: synthesizer
        prompt: "Synthesize both lanes"
        depends_on: [research_a, research_b]

``parse_workflow_yaml`` validates the document (unique task ids, no dangling
or self ``depends_on``), ``build_dispatch_inputs`` maps it onto the existing
orchestrator's ``DispatchTaskInput``, and ``dispatch_workflow`` runs it. The
DSL is a thin, declarative front-end — execution, contracts, recovery and
observability all stay in ``ParallelAgentOrchestrator``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .models import DispatchTaskInput


class WorkflowTaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    agent: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    priority: int = 0
    write_paths: list[str] = Field(default_factory=list)


class WorkflowSpec(BaseModel):
    """Validated shape of a declarative workflow document."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    description: str = ""
    max_concurrency: int | None = Field(default=None, ge=1)
    aggregation_strategy: str | None = None
    execution_mode: str | None = None
    thread_id: str | None = None
    model_name: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    tasks: list[WorkflowTaskSpec] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate_dependency_graph(self) -> WorkflowSpec:
        ids = [task.id for task in self.tasks]
        seen: set[str] = set()
        for task_id in ids:
            if task_id in seen:
                raise ValueError(f"duplicate task id {task_id!r}")
            seen.add(task_id)
        id_set = set(ids)
        for task in self.tasks:
            for dep in task.depends_on:
                if dep == task.id:
                    raise ValueError(f"task {task.id!r} depends on itself")
                if dep not in id_set:
                    raise ValueError(f"task {task.id!r} depends on unknown task {dep!r}")
        return self


def parse_workflow_dict(data: dict[str, Any]) -> WorkflowSpec:
    """Build a validated :class:`WorkflowSpec` from a parsed mapping."""
    try:
        return WorkflowSpec.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"invalid workflow: {exc}") from exc


def parse_workflow_yaml(path: str | Path) -> WorkflowSpec:
    """Load and validate a workflow YAML document."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: workflow must be a mapping")
    return parse_workflow_dict(raw)


def build_dispatch_inputs(spec: WorkflowSpec) -> list[DispatchTaskInput]:
    """Map a declarative workflow onto the orchestrator's task inputs."""
    return [
        DispatchTaskInput(
            task_id=task.id,
            description=task.prompt,
            subagent_name=task.agent,
            depends_on=list(task.depends_on),
            priority=task.priority,
            write_paths=list(task.write_paths),
        )
        for task in spec.tasks
    ]


def dispatch_workflow(
    orchestrator: Any,
    spec: WorkflowSpec,
    *,
    owner_id: str | None = None,
    **overrides: Any,
) -> Any:
    """Run a declarative workflow on a ``ParallelAgentOrchestrator``.

    ``overrides`` may set any ``dispatch`` keyword (``thread_id``,
    ``model_name``, ``max_concurrency`` …); they win over the workflow
    document. Returns the orchestrator's ``BatchResult``.
    """
    return orchestrator.dispatch(
        build_dispatch_inputs(spec),
        max_concurrency=overrides.pop("max_concurrency", spec.max_concurrency),
        aggregation_strategy=overrides.pop("aggregation_strategy", spec.aggregation_strategy),
        execution_mode=overrides.pop("execution_mode", spec.execution_mode),
        thread_id=overrides.pop("thread_id", spec.thread_id),
        model_name=overrides.pop("model_name", spec.model_name),
        context=overrides.pop("context", spec.context),
        owner_id=owner_id,
        **overrides,
    )


def load_and_dispatch(path: str | Path, orchestrator: Any, **kwargs: Any) -> Any:
    """One-call convenience: parse a workflow file and dispatch it."""
    return dispatch_workflow(orchestrator, parse_workflow_yaml(path), **kwargs)
