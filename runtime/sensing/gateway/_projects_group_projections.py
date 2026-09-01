"""Project-group read-model projections and compensation helpers.

This module keeps the cross-store mechanics out of the HTTP router while the
router remains the owner of authentication, request validation, and response
shapes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request

from runtime.projectos.cowork_bridge import (
    full_project_state,
    project_task_to_collaboration,
)
from runtime.projectos.store import (
    ProjectAlreadyBoundError,
    ProjectBindingActiveError,
    ProjectClaimActiveError,
    ProjectStore,
)

_PROJECT_DELETE_PROJECTION_PENDING = "project.delete_projection_pending"


class ProjectGroupProjectionContext:
    """Coordinate Project OS projections without owning HTTP route policy."""

    def __init__(
        self,
        *,
        collaboration_store: Any,
        group_store: Callable[[], Any],
        scoped_store: Callable[[Request], ProjectStore],
        thread_store: Any,
        team_rooms_router: Any,
        require_auth: bool,
    ) -> None:
        self._collaboration_store = collaboration_store
        self._group_store = group_store
        self._scoped_store = scoped_store
        self._thread_store = thread_store
        self._team_rooms_router = team_rooms_router
        self._require_auth = require_auth

    def project_to_collaboration(
        self,
        request: Request,
        project_id: str,
        *,
        thread_id: str = "",
        strict: bool = False,
        binding_generation: int | None = None,
    ) -> None:
        if self._collaboration_store is None:
            return
        try:
            state = full_project_state(self._scoped_store(request), project_id)
            if state is None:
                return
            raw_project = state.get("project")
            project = raw_project if isinstance(raw_project, dict) else {}
            session_id = thread_id or f"project:{project_id}"
            room_id = f"project:{project_id}"
            promote_standalone_room = False
            if thread_id:
                try:
                    group_state = self._group_store().state(thread_id)
                    linked_room = getattr(group_state, "room_id", "") or ""
                    if linked_room:
                        room_id = str(linked_room)
                except Exception:  # noqa: BLE001
                    linked_room = ""
                if not linked_room:
                    room_for_session = getattr(
                        self._collaboration_store,
                        "room_for_session",
                        None,
                    )
                    current_room = (
                        room_for_session(thread_id) if callable(room_for_session) else None
                    )
                    standalone_room = (
                        room_for_session(f"project:{project_id}")
                        if callable(room_for_session) and current_room is None
                        else None
                    )
                    if isinstance(current_room, dict) and current_room.get("id"):
                        room_id = str(current_room["id"])
                    elif isinstance(standalone_room, dict) and standalone_room.get("id"):
                        room_id = str(standalone_room["id"])
                        promote_standalone_room = True
                    else:
                        room_id = f"collab-{thread_id}"
            room_payload = {
                "id": room_id,
                "name": project.get("name") or f"Project {project_id}",
                "metadata": {
                    "tenant_id": project.get("tenant_id") or "",
                    **({"thread_id": thread_id} if thread_id else {}),
                },
            }
            upsert_room = getattr(self._collaboration_store, "upsert_room", None)
            upsert_project_room = getattr(
                self._collaboration_store,
                "upsert_project_room",
                None,
            )
            projection_generation = 0 if not thread_id else binding_generation
            if callable(upsert_project_room) and projection_generation is not None:
                upsert_project_room(
                    session_id=session_id,
                    room=room_payload,
                    project_id=project_id,
                    generation=projection_generation,
                )
            elif promote_standalone_room or thread_id:
                if strict:
                    raise RuntimeError("versioned project room projection is unavailable")
                return
            elif callable(upsert_room):
                upsert_room(session_id, room_payload)
            else:
                if strict:
                    raise RuntimeError("collaboration room projection is unavailable")
                return
            set_project = getattr(
                self._collaboration_store,
                "set_room_project_metadata",
                None,
            )
            if callable(set_project):
                set_project(
                    session_id,
                    project_id,
                    generation=projection_generation,
                )
            elif strict:
                raise RuntimeError("collaboration project projection is unavailable")
            raw_milestones = state.get("milestones")
            milestones = raw_milestones if isinstance(raw_milestones, list) else []
            raw_tasks = state.get("tasks")
            tasks_by_ms = raw_tasks if isinstance(raw_tasks, dict) else {}
            for milestone in milestones:
                if not isinstance(milestone, dict):
                    continue
                milestone_id = str(milestone.get("id") or "")
                tasks = tasks_by_ms.get(milestone_id) if isinstance(tasks_by_ms, dict) else []
                if not isinstance(tasks, list):
                    continue
                for task in tasks:
                    if not isinstance(task, dict):
                        continue
                    project_task_to_collaboration(
                        self._collaboration_store,
                        session_id=session_id,
                        room_id=room_id,
                        project_id=project_id,
                        milestone_id=milestone_id,
                        task=task,
                        tenant_id=str(project.get("tenant_id") or ""),
                        binding_generation=projection_generation,
                    )
        except Exception:  # noqa: BLE001 - optional projections are best effort by default
            if strict:
                raise

    def project_to_bound_collaboration(
        self,
        request: Request,
        project_id: str,
        *,
        thread_id: str = "",
        strict: bool = False,
    ) -> None:
        """Project only from a fresh canonical binding generation."""

        generation: int | None = None
        if not thread_id:
            thread_id = self._scoped_store(request).thread_for_project(project_id) or ""
        if thread_id:
            canonical, generation = self._scoped_store(request).binding_snapshot(thread_id)
            if canonical is None or canonical.id != project_id:
                return
        self.project_to_collaboration(
            request,
            project_id,
            thread_id=thread_id,
            strict=strict,
            binding_generation=generation,
        )

    def set_thread_project_projection(
        self,
        thread_id: str,
        project_id: str | None,
        *,
        expected_project_id: str | None = None,
        strict_availability: bool = True,
        generation: int | None = None,
    ) -> None:
        if self._thread_store is None:
            return
        getter = getattr(self._thread_store, "get", None)
        if callable(getter) and getter(thread_id) is None:
            return
        setter = getattr(self._thread_store, "set_project_binding_metadata", None)
        if not callable(setter):
            if self._require_auth and strict_availability:
                raise RuntimeError("thread project projection is unavailable")
            return
        setter(
            thread_id,
            project_id,
            expected_project_id=expected_project_id,
            generation=generation,
        )

    def set_room_project_projection(
        self,
        thread_id: str,
        project_id: str | None,
        *,
        expected_project_id: str | None = None,
        strict_availability: bool = True,
        generation: int | None = None,
    ) -> None:
        if self._collaboration_store is None:
            return
        setter = getattr(self._collaboration_store, "set_room_project_metadata", None)
        if not callable(setter):
            if self._require_auth and strict_availability:
                raise RuntimeError("collaboration project projection is unavailable")
            return
        setter(
            thread_id,
            project_id,
            expected_project_id=expected_project_id,
            generation=generation,
        )

    def refresh_team_project_projection(self, thread_id: str) -> None:
        refresh = getattr(self._team_rooms_router, "refresh_project_binding", None)
        if callable(refresh):
            refresh(thread_id)

    def delete_project_task_projections(self, thread_id: str, project_id: str) -> None:
        if self._collaboration_store is None:
            return
        delete_tasks = getattr(self._collaboration_store, "delete_project_tasks", None)
        if not callable(delete_tasks):
            raise RuntimeError("collaboration project task cleanup is unavailable")
        delete_tasks(
            session_id=thread_id,
            project_id=project_id,
            source="projectos",
        )

    def delete_all_project_task_projections(self, project_id: str) -> None:
        if self._collaboration_store is None:
            return
        delete_tasks = getattr(
            self._collaboration_store,
            "delete_project_tasks_for_project",
            None,
        )
        if not callable(delete_tasks):
            raise RuntimeError("all-session project task cleanup is unavailable")
        delete_tasks(project_id=project_id, source="projectos")

    def tombstone_project_projections(self, project_id: str, delete_token: str) -> None:
        if self._collaboration_store is None:
            return
        tombstone = getattr(self._collaboration_store, "tombstone_project_projection", None)
        if not callable(tombstone):
            raise RuntimeError("project projection tombstone is unavailable")
        tombstone(project_id, delete_token)

    def project_projection_tombstone_token(self, project_id: str) -> str:
        if self._collaboration_store is None:
            return ""
        getter = getattr(
            self._collaboration_store,
            "project_projection_tombstone_token",
            None,
        )
        if not callable(getter):
            raise RuntimeError("project projection tombstone probe is unavailable")
        return str(getter(project_id) or "")

    def finalize_project_projection_tombstone(
        self,
        project_id: str,
        delete_token: str,
    ) -> None:
        if self._collaboration_store is None:
            return
        finalize = getattr(
            self._collaboration_store,
            "finalize_project_projection_tombstone",
            None,
        )
        if not callable(finalize):
            raise RuntimeError("project projection tombstone finalize is unavailable")
        finalize(project_id, delete_token)

    def finalize_deleted_project_projections(
        self,
        project_id: str,
        scoped: ProjectStore,
    ) -> bool:
        """Finish an interrupted external cleanup from the source tombstone."""

        delete_token = scoped.project_delete_tombstone_token(project_id)
        if not delete_token:
            return False
        self.finalize_project_projection_tombstone(project_id, delete_token)
        return True

    def project_group_projections(
        self,
        request: Request,
        thread_id: str,
        project_id: str,
        *,
        strict_availability: bool = True,
        generation: int | None = None,
    ) -> None:
        if generation is None:
            canonical, generation = self._scoped_store(request).binding_snapshot(thread_id)
            if canonical is None or canonical.id != project_id:
                raise RuntimeError("project binding changed before projection")
        self.set_thread_project_projection(
            thread_id,
            project_id,
            strict_availability=strict_availability,
            generation=generation,
        )
        self.project_to_collaboration(
            request,
            project_id,
            thread_id=thread_id,
            strict=True,
            binding_generation=generation,
        )
        self.set_room_project_projection(
            thread_id,
            project_id,
            strict_availability=strict_availability,
            generation=generation,
        )
        self.refresh_team_project_projection(thread_id)

    @staticmethod
    def _move_requires_detach(
        thread_id: str,
        requested_project_id: str,
        current_project_id: str,
    ) -> HTTPException:
        return HTTPException(
            409,
            {
                "code": "PROJECT_MOVE_REQUIRES_DETACH",
                "message": ("thread is already bound to another project; detach it before moving"),
                "thread_id": thread_id,
                "current_project_id": current_project_id,
                "requested_project_id": requested_project_id,
                "detach_required": True,
            },
        )

    @staticmethod
    def _move_binding_changed(
        thread_id: str,
        requested_project_id: str,
        winner_project_id: str,
    ) -> HTTPException:
        return HTTPException(
            409,
            {
                "code": "PROJECT_BINDING_CHANGED",
                "message": "thread project binding changed while projections were updating",
                "thread_id": thread_id,
                "requested_project_id": requested_project_id,
                "winner_project_id": winner_project_id,
            },
        )

    def _raise_move_projection_recovery(
        self,
        scoped: ProjectStore,
        thread_id: str,
        project_id: str,
        projection_error: Exception,
    ) -> None:
        recovery_recorded = False
        try:
            scoped.append_event(
                project_id,
                kind="project.group_projection_recovery_pending",
                payload={
                    "thread_id": thread_id,
                    "operation": "move",
                    "reason": type(projection_error).__name__,
                },
            )
        except Exception:  # noqa: BLE001 - the source binding still enables retry
            recovery_recorded = False
        else:
            recovery_recorded = True
        raise HTTPException(
            500,
            {
                "code": "PROJECT_PROJECTION_RECOVERY_REQUIRED",
                "message": "project binding was preserved but group projections need recovery",
                "project_id": project_id,
                "thread_id": thread_id,
                "recovery_recorded": recovery_recorded,
                "recovery": {
                    "method": "POST",
                    "path": "/api/projects/move",
                    "body": {"thread_id": thread_id, "project_id": project_id},
                },
            },
        ) from projection_error

    def move_project_to_thread(
        self,
        request: Request,
        thread_id: str,
        project_id: str,
        scoped: ProjectStore,
    ) -> None:
        """Attach without overwriting a binding or an active execution boundary."""

        current, _current_generation = scoped.binding_snapshot(thread_id)
        if current is not None and current.id != project_id:
            raise self._move_requires_detach(thread_id, project_id, current.id)
        try:
            canonical, _inserted, generation = scoped.bind_thread_if_absent_versioned(
                thread_id,
                project_id,
            )
        except ProjectAlreadyBoundError as exc:
            raise HTTPException(
                409,
                {
                    "code": "PROJECT_ALREADY_BOUND",
                    "message": "project is already bound to another thread; detach it first",
                    "project_id": project_id,
                    "canonical_thread_id": exc.canonical_thread_id,
                    "requested_thread_id": thread_id,
                },
            ) from exc
        except ProjectBindingActiveError as exc:
            raise HTTPException(
                409,
                {
                    "code": "TARGET_PROJECT_ACTIVE",
                    "message": "target project is already executing on another thread",
                    "thread_id": thread_id,
                    "project_id": project_id,
                    "execution_thread_id": exc.project.execution_thread_id,
                    "status": exc.project.status,
                    "started_at": exc.project.started_at,
                },
            ) from exc
        if canonical.id != project_id:
            raise self._move_requires_detach(thread_id, project_id, canonical.id)
        projection_error: Exception | None = None
        try:
            self.project_group_projections(
                request,
                thread_id,
                project_id,
                strict_availability=False,
                generation=generation,
            )
        except Exception as exc:  # noqa: BLE001 - reconcile against the durable winner below
            projection_error = exc
        try:
            winner, winner_generation = scoped.binding_snapshot(thread_id)
        except Exception as exc:  # noqa: BLE001 - source binding remains the recovery anchor
            self._raise_move_projection_recovery(scoped, thread_id, project_id, exc)
            return
        if winner is None:
            try:
                self.clear_project_group_projections(
                    thread_id,
                    project_id,
                    generation=winner_generation,
                )
            except Exception as exc:  # noqa: BLE001 - retry is anchored by the project row
                self._raise_move_projection_recovery(scoped, thread_id, project_id, exc)
            raise self._move_binding_changed(thread_id, project_id, "")
        if winner.id != project_id:
            try:
                self.replace_project_group_projections(
                    request,
                    thread_id,
                    project_id,
                    winner.id,
                )
            except Exception as exc:  # noqa: BLE001 - the winner is the recovery anchor
                self._raise_move_projection_recovery(scoped, thread_id, winner.id, exc)
            raise self._move_binding_changed(thread_id, project_id, winner.id)
        if projection_error is not None:
            self._raise_move_projection_recovery(
                scoped,
                thread_id,
                project_id,
                projection_error,
            )

    def clear_project_group_projections(
        self,
        thread_id: str,
        project_id: str,
        *,
        delete_project_tasks: bool = False,
        generation: int | None = None,
    ) -> None:
        self.set_thread_project_projection(
            thread_id,
            None,
            expected_project_id=project_id,
            generation=generation,
        )
        self.set_room_project_projection(
            thread_id,
            None,
            expected_project_id=project_id,
            generation=generation,
        )
        # Recompute join policy only after the generation-fenced room tombstone
        # is durable, so its generic Team Room payload cannot bypass the fence.
        self.refresh_team_project_projection(thread_id)
        if delete_project_tasks:
            self.delete_project_task_projections(thread_id, project_id)

    def compensate_detach_projection_failure(
        self,
        request: Request,
        thread_id: str,
        project: Any,
        projection_error: Exception,
    ) -> None:
        """Restore one detached binding or converge onto its concurrent winner."""

        scoped = self._scoped_store(request)
        restored = scoped.restore_thread_bindings(
            project.id,
            [thread_id],
            original_execution_thread_id=project.execution_thread_id,
        )
        winner_project_id = restored.conflict_project_ids.get(thread_id)
        if winner_project_id:
            try:
                self.replace_project_group_projections(
                    request,
                    thread_id,
                    project.id,
                    winner_project_id,
                )
            except Exception as compensation_error:
                raise RuntimeError(
                    "project detach and winner projection compensation failed"
                ) from compensation_error
            raise HTTPException(
                409,
                {
                    "code": "PROJECT_DETACH_COMPENSATION_CONFLICT",
                    "message": "a newer project binding won during detach compensation",
                    "thread_id": thread_id,
                    "project_id": project.id,
                    "winner_project_id": winner_project_id,
                },
            ) from projection_error
        if project.execution_thread_id and not restored.execution_restored:
            raise RuntimeError("project execution binding restore conflicted") from projection_error
        try:
            self.project_group_projections(
                request,
                thread_id,
                project.id,
                generation=restored.generations[thread_id],
            )
        except Exception as compensation_error:
            raise RuntimeError("project detach and compensation failed") from compensation_error
        scoped.append_event(
            project.id,
            kind="project.detach_compensated",
            payload={
                "thread_id": thread_id,
                "reason": type(projection_error).__name__,
            },
        )
        raise projection_error

    @staticmethod
    def _pending_delete_thread_ids(scoped: ProjectStore, project_id: str) -> set[str]:
        """Recover thread ids recorded before a delete crossed store boundaries."""

        thread_ids: set[str] = set()
        for event in scoped.events_for_project(project_id, limit=500):
            if event.get("kind") != _PROJECT_DELETE_PROJECTION_PENDING:
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            raw_thread_ids = payload.get("thread_ids")
            candidates = raw_thread_ids if isinstance(raw_thread_ids, list) else []
            legacy_thread_id = str(payload.get("thread_id") or "").strip()
            if legacy_thread_id:
                candidates = [*candidates, legacy_thread_id]
            thread_ids.update(
                thread_id
                for raw_thread_id in candidates
                if (thread_id := str(raw_thread_id or "").strip())
            )
        return thread_ids

    def replace_project_group_projections(
        self,
        request: Request,
        thread_id: str,
        _stale_project_id: str,
        winner_project_id: str,
    ) -> None:
        """Converge stale read models onto a newer authoritative CAS winner."""

        canonical, generation = self._scoped_store(request).binding_snapshot(thread_id)
        if canonical is None or canonical.id != winner_project_id:
            raise RuntimeError("project binding winner changed during projection repair")

        # The winner's generation is the authoritative replacement fence.
        # A same-generation clear would conflict with the following winner.
        self.project_group_projections(
            request,
            thread_id,
            winner_project_id,
            generation=generation,
        )

    def delete_project(
        self,
        request: Request,
        project_id: str,
        scoped: ProjectStore,
    ) -> None:
        collaboration_tombstoned = False
        try:
            delete_lease = scoped.begin_project_delete(
                project_id,
                event_kind=_PROJECT_DELETE_PROJECTION_PENDING,
            )
        except ProjectBindingActiveError as exc:
            raise HTTPException(
                409,
                {
                    "code": "PROJECT_ACTIVE",
                    "message": "project is still active and cannot be deleted",
                    "project_id": exc.project.id,
                    "status": exc.project.status,
                },
            ) from exc
        except ProjectClaimActiveError as exc:
            raise HTTPException(
                409,
                {
                    "code": "CLAIM_ACTIVE",
                    "message": "project has active worker claims and cannot be deleted",
                    "project_id": exc.project.id,
                    "task_ids": list(exc.task_ids),
                    "milestone_ids": list(exc.milestone_ids),
                },
            ) from exc
        delete_token = delete_lease.token
        try:
            cleanup_threads = list(delete_lease.thread_ids)
            for thread_id in cleanup_threads:
                canonical, generation = scoped.binding_snapshot(thread_id)
                if canonical is not None and canonical.id == project_id:
                    detached, generation = scoped.unbind_thread_for_delete(
                        thread_id,
                        project_id,
                        delete_token,
                    )
                    if detached is None:
                        raise RuntimeError("project binding changed during deletion")
                    canonical = None
                if canonical is not None:
                    self.replace_project_group_projections(
                        request,
                        thread_id,
                        project_id,
                        canonical.id,
                    )
                else:
                    self.clear_project_group_projections(
                        thread_id,
                        project_id,
                        generation=generation,
                    )
            self.tombstone_project_projections(project_id, delete_token)
            collaboration_tombstoned = True
            if not scoped.finalize_project_delete(project_id, delete_token):
                raise RuntimeError("project deletion did not remove its source row")
            self.finalize_project_projection_tombstone(project_id, delete_token)
        except Exception as delete_error:  # noqa: BLE001 - cross-store delete saga
            # Beginning deletion is the irreversible linearization point.
            # Every later failure keeps the same source claim and rolls
            # forward on an idempotent DELETE retry; no cross-store rollback
            # can race a concurrent retry into reopening a half-deleted source.
            projection_phase = "pending"
            if not collaboration_tombstoned:
                try:
                    observed_token = self.project_projection_tombstone_token(project_id)
                except Exception:  # noqa: BLE001 - uncertainty must fail closed
                    projection_phase = "unknown"
                else:
                    if observed_token == delete_token:
                        collaboration_tombstoned = True
                        projection_phase = "tombstoned"
                    elif observed_token:
                        projection_phase = "conflict"
            else:
                projection_phase = "tombstoned"
            try:
                finalized_token = scoped.project_delete_tombstone_token(project_id)
            except Exception:  # noqa: BLE001 - return retry contract below
                finalized_token = ""
            if finalized_token == delete_token:
                self.finalize_project_projection_tombstone(project_id, delete_token)
                return
            raise HTTPException(
                409,
                {
                    "code": "PROJECT_DELETE_RECOVERY_PENDING",
                    "message": "project delete is fenced and requires an idempotent retry",
                    "project_id": project_id,
                    "delete_token": delete_token,
                    "projection_phase": projection_phase,
                    "recovery": {
                        "method": "DELETE",
                        "path": f"/api/projects/{project_id}",
                    },
                },
            ) from delete_error

    def project_group_projections_or_compensate(
        self,
        request: Request,
        thread_id: str,
        project_id: str,
        result: dict[str, Any],
    ) -> None:
        raw_generation = result.get("binding_generation")
        generation = raw_generation if isinstance(raw_generation, int) else None
        try:
            self.project_group_projections(
                request,
                thread_id,
                project_id,
                generation=generation,
            )
        except Exception as projection_error:
            execution_started = bool(result.get("execution_started"))
            # A plan is public once its ProjectStore transaction commits. Any
            # automatic delete after that point can erase a concurrent task,
            # event, or operator update. Preserve the source and authoritative
            # binding for every failure stage, then expose attach-only repair.
            scoped = self._scoped_store(request)
            recovery_recorded = False
            try:
                scoped.append_event(
                    project_id,
                    kind="project.group_projection_recovery_pending",
                    payload={
                        "thread_id": thread_id,
                        "run_requested": bool(result.get("run_requested")),
                        "execution_started": execution_started,
                        "reason": type(projection_error).__name__,
                    },
                )
            except Exception:  # noqa: BLE001 - source binding remains the recovery anchor
                recovery_recorded = False
            else:
                recovery_recorded = True
            message = (
                "project execution was preserved but group projections need recovery"
                if execution_started
                else "project was preserved but group projections need recovery"
            )
            raise HTTPException(
                409,
                {
                    "code": "PROJECT_PROJECTION_RECOVERY_REQUIRED",
                    "message": message,
                    "project_id": project_id,
                    "thread_id": thread_id,
                    "run_requested": bool(result.get("run_requested")),
                    "execution_started": execution_started,
                    "recovery_recorded": recovery_recorded,
                    "recovery": {
                        "method": "POST",
                        "path": f"/api/projects/from-group/{thread_id}",
                        "run": False,
                    },
                },
            ) from projection_error
