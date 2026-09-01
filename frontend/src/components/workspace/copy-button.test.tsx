import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { renderWithProviders } from "@/test/harness";

import { CopyButton } from "./copy-button";

const writeTextMock = vi.fn().mockResolvedValue(undefined);

Object.assign(navigator, {
  clipboard: { writeText: writeTextMock },
});

describe("CopyButton", () => {
  beforeEach(() => {
    writeTextMock.mockClear();
  });

  it("renders the copy icon initially", () => {
    renderWithProviders(<CopyButton clipboardData="hello" />);
    const btn = screen.getByRole("button");
    expect(btn).toBeInTheDocument();
  });

  it("copies text to clipboard on click", async () => {
    renderWithProviders(<CopyButton clipboardData="hello world" />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() =>
      expect(writeTextMock).toHaveBeenCalledWith("hello world"),
    );
  });

  it("shows check icon after copy", async () => {
    const { container } = renderWithProviders(<CopyButton clipboardData="x" />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => {
      const svg = container.querySelector("svg");
      expect(svg?.classList.toString()).toContain("text-success");
    });
  });
});
