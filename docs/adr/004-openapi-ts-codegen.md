# ADR-004 · OpenAPI → TypeScript type generation pipeline

Status: Accepted | Date: 2026-05

## Context

The backend's `/openapi.json` has been exposed since day one (FastAPI
gives it for free), but the frontend has been consuming API
responses as untyped `dict[str, Any]` / `unknown`. Runtime shape
bugs (`listAgents` returning `[]` vs `{agents: [...]}`,
`agent.soul` field leaking `HARD SYSTEM RULE` banner into UI,
`event_type` vs `kind` mislabel) kept appearing because:

1. Backend endpoints return `dict[str, Any]` → OpenAPI schema is
   `{additionalProperties: true}` → no TS constraints to catch
   drift at compile time.
2. Frontend writes `(data as {agents: Agent[]}).agents` casts
   without any cross-check against the real backend shape.
3. A backend wire change (adding / renaming / removing a field)
   surfaces as a runtime exception in the frontend, not a failed
   build.

Two fixes needed together:
* **Typed response models on the backend** (pydantic, not
  `dict[str, Any]`) so `/openapi.json` carries real schemas.
* **Codegen on the frontend** that turns those schemas into
  `.ts` types the UI code can import and type-check against.

This ADR covers the codegen half · the typed-response half is
already underway per endpoint (config router, meta router,
uploads router, observability router all emit typed responses).

## Decision

Use [`openapi-typescript`](https://openapi-ts.dev/) as the
codegen tool. It's schema-only (no runtime), writes a single
type-dense `.ts` file with `paths` and `components.schemas`
interfaces.

Flow:

1. Backend changes an endpoint.
2. `make openapi-snapshot` runs the snapshot drift test with
   `ECHO_OPENAPI_WRITE=1`, refreshing
   `docs/openapi-snapshot.json`.
3. `make frontend-types` regenerates
   `frontend/src/core/api/openapi-types.ts` from that snapshot.
4. PR reviewer sees three commits together: endpoint change,
   snapshot update, generated types update.
5. Frontend code that imports from `openapi-types.ts` fails to
   compile if the change broke its contract.

The snapshot file is the intermediate "reviewable contract" · it
lives under git so PR diffs surface schema changes, and the TS
types derive from it (not from a live server) so the build is
deterministic and doesn't require a running backend.

## Alternatives considered

**A. `fastapi-codegen` + direct server call.** Generates Pydantic
TS definitions by starting the server. Rejected because it
couples the codegen step to a running process (slower, racier,
requires all optional deps installed).

**B. Hand-maintained `types.ts` beside each API file.** Current
state. Rejected because it's what caused the drift bugs in the
first place.

**C. tRPC or similar RPC frameworks.** Would reshape the entire
API surface. Huge migration cost for a codebase that already has
stable REST endpoints. Out of scope.

**D. `openapi-generator-cli` (Java).** Feature-complete but a
Java dep in a Python+TS project is a heavy commitment. The JS-
native `openapi-typescript` covers our current needs.

## Consequences

- **`openapi-typescript`** is added as a frontend devDependency
  (v7.x). Install via `pnpm install` or `make frontend-install`.
- **`docs/openapi-snapshot.json`** (~139KB) is the source of
  truth committed to git. Any endpoint change that would shift
  the wire shape requires updating it (the
  `test_openapi_snapshot.py` test fails otherwise).
- **`frontend/src/core/api/openapi-types.ts`** (~6500 lines
  auto-generated, gitignored? — we keep it tracked for PR
  reviewability) is the importable-to-UI type file.
- **`make frontend-types`** is the regen command. CI can add
  `frontend-types` + `git diff --exit-code` to gate against
  stale generated types.
- **Backend endpoints that still return `dict[str, Any]`** emit
  generic `{[k: string]: unknown}` TS types · not immediately
  useful but at least not broken. Gradually typing those with
  pydantic response models (the per-router extraction work has
  been doing this) tightens the TS types without any frontend
  action.
- **The generated file is large** (6500 lines). If it ever
  starts slowing TS compile noticeably, we can split by router
  via `openapi-typescript`'s per-path option, or move it to a
  `node_modules`-style ignored artifact with a build step.

## Usage

Day-to-day:

```bash
# Backend change, see shape drift
pytest tests/test_openapi_snapshot.py
# → fails · regenerate
make openapi-snapshot
# → docs/openapi-snapshot.json updated
make frontend-types
# → frontend/src/core/api/openapi-types.ts regenerated
```

In frontend code:

```ts
import type { components } from "@/core/api/openapi-types";

type CustomModelEntry = components["schemas"]["CustomModelEntry"];
type CustomModelsList = components["schemas"]["CustomModelsList"];

// Use in fetch responses, react-query typings, etc.
```
