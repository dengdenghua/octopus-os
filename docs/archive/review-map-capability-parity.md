# Reviewer map · capability parity campaign

This is a **reviewer's onboarding doc** for the work that landed
across sprints 2-4 of the capability-parity effort, plus the
follow-on judge wiring / notification dispatches / frontend UI.
It exists so an external reviewer can answer two questions in
30 minutes:

1. **What's the surface area?** (what changed, where, why)
2. **Where are the load-bearing assumptions a reviewer should attack?**

If you only have time for one section, read **§5 Risk hotspots**.

---

## 1. Scope summary

| Sprint | Subsystem                       | New tests | Key files                                                       |
|--------|---------------------------------|-----------|-----------------------------------------------------------------|
| 1A     | Three-tier memory               | 8         | `runtime/execution/agents/loader.py` (`_memory_tier_paths`)     |
| 1B     | Plan mode + `exit_plan_mode`    | 9         | `runtime/platform/scope.py`, `runtime/execution/suckers/plan_mode.py`, `runtime/execution/beak/executor.py` |
| 2A     | Lifecycle hooks                 | 14        | `runtime/safety/hooks/{events,registry,runner}.py`              |
| 2B     | Slash commands SDK              | 18        | `runtime/execution/slash_commands/{__init__,loader}.py`         |
| 3A     | `call_agent` skill              | 10        | `runtime/execution/suckers/sub_agent.py`                        |
| 3B     | MCP trust store                 | 12        | `runtime/adapters/mcp_client/trust.py`, bridge `require_trust` gate |
| 4      | Constitution internalization    | 18        | `runtime/safety/constitution/{soul,judge,profiles,llm_judge}.py` |
| ext    | Notifications (organic)         | 6         | `runtime/platform/models/governance.py`, `runtime/execution/beak/executor.py`, `runtime/sensing/eyes/anthropic_router.py` |
| ext    | Frontend UI                     | tsc clean | `frontend/src/components/workspace/{chat-input-box,settings/*}.tsx` |

**Total new tests**: 95 backend + frontend tsc.
**Total suite**: 2534 backend pass + 13 skipped.
**ADRs**: 006 (hooks) · 007 (MCP trust) · 008 (constitution profiles).

---

## 2. Read order for fastest comprehension

If the reviewer's goal is "understand what shipped", read in
this order:

1. `docs/extending.md` — the user-facing API contract for hooks /
   slash commands / constitution, with runnable recipes. **30 sec
   skim tells you what we promised.**
2. `docs/adr/006-lifecycle-hooks.md` + `007-mcp-trust-store.md` +
   `008-constitution-profiles.md` — why each design over the
   alternatives. **5 min total.**
3. `runtime/safety/hooks/runner.py` — 80 lines · the dispatch
   primitive every other sprint plugs into.
4. `runtime/execution/beak/executor.py` lines around 145-260 ·
   the integration site that wires hooks + plan-mode +
   sandbox enforcement together.
5. `tests/test_safety_hooks.py` + `tests/test_constitution_profiles.py`
   — the contract pinned in executable form.

---

## 3. Architectural invariants a reviewer can assume

These hold across the new code · violations are bugs to flag:

- **Fail-open for hooks.** Any handler exception is caught
  (logged warning) and treated as `pass_through`. Reviewer test:
  grep for `dispatch_*` calls — every site should be wrapped in
  `try/except`. Specific files: `executor.py`, `thread_compat_router.py`,
  `anthropic_router.py`. The sole exception is the **gate**
  itself, which always returns a `Verdict` and never raises.

- **Hard floor: secrets.** `enforces_secrets_block()` in
  `profiles.py` returns `True` unconditionally. No profile, no
  hook, no judge can let `sk-ant-…` patterns through.

- **Default-safe MCP.** The bridge defaults to `require_trust=False`
  (so unit tests with `MockMCPClient` keep working), but every
  production caller in `mcp_router.py` passes `require_trust=True,
  server_name=name`. The trust store is opt-out at the test layer,
  opt-in at production.

- **Notification ≠ audit.** `dispatch_notification` is best-effort
  — it must never fail commits or change call results. Journal
  remains the audit source of truth.

- **Stop hook fires once per worker turn.** Wrapped in the SSE
  worker's `finally` block · runs even on exception. `_traj`
  is read defensively (`locals().get("trajectory")`) because on
  the error path it may not have been bound yet.

---

## 4. New HTTP surface

These are new endpoints since the last snapshot. Reviewer should
verify:
- they appear in `docs/openapi-snapshot.json` (regenerated)
- the frontend TS types in `openapi-types.ts` were regenerated
- no auth bypass (they piggyback on existing router auth · same
  pattern as adjacent endpoints)

| Method | Path                                         | Purpose                                |
|--------|----------------------------------------------|----------------------------------------|
| GET    | `/api/slash-commands`                        | Merged catalog for typeahead           |
| GET    | `/api/mcp/trust`                             | List MCP trust entries                 |
| POST   | `/api/mcp/trust`                             | Approve a server (with tool digest)    |
| DELETE | `/api/mcp/trust/{server_name}`               | Revoke approval                        |
| GET    | `/api/safety/constitution-profile`           | Read current profile                   |
| PUT    | `/api/safety/constitution-profile`           | Set profile (strict/normal/lax)        |

---

## 5. Risk hotspots — attack these first

Where a reviewer's attention pays the highest dividend:

### 5.1 Hook dispatch race

`get_global_registry()` returns a **process-wide singleton**.
Multiple test cases registering on the same registry could
leak state. Mitigation: `tests/test_safety_hooks.py` autouse
fixture calls `clear()`. Risk: production code that registers
hooks at import time could double-register if the module is
imported under multiple paths. **Reviewer task**: grep for
`@register_hook` outside of `tests/` and confirm idempotence.

### 5.2 MCP trust digest staleness

`tool_digest = blake2b(sorted(set(tool_names)))`. If a server
returns the same tool *names* but with mutated *schemas* (new
required parameter, expanded permissions on existing tool), the
digest matches and we silently re-approve. Documented limitation
in `trust.py` — full schema fingerprinting is a future add.
**Reviewer task**: think about whether name-only is acceptable
for our threat model · the alternative is hashing the full
JSON-Schema which we deferred.

### 5.3 LLM-judge cache key

`hashlib.sha256(destination + "\x00" + message)`. Reviewer
should think about:
- Cache poisoning if message contains `\x00` deliberately
  (collision with separator). Empirically rare in tool outputs
  but worth flagging.
- Fingerprinting: caching at all leaks judge cost across
  semantically identical messages. Acceptable for our
  single-tenant model · would not be in multi-tenant.

### 5.4 Constitution profile global state

`_PROFILE` is a module-level variable. **No per-tenant** /
per-session override. ADR-008 documents this as deliberate ·
multi-tenant deployments need to thread a `profile=` kwarg
into `check_outbound`. Reviewer task: confirm we have no
prod multi-tenant path that quietly relies on per-tenant
isolation here.

### 5.5 Slash-command frontend cache

`_cache: SlashCommand[] | null` lives at module scope in
`slash-command-picker.tsx`. Once loaded, it survives until the
SPA reloads. If the user adds a `.md` to `.echo/commands/`
mid-session, the picker won't show it without a refresh. Doc'd
as a design choice (slash commands change rarely) but worth
flagging if the reviewer thinks otherwise.

### 5.6 Provider-down notification leakage

`anthropic_router.py` dispatches a `provider_down` notification
with `error_message: str(exc)[:200]`. Reviewer task: confirm
that no anthropic SDK exception serializes API keys or other
secrets into its `__str__`. We slice to 200 chars · still risky
if the provider's error format changes upstream.

---

## 6. What's NOT in scope

Things a reviewer might assume we touched but didn't:

- **MCP server schema validation.** We trust the names · we don't
  validate the JSON Schema of each tool. (See 5.2.)
- **Hook registration sandboxing.** Community hooks run in our
  process, with our privileges. There is no plugin sandbox.
  Documented in extending.md.
- **Notification durability.** `dispatch_notification` is in-process
  fire-and-forget. There's no journal entry for notifications
  themselves (the underlying events — budget commits, immune
  rejects — *do* go to the journal · the notification is just a
  derived signal for hook subscribers).
- **Frontend tests.** We added `tsc --noEmit` clean to CI but
  did NOT add Playwright / RTL coverage for the new UI. The new
  components are mechanical (typeahead, button, picker) · we
  judged the cost/benefit unfavorable.
- **Per-agent constitution overrides.** Spec says `profile.jsonc::
  constitution.overrides` should let a finance agent disable
  `DGNT-4`. Not implemented. Scoped to a future sprint.

---

## 7. How to run the review locally

```bash
# Backend full suite
cd <repo> && python -m pytest tests/ -q --ignore=tests/test_cli_optimize.py

# Targeted: just the new tests from this campaign
python -m pytest tests/test_safety_hooks.py \
                tests/test_slash_commands.py \
                tests/test_sub_agent_skill.py \
                tests/test_mcp_trust.py \
                tests/test_constitution_internalization.py \
                tests/test_constitution_profiles.py \
                tests/test_constitution_llm_judge.py \
                tests/test_notification_events.py \
                tests/test_plan_mode.py \
                tests/test_memory_tiers.py -q

# Frontend type check
cd frontend && npx tsc --noEmit

# OpenAPI snapshot drift check (fails if backend changed schemas without
# regenerating · CI gate, ADR-004)
python -m pytest tests/test_openapi_snapshot.py -q
```

Expected: 95 new backend tests pass · 2534 total · tsc clean ·
snapshot test green.
