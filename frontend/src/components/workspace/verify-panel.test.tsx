import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import {
  VerifyPanel,
  type AutoVerifySummary,
  type VerifyResult,
} from "./verify-panel";

describe("<VerifyPanel /> auto verification", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders an injected auto verification failure snapshot", () => {
    const result: VerifyResult = {
      kind: "node-ts",
      passed: false,
      results: [
        {
          name: "typecheck",
          command: "npm run typecheck",
          passed: false,
          exit_code: 2,
          stdout: "",
          stderr: "Type error",
          duration_ms: 1250,
        },
      ],
    };
    const autoSummary: AutoVerifySummary = {
      attempt: 2,
      retryCount: 2,
      maxRetries: 2,
      autoFixQueued: false,
      exhausted: true,
    };

    renderWithProviders(
      <VerifyPanel
        workDir="F:/echo-agent"
        initialResult={result}
        autoSummary={autoSummary}
      />,
    );

    expect(screen.getByText("Auto verify attempt 2")).toBeInTheDocument();
    expect(
      screen.getByText("Attempt 2. Auto-fix limit reached (2/2)"),
    ).toBeInTheDocument();
    expect(screen.getByText("typecheck")).toBeInTheDocument();
    expect(screen.getByText("0/1")).toBeInTheDocument();
  });

  it("shows pending files awaiting verification", () => {
    renderWithProviders(
      <VerifyPanel
        workDir="F:/echo-agent"
        pendingFiles={["src/App.tsx", "src/App.css"]}
      />,
    );

    expect(screen.getByText("Changes awaiting verify")).toBeInTheDocument();
    expect(screen.getByText("src/App.tsx")).toBeInTheDocument();
    expect(screen.getByText("src/App.css")).toBeInTheDocument();
  });

  it("normalizes manual run results and reports them to the parent", async () => {
    const onResult = vi.fn();
    const payload: VerifyResult = {
      kind: "node",
      passed: true,
      results: [
        {
          name: "typecheck",
          command: "npm run typecheck",
          passed: true,
          exit_code: 0,
          stdout: "ok",
          stderr: "",
          duration_ms: 42,
        },
      ],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => payload,
    } as Response);

    renderWithProviders(
      <VerifyPanel
        workDir="F:/echo-agent"
        pendingFiles={["src/App.tsx"]}
        browserRegressionEnabled
        browserRegressionPreviewUrl="http://localhost:3000/preview"
        onResult={onResult}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Run checks" }));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(payload));
    const request = vi.mocked(globalThis.fetch).mock.calls[0];
    expect(JSON.parse(String(request[1]?.body))).toMatchObject({
      workspace: "F:/echo-agent",
      browser_regression_enabled: true,
      browser_regression_mode: "human_cursor",
      browser_regression_preview_url: "http://localhost:3000/preview",
      browser_regression_requires_visible_cursor: true,
    });
    expect(screen.getByText("1/1")).toBeInTheDocument();
  });
});
