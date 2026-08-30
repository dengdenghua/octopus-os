from __future__ import annotations

import ast
import json
import re
from typing import Any

from .response_formatter import _clean_report_text


def _react_action_name(action: Any) -> str:
    if not isinstance(action, str):
        return ""
    match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_\-]*)", action)
    return match.group(1) if match else ""


def _react_action_args(action: Any) -> dict[str, Any]:
    if not isinstance(action, str):
        return {}
    match = re.search(r"\((.*)\)\s*$", action.strip(), flags=re.DOTALL)
    raw = match.group(1).strip() if match else ""
    if not raw:
        return {}
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(raw)
        except (json.JSONDecodeError, ValueError, TypeError, SyntaxError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _react_observation_payload(observation: Any) -> Any:
    if isinstance(observation, (dict, list)):
        return observation
    text = str(observation or "").strip()
    if not text or text == "N/A":
        return ""
    candidates = [text]
    first_brace = min(
        [idx for idx in (text.find("{"), text.find("[")) if idx >= 0],
        default=-1,
    )
    if first_brace >= 0:
        candidates.append(text[first_brace:])
    for candidate in candidates:
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(candidate)
            except (json.JSONDecodeError, ValueError, TypeError, SyntaxError):
                continue
            if isinstance(parsed, (dict, list)):
                return parsed
    return text


def _trajectory_research_evidence(trajectory: Any) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for step in getattr(trajectory, "steps", []) or []:
        action = getattr(step, "action", None)
        result = getattr(step, "result", None)
        if isinstance(action, str):
            tool_name = _react_action_name(action)
            args = _react_action_args(action)
            output = _react_observation_payload(
                getattr(step, "observation", ""),
            )
        else:
            tool_name = str(getattr(action, "sucker_id", "") or "")
            args = getattr(action, "args", {}) or {}
            output = getattr(result, "output", None)
        query = _clean_report_text(args.get("query") if isinstance(args, dict) else "")
        if isinstance(output, list):
            output = {"results": output}
        if isinstance(output, dict):
            if isinstance(output.get("results"), list):
                for item in output["results"]:
                    if not isinstance(item, dict):
                        continue
                    title = _clean_report_text(item.get("title") or item.get("url") or "Source")
                    url = _clean_report_text(item.get("url") or "", max_len=500)
                    snippet = _clean_report_text(
                        item.get("snippet") or item.get("content") or item.get("summary") or "",
                    )
                    key = (url, title)
                    if key in seen:
                        continue
                    seen.add(key)
                    evidence.append(
                        {
                            "tool": tool_name or "source",
                            "query": query,
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                        }
                    )
            elif tool_name == "fetch_url" or output.get("url"):
                url = _clean_report_text(output.get("url") or args.get("url") or "", max_len=500)
                content = _clean_report_text(
                    output.get("content") or output.get("text") or output.get("error") or "",
                )
                title = url or tool_name or "Fetched source"
                key = (url, title)
                if key not in seen:
                    seen.add(key)
                    evidence.append(
                        {
                            "tool": tool_name or "fetch_url",
                            "query": query,
                            "title": title,
                            "url": url,
                            "snippet": content,
                        }
                    )
        elif isinstance(output, str) and output.strip():
            urls = re.findall(r"https?://[^\s)\]>\"']+", output)
            if urls:
                for url in urls[:8]:
                    key = (url, url)
                    if key in seen:
                        continue
                    seen.add(key)
                    evidence.append(
                        {
                            "tool": tool_name or "source",
                            "query": query,
                            "title": url,
                            "url": url,
                            "snippet": _clean_report_text(output),
                        }
                    )
            elif tool_name in {"web_search", "fetch_url", "site_search"}:
                title = query or tool_name or "Tool observation"
                key = ("", title)
                if key not in seen:
                    seen.add(key)
                    evidence.append(
                        {
                            "tool": tool_name or "source",
                            "query": query,
                            "title": title,
                            "url": "",
                            "snippet": _clean_report_text(output),
                        }
                    )
    return evidence[:30]


def _trajectory_tool_lines(trajectory: Any) -> list[str]:
    lines: list[str] = []
    for index, step in enumerate(getattr(trajectory, "steps", []) or [], 1):
        action = getattr(step, "action", None)
        if isinstance(action, str):
            observation = _clean_report_text(
                getattr(step, "observation", ""),
                max_len=220,
            )
            tool = _react_action_name(action) or action.strip() or "tool"
            lines.append(f"{index}. {tool}" + (f" -> {observation}" if observation else ""))
            continue
        try:
            from ..openai_formatting import summarize_step_for_stream as _summarize_step_for_stream

            lines.append(f"{index}. {_summarize_step_for_stream(step)}")
        except (ImportError, AttributeError, TypeError):
            lines.append(f"{index}. {getattr(action, 'sucker_id', 'tool')}")
    return lines
