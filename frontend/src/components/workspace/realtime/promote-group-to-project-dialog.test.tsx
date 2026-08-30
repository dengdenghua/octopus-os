import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
}));

vi.mock("@/core/projects/hooks", () => ({
  usePromoteGroupToProject: () => ({
    mutate: mocks.mutate,
    isPending: false,
  }),
}));

import { PromoteGroupToProjectDialog } from "./promote-group-to-project-dialog";

describe("PromoteGroupToProjectDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("keeps the current group and creates an attached project without running it", async () => {
    const user = userEvent.setup();
    const onPromoted = vi.fn();
    mocks.mutate.mockImplementation((_input, options) => {
      void options.onSuccess({
        project: { id: "P-1", name: "秋季发布" },
      });
    });

    renderWithProviders(
      <PromoteGroupToProjectDialog
        open
        onOpenChange={vi.fn()}
        threadId="thread-1"
        defaultName="发布讨论群"
        onPromoted={onPromoted}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByLabelText("项目名称")).toHaveValue("发布讨论群");
    await user.clear(screen.getByLabelText("项目名称"));
    await user.type(screen.getByLabelText("项目名称"), "秋季发布");
    await user.type(
      screen.getByLabelText("项目目标"),
      "在九月底前完成新品发布",
    );
    await user.click(screen.getByRole("button", { name: "创建并绑定" }));

    expect(mocks.mutate).toHaveBeenCalledWith(
      {
        threadId: "thread-1",
        name: "秋季发布",
        goal: "在九月底前完成新品发布",
      },
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );
    expect(onPromoted).toHaveBeenCalledWith({
      id: "P-1",
      name: "秋季发布",
    });
  });
});
