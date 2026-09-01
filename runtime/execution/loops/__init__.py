from .errors import SafeRepairableAttemptError
from .models import (
    CancelLoopRunRequest,
    CreateLoopRunRequest,
    LoopAttempt,
    LoopMode,
    LoopPolicy,
    LoopRun,
    LoopRunListResponse,
    LoopRunRuntimeStateResponse,
    LoopRunsOverviewResponse,
    LoopRunStatus,
    RestartLoopRunRequest,
    VerifierFinding,
    VerifierResult,
)

_LAZY_EXPORTS = {
    "LoopController": (".controller", "LoopController"),
    "LoopRunDispatcher": (".dispatcher", "LoopRunDispatcher"),
    "LoopRunStore": (".store", "LoopRunStore"),
    "LoopVerifierRegistry": (".verifiers", "LoopVerifierRegistry"),
    "build_default_loop_verifier_registry": (
        ".verifiers",
        "build_default_loop_verifier_registry",
    ),
    "build_loop_run_checkpoint": (".recovery", "build_loop_run_checkpoint"),
    "build_loop_run_resume_prompt": (".recovery", "build_loop_run_resume_prompt"),
    "build_loop_run_resume_proposal": (".recovery", "build_loop_run_resume_proposal"),
    "build_loop_run_findings": (".replay", "build_loop_run_findings"),
    "build_loop_run_replay": (".replay", "build_loop_run_replay"),
    "build_loop_run_replay_case": (".replay", "build_loop_run_replay_case"),
    "build_loop_run_review_score": (".replay", "build_loop_run_review_score"),
    "evaluate_loop_run_replay_case": (".replay", "evaluate_loop_run_replay_case"),
    "build_loop_run_review": (".learning", "build_loop_run_review"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module_name, attr_name = target
    value = getattr(import_module(module_name, __name__), attr_name)
    globals()[name] = value
    return value


__all__ = [
    "CancelLoopRunRequest",
    "CreateLoopRunRequest",
    "LoopAttempt",
    "LoopController",
    "LoopRunDispatcher",
    "LoopMode",
    "LoopPolicy",
    "LoopRun",
    "LoopRunListResponse",
    "LoopRunRuntimeStateResponse",
    "LoopRunsOverviewResponse",
    "RestartLoopRunRequest",
    "SafeRepairableAttemptError",
    "LoopRunStatus",
    "LoopRunStore",
    "LoopVerifierRegistry",
    "VerifierFinding",
    "VerifierResult",
    "build_default_loop_verifier_registry",
    "build_loop_run_checkpoint",
    "build_loop_run_findings",
    "build_loop_run_replay",
    "build_loop_run_replay_case",
    "build_loop_run_resume_prompt",
    "build_loop_run_resume_proposal",
    "build_loop_run_review",
    "build_loop_run_review_score",
    "evaluate_loop_run_replay_case",
]
