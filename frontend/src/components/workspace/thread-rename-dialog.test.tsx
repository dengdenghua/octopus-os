import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { AllProviders } from "@/test/harness";
import { ThreadRenameDialog } from "./thread-rename-dialog";

describe("ThreadRenameDialog", () => {
  it("keeps rename actions keyboard-safe and prevents blank saves", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    function Harness() {
      const [value, setValue] = useState("原始标题");
      return (
        <AllProviders locale="zh-CN">
          <ThreadRenameDialog
            open
            value={value}
            onChange={(next) => {
              onChange(next);
              setValue(next);
            }}
            onClose={onClose}
            onSubmit={onSubmit}
          />
        </AllProviders>
      );
    }

    render(<Harness />);

    expect(screen.getByRole("dialog")).toHaveAccessibleDescription("重命名");
    const input = screen.getByRole("textbox");
    await user.clear(input);
    expect(onChange).toHaveBeenCalledWith("");
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();

    await user.type(input, "新标题");
    await user.keyboard("{Enter}");
    expect(onSubmit).toHaveBeenCalledOnce();

    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
