# Self-Evolution Minimum Loop

This document defines the near-term self-evolution loop for Echo. The target
is not blind self-modification. The target is governed improvement: propose,
attach evidence, evaluate, review, promote, monitor, and roll back.

## Existing Foundations

| Module | Existing capability | Gap |
|---|---|---|
| `runtime/memory/user_store.py` | Explicit user facts, scopes, simple search, injection config | Missing candidates, conflicts, provenance, approval, rollback |
| `runtime/memory/profile.py` | Extracts explicit remember/note style statements | Does not learn from ordinary success/failure traces |
| `runtime/memory/turn_scoring.py` | Per-turn scores and SOUL impact analysis | Not yet a unified promotion ledger |
| `runtime/memory/deep_evolution.py` | `deep_reflect`, `deep_evolve`, dry-run, judged candidates | Focused on SOUL lessons; needs a general proposal system |
| `runtime/safety/regeneration/*` | Recipe eval, auto promote, memory consolidation, skill proposal decisions | Promotion evidence needs one review model |
| `runtime/memory/genome/journal.py` | Append-only journal and decision event types | Needs a unified proposal/audit read model |

## Minimum Loop

```text
Run finished
  -> Collect evidence
  -> Score turn
  -> Generate proposals
  -> Attach provenance
  -> Run checks
  -> Human review
  -> Promote / reject / defer
  -> Record decision
  -> Monitor regression
  -> Roll back when needed
```

## Proposal Types

| Type | Source | Promotion target | Minimum evidence |
|---|---|---|---|
| `memory_candidate` | User preferences, project facts, failure lessons, success lessons | User/project/agent/team memory | Source thread, source text, confidence, scope, conflict check |
| `skill_candidate` | Repeated successful tool sequences | Skill registry or `SKILL.md` | Sample count, success rate, input/output pattern, shadow validation |
| `workflow_candidate` | Repeatable execution traces | Workflow editor / DAG | Representative run, step graph, failure recovery point, eval case |
| `prompt_candidate` | SOUL lesson, system prompt, mode instruction | Agent core or prompt registry | Before/after score, risk level, rollback point |
| `mcp_candidate` | Tool discovery or recommendation | MCP config/trust store | Tool digest, permission scope, vet report |
| `permission_candidate` | Repeated approval/denial behavior | Approval policy rule | Match condition, risk explanation, expiration policy |

## Suggested Proposal Shape

```json
{
  "id": "proposal_xxx",
  "type": "memory_candidate",
  "status": "draft | needs_eval | ready_for_review | promoted | rejected | rolled_back",
  "scope": "global | project | agent | team | team-agent",
  "source": {
    "thread_id": "thread_xxx",
    "task_id": "task_xxx",
    "journal_event_ids": [],
    "artifact_paths": []
  },
  "candidate": {},
  "evidence": {
    "sample_count": 0,
    "success_rate": null,
    "score_delta": null,
    "cost_delta": null,
    "risk": "low | medium | high"
  },
  "checks": {
    "conflict_check": "pass | fail | unknown",
    "security_check": "pass | fail | unknown",
    "eval_check": "pass | fail | skipped"
  },
  "decision": {
    "actor": "",
    "action": "promote | reject | defer | rollback",
    "reason": "",
    "ts": ""
  }
}
```

## P0 Build Order

1. Create a proposal ledger.

   Start with JSONL or SQLite. The important part is one status model for memory,
   skill, workflow, prompt, MCP, and permission proposals.

2. Record decisions through journal.

   The journal already has several decision events. Short term, add a generic
   evolution proposal decision or build a read model that aggregates the
   existing decision events.

3. Implement `memory_candidate` first.

   Minimum behavior:

   - generate candidate lessons from successful and failed turns;
   - preserve thread/task/event provenance;
   - detect duplicates and obvious conflicts;
   - send candidates to a review queue;
   - write approved candidates to scoped memory;
   - show which memories were injected into later runs.

4. Implement `workflow_candidate` next.

   Code repair, research, browser, and file-organization runs can become
   workflows only after at least one replay/eval check.

5. Open automatic promotion last.

   Auto-promotion should only apply to low-risk, reversible, eval-backed
   candidates with cooldown and regression monitoring.

## Memory Governance Rules

| Rule | Requirement |
|---|---|
| No silent long-term write | Except explicit user commands, automatic extraction creates candidates only |
| Every memory has provenance | At minimum: thread/task/source text |
| Every memory has scope | Do not mix global, project, agent, team, and team-agent memory |
| Conflict beats merge | Contradictions go to review instead of overwrite |
| Memory use is explainable | Run detail lists memory injections |
| Expiration and revocation exist | Time-sensitive or low-confidence memories can expire |

## Promotion Gates

| Risk | Example | Promotion requirement |
|---|---|---|
| Low | User preference, formatting preference, low-impact workflow | Provenance + review |
| Medium | Prompt lesson, common permission rule, project fact | Eval + review + rollback |
| High | Auto-execute permission, MCP installation, shell/write policy | Security check + eval + explicit approval |

## P0 Acceptance Criteria

1. After any task, the system can show whether proposals were produced and why.
2. A user can approve or reject a memory candidate.
3. Approved memory is retrieved in a later task and the run shows the citation.
4. Rejected candidates do not repeatedly reappear unless new evidence changes.
5. Promotion, rejection, deferral, and rollback are recorded in journal or the
   proposal ledger.
