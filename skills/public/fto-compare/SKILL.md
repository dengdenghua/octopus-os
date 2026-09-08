---
name: fto-compare
description: Compare a new product feature description against a list of patent records and produce per-pair FTO risk hypotheses. Returns a similarity matrix with risk levels, claim-overlap reasoning, and design-around suggestions. Used after patent-search has narrowed the corpus to high-relevance candidates and claim-extract has populated keyClaimsSummary.
source: registry
---

# Freedom-to-operate comparison

Given a description of a new product feature and a set of candidate `PatentRecord`s, produce per-patent risk hypotheses and design-around suggestions.

> You are NOT a patent attorney. Your output is a screening hypothesis, not a legal opinion. Use language like "potential overlap with claim 1" — never "this would infringe" or "this is clear of patent X".

## Step 1: Reduce the new feature to claim-shaped elements

Convert the prose feature description into a list of technical elements that mirror the structure of a patent claim. For example:

Feature: "床笠内嵌入压力传感阵列，结合 PPG 传感器测量心率和呼吸，输出睡眠分期结果到手机 App"

Elements:
1. 床笠形态的可穿戴/家居载体
2. 压力传感阵列（多点）
3. PPG 传感器（光学心率）
4. 呼吸监测（基于压力或 PPG 衍生）
5. 睡眠分期算法（输出阶段标签）
6. 手机 App 显示报告

Each element becomes a row in the comparison matrix.

## Step 2: For each candidate patent, locate the relevant claim text

Use the patent's `keyClaimsSummary` (populated by `claim-extract`). If empty, raise an explicit warning and recommend running `claim-extract` first — do not score against an empty claim.

For each independent claim, identify which of the new feature's elements appear (literally or by clear equivalence) in the claim language.

## Step 3: Score per patent

| Risk level | Trigger |
|---|---|
| `critical` | Independent claim reads on ≥ 80% of the new feature's elements; same product category; granted in target market; legal status active. |
| `high` | Independent claim reads on ≥ 60% of elements OR a key novel element of the feature is fully covered. |
| `medium` | Independent claim reads on ≥ 40% of elements OR a common-but-non-core element is covered. |
| `low` | Some keyword overlap but no claim element clearly maps; or patent is expired/withdrawn in the target market. |

## Step 4: Suggest design-arounds (only for medium+ risks)

For each medium+ risk, propose a SPECIFIC design change that would remove a covered element. Examples:

- "Replace pressure-array sensor with a single load-cell strain gauge" — removes the multi-point feature element.
- "Move sleep-staging inference to cloud rather than edge" — removes the on-device-algorithm element.
- "Use ECG instead of PPG for heart rate" — sidesteps the optical-sensor claim element.

Always frame design-arounds as hypotheses to be reviewed; never as guaranteed clearance.

## Step 5: Persist a PatentRisk record per medium+ risk

For each medium / high / critical risk, call:

```
POST /api/company/projects/{projectId}/patents/risks
Body: {
  "projectId": "...",
  "patentRecordId": "<the candidate patent's id>",
  "title": "Potential overlap with <Patent Title>",
  "relatedProductFeature": "<element-level description>",
  "riskLevel": "high",
  "reason": "Independent claim 1 reads on elements 1, 2, 4 of the new feature.",
  "suggestedDesignAround": "Replace pressure-array with single load-cell.",
  "requiresPatentAttorneyReview": true,
  "status": "open"
}
```

`requiresPatentAttorneyReview` MUST be `true` for any high or critical risk. This is non-negotiable — it gates downstream actions (crowdfunding launch, public reveal).

## Step 6: Output the comparison matrix

```text
FTO comparison: <feature label>

Project: <projectId>
Candidates compared: <N>

Critical risks: <count>
High risks: <count>
Medium risks: <count>
Low / cleared: <count>

Findings:

1. [CRITICAL] <Patent Title> · <Applicant> · <Country>
   Patent: <publicationNumber>
   Claim language overlap: elements 1, 2, 3, 4 of 6
   Reason: Independent claim 1 reads on the multi-point pressure
           array combined with PPG. Same product category (床笠).
   Design-around: ...
   Risk record id: <PatentRisk id>
   ⚠ Requires patent-attorney review before launch.

2. [HIGH] ...

Recommendation:
- Schedule patent-attorney review for the N high+ risks before BOM lock-in.
- Apply the suggested design-arounds and re-run fto-compare to verify.
- Consider filing on novel elements not yet covered by competitor patents (use claim-extract output to identify white space).
```

Hand the output to the parent orchestrator. Critical / high risks should be routed via the company workbench dispatcher to the project owner.
