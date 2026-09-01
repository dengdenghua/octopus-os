from __future__ import annotations

from runtime.platform.reach import (
    diagnose_reach,
    platform_collect,
    platform_monitor,
    platform_read,
    platform_search,
)

from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

REACH_SKILL_NAMES = [
    "platform_search",
    "platform_read",
    "platform_collect",
    "platform_monitor",
    "reach_doctor",
]


def register_reach_skills(registry: SkillRegistry) -> int:
    registry.register(
        Skill(
            name="platform_monitor",
            summary="Create a durable recurring monitor for platform queries.",
            description=(
                "Schedule recurring platform_collect runs in Echo' existing cron store. "
                "Args: platform, queries, cron_expression, max_results, output_format, name, "
                "channel_id and thread_id. The result can be managed with list_scheduled_tasks "
                "and cancel_scheduled_task."
            ),
            affinity=["web", "search", "monitor", "scheduling"],
            cost_profile="low",
            trusted_source="builtin://reach/platform_monitor",
            handler=platform_monitor,
            tests=[
                SkillTestCase(
                    name="missing_queries",
                    tier="golden",
                    args={},
                    expect=SkillExpect(schema_keys=["ok", "error", "error_type"]),
                )
            ],
        )
    )
    registry.register(
        Skill(
            name="platform_collect",
            summary="Batch collect platform searches and URLs into JSON or Markdown evidence.",
            description=(
                "Run up to 50 queries and 100 URL reads in one bounded collection. Persists "
                "JSON or Markdown under ~/.echo/data/reach/collections by default. Args: "
                "platform, queries, urls, max_results, output_path, output_format, use_browser. "
                "Use schedule_task with a prompt calling platform_collect for recurring monitoring."
            ),
            affinity=["web", "search", "collection", "monitor"],
            cost_profile="mid",
            trusted_source="builtin://reach/platform_collect",
            handler=platform_collect,
            tests=[
                SkillTestCase(
                    name="missing_inputs",
                    tier="golden",
                    args={},
                    expect=SkillExpect(schema_keys=["error", "items"]),
                )
            ],
        )
    )
    registry.register(
        Skill(
            name="platform_search",
            summary="Search a specific internet platform through native Echo routes.",
            description=(
                "Search one platform through Echo native adapters. Platforms: web, github, "
                "youtube, bilibili, reddit, x, xiaohongshu, douyin, toutiao and doubao. "
                "Uses public APIs where available "
                "and SearXNG site routing otherwise. Args: platform, query, max_results."
            ),
            affinity=["web", "search", "platform"],
            cost_profile="low",
            trusted_source="builtin://reach/platform_search",
            handler=platform_search,
            tests=[
                SkillTestCase(
                    name="missing_query",
                    tier="golden",
                    args={"platform": "reddit", "query": ""},
                    expect=SkillExpect(schema_keys=["error", "results"]),
                )
            ],
        )
    )
    registry.register(
        Skill(
            name="platform_read",
            summary="Read structured content from a supported internet platform.",
            description=(
                "Read a URL using a platform-specific adapter. Public GitHub repositories, "
                "GitHub repositories/issues/pull requests, YouTube metadata/transcripts, "
                "Bilibili videos and RSS feeds are read directly. Reddit, X "
                "and Xiaohongshu return an explicit browser-session handoff when login is needed."
            ),
            affinity=["web", "read", "platform"],
            cost_profile="mid",
            trusted_source="builtin://reach/platform_read",
            handler=platform_read,
            tests=[
                SkillTestCase(
                    name="missing_url",
                    tier="golden",
                    args={"url": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                )
            ],
        )
    )
    registry.register(
        Skill(
            name="reach_doctor",
            summary="Diagnose native internet platform routes and dependencies.",
            description=(
                "Check SearXNG, GitHub, YouTube, Bilibili, RSS and browser-backed platform "
                "routes. Returns backend, availability, login requirement and repair context."
            ),
            affinity=["web", "diagnostic", "platform"],
            cost_profile="low",
            trusted_source="builtin://reach/doctor",
            handler=diagnose_reach,
        )
    )
    return len(REACH_SKILL_NAMES)


__all__ = ["REACH_SKILL_NAMES", "register_reach_skills"]
