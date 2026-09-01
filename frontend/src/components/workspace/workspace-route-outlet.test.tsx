import { act, lazy } from "react";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { WorkspaceRouteOutlet } from "./workspace-route-outlet";

describe("WorkspaceRouteOutlet", () => {
  it("shows an explicit pending state while a cold route chunk loads", async () => {
    let resolveColdPage!: (module: {
      default: () => React.JSX.Element;
    }) => void;
    const ColdPage = lazy(
      () =>
        new Promise<{ default: () => React.JSX.Element }>((resolve) => {
          resolveColdPage = resolve;
        }),
    );
    const user = userEvent.setup();

    renderWithProviders(
      <>
        <Link to="/workspace/cold">Open cold route</Link>
        <Routes>
          <Route path="/workspace" element={<WorkspaceRouteOutlet />}>
            <Route path="current" element={<div>Current route content</div>} />
            <Route path="cold" element={<ColdPage />} />
          </Route>
        </Routes>
      </>,
      { initialRoute: "/workspace/current" },
    );

    await user.click(screen.getByRole("link", { name: "Open cold route" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Loading");
    expect(screen.queryByText("Current route content")).not.toBeInTheDocument();

    await act(async () => {
      resolveColdPage({ default: () => <div>Cold route ready</div> });
    });

    expect(await screen.findByText("Cold route ready")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
