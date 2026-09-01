# Operator Readiness Guide

This guide is the stable handoff page for running Echo as a coding and
governed agent workspace. It ties the day-to-day operator loop to the runtime
surfaces that must stay documented: code mode, permissions, replay gates, and
plugins.

## Code Mode

Code mode should follow a small, repeatable loop:

1. Inspect the repository state before editing.
2. Edit the smallest set of files needed for the request.
3. Verify with targeted tests, linters, or runtime probes.
4. Report the exact checks that passed or failed.

When the model does not supply verification evidence, the runtime executes a
bounded batch of up to three distinct safe checks (for example lint plus a
targeted test). The batch stops on the first failure, emits every check as a
verification item, and records its candidate count, attempted commands, pass
count, and stop reason for later audit.

The main runtime path is:

- `runtime/core/cerebrum/react_loop.py` plans and resumes coding turns.
- `runtime/execution/tool_engine/executor.py` executes tools.
- `runtime/sensing/gateway/realtime_turn_outcome.py` records outcomes.
- `runtime/safety/evolution/auto_verifier_metrics.py` tracks verifier quality.

Repository inspection classifies staged, unstaged, untracked, conflicted,
deleted, and renamed files. Any existing change requires preservation, and an
unresolved conflict marks the workspace unsafe for automatic editing until it
is resolved.

Mixed browser-and-code requests use a multi-lane completion contract. When a
goal explicitly asks the agent to reproduce or inspect through the browser and
then patch and verify repository code, completion requires successful browser
evidence, a workspace code write, and a verification command. Evidence from
one lane cannot substitute for an unexecuted lane.

Operator signal:

- The operator panel should show recent task runs, process timelines, replay
  gate state, auto-verifier drift, and scorecard gaps.
- Repeated verifier failures should become repair-route backlog items instead
  of being hidden in logs.
- Recent verifier batches should show whether the command budget was exhausted,
  capped, or stopped by a failure.

Automatic third-party CLI discovery has been removed. See the
[Local CLI migration boundary](local-cli-partners.md) before handling an old
project that still names one of those backends. For current operation, use the
built-in Kane/Codex engine or install the OpenCode Zen API adapter explicitly.

## Permissions

Permissions are the contract between autonomy and local safety. A high-quality
run must make approval, sandbox, and override state visible.

Core expectations:

- Tool calls pass through trust, path, approval, and sandbox checks.
- Risky actions require approval or an explicit operator override.
- Overrides need a reason and should be written to governance audit records.
- Policy review proposals should be replay-backed before becoming rules.
- Delegated security context is monotonic: model-supplied child context cannot
  replace parent sandbox, workspace, network, approval, routing, denied-path,
  session, or prompt-injection taint state. Stripped keys are recorded on the
  tool execution span without entering the child request.

Relevant implementation paths:

- `runtime/safety/hooks/tool_edge_hooks.py`
- `runtime/safety/audit/trust_gateway.py`
- `runtime/safety/evolution/policy_review_rules.py`
- `runtime/sensing/gateway/agent_trace_router.py`

## Multi-Agent Execution

Parallel batches validate dependency cycles and overlapping write ownership
before dispatch. Each batch also records bounded task, dispatch-queue, and
cancellation-grace timeouts in its recovery snapshot. A task that ignores
cancellation is terminally cancelled after the grace period; a blocked task is
marked `timed_out`, its dependants are cancelled, and any late result is
ignored instead of overwriting the terminal receipt.

Operators can use the parallel batch recovery snapshot and process timeline to
see submitted, started, cancellation-requested, and completed timestamps. The
default limits are 900 seconds for execution, 60 seconds for dispatch queueing,
and 5 seconds for cancellation grace; callers may lower them for bounded jobs.

## Replay Gates

Replay gates keep promotion from becoming guesswork. A memory, policy, or
evolution change should cite evidence that can be inspected later.

Promotion readiness should answer:

- Which replay case supports the change?
- Did the replay gate pass?
- Was an override used?
- Which audit entry records the decision?

Operator surfaces:

- `/api/agent-trace/replay-gate`
- `/api/agent-trace/review-queue/promotions/apply`
- `/api/agent-trace/review-queue/promotions/audit/summary`
- `/api/agent-trace/review-queue/promotions/audit/export`
- `/api/agent-trace/review-queue/promotions/audit/rotation`
- `/api/evolution/agent-scorecard`

Governance audit rotation is configured with a confirmed `POST` to the
rotation endpoint. Schedules use UTC cron expressions and a bounded retention
count. The persistent serve scheduler checks the policy every minute without
executing shell commands. A due run verifies the HMAC audit chain before
atomically writing the export; integrity failure produces no export. Successful
configuration and rotation operations append receipts to the governance chain,
and old export bundles are pruned only after the new verified bundle is durable.

## Plugins

Plugins extend the runtime, so plugin maturity depends on smoke checks,
permission review, and hook governance.

Minimum plugin readiness:

- A plugin exposes at least one useful capability.
- Local smoke checks pass or produce a clear review-required reason.
- Permission review happens before high-risk plugin actions.
- Lifecycle hook behavior is auditable like ordinary tool hooks.
- Publisher provenance is verified against an operator-owned trust store;
  invalid, tampered, untrusted, and revoked signatures fail the smoke gate.

Operator surfaces:

- `/api/plugins`
- `/api/plugins/smoke-summary`
- `/api/plugins/publisher-trust`
- `/api/plugins/publisher-trust/rotate`
- `/api/plugins/publisher-trust/revoke`
- `/api/plugin-hub/plugins`
- The operator panel `Plugin health` and `Publisher trust` cards

Publisher trust operations require explicit confirmation. Key rotation installs
the replacement Ed25519 public key and retires its predecessor in one atomic
write. Emergency revocation takes effect immediately. Both operations record
the actor, key fingerprint, reason, and result in the governance audit chain;
private signing keys are never accepted by these APIs.

### Transactional install and lifecycle rollback

Local plugins are installed through `POST /api/plugins/lifecycle/install` with
an explicit `confirm_install`. The runtime rejects symbolic links, validates
the manifest and content provenance in an isolated staging directory, runs the
smoke gate, and requires the migration gate for upgrades. Only then does it
atomically replace the managed plugin directory. If replacement fails after the
old version is moved, automatic restore puts the previous version back.

Every committed install or upgrade returns a transaction ID. Operators can
inspect `GET /api/plugins/lifecycle/history` and perform a confirmed lifecycle
rollback with `POST /api/plugins/lifecycle/rollback`. Install, upgrade, and
rollback outcomes are appended as `plugin_lifecycle_install` or
`plugin_lifecycle_rollback` governance events. Initial-install rollback removes
the installed version; upgrade rollback restores the exact preserved version.

## Release Checklist

Before raising the ecosystem maturity score, confirm:

- Code mode has an inspect/edit/verify loop with a visible process timeline.
- Permission and sandbox outcomes are visible in the operator panel.
- Replay gate failures block promotion unless a reasoned override is recorded.
- Plugin smoke summary is green or has explicit review-required rows.
- Public plugins show a verified publisher count and no invalid signatures.
- Plugin upgrades pass the transactional smoke and migration gates, and a
  lifecycle rollback drill restores the preserved version.
- Every publisher has an active key, overdue keys are rotated, and revocation
  drills remain traceable through the governance audit chain.
- The competitor scorecard shows the relevant evidence checklist.

