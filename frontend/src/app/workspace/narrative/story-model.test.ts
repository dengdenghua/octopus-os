import { describe, expect, it } from "vitest";

import type { NarrativeContextPack, NarrativeReviewRequest } from "./api";
import {
  contextBudgetUsage,
  mergePipelineStages,
  reviewReadiness,
} from "./story-model";

describe("narrative governance models", () => {
  it("always presents the complete ordered six-stage pipeline", () => {
    const stages = mergePipelineStages([
      {
        id: "draft",
        name: "draft",
        ordinal: 2,
        status: "submitted",
      },
      {
        id: "outline",
        name: "outline",
        ordinal: 1,
        status: "submitted",
      },
    ]);

    expect(stages.map((stage) => stage.id)).toEqual([
      "outline",
      "draft",
      "continuity",
      "style",
      "revision",
      "editorial",
    ]);
    expect(stages[2]).toMatchObject({ ordinal: 3, status: "pending" });
  });

  it("computes context usage without hiding over-budget data", () => {
    const pack = {
      token_budget: 1000,
      token_count: 1250,
    } as NarrativeContextPack;

    expect(contextBudgetUsage(pack)).toEqual({
      used: 1250,
      budget: 1000,
      percentage: 100,
      overBudget: true,
    });
  });

  it("requires both quorum and zero blockers before enabling canon commit", () => {
    const review = {
      status: "open",
      quorum_required: 2,
      quorum_received: 2,
      blockers: [],
    } as NarrativeReviewRequest;

    expect(reviewReadiness(review)).toEqual({
      quorumMet: true,
      hasBlockers: false,
      canCommit: true,
    });
    expect(
      reviewReadiness({ ...review, blockers: ["continuity conflict"] }),
    ).toMatchObject({ canCommit: false, hasBlockers: true });
    expect(reviewReadiness({ ...review, quorum_received: 1 })).toMatchObject({
      canCommit: false,
      quorumMet: false,
    });
  });
});
