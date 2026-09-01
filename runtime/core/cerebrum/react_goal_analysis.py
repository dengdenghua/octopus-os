"""Goal-intent and evidence-path analysis for ReAct guards.

Extracted from ``react_guards.py`` (Wave 3, cluster 1): the pure
"what did the user ask for / which files did we actually read"
helpers shared by the guard clusters and by react_convergence /
react_explicit_reads / react_prompt_assembly / react_public_updates.
Leaf module: depends only on re / react_parsing / react_types — must
never import react_guards.
"""

from __future__ import annotations

import re
from typing import Any

from runtime.core.cerebrum.react_parsing import _parse_action
from runtime.core.cerebrum.react_types import ReActStep


def _conversation_message_text(content: Any) -> str:
    """Return visible text from a persisted conversation message payload."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        value = item.get("text") or item.get("content")
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n".join(parts).strip()


def _assistant_left_execution_open(text: str) -> bool:
    """Whether the latest assistant message promises work instead of delivering it.

    This deliberately examines the assistant's lifecycle statement, not the
    wording of the user's follow-up.  Consequently arbitrary nudges can resume
    the same unfinished contract without maintaining an endless synonym list
    for "continue".
    """
    visible = re.sub(r"\s+", " ", str(text or "")).strip()
    if not visible:
        return False
    future = re.search(
        r"(?:^|[。.!！；;]\s*)(?:我)?(?:现在(?:立刻|马上)|"
        r"(?:会|将|先|接下来|下一步|随后|马上|这就|开始|继续|准备)"
        r"(?:会|将|先)?\s*)"
        r"(?:检查|查看|读取|搜索|定位|核对|验证|测试|运行|执行|修改|修复|实现|补充|提交|审查)|"
        # 口语化"先/现在/这次 + 看/列/拆/摸/进入/动手/锁定/读/扫"的预告(排除过去式)。
        # 这些非正式动词不在上面的窄动词表里,导致"我先实际看一下…再下结论"这类
        # 预告句不被识别为"执行未闭环",下一轮 steering 就接不回原目标(thread
        # tPO8mDlhtQev_grzsY1etH,6 轮 announce-only 全漏)。这里不加句首/标点边界,
        # 因为"执行阶段前先进入理解阶段"的"先"就嵌在句中;过去式仍靠"了/过/完"排除。
        r"(?:我)?(?:现在|这次|先|接下来|下一步|马上|这就|开始|继续|准备)"
        r"[^。.!！；;\n]{0,24}?"
        r"(?:看(?!了|过|完|见|似|法|起来|上去)|列出|列出来|拆|拆分|拆解|"
        r"摸清|摸透|摸底|进入|动手|动工|锁定|读(?!了|过|完|取|者)|扫(?!描|把|尾))"
        r"(?!了|过|完)|"
        r"\b(?:i(?:'ll| will)|let me|next i(?:'ll| will))\b[^.!?]{0,80}"
        r"\b(?:inspect|read|search|locate|verify|test|run|execute|edit|fix|implement|commit|review)\b",
        visible,
        re.IGNORECASE,
    )
    unfinished = re.search(
        r"(?:尚未|还没|未完成|没有完成|准备|然后|再|下一步|接下来|"
        r"没动手|没执行|没做|往深挖|深挖|理解阶段|执行阶段前|阶段前)|"
        r"\b(?:not yet|not completed|next|then|after that|ready to)\b",
        visible,
        re.IGNORECASE,
    )
    return bool(future and unfinished)


def _goal_explicitly_cancels_execution(goal: str) -> bool:
    """Recognise explicit cancellation so an old contract is never resurrected."""
    return bool(
        re.search(
            r"(?:不用|无需|不要|别|停止|取消|终止|暂停)(?:再|继续|执行|修改|实现|做|处理)?|"
            r"\b(?:stop|cancel|abort|pause|do not continue|don't continue|never mind)\b",
            str(goal or ""),
            re.IGNORECASE,
        )
    )


def derive_effective_execution_goal(current_goal: str, conversation_messages: Any) -> str:
    """Carry an explicitly requested unfinished execution contract across turns.

    A conversational steering message is not itself proof that the original
    implementation request disappeared.  When the immediately preceding
    assistant message only promised execution, retain the latest actionable
    user request as the guard/tool goal.  Explicit cancellation and a new
    actionable request always win.
    """
    current = str(current_goal or "").strip()
    if not current or _goal_explicitly_cancels_execution(current):
        return current
    if (
        # Colloquial prods are excluded here on purpose: "动手啊" authorises
        # writes but is not a goal, so it must inherit the earlier objective
        # rather than replace it.
        _goal_requests_code_mutation(current, include_colloquial=False)
        or _goal_requests_project_inspection(current)
        or _explicitly_requested_tool_names(current)
    ):
        return current
    if not isinstance(conversation_messages, list):
        return current

    history = [item for item in conversation_messages[:-1] if isinstance(item, dict)]
    if not history:
        return current
    latest_assistant = next(
        (
            _conversation_message_text(item.get("content"))
            for item in reversed(history)
            if item.get("role") == "assistant"
        ),
        "",
    )
    if not _assistant_left_execution_open(latest_assistant):
        return current

    prior_goal = next(
        (
            text
            for item in reversed(history)
            if item.get("role") == "user"
            and (text := _conversation_message_text(item.get("content")))
            and (
                _goal_requests_code_mutation(text)
                or _goal_requests_project_inspection(text)
                or _explicitly_requested_tool_names(text)
            )
        ),
        "",
    )
    if not prior_goal:
        return current
    return f"{prior_goal}\n\n当前用户补充：{current}"


def _inspection_goal_text(goal: str) -> str:
    """Remove negative read-only clauses before finding inspection intent."""
    lowered = (goal or "").lower()
    lowered = re.sub(r"\bread[- ]only\b", " ", lowered)
    lowered = re.sub(
        r"\b(?:do\s+not|don't|must\s+not|never)\b"
        r"[^.!;\n]{0,120}\b(?:files?|code|repo(?:sitory)?|workspace)\b",
        " ",
        lowered,
    )
    return re.sub(
        r"(?:不要|严禁|禁止|不得|不可|不允许)[^。.!！；;\n]{0,120}"
        r"(?:文件|代码|内容|工作区|仓库|项目)",
        " ",
        lowered,
    )


def _goal_requests_project_inspection(goal: str) -> bool:
    lowered = _inspection_goal_text(goal)
    if re.search(
        r"\b(?:list_cwd|read_file)\b|"
        r"\b(?:inspect|read|review|check|open|analy[sz]e)\b[^.!?\n]{0,48}"
        r"\b(?:files?|config(?:uration)?|project|repo(?:sitory)?|workspace|"
        r"codebase|source\s+code)\b|"
        r"(?:检查|查看|读取|分析|调研|审计|梳理|了解|评估|摸清|研究|评价|点评|评审)"
        r"[^。.!！；;\n]{0,48}(?:当前项目|这个项目|项目目录|本地仓库|这个仓库|"
        r"工作区|代码库|这个代码库|前端|界面)|"
        r"(?:^|[\s'\"`(])[^\s'\"`()]+\."
        r"(?:py|ts|tsx|js|jsx|json|ya?ml|toml|md|css|html|go|rs)\b",
        lowered,
    ):
        return True
    return any(
        marker in lowered
        for marker in (
            "本地文件",
            "项目文件",
            "配置文件",
            "源代码",
            "源码",
        )
    )


def _goal_requests_research_lookup(goal: str) -> bool:
    """Whether a non-code (research/chat) goal demands external lookup.

    Complements ``_goal_requests_project_inspection`` (code mode): that one
    recognises project/repo/code vocabulary, this one recognises "go find out
    / verify against the outside world" vocabulary. A pure knowledge question
    ("什么是 X" / "解释一下" / "翻译") is legitimately answered from memory with
    zero tool calls, so only an explicit lookup/verify verb arms the guard that
    consumes this classifier. Deliberately avoids the bare 查/找/验证 roots —
    too common in prose, and 验证 would false-match 验证码.
    """
    lowered = _inspection_goal_text(goal)
    if not lowered:
        return False
    return bool(
        re.search(
            r"(?:查一下|查查|查一查|查证|核查|核实|查询|查资料|上网查|联网查|"
            r"搜索|搜一下|搜搜|检索|调研|找一下|找找|谷歌|百度|"
            r"\b(?:search|look\s*up|find\s*out|research|verify|"
            r"check\s+(?:whether|if|the\s+latest)|fetch|retrieve|scrape)\b)",
            lowered,
            re.IGNORECASE,
        )
    )


# Bare colloquial imperatives ("干活啊", "动手", "改吧"). These authorise writes
# but, unlike the formal verbs, they carry no subject — they are steering
# prods that only mean something relative to an earlier goal. So they count as
# a mutation request for guard purposes, yet must NOT make a steering message
# look self-contained to ``derive_effective_execution_goal``; otherwise
# "动手啊" replaces the original question instead of inheriting it.
_COLLOQUIAL_MUTATION_MARKERS = (
    "干活",
    "动手",
    "开始优化",
    "开始改",
    "去改",
    "改吧",
    "修一下",
    "修掉",
    "全修",
    "都修",
    "落地",
)


def _goal_requests_code_mutation(goal: str, *, include_colloquial: bool = True) -> bool:
    """Whether the user asked code mode to change workspace state.

    This is intentionally keyed off explicit action verbs.  Code mode is
    also used for read-only reviews, so merely mentioning a file/repository
    must not force an edit.

    ``include_colloquial=False`` restricts the match to *self-contained*
    mutation requests, excluding bare prods like "动手啊" that need an earlier
    goal to be meaningful.
    """

    lowered = _inspection_goal_text(goal)
    # A read-only request often names the forbidden action explicitly
    # ("do not modify files" / "不要修改任何文件"). Matching mutation verbs
    # before removing that negated clause turns analysis and research turns
    # into false implementation tasks and blocks their final report behind
    # the write-evidence guard.
    lowered = re.sub(
        r"\b(?:do\s+not|don't|must\s+not|never)\s+"
        r"(?:modify|change|edit|write|create|update|add|remove|delete|patch|fix|refactor)\b",
        " ",
        lowered,
    )
    lowered = re.sub(
        r"\bwithout\s+"
        r"(?:modifying|changing|editing|writing|creating|updating|adding|removing|deleting|patching|fixing|refactoring)\b",
        " ",
        lowered,
    )
    # ``别`` is as common as ``不要`` in spoken prohibitions ("别改吧"), and the
    # short colloquial verbs added to ``chinese_markers`` below must be
    # negatable too — otherwise widening the positive vocabulary would turn
    # "别改吧" into an implementation mandate.
    _negatable = (
        r"修改|改动|更改|重命名|更新|创建|新增|添加|删除|写入|修复|构建|迁移|重构"
        r"|干活|动手|落地|改吧|开始改|去改|修一下|修掉"
    )
    lowered = re.sub(
        r"(?:不要|无需|不需要|禁止|不得|不可|别|"
        rf"不(?={_negatable}))\s*"
        rf"(?:{_negatable})",
        " ",
        lowered,
    )
    # Progress narration is conversation output, not a workspace mutation.
    # Phrases such as “自然更新进展” previously matched the bare 更新 marker
    # and forced read-only analysis turns behind the implementation-write gate.
    lowered = re.sub(
        r"(?:自然|持续|及时|实时|定期)?\s*更新\s*(?:进展|进度|状态|过程|消息|说明)",
        " ",
        lowered,
    )
    lowered = re.sub(
        r"\b(?:update|post|share|provide)\s+(?:the\s+)?"
        r"(?:progress|status)(?:\s+updates?)?\b|"
        r"\b(?:progress|status)\s+updates?\b",
        " ",
        lowered,
    )
    # Todo/checklist bookkeeping mutates the conversation protocol, not the
    # user's workspace.  A goal such as ``用 todo_write 创建一项已完成清单`` used
    # to match the bare ``创建`` marker below, so the implementation-write
    # guard demanded an unrelated file edit after both requested tools had
    # already succeeded.  Remove only the local verb+noun phrase; a second
    # mutation clause (``然后创建 README.md``) remains visible.  The negative
    # lookahead also keeps requests for checklist files/components/pages armed.
    lowered = re.sub(
        r"(?:创建|新增|添加|更新|维护|写入|记录|完成)\s*"
        r"(?:一\s*(?:个|项|份|条|张)?\s*)?"
        r"(?:(?:已完成|完成状态|未完成|待办)\s*)?(?:的\s*)?"
        r"(?:任务\s*)?(?:待办(?:事项)?(?:清单)?|任务清单|清单)"
        r"(?!\s*(?:文件|页面|组件|接口|数据表|模块|功能|系统))",
        " ",
        lowered,
    )
    lowered = re.sub(
        r"\b(?:create|add|update|write|maintain|record|complete)\b\s+"
        r"(?:(?:an?|the)\s+)?(?:(?:completed?|pending)\s+)?"
        r"(?:todo(?:\s+list)?|checklist|task\s+list)\b"
        r"(?!\s+(?:file|page|component|api|database|module|feature|app)\b)",
        " ",
        lowered,
    )
    # Match English actions as whole words. Tool/protocol identifiers are not
    # natural-language mutation requests: the old substring check treated the
    # ``write`` part of ``todo_write`` as a request to edit workspace files.
    english_mutation = re.search(
        r"\b(?:implement|change|modify|rename|update|create|add|remove|delete|"
        r"write|rewrite|overwrite|patch|fix|build|migrate|refactor)\b",
        lowered,
    )
    chinese_markers = (
        "实现",
        "修改",
        "改动",
        "更改",
        "重命名",
        "更新",
        "创建",
        "新增",
        "添加",
        "删除",
        "写入",
        "修复",
        "构建",
        "迁移",
        "重构",
        # Colloquial imperatives carry the same mandate as the formal verbs
        # above and were previously invisible here: "干活啊" and "开始优化"
        # both returned False, so the missing-write guard never fired on turns
        # that produced zero file changes (trn_3348dff0b9e54a99,
        # trn_7e2403ef8c5a42db). They are imperative-only by construction —
        # "优化" alone is excluded because it routinely appears in read-only
        # review prose ("这段代码可以优化"), whereas "开始优化" cannot.
    )
    if english_mutation is not None or any(marker in lowered for marker in chinese_markers):
        return True
    if not include_colloquial:
        return False
    return any(marker in lowered for marker in _COLLOQUIAL_MUTATION_MARKERS)


def _explicitly_requested_tool_names(goal: str) -> set[str]:
    """Extract concrete tool calls the user explicitly required.

    This is deliberately narrower than general tool intent. It only captures
    snake_case identifiers attached to an imperative "call/use" verb, and it
    removes negated requests first so "不要调用 todo_write" never becomes a
    completion requirement.
    """

    text = str(goal or "")
    text = re.sub(
        r"(?:不要|无需|不需要|禁止|不得|不可|别)\s*(?:调用|使用)\s*"
        r"[`「『]?([a-z][a-z0-9_]*_[a-z0-9_]+)[`」』]?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?:do\s+not|don't|never)\s+(?:call|use|invoke)\s+(?:the\s+)?"
        r"[`'\"]?([a-z][a-z0-9_]*_[a-z0-9_]+)[`'\"]?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    names: set[str] = set()
    patterns = (
        r"(?:调用|使用)\s*[`「『]?([a-z][a-z0-9_]*_[a-z0-9_]+)[`」』]?",
        r"\b(?:call|use|invoke)\s+(?:the\s+)?"
        r"[`'\"]?([a-z][a-z0-9_]*_[a-z0-9_]+)[`'\"]?",
    )
    for pattern in patterns:
        names.update(
            match.group(1).lower() for match in re.finditer(pattern, text, flags=re.IGNORECASE)
        )
    return names


def _goal_requires_file_content(goal: str) -> bool:
    lowered = _inspection_goal_text(goal)
    return bool(
        re.search(
            r"\bread_file\b|\b(?:read|inspect|review|check|open|analy[sz]e)\b"
            r"[^.!?\n]{0,48}\b(?:files?|config(?:uration)?|source\s+code)\b|"
            r"(?:^|[\s'\"`(])[^\s'\"`()]+\."
            r"(?:py|ts|tsx|js|jsx|json|ya?ml|toml|md|css|html|go|rs)\b|"
            r"(?:检查|查看|读取|分析)[^。.!！；;\n]{0,48}"
            r"(?:文件|配置|源代码|源码)",
            lowered,
        )
        or any(
            marker in lowered for marker in ("本地文件", "项目文件", "配置文件", "源代码", "源码")
        )
    )


_EXPLICIT_SOURCE_PATH_RE = re.compile(
    r"(?<![\w.-])(?:\.{0,2}/)?(?:[A-Za-z0-9_@.-]+/)*"
    r"[A-Za-z0-9_@.-]+\."
    r"(?:py|tsx|ts|jsx|json|js|ya?ml|toml|md|css|html|go|rs)"
    r"(?::\d+(?::\d+)?)?",
    re.IGNORECASE,
)


def _normalize_evidence_path(value: str) -> str:
    path = str(value or "").strip().strip("`'\"()[]{}.,;，。；")
    path = re.sub(r":\d+(?::\d+)?$", "", path)
    while path.startswith("./"):
        path = path[2:]
    return path.replace("\\", "/").strip("/").lower()


def _explicit_source_paths(goal: str) -> list[str]:
    """Return the concrete source files the user explicitly named."""

    result: list[str] = []
    seen: set[str] = set()
    for match in _EXPLICIT_SOURCE_PATH_RE.finditer(str(goal or "")):
        path = _normalize_evidence_path(match.group(0))
        if not path or path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def _path_evidence_matches(requested: str, observed: str) -> bool:
    requested_norm = _normalize_evidence_path(requested)
    observed_norm = _normalize_evidence_path(observed)
    if not requested_norm or not observed_norm:
        return False
    if "/" in requested_norm:
        return observed_norm == requested_norm or observed_norm.endswith("/" + requested_norm)
    return observed_norm.rsplit("/", 1)[-1] == requested_norm


def _successful_read_paths(steps: list[ReActStep]) -> set[str]:
    """Collect file paths backed by successful read-file receipts."""

    paths: set[str] = set()
    read_tools = {
        "read_file",
        "read_file_range",
        "read_files",
        "read_text_file",
        "bb_read",
    }
    for step in steps:
        actions = step.actions or ([step.action] if step.action else [])
        for index, raw_action in enumerate(actions):
            parsed = _parse_action(raw_action)
            if parsed is None:
                continue
            name, args = parsed
            if name.lower() not in read_tools:
                continue
            if index < len(step.action_results):
                succeeded = bool(step.action_results[index].get("ok"))
            else:
                observation = (step.observation or "").lower()
                succeeded = bool(observation.strip()) and not any(
                    marker in observation
                    for marker in (
                        "未执行观察",
                        "not executed",
                        "工具失败",
                        "工具执行异常",
                        '"error":',
                        "timed_out",
                    )
                )
            if not succeeded:
                continue
            raw_paths: list[str] = []
            for key in ("path", "file_path", "filepath", "file"):
                value = args.get(key)
                if isinstance(value, str):
                    raw_paths.append(value)
            values = args.get("paths") or args.get("files")
            if isinstance(values, list):
                raw_paths.extend(str(value) for value in values if isinstance(value, str))
            paths.update(
                normalized for value in raw_paths if (normalized := _normalize_evidence_path(value))
            )
    return paths


def _successful_write_paths(steps: list[ReActStep]) -> set[str]:
    """Collect file paths backed by successful write/edit receipts.

    A file the model just created or edited is as grounded as a read:
    the content came from the model's own successful write receipt, so
    demanding a redundant read_file blocks create/edit tasks behind the
    inspection-evidence guard.
    """
    from runtime.core.cerebrum._react_parsing_core import _is_code_write_step

    paths: set[str] = set()
    for step in steps:
        if not _is_code_write_step(step):
            continue
        if step.action_results and not any(
            result.get("ok") is True for result in step.action_results
        ):
            continue
        observation = (step.observation or "").lower()
        if not step.action_results and (
            not observation
            or any(
                marker in observation
                for marker in (
                    "未执行观察",
                    "not executed",
                    "工具失败",
                    "工具执行异常",
                    '"error":',
                    "timed_out",
                )
            )
        ):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        raw_path = args.get("path") or args.get("file_path") or args.get("file")
        if isinstance(raw_path, str) and raw_path.strip():
            normalized = _normalize_evidence_path(raw_path.strip())
            if normalized:
                paths.add(normalized)
    return paths


def _final_answer_requests_user_help(
    final_answer: str,
    *,
    allow_short_loose: bool = True,
) -> bool:
    """Whether the final answer is asking the user to do something
    rather than reporting a result.

    The completion guards short-circuit when this returns True so a
    truly blocked agent (missing API key, needs human approval, etc.)
    can hand off cleanly. **The detection has to be conservative** —
    a research report that merely *mentions* "permission" or "token"
    must NOT count as a help request, otherwise the report ends
    prematurely the moment the model emits its first ``Final Answer:``.

    Strategy:
      * Inspect only the tail of the answer (last ~400 chars). Help
        requests live in the closing paragraph, not in body content
        of a long report.
      * Require a marker phrase that pairs an action verb with the
        ask, e.g. ``please confirm`` rather than the bare word
        ``confirm``. Single-word markers (``token``/``permission``)
        are too noisy in technical writing.
      * Or accept a short answer (< 150 chars) with the original
        looser markers — those really are short hand-off messages.
        Fail-closed guards (the hard security guards) pass
        ``allow_short_loose=False`` so only a genuine tight-marker
        hand-off can escape them — a brief report that happens to
        mention ``token``/``权限`` is not a hand-off.
    """

    raw = (final_answer or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    tail = lowered[-400:]

    # Tight markers: action + dependency phrasing. These appear in
    # genuine "I need you to ..." sign-offs and almost never in
    # report prose.
    tight_markers = (
        "需要你",
        "请你",
        "需要用户",
        "用户协助",
        "用户帮忙",
        "无法继续",
        "需要确认",
        "请确认",
        "需要登录",
        "请登录",
        "请提供",
        "请补充",
        "需要您",
        "请您",
        "缺少 api",
        "缺少凭证",
        "缺少权限",
        "需要权限",
        "请授权",
        "需要授权",
        "没有权限",
        "等待批准",
        "等待确认",
        "need your",
        "please confirm",
        "please provide",
        "please grant",
        "please supply",
        "missing api key",
        "missing credential",
        "missing token",
        "permission denied",
        "access denied",
        "please log in",
        "please login",
        "blocked by",
    )
    if any(marker in tail for marker in tight_markers):
        return True

    # Short final → looser markers are still informative because the
    # agent isn't writing a report, just signing off. Threshold is
    # tuned so that a structured Chinese paragraph (which is denser
    # than English by character count) doesn't trip — a real
    # hand-off message is closer to one or two sentences.
    if allow_short_loose and len(raw) < 150:
        loose_markers = (
            "权限",
            "批准",
            "缺少",
            "api key",
            "credential",
            "blocked",
            "permission",
            "login",
            "token",
        )
        if any(marker in tail for marker in loose_markers):
            return True

    return False
