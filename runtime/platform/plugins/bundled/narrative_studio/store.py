"""Atomic, project-isolated JSON storage and governance for Narrative Studio."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import unicodedata
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from .models import (
    PIPELINE_STAGE_ORDER,
    BranchCreate,
    CandidateRevision,
    CanonCommit,
    CanonCommitCreate,
    Chapter,
    ChapterCreate,
    ChapterUpdate,
    ContextPack,
    ContextPackBuildRequest,
    ContextPackUpdate,
    ContextSource,
    Entity,
    EntityCreate,
    EntityUpdate,
    FactCreate,
    Foreshadow,
    ForeshadowCreate,
    ForeshadowUpdate,
    GovernancePolicy,
    NarrativeFact,
    NarrativeProject,
    PipelineRun,
    PipelineRunCreate,
    PipelineRunUpdate,
    PipelineStage,
    PipelineStageSubmit,
    ProjectCreate,
    ProjectUpdate,
    Relationship,
    RelationshipCreate,
    RelationshipUpdate,
    ReviewRequest,
    ReviewRequestCreate,
    ReviewRequestUpdate,
    ReviewTargetType,
    ReviewVote,
    ReviewVoteCreate,
    ReviewVoteUpdate,
    RevisionActorSource,
    RevisionOperation,
    RevisionTargetType,
    Scene,
    SceneCreate,
    SceneUpdate,
    StateChange,
    StateChangeCreate,
    StoryArc,
    StoryArcCreate,
    StoryArcUpdate,
    StoryBranch,
    WorldPack,
    WorldPackCreate,
    WorldResource,
    utc_now,
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_V2_SCHEMA = "echo.narrative-studio.project.v2"
_BASE_COLLECTIONS = (
    "world_packs",
    "branches",
    "chapters",
    "scenes",
    "facts",
    "state_changes",
)
_V2_COLLECTIONS = (
    "story_arcs",
    "entities",
    "relationships",
    "foreshadows",
    "context_packs",
    "pipeline_runs",
    "review_requests",
    "review_votes",
    "canon_commits",
)
_COLLECTIONS = (*_BASE_COLLECTIONS, *_V2_COLLECTIONS)
_VERSIONED_COLLECTIONS: dict[str, RevisionTargetType] = {
    "chapters": "chapter",
    "scenes": "scene",
}
_TARGET_COLLECTIONS: dict[ReviewTargetType, tuple[str, type[BaseModel]]] = {
    "world_pack": ("world_packs", WorldPack),
    "branch": ("branches", StoryBranch),
    "story_arc": ("story_arcs", StoryArc),
    "chapter": ("chapters", Chapter),
    "scene": ("scenes", Scene),
    "fact": ("facts", NarrativeFact),
    "state_change": ("state_changes", StateChange),
    "entity": ("entities", Entity),
    "relationship": ("relationships", Relationship),
    "foreshadow": ("foreshadows", Foreshadow),
    "context_pack": ("context_packs", ContextPack),
    "pipeline_run": ("pipeline_runs", PipelineRun),
}
T = TypeVar("T", bound=BaseModel)


class NarrativeStoreError(ValueError):
    pass


class NarrativeNotFound(NarrativeStoreError):
    pass


class NarrativeConflict(NarrativeStoreError):
    pass


def _slug(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    clean = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:48]
    return clean or fallback


def _new_id(prefix: str, label: str = "") -> str:
    stem = _slug(label, prefix)
    return f"{prefix}-{stem}-{uuid4().hex[:8]}"[:80]


def _validate_id(value: str, label: str = "id") -> str:
    clean = (value or "").strip().lower()
    if not _ID_RE.fullmatch(clean):
        raise NarrativeStoreError(f"invalid {label}: use lowercase letters, digits, '-' or '_'")
    return clean


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


def _request_changes(request: BaseModel) -> dict[str, Any]:
    return {
        key: value for key, value in request.model_dump().items() if key in request.model_fields_set
    }


class NarrativeStore:
    """One directory per story project; every record is an atomic JSON file."""

    def __init__(
        self,
        data_dir: Path | str,
        *,
        default_review_quorum: int = 2,
        default_approval_ratio: float = 0.67,
        context_max_chars: int = 60_000,
        context_max_items: int = 200,
        history_max_snapshot_bytes: int = 16 * 1024 * 1024,
        history_max_revisions_per_record: int = 500,
        history_max_entries_per_project: int = 20_000,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.projects_dir = self.data_dir / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.default_governance = GovernancePolicy(
            review_quorum=default_review_quorum,
            approval_ratio=default_approval_ratio,
        )
        self.context_max_chars = max(256, int(context_max_chars))
        self.context_max_items = max(1, int(context_max_items))
        self.history_max_snapshot_bytes = max(1024, int(history_max_snapshot_bytes))
        self.history_max_revisions_per_record = max(1, int(history_max_revisions_per_record))
        self.history_max_entries_per_project = max(1, int(history_max_entries_per_project))
        self._lock = threading.RLock()

    # -- Project lifecycle and v1 -> v2 migration ---------------------------------

    def create_project(self, data: ProjectCreate) -> NarrativeProject:
        project_id = (
            _validate_id(data.id, "project id") if data.id else _new_id("project", data.title)
        )
        branch_id = "main"
        governance = self.default_governance.model_copy(
            update={
                "review_quorum": data.review_quorum or self.default_governance.review_quorum,
                "approval_ratio": data.approval_ratio or self.default_governance.approval_ratio,
            }
        )
        project = NarrativeProject(
            id=project_id,
            title=data.title,
            premise=data.premise,
            language=data.language,
            default_branch_id=branch_id,
            governance=governance,
        )
        project_dir = self._project_dir(project_id)
        with self._lock:
            if project_dir.exists():
                raise NarrativeConflict(f"project already exists: {project_id}")
            staging = self.projects_dir / f".{project_id}.{uuid4().hex}.tmp"
            try:
                for collection in _COLLECTIONS:
                    (staging / collection).mkdir(parents=True, exist_ok=True)
                (staging / "revisions" / "chapters").mkdir(parents=True, exist_ok=True)
                (staging / "revisions" / "scenes").mkdir(parents=True, exist_ok=True)
                _atomic_write_json(staging / "project.json", project.model_dump(mode="json"))
                main = StoryBranch(
                    id=branch_id,
                    project_id=project_id,
                    name="Main candidate branch",
                    purpose="Default non-canonical writing branch",
                )
                _atomic_write_json(
                    staging / "branches" / f"{branch_id}.json", main.model_dump(mode="json")
                )
                os.replace(staging, project_dir)
            except BaseException:
                if staging.exists():
                    import shutil

                    shutil.rmtree(staging, ignore_errors=True)
                raise
        return project

    def list_projects(self) -> list[NarrativeProject]:
        rows: list[NarrativeProject] = []
        with self._lock:
            for path in sorted(self.projects_dir.glob("*/project.json")):
                try:
                    rows.append(self._load_project(path, persist_migration=True))
                except (OSError, ValueError, TypeError):
                    continue
        return sorted(rows, key=lambda row: (row.updated_at, row.id), reverse=True)

    def get_project(self, project_id: str) -> NarrativeProject:
        path = self._project_dir(project_id) / "project.json"
        if not path.is_file():
            raise NarrativeNotFound(f"project not found: {project_id}")
        with self._lock:
            return self._load_project(path, persist_migration=True)

    def update_project(self, project_id: str, data: ProjectUpdate) -> NarrativeProject:
        current = self.get_project(project_id)
        changes = _request_changes(data)
        governance = current.governance
        if "review_quorum" in changes or "approval_ratio" in changes:
            governance = governance.model_copy(
                update={
                    "review_quorum": changes.pop("review_quorum", governance.review_quorum),
                    "approval_ratio": changes.pop("approval_ratio", governance.approval_ratio),
                }
            )
            changes["governance"] = governance
        changes["updated_at"] = utc_now()
        updated = NarrativeProject.model_validate({**current.model_dump(mode="python"), **changes})
        with self._lock:
            _atomic_write_json(
                self._project_dir(current.id) / "project.json",
                updated.model_dump(mode="json"),
            )
        return updated

    def project_detail(self, project_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        # Keep the v1 `counts` response stable for existing clients. New clients
        # get every v2 collection in the additive `extended_counts` field.
        base_counts = {
            name: len(self._record_paths(project.id, name)) for name in _BASE_COLLECTIONS
        }
        extended = {name: len(self._record_paths(project.id, name)) for name in _COLLECTIONS}
        return {
            "project": project.model_dump(mode="json"),
            "counts": base_counts,
            "extended_counts": extended,
        }

    def _load_project(self, path: Path, *, persist_migration: bool) -> NarrativeProject:
        payload = self._read_json(path)
        original_schema = str(payload.get("schema_version") or "pre-v1")
        if original_schema in {"pre-v1", "echo.narrative-studio.project.v1"}:
            payload = copy.deepcopy(payload)
            payload["schema_version"] = _V2_SCHEMA
            payload["migrated_from"] = original_schema
            payload.setdefault("governance", self.default_governance.model_dump(mode="json"))
            project = NarrativeProject.model_validate(payload)
            if persist_migration:
                _atomic_write_json(path, project.model_dump(mode="json"))
            return project
        return NarrativeProject.model_validate(payload)

    # -- v1 candidate records -------------------------------------------------------

    def create_world_pack(self, project_id: str, data: WorldPackCreate) -> WorldPack:
        pid = self._require_project(project_id).id
        record = WorldPack(
            id=_validate_id(data.id, "world pack id") if data.id else _new_id("pack", data.name),
            project_id=pid,
            name=data.name,
            summary=data.summary,
            resources=data.resources,
            metadata=data.metadata,
        )
        return self._create_record(pid, "world_packs", record)

    def create_imported_world_pack(
        self,
        project_id: str,
        *,
        name: str,
        source_root: str,
        resources: list[WorldResource],
        metadata: dict[str, Any],
    ) -> WorldPack:
        pid = self._require_project(project_id).id
        record = WorldPack(
            id=_new_id("pack", name),
            project_id=pid,
            name=name,
            summary=f"Candidate import ({len(resources)} text resources).",
            source_kind="echo-universe-engine",
            source_root=source_root,
            resources=resources,
            metadata=metadata,
        )
        return self._create_record(pid, "world_packs", record)

    def create_branch(self, project_id: str, data: BranchCreate) -> StoryBranch:
        pid = self._require_project(project_id).id
        if data.base_branch_id:
            self.get_branch(pid, data.base_branch_id)
        record = StoryBranch(
            id=_validate_id(data.id, "branch id") if data.id else _new_id("branch", data.name),
            project_id=pid,
            name=data.name,
            base_branch_id=data.base_branch_id,
            purpose=data.purpose,
        )
        return self._create_record(pid, "branches", record)

    def create_chapter(
        self,
        project_id: str,
        data: ChapterCreate,
        *,
        actor: str = "local",
        actor_source: RevisionActorSource = "local",
    ) -> Chapter:
        pid = self._require_project(project_id).id
        branch = self.get_branch(pid, data.branch_id)
        record = Chapter(
            id=_validate_id(data.id, "chapter id") if data.id else _new_id("chapter", data.title),
            project_id=pid,
            branch_id=branch.id,
            ordinal=data.ordinal,
            title=data.title,
            summary=data.summary,
            body=data.body,
            status=data.status,
        )
        return self._create_versioned_record(
            pid,
            "chapters",
            record,
            actor=actor,
            actor_source=actor_source,
        )

    def update_chapter(
        self,
        project_id: str,
        chapter_id: str,
        data: ChapterUpdate,
        *,
        expected_revision: int | None = None,
        actor: str = "local",
        actor_source: RevisionActorSource = "local",
    ) -> Chapter:
        changes = _request_changes(data)
        body_expected = changes.pop("expected_revision", None)
        expected = expected_revision if expected_revision is not None else body_expected
        with self._lock:
            current = self.get_chapter(project_id, chapter_id)
            return self._update_versioned_record(
                current,
                "chapters",
                changes,
                Chapter,
                expected_revision=expected,
                actor=actor,
                actor_source=actor_source,
            )

    def create_scene(
        self,
        project_id: str,
        chapter_id: str,
        data: SceneCreate,
        *,
        actor: str = "local",
        actor_source: RevisionActorSource = "local",
    ) -> Scene:
        pid = self._require_project(project_id).id
        chapter = self.get_chapter(pid, chapter_id)
        if chapter.branch_id != _validate_id(data.branch_id, "branch id"):
            raise NarrativeStoreError("scene branch must match its chapter branch")
        if data.pov_character_id:
            entity = self.get_entity(pid, data.pov_character_id)
            if entity.kind != "character":
                raise NarrativeStoreError("scene point of view must reference a character")
        record = Scene(
            id=_validate_id(data.id, "scene id") if data.id else _new_id("scene", data.title),
            project_id=pid,
            branch_id=chapter.branch_id,
            chapter_id=chapter.id,
            ordinal=data.ordinal,
            title=data.title,
            goal=data.goal,
            conflict=data.conflict,
            outcome=data.outcome,
            pov_character_id=data.pov_character_id,
            body=data.body,
            status=data.status,
        )
        return self._create_versioned_record(
            pid,
            "scenes",
            record,
            actor=actor,
            actor_source=actor_source,
        )

    def update_scene(
        self,
        project_id: str,
        chapter_id: str,
        scene_id: str,
        data: SceneUpdate,
        *,
        expected_revision: int | None = None,
        actor: str = "local",
        actor_source: RevisionActorSource = "local",
    ) -> Scene:
        changes = _request_changes(data)
        body_expected = changes.pop("expected_revision", None)
        expected = expected_revision if expected_revision is not None else body_expected
        with self._lock:
            current = self.get_scene(project_id, scene_id)
            if current.chapter_id != _validate_id(chapter_id, "chapter id"):
                raise NarrativeNotFound("scene not found in chapter")
            if changes.get("pov_character_id"):
                entity = self.get_entity(project_id, str(changes["pov_character_id"]))
                if entity.kind != "character":
                    raise NarrativeStoreError("scene point of view must reference a character")
            return self._update_versioned_record(
                current,
                "scenes",
                changes,
                Scene,
                expected_revision=expected,
                actor=actor,
                actor_source=actor_source,
            )

    def create_fact(self, project_id: str, data: FactCreate) -> NarrativeFact:
        pid = self._require_project(project_id).id
        branch_id = self._optional_branch(pid, data.branch_id)
        if data.scope != "world" and branch_id is None:
            raise NarrativeStoreError("non-world facts require branch_id")
        record = NarrativeFact(
            id=_validate_id(data.id, "fact id") if data.id else _new_id("fact", data.subject),
            project_id=pid,
            branch_id=branch_id,
            subject=data.subject,
            predicate=data.predicate,
            object=data.object,
            scope=data.scope,
            source_refs=data.source_refs,
            confidence=data.confidence,
        )
        return self._create_record(pid, "facts", record)

    def create_state_change(self, project_id: str, data: StateChangeCreate) -> StateChange:
        pid = self._require_project(project_id).id
        branch = self.get_branch(pid, data.branch_id)
        chapter_id = None
        scene_id = None
        if data.chapter_id:
            chapter = self.get_chapter(pid, data.chapter_id)
            if chapter.branch_id != branch.id:
                raise NarrativeStoreError("state change chapter belongs to another branch")
            chapter_id = chapter.id
        if data.scene_id:
            scene = self.get_scene(pid, data.scene_id)
            if scene.branch_id != branch.id:
                raise NarrativeStoreError("state change scene belongs to another branch")
            if chapter_id and scene.chapter_id != chapter_id:
                raise NarrativeStoreError("state change scene belongs to another chapter")
            chapter_id = chapter_id or scene.chapter_id
            scene_id = scene.id
        record = StateChange(
            id=(
                _validate_id(data.id, "state change id")
                if data.id
                else _new_id("change", f"{data.entity_id}-{data.field}")
            ),
            project_id=pid,
            branch_id=branch.id,
            chapter_id=chapter_id,
            scene_id=scene_id,
            entity_id=data.entity_id,
            field=data.field,
            before=data.before,
            after=data.after,
            reason=data.reason,
            source_refs=data.source_refs,
        )
        return self._create_record(pid, "state_changes", record)

    # -- Structured world model ------------------------------------------------------

    def create_story_arc(self, project_id: str, data: StoryArcCreate) -> StoryArc:
        pid = self._require_project(project_id).id
        branch = self.get_branch(pid, data.branch_id)
        self._validate_arc_range(data.start_chapter_ordinal, data.end_chapter_ordinal)
        record = StoryArc(
            id=_validate_id(data.id, "story arc id") if data.id else _new_id("arc", data.name),
            project_id=pid,
            branch_id=branch.id,
            name=data.name,
            summary=data.summary,
            start_chapter_ordinal=data.start_chapter_ordinal,
            end_chapter_ordinal=data.end_chapter_ordinal,
            beats=data.beats,
            status=data.status,
        )
        return self._create_record(pid, "story_arcs", record)

    def update_story_arc(self, project_id: str, record_id: str, data: StoryArcUpdate) -> StoryArc:
        current = self.get_story_arc(project_id, record_id)
        changes = _request_changes(data)
        start = changes.get("start_chapter_ordinal", current.start_chapter_ordinal)
        end = changes.get("end_chapter_ordinal", current.end_chapter_ordinal)
        self._validate_arc_range(start, end)
        return self._update_record(current, "story_arcs", changes, StoryArc)

    def create_entity(self, project_id: str, data: EntityCreate) -> Entity:
        pid = self._require_project(project_id).id
        branch_id = self._optional_branch(pid, data.branch_id)
        record = Entity(
            id=_validate_id(data.id, "entity id") if data.id else _new_id("entity", data.name),
            project_id=pid,
            branch_id=branch_id,
            kind=data.kind,
            name=data.name,
            summary=data.summary,
            aliases=data.aliases,
            attributes=data.attributes,
            source_refs=data.source_refs,
        )
        return self._create_record(pid, "entities", record)

    def update_entity(self, project_id: str, record_id: str, data: EntityUpdate) -> Entity:
        return self._update_record(
            self.get_entity(project_id, record_id),
            "entities",
            _request_changes(data),
            Entity,
        )

    def create_relationship(self, project_id: str, data: RelationshipCreate) -> Relationship:
        pid = self._require_project(project_id).id
        branch_id = self._optional_branch(pid, data.branch_id)
        from_entity = self.get_entity(pid, data.from_entity_id)
        to_entity = self.get_entity(pid, data.to_entity_id)
        record = Relationship(
            id=(
                _validate_id(data.id, "relationship id")
                if data.id
                else _new_id("relation", f"{from_entity.name}-{to_entity.name}")
            ),
            project_id=pid,
            branch_id=branch_id,
            from_entity_id=from_entity.id,
            to_entity_id=to_entity.id,
            kind=data.kind,
            summary=data.summary,
            bidirectional=data.bidirectional,
            attributes=data.attributes,
            source_refs=data.source_refs,
        )
        return self._create_record(pid, "relationships", record)

    def update_relationship(
        self, project_id: str, record_id: str, data: RelationshipUpdate
    ) -> Relationship:
        return self._update_record(
            self.get_relationship(project_id, record_id),
            "relationships",
            _request_changes(data),
            Relationship,
        )

    def create_foreshadow(self, project_id: str, data: ForeshadowCreate) -> Foreshadow:
        pid = self._require_project(project_id).id
        branch = self.get_branch(pid, data.branch_id)
        setup_id = self._chapter_in_branch(pid, data.setup_chapter_id, branch.id)
        payoff_id = self._chapter_in_branch(pid, data.payoff_chapter_id, branch.id)
        record = Foreshadow(
            id=(
                _validate_id(data.id, "foreshadow id")
                if data.id
                else _new_id("foreshadow", data.title)
            ),
            project_id=pid,
            branch_id=branch.id,
            title=data.title,
            setup=data.setup,
            intended_payoff=data.intended_payoff,
            setup_chapter_id=setup_id,
            payoff_chapter_id=payoff_id,
            status=data.status,
            source_refs=data.source_refs,
        )
        return self._create_record(pid, "foreshadows", record)

    def update_foreshadow(
        self, project_id: str, record_id: str, data: ForeshadowUpdate
    ) -> Foreshadow:
        current = self.get_foreshadow(project_id, record_id)
        changes = _request_changes(data)
        if "payoff_chapter_id" in changes:
            changes["payoff_chapter_id"] = self._chapter_in_branch(
                project_id, changes["payoff_chapter_id"], current.branch_id
            )
        return self._update_record(current, "foreshadows", changes, Foreshadow)

    # -- Bounded, source-cited context ------------------------------------------------

    def build_context_pack(self, project_id: str, data: ContextPackBuildRequest) -> ContextPack:
        pid = self._require_project(project_id).id
        branch = self.get_branch(pid, data.branch_id)
        target = self.get_chapter(pid, data.target_chapter_id) if data.target_chapter_id else None
        if target and target.branch_id != branch.id:
            raise NarrativeStoreError("context target chapter belongs to another branch")
        max_chars = min(data.max_chars or self.context_max_chars, self.context_max_chars)
        max_items = min(data.max_items or self.context_max_items, self.context_max_items)
        candidates = self._context_candidates(pid, branch.id, target)
        sources: list[ContextSource] = []
        blocks: list[str] = []
        used = 0
        any_source_truncated = False
        for kind, ref, title, raw_content in candidates:
            if len(sources) >= max_items:
                break
            content = str(raw_content).strip()
            if not content:
                continue
            separator = "\n\n" if blocks else ""
            header = f"[{ref}] {title}\n"
            remaining = max_chars - used - len(separator) - len(header)
            if remaining <= 0:
                break
            excerpt = content[:remaining]
            source_truncated = len(excerpt) < len(content)
            block = f"{header}{excerpt}"
            blocks.append(block)
            used += len(separator) + len(block)
            sources.append(
                ContextSource(
                    ref=ref,
                    kind=kind,
                    title=title,
                    content=excerpt,
                    char_count=len(excerpt),
                    truncated=source_truncated,
                )
            )
            any_source_truncated = any_source_truncated or source_truncated
            if used >= max_chars:
                break
        content = "\n\n".join(blocks)
        omitted_count = max(0, len(candidates) - len(sources))
        record = ContextPack(
            id=(
                _validate_id(data.id, "context pack id")
                if data.id
                else _new_id("context", data.label or branch.name)
            ),
            project_id=pid,
            branch_id=branch.id,
            target_chapter_id=target.id if target else None,
            label=data.label,
            max_chars=max_chars,
            max_items=max_items,
            total_chars=len(content),
            estimated_tokens=(len(content) + 3) // 4,
            omitted_count=omitted_count,
            sources=sources,
            content=content,
            truncated=any_source_truncated or omitted_count > 0,
        )
        return self._create_record(pid, "context_packs", record)

    def update_context_pack(
        self, project_id: str, record_id: str, data: ContextPackUpdate
    ) -> ContextPack:
        return self._update_record(
            self.get_context_pack(project_id, record_id),
            "context_packs",
            _request_changes(data),
            ContextPack,
        )

    def _context_candidates(
        self, project_id: str, branch_id: str, target: Chapter | None
    ) -> list[tuple[str, str, str, str]]:
        rows: list[tuple[str, str, str, str]] = []
        for pack in self.list_world_packs(project_id):
            if pack.summary:
                rows.append(("world_pack", f"world_pack:{pack.id}", pack.name, pack.summary))
            for resource in pack.resources:
                text = resource.excerpt or f"{resource.category}: {resource.relative_path}"
                rows.append(
                    (
                        "world_resource",
                        f"world_pack:{pack.id}/resource:{resource.relative_path}",
                        resource.relative_path,
                        text,
                    )
                )
        for arc in self.list_story_arcs(project_id, branch_id):
            content = "\n".join([arc.summary, *arc.beats]).strip()
            rows.append(("story_arc", f"story_arc:{arc.id}@r{arc.revision}", arc.name, content))
        for fact in self.list_facts(project_id, branch_id):
            rows.append(
                (
                    "fact",
                    f"fact:{fact.id}@r{fact.revision}",
                    fact.subject,
                    f"{fact.subject} — {fact.predicate} — {fact.object}",
                )
            )
        for entity in self.list_entities(project_id, branch_id):
            attributes = json.dumps(entity.attributes, ensure_ascii=False, sort_keys=True)
            text = "\n".join(
                value for value in (entity.summary, attributes) if value and value != "{}"
            )
            rows.append(("entity", f"entity:{entity.id}@r{entity.revision}", entity.name, text))
        for relation in self.list_relationships(project_id, branch_id):
            text = f"{relation.from_entity_id} — {relation.kind} — {relation.to_entity_id}"
            if relation.summary:
                text += f"\n{relation.summary}"
            rows.append(
                (
                    "relationship",
                    f"relationship:{relation.id}@r{relation.revision}",
                    relation.kind,
                    text,
                )
            )
        for item in self.list_foreshadows(project_id, branch_id):
            text = f"Setup: {item.setup}\nPayoff: {item.intended_payoff}\nStatus: {item.status}"
            rows.append(
                (
                    "foreshadow",
                    f"foreshadow:{item.id}@r{item.revision}",
                    item.title,
                    text,
                )
            )
        chapters = self.list_chapters(project_id, branch_id)
        if target:
            chapters = [chapter for chapter in chapters if chapter.ordinal < target.ordinal]
        for chapter in chapters:
            text = "\n".join(value for value in (chapter.summary, chapter.body) if value)
            rows.append(
                (
                    "previous_chapter",
                    f"chapter:{chapter.id}@r{chapter.revision}",
                    f"{chapter.ordinal}. {chapter.title}",
                    text,
                )
            )
        for change in self.list_state_changes(project_id, branch_id):
            text = (
                f"{change.entity_id}.{change.field}: "
                f"{json.dumps(change.before, ensure_ascii=False)} -> "
                f"{json.dumps(change.after, ensure_ascii=False)}"
            )
            if change.reason:
                text += f"\n{change.reason}"
            rows.append(
                (
                    "branch_state",
                    f"state_change:{change.id}@r{change.revision}",
                    f"{change.entity_id}.{change.field}",
                    text,
                )
            )
        return rows

    # -- Candidate-only pipeline ------------------------------------------------------

    def create_pipeline_run(self, project_id: str, data: PipelineRunCreate) -> PipelineRun:
        pid = self._require_project(project_id).id
        branch = self.get_branch(pid, data.branch_id)
        chapter_id = self._chapter_in_branch(pid, data.chapter_id, branch.id)
        context_pack_id = None
        if data.context_pack_id:
            pack = self.get_context_pack(pid, data.context_pack_id)
            if pack.branch_id != branch.id:
                raise NarrativeStoreError("pipeline context pack belongs to another branch")
            context_pack_id = pack.id
        stages = [
            PipelineStage(id=name, name=name, ordinal=index)
            for index, name in enumerate(PIPELINE_STAGE_ORDER, start=1)
        ]
        record = PipelineRun(
            id=(
                _validate_id(data.id, "pipeline run id")
                if data.id
                else _new_id("pipeline", data.goal)
            ),
            project_id=pid,
            branch_id=branch.id,
            chapter_id=chapter_id,
            context_pack_id=context_pack_id,
            goal=data.goal,
            stages=stages,
        )
        return self._create_record(pid, "pipeline_runs", record)

    def update_pipeline_run(
        self, project_id: str, run_id: str, data: PipelineRunUpdate
    ) -> PipelineRun:
        current = self.get_pipeline_run(project_id, run_id)
        if current.status == "complete":
            raise NarrativeConflict("completed pipeline runs are immutable")
        if current.status == "active" and data.status == "active":
            return current
        return self._update_record(
            current,
            "pipeline_runs",
            {"status": data.status},
            PipelineRun,
        )

    def submit_pipeline_stage(
        self,
        project_id: str,
        run_id: str,
        stage_name: str,
        data: PipelineStageSubmit,
    ) -> PipelineRun:
        with self._lock:
            current = self.get_pipeline_run(project_id, run_id)
            if current.status != "active":
                raise NarrativeConflict("pipeline run is not active")
            if current.current_stage != stage_name:
                raise NarrativeConflict(
                    f"pipeline expects stage {current.current_stage}, not {stage_name}"
                )
            stages = [stage.model_copy(deep=True) for stage in current.stages]
            target = next((stage for stage in stages if stage.id == stage_name), None)
            if target is None:
                raise NarrativeStoreError(f"unknown pipeline stage: {stage_name}")
            if target.status == "submitted":
                raise NarrativeConflict(f"pipeline stage already submitted: {stage_name}")
            target.status = "submitted"
            target.output = data.output
            target.source_refs = data.source_refs
            target.submitted_by = data.submitted_by
            target.submitted_at = utc_now()
            target.updated_at = target.submitted_at
            next_index = target.ordinal
            next_stage = (
                PIPELINE_STAGE_ORDER[next_index] if next_index < len(PIPELINE_STAGE_ORDER) else None
            )
            status = "active" if next_stage else "complete"
            return self._update_record(
                current,
                "pipeline_runs",
                {"stages": stages, "current_stage": next_stage, "status": status},
                PipelineRun,
            )

    # -- Human review and immutable canon commits ------------------------------------

    def create_review_request(
        self, project_id: str, data: ReviewRequestCreate, *, actor_source: str
    ) -> ReviewRequest:
        pid = self._require_project(project_id).id
        target = self.get_candidate(project_id, data.target_type, data.target_id)
        record = ReviewRequest(
            id=(
                _validate_id(data.id, "review request id")
                if data.id
                else _new_id("review", data.title)
            ),
            project_id=pid,
            target_type=data.target_type,
            target_id=str(target.id),
            target_revision=int(target.revision),
            title=data.title,
            summary=data.summary,
            blocking=data.blocking,
            requested_by=data.requested_by,
            actor_source=actor_source,
        )
        return self._create_record(pid, "review_requests", record)

    def update_review_request(
        self, project_id: str, review_id: str, data: ReviewRequestUpdate
    ) -> ReviewRequest:
        current = self.get_review_request(project_id, review_id)
        if data.status == "resolved" and not data.resolution.strip():
            raise NarrativeStoreError("resolving a review requires a resolution")
        return self._update_record(
            current,
            "review_requests",
            data.model_dump(),
            ReviewRequest,
        )

    def create_review_vote(
        self,
        project_id: str,
        review_id: str,
        data: ReviewVoteCreate,
        *,
        actor_source: str,
    ) -> ReviewVote:
        with self._lock:
            pid = self._require_project(project_id).id
            review = self.get_review_request(pid, review_id)
            voter_key = data.voter_id.strip().casefold()
            for vote in self.list_review_votes(pid, review.id):
                if vote.voter_id.strip().casefold() == voter_key:
                    raise NarrativeConflict("review voter has already voted")
            record = ReviewVote(
                id=_new_id("vote", data.voter_id),
                project_id=pid,
                review_request_id=review.id,
                voter_id=data.voter_id,
                decision=data.decision,
                rationale=data.rationale,
                actor_source=actor_source,
            )
            return self._create_record(pid, "review_votes", record)

    def update_review_vote(
        self,
        project_id: str,
        review_id: str,
        vote_id: str,
        data: ReviewVoteUpdate,
    ) -> ReviewVote:
        current = self.get_review_vote(project_id, vote_id)
        if current.review_request_id != _validate_id(review_id, "review request id"):
            raise NarrativeNotFound("vote not found in review request")
        return self._update_record(
            current,
            "review_votes",
            data.model_dump(),
            ReviewVote,
        )

    def review_detail(self, project_id: str, review_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        review = self.get_review_request(project.id, review_id)
        votes = self.list_review_votes(project.id, review.id)
        approve = sum(vote.decision == "approve" for vote in votes)
        reject = sum(vote.decision == "reject" for vote in votes)
        abstain = sum(vote.decision == "abstain" for vote in votes)
        decisive = approve + reject
        approval_ratio = approve / decisive if decisive else 0.0
        blockers = [
            row
            for row in self.list_review_requests(project.id)
            if row.target_type == review.target_type
            and row.target_id == review.target_id
            and row.blocking
            and row.status == "open"
        ]
        return {
            **review.model_dump(mode="json"),
            "votes": [vote.model_dump(mode="json") for vote in votes],
            "vote_counts": {
                "approve": approve,
                "reject": reject,
                "abstain": abstain,
                "total": len(votes),
            },
            "quorum_required": project.governance.review_quorum,
            "quorum_received": len(votes),
            "approval_ratio_required": project.governance.approval_ratio,
            "approval_ratio": approval_ratio,
            "blockers": [blocker.model_dump(mode="json") for blocker in blockers],
        }

    def list_review_details(self, project_id: str) -> list[dict[str, Any]]:
        return [
            self.review_detail(project_id, review.id)
            for review in self.list_review_requests(project_id)
        ]

    def create_canon_commit(
        self,
        project_id: str,
        data: CanonCommitCreate,
        *,
        actor_source: str,
    ) -> CanonCommit:
        with self._lock:
            return self._create_canon_commit_locked(
                project_id,
                data,
                actor_source=actor_source,
            )

    def _create_canon_commit_locked(
        self,
        project_id: str,
        data: CanonCommitCreate,
        *,
        actor_source: str,
    ) -> CanonCommit:
        if not data.confirm:
            raise NarrativeStoreError("canon commit requires confirm=true")
        project = self.get_project(project_id)
        review = self.get_review_request(project.id, data.review_request_id)
        detail = self.review_detail(project.id, review.id)
        if detail["quorum_received"] < detail["quorum_required"]:
            raise NarrativeConflict("review quorum has not been reached")
        if detail["approval_ratio"] < detail["approval_ratio_required"]:
            raise NarrativeConflict("review approval ratio has not been reached")
        if detail["blockers"]:
            raise NarrativeConflict("unresolved blocking reviews prevent canon commit")
        target = self.get_candidate(project.id, review.target_type, review.target_id)
        if int(target.revision) != review.target_revision:
            raise NarrativeConflict("candidate changed after review was requested")
        for existing in self.list_canon_commits(project.id):
            if existing.review_request_id == review.id:
                raise NarrativeConflict("review already has a canon commit")
        snapshot = copy.deepcopy(target.model_dump(mode="json"))
        canonical_json = json.dumps(
            snapshot,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        commit = CanonCommit(
            id=_new_id("canon", review.target_id),
            project_id=project.id,
            review_request_id=review.id,
            target_type=review.target_type,
            target_id=review.target_id,
            target_revision=review.target_revision,
            snapshot=snapshot,
            snapshot_sha256=hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
            governance={
                "review_quorum": detail["quorum_required"],
                "quorum_received": detail["quorum_received"],
                "approval_ratio_required": detail["approval_ratio_required"],
                "approval_ratio": detail["approval_ratio"],
                "vote_counts": detail["vote_counts"],
                "actor_source": actor_source,
            },
            committed_by=data.committed_by,
            message=data.message,
            actor_source=actor_source,
        )
        return self._create_record(project.id, "canon_commits", commit)

    def get_candidate(self, project_id: str, target_type: ReviewTargetType, target_id: str) -> Any:
        collection, model = _TARGET_COLLECTIONS[target_type]
        return self._get_record(project_id, collection, target_id, model)

    # -- Immutable chapter / scene revision history --------------------------------

    def list_chapter_revisions(self, project_id: str, chapter_id: str) -> list[CandidateRevision]:
        current = self.get_chapter(project_id, chapter_id)
        return self._list_candidate_revisions(current, "chapters")

    def get_chapter_revision(
        self, project_id: str, chapter_id: str, revision: int
    ) -> CandidateRevision:
        current = self.get_chapter(project_id, chapter_id)
        return self._get_candidate_revision(current, "chapters", revision)

    def restore_chapter_revision(
        self,
        project_id: str,
        chapter_id: str,
        revision: int,
        *,
        expected_revision: int | None = None,
        actor: str = "local",
        actor_source: RevisionActorSource = "local",
        message: str = "",
    ) -> Chapter:
        with self._lock:
            current = self.get_chapter(project_id, chapter_id)
            return self._restore_candidate_revision(
                current,
                "chapters",
                revision,
                Chapter,
                expected_revision=expected_revision,
                actor=actor,
                actor_source=actor_source,
                message=message,
            )

    def list_scene_revisions(
        self, project_id: str, chapter_id: str, scene_id: str
    ) -> list[CandidateRevision]:
        current = self._scene_in_chapter(project_id, chapter_id, scene_id)
        return self._list_candidate_revisions(current, "scenes")

    def get_scene_revision(
        self,
        project_id: str,
        chapter_id: str,
        scene_id: str,
        revision: int,
    ) -> CandidateRevision:
        current = self._scene_in_chapter(project_id, chapter_id, scene_id)
        return self._get_candidate_revision(current, "scenes", revision)

    def restore_scene_revision(
        self,
        project_id: str,
        chapter_id: str,
        scene_id: str,
        revision: int,
        *,
        expected_revision: int | None = None,
        actor: str = "local",
        actor_source: RevisionActorSource = "local",
        message: str = "",
    ) -> Scene:
        with self._lock:
            current = self._scene_in_chapter(project_id, chapter_id, scene_id)
            return self._restore_candidate_revision(
                current,
                "scenes",
                revision,
                Scene,
                expected_revision=expected_revision,
                actor=actor,
                actor_source=actor_source,
                message=message,
            )

    # -- Listing and lookup -----------------------------------------------------------

    def list_world_packs(self, project_id: str) -> list[WorldPack]:
        return self._list_records(project_id, "world_packs", WorldPack)

    def list_branches(self, project_id: str) -> list[StoryBranch]:
        return self._list_records(project_id, "branches", StoryBranch)

    def list_chapters(self, project_id: str, branch_id: str | None = None) -> list[Chapter]:
        rows = self._list_records(project_id, "chapters", Chapter)
        if branch_id:
            clean = _validate_id(branch_id, "branch id")
            rows = [row for row in rows if row.branch_id == clean]
        return sorted(rows, key=lambda row: (row.ordinal, row.id))

    def list_scenes(self, project_id: str, chapter_id: str | None = None) -> list[Scene]:
        rows = self._list_records(project_id, "scenes", Scene)
        if chapter_id:
            clean = _validate_id(chapter_id, "chapter id")
            rows = [row for row in rows if row.chapter_id == clean]
        return sorted(rows, key=lambda row: (row.ordinal, row.id))

    def list_facts(self, project_id: str, branch_id: str | None = None) -> list[NarrativeFact]:
        rows = self._list_records(project_id, "facts", NarrativeFact)
        if branch_id:
            clean = _validate_id(branch_id, "branch id")
            rows = [row for row in rows if row.branch_id in (None, clean)]
        return rows

    def list_state_changes(
        self, project_id: str, branch_id: str | None = None
    ) -> list[StateChange]:
        rows = self._list_records(project_id, "state_changes", StateChange)
        if branch_id:
            clean = _validate_id(branch_id, "branch id")
            rows = [row for row in rows if row.branch_id == clean]
        return rows

    def list_story_arcs(self, project_id: str, branch_id: str | None = None) -> list[StoryArc]:
        rows = self._list_records(project_id, "story_arcs", StoryArc)
        return self._filter_branch(rows, branch_id, include_global=False)

    def list_entities(self, project_id: str, branch_id: str | None = None) -> list[Entity]:
        rows = self._list_records(project_id, "entities", Entity)
        return self._filter_branch(rows, branch_id, include_global=True)

    def list_relationships(
        self, project_id: str, branch_id: str | None = None
    ) -> list[Relationship]:
        rows = self._list_records(project_id, "relationships", Relationship)
        return self._filter_branch(rows, branch_id, include_global=True)

    def list_foreshadows(self, project_id: str, branch_id: str | None = None) -> list[Foreshadow]:
        rows = self._list_records(project_id, "foreshadows", Foreshadow)
        return self._filter_branch(rows, branch_id, include_global=False)

    def list_context_packs(self, project_id: str) -> list[ContextPack]:
        return self._list_records(project_id, "context_packs", ContextPack)

    def list_pipeline_runs(self, project_id: str) -> list[PipelineRun]:
        return self._list_records(project_id, "pipeline_runs", PipelineRun)

    def list_review_requests(self, project_id: str) -> list[ReviewRequest]:
        return self._list_records(project_id, "review_requests", ReviewRequest)

    def list_review_votes(self, project_id: str, review_id: str) -> list[ReviewVote]:
        clean = _validate_id(review_id, "review request id")
        rows = self._list_records(project_id, "review_votes", ReviewVote)
        return [row for row in rows if row.review_request_id == clean]

    def list_canon_commits(self, project_id: str) -> list[CanonCommit]:
        return self._list_records(project_id, "canon_commits", CanonCommit)

    def get_branch(self, project_id: str, record_id: str) -> StoryBranch:
        return self._get_record(project_id, "branches", record_id, StoryBranch)

    def get_chapter(self, project_id: str, record_id: str) -> Chapter:
        return self._get_record(project_id, "chapters", record_id, Chapter)

    def get_scene(self, project_id: str, record_id: str) -> Scene:
        return self._get_record(project_id, "scenes", record_id, Scene)

    def get_scene_in_chapter(self, project_id: str, chapter_id: str, scene_id: str) -> Scene:
        return self._scene_in_chapter(project_id, chapter_id, scene_id)

    def get_story_arc(self, project_id: str, record_id: str) -> StoryArc:
        return self._get_record(project_id, "story_arcs", record_id, StoryArc)

    def get_entity(self, project_id: str, record_id: str) -> Entity:
        return self._get_record(project_id, "entities", record_id, Entity)

    def get_relationship(self, project_id: str, record_id: str) -> Relationship:
        return self._get_record(project_id, "relationships", record_id, Relationship)

    def get_foreshadow(self, project_id: str, record_id: str) -> Foreshadow:
        return self._get_record(project_id, "foreshadows", record_id, Foreshadow)

    def get_context_pack(self, project_id: str, record_id: str) -> ContextPack:
        return self._get_record(project_id, "context_packs", record_id, ContextPack)

    def get_pipeline_run(self, project_id: str, record_id: str) -> PipelineRun:
        return self._get_record(project_id, "pipeline_runs", record_id, PipelineRun)

    def get_review_request(self, project_id: str, record_id: str) -> ReviewRequest:
        return self._get_record(project_id, "review_requests", record_id, ReviewRequest)

    def get_review_vote(self, project_id: str, record_id: str) -> ReviewVote:
        return self._get_record(project_id, "review_votes", record_id, ReviewVote)

    def get_canon_commit(self, project_id: str, record_id: str) -> CanonCommit:
        return self._get_record(project_id, "canon_commits", record_id, CanonCommit)

    # -- Internal invariants ----------------------------------------------------------

    def _optional_branch(self, project_id: str, branch_id: str | None) -> str | None:
        return self.get_branch(project_id, branch_id).id if branch_id else None

    def _chapter_in_branch(
        self, project_id: str, chapter_id: str | None, branch_id: str
    ) -> str | None:
        if not chapter_id:
            return None
        chapter = self.get_chapter(project_id, chapter_id)
        if chapter.branch_id != branch_id:
            raise NarrativeStoreError("chapter belongs to another branch")
        return chapter.id

    @staticmethod
    def _validate_arc_range(start: int | None, end: int | None) -> None:
        if start is not None and end is not None and end < start:
            raise NarrativeStoreError("story arc end must not precede its start")

    @staticmethod
    def _filter_branch(rows: list[T], branch_id: str | None, *, include_global: bool) -> list[T]:
        if not branch_id:
            return rows
        clean = _validate_id(branch_id, "branch id")
        accepted = (None, clean) if include_global else (clean,)
        return [row for row in rows if getattr(row, "branch_id", None) in accepted]

    def _require_project(self, project_id: str) -> NarrativeProject:
        return self.get_project(project_id)

    def _project_dir(self, project_id: str) -> Path:
        return self.projects_dir / _validate_id(project_id, "project id")

    def _record_path(self, project_id: str, collection: str, record_id: str) -> Path:
        if collection not in _COLLECTIONS:
            raise NarrativeStoreError("unknown collection")
        return self._project_dir(project_id) / collection / f"{_validate_id(record_id)}.json"

    def _record_paths(self, project_id: str, collection: str) -> list[Path]:
        if collection not in _COLLECTIONS:
            raise NarrativeStoreError("unknown collection")
        directory = self._project_dir(project_id) / collection
        return sorted(directory.glob("*.json")) if directory.is_dir() else []

    def _revision_dir(self, project_id: str, collection: str, record_id: str) -> Path:
        if collection not in _VERSIONED_COLLECTIONS:
            raise NarrativeStoreError("record type does not support revision history")
        return (
            self._project_dir(project_id)
            / "revisions"
            / collection
            / _validate_id(record_id, f"{_VERSIONED_COLLECTIONS[collection]} id")
        )

    def _revision_path(
        self,
        project_id: str,
        collection: str,
        record_id: str,
        revision: int,
    ) -> Path:
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise NarrativeStoreError("revision must be a positive integer")
        return self._revision_dir(project_id, collection, record_id) / f"{revision}.json"

    def _revision_paths(self, project_id: str, collection: str, record_id: str) -> list[Path]:
        directory = self._revision_dir(project_id, collection, record_id)
        if not directory.is_dir():
            return []
        paths = [path for path in directory.glob("*.json") if path.stem.isdigit()]
        return sorted(paths, key=lambda path: int(path.stem))

    def _history_entry_count(self, project_id: str) -> int:
        root = self._project_dir(project_id) / "revisions"
        if not root.is_dir():
            return 0
        return sum(1 for path in root.glob("*/*/*.json") if path.is_file())

    @staticmethod
    def _normalized_actor(actor: str) -> str:
        clean = str(actor or "").strip()
        if not clean:
            clean = "local"
        if len(clean) > 240:
            raise NarrativeStoreError("revision actor exceeds 240 characters")
        return clean

    def _make_candidate_revision(
        self,
        record: T,
        collection: str,
        *,
        operation: RevisionOperation,
        actor: str,
        actor_source: RevisionActorSource,
        message: str = "",
        restored_from_revision: int | None = None,
        history_origin: str = "native",
        reconstructed: bool = False,
    ) -> CandidateRevision:
        target_type = _VERSIONED_COLLECTIONS.get(collection)
        if target_type is None:
            raise NarrativeStoreError("record type does not support revision history")
        snapshot = copy.deepcopy(record.model_dump(mode="json"))
        if snapshot.get("canon_status", "candidate") != "candidate":
            raise NarrativeStoreError("only candidate artifacts may enter revision history")
        canonical = json.dumps(
            snapshot,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(canonical) > self.history_max_snapshot_bytes:
            raise NarrativeStoreError(
                "revision snapshot exceeds the configured single-entry byte limit"
            )
        created_at = str(snapshot.get("updated_at") or snapshot.get("created_at") or utc_now())
        return CandidateRevision(
            project_id=str(record.project_id),  # type: ignore[attr-defined]
            target_type=target_type,
            target_id=str(record.id),  # type: ignore[attr-defined]
            revision=int(record.revision),  # type: ignore[attr-defined]
            snapshot=snapshot,
            snapshot_sha256=hashlib.sha256(canonical).hexdigest(),
            snapshot_bytes=len(canonical),
            operation=operation,
            actor=self._normalized_actor(actor),
            actor_source=actor_source,
            message=message,
            restored_from_revision=restored_from_revision,
            history_origin=history_origin,
            reconstructed=reconstructed,
            created_at=created_at,
        )

    def _assert_history_capacity(self, project_id: str, collection: str, record_id: str) -> None:
        if (
            len(self._revision_paths(project_id, collection, record_id))
            >= self.history_max_revisions_per_record
        ):
            raise NarrativeConflict("revision history limit reached for this record")
        if self._history_entry_count(project_id) >= self.history_max_entries_per_project:
            raise NarrativeConflict("revision history limit reached for this project")

    def _validate_revision_record(
        self,
        value: CandidateRevision,
        current: T,
        collection: str,
    ) -> CandidateRevision:
        expected_type = _VERSIONED_COLLECTIONS[collection]
        if (
            value.project_id != str(current.project_id)  # type: ignore[attr-defined]
            or value.target_type != expected_type
            or value.target_id != str(current.id)  # type: ignore[attr-defined]
            or value.revision != int(value.snapshot.get("revision", -1))
            or value.snapshot.get("project_id") != str(current.project_id)  # type: ignore[attr-defined]
            or value.snapshot.get("id") != str(current.id)  # type: ignore[attr-defined]
        ):
            raise NarrativeStoreError("revision snapshot identity does not match its record")
        canonical = json.dumps(
            value.snapshot,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if hashlib.sha256(canonical).hexdigest() != value.snapshot_sha256:
            raise NarrativeStoreError("revision snapshot checksum mismatch")
        if len(canonical) != value.snapshot_bytes:
            raise NarrativeStoreError("revision snapshot size metadata mismatch")
        return value

    def _ensure_revision_baseline(self, current: T, collection: str) -> None:
        path = self._revision_path(
            str(current.project_id),  # type: ignore[attr-defined]
            collection,
            str(current.id),  # type: ignore[attr-defined]
            int(current.revision),  # type: ignore[attr-defined]
        )
        with self._lock:
            if path.is_file():
                value = CandidateRevision.model_validate(self._read_json(path))
                self._validate_revision_record(value, current, collection)
                return
            self._assert_history_capacity(
                str(current.project_id),  # type: ignore[attr-defined]
                collection,
                str(current.id),  # type: ignore[attr-defined]
            )
            baseline = self._make_candidate_revision(
                current,
                collection,
                operation="migrated",
                actor="migration",
                actor_source="local",
                message=(
                    "Legacy baseline reconstructed at the current revision; "
                    "earlier revisions were not available."
                ),
                history_origin="legacy_baseline",
                reconstructed=True,
            )
            _atomic_write_json(path, baseline.model_dump(mode="json"))

    def _create_versioned_record(
        self,
        project_id: str,
        collection: str,
        record: T,
        *,
        actor: str,
        actor_source: RevisionActorSource,
    ) -> T:
        record_path = self._record_path(project_id, collection, str(record.id))
        revision_path = self._revision_path(
            project_id,
            collection,
            str(record.id),
            int(record.revision),  # type: ignore[attr-defined]
        )
        snapshot = self._make_candidate_revision(
            record,
            collection,
            operation="create",
            actor=actor,
            actor_source=actor_source,
        )
        with self._lock:
            if record_path.exists():
                raise NarrativeConflict(f"{collection} record already exists: {record.id}")
            if revision_path.exists():
                raise NarrativeConflict("revision snapshot already exists")
            self._assert_history_capacity(project_id, collection, str(record.id))
            _atomic_write_json(revision_path, snapshot.model_dump(mode="json"))
            try:
                _atomic_write_json(record_path, record.model_dump(mode="json"))
            except BaseException:
                with contextlib.suppress(OSError):
                    revision_path.unlink()
                raise
            self._touch_project(project_id)
        return record

    def _persist_versioned_update(
        self,
        current: T,
        updated: T,
        collection: str,
        *,
        operation: RevisionOperation,
        actor: str,
        actor_source: RevisionActorSource,
        message: str = "",
        restored_from_revision: int | None = None,
    ) -> T:
        project_id = str(current.project_id)  # type: ignore[attr-defined]
        record_id = str(current.id)  # type: ignore[attr-defined]
        revision_path = self._revision_path(
            project_id,
            collection,
            record_id,
            int(updated.revision),  # type: ignore[attr-defined]
        )
        snapshot = self._make_candidate_revision(
            updated,
            collection,
            operation=operation,
            actor=actor,
            actor_source=actor_source,
            message=message,
            restored_from_revision=restored_from_revision,
        )
        with self._lock:
            self._ensure_revision_baseline(current, collection)
            if revision_path.exists():
                raise NarrativeConflict("revision snapshot already exists")
            self._assert_history_capacity(project_id, collection, record_id)
            _atomic_write_json(revision_path, snapshot.model_dump(mode="json"))
            try:
                _atomic_write_json(
                    self._record_path(project_id, collection, record_id),
                    updated.model_dump(mode="json"),
                )
            except BaseException:
                with contextlib.suppress(OSError):
                    revision_path.unlink()
                raise
            self._touch_project(project_id)
        return updated

    def _update_versioned_record(
        self,
        current: T,
        collection: str,
        changes: dict[str, Any],
        model: type[T],
        *,
        expected_revision: int | None,
        actor: str,
        actor_source: RevisionActorSource,
    ) -> T:
        if expected_revision is not None and int(current.revision) != int(  # type: ignore[attr-defined]
            expected_revision
        ):
            raise NarrativeConflict(
                f"revision conflict: expected {expected_revision}, current is {current.revision}"  # type: ignore[attr-defined]
            )
        protected = {"id", "project_id", "canon_status", "created_at", "revision"}
        if protected.intersection(changes):
            raise NarrativeStoreError("record identity and canon status are immutable")
        payload = current.model_dump(mode="python")
        payload.update(changes)
        payload["revision"] = int(current.revision) + 1  # type: ignore[attr-defined]
        payload["updated_at"] = utc_now()
        updated = model.model_validate(payload)
        return self._persist_versioned_update(
            current,
            updated,
            collection,
            operation="update",
            actor=actor,
            actor_source=actor_source,
        )

    def _list_candidate_revisions(self, current: T, collection: str) -> list[CandidateRevision]:
        self._ensure_revision_baseline(current, collection)
        rows: list[CandidateRevision] = []
        for path in self._revision_paths(
            str(current.project_id),
            collection,
            str(current.id),  # type: ignore[attr-defined]
        ):
            revision = int(path.stem)
            if revision > int(current.revision):  # type: ignore[attr-defined]
                continue
            value = CandidateRevision.model_validate(self._read_json(path))
            rows.append(self._validate_revision_record(value, current, collection))
        return sorted(rows, key=lambda row: row.revision, reverse=True)

    def _get_candidate_revision(
        self,
        current: T,
        collection: str,
        revision: int,
    ) -> CandidateRevision:
        self._ensure_revision_baseline(current, collection)
        if revision > int(current.revision):  # type: ignore[attr-defined]
            raise NarrativeNotFound(f"revision not found: {revision}")
        path = self._revision_path(
            str(current.project_id),  # type: ignore[attr-defined]
            collection,
            str(current.id),  # type: ignore[attr-defined]
            revision,
        )
        if not path.is_file():
            raise NarrativeNotFound(f"revision not found: {revision}")
        value = CandidateRevision.model_validate(self._read_json(path))
        return self._validate_revision_record(value, current, collection)

    def _restore_candidate_revision(
        self,
        current: T,
        collection: str,
        revision: int,
        model: type[T],
        *,
        expected_revision: int | None,
        actor: str,
        actor_source: RevisionActorSource,
        message: str,
    ) -> T:
        if expected_revision is not None and int(current.revision) != expected_revision:  # type: ignore[attr-defined]
            raise NarrativeConflict(
                f"revision conflict: expected {expected_revision}, current is {current.revision}"  # type: ignore[attr-defined]
            )
        source = self._get_candidate_revision(current, collection, revision)
        payload = copy.deepcopy(source.snapshot)
        payload.update(
            {
                "id": str(current.id),  # type: ignore[attr-defined]
                "project_id": str(current.project_id),  # type: ignore[attr-defined]
                "canon_status": "candidate",
                "created_at": str(current.created_at),  # type: ignore[attr-defined]
                "updated_at": utc_now(),
                "revision": int(current.revision) + 1,  # type: ignore[attr-defined]
            }
        )
        if collection == "chapters":
            payload["branch_id"] = str(current.branch_id)  # type: ignore[attr-defined]
        elif collection == "scenes":
            payload["branch_id"] = str(current.branch_id)  # type: ignore[attr-defined]
            payload["chapter_id"] = str(current.chapter_id)  # type: ignore[attr-defined]
        restored = model.model_validate(payload)
        return self._persist_versioned_update(
            current,
            restored,
            collection,
            operation="restore",
            actor=actor,
            actor_source=actor_source,
            message=message,
            restored_from_revision=revision,
        )

    def _scene_in_chapter(self, project_id: str, chapter_id: str, scene_id: str) -> Scene:
        scene = self.get_scene(project_id, scene_id)
        if scene.chapter_id != _validate_id(chapter_id, "chapter id"):
            raise NarrativeNotFound("scene not found in chapter")
        return scene

    def _create_record(self, project_id: str, collection: str, record: T) -> T:
        path = self._record_path(project_id, collection, str(record.id))
        with self._lock:
            if path.exists():
                raise NarrativeConflict(f"{collection} record already exists: {record.id}")
            _atomic_write_json(path, record.model_dump(mode="json"))
            self._touch_project(project_id)
        return record

    def _update_record(
        self, current: T, collection: str, changes: dict[str, Any], model: type[T]
    ) -> T:
        protected = {"id", "project_id", "canon_status", "created_at", "revision"}
        if protected.intersection(changes):
            raise NarrativeStoreError("record identity and canon status are immutable")
        payload = current.model_dump(mode="python")
        payload.update(changes)
        payload["revision"] = int(current.revision) + 1  # type: ignore[attr-defined]
        payload["updated_at"] = utc_now()
        updated = model.model_validate(payload)
        with self._lock:
            _atomic_write_json(
                self._record_path(str(current.project_id), collection, str(current.id)),
                updated.model_dump(mode="json"),
            )
            self._touch_project(str(current.project_id))
        return updated

    def _get_record(self, project_id: str, collection: str, record_id: str, model: type[T]) -> T:
        self._require_project(project_id)
        path = self._record_path(project_id, collection, record_id)
        if not path.is_file():
            raise NarrativeNotFound(f"{collection} record not found: {record_id}")
        record = model.model_validate(self._read_json(path))
        if collection in _VERSIONED_COLLECTIONS:
            self._ensure_revision_baseline(record, collection)
        return record

    def _list_records(self, project_id: str, collection: str, model: type[T]) -> list[T]:
        self._require_project(project_id)
        rows = [
            model.model_validate(self._read_json(path))
            for path in self._record_paths(project_id, collection)
        ]
        if collection in _VERSIONED_COLLECTIONS:
            for record in rows:
                self._ensure_revision_baseline(record, collection)
        return rows

    def _touch_project(self, project_id: str) -> None:
        path = self._project_dir(project_id) / "project.json"
        project = self._load_project(path, persist_migration=False)
        updated = project.model_copy(update={"updated_at": utc_now()})
        _atomic_write_json(path, updated.model_dump(mode="json"))

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise NarrativeStoreError(f"invalid record: {path.name}")
        return value


__all__ = [
    "NarrativeConflict",
    "NarrativeNotFound",
    "NarrativeStore",
    "NarrativeStoreError",
]
