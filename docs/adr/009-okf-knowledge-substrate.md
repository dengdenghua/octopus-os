# ADR-009 · OKF as the knowledge substrate

Status: Proposed | Date: 2026-06-24

## Context

The planner needs *codebase* grounding, and the context composer
doesn't provide it — it feeds system / skills / memory but nothing
about the repo, so the agent re-greps every task. `repo_context`
([runtime/memory/hemolymph/repo_context.py](../../runtime/memory/hemolymph/repo_context.py))
closes that gap: it builds a BM25 index over the generated wiki and
retrieves the pages most relevant to a goal, rendered into the prompt
via `render_codebase_context()` (shared by the planner's
`_render_codebase_section` and the react chat loop). It is deliberately
**no-LLM, no-embedding**, and self-documents the known ceiling: true
synonym bridging with no shared token (planner→cerebrum) needs
embeddings, "out of scope here".

But codebase grounding is only one of **at least four knowledge stores**,
each a different shape, none cross-referencing the others:

| Store | Shape today | Source |
|-------|-------------|--------|
| Code wiki — `docs/auto/` (40 pages) | markdown body + central `index.json` tree, file-path IDs, **no per-page frontmatter, no page↔page links** | `scripts/gen_wiki.py` (AST) |
| Skills — `SKILL.md` (255: all_skills 159 + public 88 + local 8) | **markdown + YAML frontmatter** (`name`, `description`) | hand-authored |
| Knowledge graph | sqlite/kuzu typed edges | journal → `KGUpdater`, fed to planner via `_render_kg_section` |
| Document KB | separate service, `/v1/search` | Storage repo (cross-repo, File Agent) |

Each is queried a different way; an edge in one is invisible to the
others. Adding a new consumer means re-solving context assembly from
scratch — the exact fragmentation Google's **Open Knowledge Format**
names.

Four external reference points triangulate on the *same* stack, and we
already own scattered pieces of every layer:

- **OKF** (Google, [blog][okf]) — the **format**: markdown + YAML
  frontmatter, file paths as concept IDs, markdown links as graph edges,
  `index.md` for progressive disclosure, `timestamp`/`log.md` for
  provenance. Only `type` is required; no embeddings ("metadata and
  documentation, not vector representations"); "format, not platform".
- **gbrain** (Garry Tan, [repo][gbrain]) — the **engine**: markdown
  brain-repo as system-of-record + hybrid retrieval (vector + BM25 +
  reciprocal-rank fusion + source-tier boost) + **self-wiring graph that
  extracts typed edges on every page write with zero LLM calls** (reports
  +31.4 P@5 over its graph-disabled variant) + a 24/7 "dream cycle" that
  dedups and consolidates.
- **Karpathy** — the **context policy**: the context window is RAM, the
  store is disk, the retriever is the pager. `repo_context` already pages
  wiki pages per task; the KG section is a second pager.
- **Obsidian / Zettelkasten** — the **human UX**: a navigable graph of
  links and backlinks over local-first markdown.

Key observation: **echo already speaks most of OKF by accident.**
`SKILL.md` is already markdown + frontmatter. `docs/auto` is already
markdown with file-path identifiers. The Claude-Code memory convention
this repo is developed under (markdown + frontmatter + `[[links]]` +
`MEMORY.md` index) is a live reference of the full pattern. We are not
being asked to invent a format — we are being asked to *converge* on one
we are 80% of the way to.

Two forces make convergence worth the churn now:

1. **No-LLM ethos vs. retrieval quality.** OKF resolves the tension we
   kept hitting: embeddings are an *engine* concern, not a *format* one.
   The format stays pure and portable; the engine can add a vector lane
   later without touching a single file.
2. **The five-repo family has no interchange format.** agent ↔ Storage ↔
   mobile ↔ os each hold knowledge; OKF is explicitly designed to be the
   lingua franca between such systems, version-controlled alongside code.

(The full-stack audit on this branch independently confirmed the
governing principle: generated-and-gated artifacts stay fresh
[`docs/auto` has `tests/test_auto_docs_fresh.py`]; hand-maintained ones
drift (the openapi snapshot; the 1993-line `CODE_WIKI.md` drifted so far it
was retired in favour of `docs/auto` + `docs/architecture*`). OKF + a freshness gate is
the durable side of that line.)

## Decision

Adopt **OKF as the common knowledge substrate** for the Echo family,
with an explicitly layered architecture so each layer evolves
independently:

```
FORMAT   OKF bundle      markdown + frontmatter + path-IDs + link-edges     (no embeddings; only `type` required)
ENGINE   repo_context    BM25 today → hybrid (BM25 + optional vector + RRF + source-tier); zero-LLM edge extraction
POLICY   planner paging  retrieve per task into the prompt (_render_codebase_section + _render_kg_section)
UX       static viewer   OKF HTML visualizer / Obsidian-style graph (optional)
```

The seam that makes this safe: **format carries no embeddings; the engine
owns embeddings.** Today's BM25-only `repo_context` is a *valid OKF
consumer*, not a stopgap — adding a vector lane is an engine change, not a
format migration.

Conformance deltas (what "adopt OKF" concretely means here):

- **Per-page frontmatter + `index.md`, not a central `index.json`.**
  `gen_wiki` emits each page with `type`/`title`/`description`/`tags`/
  `timestamp` frontmatter and per-directory `index.md`. Distributed
  frontmatter is git-mergeable (no central conflict magnet) and gives the
  retriever high-signal fields to index instead of raw body. Keep
  `index.json` as a derived artifact if the frontend needs it.
- **Markdown links as edges.** Pages cross-link via relative markdown
  links (markdown format like "customers → tables/customers.md"). The tree gains a
  graph. The Claude-Code memory's `[[name]]` style is the same idea; emit
  both if terseness for the author matters, but the portable form is the
  relative link.
- **Per-page `timestamp` provenance.** The cheap version of the
  source-tier / freshness signal — lets drift detection move from
  "regenerate the whole bundle" to "diff the stale page", and gives the
  engine a recency boost.
- **Zero-LLM edge extraction on write (gbrain pattern).** Unify the three
  edge sources that don't talk today — `SKILL.md` frontmatter, journal-KG
  edges, and wiki cross-links — into one retrievable graph, extracted
  without an LLM call. This is the step gbrain's +31.4 P@5 motivates and
  the one that needs no embeddings.
- **OKF export for the non-markdown stores.** The knowledge graph and the
  Storage doc-KB expose an OKF view, making the family's knowledge
  exchangeable across repos via a vendor-neutral format.

Phased, with the cheapest validation first:

- **Phase 0 — DONE (2026-06-24): validated by pitting the two existing
  rankers against each other; no format change.** The repo already has two
  relevance rankers over two corpora — the composer's word-overlap + CJK-bigram
  over skills, and `repo_context`'s BM25 over the wiki. Rather than duplicate
  skill surfacing (the original plan here), Phase 0 measured them head-to-head
  on skill selection (15 bilingual goal→skill labels). They tied on top-1 but
  **split by language**: BM25 won on English/identifier goals (length-norm +
  idf), composer won on Chinese — `repo_context` kept CJK runs *whole* and so
  catastrophically missed e.g. `resume-craft` @85, `cn-finance-data` @23.
  Folding **CJK bigrams** into `repo_context`'s tokenizer fixed both (→ rank 1)
  while keeping the English wins, lifting the unified BM25 *above both*
  (MRR 0.762→**0.824** vs composer 0.793; top-3 0.80→**0.93**). **Conclusion:
  one engine does beat two siloed rankers — but the win is in *merging* their
  signals, not picking one.** The CJK-bigram change shipped to the live
  retriever (improves Chinese wiki grounding independent of the rest of OKF);
  pinned by `tests/test_repo_context.py`.
- **Phase 2 (engine) — DONE first (2026-06-24): zero-LLM edge graph +
  graph-augmented retrieval.** Reordered ahead of Phase 1 because it carries
  the value gbrain measured and needs no page-format change (so no frontend /
  31-page-regeneration risk). `gen_wiki` now derives page→page dependency edges
  from the AST import graph (`_page_import_edges`, ~50 edges e.g.
  cerebrum→tool-engine/validation/journal) and emits them into `index.json`
  under an additive `edges` key (the freshness gate already exempts
  `index.json` from byte-compare). `repo_context` loads them into an
  undirected adjacency and applies a bounded neighbour re-rank
  (`_GRAPH_BOOST`): a page connected to a strong hit outranks an
  equally-lexical but unconnected one. Conservative (re-ranks only matched
  pages, never promotes zero-overlap), self-gated (no `edges` → no-op),
  `ECHO_CODEBASE_GRAPH=0` disables; pinned by `tests/test_repo_context.py`.
  (Superseded 2026-06-24: the additive `_GRAPH_BOOST` became a proper
  reciprocal-rank fusion lane — see Phase 3.)
- **Phase 1 — format — DONE (2026-06-24).** `gen_wiki` now prepends
  deterministic OKF frontmatter to every page (`type`/`title`/`description`/
  `tags`/`tier`, all derived from path + body — no wall-clock, so the freshness
  gate's byte-compare still holds). `wiki_router` strips it when serving (the UI
  gets clean markdown; the parsed metadata is returned alongside as `meta` for
  an optional type/tier header) and `repo_context` strips it from the indexed /
  injected body while weighting `description` + `tags` and applying the `tier`
  multiplier. Verified end-to-end on a live server — the docs endpoint returns
  frontmatter-free content + structured `meta`. Deferred: a per-page
  `timestamp` (needs a deterministic source-mtime, not wall-clock) and
  switching the Claude-memory-style `[[name]]` links to OKF relative-path
  links.
- **Phase 3 (hybrid engine) — DONE (2026-06-24): RRF fusion + semantic lane.**
  `repo_context` now fuses up to three ranked lanes by reciprocal-rank fusion
  (`_rrf`): **lexical** (BM25 × tier, always), **semantic** (reranks the lexical
  top-pool via the existing `ECHO_EMBED_*` embedder — Ollama / fastembed /
  sentence-transformers — bridging the planner→cerebrum synonym gap this module
  documented), and **graph** (the import-edge neighbours, now a fusion lane
  rather than an additive boost). RRF of a single lane is that lane's order, so
  the default no-embedder / no-edge path stays byte-for-byte plain BM25 — the
  semantic lane is dormant and free until an embedder is configured (a *local*
  model satisfies it; no external API required). This closes the biggest gbrain
  gap — hybrid retrieval — with in-repo infrastructure; pinned by
  `tests/test_repo_context.py`. Embeddings are cached per corpus (keyed by
  index.json mtime, `_page_vectors`) so a hot loop embeds only the per-call
  query — gbrain's precompute pattern.
- **Synthesis layer — DONE (2026-06-24): `POST /api/wiki/ask`.** gbrain's "give
  the answer, not raw pages" — retrieve the wiki context and compose a grounded,
  cited answer via echo's own model router (`stack.planner.router`),
  instructed to answer ONLY from the retrieved wiki and name gaps rather than
  invent. Gated and safe: no model or no relevant context → `grounded=False`
  with no LLM call; a model error degrades the same way. Pinned by
  `tests/test_wiki_qa.py`.
- **Dream cycle — already exists, not a gap.** echo's `RegenerationScheduler`
  (`runtime/safety/recovery/scheduler.py`, ~600s loop) already extracts learned
  rules from the journal and runs `MemoryConsolidator` / SkillForge / KG-updater
  — the autonomous-consolidation role gbrain's dream cycle plays. We do not
  duplicate it.
- **Reranker — DONE (2026-06-24): gated cross-encoder rerank stage.**
  `repo_context` adds a final `_maybe_rerank` stage that reorders the fused
  top-pool through echo's own `research.rerank` — its **Cohere Rerank v3**
  cross-encoder backend when `COHERE_API_KEY` is set. Gated on the key (rerank's
  zero-dep BM25 backend would just echo the lexical lane), wrapped so it never
  breaks retrieval, and pinned by `tests/test_repo_context.py`. (Fixed en route:
  `runtime.research` re-exports `rerank`, so the import had to come from the
  submodule or it silently no-op'd.)
- **Graph-visualisation UI — DONE (2026-06-24).** `GET /api/wiki/graph` serves
  the page nodes + dependency edges from index.json; a dependency-free
  `WikiGraphPanel` (circular SVG, hover-to-highlight, design-token theming)
  renders it in the workspace Knowledge → Wiki tab. Verified: endpoint returns
  41 nodes / 50 edges (`tests/test_wiki_qa.py`), component type-checks, and the
  graph renders cleanly (cerebrum + gateway carry the highest fan-out).
- **OKF export — in-repo half DONE (2026-06-24).** `GET /api/wiki/okf-bundle`
  streams the wiki as a portable `tar.gz` OKF bundle (markdown + frontmatter +
  index.json + edges) — the family lingua franca: any OKF-aware consumer
  (Storage, mobile, os) fetches and ingests it with no proprietary SDK. Pinned
  by `tests/test_wiki_qa.py`.
- **OKF consume — Storage half DONE (2026-06-25, `echo-storage`).** The loop
  is now closed end-to-end in the sibling repo: `POST /v1/okf/ingest` safely
  unpacks an OKF bundle (hardened, `extractall`-free: rejects symlink/traversal/
  absolute members, caps entry-count + uncompressed size), registers it as a
  folder source, and indexes it — so this repo's wiki becomes searchable through
  Storage's `/v1/search` + `/v1/answer` hybrid retrieval. Verified by an
  ingest → auto-index → search end-to-end test there. (Lives in
  `echo-storage` on branch `feat/okf-ingest`; tracked separately from this
  repo's PR.) Storage *emitting* its own doc-KB as OKF remains optional future
  surface area.

With Phases 0–3, the reranker, the graph UI, and the OKF export landed in this
repo and the Storage consume side landed in `echo-storage`, the catch-up to
gbrain's knowledge layer is complete end-to-end across the family. What remains
is optional (a heavier external-model reranker; Storage emitting OKF) — nothing
load-bearing.

## Alternatives considered

- **RDF / JSON-LD / semantic web.** Formally richer, but needs tooling to
  read/write and isn't human-renderable in a plain editor. OKF explicitly
  rejects this trade for readability; so do we — our authors and the agent
  both edit markdown.
- **Vector DB as system-of-record** (gbrain's Postgres+pgvector default).
  Couples truth to an engine and a deployment. We keep markdown files as
  the system of record and the DB/index as *derived* — losing the index is
  a rebuild, not a data-loss event. (gbrain itself keeps a markdown brain
  repo as its record for the same reason.)
- **Keep the bespoke `index.json`.** Works locally, but it is not an
  interchange format (no cross-repo / cross-vendor portability), and a
  single central file is a merge-conflict magnet under concurrent edits.
- **Do nothing.** Each store stays siloed and each new consumer re-solves
  context assembly. Tolerable at today's scale; the cost compounds as the
  family grows and more agents need the same grounding.

## Consequences

**Positive**

- One substrate, four consumers (planner, react loop, frontend viewer,
  sibling repos) — and any OKF-aware external tool — read the same bundle.
- Format stays no-LLM / no-embedding and git-native; the engine can grow
  to embeddings without a format migration.
- Per-page frontmatter + `timestamp` turns drift detection page-granular
  and gives the retriever better signal than raw-body BM25.
- The three disconnected edge sources become one retrievable graph; the
  gbrain result says that is where the retrieval-quality win is.
- Cross-repo knowledge exchange gets a vendor-neutral contract instead of
  bespoke per-boundary glue.

**Negative**

- `gen_wiki` and `repo_context` both change; the CI freshness gate and any
  `index.json` consumers (frontend wiki surface) must follow.
- Two link syntaxes in play during migration (`[[name]]` vs relative
  links) unless we pick one; risk of half-migrated bundles.
- An OKF export for the KG / Storage KB is real new surface area, not just
  a reshape of existing files.

**Neutral**

- BM25-only retrieval is unchanged through Phase 1 — it is already a
  conformant OKF consumer, so nothing regresses while the format lands.
- Embeddings remain explicitly out of the format; whether we ever add the
  vector lane (Phase 3) stays a separate, reversible engine decision.

## References

- [How the Open Knowledge Format can improve data sharing][okf] — Google Cloud
- [garrytan/gbrain][gbrain] — knowledge layer for AI agents (hybrid retrieval, zero-LLM self-wiring graph, dream cycle)
- Karpathy — "context engineering" / LLM-OS (context window as RAM, external knowledge as disk)
- [runtime/memory/hemolymph/repo_context.py](../../runtime/memory/hemolymph/repo_context.py) — current BM25 wiki retriever (the engine to evolve)
- [scripts/gen_wiki.py](../../scripts/gen_wiki.py) — AST → `docs/auto` generator (the producer to make OKF-emitting)
- [docs/auto/README.md](../auto/README.md) — current generated wiki + freshness gate

[okf]: https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing
[gbrain]: https://github.com/garrytan/gbrain

