import { fireEvent, screen } from "@testing-library/react";
import { useLocation } from "react-router-dom";
import { describe, expect, test } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { useDeferredRouteCommit } from "./use-deferred-route-commit";

function DeferredRouteProbe() {
  const location = useLocation();
  const { stageRoute, commitRoute } = useDeferredRouteCommit();
  return (
    <>
      <output data-testid="route">{location.pathname}</output>
      <button
        type="button"
        onClick={() => stageRoute("/workspace/realtime/thread-1")}
      >
        Stage
      </button>
      <button type="button" onClick={commitRoute}>
        Commit
      </button>
    </>
  );
}

describe("useDeferredRouteCommit", () => {
  test("keeps the live route mounted until the terminal commit", () => {
    renderWithProviders(<DeferredRouteProbe />, {
      initialRoute: "/workspace/realtime/new",
    });

    fireEvent.click(screen.getByRole("button", { name: "Stage" }));
    expect(screen.getByTestId("route")).toHaveTextContent(
      "/workspace/realtime/new",
    );

    fireEvent.click(screen.getByRole("button", { name: "Commit" }));
    expect(screen.getByTestId("route")).toHaveTextContent(
      "/workspace/realtime/thread-1",
    );
  });
});
