---
name: patent-risk-register
description: Record a patent-related risk in the project risk register with rationale, suggested mitigation, and routing flags. Every medium+ patent risk becomes a tracked entry that surfaces in project dashboards, daily risk reports, and (for high+ risks) gates launch decisions. Use after fto-compare has identified a patent that may block the project's technical approach.
source: registry
---

# Patent risk registration

Persist a patent-related risk to the project risk register and route it for appropriate review.

> **You are registering a hypothesis, not a legal conclusion.** The risk record language must reflect the screening nature of the AI output.

## Step 1: Collect the parameters

You receive:

```json
{
  "projectId": "...",
  "patentRecordId": "<id of the patent in question>",
  "title": "Potential overlap with <Patent Title>",
  "relatedProductFeature": "床笠 PPG 传感 + 睡眠分期",
  "riskLevel": "high",
  "reason": "Independent claim 1 of CN113711690B reads on elements...",
  "suggestedDesignAround": "Replace PPG with ECG; move staging to cloud.",
  "requiresPatentAttorneyReview": true
}
```

## Step 2: Validate

- `riskLevel` must be one of: `low | medium | high | critical`.
- `requiresPatentAttorneyReview` MUST be `true` if `riskLevel` is `high` or `critical`. If the caller passes `false` for a high+ risk, **force it to `true`** and surface a warning in the response.
- `reason` must reference specific claim language or elements; generic phrases like "overlap detected" are insufficient. If the reason is too vague, request that the caller re-run `fto-compare` with more detail.

## Step 3: Persist the risk

Call the company workbench risk API:

```
POST /api/company/projects/{projectId}/risks
Body: {
  "type": "patent",
  "title": "...",
  "relatedPatentRecordId": "<patentRecordId>",
  "relatedProductFeature": "...",
  "riskLevel": "high",
  "reason": "...",
  "suggestedMitigation": "<suggestedDesignAround>",
  "requiresLegalReview": <requiresPatentAttorneyReview>,
  "status": "open",
  "ownerId": null,  // will be routed by company workbench dispatcher
  "createdAt": "<now ISO>",
  "updatedAt": "<now ISO>"
}
```

The API returns `{riskId, routed_to: [...]}`

## Step 4: Record routing outcome

If `requiresPatentAttorneyReview` is `true`, the risk should be tagged for escalation. The company workbench dispatcher will:

- Send a notification to the project owner via the project's ding_talk_group_id (if configured).
- Surface the risk in the daily `/今日风险` report.
- Block the project from transitioning to `pilot` or `commercial` stage until the risk status changes to `mitigated` or `accepted`.

## Step 5: Output

```text
Patent risk registered: <title>

Risk id: <riskId>
Project: <projectId>
Patent: <publicationNumber>
Risk level: HIGH
Requires patent attorney review: YES
Routed to: <project owner>, <patent attorney contact if configured>

This risk will surface in:
- Daily risk report (/今日风险)
- Project dashboard
- Stage transition gate (blocks pilot/commercial until resolved)

Next step:
- Schedule patent-attorney consultation.
- Prototype the suggested design-around and validate technical feasibility.
- Re-run fto-compare after design changes to verify clearance.
```

Hand the output to the parent orchestrator.
