from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from threading import Lock
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from runtime.adapters.instrumentation import trace_stage
from runtime.safety.invariants import AppendOnlyList

from .triple import Triple

Verdict = Literal["accepted", "superseded_old", "ignored_lower_conf", "disputed"]


class AddResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    new_triple_id: UUID
    superseded_ids: list[UUID] = []
    reason: str = ""


class KnowledgeGraph:
    DEFAULT_MULTI_VALUED: set[str] = frozenset(
        {  # type: ignore[assignment]
            "returned",
            "mentions",
            "contains",
            "tagged_with",
            "cites",
        }
    )

    def __init__(self, multi_valued_predicates: set[str] | None = None) -> None:
        self._triples = AppendOnlyList[Triple](rule_id="CC-5")
        self._by_subject: dict[str, list[UUID]] = defaultdict(list)
        self._by_predicate: dict[str, list[UUID]] = defaultdict(list)
        self._by_object: dict[str, list[UUID]] = defaultdict(list)
        self._index: dict[UUID, Triple] = {}
        self._lock = Lock()
        self._multi_valued: set[str] = (
            set(multi_valued_predicates)
            if multi_valued_predicates is not None
            else set(self.DEFAULT_MULTI_VALUED)
        )

    def add(self, triple: Triple) -> AddResult:
        with trace_stage("kg.add") as span:
            span.set_attribute("echo.kg.subject", triple.subject)
            span.set_attribute("echo.kg.predicate", triple.predicate)

            with self._lock:
                existing_actives = [
                    self._index[tid]
                    for tid in self._by_subject.get(triple.subject, [])
                    if self._index[tid].status == "active"
                    and self._index[tid].predicate == triple.predicate
                ]

                for e in existing_actives:
                    if e.object == triple.object:
                        return AddResult(
                            verdict="ignored_lower_conf",
                            new_triple_id=triple.triple_id,
                            reason=f"duplicate SPO (existing={e.triple_id})",
                        )

                if triple.predicate in self._multi_valued:
                    self._store(triple)
                    return AddResult(
                        verdict="accepted",
                        new_triple_id=triple.triple_id,
                        reason="multi-valued predicate · coexist",
                    )

                superseded: list[UUID] = []
                blocked_by: Triple | None = None
                tied_with: Triple | None = None
                for e in existing_actives:
                    new_wins = (
                        triple.confidence > e.confidence
                        or triple.confidence == e.confidence
                        and triple.ts > e.ts
                    )
                    if not new_wins:
                        # A genuine tie — equal confidence AND equal timestamp,
                        # so neither dominates — on a single-valued predicate
                        # with a DIFFERENT object (exact duplicates were handled
                        # above) is a real contradiction, not "lower confidence".
                        # Surface it as ``disputed`` (the verdict/status that
                        # triple.py + Verdict declare but the resolver never
                        # produced) per protocols/conflict_resolution.md §Direct.
                        if triple.confidence == e.confidence and triple.ts == e.ts:
                            tied_with = e
                        blocked_by = e
                        break
                    superseded.append(e.triple_id)

                if tied_with is not None:
                    # Non-destructive: leave the incumbent active and don't store
                    # the newcomer — we can't auto-pick a winner, so we flag the
                    # contradiction for later resolution instead of silently
                    # mislabelling it ``ignored_lower_conf``.
                    return AddResult(
                        verdict="disputed",
                        new_triple_id=triple.triple_id,
                        reason=(
                            f"unresolvable tie with {tied_with.triple_id}: equal "
                            f"confidence {triple.confidence:.2f} and timestamp, "
                            f"different object ({tied_with.object!r} vs {triple.object!r})"
                        ),
                    )

                if blocked_by is not None:
                    return AddResult(
                        verdict="ignored_lower_conf",
                        new_triple_id=triple.triple_id,
                        reason=(
                            f"existing has higher/equal confidence "
                            f"(existing={blocked_by.triple_id} @ {blocked_by.confidence:.2f} "
                            f"vs new {triple.confidence:.2f})"
                        ),
                    )

                for e in existing_actives:
                    superseded_triple = e.model_copy(
                        update={"status": "superseded", "superseded_by": triple.triple_id}
                    )
                    self._replace(e.triple_id, superseded_triple)

                self._store(triple)
                verdict: Verdict = "superseded_old" if superseded else "accepted"
                return AddResult(
                    verdict=verdict,
                    new_triple_id=triple.triple_id,
                    superseded_ids=superseded,
                )

    def query(
        self,
        subject: str | None = None,
        predicate: str | None = None,
        object: str | None = None,
        *,
        include_archived: bool = False,
    ) -> list[Triple]:
        with self._lock:
            if subject is not None:
                candidate_ids = self._by_subject.get(subject, [])
            elif predicate is not None:
                candidate_ids = self._by_predicate.get(predicate, [])
            elif object is not None:
                candidate_ids = self._by_object.get(object, [])
            else:
                candidate_ids = list(self._index.keys())

            results: list[Triple] = []
            for tid in candidate_ids:
                t = self._index[tid]
                if not include_archived and t.status != "active":
                    continue
                if subject is not None and t.subject != subject:
                    continue
                if predicate is not None and t.predicate != predicate:
                    continue
                if object is not None and t.object != object:
                    continue
                results.append(t)
            return results

    def neighbors(self, entity: str, *, hops: int = 1, max_depth: int = 3) -> list[Triple]:
        if hops < 1:
            return []
        depth = min(hops, max_depth)
        with self._lock:
            seen: set[UUID] = set()
            return self._collect_neighbors(entity, depth=depth, seen=seen)

    def _collect_neighbors(self, entity: str, *, depth: int, seen: set[UUID]) -> list[Triple]:
        if depth <= 0:
            return []
        out_ids = self._by_subject.get(entity, [])
        in_ids = self._by_object.get(entity, [])
        results: list[Triple] = []
        for tid in list(out_ids) + list(in_ids):
            if tid in seen:
                continue
            t = self._index[tid]
            if t.status != "active":
                continue
            results.append(t)
            seen.add(tid)
        if depth <= 1:
            return results
        extra: list[Triple] = []
        for t in list(results):
            for next_entity in (t.subject, t.object):
                if next_entity == entity:
                    continue
                extra.extend(self._collect_neighbors(next_entity, depth=depth - 1, seen=seen))
        return results + extra

    def count(self, *, active_only: bool = True) -> int:
        with self._lock:
            if active_only:
                return sum(1 for t in self._index.values() if t.status == "active")
            return len(self._index)

    def __len__(self) -> int:
        return self.count(active_only=False)

    def __iter__(self) -> Iterator[Triple]:
        with self._lock:
            return iter(list(self._index.values()))

    def _store(self, t: Triple) -> None:
        self._triples.append(t)
        self._index[t.triple_id] = t
        self._add_to_indexes(t.triple_id, t)

    def _replace(self, old_id: UUID, new_triple: Triple) -> None:
        # Update the stored Triple in place — keep its (subject,
        # predicate, object) index entries so superseded triples
        # remain reachable via ``query(..., include_archived=True)``.
        # The ``status`` field on the new triple (``"superseded"`` /
        # ``"archived"``) is what the query filter looks at.
        old = self._index.get(old_id)
        if old is not None:
            self._remove_from_indexes(old_id, old)
        self._index[old_id] = new_triple
        self._add_to_indexes(old_id, new_triple)

    def export_triples(self, *, active_only: bool = True) -> list[dict]:
        with self._lock:
            triples = list(self._index.values())
            if active_only:
                triples = [t for t in triples if t.status == "active"]
            return [
                {
                    "subject": t.subject,
                    "predicate": t.predicate,
                    "object": t.object,
                    "confidence": t.confidence,
                    "status": t.status,
                    "ts": t.ts.isoformat(),
                    "valid_from": t.valid_from.isoformat() if t.valid_from else None,
                    "valid_until": t.valid_until.isoformat() if t.valid_until else None,
                }
                for t in triples
            ]

    def import_triples(
        self,
        data: list[dict],
        *,
        source_label: str = "import",
        default_confidence: float = 0.75,
    ) -> dict:
        from runtime.platform.models import Source

        src = Source(source_id=source_label, source_type="system")
        imported = skipped = errors = 0
        for item in data:
            try:
                s = item.get("subject") or item.get("from") or item.get("s") or ""
                p = item.get("predicate") or item.get("label") or item.get("p") or "related_to"
                o = item.get("object") or item.get("to") or item.get("o") or ""
                if not s or not o:
                    skipped += 1
                    continue
                conf = float(item.get("confidence", default_confidence))
                t = Triple(
                    subject=str(s).strip(),
                    predicate=str(p).strip(),
                    object=str(o).strip(),
                    confidence=min(max(conf, 0.0), 1.0),
                    source=src,
                )
                result = self.add(t)
                if result.verdict in ("accepted", "superseded_old"):
                    imported += 1
                else:
                    skipped += 1
            except (TypeError, ValueError):
                errors += 1
        return {"imported": imported, "skipped": skipped, "errors": errors}

    def _add_to_indexes(self, triple_id: UUID, t: Triple) -> None:
        self._by_subject[t.subject].append(triple_id)
        self._by_predicate[t.predicate].append(triple_id)
        self._by_object[t.object].append(triple_id)

    def _remove_from_indexes(self, triple_id: UUID, t: Triple) -> None:
        self._remove_id(self._by_subject, t.subject, triple_id)
        self._remove_id(self._by_predicate, t.predicate, triple_id)
        self._remove_id(self._by_object, t.object, triple_id)

    @staticmethod
    def _remove_id(index: dict[str, list[UUID]], key: str, triple_id: UUID) -> None:
        bucket = index.get(key)
        if not bucket:
            return
        remaining = [tid for tid in bucket if tid != triple_id]
        if remaining:
            index[key] = remaining
        else:
            index.pop(key, None)

    def import_obsidian_markdown(self, markdown_text: str, *, page_name: str = "") -> dict:
        import re

        links = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", markdown_text)
        if not page_name:
            lines = markdown_text.strip().splitlines()
            for line in lines:
                if line.startswith("# "):
                    page_name = line[2:].strip()
                    break
            if not page_name:
                page_name = "unknown_page"
        data = [
            {"subject": page_name, "predicate": "links_to", "object": link.strip()}
            for link in links
            if link.strip()
        ]
        return self.import_triples(data, source_label=f"obsidian:{page_name}")
