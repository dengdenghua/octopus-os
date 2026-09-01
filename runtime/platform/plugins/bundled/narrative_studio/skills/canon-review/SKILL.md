---
name: canon-review
description: Prepare an evidence-based editorial recommendation for a Narrative Studio candidate without exercising human canon-governance authority.
license: Apache-2.0
metadata:
  summary: Recommend, never promote, candidate canon
  affinity: [narrative, editorial, governance, candidate]
  cost_profile: low
  canon_policy: candidate_only
---

# Canon readiness review

Assess whether a candidate is ready for human governance. Summarize scope, revision, supporting source refs, continuity findings, unresolved blockers, and a recommendation of `ready_for_human_review`, `revise`, or `insufficient_evidence`. Distinguish editorial preference from a factual or policy blocker.

This skill has no governance authority. It must not submit or alter votes, mark review requests resolved, remove blockers, create a CanonCommit, or describe a candidate as canon. Only an authenticated human governance path with explicit confirmation may perform those actions.
