import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import { renderWithProviders } from "@/test/harness";
import type { ExecutionPlan } from "@/core/threads/types";
import { ExecutionPlanReview, type PlanAction } from "./execution-plan-review";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const plan: ExecutionPlan = {
  created_at: 0,
  plan_id: "plan/a",
  title: "Organize documents",
  status: "pending_review",
  risk_level: "medium",
  estimated_actions: 1,
  steps: [
    {
      step_id: "s1",
      description: "Preview document destinations",
      tools_needed: [],
      estimated_duration: "fast",
      risk: "low",
      status: "pending",
    },
  ],
};
const labels = {
  approve: "Approve",
  modify: "Save and review",
  reject: "Confirm reject",
};

function openAction(action: PlanAction) {
  if (action === "modify") {
    fireEvent.click(screen.getByRole("button", { name: "Edit plan" }));
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Preserve uncertain documents" },
    });
  }
  if (action === "reject") {
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Review dates first" },
    });
  }
  return screen.getByRole("button", { name: labels[action] });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe.each<PlanAction>(["approve", "modify", "reject"])(
  "legacy plan %s",
  (action) => {
    it.each([401, 403, 404, 409, 500, "network"])(
      "does not continue or claim success after %s; remains retryable",
      async (status) => {
        const fetchMock = vi.fn();
        if (status === "network")
          fetchMock.mockRejectedValue(new TypeError("connection lost"));
        else
          fetchMock.mockResolvedValue(
            new Response("unavailable", { status: status as number }),
          );
        vi.stubGlobal("fetch", fetchMock);
        const onAction = vi.fn();
        const event = vi.fn();
        window.addEventListener("echo:plan-action", event);
        try {
          renderWithProviders(
            <ExecutionPlanReview
              plan={plan}
              threadId="thread-a"
              onAction={onAction}
            />,
          );
          fireEvent.click(openAction(action));
          expect(await screen.findByRole("alert")).toHaveTextContent(
            `Failed to ${action} plan`,
          );
          expect(
            screen.getByRole("button", { name: labels[action] }),
          ).toBeEnabled();
          expect(onAction).not.toHaveBeenCalled();
          expect(event).not.toHaveBeenCalled();
          expect(toast.success).not.toHaveBeenCalled();
          expect(toast.info).not.toHaveBeenCalled();
          expect(toast.error).toHaveBeenCalledTimes(1);
          if (action !== "approve")
            expect(screen.getByRole("textbox")).toHaveValue(
              action === "modify"
                ? "Preserve uncertain documents"
                : "Review dates first",
            );
          expect(fetchMock.mock.calls[0]![0]).toContain(
            `/api/plan/plan%2Fa/${action}`,
          );

          // A later acknowledged request can use the same preserved edit/reason.
          fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
          fireEvent.click(screen.getByRole("button", { name: labels[action] }));
          await waitFor(() => expect(onAction).toHaveBeenCalledTimes(1));
          expect(event).toHaveBeenCalledTimes(1);
          expect(screen.queryByRole("alert")).toBeNull();
          const body = JSON.parse(fetchMock.mock.calls[1]![1].body);
          expect(body.thread_id).toBe("thread-a");
          if (action === "modify")
            expect(body.modified_steps[0].description).toBe(
              "Preserve uncertain documents",
            );
          if (action === "reject")
            expect(body.reason).toBe("Review dates first");
        } finally {
          window.removeEventListener("echo:plan-action", event);
        }
      },
    );
  },
);
