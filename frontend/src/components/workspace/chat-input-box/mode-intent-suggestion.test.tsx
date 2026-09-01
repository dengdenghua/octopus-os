import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  ModeIntentSuggestion,
  readDismissedModes,
} from "./mode-intent-suggestion";

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      modeIntent: {
        suggestSwitch: (modeLabel: string) =>
          `建议切换到「${modeLabel}」模式？`,
        switch: "切换",
        ignore: "忽略",
      },
    },
    locale: "zh",
    setLocale: () => Promise.resolve(),
  }),
}));

describe("ModeIntentSuggestion", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("renders the suggestion text and both actions", () => {
    render(<ModeIntentSuggestion mode="audit" modeLabel="审查" />);
    expect(screen.getByText("建议切换到「审查」模式？")).toBeInTheDocument();
    expect(screen.getByTestId("mode-intent-accept")).toBeInTheDocument();
    expect(screen.getByTestId("mode-intent-ignore")).toBeInTheDocument();
  });

  it("fires onAccept with the suggested mode", async () => {
    const onAccept = vi.fn();
    const user = userEvent.setup();
    render(
      <ModeIntentSuggestion
        mode="uxui"
        modeLabel="UI"
        onAccept={onAccept}
      />,
    );
    await user.click(screen.getByTestId("mode-intent-accept"));
    expect(onAccept).toHaveBeenCalledWith("uxui");
    // Once accepted, the bar hides.
    expect(
      screen.queryByTestId("mode-intent-suggestion"),
    ).not.toBeInTheDocument();
  });

  it("fires onDismiss and persists the ignore for the session", async () => {
    const onDismiss = vi.fn();
    const user = userEvent.setup();
    const { unmount } = render(
      <ModeIntentSuggestion
        mode="audit"
        modeLabel="审查"
        onDismiss={onDismiss}
      />,
    );
    await user.click(screen.getByTestId("mode-intent-ignore"));
    expect(onDismiss).toHaveBeenCalledWith("audit");
    expect(readDismissedModes()).toContain("audit");

    // Re-mounting the same mode stays hidden within this session.
    unmount();
    render(
      <ModeIntentSuggestion mode="audit" modeLabel="审查" onDismiss={onDismiss} />,
    );
    expect(
      screen.queryByTestId("mode-intent-suggestion"),
    ).not.toBeInTheDocument();
  });

  it("shows a different mode even after another was dismissed", () => {
    // Simulate a prior dismissal of audit only, then render uxui: it shows.
    window.sessionStorage.setItem("echo:modeIntentDismissed", '["audit"]');
    render(<ModeIntentSuggestion mode="uxui" modeLabel="UI" />);
    expect(screen.getByTestId("mode-intent-suggestion")).toBeInTheDocument();
  });
});
