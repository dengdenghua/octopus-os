import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";

vi.mock("@/core/agents/active", () => ({
  useActiveAgentId: () => "general",
}));
import {
  canOpenWorkbenchAsset,
  workbenchAppForAsset,
} from "./workbench-app-placement";
import type { AgentHubAsset } from "./agent-assets";
import { WorkbenchAppPlacement } from "./workbench-app-placement";

it("resolves only registered workbench identities", () => {
  expect(
    workbenchAppForAsset({
      kind: "workbench",
      id: "workbench:workbench_design",
      installId: "design",
    })?.workspaceRoute,
  ).toBe("/workspace/design");
  expect(
    workbenchAppForAsset({
      kind: "plugin",
      id: "workbench:workbench_design",
      installId: "design",
    }),
  ).toBeUndefined();
  expect(
    workbenchAppForAsset({
      kind: "workbench",
      id: "https://external.invalid",
      installId: "unknown",
    }),
  ).toBeUndefined();
});

it.each([
  { installed: false },
  { enabled: false },
  { permissionReviewRequired: true },
  { lifecycleState: "broken" },
  { compatibility: "incompatible" },
])(
  "does not offer launch or placement for an unavailable app: %j",
  (override) => {
    const asset = {
      installed: true,
      enabled: true,
      permissionReviewRequired: false,
      lifecycleState: "enabled",
      compatibility: "compatible",
      ...override,
    } as AgentHubAsset;
    expect(canOpenWorkbenchAsset(asset)).toBe(false);
  },
);

it("keeps the workbench launch available for an enabled installed app", async () => {
  const onOpenWorkbench = vi.fn();
  render(
    <MemoryRouter>
      <WorkbenchAppPlacement
        asset={
          {
            kind: "workbench",
            id: "workbench:workbench_design",
            installId: "design",
            installed: true,
            enabled: true,
            permissionReviewRequired: false,
            lifecycleState: "enabled",
            compatibility: "compatible",
          } as AgentHubAsset
        }
        onOpenWorkbench={onOpenWorkbench}
      />
    </MemoryRouter>,
  );
  await userEvent.click(screen.getByRole("button", { name: "在工作台中打开" }));
  expect(onOpenWorkbench).toHaveBeenCalledWith("/workspace/design");
});
