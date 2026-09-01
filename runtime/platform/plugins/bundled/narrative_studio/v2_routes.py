"""HTTP surface for Narrative Studio v2 resources and human governance."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query, Request

from .models import (
    CanonCommitCreate,
    CanonReviewCommitRequest,
    ContextPackBuildRequest,
    ContextPackUpdate,
    EntityCreate,
    EntityUpdate,
    ForeshadowCreate,
    ForeshadowUpdate,
    PipelineRunCreate,
    PipelineRunUpdate,
    PipelineStageSubmit,
    ProjectUpdate,
    RelationshipCreate,
    RelationshipUpdate,
    ReviewRequestCreate,
    ReviewRequestUpdate,
    ReviewVoteCreate,
    ReviewVoteUpdate,
    StoryArcCreate,
    StoryArcUpdate,
)

if TYPE_CHECKING:
    from . import NarrativeStudioPlugin


def _items(rows: list[Any]) -> dict[str, Any]:
    values = [row.model_dump(mode="json") if hasattr(row, "model_dump") else row for row in rows]
    return {"items": values, "total": len(values)}


def _principal(request: Request) -> str | None:
    """Read only identities installed by server authentication middleware.

    Headers are intentionally ignored because an arbitrary client may forge
    them. If no middleware principal exists, the record is explicitly marked
    ``client_asserted`` by the route instead of pretending it was authenticated.
    """

    candidates = [request.scope.get("user")]
    state = request.scope.get("state") or {}
    if isinstance(state, dict):
        candidates.extend(state.get(key) for key in ("principal", "current_user", "user"))
    for candidate in candidates:
        if candidate is None:
            continue
        authenticated = getattr(candidate, "is_authenticated", True)
        if authenticated is False:
            continue
        if isinstance(candidate, dict):
            for key in ("actor_id", "id", "user_id", "sub", "username", "email"):
                value = candidate.get(key)
                if value:
                    return str(value)
            continue
        for key in (
            "actor_id",
            "id",
            "user_id",
            "sub",
            "username",
            "email",
            "display_name",
        ):
            value = getattr(candidate, key, None)
            if value:
                return str(value)
        text = str(candidate).strip()
        if text and text.lower() not in {"<unauthenticateduser>", "anonymous"}:
            return text
    return None


def _review_compat(value: dict[str, Any]) -> dict[str, Any]:
    """Present unresolved blockers in the native workbench's stable shape."""

    raw_blockers = value.get("blockers")
    blockers: list[str] = []
    if isinstance(raw_blockers, list):
        for blocker in raw_blockers:
            if isinstance(blocker, str):
                blockers.append(blocker)
            elif isinstance(blocker, dict):
                label = blocker.get("summary") or blocker.get("title") or blocker.get("id")
                if label:
                    blockers.append(str(label))
    return {
        **value,
        "review_revision": value.get("revision"),
        "revision": value.get("target_revision", value.get("revision", 1)),
        "review_blocking": value.get("blocking") is True,
        "blocking": bool(blockers),
        "blockers": blockers,
    }


def register_v2_routes(router: APIRouter, plugin: NarrativeStudioPlugin) -> None:
    store = plugin._require_store
    api = plugin._api

    @router.put("/projects/{project_id}")
    def update_project(project_id: str, body: ProjectUpdate) -> Any:
        return api(lambda: store().update_project(project_id, body))

    @router.get("/projects/{project_id}/story-arcs")
    def list_story_arcs(project_id: str, branch_id: str | None = Query(default=None)) -> Any:
        return api(lambda: _items(store().list_story_arcs(project_id, branch_id)))

    @router.post("/projects/{project_id}/story-arcs", status_code=201)
    def create_story_arc(project_id: str, body: StoryArcCreate) -> Any:
        return api(lambda: store().create_story_arc(project_id, body))

    @router.get("/projects/{project_id}/story-arcs/{record_id}")
    def get_story_arc(project_id: str, record_id: str) -> Any:
        return api(lambda: store().get_story_arc(project_id, record_id))

    @router.put("/projects/{project_id}/story-arcs/{record_id}")
    def update_story_arc(project_id: str, record_id: str, body: StoryArcUpdate) -> Any:
        return api(lambda: store().update_story_arc(project_id, record_id, body))

    @router.get("/projects/{project_id}/entities")
    def list_entities(project_id: str, branch_id: str | None = Query(default=None)) -> Any:
        return api(lambda: _items(store().list_entities(project_id, branch_id)))

    @router.post("/projects/{project_id}/entities", status_code=201)
    def create_entity(project_id: str, body: EntityCreate) -> Any:
        return api(lambda: store().create_entity(project_id, body))

    @router.get("/projects/{project_id}/entities/{record_id}")
    def get_entity(project_id: str, record_id: str) -> Any:
        return api(lambda: store().get_entity(project_id, record_id))

    @router.put("/projects/{project_id}/entities/{record_id}")
    def update_entity(project_id: str, record_id: str, body: EntityUpdate) -> Any:
        return api(lambda: store().update_entity(project_id, record_id, body))

    @router.get("/projects/{project_id}/relationships")
    def list_relationships(project_id: str, branch_id: str | None = Query(default=None)) -> Any:
        return api(lambda: _items(store().list_relationships(project_id, branch_id)))

    @router.post("/projects/{project_id}/relationships", status_code=201)
    def create_relationship(project_id: str, body: RelationshipCreate) -> Any:
        return api(lambda: store().create_relationship(project_id, body))

    @router.get("/projects/{project_id}/relationships/{record_id}")
    def get_relationship(project_id: str, record_id: str) -> Any:
        return api(lambda: store().get_relationship(project_id, record_id))

    @router.put("/projects/{project_id}/relationships/{record_id}")
    def update_relationship(project_id: str, record_id: str, body: RelationshipUpdate) -> Any:
        return api(lambda: store().update_relationship(project_id, record_id, body))

    @router.get("/projects/{project_id}/foreshadows")
    def list_foreshadows(project_id: str, branch_id: str | None = Query(default=None)) -> Any:
        return api(lambda: _items(store().list_foreshadows(project_id, branch_id)))

    @router.post("/projects/{project_id}/foreshadows", status_code=201)
    def create_foreshadow(project_id: str, body: ForeshadowCreate) -> Any:
        return api(lambda: store().create_foreshadow(project_id, body))

    @router.get("/projects/{project_id}/foreshadows/{record_id}")
    def get_foreshadow(project_id: str, record_id: str) -> Any:
        return api(lambda: store().get_foreshadow(project_id, record_id))

    @router.put("/projects/{project_id}/foreshadows/{record_id}")
    def update_foreshadow(project_id: str, record_id: str, body: ForeshadowUpdate) -> Any:
        return api(lambda: store().update_foreshadow(project_id, record_id, body))

    @router.get("/projects/{project_id}/context-packs")
    def list_context_packs(project_id: str) -> Any:
        return api(lambda: _items(store().list_context_packs(project_id)))

    @router.post("/projects/{project_id}/context-packs", status_code=201)
    def build_context_pack(project_id: str, body: ContextPackBuildRequest) -> Any:
        return api(lambda: store().build_context_pack(project_id, body))

    @router.get("/projects/{project_id}/context-packs/{record_id}")
    def get_context_pack(project_id: str, record_id: str) -> Any:
        return api(lambda: store().get_context_pack(project_id, record_id))

    @router.put("/projects/{project_id}/context-packs/{record_id}")
    def update_context_pack(project_id: str, record_id: str, body: ContextPackUpdate) -> Any:
        return api(lambda: store().update_context_pack(project_id, record_id, body))

    @router.get("/projects/{project_id}/pipelines")
    def list_pipelines(project_id: str) -> Any:
        return api(lambda: _items(store().list_pipeline_runs(project_id)))

    @router.post("/projects/{project_id}/pipelines", status_code=201)
    def create_pipeline(project_id: str, body: PipelineRunCreate) -> Any:
        return api(lambda: store().create_pipeline_run(project_id, body))

    @router.get("/projects/{project_id}/pipelines/{run_id}")
    def get_pipeline(project_id: str, run_id: str) -> Any:
        return api(lambda: store().get_pipeline_run(project_id, run_id))

    @router.put("/projects/{project_id}/pipelines/{run_id}")
    def update_pipeline(project_id: str, run_id: str, body: PipelineRunUpdate) -> Any:
        return api(lambda: store().update_pipeline_run(project_id, run_id, body))

    @router.put("/projects/{project_id}/pipelines/{run_id}/stages/{stage_id}")
    def submit_pipeline_stage(
        project_id: str,
        run_id: str,
        stage_id: str,
        body: PipelineStageSubmit,
    ) -> Any:
        return api(lambda: store().submit_pipeline_stage(project_id, run_id, stage_id, body))

    @router.get("/projects/{project_id}/reviews")
    def list_reviews(project_id: str) -> Any:
        return api(lambda: _items(store().list_review_details(project_id)))

    @router.post("/projects/{project_id}/reviews", status_code=201)
    def create_review(project_id: str, body: ReviewRequestCreate, request: Request) -> Any:
        principal = _principal(request)
        effective = body.model_copy(update={"requested_by": principal} if principal else {})
        source = "authenticated_principal" if principal else "client_asserted"
        return api(
            lambda: store().create_review_request(project_id, effective, actor_source=source)
        )

    @router.get("/projects/{project_id}/reviews/{review_id}")
    def get_review(project_id: str, review_id: str) -> Any:
        return api(lambda: store().review_detail(project_id, review_id))

    @router.put("/projects/{project_id}/reviews/{review_id}")
    def update_review(project_id: str, review_id: str, body: ReviewRequestUpdate) -> Any:
        return api(lambda: store().update_review_request(project_id, review_id, body))

    @router.get("/projects/{project_id}/reviews/{review_id}/votes")
    def list_review_votes(project_id: str, review_id: str) -> Any:
        return api(lambda: _items(store().list_review_votes(project_id, review_id)))

    @router.post("/projects/{project_id}/reviews/{review_id}/votes", status_code=201)
    def create_review_vote(
        project_id: str,
        review_id: str,
        body: ReviewVoteCreate,
        request: Request,
    ) -> Any:
        principal = _principal(request)
        effective = body.model_copy(update={"voter_id": principal} if principal else {})
        source = "authenticated_principal" if principal else "client_asserted"
        return api(
            lambda: store().create_review_vote(
                project_id,
                review_id,
                effective,
                actor_source=source,
            )
        )

    @router.get("/projects/{project_id}/reviews/{review_id}/votes/{vote_id}")
    def get_review_vote(project_id: str, review_id: str, vote_id: str) -> Any:
        def operation() -> Any:
            vote = store().get_review_vote(project_id, vote_id)
            if vote.review_request_id != review_id:
                from .store import NarrativeNotFound

                raise NarrativeNotFound("vote not found in review request")
            return vote

        return api(operation)

    @router.put("/projects/{project_id}/reviews/{review_id}/votes/{vote_id}")
    def update_review_vote(
        project_id: str,
        review_id: str,
        vote_id: str,
        body: ReviewVoteUpdate,
    ) -> Any:
        return api(lambda: store().update_review_vote(project_id, review_id, vote_id, body))

    @router.get("/projects/{project_id}/canon-commits")
    def list_canon_commits(project_id: str) -> Any:
        return api(lambda: _items(store().list_canon_commits(project_id)))

    @router.post("/projects/{project_id}/canon-commits", status_code=201)
    def create_canon_commit(project_id: str, body: CanonCommitCreate, request: Request) -> Any:
        principal = _principal(request)
        effective = body.model_copy(update={"committed_by": principal} if principal else {})
        source = "authenticated_principal" if principal else "client_asserted"
        return api(
            lambda: store().create_canon_commit(
                project_id,
                effective,
                actor_source=source,
            )
        )

    @router.get("/projects/{project_id}/canon-commits/{commit_id}")
    def get_canon_commit(project_id: str, commit_id: str) -> Any:
        return api(lambda: store().get_canon_commit(project_id, commit_id))

    # Native workbench compatibility aliases. These intentionally call the
    # same store methods as the canonical routes, so no governance rule can
    # diverge between the UI and other API clients.

    @router.get("/projects/{project_id}/arcs")
    def compat_list_arcs(project_id: str, branch_id: str | None = Query(default=None)) -> Any:
        return api(lambda: _items(store().list_story_arcs(project_id, branch_id)))

    @router.post("/projects/{project_id}/arcs", status_code=201)
    def compat_create_arc(project_id: str, body: StoryArcCreate) -> Any:
        return api(lambda: store().create_story_arc(project_id, body))

    @router.get("/projects/{project_id}/arcs/{record_id}")
    def compat_get_arc(project_id: str, record_id: str) -> Any:
        return api(lambda: store().get_story_arc(project_id, record_id))

    @router.put("/projects/{project_id}/arcs/{record_id}")
    def compat_update_arc(project_id: str, record_id: str, body: StoryArcUpdate) -> Any:
        return api(lambda: store().update_story_arc(project_id, record_id, body))

    @router.get("/projects/{project_id}/pipeline-runs")
    def compat_list_pipeline_runs(project_id: str) -> Any:
        return api(lambda: _items(store().list_pipeline_runs(project_id)))

    @router.post("/projects/{project_id}/pipeline-runs", status_code=201)
    def compat_create_pipeline_run(project_id: str, body: PipelineRunCreate) -> Any:
        return api(lambda: store().create_pipeline_run(project_id, body))

    @router.get("/projects/{project_id}/pipeline-runs/{run_id}")
    def compat_get_pipeline_run(project_id: str, run_id: str) -> Any:
        return api(lambda: store().get_pipeline_run(project_id, run_id))

    @router.put("/projects/{project_id}/pipeline-runs/{run_id}")
    def compat_update_pipeline_run(project_id: str, run_id: str, body: PipelineRunUpdate) -> Any:
        return api(lambda: store().update_pipeline_run(project_id, run_id, body))

    @router.post("/projects/{project_id}/pipeline-runs/{run_id}/stages/{stage_id}/submit")
    def compat_submit_pipeline_stage(
        project_id: str,
        run_id: str,
        stage_id: str,
        body: PipelineStageSubmit,
    ) -> Any:
        return api(lambda: store().submit_pipeline_stage(project_id, run_id, stage_id, body))

    @router.get("/projects/{project_id}/review-requests")
    def compat_list_review_requests(project_id: str) -> Any:
        def operation() -> dict[str, Any]:
            rows = [_review_compat(row) for row in store().list_review_details(project_id)]
            return _items(rows)

        return api(operation)

    @router.post("/projects/{project_id}/review-requests", status_code=201)
    def compat_create_review_request(
        project_id: str, body: ReviewRequestCreate, request: Request
    ) -> Any:
        principal = _principal(request)
        effective = body.model_copy(update={"requested_by": principal} if principal else {})
        source = "authenticated_principal" if principal else "client_asserted"

        def operation() -> dict[str, Any]:
            review = store().create_review_request(
                project_id,
                effective,
                actor_source=source,
            )
            return _review_compat(store().review_detail(project_id, review.id))

        return api(operation)

    @router.get("/projects/{project_id}/review-requests/{review_id}")
    def compat_get_review_request(project_id: str, review_id: str) -> Any:
        return api(lambda: _review_compat(store().review_detail(project_id, review_id)))

    @router.put("/projects/{project_id}/review-requests/{review_id}")
    def compat_update_review_request(
        project_id: str, review_id: str, body: ReviewRequestUpdate
    ) -> Any:
        def operation() -> dict[str, Any]:
            store().update_review_request(project_id, review_id, body)
            return _review_compat(store().review_detail(project_id, review_id))

        return api(operation)

    @router.get("/projects/{project_id}/review-requests/{review_id}/votes")
    def compat_list_review_votes(project_id: str, review_id: str) -> Any:
        return api(lambda: _items(store().list_review_votes(project_id, review_id)))

    @router.post(
        "/projects/{project_id}/review-requests/{review_id}/votes",
        status_code=201,
    )
    def compat_create_review_vote(
        project_id: str,
        review_id: str,
        body: ReviewVoteCreate,
        request: Request,
    ) -> Any:
        principal = _principal(request)
        effective = body.model_copy(update={"voter_id": principal} if principal else {})
        source = "authenticated_principal" if principal else "client_asserted"

        def operation() -> dict[str, Any]:
            store().create_review_vote(
                project_id,
                review_id,
                effective,
                actor_source=source,
            )
            return _review_compat(store().review_detail(project_id, review_id))

        return api(operation)

    @router.post("/projects/{project_id}/review-requests/{review_id}/commit", status_code=201)
    def compat_commit_review(
        project_id: str,
        review_id: str,
        body: CanonReviewCommitRequest,
        request: Request,
    ) -> Any:
        principal = _principal(request)
        source = "authenticated_principal" if principal else "client_asserted"
        commit_body = CanonCommitCreate(
            review_request_id=review_id,
            confirm=body.confirm,
            committed_by=principal or body.actor,
            message=body.rationale,
        )
        return api(
            lambda: store().create_canon_commit(
                project_id,
                commit_body,
                actor_source=source,
            )
        )


__all__ = ["register_v2_routes"]
