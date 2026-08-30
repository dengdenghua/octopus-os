import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import type { StreamVitals } from "@/core/realtime";
import { renderWithProviders } from "@/test/harness";

import { RunDurationBadge } from "./run-duration-badge";

function vitals(partial: Partial<StreamVitals> = {}): StreamVitals {
  return {
    phase: "working",
    ttftMs: null,
    lastDeltaAgeMs: 0,
    sinceActivityMs: 0,
    elapsedMs: 137_000,
    maxDeltaGapMs: 0,
    stalled: false,
    ...partial,
  };
}

describe("RunDurationBadge", () => {
  test("places the total run duration in the header status", () => {
    renderWithProviders(<RunDurationBadge isLoading vitals={vitals()} />, {
      locale: "zh-CN",
    });

    expect(screen.getByTestId("run-duration-badge")).toHaveTextContent(
      "正在处理",
    );
    expect(screen.getByTestId("run-duration-badge")).toHaveTextContent(
      "2m 17s",
    );
  });

  test("treats the optimistic pre-receipt window as waiting", () => {
    renderWithProviders(
      <RunDurationBadge
        isLoading
        vitals={vitals({ phase: "idle", elapsedMs: 0 })}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByTestId("run-duration-badge")).toHaveTextContent(
      "思考中",
    );
  });

  test("escalates an unusually long first-response wait without calling it disconnected", () => {
    renderWithProviders(
      <RunDurationBadge
        isLoading
        vitals={vitals({
          phase: "waiting",
          elapsedMs: 104_500,
          stalled: false,
        })}
      />,
      { locale: "zh-CN" },
    );

    const badge = screen.getByTestId("run-duration-badge");
    expect(badge).toHaveTextContent("首个响应较慢，任务仍在等待");
    expect(badge).toHaveTextContent("1m 44s");
    expect(badge).toHaveAttribute("data-first-response-delayed", "true");
    expect(badge.className).toContain("text-warning");
  });

  test("shows the time-to-first-token once the first token arrived", () => {
    renderWithProviders(
      <RunDurationBadge isLoading vitals={vitals({ ttftMs: 1240 })} />,
      { locale: "zh-CN" },
    );

    expect(screen.getByTestId("ttft-badge")).toHaveTextContent("首字 1.2s");
  });

  test("hides the ttft badge while still waiting for the first token", () => {
    renderWithProviders(
      <RunDurationBadge isLoading vitals={vitals({ ttftMs: null })} />,
      { locale: "zh-CN" },
    );

    expect(screen.queryByTestId("ttft-badge")).not.toBeInTheDocument();
  });

  test("shows the time-to-first-token once the first token arrived", () => {
    renderWithProviders(
      <RunDurationBadge isLoading vitals={vitals({ ttftMs: 1240 })} />,
      { locale: "zh-CN" },
    );

    expect(screen.getByTestId("ttft-badge")).toHaveTextContent("首字 1.2s");
  });

  test("hides the ttft badge while still waiting for the first token", () => {
    renderWithProviders(
      <RunDurationBadge isLoading vitals={vitals({ ttftMs: null })} />,
      { locale: "zh-CN" },
    );

    expect(screen.queryByTestId("ttft-badge")).not.toBeInTheDocument();
  });

  test("does not render after the run settles", () => {
    renderWithProviders(
      <RunDurationBadge isLoading={false} vitals={vitals()} />,
    );
    expect(screen.queryByTestId("run-duration-badge")).not.toBeInTheDocument();
  });
});
