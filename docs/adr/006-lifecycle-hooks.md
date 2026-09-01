# ADR-006 · Lifecycle hook system

Status: Accepted | Date: 2026-04

## Context

Two parallel needs converged on the same shape:

1. **Community extensibility.** Third-party developers want to plug
   into the runtime without forking — block specific tool calls,
   scrub outputs, log to their own metrics pipeline, inject
   per-user context at session start. The `hooks` subsystem
   (`PreToolUse` / `PostToolUse` / `UserPromptSubmit` / `Stop` /
   `SessionStart` / `Notification`) is now the de-facto contract
   users expect from any modern agent runtime.

2. **Internal lifecycle observability.** The constitution gate, the
   journal tagger, the budget circuit-breaker — all want the same
   "fires before X, fires after X" spots. Without a uniform
   lifecycle they grow as ad-hoc try/finally blocks scattered
   through `executor.py` and `thread_compat_router.py`.

A second hook stack already existed: `runtime.core.nerves.hooks`
(`HookManager` + `HookContext` + `HookError`), wired through
`ToolExecutor.hooks`. It serves the runtime's *internal* needs but
its API is intentionally tied to our internal types (it speaks
`Step` / `ExecutionResult` / `ArmId`) — community handlers
shouldn't have to import internal models to write a "block
`rm -rf /`" hook.

## Decision

Build a **second, simpler** hook stack at `runtime.safety.hooks`
that mirrors the established event names and shape. Both stacks
coexist — the legacy one stays for internal use, the new one is
the public-facing API.

### Shape

```python
from runtime.safety.hooks import (
    register_hook, PreToolUseEvent, HookDecision,
)

@register_hook(PreToolUseEvent)
def block_rm_rf(event):
    if event.sucker_id == "exec_shell":
        cmd = event.args.get("cmd", "")
        if "rm -rf /" in cmd:
            return HookDecision.cancel("refuse rm -rf /")
    return HookDecision.pass_through()
```

Six event types · six dispatch helpers · one `HookDecision`
dataclass with factory methods (`pass_through` / `cancel` /
`modify_args` / `modify_output` / `modify_prompt`).

### Dispatch semantics

* Handlers run in **registration order**.
* First `cancelled=True` decision **short-circuits** the chain —
  later handlers don't see the event.
* Modifications **accumulate**: each handler's `modified_args` /
  `modified_output` / `modified_prompt` overrides the previous
  for the field it touches; untouched fields keep the prior
  value.
* Handler exceptions are **caught and logged** as warnings; the
  hook is treated as `pass_through`. Rationale: a buggy
  third-party hook must not take down the runtime.

### Integration points

| Event              | Fires at                                            |
|--------------------|-----------------------------------------------------|
| `PreToolUseEvent`  | `ToolExecutor.execute_step` before handler call     |
| `PostToolUseEvent` | `ToolExecutor.execute_step` after handler returns   |
| `UserPromptSubmit` | `thread_compat_router.run_stream` after goal extract|
| `Stop`             | `run_stream` worker `finally` block                 |
| `SessionStart`     | `_bind_session_in_thread` after `bind_thread_session`|
| `Notification`     | (reserved · no built-in dispatchers yet)            |

`PreToolUse` cancel produces a failed `Step` with
`stderr_tags=["hook_cancel: <reason>"]`, with budget committed at
zero so the reservation doesn't leak.

`PreToolUse` modify_args replaces the dict before the handler
sees it (the handler runs against the new args).

`PostToolUse` modify_output rewrites `ExecutionResult.output` and
appends `"post_hook_rewrote"` to `stderr_tags` for audit.

`UserPromptSubmit` cancel surfaces as an SSE error frame to the
client with the handler's reason; the planner never runs.

## Alternatives considered

* **One hook stack, not two.** Replace the legacy `HookManager`
  with the new public stack. Rejected for now — the legacy stack
  has internal callers we haven't audited; doing the migration
  earns nothing the parallel stack doesn't already deliver.
  Migration is a separate ADR if/when we want it.

* **JSON-defined hooks (shell-out variant).** A common
  alternative is to configure hooks via a JSON file shelling out
  to commands. We chose the Python-decorator path because
  Echo's audience is library users writing Python · not CLI
  power-users who want shell commands. A JSON-driven adapter on
  top is a future possibility.

* **Synchronous-only.** We don't expose async handlers today.
  The dispatch path runs inside the executor thread — adding
  async would require a runner restructure. Kept simple; can
  retrofit if a real async use-case appears.

## Consequences

**Positive**

* Community handlers are 5 lines · no internal imports needed.
* Constitution gate, channel adapters, future audit-tooling all
  have the same plug-in story as third-party hooks.
* Aligns terminology with industry expectations · reduces
  conceptual translation cost for users.

**Negative**

* Two hook stacks to keep in mind. Documented above; ADR-numbered
  so the next maintainer doesn't try to "consolidate" without
  reading the rationale first.

* Global registry singleton. Tests must `clear()` between cases —
  we expose `get_global_registry()` precisely so isolation is
  easy. A per-app registry is on the long-term roadmap if a real
  multi-tenant deployment needs it.

**Neutral**

* `Notification` event ships with no built-in dispatchers — we
  define the type so community handlers have a place to register
  generic "something happened" events; the runtime will start
  using it organically (budget warns / rate limits) as those
  paths are added.

## References

* Hooks documentation standard: established event-name conventions in modern agent runtimes
* Tests: `tests/test_safety_hooks.py` (14 cases · registry +
  dispatch chain + executor integration)
* Implementation: `runtime/safety/hooks/{events,registry,runner}.py`
