"""Project OS — a Milestone-driven execution OS for autonomous AI project teams.

Not a chat system and not a tool grab-bag: this is the layer that lets an AI team
*run a whole project to completion*, driven by milestones rather than turns.

Three layers (see the module map):
- L0  model/store  — Project → Milestone → Task DAG (the only global state).
- L1  roles        — PM / Engineer / Research / QA agents (each owns a domain).
- L2  engine       — the execution loop: MS check → assign ready tasks → execute
                     → QA gate → update MS → advance, until every milestone is met.

Built on the cowork primitives: tasks dispatch through ``cowork.async_work`` and
shared state lives on the cowork blackboard, so a project IS a cowork thread that
happens to be milestone-driven.
"""

from runtime.projectos.model import (
    Milestone,
    MilestoneStatus,
    Project,
    Task,
    TaskStatus,
    TaskType,
)

__all__ = [
    "Milestone",
    "MilestoneStatus",
    "Project",
    "Task",
    "TaskStatus",
    "TaskType",
]
