import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Message } from "@/core/api/types";
import { renderWithProviders } from "@/test/harness";

import { GroundingChip } from "./grounding-chip";

function aiWithGrounding(grounding: unknown): Message {
  return {
    type: "ai",
    content: "answer",
    additional_kwargs: grounding === undefined ? {} : { grounding },
  } as Message;
}

describe("GroundingChip", () => {
  it("renders nothing without grounding sources", () => {
    const { container } = renderWithProviders(
      <GroundingChip message={aiWithGrounding(undefined)} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("names real context, collapsed, then expands the exact sources", async () => {
    const grounding = [
      {
        kind: "doc",
        title: "Hemolymph (Context)",
        path: "23-memory/hemolymph.md",
      },
      {
        kind: "source",
        title: "react_loop.py",
        path: "runtime/react_loop.py:501",
      },
    ];
    renderWithProviders(<GroundingChip message={aiWithGrounding(grounding)} />);

    // Collapsed: one quiet plain-language line, no source paths leaking in.
    const trigger = screen.getByRole("button", {
      name: "Used Hemolymph (Context) and 1 more",
    });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("runtime/react_loop.py:501")).toBeNull();

    // Expand: the exact docs/chunks the agent was grounded on.
    await userEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Hemolymph (Context)")).toBeInTheDocument();
    expect(screen.getByText("23-memory/hemolymph.md")).toBeInTheDocument();
    expect(screen.getByText("react_loop.py")).toBeInTheDocument();
    expect(screen.getByText("runtime/react_loop.py:501")).toBeInTheDocument();
    expect(screen.queryByText("doc")).not.toBeInTheDocument();
    expect(screen.queryByText("code")).not.toBeInTheDocument();
  });

  it("ignores a non-array grounding value", () => {
    const { container } = renderWithProviders(
      <GroundingChip message={aiWithGrounding("not-an-array")} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("does not expose legacy agent-private grounding under another reply", () => {
    const { container } = renderWithProviders(
      <GroundingChip
        message={aiWithGrounding([
          {
            kind: "doc",
            title: "✨ Luna · vibe_selling",
            path: "20-backend/26-agents/vibe_selling.md",
          },
        ])}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
