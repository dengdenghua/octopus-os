Write `decision_memo.json` with this machine-checkable top-level contract:

- `recommendation`: the string `"Option B"` (or `"B"`).
- `claims`: a list of material claim objects; each object has a `citations` list containing only fact IDs from the three evidence packs. The claims must collectively cite `tech-compat-b`, `fin-cost-b`, `fin-budget`, and `sec-critical-b`.
- `dissent`: explicit dissent retaining Option A's `120ms` latency advantage.
- `risks`: non-empty risks retaining Option B's vendor lock-in risk.

Resolve contradictions from the supplied evidence only; do not invent citation IDs.
