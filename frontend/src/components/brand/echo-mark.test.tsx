import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EchoMark } from "./echo-mark";

describe("EchoMark", () => {
  it("renders the canonical ring-and-signal geometry", () => {
    const { container } = render(<EchoMark title="Echo" />);

    expect(screen.getByRole("img", { name: "Echo" })).toBeVisible();
    expect(container.querySelector("path")).toHaveAttribute(
      "d",
      "M45.25 15.9A21.5 21.5 0 1 0 45.25 48.1",
    );
    expect(container.querySelector("circle")).toHaveAttribute("cx", "51.5");
  });

  it("supports a current-color monochrome treatment", () => {
    const { container } = render(<EchoMark tone="current" />);

    expect(container.querySelector("linearGradient")).toBeNull();
    expect(container.querySelector("path")).toHaveAttribute(
      "stroke",
      "currentColor",
    );
  });
});
