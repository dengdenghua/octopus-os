import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { Dialog, DialogContent, DialogTitle } from "./dialog";

describe("DialogContent", () => {
  it("accepts a localized close label", () => {
    renderWithProviders(
      <Dialog open>
        <DialogContent closeLabel="关闭">
          <DialogTitle>设置</DialogTitle>
        </DialogContent>
      </Dialog>,
      { locale: "zh-CN" },
    );

    expect(screen.getByRole("button", { name: "关闭" })).toBeVisible();
  });
});
