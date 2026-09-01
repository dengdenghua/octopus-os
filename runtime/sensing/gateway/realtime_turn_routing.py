"""Turn-routing helpers for the realtime runtime."""

from __future__ import annotations

import re

_CHITCHAT_RE = re.compile(
    r"^\s*("
    r"你好|您好|早上好|晚上好|嗨|哈喽|hello|hi|hey|"
    r"谢谢|多谢|感谢|thx|thanks|"
    r"再见|拜拜|bye|byebye|"
    r"哈+|呵+|嘿+|嘻+|嗯+|哦+|噢+|"
    r"行|好的|好啊|可以|没问题|收到|明白|懂了|"
    r"666+|nice|cool|awesome|"
    r"\?+|!+|。+"
    r")\s*[!。?\.\?\!,~\u3002\uff01\uff1f]*\s*$",
    re.IGNORECASE,
)

_AFFIRM_CHARS = set("好行可以是嗯哦噢哈呵啊唉嘛对呀阿的是的对的没事ok!?。.,~ \t")

_FILE_SIGNAL_RE = re.compile(
    r"(?:[Mm]akefile|[Dd]ockerfile|[Cc]ontainerfile|[Jj]ustfile|"
    r"[Pp]rocfile|[Rr]akefile|[Gg]emfile|[Bb]rewfile|CMakeLists|"
    r"README|LICENSE|CHANGELOG|\.[a-z]{1,8}\b|[A-Za-z]:[\\/]|"
    r"/[a-zA-Z0-9_.-]|前\s*\d+\s*行|头\s*\d+\s*行|最后\s*\d+\s*行|"
    r"first\s+\d+\s+lines|last\s+\d+\s+lines)",
    re.IGNORECASE,
)

_KNOWLEDGE_QA_RE = re.compile(
    r"^("
    r".*(?:是什么|是啥|是什么意思|是何|什么意义)\s*[？?。!]*|"
    r"(?:今天)?(?:天气|心情|感觉)(?:怎么样|如何|咋样)\s*[？?。!]*|"
    r"(?:给我|帮我|跟我)?(?:讲|说|来)(?:一?个|一?段|点).*|"
    r".*(?:等于几|等于多少|是多少)\s*[？?。!]*|"
    r".*的(?:时间|空间)复杂度\s*[？?。!]*"
    r")$",
    re.IGNORECASE,
)

_NO_TOOL_DIRECTIVE_RE = re.compile(
    r"("
    r"(?:不要|不需要|无需|不用|别).{0,16}"
    r"(?:调用|使用|执行|打开|启动).{0,16}"
    r"(?:工具|外部工具|浏览器|桌面|搜索|联网|截图)|"
    r"(?:不要|不需要|无需|不用|别).{0,16}"
    r"(?:工具|外部工具|浏览器|桌面|搜索|联网|截图)|"
    r"\b(?:do\s+not|don't|dont|without|no)\s+"
    r"(?:(?:call|use|run|open)\s+)?"
    r"(?:tools?|external\s+tools?|browser|desktop|search|web|screenshot)\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_DIRECT_REPLY_RE = re.compile(
    r"("
    r"只(?:用|需|要)?(?:一句话|回复|返回|回答)|"
    r"仅(?:用|需|要)?(?:一句话|回复|返回|回答)|"
    r"一句话(?:回复|回答|说明)?|"
    r"直接(?:回复|回答|返回)|"
    r"\b(?:only\s+reply|reply\s+only|answer\s+only|one\s+sentence)\b"
    r")",
    re.IGNORECASE,
)

_TOOL_INTENT_RE = re.compile(
    r"("
    r"搜索|搜一下|查一下|找一下|找到|调研|研究报告|市场研究|行业报告|竞品分析|"
    r"读取?|看一下.{0,10}文件|打开.{0,10}文件|列(出|一下)|编辑|修改|"
    r"运行|执行|新建|创建|删除|更新|"
    r"todo|计划|规划|任务|"
    r"股价|行情|收盘|开盘|涨跌|汇率|报价|新闻|头条|实时|"
    r"比赛|比分|战况|赛果|赛程|湖人|火箭|勇士|凯尔特人|nba|cba|"
    r"记(下|住|得|录)|remember|recall|note|"
    r"以前(说|讲|提)|之前(说|讲|提|跟你)|你还记得|我的偏好|我的(习惯|喜欢)|"
    r"委派|委托|派发|subagent|sub-agent|architect|security\.review|安全审查|"
    r"评估|审查|权衡|对比分析|架构(评估|权衡|分析)|"
    r"\bswarm\b|\bparallel\b|集群|并发|同时(调研|分析|对比)|"
    r"update_soul|revert_soul|list_soul_history|soul\.md|"
    r"回退|\brevert\b|recall_scores|analyze_soul_impact|deep_reflect|deep_evolve|"
    r"深度演化|深度反思|\bself.?eval\b|自我评估|自评|\bevolve\b|演化|进化|"
    r"learn_skill_from_text|apply_skill|list_learned_skills|\bskill\b|"
    r"\btemplate\b|web.?search|grep|cat |ls |mkdir|rm |"
    r"\.(md|py|ts|tsx|js|jsx|json|yaml|yml|txt|csv|log|toml|ini|sh|go|rs)\b|"
    r"\b(?:[Mm]akefile|[Dd]ockerfile|[Cc]ontainerfile|[Jj]ustfile|[Pp]rocfile|"
    r"[Rr]akefile|[Gg]emfile|[Bb]rewfile|CMakeLists|README|LICENSE|CHANGELOG)\b|"
    r"前\s*\d+\s*行|头\s*\d+\s*行|最后\s*\d+\s*行|"
    r"first\s+\d+\s+lines|last\s+\d+\s+lines|"
    r"[A-Za-z]:[\\/][A-Za-z0-9_.\\/-]{2,}|/[a-zA-Z0-9_./-]{2,}|"
    r"https?://|\$\{?[A-Z_]+|commit|pull request|pr \b|"
    r"write.*(to|into)|modify.*file|edit.*file"
    r")",
    re.IGNORECASE,
)

_TOOL_META_RE = re.compile(
    r"(?:调用|使用|执行).{0,8}工具|工具.{0,8}(?:调用|执行|用不了|没用|么|吗)|"
    r"启动了么|启动了吗|看不到.{0,8}(?:过程|进度)|进度",
    re.IGNORECASE,
)

_RUNTIME_SURFACE_RE = re.compile(
    r"@(browser|chrome|computer)\b",
    re.IGNORECASE,
)

_CONTEXT_CONFIRM_RE = re.compile(
    r"^\s*(?:好|好的|可以|行|要|需要|开始|启动|继续|就这个|选这个|对|嗯|ok|yes|go)\s*[。.!！?？]*\s*$",
    re.IGNORECASE,
)

_CONTEXT_TOOL_OFFER_RE = re.compile(
    r"(?:需要我|要我|是否|要不要|可以(?:直接|立刻|现在)?|我(?:现在|来)?|下一步)"
    r".{0,50}(?:启动|继续|开始|跑|执行|调用|搜索|联网|调研|研究|工具|deep[-_ ]?research|web[_-]?search)|"
    r"(?:启动|继续|开始|跑一轮|执行|调用).{0,50}"
    r"(?:调研|搜索|研究|工具|deep[-_ ]?research|web[_-]?search)|"
    r"(?:deep[-_ ]?research|web[_-]?search|工具调用)",
    re.IGNORECASE | re.DOTALL,
)


def _is_short_chitchat(text: str) -> bool:
    s = (text or "").strip()
    return bool(s) and len(s) <= 3 and all(c in _AFFIRM_CHARS for c in s)


def _has_explicit_non_tool_directive(text: str) -> bool:
    return bool(_NO_TOOL_DIRECTIVE_RE.search(text or ""))


def looks_like_plain_chat(goal: str) -> bool:
    """Return true for turns that are safe to answer without tools."""
    g = (goal or "").strip()
    if not g:
        return False
    if _has_explicit_non_tool_directive(g):
        return True
    if _DIRECT_REPLY_RE.search(g) and not _FILE_SIGNAL_RE.search(g):
        return True
    if _CHITCHAT_RE.match(g) or _is_short_chitchat(g):
        return True
    return bool(_KNOWLEDGE_QA_RE.match(g) and not _FILE_SIGNAL_RE.search(g))


def looks_like_tool_intent(goal: str) -> bool:
    """Return true when a turn should enter tool-capable execution."""
    g = (goal or "").strip()
    if not g:
        return False
    if looks_like_plain_chat(g):
        return False
    if _RUNTIME_SURFACE_RE.search(g):
        return True
    if _TOOL_META_RE.search(g):
        return True
    return bool(_TOOL_INTENT_RE.search(g))


def _message_text(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    text = message.get("text")
    return text if isinstance(text, str) else ""


def _last_assistant_message_text(conversation_messages: object) -> str:
    if not isinstance(conversation_messages, list):
        return ""
    for message in reversed(conversation_messages[-8:]):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or message.get("type") or "").lower()
        if role in {"assistant", "ai"}:
            text = _message_text(message).strip()
            if text:
                return text
    return ""


def looks_like_contextual_tool_followup(
    goal: str,
    conversation_messages: list[dict[str, object]] | None = None,
) -> bool:
    """Detect short follow-ups that confirm a prior tool/research offer."""
    g = (goal or "").strip()
    if not g:
        return False
    if looks_like_tool_intent(g):
        return True
    last_assistant = _last_assistant_message_text(conversation_messages or [])
    if not last_assistant or not _CONTEXT_TOOL_OFFER_RE.search(last_assistant):
        return False
    if _CONTEXT_CONFIRM_RE.match(g):
        return True
    return len(g) <= 80 and not looks_like_plain_chat(g)


def local_non_tool_reply(goal: str) -> str | None:
    """Last-resort reply when no model router exists for simple chat."""
    g = (goal or "").strip()
    if not g:
        return None
    if _CHITCHAT_RE.match(g) or _is_short_chitchat(g):
        if re.search(r"(谢谢|多谢|感谢|thx|thanks)", g, re.IGNORECASE):
            return "不用客气，我在。"
        if re.search(r"(再见|拜拜|bye|byebye)", g, re.IGNORECASE):
            return "回头见。"
        return "你好，我在。"
    return "当前没有可用的对话模型路由，不能可靠回答这个非工具问题。请启用完整 LLM planner 后重试。"


__all__ = [
    "local_non_tool_reply",
    "looks_like_contextual_tool_followup",
    "looks_like_plain_chat",
    "looks_like_tool_intent",
]
