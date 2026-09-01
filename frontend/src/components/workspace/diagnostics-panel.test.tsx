import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { DiagnosticsPanel } from "./diagnostics-panel";
import type { PreviewDiagnostic } from "./live-preview-panel";

const SESSION_INFO = {
  thread_id: "thread-1",
  workspace_path: "F:/echo-agent",
  workspace_exists: true,
  workspace_resolved: "F:/echo-agent",
  project: { kind: "node", checks: ["typecheck"] },
  rules_file: null,
  git_initialized: false,
  thread_metadata: null,
  write_scope: null,
  server_cwd: "F:/echo-agent",
  python_executable: "python",
};

describe("<DiagnosticsPanel /> preview diagnostics", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders preview console diagnostics with session info", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => SESSION_INFO,
    } as Response);
    const diagnostics: PreviewDiagnostic[] = [
      {
        id: "d1",
        level: "error",
        source: "console",
        message: "ReferenceError: app is not defined",
        timestamp: 1_700_000_000_000,
      },
    ];

    renderWithProviders(
      <DiagnosticsPanel
        threadId="thread-1"
        workDir="F:/echo-agent"
        previewDiagnostics={diagnostics}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText("Workspace")).toBeInTheDocument(),
    );
    expect(screen.getByText("Preview")).toBeInTheDocument();
    expect(screen.getByText("console")).toBeInTheDocument();
    expect(
      screen.getByText("ReferenceError: app is not defined"),
    ).toBeInTheDocument();
  });
});
