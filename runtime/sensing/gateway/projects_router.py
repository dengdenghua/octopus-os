"""Project OS API — drive milestone-driven projects over HTTP.

Reads (project state + report) are public; mutations (plan / tick / run) are
auth-gated, mirroring the cowork router. The engine uses LLM hooks when a model
router is available, else deterministic stubs so the endpoints always work.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from runtime.projectos.cowork_bridge import (
    full_project_state,
    run_project_from_group,
)
from runtime.projectos.engine import (
    DEFAULT_RUN_MAX_TICKS,
    HARD_MAX_RUN_TICKS,
    ProjectEngine,
    stub_decompose_tasks,
    stub_generate_milestones,
)
from runtime.projectos.store import (
    ProjectBindingActiveError,
    ProjectClaimActiveError,
    ProjectStore,
)
from runtime.projectos.timeline import project_process_timeline
from runtime.safety.auth.principal import CurrentPrincipal, resolve_principal
from runtime.safety.auth.scope import TenantScope, scope_from_principal
from runtime.sensing.gateway._projects_group_projections import (
    ProjectGroupProjectionContext,
)
from runtime.sensing.gateway.thread_access import ThreadAccessResolver


class PlanBody(BaseModel):
    name: str = Field(min_length=1)
    goal: str = Field(min_length=1)


class ProjectGroupAgentBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(min_length=1)
    display_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("display_name", "displayName"),
    )
    description: str = ""
    avatar_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("avatar_url", "avatarUrl"),
    )
    icon: str | None = None


class ProjectGroupBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str = Field(min_length=1)
    goal: str | None = None
    initial_agents: list[ProjectGroupAgentBody] = Field(
        default_factory=list,
        validation_alias=AliasChoices("initial_agents", "initialAgents"),
    )


class MoveThreadBody(BaseModel):
    thread_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)


class RunBody(BaseModel):
    max_ticks: int = Field(default=DEFAULT_RUN_MAX_TICKS, ge=1, le=HARD_MAX_RUN_TICKS)


class RecoverBody(BaseModel):
    task_ids: list[str] = Field(default_factory=list)
    reset_attempts: bool = True
    clear_outputs: bool = True
    run: bool = False
    max_ticks: int = Field(default=DEFAULT_RUN_MAX_TICKS, ge=1, le=HARD_MAX_RUN_TICKS)


class TaskInterventionBody(BaseModel):
    action: str = Field(min_length=1)
    assigned_agent: str | None = None
    assigned_role: str | None = None
    output: Any = None
    reason: str = ""
    reset_attempts: bool = True
    cascade: bool = True
    run: bool = False
    max_ticks: int = Field(default=DEFAULT_RUN_MAX_TICKS, ge=1, le=HARD_MAX_RUN_TICKS)


class FromGroupBody(BaseModel):
    name: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    run: bool = False
    max_ticks: int = Field(default=DEFAULT_RUN_MAX_TICKS, ge=1, le=HARD_MAX_RUN_TICKS)


class DetachFromGroupBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    force: bool = False
    expected_project_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("expected_project_id", "expectedProjectId"),
    )


def _claim_active(exc: ProjectClaimActiveError) -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "CLAIM_ACTIVE",
            "message": "a worker claim is active; wait for it to finish",
            "project_id": exc.project.id,
            "task_ids": list(exc.task_ids),
            "milestone_ids": list(exc.milestone_ids),
        },
    )


def create_projects_router(
    *,
    store: ProjectStore | None = None,
    group_store: Any = None,
    collaboration_store: Any = None,
    team_rooms_router: Any = None,
    thread_store: Any = None,
    workspace_root: Any = None,
    model_router: Any = None,
    subagent_runner: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    """Create the ``/api/projects/*`` router."""
    project_store = store or ProjectStore()
    bind_team_project_store = getattr(team_rooms_router, "bind_project_store", None)
    if callable(bind_team_project_store):
        bind_team_project_store(project_store)

    def _group_store():
        if group_store is not None:
            return group_store
        from runtime.memory.cowork.group_store import GroupStore

        return GroupStore()

    thread_access = ThreadAccessResolver(
        thread_store=thread_store,
        group_store=_group_store(),
        collaboration_store=collaboration_store,
        team_rooms_router=team_rooms_router,
        identity_store=identity_store,
    )

    def _base_hooks() -> dict[str, Any]:
        """Intelligence hooks: LLM when a model router is available, else stubs."""
        if model_router is not None:
            from runtime.projectos.llm_hooks import create_llm_hooks

            return create_llm_hooks(
                model_router,
                subagent_runner=subagent_runner,
            )
        return {
            "generate_milestones": stub_generate_milestones,
            "decompose_tasks": stub_decompose_tasks,
        }

    def _principal(request: Request) -> CurrentPrincipal | None:
        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        if principal is not None:
            request.state.project_principal = principal
        return principal

    def _scoped_store(request: Request) -> ProjectStore:
        principal = _principal(request)
        if principal is None:
            return project_store
        allow_cross_tenant = bool(principal.roles.intersection({"admin", "operator"}))
        return project_store.with_scope(
            scope_from_principal(principal, allow_cross_tenant=allow_cross_tenant)
        )

    def _engine(principal: CurrentPrincipal | None = None) -> ProjectEngine:
        scope = scope_from_principal(
            principal,
            allow_cross_tenant=bool(
                principal is not None and principal.roles.intersection({"admin", "operator"})
            ),
        )
        return ProjectEngine(
            project_store,
            **_base_hooks(),
            owner_id=principal.actor_id if principal is not None else "",
            tenant_id=principal.tenant_id if principal is not None else "",
            scope=scope,
            resolve_thread_context=_execution_context_resolver(principal),
        )

    def _execution_context_resolver(principal: CurrentPrincipal | None = None):
        if not require_auth:
            return None

        def _resolve(thread_id: str) -> dict[str, Any]:
            if not thread_id or thread_store is None or not hasattr(thread_store, "get"):
                raise RuntimeError("project must be bound to a managed thread workspace")
            thread = thread_store.get(thread_id)
            raw_metadata = thread.get("metadata") if isinstance(thread, dict) else None
            metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            if principal is not None and not principal.roles.intersection({"admin", "operator"}):
                if metadata.get("owner_actor_id") != principal.actor_id:
                    raise PermissionError("project thread belongs to another actor")
                stored_tenant = str(metadata.get("tenant_id") or "")
                if stored_tenant != principal.tenant_id:
                    raise PermissionError("project thread belongs to another tenant")
            from runtime.sensing.gateway.thread_workspace import verified_managed_workspace

            workspace = verified_managed_workspace(
                workspace_root,
                thread_id=thread_id,
                metadata=metadata,
            )
            if workspace is None:
                raise RuntimeError("project thread has no verified managed workspace")
            return {
                "workspace_path": str(workspace),
                "runtime_session_metadata": {
                    "workspace_path": str(workspace),
                    "_artifact_output_root": str(workspace / "output" / "final"),
                    "tenant_id": str(metadata.get("tenant_id") or ""),
                    "owner_actor_id": str(metadata.get("owner_actor_id") or ""),
                },
            }

        return _resolve

    def _require_execution_context(request: Request, project_id: str) -> None:
        resolver = _execution_context_resolver(_principal(request))
        if resolver is None:
            return
        thread_id = _scoped_store(request).thread_for_project(project_id) or ""
        try:
            resolver(thread_id)
        except (OSError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(
                409,
                "project execution requires a verified managed thread workspace",
            ) from exc

    def _auth_dep(request: Request) -> None:
        _principal(request)

    router = APIRouter(tags=["projectos"], dependencies=[Depends(_auth_dep)])

    def _bad_request(exc: ValueError) -> HTTPException:
        return HTTPException(400, str(exc))

    def _project_or_404(
        request: Request,
        project_id: str,
        *,
        allow_operator: bool = True,
        allow_collaborator_read: bool = False,
    ):
        try:
            project = _scoped_store(request).get_project(project_id)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        principal = _principal(request)
        if project is None and allow_collaborator_read and principal is not None:
            # Owner-scoped storage intentionally hides another actor's row.
            # Resolve the raw record only long enough to prove its tenant and
            # linked canonical-thread ACL; subsequent reads use the persisted
            # project owner's scope, never the caller's arbitrary input.
            try:
                candidate = project_store.get_project(project_id)
            except ValueError as exc:
                raise _bad_request(exc) from exc
            if candidate is not None and candidate.tenant_id == principal.tenant_id:
                thread_id = project_store.thread_for_project(project_id) or ""
                decision = thread_access.resolve(
                    thread_id,
                    principal.actor_id,
                    principal.tenant_id,
                )
                if decision.can_read:
                    project = candidate
        if project is None:
            raise HTTPException(404, "project not found")
        if principal is not None:
            global_operator = bool(principal.roles.intersection({"admin", "operator"}))
            if not project.owner_id or not project.tenant_id:
                if not (allow_operator and global_operator):
                    raise HTTPException(404, "project not found")
            elif project.tenant_id != principal.tenant_id:
                raise HTTPException(404, "project not found")
            elif project.owner_id != principal.actor_id and not global_operator:
                if not allow_collaborator_read:
                    raise HTTPException(404, "project not found")
                thread_id = project_store.thread_for_project(project_id) or ""
                decision = thread_access.resolve(
                    thread_id,
                    principal.actor_id,
                    principal.tenant_id,
                )
                if not decision.can_read:
                    raise HTTPException(404, "project not found")
        return project

    def _project_read_store(request: Request, project: Any) -> ProjectStore:
        principal = _principal(request)
        if principal is None or principal.roles.intersection({"admin", "operator"}):
            return _scoped_store(request)
        if project.owner_id == principal.actor_id:
            return _scoped_store(request)
        return project_store.with_scope(
            TenantScope(
                tenant_id=str(project.tenant_id or ""),
                actor_id=str(project.owner_id or ""),
            )
        )

    def _thread_access(
        request: Request,
        thread_id: str,
        *,
        write: bool = False,
    ) -> CurrentPrincipal | None:
        principal = _principal(request)
        if principal is None:
            return None
        if thread_store is None or not hasattr(thread_store, "get"):
            raise HTTPException(503, "thread ownership unavailable")
        if principal.roles.intersection({"admin", "operator"}):
            return principal
        decision = thread_access.resolve(thread_id, principal.actor_id, principal.tenant_id)
        allowed = decision.can_manage if write else decision.can_read
        if not allowed:
            raise HTTPException(404, "thread not found")
        return principal

    def _full_state(
        request: Request,
        project_id: str,
        *,
        allow_collaborator_read: bool = False,
    ) -> dict[str, Any]:
        project = _project_or_404(
            request,
            project_id,
            allow_collaborator_read=allow_collaborator_read,
        )
        try:
            state = full_project_state(_project_read_store(request, project), project_id)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        if state is None:
            raise HTTPException(404, "project not found")
        return state

    projections = ProjectGroupProjectionContext(
        collaboration_store=collaboration_store,
        group_store=_group_store,
        scoped_store=_scoped_store,
        thread_store=thread_store,
        team_rooms_router=team_rooms_router,
        require_auth=require_auth,
    )
    _project_to_collaboration = projections.project_to_bound_collaboration
    _project_group_projections = projections.project_group_projections
    _clear_project_group_projections = projections.clear_project_group_projections

    @router.get("/api/projects")
    def list_projects(request: Request) -> dict[str, Any]:
        principal = _principal(request)
        projects = (
            project_store.list_projects()
            if principal is not None
            else _scoped_store(request).list_projects()
        )
        if principal is not None:
            global_operator = bool(principal.roles.intersection({"admin", "operator"}))
            visible: list[Any] = []
            for project in projects:
                if project.tenant_id and project.tenant_id != principal.tenant_id:
                    continue
                if not project.owner_id or not project.tenant_id:
                    if global_operator:
                        visible.append(project)
                    continue
                if project.owner_id == principal.actor_id or global_operator:
                    visible.append(project)
                    continue
                thread_id = project_store.thread_for_project(project.id) or ""
                if thread_access.resolve(
                    thread_id,
                    principal.actor_id,
                    principal.tenant_id,
                ).can_read:
                    visible.append(project)
            projects = visible
        return {"projects": [p.to_dict() for p in projects]}

    @router.get("/api/projects/by-thread/{thread_id}")
    def get_project_by_thread(request: Request, thread_id: str) -> dict[str, Any]:
        _thread_access(request, thread_id)
        try:
            project = project_store.project_for_thread(thread_id)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        if project is None:
            raise HTTPException(404, "project not found for thread")
        return _full_state(request, project.id, allow_collaborator_read=True)

    @router.get("/api/projects/thread-map")
    def thread_project_map(request: Request) -> dict[str, str]:
        principal = _principal(request)
        mapping = project_store.thread_project_map()
        if principal is None:
            return mapping
        filtered: dict[str, str] = {}
        for thread_id, project_id in mapping.items():
            project = project_store.get_project(project_id)
            if project is None or project.tenant_id != principal.tenant_id:
                continue
            if (
                project.owner_id == principal.actor_id
                or principal.roles.intersection({"admin", "operator"})
                or thread_access.resolve(
                    thread_id,
                    principal.actor_id,
                    principal.tenant_id,
                ).can_read
            ):
                filtered[thread_id] = project_id
        return filtered

    @router.get("/api/projects/{project_id}")
    def get_project(request: Request, project_id: str) -> dict[str, Any]:
        return _full_state(request, project_id, allow_collaborator_read=True)

    @router.get("/api/projects/{project_id}/report")
    def report(request: Request, project_id: str) -> dict[str, Any]:
        """A milestone report: each milestone + its tasks' status/output."""
        project = _project_or_404(request, project_id, allow_collaborator_read=True)
        out = []
        try:
            scoped_store = _project_read_store(request, project)
            milestones = scoped_store.milestones_for(project_id)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        for m in milestones:
            out.append(
                {
                    "id": m.id,
                    "name": m.name,
                    "status": m.status,
                    "success_criteria": m.success_criteria,
                    "tasks": [
                        {
                            "id": t.id,
                            "role": t.assigned_role,
                            "type": t.type,
                            "status": t.status,
                            "output": t.output,
                        }
                        for t in scoped_store.tasks_for_milestone(m.id)
                    ],
                }
            )
        return {"project": project.name, "status": project.status, "milestones": out}

    @router.get("/api/projects/{project_id}/pm")
    def pm_console(request: Request, project_id: str) -> dict[str, Any]:
        """PM 驾驶舱：里程碑健康度、燃尽、风险/阻塞、下一步、指派。"""
        project = _project_or_404(request, project_id, allow_collaborator_read=True)
        try:
            scoped_store = _project_read_store(request, project)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        from runtime.projectos.pm import build_pm_report

        report = build_pm_report(scoped_store, project_id)
        return {
            "project_id": project_id,
            "project": project.name,
            "status": project.status,
            "pm": report or {},
        }

    @router.get("/api/projects/{project_id}/retro")
    def retro(request: Request, project_id: str) -> dict[str, Any]:
        """复盘：完工项目的交付、成本与建议。"""
        project = _project_or_404(request, project_id, allow_collaborator_read=True)
        try:
            scoped_store = _project_read_store(request, project)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        from runtime.projectos.pm import build_retro

        return {
            "project_id": project_id,
            "project": project.name,
            "retro": build_retro(scoped_store, project_id) or {},
        }

    @router.get("/api/projects/{project_id}/events")
    def events(request: Request, project_id: str, limit: int = 100) -> dict[str, Any]:
        """Project audit trail: recoveries, interventions, and future operator actions."""
        project = _project_or_404(request, project_id, allow_collaborator_read=True)
        try:
            audit_events = _project_read_store(request, project).events_for_project(
                project_id,
                limit=limit,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return {
            "project_id": project_id,
            "events": audit_events,
        }

    @router.get("/api/projects/{project_id}/process-timeline")
    def process_timeline(request: Request, project_id: str, limit: int = 100) -> dict[str, Any]:
        """Project process timeline: persisted plan/run/control evidence."""
        project = _project_or_404(request, project_id, allow_collaborator_read=True)
        try:
            timeline = project_process_timeline(
                _project_read_store(request, project),
                project_id,
                limit=limit,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc
        if timeline is None:
            raise HTTPException(404, "project not found")
        return {"timeline": timeline}

    @router.post("/api/projects", dependencies=[Depends(_auth_dep)])
    def plan(request: Request, body: PlanBody) -> dict[str, Any]:
        """Turn a one-line goal into a project with generated milestones."""
        principal = _principal(request)
        try:
            project = _engine(principal).plan(body.name, body.goal)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        _project_to_collaboration(request, project.id)
        return {"ok": True, **_full_state(request, project.id)}

    @router.post("/api/projects/group", dependencies=[Depends(_auth_dep)])
    def create_project_group(request: Request, body: ProjectGroupBody) -> dict[str, Any]:
        """Create a project and its canonical collaboration group as one saga.

        This is the preferred creation boundary.  The older project, thread,
        cowork and room endpoints remain available for clients that still
        manage those surfaces independently.
        """

        from runtime.projectos.group_service import (
            ProjectGroupBindingChanged,
            ProjectGroupCreationRecoveryPending,
            ProjectGroupCreationService,
        )

        principal = _principal(request)
        normalized_agents: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_agent in body.initial_agents:
            agent_id = raw_agent.id.strip()
            if not agent_id or agent_id in seen:
                continue
            seen.add(agent_id)
            normalized_agents.append(
                {
                    "id": agent_id,
                    "display_name": (raw_agent.display_name or "").strip() or agent_id,
                    "description": raw_agent.description.strip(),
                    "avatar_url": raw_agent.avatar_url,
                    "icon": (raw_agent.icon or "").strip() or None,
                }
            )
        if not normalized_agents:
            normalized_agents = [{"id": "general", "display_name": "通用助手"}]

        actor_id = principal.actor_id if principal is not None else ""
        tenant_id = principal.tenant_id if principal is not None else ""
        service = ProjectGroupCreationService(
            project_store=_scoped_store(request),
            group_store=_group_store(),
            collaboration_store=collaboration_store,
            team_rooms_router=team_rooms_router,
            thread_store=thread_store,
            workspace_root=workspace_root,
            require_auth=require_auth,
        )
        try:
            created = service.create(
                request=request,
                name=body.name.strip(),
                goal=(body.goal or "").strip() or body.name.strip(),
                agents=normalized_agents,
                actor_id=actor_id,
                tenant_id=tenant_id,
                plan_project=lambda project_id: _engine(principal).plan(
                    body.name.strip(),
                    (body.goal or "").strip() or body.name.strip(),
                    project_id=project_id,
                ),
            )
        except (ProjectGroupBindingChanged, ProjectGroupCreationRecoveryPending) as exc:
            raise HTTPException(409, detail=exc.detail()) from exc
        except HTTPException:
            raise
        except ValueError as exc:
            raise _bad_request(exc) from exc
        except RuntimeError as exc:
            status = 503 if "not wired" in str(exc) else 500
            raise HTTPException(status, "project group creation failed") from exc
        except Exception as exc:  # noqa: BLE001 - keep store internals out of the API
            raise HTTPException(500, "project group creation failed") from exc

        return {
            "ok": True,
            **created["project_state"],
            "thread_id": created["thread_id"],
            "thread": created["thread"],
            "room": created["room"],
            "group": created["group_state"].to_dict(),
        }

    @router.post("/api/projects/move", dependencies=[Depends(_auth_dep)])
    def move_thread(request: Request, body: MoveThreadBody) -> dict[str, Any]:
        project = _project_or_404(request, body.project_id)
        _thread_access(request, body.thread_id, write=True)
        try:
            scoped = _scoped_store(request)
            projections.move_project_to_thread(
                request,
                body.thread_id,
                project.id,
                scoped,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc
        except PermissionError as exc:
            raise HTTPException(404, "project not found") from exc
        return {"ok": True, "thread_id": body.thread_id, "project_id": project.id}

    @router.delete("/api/projects/{project_id}", dependencies=[Depends(_auth_dep)])
    def delete_project(request: Request, project_id: str) -> dict[str, Any]:
        scoped = _scoped_store(request)
        try:
            _project_or_404(request, project_id)
        except HTTPException as exc:
            if exc.status_code != 404 or not projections.finalize_deleted_project_projections(
                project_id, scoped
            ):
                raise
            return {"ok": True, "project_id": project_id, "recovered": True}
        projections.delete_project(request, project_id, scoped)
        return {"ok": True, "project_id": project_id}

    @router.post("/api/projects/from-group/{thread_id}", dependencies=[Depends(_auth_dep)])
    def from_group(request: Request, thread_id: str, body: FromGroupBody) -> dict[str, Any]:
        """Attach Project OS to a cowork group and optionally start execution.

        The group remains a normal conversation surface. Its chat/cluster/swarm
        response strategy is independent from the persistent project binding.
        Project work starts only when the caller explicitly requests ``run``.
        """
        principal = _thread_access(request, thread_id, write=True)
        if body.run and require_auth:
            resolver = _execution_context_resolver(principal)
            try:
                if resolver is None:
                    raise RuntimeError("managed thread workspace resolver unavailable")
                resolver(thread_id)
            except (OSError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
                raise HTTPException(
                    409,
                    "project execution requires a verified managed thread workspace",
                ) from exc
        try:
            hooks = _base_hooks()
            resolver = _execution_context_resolver(principal)
            if resolver is not None:
                hooks["resolve_thread_context"] = resolver
            result = run_project_from_group(
                _scoped_store(request),
                _group_store(),
                thread_id,
                name=body.name,
                goal=body.goal,
                hooks=hooks,
                run=body.run,
                max_ticks=body.max_ticks,
                subagent_runner=subagent_runner,
                owner_id=principal.actor_id if principal is not None else "",
                tenant_id=principal.tenant_id if principal is not None else "",
                reuse_active=True,
            )
            if result.get("recovery_pending"):
                raise HTTPException(409, result.get("recovery") or result)
            # `run_project_from_group` only returns after `engine.run` has
            # crossed its external execution boundary. Projection
            # compensation must therefore retain this project on run=True.
            result["run_requested"] = body.run
            result["execution_started"] = body.run
            raw_project = result.get("project")
            project = raw_project if isinstance(raw_project, dict) else {}
            project_id = str(project.get("id") or "")
            if project_id:
                projections.project_group_projections_or_compensate(
                    request,
                    thread_id,
                    project_id,
                    result,
                )
            return result
        except ValueError as exc:
            raise _bad_request(exc) from exc
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, "project run failed") from exc

    @router.delete("/api/projects/from-group/{thread_id}", dependencies=[Depends(_auth_dep)])
    def detach_from_group(
        request: Request,
        thread_id: str,
        body: DetachFromGroupBody | None = None,
    ) -> dict[str, Any]:
        """Close a group's project capability without deleting the group.

        The project record and all project/chat history remain inspectable.
        Running or blocked work is protected unless the owner explicitly uses
        ``force``. The optional expected id makes UI retries safe against a
        concurrent rebind.
        """

        principal = _thread_access(request, thread_id, write=True)
        options = body or DetachFromGroupBody()
        scoped = _scoped_store(request)
        try:
            project, binding_generation = scoped.binding_snapshot(thread_id)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        if project is None:
            prior_project = None
            if options.expected_project_id:
                try:
                    prior_project = scoped.get_project(options.expected_project_id)
                except ValueError as exc:
                    raise _bad_request(exc) from exc
                if prior_project is not None:
                    try:
                        _clear_project_group_projections(
                            thread_id,
                            options.expected_project_id,
                            generation=binding_generation,
                        )
                    except Exception as exc:  # noqa: BLE001 - retryable cross-store cleanup
                        raise HTTPException(500, "project detach failed") from exc
            return {
                "ok": True,
                "thread_id": thread_id,
                "project_id": options.expected_project_id or "",
                "detached": False,
                "project": prior_project.to_dict() if prior_project is not None else None,
            }
        if options.expected_project_id and project.id != options.expected_project_id:
            raise HTTPException(
                409,
                {
                    "code": "PROJECT_BINDING_CHANGED",
                    "message": "thread project binding changed",
                    "project_id": project.id,
                },
            )
        # Legacy plans are persisted as ``running`` before the first tick even
        # though no work has started. ``started_at`` is the durable execution
        # boundary; blocked work is always considered active/recoverable.
        project_is_active = project.status == "blocked" or (
            project.status == "running" and bool(project.started_at)
        )
        if project_is_active and not options.force:
            raise HTTPException(
                409,
                {
                    "code": "PROJECT_ACTIVE",
                    "message": "project is still active; complete it or explicitly detach with force=true",
                    "project_id": project.id,
                    "status": project.status,
                    "force_required": True,
                },
            )

        try:
            detached, generation = scoped.unbind_thread_versioned(
                thread_id,
                expected_project_id=project.id,
                event_kind="project.detached_from_group",
                event_payload={
                    "thread_id": thread_id,
                    "actor": principal.actor_id if principal is not None else "local",
                    "force": options.force,
                    "status_at_detach": project.status,
                },
                reject_active=not options.force,
            )
            if detached is None:
                return {
                    "ok": True,
                    "thread_id": thread_id,
                    "project_id": project.id,
                    "detached": False,
                    "project": project.to_dict(),
                }
            try:
                _clear_project_group_projections(
                    thread_id,
                    project.id,
                    generation=generation,
                )
            except Exception as projection_error:
                projections.compensate_detach_projection_failure(
                    request,
                    thread_id,
                    project,
                    projection_error,
                )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ProjectBindingActiveError as exc:
            raise HTTPException(
                409,
                {
                    "code": "PROJECT_ACTIVE",
                    "message": "project is still active; complete it or explicitly detach with force=true",
                    "project_id": exc.project.id,
                    "status": exc.project.status,
                    "force_required": True,
                },
            ) from exc
        except PermissionError as exc:
            raise HTTPException(404, "project not found") from exc
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, "project detach failed") from exc

        refreshed = scoped.get_project(project.id)
        return {
            "ok": True,
            "thread_id": thread_id,
            "project_id": project.id,
            "detached": True,
            "project": (refreshed or detached).to_dict(),
        }

    @router.post("/api/projects/{project_id}/tick", dependencies=[Depends(_auth_dep)])
    def tick(request: Request, project_id: str) -> dict[str, Any]:
        """Advance the project one loop iteration."""
        _project_or_404(request, project_id)
        _require_execution_context(request, project_id)
        try:
            result = _engine(_principal(request)).tick(project_id)
            thread_project = _scoped_store(request).thread_for_project(project_id)
            _project_to_collaboration(request, project_id, thread_id=thread_project or "")
            return result
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.post("/api/projects/{project_id}/run", dependencies=[Depends(_auth_dep)])
    def run(request: Request, project_id: str, body: RunBody) -> dict[str, Any]:
        """Drive the loop until the project is done/blocked or max_ticks."""
        _project_or_404(request, project_id)
        _require_execution_context(request, project_id)
        try:
            result = _engine(_principal(request)).run(project_id, max_ticks=body.max_ticks)
            thread_project = _scoped_store(request).thread_for_project(project_id)
            _project_to_collaboration(request, project_id, thread_id=thread_project or "")
            return result
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.post("/api/projects/{project_id}/recover", dependencies=[Depends(_auth_dep)])
    def recover(request: Request, project_id: str, body: RecoverBody) -> dict[str, Any]:
        """Reopen blocked project work after an operator fixes the cause."""
        _project_or_404(request, project_id)
        engine = _engine(_principal(request))
        try:
            recovered = engine.recover(
                project_id,
                task_ids=body.task_ids,
                reset_attempts=body.reset_attempts,
                clear_outputs=body.clear_outputs,
            )
        except ProjectClaimActiveError as exc:
            raise _claim_active(exc) from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc
        if body.run:
            _require_execution_context(request, project_id)
            try:
                run_result = engine.run(project_id, max_ticks=body.max_ticks)
            except ValueError as exc:
                raise _bad_request(exc) from exc
            thread_project = _scoped_store(request).thread_for_project(project_id)
            _project_to_collaboration(request, project_id, thread_id=thread_project or "")
            return {
                "ok": True,
                "recover": recovered,
                "run": run_result,
                **_full_state(request, project_id),
            }
        thread_project = _scoped_store(request).thread_for_project(project_id)
        _project_to_collaboration(request, project_id, thread_id=thread_project or "")
        return {"ok": True, "recover": recovered, **_full_state(request, project_id)}

    @router.post(
        "/api/projects/{project_id}/tasks/{task_id}/intervene",
        dependencies=[Depends(_auth_dep)],
    )
    def intervene_task(
        request: Request,
        project_id: str,
        task_id: str,
        body: TaskInterventionBody,
    ) -> dict[str, Any]:
        """Manually reassign, reset, complete, or skip a task."""
        _project_or_404(request, project_id)
        engine = _engine(_principal(request))
        try:
            intervention = engine.intervene_task(
                project_id,
                task_id,
                action=body.action,
                assigned_agent=body.assigned_agent,
                assigned_role=body.assigned_role,
                output=body.output,
                reason=body.reason,
                reset_attempts=body.reset_attempts,
                cascade=body.cascade,
            )
        except ProjectClaimActiveError as exc:
            raise _claim_active(exc) from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc
        if any(str(event).startswith("task_not_found:") for event in intervention["events"]):
            raise HTTPException(404, "task not found")
        if any(str(event).startswith("unknown_task_action:") for event in intervention["events"]):
            raise HTTPException(400, "unknown task intervention action")
        if body.run:
            _require_execution_context(request, project_id)
            try:
                run_result = engine.run(project_id, max_ticks=body.max_ticks)
            except ValueError as exc:
                raise _bad_request(exc) from exc
            thread_project = _scoped_store(request).thread_for_project(project_id)
            _project_to_collaboration(request, project_id, thread_id=thread_project or "")
            return {
                "ok": True,
                "intervention": intervention,
                "run": run_result,
                **_full_state(request, project_id),
            }
        thread_project = _scoped_store(request).thread_for_project(project_id)
        _project_to_collaboration(request, project_id, thread_id=thread_project or "")
        return {
            "ok": True,
            "intervention": intervention,
            **_full_state(request, project_id),
        }

    return router
