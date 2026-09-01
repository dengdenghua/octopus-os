import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as desktopReturn from "@/core/navigation/desktop-return";
import * as bridge from "./embedded-window-bridge";
import { MacWindowControls } from "./mac-window-controls";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("MacWindowControls", () => {
  it("does not duplicate the Echo OS traffic lights when embedded", () => {
    vi.spyOn(bridge, "isEmbeddedWindow").mockReturnValue(true);

    render(<MacWindowControls />);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("uses close and minimize to return a standalone Agent to Echo OS", async () => {
    const user = userEvent.setup();
    vi.spyOn(bridge, "isEmbeddedWindow").mockReturnValue(false);
    const navigate = vi
      .spyOn(desktopReturn, "navigateToEchoOsDesktop")
      .mockImplementation(() => undefined);

    render(<MacWindowControls />);
    await user.click(screen.getByRole("button", { name: "关闭窗口" }));
    await user.click(screen.getByRole("button", { name: "最小化窗口" }));

    expect(navigate).toHaveBeenCalledTimes(2);
    expect(screen.getAllByRole("button")).toHaveLength(3);
  });

  it("uses the zoom control to enter fullscreen in a standalone Agent", async () => {
    const user = userEvent.setup();
    vi.spyOn(bridge, "isEmbeddedWindow").mockReturnValue(false);
    const requestFullscreen = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(document.documentElement, "requestFullscreen", {
      configurable: true,
      value: requestFullscreen,
    });

    try {
      render(<MacWindowControls />);
      await user.click(screen.getByRole("button", { name: "缩放窗口" }));

      expect(requestFullscreen).toHaveBeenCalledTimes(1);
    } finally {
      delete (document.documentElement as Partial<HTMLElement>)
        .requestFullscreen;
    }
  });
});
