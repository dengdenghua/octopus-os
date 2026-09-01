import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      modes: {
        develop: "编程",
        developDesc: "",
        developEffect: "",
        developTooltip: "",
        audit: "审查",
        auditDesc: "",
        auditEffect: "",
        auditTooltip: "",
        uxui: "界面",
        uxuiDesc: "",
        uxuiEffect: "",
        uxuiTooltip: "",
        manualOverrideShort: "手动",
        standard: "标准",
        ultra: "深度",
      },
    },
    locale: "zh",
    setLocale: () => Promise.resolve(),
  }),
}));

import { ModeSelector } from "./mode-selector";

function mockFetch() {
  return vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/agent-modes/detect")) {
        return new Response(
          JSON.stringify({
            recommended_mode: "coder",
            confidence: 0.9,
            reason: "test",
            signals: {},
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/agent-modes")) {
        return new Response(
          JSON.stringify({
            modes: [{ name: "develop", display_name: "编程" }],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    }),
  );
}

describe("ModeSelector.onManualOverrideChange", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockFetch();
  });

  it("reports true when the user manually switches modes", async () => {
    const onManualOverrideChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ModeSelector
        workDir="/workspace/a"
        sessionId="s1"
        mode="develop"
        onModeChange={() => {}}
        onManualOverrideChange={onManualOverrideChange}
      />,
    );

    // Open the popup and pick the audit option.
    await user.click(screen.getByRole("button", { haspopup: "listbox" }));
    const options = await screen.findAllByRole("option");
    const audit = options.find((o) => o.textContent?.includes("审查"));
    expect(audit).toBeTruthy();
    await user.click(audit!);

    expect(onManualOverrideChange).toHaveBeenCalledWith(true);
  });

  it("restores a persisted mode and audit intensity on mount", () => {
    window.localStorage.setItem(
      "echo:modeOverride",
      JSON.stringify({
        "/workspace/a": { mode: "audit", auditIntensity: "max" },
      }),
    );
    const onModeChange = vi.fn();
    const onAuditIntensityChange = vi.fn();
    render(
      <ModeSelector
        workDir="/workspace/a"
        sessionId="s1"
        mode="develop"
        auditIntensity="standard"
        onModeChange={onModeChange}
        onAuditIntensityChange={onAuditIntensityChange}
      />,
    );

    // The persisted choice (audit + 最高) is reapplied after refresh instead
    // of snapping back to the default develop/标准.
    expect(onModeChange).toHaveBeenCalledWith("audit");
    expect(onAuditIntensityChange).toHaveBeenCalledWith("max");
  });
});
