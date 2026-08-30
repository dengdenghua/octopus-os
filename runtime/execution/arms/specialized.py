from __future__ import annotations

from runtime.core.graph_runtime import GraphRuntime
from runtime.platform.models import ArmId, SkillId

from .base import Worker

_CODE_WRITE: list[SkillId] = [
    SkillId("write_text_file"),
    SkillId("append_text_file"),
    SkillId("edit_text_file"),
]

_CODE_GIT_LOCAL: list[SkillId] = [
    SkillId("git_status"),
    SkillId("git_diff"),
    SkillId("git_log"),
    SkillId("git_add"),
    SkillId("git_commit"),
    SkillId("git_branch"),
]

_CODE_GIT_NETWORK: list[SkillId] = [
    SkillId("git_push"),
    SkillId("git_pull"),
    SkillId("git_checkout"),
    SkillId("git_stash"),
    SkillId("git_create_pr"),
]

_CODE_EXEC: list[SkillId] = [
    SkillId("exec_shell"),
    SkillId("background_exec"),
    SkillId("read_background_output"),
    SkillId("read_shell_output"),
    SkillId("kill_background_exec"),
    SkillId("kill_shell"),
]

_CODE_QUALITY: list[SkillId] = [
    SkillId("run_tests"),
    SkillId("lint_check"),
    SkillId("format_code"),
]

_SEARCH_SKILLS: list[SkillId] = [
    SkillId("web_search"),
    SkillId("rag_lookup"),
    SkillId("summarize"),
]

_FILE_SKILLS: list[SkillId] = [
    SkillId("read_file"),
    SkillId("list_dir"),
    SkillId("stat_file"),
]


def make_code_arm(
    runtime: GraphRuntime,
    *,
    enable_exec: bool = False,
    enable_git_network: bool = False,
    enable_quality: bool = True,
) -> Worker:
    skills: list[SkillId] = [*_CODE_WRITE, *_CODE_GIT_LOCAL]
    affinity = ["code", "git", "test"]
    if enable_quality:
        skills.extend(_CODE_QUALITY)
        affinity.append("quality")
    if enable_exec:
        skills.extend(_CODE_EXEC)
        affinity.append("shell")
    if enable_git_network:
        skills.extend(_CODE_GIT_NETWORK)
        affinity.append("network")
    return Worker(
        arm_id=ArmId("code_arm"),
        affinity=affinity,
        allowed_skills=skills,
        runtime=runtime,
    )


def make_search_arm(runtime: GraphRuntime) -> Worker:
    return Worker(
        arm_id=ArmId("search_arm"),
        affinity=["search", "web", "rag"],
        allowed_skills=list(_SEARCH_SKILLS),
        runtime=runtime,
    )


def make_file_arm(runtime: GraphRuntime) -> Worker:
    return Worker(
        arm_id=ArmId("file_arm"),
        affinity=["file", "fs", "io"],
        allowed_skills=list(_FILE_SKILLS),
        runtime=runtime,
    )
