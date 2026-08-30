# Extending Echo · community cookbook

This guide walks through the three extension points a third-party
developer needs to customize agent behavior without forking the
runtime:

1. **Hooks** — intercept tool calls / prompts / session events.
2. **Slash commands** — drop markdown files that become `/foo`
   prompt templates.
3. **Constitution** — tune the safety gate's aggressiveness, plug
   in a custom judge, or internalize a bespoke policy.

Each section has a minimal runnable recipe. All APIs here are
stable — they live in `runtime.safety.*` and
`runtime.execution.slash_commands` and have explicit test coverage.

---

## 1. Hooks

Hooks fire at fixed lifecycle points and can cancel, modify, or
pass through. Full contract in
[ADR-006](adr/006-lifecycle-hooks.md).

### Register a hook

```python
# ~/.echo/hooks/my_hooks.py
from runtime.safety.hooks import (
    HookDecision, PreToolUseEvent, register_hook,
)

@register_hook(PreToolUseEvent)
def block_dangerous_shell(event):
    if event.sucker_id == "exec_shell":
        cmd = event.args.get("cmd", "")
        if "rm -rf /" in cmd or "dd if=" in cmd:
            return HookDecision.cancel(f"refused: {cmd[:40]}")
    return HookDecision.pass_through()
```

That's it. At runtime bootstrap, the hooks module is imported
once and the decorator installs the handler on the global
registry. From that moment, every `exec_shell` call passes
through your check before the handler runs.

### Recipes

#### Scrub secrets in tool outputs

```python
import re
from runtime.safety.hooks import (
    HookDecision, PostToolUseEvent, register_hook,
)

_SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*\S+")

@register_hook(PostToolUseEvent)
def scrub_secrets(event):
    if not isinstance(event.output, str):
        return HookDecision.pass_through()
    if _SECRET_RE.search(event.output):
        cleaned = _SECRET_RE.sub(r"\1=[REDACTED]", event.output)
        return HookDecision.modify_output(cleaned)
    return HookDecision.pass_through()
```

#### Async handlers

`async def` hooks work transparently — the dispatcher detects a
returned coroutine and drives it to completion. Useful when your
handler needs to call an external API, hit a DB, or read a file:

```python
from runtime.safety.hooks import (
    HookDecision, PreToolUseEvent, register_hook,
)

@register_hook(PreToolUseEvent)
async def policy_check(event):
    if event.sucker_id == "exec_shell":
        allowed = await policy_service.is_allowed(event.args.get("cmd"))
        if not allowed:
            return HookDecision.cancel("policy denied")
    return HookDecision.pass_through()
```

Semantics match sync handlers:
- registration order preserved
- first cancel short-circuits
- exceptions caught and treated as pass_through

Cost: each async handler spins a fresh event loop (or offloads
to a worker thread when called from inside an existing loop).
Cheaper to keep handlers sync if your work is CPU-bound; use
async when you're genuinely awaiting I/O.

#### Inject per-user context at session start

```python
from runtime.safety.hooks import SessionStartEvent, register_hook

@register_hook(SessionStartEvent)
def log_session(event):
    user = getattr(event.session, "actor", "?")
    print(f"[audit] session {event.thread_id} started for {user}")
    # return None · pass-through, no modification
```

#### Monitor budget and provider health

The runtime fires `NotificationEvent`s at lifecycle signals.
Kinds currently dispatched:

| Kind              | Fires when                                              | Details keys                                                                           |
|-------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------|
| `budget_warn`     | Task budget crosses 80% or 95% utilization (once each)  | `level_pct`, `task_id`, `arm_id`, `utilization`, `usd_spent`, `tokens_spent`, limits  |
| `budget_squirt`   | Circuit-breaker trips (insufficient budget for a call)  | `task_id`, `arm_id`, `sucker_id`, `reason`, spent totals                               |
| `provider_down`   | LLM provider call raises (non-rate-limit error)         | `provider`, `model`, `error_type`, `error_message`                                     |
| `rate_limit`      | Provider returns 429 / RateLimitError                   | `provider`, `model`, `error_type`, `error_message`                                     |
| `immune_reject`   | TrustEngine rejects a tool call (untrusted source)      | `task_id`, `arm_id`, `sucker_id`, `trusted_source`, `reason`                           |
| `plan_mode_exit`  | Agent calls `exit_plan_mode` and transitions tier       | `from`, `to`, `thread_id`, `plan_preview`                                              |

```python
from runtime.safety.hooks import NotificationEvent, register_hook

@register_hook(NotificationEvent)
def on_notification(event):
    if event.kind == "budget_warn" and event.details.get("level_pct") == 95:
        page_oncall(f"budget 95% on task {event.details['task_id']}")
    elif event.kind == "provider_down":
        metrics.increment(f"provider.down.{event.details['provider']}")
    # return None · notification hooks typically pass through
```

#### Block certain prompts

```python
from runtime.safety.hooks import (
    HookDecision, UserPromptSubmitEvent, register_hook,
)

BLOCKLIST = {"sudo rm -rf", "drop database"}

@register_hook(UserPromptSubmitEvent)
def reject_obvious_footguns(event):
    text = event.prompt_text.lower()
    for phrase in BLOCKLIST:
        if phrase in text:
            return HookDecision.cancel(
                f"prompt contains blocklist phrase: {phrase}",
            )
    return HookDecision.pass_through()
```

### Chain semantics

- Handlers run in registration order.
- First `cancelled=True` wins · later handlers don't see the event.
- `modified_args` / `modified_output` / `modified_prompt` accumulate
  (later handler's modification replaces earlier for the same
  field, untouched fields carry through).
- Handler exceptions are caught, logged as warnings, and treated
  as `pass_through`. A buggy hook cannot crash the runtime.

---

## 2. Slash commands

Slash commands are markdown files that become prompt templates.
Familiar to anyone who has used a slash-driven agent runtime.

### Directory layout

```
~/.echo/commands/                  # global (all projects)
└── review-pr.md

<project>/.echo/commands/          # project-local override
└── review-pr.md                      # overrides the global one
```

Project beats global on name collisions.

### File format

````markdown
---
description: Review a PR for security and performance
argument-hint: <pr-number> [--strict]
allowed-tools: fetch_url, read_file
model: claude-opus-4
---
Review PR #$1 for security and performance.
Flags: $ARGUMENTS

Focus on:
- SQL injection / XSS / path traversal
- N+1 queries
- Error handling gaps
````

Frontmatter is optional. Body is whatever prompt you want.

### Template tokens

| Token        | Expands to                                              |
|--------------|---------------------------------------------------------|
| `$ARGUMENTS` | Full raw argument string (everything after the command) |
| `$1`, `$2`… | Positional tokens (shlex-split · quoted args respected) |
| `$<name>`    | Named kwarg lookup when the API caller passes a dict    |

Unresolved placeholders stay literal · no template error crashes
on missing args.

### Loading programmatically

```python
from runtime.execution.slash_commands import (
    load_slash_commands, expand,
)

cmds = load_slash_commands(project_dir=".")
review = next(c for c in cmds if c.name == "review-pr")
prompt = expand(review, "123 --strict")
# → "Review PR #123 for security and performance.\nFlags: 123 --strict\n..."
```

### HTTP surface

`GET /api/slash-commands` returns the merged catalog for the
frontend command palette. Body is **not** returned — only the
metadata the UI needs to render a picker (`name`, `description`,
`argument_hint`, `allowed_tools`, `model`, `source`).

---

## 3. Constitution

The constitution layer is the safety gate. It runs on every
outbound message (channel send, notification, tool output
leaving the owner's surface). Full clause list in
[`docs/constitution.md`](constitution.md).

### Profiles

Three built-in profiles · pick one globally at bootstrap:

```python
from runtime.safety.validation import set_profile
set_profile("strict")   # default · external agents
# set_profile("normal") # local dev · LLM judge audit-only
# set_profile("lax")    # air-gapped · only hard floor (secrets) enforced
```

Profile table:

| Profile | PII rewrite | LLM judge block | Secret block |
|---------|-------------|-----------------|--------------|
| strict  | yes         | authoritative   | always       |
| normal  | yes         | audit-only      | always       |
| lax     | no (logged) | audit-only      | always       |

Secrets always block. That's a hard floor — once a credential is
on the wire, the attacker owns it. Profile can't un-ring that bell.

### Custom LLM judge

The judge catches semantic violations the regex can't reach
(ransomware requests, phishing drafts, coerced role-play to
exfil PII). The recommended wiring goes through the
runtime's `ModelRouter` abstraction — any Anthropic / OpenAI /
Mock subclass works, and you get a 60 s TTL cache
for free (chatty agents re-send near-duplicates during tool
loops · the cache keeps your judge cost bounded):

```python
from runtime.safety.validation import (
    set_judge, build_judge_from_router,
)
from runtime.sensing.model_router.anthropic_router import AnthropicModelRouter

router = AnthropicModelRouter(api_key=...)
set_judge(build_judge_from_router(
    router,
    model="claude-haiku-4-5-20251001",  # low latency · judge doesn't need deep reasoning
    cache_ttl_s=60,                       # same (msg, dest) within 60 s = cache hit
))
```

For full control (custom provider / batching / multiplexing)
drop down to the raw LLM-function builder:

```python
from runtime.safety.validation import (
    set_judge, build_judge_from_llm_fn,
)

def my_llm(prompt: str) -> str:
    return openai_client.complete(prompt).text

set_judge(build_judge_from_llm_fn(my_llm))
```

Either way, the judge's reply format is documented in
`JUDGE_PROMPT` — return lines starting with `BLOCK:`,
`ESCALATE:`, or `ALLOW:`. Unknown replies default to allow
(we never fail-closed on parser errors — the regex gate is
the hard floor). Provider errors / timeouts also degrade to
allow · judge latency or outage can't DoS outbound traffic.

### Clause-level overrides

Above the profile knob, you can tune individual clauses per agent
or per outbound call. Two actions are available:

| Action     | Effect                                                           |
|------------|------------------------------------------------------------------|
| `journal`  | Downgrade this clause's hits to audit-only allow (no rewrite)    |
| `block`    | Upgrade to an unconditional block (even if profile would scrub)  |

**Agent-level** · set in the agent's declaration::

```python
# runtime / agent loader code
agent.capabilities["constitution_overrides"] = {
    "PRIV-2": "journal",   # finance agent routinely writes emails
}
```

**Per-call** · explicit kwarg on `check_outbound`::

```python
from runtime.safety.validation import check_outbound

v = check_outbound(
    message=draft,
    destination="channels:slack:internal",
    session=current_session(),
    overrides={"DGNT-4": "journal"},  # disclaimer requirement off
)
```

Per-call overrides merge on top of agent-level ones (caller wins
on collision). **Secrets (`PRIV-4`) never honor `journal`** — any
attempt is logged and the block still fires. Unknown action
values are silently dropped so a typo can't weaken the gate.

### Custom outbound check

Channel adapters use `safe_send()` automatically (see
`runtime/adapters/channels/base.py`). For new outbound paths
you write yourself, call the gate directly:

```python
from runtime.safety.validation import check_outbound

def send_email(to, subject, body, session):
    verdict = check_outbound(
        message=body,
        destination=f"email:{to}",
        session=session,
    )
    if verdict.action == "block":
        raise ValueError(f"blocked: {verdict.reason}")
    smtp.send_message(to, subject, verdict.sanitized_text)
```

A static audit (see `runtime/adapters/channels/manager.py`)
checks every registered channel adapter's source for a call to
`safe_send` / `check_outbound` — if you forget, you get a
logged warning at registration time.

### Opt out of the internalized summary

The ~250-token `CONSTITUTION_SUMMARY` is injected into every
agent's system prompt by default. To disable for a research
build:

```jsonc
// agents/<id>/profile.jsonc
{
  "systemPrompt": {
    "includeConstitution": false
  }
}
```

**Not recommended for production.** The runtime gate is the
backstop; the internalized summary is the first line of
defense (models that *know* the principles produce fewer
violations that reach the gate).

---

## Where to go next

- **Capability flags** ([ADR-005](adr/005-agent-capabilities.md)):
  gate new privileged features per-agent.
- **Mode ladder** ([ADR-002](adr/002-mode-gated-scope.md)):
  write scope tiers · plan / chat / team / code.
- **MCP trust** ([ADR-007](adr/007-mcp-trust-store.md)):
  approve MCP servers before their tools become callable.
- **Sub-agent dispatch**: `call_agent` skill · one agent delegating
  to another synchronously. Registered in the `orchestration`
  group · whitelist per-agent via `profile.jsonc::allowedSkills`.
