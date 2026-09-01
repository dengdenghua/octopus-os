# Echo Agent Optimization Roadmap

Captured 2026-05-31. Written into the repo so the plan survives session
boundaries — each item is sized to be picked up independently in a new
Claude session without re-loading the whole conversation.

The TL;DR: the realtime/JSON-RPC skeleton is in place, but the three
biggest gaps are (a) the item protocol isn't fully closed end-to-end,
(b) sub-agents are still half-structured, (c) the legacy
`AgentThreadState/Message/liveToolEvent` adapter carries debt that
loses information.

Items are grouped by priority. Each entry should produce one PR.

---

## P0 — Correctness and UX cliff

### P0-1 Unify the realtime protocol consumption surface

Pull every server event from `runtime/protocol/events.py` and check
each one against `frontend/src/core/realtime/reducer.ts`. Produce a
matrix:

| event | backend emits? | reducer handles? | UI renders? | tests cover? |

Things known to be incomplete: file-hunk delta, MCP progress, plan
updated. The deliverable is a markdown matrix in this file (see
`Protocol support matrix` section below) plus a list of the 3-5
biggest gaps to fix in follow-up PRs.

### P0-2 Lower the dual-model adapter risk

The new wire protocol is `Conversation/Item`; the legacy workspace UI
still consumes `AgentThreadState/Message/liveToolEvents`. Two-step:

- **Short term** — beef up tests on `realtime-adapter.ts`. Cover
  reasoning, tool, file-change, sub-agent, approval, interrupted,
  reconnect.
- **Mid term** — migrate the core workspace UI to consume the item
  model directly so the adapter stops being a lossy bridge.

### P0-3 Fix encoding pollution

Hunt `â€¦`, `â€"`, `ðŸ`, garbled CJK across frontend strings, comments,
i18n. Pure cleanup; high ROI on perceived quality.

### P0-4 Tighten the `connected` flag semantics

`use-realtime-thread.ts` currently does
`client.connect(); setConnected(true);` optimistically. Have
`RealtimeClient` surface real `onOpen / onClose / onReconnect /
onError` and drive UI from actual socket state. (Partly done — onClose
already wires through, finish the rest.)

### P0-5 Reconnect/outbox edge-case tests

Test cases:

- Disconnect mid-turn → pending requests fail cleanly
- Outbox does NOT replay a stale `turn/start` after reconnect
- Resume does NOT duplicate tool / message items
- Disconnect during tool output, approval prompt, file-hunk decision

---

## P1 — Agentic coding capability

### P1-6 Reconcile plan-mode docs with actual behaviour

`TurnParams.planning_mode` docstring still reads "tool execution off".
Reality (2026-05-31): tool execution stays on; planning_mode only
nudges the prompt. Update docstring, type hints, UI tooltip, i18n,
and the Plan-first banner copy.

### P1-7 Strengthen coding guards

Today's evidence/final/todo/verification guards are a foundation.
Add:

- After a code edit, a verification action MUST follow
- High-risk file changes MUST surface a diff for review
- A failing test MUST block "completed" status

Tests live in `react_guards` and a realtime end-to-end harness.

### P1-8 Unify the tool execution gateway

Sub-agent runner has a path that talks directly to registry handlers,
bypassing the main Beak executor's approval/sandbox/trace pipeline.
Funnel everything through one gateway so the safety + observability
surface is consistent.

### P1-9 Upgrade file-edit UX

We have file-diff → `FileChangeItem` plus per-hunk accept/reject.
Add: streaming hunks, file-tree highlight for changed files, per-file
accept/reject, revert preview, conflict hints.

### P1-10 Promote diagnostics into the item model

Post-write diagnostics currently live in backend traces only. Mint a
`DiagnosticItem` (or `verification`) so the frontend can render lint /
test / typecheck results inline.

---

## P1 — Sub-agent / multi-agent

### P1-11 Drop the sub-agent magic markers

`__subagent_spawned__` / `__subagent_finished__` masquerade as tool /
MCP items. Add a first-class `SubagentItem` (or `item/subagent/*` event
family) covering spawned, thought, tool, artifact, finished, failed.

### P1-12 Better concurrency budgets

Per-turn fan-out is conservatively capped today. Move to
budget-based: separate caps for token, wall-time, tool-calls, risk
level, workspace-write permission.

### P1-13 Sub-agent trace explainability

The user should see: which agent, why it was dispatched, what the
input was, what it produced, whether it influenced the final answer.
`LiveToolTimeline` keeps working but a native "Agent Run Tree" beats
it.

### P1-14 Productise the blackboard

`bb_read / write / keys` exists but isn't a UI first-class citizen.
Surface blackboard contents as observable artifacts: who wrote, who
read, which keys the final synthesis used.

### P1-15 Configurable team topology

Swarm/topology has built-in capabilities but the UI selector and the
runtime execution are loosely coupled. Make topology + roles + fan-out
+ evaluator strategy explicit; allow per-turn override.

---

## P1 — Realtime rendering / front-end-back-end coordination

### P1-16 Keep RAF batching, add tests

`client.ts` already batches `started/completed/delta` together to keep
ordering stable. Tests to add:

- completed arrives before delta
- delta arrives before delta from another item
- multiple items interleaved
- reasoning across multiple `contentIndex`

### P1-17 Stop relying on legacy ReAct text splitting

`splitReactTrace` in `realtime-adapter.ts` parses `Thought / Action /
Observation / Final Answer` out of plain text. Replace with
backend-side structured reasoning/action/final, so the frontend never
guesses.

### P1-18 Fold approvals into the item stream

Pending approvals currently sit in a side hook then map back into
`liveToolEvent`. Approvals should be items (or item statuses) so
resume / history / audit are consistent.

### P1-19 Spec out turn-resume semantics

`thread/resume` returns durable turns, but the precedence rules for
active / interrupted / completed states need a written spec, plus
reducer tests.

### P1-20 Item-view status coverage

Every item view should cover pending, inProgress, completed, failed,
interrupted, declined — with priority on tool, fileChange,
mcpToolCall, todo-list.

---

## P2 — Agentic office / automation

### P2-21 Wire automation runs to the thread/turn/item model

Cron / schedule_task currently feel like generic tools. Surface each
automation run as a thread + turn + items so it can be tracked,
paused, resumed, inspected.

### P2-22 Office artifacts as first-class citizens

Promote docx/xlsx/ppt/pdf above plain file paths. Add `ArtifactItem`:
type, preview, version, source tool, verification screenshot, export
path.

### P2-23 Render-and-verify loop for documents

Office agent quality lives or dies on "did it actually look right".
After every document/sheet/deck generation, auto-render a preview;
attach screenshot / page count / key-content checks to the trace.

### P2-24 Task-level memory and reuse

Codify common office workflows as skills (cf. Codex skill system)
instead of regenerating prompts every turn. Show in the UI which
skills were used, which versions, with what output quality.

---

## P2 — Engineering quality

### P2-25 Golden end-to-end traces

Sample suite: plain Q&A, tool call, file edit, hunk reject, approval,
sub-agent, reconnect-recovery, background shell. Backend records
event log, frontend replays through the reducer, snapshot-compare the
final UI state.

### P2-26 Runtime trace viewer

Today's information is scattered across event log, tool output,
adapter, live timeline. Build a dev-only viewer that shows JSON-RPC
envelope flow, item lifecycle, turn status, latency, drop/coalesce
metrics.

### P2-27 Documentation sync

`docs/architecture.md`, protocol docstrings, frontend adapter
comments, and CHANGELOG must move together. Especially flag: SSE is
retired; the only realtime channel is `/api/realtime` JSON-RPC WS.

### P2-28 Codex gap roadmap

Phase 1 — protocol complete + reducer/UI complete.
Phase 2 — native sub-agent items + agent run tree.
Phase 3 — artifact / automation as first-class control plane.
Phase 4 — unified tool execution gateway, approval, trace, verify
loop.

---

## Working agreement

- One PR per item where reasonable.
- Update this file's status checkboxes as PRs land.
- New session picks up here:
  - `docs/optimization-roadmap.md`
  - look for `[ ]` items, smallest one first if you have <30 min
  - largest "P0" item first if you have a full session

## Status

Each item starts unchecked. Mark `[x]` and add the merge SHA + date
when a PR lands.

- [x] P0-1 Protocol support matrix (2026-05-31)
- [x] P0-2 Adapter test coverage (2026-06-01 — partial: interrupted/failed/inProgress + approval-pending; subagent/reconnect still pending)
- [x] P0-3 Encoding pollution scan (2026-05-31)
- [x] P0-4 connected flag semantics (2026-06-01)
- [x] P0-5 Reconnect/outbox tests (2026-06-01 — outbox invariant + behaviour fix)
- [x] P1-6 Plan-mode doc reconciliation (2026-05-31)
- [ ] P1-7 Strengthen coding guards
- [ ] P1-8 Unify tool execution gateway
- [ ] P1-9 File-edit UX upgrades
- [ ] P1-10 Diagnostics as item
- [ ] P1-11 Native sub-agent items
- [ ] P1-12 Sub-agent budgets
- [ ] P1-13 Sub-agent trace tree
- [ ] P1-14 Blackboard UI
- [ ] P1-15 Configurable topology UI
- [ ] P1-16 RAF batching tests
- [ ] P1-17 Drop legacy ReAct text splitting
- [ ] P1-18 Approvals-as-items
- [ ] P1-19 Turn-resume spec
- [ ] P1-20 Item view state coverage
- [ ] P2-21 Automation runs as threads
- [ ] P2-22 Artifact items
- [ ] P2-23 Render-and-verify loop
- [ ] P2-24 Task-level skill reuse
- [ ] P2-25 Golden traces
- [ ] P2-26 Runtime trace viewer
- [ ] P2-27 Doc sync
- [ ] P2-28 Codex gap phase plan

---

## Protocol support matrix (P0-1)

Captured 2026-05-31 by walking
`runtime/protocol/events.py:ServerMethod` against
`frontend/src/core/realtime/reducer.ts` and the corresponding render
sites. "✅" means the back end emits and the front end consumes the
event without info loss; "⚠" means present-but-incomplete; "—" means
not yet wired through.

### Notifications

| event | backend emits | reducer | UI renders | tests cover |
|---|---|---|---|---|
| `thread/started` | ✅ | ✅ no-op | n/a (lifecycle ack) | ⚠ partial |
| `thread/status/changed` | ✅ | ✅ no-op | — | — |
| `thread/tokenUsage/updated` | ✅ | ✅ no-op | — | — |
| `turn/started` | ✅ | ✅ | ✅ | ✅ |
| `turn/completed` | ✅ | ✅ (status, completedAt, mergeCompletedTurn) | ✅ | ✅ |
| `turn/interrupted` | ✅ (synthesised) | ✅ | ✅ | ✅ |
| `turn/diff/updated` | ✅ | ⚠ no-op (returns unchanged state, just signals) | — | — |
| `turn/plan/updated` | ✅ | — **NOT HANDLED** | — | — |
| `item/started` | ✅ | ✅ (upsertItem 'started') | ✅ depending on item type | ⚠ |
| `item/completed` | ✅ | ✅ (upsertItem 'completed') | ✅ | ⚠ |
| `item/agentMessage/delta` | ✅ | ✅ | ✅ | ✅ |
| `item/reasoning/textDelta` | ✅ | ✅ (status-guarded merge) | ✅ | ⚠ |
| `item/plan/delta` | ✅ | ✅ | ⚠ partial (rendered as text) | — |
| `item/commandExecution/outputDelta` | ✅ | ✅ | ✅ | ⚠ |
| `item/fileChange/outputDelta` | ✅ | — **NOT HANDLED** | — | — |
| `item/fileChange/hunkDelta` | ✅ | — **NOT HANDLED** | — | — |
| `item/fileChange/hunkDecision` | ✅ | ✅ (applyHunkDecision) | ⚠ partial | — |
| `item/mcpToolCall/progress` | ✅ | — **NOT HANDLED** | — | — |
| `error` | ✅ | ✅ (synthesises ErrorItem) | ✅ | ⚠ |
| `model/rerouted` | ✅ | — **NOT HANDLED** | — | — |

### Server-initiated requests

| method | backend sends | client handler | UI shows approval | tests cover |
|---|---|---|---|---|
| `item/commandExecution/requestApproval` | ✅ | ✅ via `onIncomingRequest` | ✅ approval card | ⚠ |
| `item/fileChange/requestApproval` | ✅ | ✅ | ⚠ partial | — |
| `item/permissions/requestApproval` | ✅ | ⚠ generic handler | — | — |
| `item/tool/requestUserInput` | ✅ | ⚠ generic handler | — | — |
| `mcpServer/elicitation/request` | ✅ | ⚠ generic handler | — | — |
| `item/planMode/exitRequest` | ✅ | ⚠ generic handler | ⚠ via approval card | — |

### Top gaps to address (sorted by user-visible impact)

1. **`turn/plan/updated` and `item/plan/delta` are reserved-not-emitted**
   — both events exist on `ServerMethod` but no backend code path
   pushes them yet. Plan UI today derives its state from
   `todo_write` tool-use events (`input.items`) and from parsing
   `plan.md` files written by the agent. That works for the common
   path but doesn't cover edits the model makes via free-form
   markdown. Two options:
   (a) drop the unused events to stop misleading future maintainers, or
   (b) wire the backend to emit `item/plan/delta` whenever
   `todo_write` is invoked AND when `plan.md` is rewritten, so the
   reducer's existing `mergeDelta(... "plan" ...)` handler actually
   does work.
   Option (b) is the right long-term call (matches P1-10 diagnostics
   shape), but the corresponding `PlanItem` UI also has gaps (see
   matrix above), so this is a 2-PR job. Until then the docstring on
   `ServerMethod` should say "reserved — emission not implemented".

2. **`item/fileChange/hunkDelta` + `outputDelta` reserved-not-emitted**
   only sees the final FileChangeItem. To deliver the streaming
   hunk experience promised by P1-9, the reducer needs delta paths
   that grow `hunks[]` in place.

3. **`item/mcpToolCall/progress`** — MCP long-running tools (browser
   automation, large fetches) emit progress fractions; the UI shows
   a static spinner because the reducer ignores progress events.

4. **`model/rerouted`** — when smart routing kicks the turn over to a
   different upstream model mid-stream, the user sees no signal.
   Surface a small inline notice ("rerouted to claude-opus-4.7
   because…").

5. **`turn/diff/updated`** — currently a no-op pass-through. Either
   remove it (dead code) or wire it through to `Conversation.diff`
   so the UI can render aggregated diff sidebar info.

6. **Approval requests beyond the two main types** —
   `requestPermissionsApproval`, `requestUserInput`,
   `mcpServer/elicitation` all funnel through the generic
   `onIncomingRequest` handler with no UI for each shape. Each needs
   its own approval card.

These six gaps are the P0-1 deliverables; pick them up as separate
PRs (recommended order: 1 → 2 → 3 → 6 → 4 → 5).
