# Memory · Hemolymph (Context)

> Context Composer · 给 planner 组装上下文（最近 trajectory + learned rules + memories）。

**Source**: `runtime/memory/hemolymph/`

## Exports

- `ContextComposer`
- `ContextEngine`
- `TruncationContextEngine`
- `estimate_tokens`

## Modules

| Module | Summary |
| --- | --- |
| `_image_semantic_vectors.py` | Pure image/vector helpers for the semantic image index. |
| `code_index.py` | Auto-retrieve relevant *source* chunks for planner grounding. |
| `composer.py` | — |
| `embedding_backend.py` | Unified, configurable text embedder for echo's code index. |
| `image_semantic_index.py` | Local image semantic search + face grouping over a persisted index. |
| `repo_context.py` | Auto-retrieve relevant codebase context from the project wiki. |
| `semantic_code_index.py` | Read-only semantic search over the work-mode KB's persisted code index. |
| `semantic_rank.py` | Generic semantic ranking — order candidate texts by relevance to a query. |
| `video_semantic_index.py` | Local video semantic search + face grouping + speech search over a persisted index. |
| `video_watchdog.py` | Auto incremental indexing for the local video library. |

## Who imports this

**15** file(s) reference this package:

- **`runtime/cli_core.py/`** · 1 file(s)
  - `runtime/cli_core.py`
- **`runtime/core/`** · 2 file(s)
  - `runtime/core/cerebrum/_react_prompt_assembly_sections.py`
  - `runtime/core/cerebrum/llm_planner.py`
- **`runtime/execution/`** · 4 file(s)
  - `runtime/execution/suckers/code_intelligence_skills.py`
  - `runtime/execution/suckers/image_album_skills.py`
  - `runtime/execution/suckers/image_semantic_skills.py`
  - `runtime/execution/suckers/video_album_skills.py`
- **`runtime/memory/`** · 1 file(s)
  - `runtime/memory/learning/experience_ledger.py`
- **`runtime/platform/`** · 1 file(s)
  - `runtime/platform/config/builder.py`
- **`runtime/sensing/`** · 5 file(s)
  - `runtime/sensing/gateway/_observability_rollback_panels.py`
  - `runtime/sensing/gateway/local_brain.py`
  - `runtime/sensing/gateway/media_router.py`
  - `runtime/sensing/gateway/retrieve_router.py`
  - `runtime/sensing/gateway/wiki_router.py`
- **`runtime/tour.py/`** · 1 file(s)
  - `runtime/tour.py`

