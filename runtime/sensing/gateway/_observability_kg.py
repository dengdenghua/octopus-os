"""Knowledge-graph endpoints for the observability router.

Pure structural extraction from ``_observability_router_factory.py`` (no logic
changes). Builder that registers the ``/api/kg`` and ``/api/knowledge/*``
handlers onto the router. The KG sync cache (``kg_sync_lock`` /
``kg_sync_cache`` / ``kg_sync_ttl_seconds``) is created per router instance
here, matching the factory's per-call state.
"""

from __future__ import annotations

import contextlib
import json
import time
from threading import Lock
from typing import Any

from runtime.sensing._fastapi_guard import require_fastapi

from ._observability_auth import _require_global_control
from ._observability_helpers import HTTPException, Query, Request
from ._observability_state import ObservabilityContext


def register_kg_endpoints(router: Any, ctx: ObservabilityContext) -> None:
    """Register the knowledge-graph / knowledge endpoints."""
    require_fastapi(__name__)

    journal = ctx.journal

    kg_sync_lock = Lock()
    kg_sync_cache: dict[str, Any] = {
        "expires_at": 0.0,
        "report": None,
    }
    kg_sync_ttl_seconds = 3.0

    def _kg_db_path() -> Any:
        from runtime.platform.process.paths import app_paths

        return app_paths().data_dir / "knowledge_graph.sqlite"

    def _open_synced_kg(
        *,
        scope: Any = None,
        force_sync: bool = False,
    ) -> tuple[Any, bool, Any]:
        from runtime.memory.knowledge_graph import KnowledgeGraph, SqliteKnowledgeGraph
        from runtime.safety.recovery import KGUpdater

        persistent = True
        kg: KnowledgeGraph
        try:
            kg = SqliteKnowledgeGraph(_kg_db_path())
        except Exception:  # noqa: BLE001 - observability must degrade gracefully.
            persistent = False
            kg = KnowledgeGraph()
        now = time.monotonic()
        with kg_sync_lock:
            cache_fresh = persistent and now < float(kg_sync_cache["expires_at"])
            if cache_fresh and not force_sync:
                report = kg_sync_cache["report"]
            else:
                report = KGUpdater(journal, kg, scope=scope).update()
                if persistent:
                    kg_sync_cache["report"] = report
                    kg_sync_cache["expires_at"] = now + kg_sync_ttl_seconds
        return kg, persistent, report

    def _close_kg(kg: Any) -> None:
        close = getattr(kg, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()

    def _model_dump(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            return dump(mode="json")
        if isinstance(value, dict):
            return value
        return None

    def _source_payload(t: Any) -> dict[str, Any]:
        source = getattr(t, "source", None)
        dump = getattr(source, "model_dump", None)
        if callable(dump):
            return dump(mode="json")
        return {}

    def _triple_payload(t: Any) -> dict[str, Any]:
        source = _source_payload(t)
        return {
            "triple_id": str(getattr(t, "triple_id", "")),
            "subject": t.subject,
            "predicate": t.predicate,
            "object": t.object,
            "confidence": t.confidence,
            "status": getattr(t, "status", "active"),
            "source": source,
            "source_label": source.get("source_id") or source.get("source_type") or "",
            "ts": t.ts.isoformat() if getattr(t, "ts", None) else None,
            "valid_from": (t.valid_from.isoformat() if getattr(t, "valid_from", None) else None),
            "valid_until": (t.valid_until.isoformat() if getattr(t, "valid_until", None) else None),
            "superseded_by": (str(t.superseded_by) if getattr(t, "superseded_by", None) else None),
        }

    def _build_kg_view(
        limit: int = 200,
        *,
        scope: Any = None,
    ) -> tuple[list, list, dict]:
        kg, persistent, update_report = _open_synced_kg(scope=scope)
        try:
            triples = kg.query()[:limit]

            entity_set: dict[str, dict[str, Any]] = {}
            rels: list[dict[str, Any]] = []
            type_counts: dict[str, int] = {}

            for t in triples:
                for val, role in [(t.subject, "subject"), (t.object, "object")]:
                    current = entity_set.get(val)
                    if current is None:
                        current = {
                            "id": val,
                            "name": val[:80],
                            "full_name": val,
                            "entity_type": role,
                            "roles": [],
                            "description": "",
                            "degree": 0,
                            "in_degree": 0,
                            "out_degree": 0,
                            "confidence_sum": 0.0,
                            "confidence_count": 0,
                            "sources": [],
                            "first_seen": None,
                            "last_seen": None,
                        }
                        entity_set[val] = current
                    if role not in current["roles"]:
                        current["roles"].append(role)
                    if current["entity_type"] != role:
                        current["entity_type"] = (
                            "subject" if "subject" in current["roles"] else role
                        )

                source = _source_payload(t)
                source_label = source.get("source_id") or source.get("source_type") or ""
                ts = t.ts.isoformat() if getattr(t, "ts", None) else None
                subject = entity_set[t.subject]
                object_ = entity_set[t.object]
                subject["degree"] += 1
                subject["out_degree"] += 1
                object_["degree"] += 1
                object_["in_degree"] += 1
                for entity in (subject, object_):
                    entity["confidence_sum"] += float(t.confidence)
                    entity["confidence_count"] += 1
                    if source_label and source_label not in entity["sources"]:
                        entity["sources"].append(source_label)
                    if ts and (entity["first_seen"] is None or ts < entity["first_seen"]):
                        entity["first_seen"] = ts
                    if ts and (entity["last_seen"] is None or ts > entity["last_seen"]):
                        entity["last_seen"] = ts

                rels.append(
                    {
                        "id": str(t.triple_id),
                        "triple_id": str(t.triple_id),
                        "source_name": t.subject,
                        "target_name": t.object,
                        "source_label": t.subject[:80],
                        "target_label": t.object[:80],
                        "relationship_type": t.predicate,
                        "confidence": t.confidence,
                        "status": getattr(t, "status", "active"),
                        "source": source,
                        "source_ref": source_label,
                        "ts": ts,
                        "valid_from": (
                            t.valid_from.isoformat() if getattr(t, "valid_from", None) else None
                        ),
                        "valid_until": (
                            t.valid_until.isoformat() if getattr(t, "valid_until", None) else None
                        ),
                    }
                )

            entities: list[dict[str, Any]] = []
            for entity in entity_set.values():
                count = max(1, int(entity.pop("confidence_count", 0) or 0))
                confidence_sum = float(entity.pop("confidence_sum", 0.0) or 0.0)
                entity["confidence_avg"] = round(confidence_sum / count, 4)
                entity["sources"] = entity["sources"][:8]
                entities.append(entity)
                etype = str(entity.get("entity_type") or "other")
                type_counts[etype] = type_counts.get(etype, 0) + 1

            stats = {
                "total_entities": len(entities),
                "total_relationships": len(rels),
                "entity_types": type_counts,
                "persistent": persistent,
                "kg_size": kg.count(),
                "update_report": _model_dump(update_report),
            }
            return entities, rels, stats
        finally:
            _close_kg(kg)

    def _relationship_payload(t: Any) -> dict[str, Any]:
        source = _source_payload(t)
        return {
            "id": str(t.triple_id),
            "triple_id": str(t.triple_id),
            "source": t.subject,
            "target": t.object,
            "label": t.predicate,
            "confidence": t.confidence,
            "status": getattr(t, "status", "active"),
            "source_ref": source.get("source_id", ""),
            "ts": t.ts.isoformat() if getattr(t, "ts", None) else None,
        }

    # ─── /api/kg ────────────────────────────────────────────
    @router.get("/api/kg")
    def api_kg(
        request: Request,
        subject: str | None = None,
        predicate: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _require_global_control(request, ctx, cross_tenant=cross_tenant)
        kg, persistent, update_report = _open_synced_kg(scope=scope)
        try:
            triples = kg.query(subject=subject, predicate=predicate)[:limit]
            return {
                "global_control_plane": True,
                "count": len(triples),
                "kg_size": kg.count(),
                "persistent": persistent,
                "update_report": _model_dump(update_report),
                "triples": [_triple_payload(t) for t in triples],
            }
        finally:
            _close_kg(kg)

    # ─── /api/kg/export + /api/kg/import ───────────────────
    @router.get("/api/kg/export")
    def api_kg_export(
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _require_global_control(request, ctx, cross_tenant=cross_tenant)
        kg, persistent, update_report = _open_synced_kg(scope=scope)
        try:
            triples = kg.export_triples(active_only=True)
            return {
                "global_control_plane": True,
                "count": len(triples),
                "persistent": persistent,
                "update_report": _model_dump(update_report),
                "triples": triples,
            }
        finally:
            _close_kg(kg)

    @router.post("/api/kg/import")
    async def api_kg_import(
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _require_global_control(request, ctx, cross_tenant=cross_tenant)
        try:
            body = await request.json()
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            raise HTTPException(400, f"body: {e}") from e

        kg, persistent, update_report = _open_synced_kg(scope=scope)
        try:
            obsidian_text = body.get("obsidian")
            if isinstance(obsidian_text, str):
                result = kg.import_obsidian_markdown(
                    obsidian_text,
                    page_name=body.get("page_name", ""),
                )
            else:
                triples = body.get("triples", [])
                if not isinstance(triples, list):
                    raise HTTPException(400, "triples must be a list")
                result = kg.import_triples(
                    triples,
                    source_label=body.get("source", "api_import"),
                    default_confidence=float(body.get("confidence", 0.75)),
                )
            return {
                "global_control_plane": True,
                **result,
                "persistent": persistent,
                "update_report": _model_dump(update_report),
            }
        finally:
            _close_kg(kg)

    @router.get("/api/knowledge/graph")
    def api_knowledge_graph(
        request: Request,
        limit: int = Query(default=200, ge=1, le=1000),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _require_global_control(request, ctx, cross_tenant=cross_tenant)
        entities, relationships, stats = _build_kg_view(limit, scope=scope)
        return {
            "global_control_plane": True,
            "entities": entities,
            "relationships": relationships,
            "stats": stats,
            "meta": {
                "schema": "echo.knowledge_graph_view.v2",
                "edge_ids_are_full_entity_ids": True,
            },
        }

    @router.get("/api/knowledge/stats")
    def api_knowledge_stats(
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _require_global_control(request, ctx, cross_tenant=cross_tenant)
        _, _, stats = _build_kg_view(500, scope=scope)
        return {"global_control_plane": True, **stats}

    @router.get("/api/knowledge/neighbors")
    def api_knowledge_neighbors(
        request: Request,
        entity: str = Query(...),
        hops: int = Query(default=1, ge=1, le=3),
        limit: int = Query(default=50, ge=1, le=200),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _require_global_control(request, ctx, cross_tenant=cross_tenant)
        kg, persistent, update_report = _open_synced_kg(scope=scope)
        try:
            triples = kg.neighbors(entity, hops=hops)[:limit]
            nodes: dict[str, dict[str, Any]] = {}
            edges: list[dict[str, Any]] = []
            for t in triples:
                for val in [t.subject, t.object]:
                    if val not in nodes:
                        nodes[val] = {
                            "id": val,
                            "label": val[:60],
                            "full_name": val,
                            "entity_type": "neighbor",
                        }
                edges.append(
                    {
                        "id": str(t.triple_id),
                        "triple_id": str(t.triple_id),
                        "source": t.subject,
                        "target": t.object,
                        "label": t.predicate,
                        "confidence": t.confidence,
                        "status": getattr(t, "status", "active"),
                        "source_ref": _source_payload(t).get("source_id", ""),
                        "ts": t.ts.isoformat() if getattr(t, "ts", None) else None,
                    }
                )
            return {
                "global_control_plane": True,
                "center": entity,
                "nodes": list(nodes.values()),
                "edges": edges,
                "persistent": persistent,
                "update_report": _model_dump(update_report),
            }
        finally:
            _close_kg(kg)

    @router.get("/api/knowledge/search")
    def api_knowledge_search(
        request: Request,
        q: str = Query(default=""),
        limit: int = Query(default=50, ge=1, le=200),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _require_global_control(request, ctx, cross_tenant=cross_tenant)
        query = " ".join(q.split()).casefold()
        if not query:
            return {
                "global_control_plane": True,
                "nodes": [],
                "edges": [],
                "count": 0,
            }
        kg, persistent, update_report = _open_synced_kg(scope=scope)
        try:
            matches = [
                t for t in kg.query() if query in f"{t.subject} {t.predicate} {t.object}".casefold()
            ][:limit]
            nodes: dict[str, dict[str, str]] = {}
            edges: list[dict[str, Any]] = []
            for t in matches:
                for val in (t.subject, t.object):
                    nodes.setdefault(
                        val,
                        {
                            "id": val,
                            "label": val[:60],
                            "full_name": val,
                        },
                    )
                edges.append(_relationship_payload(t))
            return {
                "global_control_plane": True,
                "nodes": list(nodes.values()),
                "edges": edges,
                "count": len(edges),
                "persistent": persistent,
                "update_report": _model_dump(update_report),
            }
        finally:
            _close_kg(kg)

    @router.get("/api/knowledge/path")
    def api_knowledge_path(
        request: Request,
        source: str = Query(...),
        target: str = Query(...),
        max_hops: int = Query(default=3, ge=1, le=6),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _require_global_control(request, ctx, cross_tenant=cross_tenant)
        kg, persistent, update_report = _open_synced_kg(scope=scope)
        try:
            triples = kg.query()
            by_entity: dict[str, list[Any]] = {}
            for t in triples:
                by_entity.setdefault(t.subject, []).append(t)
                by_entity.setdefault(t.object, []).append(t)
            queue: list[tuple[str, list[Any]]] = [(source, [])]
            seen = {source}
            path: list[Any] = []
            while queue:
                current, so_far = queue.pop(0)
                if len(so_far) >= max_hops:
                    continue
                for t in by_entity.get(current, []):
                    other = t.object if t.subject == current else t.subject
                    if other in seen:
                        continue
                    next_path = [*so_far, t]
                    if other == target:
                        path = next_path
                        queue = []
                        break
                    seen.add(other)
                    queue.append((other, next_path))
            nodes: dict[str, dict[str, str]] = {}
            for t in path:
                for val in (t.subject, t.object):
                    nodes.setdefault(
                        val,
                        {
                            "id": val,
                            "label": val[:60],
                            "full_name": val,
                        },
                    )
            return {
                "global_control_plane": True,
                "source": source,
                "target": target,
                "found": bool(path),
                "hops": len(path),
                "nodes": list(nodes.values()),
                "edges": [_relationship_payload(t) for t in path],
                "persistent": persistent,
                "update_report": _model_dump(update_report),
            }
        finally:
            _close_kg(kg)


__all__ = ["register_kg_endpoints"]
