import { act, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { ContextCompressor } from "./context-compressor";

describe("ContextCompressor automatic compaction", () => {
  test("waits for an active stream to settle before auto-compressing", async () => {
    const onCompress = vi.fn().mockResolvedValue(undefined);
    const { rerender } = renderWithProviders(
      <ContextCompressor
        currentTokens={95_000}
        maxTokens={100_000}
        disabled
        onCompress={onCompress}
      />,
    );

    await act(async () => {});
    expect(onCompress).not.toHaveBeenCalled();
    expect(screen.getByRole("button")).toBeDisabled();

    rerender(
      <ContextCompressor
        currentTokens={95_000}
        maxTokens={100_000}
        disabled={false}
        onCompress={onCompress}
      />,
    );
    await act(async () => {});

    expect(onCompress).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button")).toBeEnabled();
  });

  test("does not retry on unrelated renders after a successful trigger", async () => {
    const onCompress = vi.fn().mockResolvedValue(undefined);
    const { rerender } = renderWithProviders(
      <ContextCompressor
        currentTokens={90_000}
        maxTokens={100_000}
        onCompress={onCompress}
      />,
    );
    await act(async () => {});

    rerender(
      <ContextCompressor
        currentTokens={96_000}
        maxTokens={100_000}
        onCompress={onCompress}
      />,
    );
    await act(async () => {});

    expect(onCompress).toHaveBeenCalledTimes(1);
  });
});
