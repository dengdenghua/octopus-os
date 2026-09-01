# ADR 锚点 · Governance Map

> 哪个 ADR 治理哪段代码 · 从 ADR markdown 里的反引号路径引用抽取 · **PR 审查时用**：改了某文件 · 看它被哪些 ADR 引用。

## Per ADR

### [ADR-001 · Bionic naming + dual-track contracts](../../adr/001-bionic-naming.md) · *Accepted*

- `docs/biomimetic/*`
- `docs/invariants.md`
- `docs/naming.md`
- `runtime/core/cerebrum/`
- `runtime/core/hearts/`
- `runtime/execution/suckers/`

### [ADR-002 · Mode-gated write scope](../../adr/002-mode-gated-scope.md) · *Accepted*

- `agents/<id>/workspace/<thread_id>/`
- `tests/test_scope.py`

### [ADR-003 · Session object replaces scattered ContextVars](../../adr/003-session-object.md) · *Accepted*

_未引用代码路径_

### [ADR-004 · OpenAPI → TypeScript type generation pipeline](../../adr/004-openapi-ts-codegen.md) · *Accepted*

- `docs/openapi-snapshot.json`
- `frontend/src/core/api/openapi-types.ts`

### [ADR-005 · Agent capability flags](../../adr/005-agent-capabilities.md) · *Accepted*

- `docs/agent-capabilities.md`
- `frontend/src/core/agents/types.ts`

### [ADR-006 · Lifecycle hook system](../../adr/006-lifecycle-hooks.md) · *Accepted*

- `runtime/safety/hooks/{events,registry,runner}.py`
- `tests/test_safety_hooks.py`

### [ADR-007 · MCP server trust store](../../adr/007-mcp-trust-store.md) · *Accepted*

- `runtime/adapters/mcp_client/bridge.py:require_trust`
- `runtime/adapters/mcp_client/trust.py`
- `tests/test_mcp_trust.py`

### [ADR-008 · Constitution enforcement profiles](../../adr/008-constitution-profiles.md) · *Accepted*

- `runtime/safety/constitution/gate.py`
- `runtime/safety/constitution/profiles.py`
- `tests/test_constitution_profiles.py`

### [ADR-008 · Echo Mobile（移动触手 / 跨端编排）](../../adr/008-echo-mobile.md) · *Accepted*

- `runtime/execution/arms/presets.py`
- `runtime/tentacle/`

### [ADR-009 · OKF as the knowledge substrate](../../adr/009-okf-knowledge-substrate.md) · *Proposed*

- `docs/architecture*`
- `docs/auto`
- `docs/auto/`
- `runtime/safety/recovery/scheduler.py`
- `scripts/gen_wiki.py`
- `tests/test_auto_docs_fresh.py`
- `tests/test_repo_context.py`
- `tests/test_wiki_qa.py`

## Per file

- `agents/<id>/workspace/<thread_id>/` ← [002-mode-gated-scope](../../adr/002-mode-gated-scope.md)
- `docs/agent-capabilities.md` ← [005-agent-capabilities](../../adr/005-agent-capabilities.md)
- `docs/architecture*` ← [009-okf-knowledge-substrate](../../adr/009-okf-knowledge-substrate.md)
- `docs/auto` ← [009-okf-knowledge-substrate](../../adr/009-okf-knowledge-substrate.md)
- `docs/auto/` ← [009-okf-knowledge-substrate](../../adr/009-okf-knowledge-substrate.md)
- `docs/biomimetic/*` ← [001-bionic-naming](../../adr/001-bionic-naming.md)
- `docs/invariants.md` ← [001-bionic-naming](../../adr/001-bionic-naming.md)
- `docs/naming.md` ← [001-bionic-naming](../../adr/001-bionic-naming.md)
- `docs/openapi-snapshot.json` ← [004-openapi-ts-codegen](../../adr/004-openapi-ts-codegen.md)
- `frontend/src/core/agents/types.ts` ← [005-agent-capabilities](../../adr/005-agent-capabilities.md)
- `frontend/src/core/api/openapi-types.ts` ← [004-openapi-ts-codegen](../../adr/004-openapi-ts-codegen.md)
- `runtime/adapters/mcp_client/bridge.py:require_trust` ← [007-mcp-trust-store](../../adr/007-mcp-trust-store.md)
- `runtime/adapters/mcp_client/trust.py` ← [007-mcp-trust-store](../../adr/007-mcp-trust-store.md)
- `runtime/core/cerebrum/` ← [001-bionic-naming](../../adr/001-bionic-naming.md)
- `runtime/core/hearts/` ← [001-bionic-naming](../../adr/001-bionic-naming.md)
- `runtime/execution/arms/presets.py` ← [008-echo-mobile](../../adr/008-echo-mobile.md)
- `runtime/execution/suckers/` ← [001-bionic-naming](../../adr/001-bionic-naming.md)
- `runtime/safety/constitution/gate.py` ← [008-constitution-profiles](../../adr/008-constitution-profiles.md)
- `runtime/safety/constitution/profiles.py` ← [008-constitution-profiles](../../adr/008-constitution-profiles.md)
- `runtime/safety/hooks/{events,registry,runner}.py` ← [006-lifecycle-hooks](../../adr/006-lifecycle-hooks.md)
- `runtime/safety/recovery/scheduler.py` ← [009-okf-knowledge-substrate](../../adr/009-okf-knowledge-substrate.md)
- `runtime/tentacle/` ← [008-echo-mobile](../../adr/008-echo-mobile.md)
- `scripts/gen_wiki.py` ← [009-okf-knowledge-substrate](../../adr/009-okf-knowledge-substrate.md)
- `tests/test_auto_docs_fresh.py` ← [009-okf-knowledge-substrate](../../adr/009-okf-knowledge-substrate.md)
- `tests/test_constitution_profiles.py` ← [008-constitution-profiles](../../adr/008-constitution-profiles.md)
- `tests/test_mcp_trust.py` ← [007-mcp-trust-store](../../adr/007-mcp-trust-store.md)
- `tests/test_repo_context.py` ← [009-okf-knowledge-substrate](../../adr/009-okf-knowledge-substrate.md)
- `tests/test_safety_hooks.py` ← [006-lifecycle-hooks](../../adr/006-lifecycle-hooks.md)
- `tests/test_scope.py` ← [002-mode-gated-scope](../../adr/002-mode-gated-scope.md)
- `tests/test_wiki_qa.py` ← [009-okf-knowledge-substrate](../../adr/009-okf-knowledge-substrate.md)

