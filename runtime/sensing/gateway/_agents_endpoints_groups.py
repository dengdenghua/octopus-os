"""Agent-group endpoints for the agents router.

Pure structural split of ``_agents_endpoints.py`` — no logic changes.
``_register_groups`` attaches the group CRUD + membership endpoints to the
injected router, only when a group registry is wired.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from fastapi import HTTPException, Request, Response

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    Response = None  # type: ignore[assignment, misc]

from ._agents_endpoints_shared import _AuthActions
from ._agents_helpers import _group_to_wire
from .agents_models import GroupCreate, GroupUpdate, GroupWire

if TYPE_CHECKING:
    from ._agents_endpoints import _AgentsCtx


def _register_groups(router: Any, ctx: _AgentsCtx, auth: _AuthActions) -> None:
    group_registry = ctx.group_registry
    registry = ctx.registry
    _auth = auth.auth
    _require_admin = auth.require_admin

    if group_registry is not None:
        from runtime.execution.agents.groups import AgentGroup, AgentGroupNotFound

        @router.get("/api/groups")
        def list_groups(request: Request) -> list[GroupWire]:
            _auth(request)  # AUTH-OK: actor-agnostic — group registry is server-global
            return [_group_to_wire(g) for g in group_registry.list_all()]

        @router.get("/api/groups/{group_id}")
        def get_group(request: Request, group_id: str) -> GroupWire:
            _auth(request)  # AUTH-OK: actor-agnostic — group registry is server-global
            try:
                g = group_registry.get(group_id)
            except AgentGroupNotFound as e:
                raise HTTPException(404, f"group not found: {group_id}") from e
            return _group_to_wire(g)

        @router.post("/api/groups", status_code=201)
        def create_group(
            request: Request,
            body: GroupCreate,
        ) -> GroupWire:
            _require_admin(request)  # Mutation: creates agent group in registry
            try:
                group_registry.create(
                    AgentGroup(
                        group_id=body.group_id,
                        display_name=body.display_name,
                        description=body.description,
                        members=body.members,
                    )
                )
            except ValueError as e:
                msg = str(e)
                code = 409 if "duplicate" in msg else 400
                raise HTTPException(code, msg) from e
            return _group_to_wire(group_registry.get(body.group_id))

        @router.put("/api/groups/{group_id}")
        def update_group(
            request: Request,
            group_id: str,
            body: GroupUpdate,
        ) -> GroupWire:
            _require_admin(request)  # Mutation: updates agent group
            try:
                g = group_registry.update(
                    group_id,
                    display_name=body.display_name,
                    description=body.description,
                )
            except AgentGroupNotFound as e:
                raise HTTPException(404, f"group not found: {group_id}") from e
            return _group_to_wire(g)

        @router.delete(
            "/api/groups/{group_id}", status_code=204, response_class=Response, response_model=None
        )
        def delete_group(request: Request, group_id: str):
            _require_admin(request)  # Mutation: deletes agent group
            if not group_registry.remove(group_id):
                raise HTTPException(404, f"group not found: {group_id}")
            return

        @router.post(
            "/api/groups/{group_id}/members/{agent_id}",
            status_code=200,
        )
        def add_member(
            request: Request,
            group_id: str,
            agent_id: str,
        ) -> dict[str, Any]:
            _require_admin(request)  # Mutation: adds agent to group
            if not registry.has(agent_id):
                raise HTTPException(404, f"agent not found: {agent_id}")
            try:
                added = group_registry.add_member(group_id, agent_id)
            except AgentGroupNotFound as e:
                raise HTTPException(404, f"group not found: {group_id}") from e
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            return {
                "group_id": group_id,
                "agent_id": agent_id,
                "added": added,
            }

        @router.delete(
            "/api/groups/{group_id}/members/{agent_id}",
            status_code=200,
        )
        def remove_member(
            request: Request,
            group_id: str,
            agent_id: str,
        ) -> dict[str, Any]:
            _require_admin(request)  # Mutation: removes agent from group
            try:
                removed = group_registry.remove_member(group_id, agent_id)
            except AgentGroupNotFound as e:
                raise HTTPException(404, f"group not found: {group_id}") from e
            return {
                "group_id": group_id,
                "agent_id": agent_id,
                "removed": removed,
            }

        @router.get("/api/groups/by-agent/{agent_id}")
        def groups_for_agent_ep(
            request: Request,
            agent_id: str,
        ) -> dict[str, Any]:
            _auth(request)  # AUTH-OK: actor-agnostic — group membership is server-global
            static_groups: list[str] = []
            if registry.has(agent_id):
                agent = registry.get(agent_id)
                static_groups = list(getattr(agent, "groups", []))
            dynamic_groups = group_registry.groups_for_agent(agent_id)
            effective = sorted(set(static_groups) | set(dynamic_groups))
            return {
                "agent_id": agent_id,
                "groups": effective,
                "static": static_groups,
                "dynamic": dynamic_groups,
            }
