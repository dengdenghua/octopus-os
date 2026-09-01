# ADR-007 · MCP server trust store

Status: Accepted | Date: 2026-04

## Context

MCP (Model Context Protocol) servers are arbitrary executables
the runtime spawns and whose tools it exposes to the planner.
Today's flow:

1. User declares a server in `config.yaml` or via
   `PUT /api/mcp/config`.
2. The router spawns the binary (e.g. `npx -y @page-agent/mcp`).
3. `register_mcp_tools_as_skills(registry, client)` enumerates
   the server's tools and registers each as an Echo `Skill`.
4. The planner sees those skills indistinguishably from built-in
   ones and may call them.

That's a supply-chain attack waiting to happen. A typo in a
config (`@page-aget/mcp` resolves to a typosquatted package), a
compromised npm publisher, a malicious server in a shared config
template — any of these silently grants tool execution rights to
arbitrary code in the user's process space.

Modern agent runtimes mitigate this by demanding **explicit user
approval** before an MCP server's tools become callable. We
needed the same for Echo.

## Decision

Add a persistent, per-user trust store at
`runtime.adapters.mcp_client.trust.MCPTrustStore`, backed by a
JSON file (`$ECHO_HOME/mcp_trust.json`, default
`~/.echo/mcp_trust.json`).

### Trust entry

Each entry records:

```python
TrustEntry(
    server_name: str,
    approved: bool,
    added_ts: float,
    tool_digest: str,   # hash of sorted(set(tool_names))
    note: str,
)
```

`tool_digest` pins the tool surface at approval time. If the
server later adds or removes tools (a benign upgrade or a
malicious update), the digest mismatches and `is_approved()`
returns `False` — forcing the user to re-confirm what's now
exposed.

### Bridge gate

`register_mcp_tools_as_skills` grew a `require_trust=False`
parameter. Production callers (the MCP router's
`_register_runtime_mcp`) pass `require_trust=True`. Test code
using `MockMCPClient` passes `require_trust=False` because
mocks aren't running user binaries.

When `require_trust=True` and the server is not approved, the
bridge logs a warning and returns an empty list — the registry
gets no new skills, the planner can't call the server's tools,
and the operator sees a clear "approve via /api/mcp/trust" hint.

### HTTP surface

```
GET    /api/mcp/trust                  · list all entries
POST   /api/mcp/trust                  · approve {server_name, tool_names?, note?}
DELETE /api/mcp/trust/{server_name}    · revoke (entry retained for audit)
```

Approval and revocation are explicit user actions through the
Settings → MCP page (frontend surface added separately).

## Alternatives considered

* **Trust-on-first-use (TOFU).** Auto-approve the first
  registration, prompt only on tool-set changes. Rejected — the
  attack window is precisely the first registration; TOFU
  protects nothing.

* **Allowlist by source URL.** Approve `npm:@anthropic/*` in bulk
  and skip per-server confirmation. Considered for future · a
  reasonable convenience layer once we have a curated registry,
  but doesn't replace per-server approval for arbitrary commands.

* **Unconditional gating (no escape hatch).** Default
  `require_trust=True` on the bridge with no override. Rejected
  because tests use `MockMCPClient` extensively · forcing them
  through the trust store would couple unit tests to an
  unrelated subsystem.

## Consequences

**Positive**

* Default-safe: a fresh install can't run an MCP server's tools
  without an explicit user approval, even if the config is
  inherited from a template.
* Tool-digest pinning catches benign-looking server upgrades that
  add new (potentially dangerous) tools.
* Audit trail: revoked entries keep their `added_ts` and
  `tool_digest` for forensic review.

**Negative**

* One extra click in the user flow. Acceptable cost for the
  threat model.
* The HTTP surface adds three endpoints · OpenAPI snapshot must
  be regenerated on next CI run.

**Neutral**

* Tests must monkeypatch `ECHO_HOME` and call
  `reset_trust_store_for_tests()` to avoid touching the real
  user's `~/.echo`. Documented in the test fixture.

## References

* MCP trust-prompt reference: established security pattern in modern agent runtimes
* Implementation: `runtime/adapters/mcp_client/trust.py`
* Bridge gate: `runtime/adapters/mcp_client/bridge.py:require_trust` parameter
* Tests: `tests/test_mcp_trust.py` (12 cases)
