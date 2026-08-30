"""Narrative Studio — generic, governed long-form story engine.

Agents create only candidates and review opinions. A separate human HTTP
governance path may create immutable canon snapshots after quorum, approval,
blocking-review, freshness, identity-source, and explicit-confirmation checks.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse

from runtime.execution.suckers.registry import Skill
from runtime.platform.plugins.plugin_base import ModulePlugin, ProvidedCapability
from runtime.platform.process.paths import app_paths

from .echo_adapter import EchoUniverseAdapter
from .mcp_server import MCP_ENDPOINT, NarrativeMcpServer
from .models import (
    BranchCreate,
    ChapterCreate,
    ChapterUpdate,
    ContextPackBuildRequest,
    EchoImportRequest,
    FactCreate,
    PipelineRunCreate,
    PipelineStageSubmit,
    ProjectCreate,
    ReviewRequestCreate,
    RevisionActorSource,
    RevisionRestoreRequest,
    SceneCreate,
    SceneUpdate,
    StateChangeCreate,
    WorldPackCreate,
)
from .skill_assets import PackagedSkillAsset, load_packaged_skill_assets
from .store import NarrativeConflict, NarrativeNotFound, NarrativeStore, NarrativeStoreError
from .v2_routes import _principal, register_v2_routes

API_PREFIX = "/api/plugins/narrative-studio"
CANON_POLICY = "candidate_only"


def _items(rows: list[Any]) -> dict[str, Any]:
    values = [row.model_dump(mode="json") if hasattr(row, "model_dump") else row for row in rows]
    return {"items": values, "total": len(values)}


def _revision_items(rows: list[Any]) -> dict[str, Any]:
    """Return bounded history metadata; full immutable content has a detail route."""

    values = [row.model_dump(mode="json", exclude={"snapshot"}) for row in rows]
    return {"items": values, "total": len(values)}


_IF_MATCH_RE = re.compile(r'^(?:W/)?"?([1-9][0-9]*)"?$')


def _expected_revision(
    request: Request,
    query_value: int | None,
    body_value: int | None,
) -> int | None:
    """Resolve optimistic-concurrency input with HTTP precondition priority."""

    raw = request.headers.get("if-match")
    if raw is not None:
        clean = raw.strip()
        if clean == "*":
            return None
        match = _IF_MATCH_RE.fullmatch(clean)
        if match is None:
            raise NarrativeStoreError("If-Match must contain one positive revision ETag")
        return int(match.group(1))
    return query_value if query_value is not None else body_value


def _revision_identity(request: Request) -> tuple[str, RevisionActorSource]:
    principal = _principal(request)
    if principal:
        return principal, "authenticated_principal"
    return "local", "client_asserted"


class NarrativeStudioPlugin(ModulePlugin):
    name = "narrative_studio"
    display_name = "叙事工坊"
    version = "0.2.0"
    description = "通用 AI 叙事引擎：候选创作流水线、来源上下文、人工评审与不可变正典快照"
    author = "Echo"

    def __init__(self) -> None:
        super().__init__()
        self.store: NarrativeStore | None = None
        self.echo_root = ""
        self.echo_max_files = 500
        self.echo_max_chars_per_file = 24_000
        self.echo_max_bytes_per_file = 2 * 1024 * 1024
        self.mcp_server: NarrativeMcpServer | None = None
        self.packaged_skill_assets: list[PackagedSkillAsset] = []
        self._host_requires_auth = False

    def on_load(self, ctx: Any) -> None:
        config = dict(ctx.config or {})
        data_dir = self._resolve_data_dir(config)
        self.store = NarrativeStore(
            data_dir,
            default_review_quorum=int(config.get("review_quorum") or 2),
            default_approval_ratio=float(config.get("approval_ratio") or 0.67),
            context_max_chars=int(config.get("context_max_chars") or 60_000),
            context_max_items=int(config.get("context_max_items") or 200),
            history_max_snapshot_bytes=int(
                config.get("history_max_snapshot_bytes") or 16 * 1024 * 1024
            ),
            history_max_revisions_per_record=int(
                config.get("history_max_revisions_per_record") or 500
            ),
            history_max_entries_per_project=int(
                config.get("history_max_entries_per_project") or 20_000
            ),
        )
        self.echo_root = self._discover_echo_root(
            configured=str(config.get("echo_source_path") or ""),
            plugin_dir=Path(ctx.plugin_dir),
        )
        self.echo_max_files = int(config.get("echo_max_files") or 500)
        self.echo_max_chars_per_file = int(config.get("echo_max_chars_per_file") or 24_000)
        self.echo_max_bytes_per_file = int(config.get("echo_max_bytes_per_file") or 2 * 1024 * 1024)
        self.mcp_server = NarrativeMcpServer(self._require_store)
        app_state = getattr(getattr(ctx, "fastapi_app", None), "state", None)
        self._host_requires_auth = bool(getattr(app_state, "echo_require_auth", False))
        super().on_load(ctx)

    def on_unload(self, _ctx: Any) -> None:
        """Drop plugin-owned service references after hub-owned cleanup."""

        self.mcp_server = None
        self.store = None
        self.packaged_skill_assets = []

    @staticmethod
    def _resolve_data_dir(config: dict[str, Any]) -> Path:
        """Follow the shared app data root while preserving legacy local drafts.

        Desktop builds always set ``ECHO_DATA_DIR`` and therefore use their
        sandboxed application data directory.  Older source-checkout versions
        stored Narrative Studio under ``~/.echo/data``; when no shared-root
        override exists, keep reading that directory until the user explicitly
        configures or migrates it instead of making existing projects appear to
        disappear after the plugin upgrade.
        """

        configured = str(config.get("data_dir") or "").strip()
        if configured:
            return Path(configured).expanduser()

        preferred = app_paths().data_dir / "narrative-studio"
        if os.environ.get("ECHO_DATA_DIR") or os.environ.get("ECHO_HOME"):
            return preferred

        legacy = Path.home() / ".echo" / "data" / "narrative-studio"
        if legacy.exists() and not preferred.exists():
            return legacy
        return preferred

    def register_skills(self) -> None:
        if self.ctx is None or self.store is None:
            return
        skills = [
            Skill(
                name="narrative_studio.project_create",
                description=(
                    "创建一个叙事项目和默认 main 候选分支。参数 title 必填；可选 id、premise、"
                    "language=zh|en|bilingual。此技能不创建或晋升正典。"
                ),
                summary="创建候选叙事项目(title 必填)",
                affinity=["narrative", "novel", "story", "project", "candidate", "write"],
                cost_profile="low",
                trusted_source="plugin://narrative_studio",
                handler=self._project_create_skill,
            ),
            Skill(
                name="narrative_studio.chapter_candidate",
                description=(
                    "向现有项目写入候选章节。参数 project_id、branch_id、ordinal、title 必填；"
                    "可选 summary、body、status。永远写 canon_status=candidate，不能直接写正典。"
                ),
                summary="写入候选章节(project+branch+ordinal+title)",
                affinity=["narrative", "novel", "chapter", "candidate", "write"],
                cost_profile="mid",
                trusted_source="plugin://narrative_studio",
                handler=self._chapter_candidate_skill,
            ),
            Skill(
                name="narrative_studio.fact_candidate",
                description=(
                    "记录候选世界事实。参数 project_id、subject、predicate、object 必填；"
                    "非 world 范围必须给 branch_id。只进入候选事实库，不会更改正典。"
                ),
                summary="记录候选事实(project+subject+predicate+object)",
                affinity=["narrative", "worldbuilding", "fact", "candidate", "write"],
                cost_profile="low",
                trusted_source="plugin://narrative_studio",
                handler=self._fact_candidate_skill,
            ),
            Skill(
                name="narrative_studio.state_change_candidate",
                description=(
                    "记录叙事状态差异，而不是直接改写角色/世界正典。参数 project_id、branch_id、"
                    "entity_id、field 必填；可选 before、after、chapter_id、scene_id、reason。"
                ),
                summary="记录候选状态差异(project+branch+entity+field)",
                affinity=["narrative", "continuity", "state", "diff", "candidate", "write"],
                cost_profile="low",
                trusted_source="plugin://narrative_studio",
                handler=self._state_change_candidate_skill,
            ),
            Skill(
                name="narrative_studio.echo_import_candidate",
                description=(
                    "只读扫描 ECHO 宇宙目录并导入为候选世界包。参数 project_id 必填；"
                    "source_path 只能等于已配置白名单根。源目录缺失时安全降级；绝不写源目录。"
                ),
                summary="只读导入ECHO为候选世界包(project_id)",
                affinity=["narrative", "echo", "worldbuilding", "import", "candidate", "read"],
                cost_profile="mid",
                trusted_source="plugin://narrative_studio",
                handler=self._echo_import_skill,
            ),
            Skill(
                name="narrative_studio.build_context",
                description=(
                    "为候选分支构建带来源引用且受字符/条目硬上限保护的上下文包。"
                    "参数 project_id、branch_id 必填；可选 target_chapter_id、max_chars、max_items。"
                ),
                summary="构建有引用和硬上限的叙事上下文",
                affinity=["narrative", "context", "retrieval", "continuity", "read"],
                cost_profile="mid",
                trusted_source="plugin://narrative_studio",
                handler=self._build_context_skill,
            ),
            Skill(
                name="narrative_studio.pipeline_create",
                description=(
                    "创建固定 outline→draft→continuity→style→revision→editorial 的候选流水线。"
                    "不自动调用模型，也不会创建正典。"
                ),
                summary="创建候选叙事流水线",
                affinity=["narrative", "pipeline", "novel", "candidate", "write"],
                cost_profile="low",
                trusted_source="plugin://narrative_studio",
                handler=self._pipeline_create_skill,
            ),
            Skill(
                name="narrative_studio.pipeline_stage_submit",
                description=(
                    "按固定顺序提交某个流水线阶段的候选输出。参数 project_id、run_id、"
                    "stage_id、output、submitted_by 必填；不能跳阶段或写正典。"
                ),
                summary="按顺序提交候选流水线阶段",
                affinity=["narrative", "pipeline", "candidate", "write"],
                cost_profile="mid",
                trusted_source="plugin://narrative_studio",
                handler=self._pipeline_stage_submit_skill,
            ),
            Skill(
                name="narrative_studio.review_candidate",
                description=(
                    "针对候选对象产生一条审查意见。只创建 ReviewRequest；不能投票、"
                    "解决阻断项或创建 CanonCommit。"
                ),
                summary="产生候选审查意见（无正典权限）",
                affinity=["narrative", "review", "continuity", "candidate", "write"],
                cost_profile="low",
                trusted_source="plugin://narrative_studio",
                handler=self._review_candidate_skill,
            ),
        ]
        self.packaged_skill_assets = load_packaged_skill_assets(self.ctx.plugin_dir)
        skills.extend(asset.as_runtime_skill() for asset in self.packaged_skill_assets)
        for skill in skills:
            with contextlib.suppress(Exception):
                self.ctx.register_skill(skill)

    @property
    def capabilities(self) -> list[ProvidedCapability]:
        return [
            *super().capabilities,
            ProvidedCapability(
                type="config_ui",
                name="narrative_studio.page",
                description="Narrative Studio status page",
            ),
            ProvidedCapability(
                type="mcp",
                name="narrative_studio.mcp",
                description="Candidate-only MCP tools over authenticated host JSON-RPC",
            ),
        ]

    def _project_create_skill(
        self,
        title: str = "",
        id: str | None = None,
        premise: str = "",
        language: str = "zh",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return self._skill_call(
            lambda: self._require_store().create_project(
                ProjectCreate(id=id, title=title, premise=premise, language=language)
            )
        )

    def _chapter_candidate_skill(
        self,
        project_id: str = "",
        branch_id: str = "",
        ordinal: int = 1,
        title: str = "",
        summary: str = "",
        body: str = "",
        status: str = "draft",
        id: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return self._skill_call(
            lambda: self._require_store().create_chapter(
                project_id,
                ChapterCreate(
                    id=id,
                    branch_id=branch_id,
                    ordinal=ordinal,
                    title=title,
                    summary=summary,
                    body=body,
                    status=status,
                ),
                actor="narrative_studio.chapter_candidate",
                actor_source="agent_skill",
            )
        )

    def _fact_candidate_skill(
        self,
        project_id: str = "",
        subject: str = "",
        predicate: str = "",
        object: str = "",
        branch_id: str | None = None,
        scope: str = "world",
        source_refs: list[str] | None = None,
        confidence: float = 1.0,
        id: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return self._skill_call(
            lambda: self._require_store().create_fact(
                project_id,
                FactCreate(
                    id=id,
                    branch_id=branch_id,
                    subject=subject,
                    predicate=predicate,
                    object=object,
                    scope=scope,
                    source_refs=source_refs or [],
                    confidence=confidence,
                ),
            )
        )

    def _state_change_candidate_skill(
        self,
        project_id: str = "",
        branch_id: str = "",
        entity_id: str = "",
        field: str = "",
        before: Any = None,
        after: Any = None,
        chapter_id: str | None = None,
        scene_id: str | None = None,
        reason: str = "",
        source_refs: list[str] | None = None,
        id: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return self._skill_call(
            lambda: self._require_store().create_state_change(
                project_id,
                StateChangeCreate(
                    id=id,
                    branch_id=branch_id,
                    chapter_id=chapter_id,
                    scene_id=scene_id,
                    entity_id=entity_id,
                    field=field,
                    before=before,
                    after=after,
                    reason=reason,
                    source_refs=source_refs or [],
                ),
            )
        )

    def _echo_import_skill(
        self,
        project_id: str = "",
        source_path: str | None = None,
        pack_name: str = "ECHO Universe",
        include_content: bool = True,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        try:
            return {
                "ok": True,
                **self._import_echo(
                    project_id,
                    EchoImportRequest(
                        source_path=source_path,
                        pack_name=pack_name,
                        include_content=include_content,
                    ),
                ),
            }
        except (NarrativeStoreError, ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc), "canon_policy": CANON_POLICY}

    def _build_context_skill(
        self,
        project_id: str = "",
        branch_id: str = "",
        target_chapter_id: str | None = None,
        label: str = "",
        max_chars: int | None = None,
        max_items: int | None = None,
        id: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return self._skill_call(
            lambda: self._require_store().build_context_pack(
                project_id,
                ContextPackBuildRequest(
                    id=id,
                    branch_id=branch_id,
                    target_chapter_id=target_chapter_id,
                    label=label,
                    max_chars=max_chars,
                    max_items=max_items,
                ),
            )
        )

    def _pipeline_create_skill(
        self,
        project_id: str = "",
        branch_id: str = "",
        chapter_id: str | None = None,
        context_pack_id: str | None = None,
        goal: str = "",
        id: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return self._skill_call(
            lambda: self._require_store().create_pipeline_run(
                project_id,
                PipelineRunCreate(
                    id=id,
                    branch_id=branch_id,
                    chapter_id=chapter_id,
                    context_pack_id=context_pack_id,
                    goal=goal,
                ),
            )
        )

    def _pipeline_stage_submit_skill(
        self,
        project_id: str = "",
        run_id: str = "",
        stage_id: str = "",
        output: str = "",
        submitted_by: str = "agent",
        source_refs: list[str] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return self._skill_call(
            lambda: self._require_store().submit_pipeline_stage(
                project_id,
                run_id,
                stage_id,
                PipelineStageSubmit(
                    output=output,
                    source_refs=source_refs or [],
                    submitted_by=submitted_by,
                ),
            )
        )

    def _review_candidate_skill(
        self,
        project_id: str = "",
        target_type: str = "chapter",
        target_id: str = "",
        title: str = "Candidate review",
        summary: str = "",
        blocking: bool = False,
        requested_by: str = "agent",
        id: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return self._skill_call(
            lambda: self._require_store().create_review_request(
                project_id,
                ReviewRequestCreate(
                    id=id,
                    target_type=target_type,
                    target_id=target_id,
                    title=title,
                    summary=summary,
                    blocking=blocking,
                    requested_by=requested_by,
                ),
                actor_source="agent_skill",
            )
        )

    def register_routes(self) -> None:
        if self.ctx is None or self.ctx.fastapi_app is None:
            return
        router = APIRouter(prefix=API_PREFIX, tags=["narrative_studio"])

        def record_response(response: Response, operation: Callable[[], Any]) -> Any:
            value = self._api(operation)
            if isinstance(value, dict) and isinstance(value.get("revision"), int):
                response.headers["ETag"] = f'"{value["revision"]}"'
            return value

        @router.get("/status")
        def status() -> dict[str, Any]:
            store = self._require_store()
            return {
                "ok": True,
                "plugin": self.name,
                "version": self.version,
                "canon_policy": CANON_POLICY,
                "canon_governance": "human_confirmed_immutable_snapshot",
                "data_dir": str(store.data_dir),
                "project_count": len(store.list_projects()),
                "echo": self._echo_adapter(None).probe(),
                "mcp": {
                    "enabled": self.mcp_server is not None,
                    "endpoint": MCP_ENDPOINT,
                    "transport": "json-rpc-http",
                    "auth": "host_inherited",
                    "tool_policy": "candidate_only_allowlist",
                    "tools": self._require_mcp_server().tool_names,
                },
                "packaged_skills": [asset.catalog_entry() for asset in self.packaged_skill_assets],
                "capabilities": [
                    "projects",
                    "world_packs",
                    "branches",
                    "chapters",
                    "scenes",
                    "facts",
                    "state_changes",
                    "story_arcs",
                    "entities",
                    "relationships",
                    "foreshadows",
                    "source_cited_context_packs",
                    "ordered_candidate_pipeline",
                    "candidate_revision_history",
                    "optimistic_concurrency",
                    "human_reviews_and_votes",
                    "immutable_canon_commits",
                    "echo_read_only_import",
                    "candidate_only_mcp",
                    "packaged_skill_assets",
                ],
            }

        @router.post("/mcp")
        async def mcp_json_rpc(body: dict[str, Any], request: Request) -> Any:
            actor = _principal(request)
            # Production authentication is owned by the Echo host.  This
            # backstop trusts only its server-resolved principal and never
            # parses a second token or caller-supplied identity field.
            if self._host_requires_auth and actor is None:
                raise HTTPException(401, "authenticated Echo principal required")
            result = await self._require_mcp_server().handle_request(body, actor=actor)
            if result is None:
                return Response(status_code=202)
            return result

        @router.get("/page", response_class=HTMLResponse)
        def page() -> HTMLResponse:
            plugin_dir = Path(self.ctx.plugin_dir)
            path = plugin_dir / "page" / "index.html"
            if not path.is_file():
                raise HTTPException(404, "Narrative Studio page is unavailable")
            return HTMLResponse(path.read_text(encoding="utf-8"))

        @router.get("/projects")
        def list_projects() -> dict[str, Any]:
            return _items(self._require_store().list_projects())

        @router.post("/projects", status_code=201)
        def create_project(body: ProjectCreate) -> dict[str, Any]:
            return self._api(lambda: self._require_store().create_project(body))

        @router.get("/projects/{project_id}")
        def get_project(project_id: str) -> dict[str, Any]:
            return self._api(lambda: self._require_store().project_detail(project_id))

        @router.get("/projects/{project_id}/world-packs")
        def list_world_packs(project_id: str) -> dict[str, Any]:
            return self._api(lambda: _items(self._require_store().list_world_packs(project_id)))

        @router.post("/projects/{project_id}/world-packs", status_code=201)
        def create_world_pack(project_id: str, body: WorldPackCreate) -> dict[str, Any]:
            return self._api(lambda: self._require_store().create_world_pack(project_id, body))

        @router.get("/projects/{project_id}/branches")
        def list_branches(project_id: str) -> dict[str, Any]:
            return self._api(lambda: _items(self._require_store().list_branches(project_id)))

        @router.post("/projects/{project_id}/branches", status_code=201)
        def create_branch(project_id: str, body: BranchCreate) -> dict[str, Any]:
            return self._api(lambda: self._require_store().create_branch(project_id, body))

        @router.get("/projects/{project_id}/chapters")
        def list_chapters(
            project_id: str, branch_id: str | None = Query(default=None)
        ) -> dict[str, Any]:
            return self._api(
                lambda: _items(self._require_store().list_chapters(project_id, branch_id))
            )

        @router.post("/projects/{project_id}/chapters", status_code=201)
        def create_chapter(
            project_id: str,
            body: ChapterCreate,
            request: Request,
            response: Response,
        ) -> dict[str, Any]:
            actor, source = _revision_identity(request)
            return record_response(
                response,
                lambda: self._require_store().create_chapter(
                    project_id,
                    body,
                    actor=actor,
                    actor_source=source,
                ),
            )

        @router.get("/projects/{project_id}/chapters/{chapter_id}")
        def get_chapter(project_id: str, chapter_id: str, response: Response) -> dict[str, Any]:
            return record_response(
                response,
                lambda: self._require_store().get_chapter(project_id, chapter_id),
            )

        @router.put("/projects/{project_id}/chapters/{chapter_id}")
        def update_chapter(
            project_id: str,
            chapter_id: str,
            body: ChapterUpdate,
            request: Request,
            response: Response,
            expected_revision: int | None = Query(default=None, ge=1),
        ) -> dict[str, Any]:
            actor, source = _revision_identity(request)
            return record_response(
                response,
                lambda: self._require_store().update_chapter(
                    project_id,
                    chapter_id,
                    body,
                    expected_revision=_expected_revision(
                        request,
                        expected_revision,
                        body.expected_revision,
                    ),
                    actor=actor,
                    actor_source=source,
                ),
            )

        @router.get("/projects/{project_id}/chapters/{chapter_id}/revisions")
        def list_chapter_revisions(project_id: str, chapter_id: str) -> dict[str, Any]:
            return self._api(
                lambda: _revision_items(
                    self._require_store().list_chapter_revisions(project_id, chapter_id)
                )
            )

        @router.get("/projects/{project_id}/chapters/{chapter_id}/revisions/{revision}")
        def get_chapter_revision(
            project_id: str,
            chapter_id: str,
            revision: int,
            response: Response,
        ) -> dict[str, Any]:
            return record_response(
                response,
                lambda: self._require_store().get_chapter_revision(
                    project_id, chapter_id, revision
                ),
            )

        @router.post("/projects/{project_id}/chapters/{chapter_id}/revisions/{revision}/restore")
        def restore_chapter_revision(
            project_id: str,
            chapter_id: str,
            revision: int,
            body: RevisionRestoreRequest,
            request: Request,
            response: Response,
            expected_revision: int | None = Query(default=None, ge=1),
        ) -> dict[str, Any]:
            actor, source = _revision_identity(request)
            return record_response(
                response,
                lambda: self._require_store().restore_chapter_revision(
                    project_id,
                    chapter_id,
                    revision,
                    expected_revision=_expected_revision(
                        request,
                        expected_revision,
                        body.expected_revision,
                    ),
                    actor=actor,
                    actor_source=source,
                    message=body.message,
                ),
            )

        @router.get("/projects/{project_id}/chapters/{chapter_id}/scenes")
        def list_scenes(project_id: str, chapter_id: str) -> dict[str, Any]:
            return self._api(
                lambda: _items(self._require_store().list_scenes(project_id, chapter_id))
            )

        @router.post("/projects/{project_id}/chapters/{chapter_id}/scenes", status_code=201)
        def create_scene(
            project_id: str,
            chapter_id: str,
            body: SceneCreate,
            request: Request,
            response: Response,
        ) -> dict[str, Any]:
            actor, source = _revision_identity(request)
            return record_response(
                response,
                lambda: self._require_store().create_scene(
                    project_id,
                    chapter_id,
                    body,
                    actor=actor,
                    actor_source=source,
                ),
            )

        @router.get("/projects/{project_id}/chapters/{chapter_id}/scenes/{scene_id}")
        def get_scene(
            project_id: str,
            chapter_id: str,
            scene_id: str,
            response: Response,
        ) -> dict[str, Any]:
            return record_response(
                response,
                lambda: self._require_store().get_scene_in_chapter(
                    project_id, chapter_id, scene_id
                ),
            )

        @router.put("/projects/{project_id}/chapters/{chapter_id}/scenes/{scene_id}")
        def update_scene(
            project_id: str,
            chapter_id: str,
            scene_id: str,
            body: SceneUpdate,
            request: Request,
            response: Response,
            expected_revision: int | None = Query(default=None, ge=1),
        ) -> dict[str, Any]:
            actor, source = _revision_identity(request)
            return record_response(
                response,
                lambda: self._require_store().update_scene(
                    project_id,
                    chapter_id,
                    scene_id,
                    body,
                    expected_revision=_expected_revision(
                        request,
                        expected_revision,
                        body.expected_revision,
                    ),
                    actor=actor,
                    actor_source=source,
                ),
            )

        @router.get("/projects/{project_id}/chapters/{chapter_id}/scenes/{scene_id}/revisions")
        def list_scene_revisions(project_id: str, chapter_id: str, scene_id: str) -> dict[str, Any]:
            return self._api(
                lambda: _revision_items(
                    self._require_store().list_scene_revisions(project_id, chapter_id, scene_id)
                )
            )

        @router.get(
            "/projects/{project_id}/chapters/{chapter_id}/scenes/{scene_id}/revisions/{revision}"
        )
        def get_scene_revision(
            project_id: str,
            chapter_id: str,
            scene_id: str,
            revision: int,
            response: Response,
        ) -> dict[str, Any]:
            return record_response(
                response,
                lambda: self._require_store().get_scene_revision(
                    project_id, chapter_id, scene_id, revision
                ),
            )

        @router.post(
            "/projects/{project_id}/chapters/{chapter_id}/scenes/{scene_id}/"
            "revisions/{revision}/restore"
        )
        def restore_scene_revision(
            project_id: str,
            chapter_id: str,
            scene_id: str,
            revision: int,
            body: RevisionRestoreRequest,
            request: Request,
            response: Response,
            expected_revision: int | None = Query(default=None, ge=1),
        ) -> dict[str, Any]:
            actor, source = _revision_identity(request)
            return record_response(
                response,
                lambda: self._require_store().restore_scene_revision(
                    project_id,
                    chapter_id,
                    scene_id,
                    revision,
                    expected_revision=_expected_revision(
                        request,
                        expected_revision,
                        body.expected_revision,
                    ),
                    actor=actor,
                    actor_source=source,
                    message=body.message,
                ),
            )

        @router.get("/projects/{project_id}/facts")
        def list_facts(
            project_id: str, branch_id: str | None = Query(default=None)
        ) -> dict[str, Any]:
            return self._api(
                lambda: _items(self._require_store().list_facts(project_id, branch_id))
            )

        @router.post("/projects/{project_id}/facts", status_code=201)
        def create_fact(project_id: str, body: FactCreate) -> dict[str, Any]:
            return self._api(lambda: self._require_store().create_fact(project_id, body))

        @router.get("/projects/{project_id}/state-changes")
        def list_state_changes(
            project_id: str, branch_id: str | None = Query(default=None)
        ) -> dict[str, Any]:
            return self._api(
                lambda: _items(self._require_store().list_state_changes(project_id, branch_id))
            )

        @router.post("/projects/{project_id}/state-changes", status_code=201)
        def create_state_change(project_id: str, body: StateChangeCreate) -> dict[str, Any]:
            return self._api(lambda: self._require_store().create_state_change(project_id, body))

        @router.post("/projects/{project_id}/imports/echo")
        def import_echo(project_id: str, body: EchoImportRequest) -> dict[str, Any]:
            return self._api(lambda: self._import_echo(project_id, body))

        register_v2_routes(router, self)
        self.ctx.fastapi_app.include_router(router)

    def _import_echo(self, project_id: str, body: EchoImportRequest) -> dict[str, Any]:
        store = self._require_store()
        store.get_project(project_id)
        adapter = self._echo_adapter(body.source_path)
        result = adapter.import_resources(include_content=body.include_content)
        base = {
            "available": result["available"],
            "imported": False,
            "reason": result.get("reason", ""),
            "source_root": result["source_root"],
            "inventory": result["inventory"],
            "truncated": result.get("truncated", False),
            "skipped_oversize": result.get("skipped_oversize", 0),
        }
        if not result["available"]:
            return base
        pack = store.create_imported_world_pack(
            project_id,
            name=body.pack_name,
            source_root=result["source_root"],
            resources=result["resources"],
            metadata={
                "inventory": result["inventory"],
                "truncated": result["truncated"],
                "include_content": body.include_content,
                "skipped_oversize": result.get("skipped_oversize", 0),
                "import_mode": "read_only_snapshot",
            },
        )
        return {**base, "imported": True, "world_pack": pack.model_dump(mode="json")}

    def _echo_adapter(self, source_path: str | None) -> EchoUniverseAdapter:
        allowed = self.echo_root.strip()
        selected = allowed
        if source_path is not None and source_path.strip():
            if not allowed:
                raise NarrativeStoreError("ECHO source override is not configured")
            requested_path = Path(source_path).expanduser().resolve(strict=False)
            allowed_path = Path(allowed).expanduser().resolve(strict=False)
            if requested_path != allowed_path:
                raise NarrativeStoreError(
                    "ECHO source override must equal the configured source root"
                )
            selected = str(allowed_path)
        return EchoUniverseAdapter(
            selected,
            max_files=self.echo_max_files,
            max_chars_per_file=self.echo_max_chars_per_file,
            max_bytes_per_file=self.echo_max_bytes_per_file,
        )

    @staticmethod
    def _discover_echo_root(*, configured: str, plugin_dir: Path) -> str:
        """Resolve config/env first, then portable sibling workspace layouts."""
        explicit = configured.strip() or os.environ.get("ECHO_UNIVERSE_ROOT", "").strip()
        if explicit:
            # Keep an explicit missing path: the status endpoint can explain the
            # configuration error instead of silently importing another tree.
            return str(Path(explicit).expanduser())
        candidates = [
            Path.cwd() / "echo-universe-engine",
            Path.cwd().parent / "echo-universe-engine",
        ]
        resolved_plugin = plugin_dir.expanduser().resolve()
        for ancestor in (resolved_plugin, *resolved_plugin.parents):
            if ancestor.name == "echo-agent":
                candidates.append(ancestor.parent / "echo-universe-engine")
                break
        for candidate in candidates:
            if candidate.is_dir():
                return str(candidate.resolve())
        return ""

    def _require_store(self) -> NarrativeStore:
        if self.store is None:
            raise NarrativeStoreError("Narrative Studio is not loaded")
        return self.store

    def _require_mcp_server(self) -> NarrativeMcpServer:
        if self.mcp_server is None:
            raise NarrativeStoreError("Narrative Studio MCP is not loaded")
        return self.mcp_server

    @staticmethod
    def _skill_call(operation: Callable[[], Any]) -> dict[str, Any]:
        try:
            value = operation()
            payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            return {"ok": True, "canon_policy": CANON_POLICY, "result": payload}
        except (NarrativeStoreError, ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc), "canon_policy": CANON_POLICY}

    @staticmethod
    def _api(operation: Callable[[], Any]) -> Any:
        try:
            value = operation()
            return value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        except NarrativeNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except NarrativeConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (NarrativeStoreError, ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc


__all__ = ["API_PREFIX", "CANON_POLICY", "NarrativeStudioPlugin"]
