import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";
import { ClarificationChoiceCard } from "./clarification-choice-card";

// The structured result shape emitted by the `ask_user_question` skill.
const ASK_USER_RESULT = JSON.stringify({
  ok: true,
  posted: true,
  question: "Which output format do you prefer?",
  options: ["Short answer", "Detailed report"],
  allow_other: true,
});

function listenForQuickReply() {
  const quickReply = vi.fn();
  const handler = (event: Event) => {
    if (event.type === "echo:quick-reply") {
      quickReply((event as CustomEvent<{ text?: string }>).detail);
    }
  };
  window.addEventListener("echo:quick-reply", handler);
  return {
    quickReply,
    cleanup: () => window.removeEventListener("echo:quick-reply", handler),
  };
}

describe("ClarificationChoiceCard · clarification affordances", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  test("renders an Other free-text input for a structured single question", () => {
    renderWithProviders(
      <ClarificationChoiceCard content={ASK_USER_RESULT} active messageId="m1" />,
      { locale: "en-US" },
    );
    expect(screen.getByPlaceholderText(/Other/i)).toBeInTheDocument();
  });

  test("submits a custom Other answer on Enter", () => {
    const { quickReply, cleanup } = listenForQuickReply();
    try {
      renderWithProviders(
        <ClarificationChoiceCard content={ASK_USER_RESULT} active messageId="m1" />,
        { locale: "en-US" },
      );
      const input = screen.getByPlaceholderText(/Other/i);
      fireEvent.change(input, { target: { value: "A one-page summary" } });
      fireEvent.keyDown(input, { key: "Enter" });
      expect(quickReply).toHaveBeenCalledWith(
        expect.objectContaining({ text: "A one-page summary" }),
      );
    } finally {
      cleanup();
    }
  });

  test("auto-submits the recommended option after the timeout", () => {
    vi.useFakeTimers();
    const { quickReply, cleanup } = listenForQuickReply();
    try {
      renderWithProviders(
        <ClarificationChoiceCard content={ASK_USER_RESULT} active messageId="m1" />,
        { locale: "en-US" },
      );
      act(() => {
        vi.advanceTimersByTime(20000);
      });
      expect(quickReply).toHaveBeenCalledTimes(1);
      expect(quickReply).toHaveBeenCalledWith(
        expect.objectContaining({
          text: expect.stringContaining("Short answer"),
        }),
      );
    } finally {
      cleanup();
    }
  });

  test("renders an Other free-text input for the plain-text fallback card", () => {
    renderWithProviders(
      <ClarificationChoiceCard
        content={"请选择一个方向：\n\nA. 方案甲\nB. 方案乙"}
        active
        messageId="m1"
      />,
      { locale: "en-US" },
    );
    expect(screen.getByPlaceholderText(/Other/i)).toBeInTheDocument();
  });
});
