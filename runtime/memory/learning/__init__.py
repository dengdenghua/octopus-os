"""Learning & self-evolution · turn scoring, review queue, promotion.

Submodules:

* ``turn_scoring`` — heuristic turn-outcome scorer (0.0/0.5/1.0)
  writing to ``.scores.jsonl`` with trim-at-5000-lines
* ``soul_holdout`` — auto-seeded golden-reply holdout + regression gate
  (floor=0.6, regression_tolerance=0.05)
* ``deep_evolution`` — MiniMax-style self-evolution loop
  (propose → judge → gate → apply) over SOUL.md lessons
* ``review_queue`` — atomic-write review queue with cross-process lock
* ``promotion_applier`` — promotes review items to experience ledger /
  proposal ledger / forged skills, with audit chain
* ``experience_ledger`` — experience records with 4-axis memory_quality
  scoring (freshness + occurrence + priority + contradiction penalty)
* ``subagent_review`` — conservative subagent-run-to-review-queue
  candidate converter

Existing imports go through the submodule path, e.g.::

    from runtime.memory.learning.review_queue import ReviewQueue
    from runtime.memory.learning.experience_ledger import ExperienceLedger

This package intentionally does NOT re-export symbols at the top level
to avoid triggering heavy submodule imports (LLM router, pydantic) for
callers that only need a single module.
"""
