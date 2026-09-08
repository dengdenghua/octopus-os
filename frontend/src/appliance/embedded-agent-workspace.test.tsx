import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, useNavigate } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { EmbeddedAgentWorkspace } from "./embedded-agent-workspace";

vi.mock("@/app/workspace/workspace-routes", () => ({
  createWorkspaceRoute: () => {
    function Task() {
      const navigate = useNavigate();
      return (
        <button
          onClick={() =>
            navigate("/workspace/realtime/created-thread?agent=coder")
          }
        >
          创建后定位
        </button>
      );
    }
    return <Route path="/workspace/*" element={<Task />} />;
  },
}));

it("reports the actual task route to its desktop host after creating a thread", async () => {
  const onRouteChange = vi.fn();
  render(
    <MemoryRouter initialEntries={["/desktop"]}>
      <EmbeddedAgentWorkspace onRouteChange={onRouteChange} />
    </MemoryRouter>,
  );
  expect(onRouteChange).toHaveBeenCalledWith("/workspace/realtime/new");
  fireEvent.click(screen.getByRole("button", { name: "创建后定位" }));
  await waitFor(() =>
    expect(onRouteChange).toHaveBeenLastCalledWith(
      "/workspace/realtime/created-thread?agent=coder",
    ),
  );
});
