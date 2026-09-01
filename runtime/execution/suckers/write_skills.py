from __future__ import annotations

# ╔════════════════════════════════════════════════════════════════════════╗
# ║ write_skills.py · skills catalog + registrars.                        ║
# ║                                                                        ║
# ║   The handler implementations live in ``_write_skills_*.py``           ║
# ║   submodules and are re-imported here so the public API surface (what  ║
# ║   tests and other modules import) is unchanged.  This file keeps only  ║
# ║   the skill-name constants and the ``register_*`` functions.           ║
# ║                                                                        ║
# ║   §1 register_write_skills (the sucker entrypoint)                     ║
# ║   §2 register_git_skills                                               ║
# ║   §3 register_exec_skill                                               ║
# ║   §4 register_git_network_skills                                       ║
# ║   §5 register_code_quality_skills                                      ║
# ╚════════════════════════════════════════════════════════════════════════╝
from ._write_skills_background import (
    _BACKGROUND_PROCESSES,
    _background_execution_policy,
    _background_paths,
    _background_policy_with_result,
    _background_root,
    _BackgroundProcess,
    _probe_process,
    _read_background_metadata,
    _read_background_text,
    _snapshot_background_metadata,
    _write_background_metadata,
    recover_background_processes,
)
from ._write_skills_common import (
    _BACKGROUND_OUTPUT_CAP,
    _DEFAULT_EXEC_TIMEOUT_S,
    _DEFAULT_MAX_BYTES,
    _EXEC_OUTPUT_CAP,
    _ensure_sandbox,
    _error_with_execution_policy,
    _execution_policy_from_result,
    _optional_float,
    _parse_command,
)
from ._write_skills_exec import (
    _background_exec,
    _exec_shell,
    _ipython,
    _kill_background_exec,
    _kill_shell,
    _read_background_output,
    _read_shell_output,
)
from ._write_skills_file import (
    _append_text_file,
    _edit_file,
    _edit_text_file,
    _multi_edit_file,
    _write_text_file,
)
from ._write_skills_git import (
    _git_add,
    _git_branch,
    _git_commit,
    _git_diff,
    _git_log,
    _git_status,
    _run_git,
)
from ._write_skills_git_network import (
    _git_checkout,
    _git_create_pr,
    _git_pull,
    _git_push,
    _git_stash,
)
from ._write_skills_quality import (
    _format_code,
    _lint_check,
    _normalize_quality_paths,
    _run_quality_cmd,
    _run_tests,
)
from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

WRITE_SKILL_NAMES = [
    "write_text_file",
    "append_text_file",
    "edit_text_file",
    "edit_file",
    "multi_edit_file",
]
EXEC_SKILL_NAME = "exec_shell"
GIT_SKILL_NAMES = [
    "git_status",
    "git_diff",
    "git_log",
    "git_add",
    "git_commit",
    "git_branch",
]


def register_write_skills(registry: SkillRegistry) -> int:
    registry.register(
        Skill(
            name="write_text_file",
            description=(
                "用途: 把 UTF-8 文本完整写入一个新文件；首选用于「创建新文件」场景。会受 sandbox_dir 路径校验保护。\n"
                "何时不用: 要修改已有文件请用 edit_file (按唯一片段) 或 multi_edit_file (批量精确替换)；要追加内容用 append_text_file；要写二进制不要用本工具。\n"
                "关键参数: path (必填); content (必填); overwrite (默认 False — 文件已存在时返回错误); max_bytes (默认 1MB)。\n"
                '示例: write_text_file({"path": "notes.md", "content": "# hello\\n", "overwrite": false})'
            ),
            affinity=["file", "write"],
            cost_profile="low",
            trusted_source="skill://public/write_text_file",
            handler=_write_text_file,
            tests=[
                SkillTestCase(
                    name="missing_path_error",
                    tier="golden",
                    args={"path": "", "content": "x"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="append_text_file",
            description=(
                "用途: 在文件末尾追加 UTF-8 文本（不存在则创建）；常用于日志、增量记录、向 markdown 续写。\n"
                "何时不用: 要替换/修改文件中间某段用 edit_file；要整体覆写一个新文件用 write_text_file (overwrite=True)；要在指定位置插入用 edit_file 把上下文一并替换。\n"
                "关键参数: path (必填); content (必填, 自带换行需自己加 \\n); max_bytes (默认 1MB)。\n"
                '示例: append_text_file({"path": "log.txt", "content": "2026-05-29 ok\\n"})'
            ),
            affinity=["file", "write"],
            cost_profile="low",
            trusted_source="skill://public/append_text_file",
            handler=_append_text_file,
            tests=[
                SkillTestCase(
                    name="missing_path_error",
                    tier="golden",
                    args={"path": "", "content": "x"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="edit_text_file",
            description=(
                "用途: 老式宽松版 find-and-replace — 不强制唯一性，按计数替换出现的字面串。适合简单批量改名、注释清理。\n"
                "何时不用: 想要「唯一片段才替换」的安全编辑用 edit_file；要一次改多处不同片段用 multi_edit_file；要写新文件用 write_text_file。\n"
                "关键参数: path / find / replace (均必填); count (默认 -1 = 全部替换, 正数 = 只替换前 N 个)。\n"
                '示例: edit_text_file({"path": "a.py", "find": "foo", "replace": "bar", "count": 1})'
            ),
            affinity=["file", "edit"],
            cost_profile="low",
            trusted_source="skill://public/edit_text_file",
            handler=_edit_text_file,
            tests=[
                SkillTestCase(
                    name="missing_find_error",
                    tier="golden",
                    args={"path": "/tmp/x", "find": ""},  # nosec B108 — test fixture path, not a temp file operation
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="edit_file",
            description=(
                "用途: 安全的精确编辑 — 把 old_string 替换为 new_string，默认要求文件中只出现一次 (避免误改)。改源码首选。\n"
                "何时不用: 要一次性应用多处不同替换用 multi_edit_file (原子化、按序合并)；要写全新文件用 write_text_file；只是末尾追加用 append_text_file；宽松全替换用 edit_text_file。\n"
                "关键参数: path / old_string / new_string (均必填, old≠new); replace_all (默认 False, True 时允许多处匹配并全替换)。\n"
                '示例: edit_file({"path": "a.py", "old_string": "def foo():\\n    pass", "new_string": "def foo():\\n    return 1"})'
            ),
            summary="Replace one exact unique string in a file.",
            affinity=["file", "edit"],
            cost_profile="low",
            trusted_source="skill://public/edit_file",
            handler=_edit_file,
            tests=[
                SkillTestCase(
                    name="missing_old_string_error",
                    tier="golden",
                    args={"path": "/tmp/x", "old_string": ""},  # nosec B108 — test fixture path, not a temp file operation
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="multi_edit_file",
            description=(
                "用途: 一次调用对同一文件原子化应用多个精确替换 (按 edits 顺序累积)；适合重构 / 同时改多处。任一编辑失败整批回滚。\n"
                "何时不用: 只改一处用 edit_file；不同文件分别处理；要一行 find/replace 全文扫荡用 edit_text_file；要新建文件用 write_text_file。\n"
                "关键参数: path (必填); edits (必填, list[{old_string, new_string, replace_all?}], 顺序敏感, 默认每条要求唯一)。\n"
                '示例: multi_edit_file({"path": "a.py", "edits": [{"old_string": "v1", "new_string": "v2"}, {"old_string": "x", "new_string": "y", "replace_all": true}]})'
            ),
            summary="Apply multiple exact string edits to one file.",
            affinity=["file", "edit"],
            cost_profile="low",
            trusted_source="skill://public/multi_edit_file",
            handler=_multi_edit_file,
            tests=[
                SkillTestCase(
                    name="missing_edits_error",
                    tier="golden",
                    args={"path": "/tmp/x", "edits": []},  # nosec B108 — test fixture path, not a temp file operation
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    return 5


def register_git_skills(registry: SkillRegistry) -> int:
    registry.register(
        Skill(
            name="git_status",
            description="Structured `git status` (branch + porcelain file list).",
            affinity=["git", "read"],
            cost_profile="low",
            trusted_source="skill://public/git_status",
            handler=_git_status,
            tests=[
                SkillTestCase(
                    name="missing_repo_error",
                    tier="golden",
                    args={"repo_dir": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="git_diff",
            description="Run `git diff` (optionally --staged or for a single path).",
            affinity=["git", "read"],
            cost_profile="low",
            trusted_source="skill://public/git_diff",
            handler=_git_diff,
            tests=[
                SkillTestCase(
                    name="missing_repo_error",
                    tier="golden",
                    args={"repo_dir": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="git_log",
            description="Recent commits (sha/author/date/subject · structured).",
            affinity=["git", "read"],
            cost_profile="low",
            trusted_source="skill://public/git_log",
            handler=_git_log,
            tests=[
                SkillTestCase(
                    name="missing_repo_error",
                    tier="golden",
                    args={"repo_dir": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="git_add",
            description="Stage explicit paths · rejects -A / '.' / flag injection.",
            affinity=["git", "write"],
            cost_profile="low",
            trusted_source="skill://public/git_add",
            handler=_git_add,
            tests=[
                SkillTestCase(
                    name="missing_paths_error",
                    tier="golden",
                    args={"repo_dir": "/tmp", "paths": []},  # nosec B108 — test fixture path, not a temp file operation
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="git_commit",
            description="Commit staged changes (no auto-add, no amend, no hook skip).",
            affinity=["git", "write"],
            cost_profile="low",
            trusted_source="skill://public/git_commit",
            handler=_git_commit,
            tests=[
                SkillTestCase(
                    name="empty_message_error",
                    tier="golden",
                    args={"repo_dir": "/tmp", "message": ""},  # nosec B108 — test fixture path, not a temp file operation
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="git_branch",
            description="List branches · or create a new one (no switch, no force).",
            affinity=["git", "write"],
            cost_profile="low",
            trusted_source="skill://public/git_branch",
            handler=_git_branch,
            tests=[
                SkillTestCase(
                    name="missing_repo_error",
                    tier="golden",
                    args={"repo_dir": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    return 6


def register_exec_skill(registry: SkillRegistry) -> int:
    recover_background_processes()
    registry.register(
        Skill(
            name=EXEC_SKILL_NAME,
            description=(
                "用途: 执行本地 shell 命令 (无 shell=True、有超时)；最适合编译、打包、文件管线、调用各种 CLI；当没有专门 skill 匹配时的兜底。\n"
                "何时不用: 谷歌/搜资料用 web_search；抓取已知 URL 用 fetch_url (curl/wget 抓回的 HTML 不好解析)；跑 Python 片段用 ipython；改文件用 edit_file/write_text_file；查 git 状态用 git_status。\n"
                "关键参数: command (str 或 list[str], 必填); cwd (可选); timeout_s (默认 60); run_in_background (默认 False, True 时返回 task_id, 配合 read_shell_output / kill_shell)。不要写 `cd dir && command` 或 `2>&1`：本工具不会启动 shell；请把 dir 放入 cwd，stdout/stderr 会分别返回。\n"
                "网络: 沙箱内默认禁止网络访问（pnpm/npm/pip 等需联网的命令会失败，仅模型推理端点可达）。网络有三档：默认禁网；传 allow_network=true 完全开启；若只需安装依赖/拉取代码等开发工具场景，可传 egress_allow_common=true（放行 npm/pip/github/apt 等预置常用域名，其余仍拦截）。\n"
                '示例: exec_shell({"command": "npm test", "cwd": "web", "timeout_s": 120})'
            ),
            affinity=["shell", "exec", "dangerous"],
            cost_profile="mid",
            trusted_source="skill://public/exec_shell",
            handler=_exec_shell,
            tests=[
                SkillTestCase(
                    name="missing_command_error",
                    tier="golden",
                    args={"command": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="ipython",
            description=(
                "用途: 在带超时的子进程里跑一段 Python (当前解释器, 无 REPL 状态保留)；适合数据分析、临时计算、调用 numpy/pandas/json。\n"
                "何时不用: 跑非 Python 命令用 exec_shell；只是数文本用 count_words；改文件用 edit_file 而不是写脚本绕路；要长跑用 background_exec / exec_shell(run_in_background=True)。\n"
                "关键参数: code (必填, 完整 Python 片段, 用 print 才能拿到 stdout); cwd (可选); timeout_s (默认 60)。\n"
                '示例: ipython({"code": "import json; print(json.dumps({\\"x\\": 1}))"})'
            ),
            affinity=["python", "analysis", "exec", "dangerous"],
            cost_profile="mid",
            trusted_source="skill://public/ipython",
            handler=_ipython,
            tests=[
                SkillTestCase(
                    name="missing_code_error",
                    tier="golden",
                    args={"code": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="background_exec",
            description=(
                "Start a LOCAL long-running command in the background and "
                "return immediately with a task_id. Use for dev servers, "
                "watchers, docker compose, and long tests. Poll with "
                "`read_background_output`; stop with `kill_background_exec`."
            ),
            affinity=["shell", "exec", "background", "dangerous"],
            cost_profile="mid",
            trusted_source="skill://public/background_exec",
            handler=_background_exec,
            tests=[
                SkillTestCase(
                    name="missing_command_error",
                    tier="golden",
                    args={"command": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="read_background_output",
            description=(
                "Poll stdout/stderr and status for a command started by `background_exec`."
            ),
            affinity=["shell", "exec", "background", "read"],
            cost_profile="low",
            trusted_source="skill://public/read_background_output",
            handler=_read_background_output,
            tests=[
                SkillTestCase(
                    name="missing_task_id_error",
                    tier="golden",
                    args={"task_id": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="read_shell_output",
            description=(
                "Alias for read_background_output. Poll stdout/stderr and "
                "status for a command started by exec_shell(run_in_background=True) "
                "or background_exec."
            ),
            affinity=["shell", "exec", "background", "read"],
            cost_profile="low",
            trusted_source="skill://public/read_shell_output",
            handler=_read_shell_output,
            tests=[
                SkillTestCase(
                    name="missing_task_id_error",
                    tier="golden",
                    args={"task_id": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="kill_background_exec",
            description="Stop a command started by `background_exec`.",
            affinity=["shell", "exec", "background", "dangerous"],
            cost_profile="low",
            trusted_source="skill://public/kill_background_exec",
            handler=_kill_background_exec,
            tests=[
                SkillTestCase(
                    name="missing_task_id_error",
                    tier="golden",
                    args={"task_id": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="kill_shell",
            description=(
                "Alias for kill_background_exec. Stop a command started by "
                "exec_shell(run_in_background=True) or background_exec."
            ),
            affinity=["shell", "exec", "background", "dangerous"],
            cost_profile="low",
            trusted_source="skill://public/kill_shell",
            handler=_kill_shell,
            tests=[
                SkillTestCase(
                    name="missing_task_id_error",
                    tier="golden",
                    args={"task_id": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    return 7


GIT_NETWORK_SKILL_NAMES = [
    "git_push",
    "git_pull",
    "git_checkout",
    "git_stash",
    "git_create_pr",
]


def register_git_network_skills(registry: SkillRegistry) -> int:
    """Register git network/branch skills · opt-in · dangerous."""
    registry.register(
        Skill(
            name="git_push",
            description="Push commits to remote (never force-pushes).",
            affinity=["git", "network", "dangerous"],
            cost_profile="mid",
            trusted_source="skill://public/git_push",
            handler=_git_push,
            tests=[],
        ),
        verify_tests=False,
    )
    registry.register(
        Skill(
            name="git_pull",
            description="Pull from remote with rebase.",
            affinity=["git", "network", "dangerous"],
            cost_profile="mid",
            trusted_source="skill://public/git_pull",
            handler=_git_pull,
            tests=[],
        ),
        verify_tests=False,
    )
    registry.register(
        Skill(
            name="git_checkout",
            description="Switch branch (auto-stashes dirty state).",
            affinity=["git", "write", "dangerous"],
            cost_profile="mid",
            trusted_source="skill://public/git_checkout",
            handler=_git_checkout,
            tests=[],
        ),
        verify_tests=False,
    )
    registry.register(
        Skill(
            name="git_stash",
            description="Stash push/pop/list/show/drop.",
            affinity=["git", "write"],
            cost_profile="low",
            trusted_source="skill://public/git_stash",
            handler=_git_stash,
            tests=[],
        ),
        verify_tests=False,
    )
    registry.register(
        Skill(
            name="git_create_pr",
            description="Create a GitHub PR using `gh` CLI.",
            affinity=["git", "network", "dangerous"],
            cost_profile="mid",
            trusted_source="skill://public/git_create_pr",
            handler=_git_create_pr,
            tests=[],
        ),
        verify_tests=False,
    )
    return 5


CODE_QUALITY_SKILL_NAMES = ["run_tests", "lint_check", "format_code"]


def register_code_quality_skills(registry: SkillRegistry) -> int:
    """Register code quality skills · lint / test / format."""
    registry.register(
        Skill(
            name="run_tests",
            description="Run project tests (auto-detects pytest/vitest/npm test).",
            affinity=["test", "code", "quality"],
            cost_profile="mid",
            trusted_source="skill://public/run_tests",
            handler=_run_tests,
            tests=[
                SkillTestCase(
                    name="missing_cwd_error",
                    tier="golden",
                    args={"cwd": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="lint_check",
            description=(
                "Run linter (auto-detects ruff/eslint). Returns diagnostics plus safe-fix diff; "
                "pass fix=true to apply safe fixes."
            ),
            affinity=["lint", "code", "quality"],
            cost_profile="low",
            trusted_source="skill://public/lint_check",
            handler=_lint_check,
            tests=[
                SkillTestCase(
                    name="missing_cwd_error",
                    tier="golden",
                    args={"cwd": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="format_code",
            description="Run formatter (auto-detects ruff format/prettier). Default check-only.",
            affinity=["format", "code", "quality"],
            cost_profile="low",
            trusted_source="skill://public/format_code",
            handler=_format_code,
            tests=[
                SkillTestCase(
                    name="missing_cwd_error",
                    tier="golden",
                    args={"cwd": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    return 3


__all__ = [
    # registrars
    "register_write_skills",
    "register_git_skills",
    "register_exec_skill",
    "register_git_network_skills",
    "register_code_quality_skills",
    # skill-name constants
    "WRITE_SKILL_NAMES",
    "EXEC_SKILL_NAME",
    "GIT_SKILL_NAMES",
    "GIT_NETWORK_SKILL_NAMES",
    "CODE_QUALITY_SKILL_NAMES",
    # shared helpers
    "_ensure_sandbox",
    "_execution_policy_from_result",
    "_error_with_execution_policy",
    "_optional_float",
    "_parse_command",
    "_DEFAULT_MAX_BYTES",
    "_DEFAULT_EXEC_TIMEOUT_S",
    "_EXEC_OUTPUT_CAP",
    "_BACKGROUND_OUTPUT_CAP",
    # background machinery
    "_BackgroundProcess",
    "_BACKGROUND_PROCESSES",
    "_background_execution_policy",
    "_background_paths",
    "_background_policy_with_result",
    "_background_root",
    "_probe_process",
    "_read_background_metadata",
    "_read_background_text",
    "_snapshot_background_metadata",
    "_write_background_metadata",
    # file primitives
    "_write_text_file",
    "_append_text_file",
    "_edit_text_file",
    "_edit_file",
    "_multi_edit_file",
    # exec / background / ipython
    "_exec_shell",
    "_background_exec",
    "_read_background_output",
    "_read_shell_output",
    "_kill_background_exec",
    "_kill_shell",
    "_ipython",
    # git core
    "_run_git",
    "_git_status",
    "_git_diff",
    "_git_log",
    "_git_add",
    "_git_commit",
    "_git_branch",
    # git network
    "_git_push",
    "_git_pull",
    "_git_checkout",
    "_git_stash",
    "_git_create_pr",
    # code quality
    "_run_quality_cmd",
    "_run_tests",
    "_normalize_quality_paths",
    "_lint_check",
    "_format_code",
]
