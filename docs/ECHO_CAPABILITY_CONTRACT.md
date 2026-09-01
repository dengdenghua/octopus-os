# ECHO Capability Contract v0.1

ECHO OS exposes system operations to humans and Agents through one authenticated,
versioned contract. The contract describes **what the system can do**; it is not a
permission grant and it is not the Agent profile capability flag map.

```text
Agent entitlement       Who may request a privileged mode
System capability       What bounded operation a provider implements
Policy decision         Whether this actor/intent/target is allowed now
Approval token          Short-lived, single-use authority for high-risk execution
Audit event             Durable evidence of the decision and result
```

Existing `Agent.capabilities` flags such as `code_mode_unlock` remain default-closed
Agent entitlements. They are intentionally not copied into this registry.

## Runtime flow

```text
User intent
  → Echo Agent task
  → POST /api/appliance/capabilities/decisions
  → ALLOW | ASK | DENY
  → existing provider API
  → existing appliance audit chain
  → task result
```

The policy preflight and every provider endpoint use the authenticated device actor.
The caller cannot supply or impersonate the actor in the request body.

## Discovery API

All routes require the same device login as the file manager and application catalog.

```text
GET  /api/appliance/capabilities
GET  /api/appliance/capabilities/{capabilityId}
POST /api/appliance/capabilities/decisions
```

The list can be filtered by the exact provider identifier:

```text
GET /api/appliance/capabilities?provider=echo-os.storage.omv
```

Capabilities whose concrete router did not mount are not advertised. A mounted provider
may still report a runtime-unavailable state—for example, the application catalog when
Docker control is temporarily unavailable.

## Initial providers

| Capability | Provider | Effect | Decision |
| --- | --- | --- | --- |
| `apps.list` | `echo-os.apps` | read | allow |
| `apps.start` | `echo-os.apps` | system control | password step-up |
| `apps.stop` | `echo-os.apps` | system control | password step-up |
| `files.list` | `echo-os.files` | read | allow |
| `files.upload` | `echo-os.files` | recoverable write | allow |
| `files.trash.move` | `echo-os.files` | recoverable delete | allow |
| `files.trash.restore` | `echo-os.files` | recoverable write | allow |
| `files.trash.empty` | `echo-os.files` | irreversible delete | password step-up |
| `storage.health.read` | `echo-os.storage.omv` | read | allow |

The `storage.health.read` identifier is deliberately independent of OMV. A future
`echo-os.storage.native` provider can implement the same capability through constrained
`smartctl`, `lsblk`, Samba/NFS and filesystem adapters without changing Agent intent or UI.

## Policy request

```json
{
  "capabilityId": "apps.start",
  "intentId": "task.media.start",
  "target": "a1b2c3d4e5f6"
}
```

`intentId` is a stable task/intent identifier, not natural-language prompt content. Targets
must match the capability scope. The v0.1 validators accept only fixed resources, relative
NAS paths, enumerated-style container identifiers and recycle-bin record identifiers. An
unknown capability or invalid target produces a recorded `deny`, never a permissive fallback.

## ASK and intent-bound approval

A high-risk decision returns the existing approval request and the headers required for
execution:

```json
{
  "decision": "ask",
  "reasonCode": "PASSWORD_STEP_UP_REQUIRED",
  "approval": {
    "endpoint": "/api/appliance/approvals",
    "requestBody": {
      "action": "app.start",
      "target": "a1b2c3d4e5f6",
      "intentId": "task.media.start"
    },
    "executionHeaders": {
      "X-Echo-Approval": "<approvalToken>",
      "X-Echo-Intent": "task.media.start"
    },
    "ttlSeconds": 90,
    "singleUse": true
  }
}
```

When `intentId` is present during approval issuance, it is signed into the token. Execution
must present the same `X-Echo-Intent`; a missing or different value is rejected before the
token is consumed. Tokens issued by older clients without an intent remain compatible and
retain their actor/action/target/single-use binding.

## Security invariants

- Discovery and decisions require a verified device session.
- The registry never exposes raw shell commands or arbitrary OMV RPC names.
- Relative-path scope rejects absolute paths, parent traversal, NUL and backslash ambiguity.
- Fixed targets cannot be replaced by caller-controlled values.
- Production policy decisions fail closed when the tamper-evident audit is unavailable.
- A policy `allow` does not bypass authentication, path validation, provider audit or the
  provider's own approval gate.
- A policy decision records actor, intent id, capability id, target, risk and reason code;
  passwords, approval tokens and session credentials never enter decision metadata.

## v0.1 boundary

This version is discovery plus policy preflight over existing provider APIs. It does not add
a second execution dispatcher and therefore does not duplicate file, Docker or OMV security
logic. Echo's read-only task surface joins these decisions to the Agent's authoritative task
records through `intentId == taskId`; see [ECHO Task Projection](ECHO_TASK_PROJECTION.md).
