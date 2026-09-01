import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";
import { EchoDesktopWindowChromeContext } from "./embedded-window-bridge";
import * as bridge from "./embedded-window-bridge";
import { WorkspaceSurfaceHeader } from "./workspace-surface-header";

describe("WorkspaceSurfaceHeader", () => {
  it("keeps the workspace switch but lets Echo OS own the only window controls", () => {
    vi.spyOn(bridge, "isEmbeddedWindow").mockReturnValue(false);

    renderWithProviders(
      <EchoDesktopWindowChromeContext.Provider value>
        <WorkspaceSurfaceHeader active="agent" />
      </EchoDesktopWindowChromeContext.Provider>,
    );

    const surfaceSwitch = screen.getByRole("tablist", {
      name: "Workspace surface",
    });
    expect(surfaceSwitch).toBeVisible();
    expect(surfaceSwitch.parentElement).toHaveClass("pl-16");
    expect(screen.queryByRole("button", { name: "关闭窗口" })).toBeNull();
    expect(screen.queryByRole("button", { name: "最小化窗口" })).toBeNull();
    expect(screen.queryByRole("button", { name: "缩放窗口" })).toBeNull();
  });
});
