import { render, screen } from "@testing-library/react";
import { renderHook, act } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ensureDefaultPanels } from "./default-panels";
import { getPanel, registerPanel, resetPanelsForTests } from "./panel-manifest";
import { usePanel, usePanels } from "./use-panels";

function stubPanel(id: string, zone: "workspace" | "settings") {
  return {
    id,
    title: id,
    zone,
    component: () => null,
  };
}

describe("usePanels", () => {
  beforeEach(() => {
    resetPanelsForTests();
    ensureDefaultPanels();
  });

  it("returns the default reference panel", () => {
    const { result } = renderHook(() => usePanels());
    expect(result.current.some((p) => p.id === "workbench.system-status")).toBe(
      true,
    );
  });

  it("reacts to a dynamic registration", () => {
    const { result } = renderHook(() => usePanels());
    const before = result.current.length;
    act(() => {
      registerPanel(stubPanel("workbench.live", "workspace"));
    });
    expect(result.current.length).toBe(before + 1);
  });

  it("filters by zone", () => {
    const { result } = renderHook(() => usePanels({ zone: "settings" }));
    expect(result.current.length).toBe(0); // no settings panels registered
  });

  it("selects one panel by id", () => {
    const { result } = renderHook(() => usePanel("workbench.system-status"));
    expect(result.current?.id).toBe("workbench.system-status");
    const { result: missing } = renderHook(() => usePanel("nope"));
    expect(missing.current).toBeUndefined();
  });
});

describe("reference panel rendering", () => {
  beforeEach(() => {
    resetPanelsForTests();
    ensureDefaultPanels();
  });

  it("renders the registered component with context", () => {
    const panel = getPanel("workbench.system-status")!;
    const Component = panel.component;
    render(
      <Component
        panel={panel}
        context={{ threadId: "t-1", agentId: "coder" }}
      />,
    );
    expect(screen.getByTestId("system-status-panel")).toBeTruthy();
    expect(screen.getByText("thread: t-1")).toBeTruthy();
    expect(screen.getByText("agent: coder")).toBeTruthy();
  });
});
