import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { HighRiskApprovalDialog } from "./high-risk-approval-dialog";

describe("high-risk approval dialog", () => {
  it("requires a password and passes it only to the confirmation callback", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <HighRiskApprovalDialog
        open
        title="启动影音中心？"
        description="需要管理员本人复核"
        targetLabel="jellyfin"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    const submit = screen.getByRole("button", { name: "确认执行" });
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(submit);

    expect(onConfirm).toHaveBeenCalledWith("device-password");
    expect(screen.queryByText("device-password")).not.toBeInTheDocument();
  });

  it("keeps the dialog open and shows a backend rejection", async () => {
    const user = userEvent.setup();
    render(
      <HighRiskApprovalDialog
        open
        title="永久清空？"
        description="不可撤销"
        destructive
        onCancel={vi.fn()}
        onConfirm={vi.fn().mockRejectedValue(new Error("管理员密码不正确"))}
      />,
    );

    await user.type(screen.getByLabelText("设备管理员密码"), "wrong");
    await user.click(screen.getByRole("button", { name: "确认执行" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "管理员密码不正确",
    );
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });
});
