# Echo Main Path Audit

This audit is based on the current repository shape. The goal is not to add
another agent module, but to converge the existing runtime, frontend, memory,
permission, observability, and evolution surfaces into one verifiable product
path.

## Current Judgment

Echo already has most Agent OS primitives. The backend `runtime/` covers
planning, execution, memory, safety, MCP, realtime, workflows, and observability.
The frontend `frontend/` has now converged the primary user path to one
realtime conversation workspace plus team mode. Chat and code routes still
exist as compatibility links, but they no longer represent separate product
entry points.

The remaining convergence gap is behind that frontend:

1. Backend runtime and API surfaces are still broader than the default product
   path.
2. Legacy chat/code links and developer realtime routes must stay compatibility
   aliases, not independent transports.
3. Backend stub fallbacks can make a feature look usable before the real path is
   verified.
4. Memory, evolution, permissions, and observability exist, but are not yet one
   task-end loop.

## Evidence

| Location | Current behavior | Risk |
|---|---|---|
| `frontend/src/router.tsx` | `/workspace` redirects to `realtime/new`; `/workspace/realtime/:threadId` and `/workspace/chats/:threadId` render the same `ChatPage`; `/workspace/code*` redirects into realtime | Frontend is product-converged, but compatibility routes must not grow separate behavior again |
| `frontend/src/router.tsx` | `/realtime` remains a developer index and `/realtime/:threadId` redirects into `/workspace/realtime/:threadId` | Developer and product paths can drift if docs or tests treat `/realtime` as the product shell |
| `runtime/platform/ui/app.py` | The app mounts agents, team, parallel, deep research, MCP, fs, browser, workflow, observability, evolution, realtime, permissions, and stub routers | Broad capability surface makes the health of the main path hard to judge |
| `runtime/sensing/siphon/stub_router.py` | Several auth/account/billing APIs return `_stub: true` compatibility responses | Frontend success can mask missing real backend capability |
| `docs/GOLDEN_PATH.md` | The 10-minute path proves deterministic demo, UI, and journal basics | Useful for developer validation, but not yet the full self-evolving agent product path |

## Target Main Path

```text
Create goal
  -> Start realtime workspace thread
  -> Select workspace scope
  -> Plan
  -> Execute tools under approval policy
  -> Stream item protocol events
  -> Persist journal and artifacts
  -> Score the turn
  -> Generate memory / workflow / skill candidates
  -> Review and promote or reject
  -> Replay the run later
```

## Frontend Convergence

| Surface | Recommended status | Rationale |
|---|---|---|
| `/workspace/realtime/:threadId` | P0 main execution surface | Unifies item protocol, approvals, tool events, file changes, and replay |
| `/workspace/chats/:threadId` | P0 compatibility alias | Must render the same realtime `ChatPage`; do not reintroduce a separate chat transport |
| `/workspace/code*` | P1 compatibility redirect | Coding should be a mode inside the realtime thread/runtime, not a separate page shell |
| `/workspace/team*` | P0/P1 team surface | Team mode sits beside the single-user realtime path, not underneath legacy chat/code |
| `/workspace/observability` | P0 run review surface | Replay, journal, cost, tools, permissions, and failures should land here |
| `/workspace/evolution` | P0 review queue surface | Only evaluated candidates should be promoted |
| `/workspace/mcp` | P1 capability connection surface | Important, but should not displace the main execution path |
| `/workspace/workflows` | P1 crystallization surface | Successful runs can become reusable workflows after eval |
| `/realtime` | P2 developer index | Useful for development, not the product default |

## Backend Convergence

| Backend capability | Recommendation |
|---|---|
| Realtime gateway | Use as the main transport for new task execution |
| Journal | Every main-path run must be replayable by thread/task |
| Permissions router | Present approval gate, MCP trust, and filesystem scope as one permission model |
| Stub router | Surface `_stub` clearly in UI/logs; allow production mode to disable or warn |
| Workflow editor | Accept only run crystallization backed by evidence and eval |
| Evolution ops | Route changes through candidate, eval, approval, promotion, and rollback |

## P0 Acceptance Criteria

1. Opening `/workspace` starts or selects a realtime thread, not legacy chat.
2. `/workspace/chats/*` and `/workspace/code*` remain aliases/redirects into
   realtime and cannot fork their own transport.
3. A file-editing task shows plan, tool call, approval, file diff, test result,
   and final answer in one thread.
4. A completed task creates a turn score and a run detail entry.
5. Any memory, skill, workflow, or prompt candidate appears in an evolution
   review queue before promotion.
6. Stub responses are visible as simulated data and cannot silently pass as real
   production behavior.

## Do Not Prioritize Yet

| Defer | Reason |
|---|---|
| More agent roles | Role count is not the current bottleneck |
| More independent pages | More surfaces will dilute the main path |
| Runtime rewrite | Existing primitives are already broad enough |
| Heavy isolation sandbox first | Productized permissions and main-path verification should come first |
