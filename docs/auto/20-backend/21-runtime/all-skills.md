# All Skills 目录

> 全仓 Skill 目录 · 每个 skill 分 group / atomic 标记 · 决定哪个 arm 能包含它。

**Source**: `runtime/execution/all_skills/`

## Package summary

runtime.execution.all_skills · unified skill catalog.

## Exports

- `ALL_SKILL_IDS`
- `BASE_SKILL_IDS`
- `skill_group`
- `skill_kind`
- `skills_in_group`
- `register_all`
- `register_base`
- `register_subset`
- `register_group`
- `WEB_ONLY_GROUPS`
- `is_known_but_disabled_tool`

## Modules

| Module | Summary |
| --- | --- |
| `agnes-image-generate/scripts/agnes_image_generate.py` | AI image generation skill — dual-provider (Volcano / Agnes). |
| `agnes-video-generate/scripts/agnes_video_generate.py` | AI video generation skill (async) — dual-provider (Volcano / Agnes). |
| `agnes-video-poll/scripts/agnes_video_poll.py` | Standalone skill entry for polling an Agnes video task. |
| `api-doc-gen/scripts/generate_api_doc.py` | Generate OpenAPI 3.0 spec from source code route definitions. |
| `auto-hypothesis-test/scripts/statistical_test_suite.py` | statistical_test_suite.py — 根据数据自动选择统计检验并输出通俗解读 |
| `auto-stat-test/scripts/statistical_test_suite.py` | statistical_test_suite.py — 根据数据自动选择统计检验并输出通俗解读 |
| `cn-finance-data/scripts/api_client.py` | Tushare API 客户端 |
| `code-mentor/scripts/analyze_code.py` | Code Analyzer - Static analysis tool for code review |
| `code-mentor/scripts/complexity_analyzer.py` | Complexity Analyzer - Analyze time and space complexity of algorithms |
| `code-mentor/scripts/run_tests.py` | Test Runner - Execute and format test results |
| `code-safety-audit/scripts/security_scan.py` | security_scan.py — 代码安全扫描工具 |
| `code-vuln-audit/scripts/security_scan.py` | security_scan.py — 代码安全扫描工具 |
| `data-viz-gen/scripts/build_infographic.py` | build_infographic.py — Generate self-contained HTML/SVG infographics from JSON data. |
| `data-viz-renderer/scripts/build_infographic.py` | build_infographic.py — Generate self-contained HTML/SVG infographics from JSON data. |
| `database-inspector/scripts/db_explorer.py` | db_explorer.py — SQLite / PostgreSQL 只读数据库探索工具 |
| `database-scout/scripts/db_explorer.py` | db_explorer.py — SQLite / PostgreSQL 只读数据库探索工具 |
| `email-to-calendar/scripts/tests/test_activity_ops.py` | Tests for utils/activity_ops.py |
| `email-to-calendar/scripts/tests/test_changelog_ops.py` | Tests for utils/changelog_ops.py |
| `email-to-calendar/scripts/tests/test_common.py` | Tests for utils/common.py |
| `email-to-calendar/scripts/tests/test_date_parser.py` | Tests for utils/date_parser.py |
| `email-to-calendar/scripts/tests/test_disposition_ops.py` | Tests for utils/disposition_ops.py |
| `email-to-calendar/scripts/tests/test_event_tracking.py` | Tests for utils/event_tracking.py |
| `email-to-calendar/scripts/tests/test_invite_ops.py` | Tests for utils/invite_ops.py |
| `email-to-calendar/scripts/tests/test_json_store.py` | Tests for utils/json_store.py |
| `email-to-calendar/scripts/tests/test_pending_ops.py` | Tests for utils/pending_ops.py |
| `email-to-calendar/scripts/tests/test_undo_ops.py` | Tests for utils/undo_ops.py |
| `email-to-calendar/scripts/utils/activity_ops.py` | Activity log operations for email-to-calendar skill. |
| `email-to-calendar/scripts/utils/calendar_ops.py` | Provider-agnostic calendar operations for email-to-calendar skill. |
| `email-to-calendar/scripts/utils/changelog_ops.py` | Changelog operations for email-to-calendar skill. |
| `email-to-calendar/scripts/utils/common.py` | Shared utility functions for email-to-calendar skill. |
| `email-to-calendar/scripts/utils/date_parser.py` | Shared date and time parsing utilities for email-to-calendar skill. |
| `email-to-calendar/scripts/utils/disposition_ops.py` | Email disposition operations for email-to-calendar skill. |
| `email-to-calendar/scripts/utils/email_ops.py` | Provider-agnostic email operations for email-to-calendar skill. |
| `email-to-calendar/scripts/utils/event_tracking.py` | Event tracking operations for email-to-calendar skill. |
| `email-to-calendar/scripts/utils/invite_ops.py` | Invite status operations for email-to-calendar skill. |
| `email-to-calendar/scripts/utils/json_store.py` | Shared JSON file operations for email-to-calendar skill. |
| `email-to-calendar/scripts/utils/pending_ops.py` | Pending invites operations for email-to-calendar skill. |
| `email-to-calendar/scripts/utils/undo_ops.py` | Undo operations for email-to-calendar skill. |
| `gantt-chart-builder/scripts/gantt_generator.py` | Project Timeline - 交互式 HTML 甘特图生成器 支持关键路径分析（CPM）、依赖关系可视化、浮动时间展示 |
| `general-writing/scripts/check_chapter_quality.py` | Chapter quality checker for fiction writing pipeline. Extends check_wordcount.py with em-dash density + English leakage scanning. Minimal, zero-dependency (stdlib only), sandbox-safe. |
| `general-writing/scripts/check_wordcount.py` | Word count checker for general-writing skills. Minimal, zero-dependency (stdlib only), sandbox-safe. |
| `gitlab-cli-guide/scripts/post-inline-comment.py` | post-inline-comment.py — Post inline diff comments on GitLab MRs via JSON body. |
| `gitlab-cli-skills/scripts/post-inline-comment.py` | post-inline-comment.py — Post inline diff comments on GitLab MRs via JSON body. |
| `http-load-profiler/scripts/http_benchmark.py` | HTTP 阶梯式并发压测工具 — 封装 ab/wrk + p50/p90/p99 + 拐点分析 |
| `http-load-tester/scripts/http_benchmark.py` | HTTP 阶梯式并发压测工具 — 封装 ab/wrk + p50/p90/p99 + 拐点分析 |
| `incident-retrospective/scripts/generate_postmortem.py` | Postmortem document generator. |
| `incident-review-guide/scripts/generate_postmortem.py` | Postmortem document generator. |
| `landing-page-scaffold/scripts/generate_landing_page.py` | Generate a self-contained landing page HTML wireframe. |
| `localization-toolkit/scripts/i18n_audit.py` | Audit i18n key usage vs locale JSON files. |
| `log-diagnostic/scripts/analyze_logs.py` | 日志分析工具：错误聚类 + 频率统计 + 时间分布，支持 JSON/syslog/Nginx 格式。 |
| `log-error-digest/scripts/analyze_logs.py` | 日志分析工具：错误聚类 + 频率统计 + 时间分布，支持 JSON/syslog/Nginx 格式。 |
| `pricing-advisor/scripts/pricing_modeler.py` | Pricing modeler — projects revenue at different price points and recommends tier structure. |
| `project-sizing-guide/scripts/estimate_calculator.py` | 软件项目工时估算计算器 支持三点估算 (PERT)、T-shirt Sizing、功能点分析 (FPA) |
| `py-perf-analyzer/scripts/perf_profile.py` | Python 性能分析工具 - cProfile + line_profiler + tracemalloc |
| `route-to-openapi/scripts/generate_api_doc.py` | Generate OpenAPI 3.0 spec from source code route definitions. |
| `seedance-video-generate/scripts/seedance_video_generate.py` | Seedance Video Generation Skill for Echo Agent Uses Volcano Engine Seedance API to generate videos from text, images, or existing videos. |
| `seedream-image-generate/scripts/seedream_image_generate.py` | Seedream Image Generation Skill for Echo Agent Uses Volcano Engine Seedream API to generate images from text prompts. |
| `sql-insight/scripts/sql_query_helper.py` | sql_query_helper.py — SQL 查询辅助工具：Schema 提取 / 查询优化分析 / EXPLAIN 解读 |
| `sql-tutor/scripts/sql_query_helper.py` | sql_query_helper.py — SQL 查询辅助工具：Schema 提取 / 查询优化分析 / EXPLAIN 解读 |
| `sun-path/scripts/annual_sun_hours.py` | Annual sun/shadow hours at a point. Given a building (footprint + height + rotation) and a point (x,y in meters from building center), compute total hours per year the point is in direct sun vs in building shadow vs night. Samples at configurable interval (default 1 hour) for memory efficiency on 1G |
| `sun-path/scripts/comfort_calc.py` | — |
| `sun-path/scripts/plot_sunpath.py` | — |
| `sun-path/scripts/shadow_calc.py` | — |
| `sun-path/scripts/sun_calc.py` | — |
| `sun-path/scripts/terrain_shadow.py` | Terrain shadow from DEM at a given time. Reads a GeoTIFF DEM, computes sun position, and outputs a binary shadow raster (1 = in shadow, 0 = in sun) and optional hillshade-style plot. Designed for modest memory use (process by blocks if needed); single read for small DEMs. |
| `sunlight-analysis/scripts/annual_sun_hours.py` | Annual sun/shadow hours at a point. Given a building (footprint + height + rotation) and a point (x,y in meters from building center), compute total hours per year the point is in direct sun vs in building shadow vs night. Samples at configurable interval (default 1 hour) for memory efficiency on 1G |
| `sunlight-analysis/scripts/comfort_calc.py` | — |
| `sunlight-analysis/scripts/plot_sunpath.py` | — |
| `sunlight-analysis/scripts/shadow_calc.py` | — |
| `sunlight-analysis/scripts/sun_calc.py` | — |
| `sunlight-analysis/scripts/terrain_shadow.py` | Terrain shadow from DEM at a given time. Reads a GeoTIFF DEM, computes sun position, and outputs a binary shadow raster (1 = in shadow, 0 = in sun) and optional hillshade-style plot. Designed for modest memory use (process by blocks if needed); single read for small DEMs. |

## Who imports this

**11** file(s) reference this package:

- **`runtime/core/`** · 3 file(s)
  - `runtime/core/cerebrum/_react_context_helpers.py`
  - `runtime/core/cerebrum/_react_execution_dispatch.py`
  - `runtime/core/cerebrum/react_parallel_dispatch.py`
- **`runtime/execution/`** · 4 file(s)
  - `runtime/execution/misc/capability_catalog.py`
  - `runtime/execution/misc/capability_permissions.py`
  - `runtime/execution/swarm/drive.py`
  - `runtime/execution/tool_engine/executor.py`
- **`runtime/platform/`** · 2 file(s)
  - `runtime/platform/config/builder.py`
  - `runtime/platform/ui/health_router.py`
- **`runtime/sensing/`** · 2 file(s)
  - `runtime/sensing/gateway/_agents_endpoints_system.py`
  - `runtime/sensing/gateway/_meta_skill_metadata.py`

