import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { WorkstationSeat } from "./workstation-seat";

describe("WorkstationSeat", () => {
  it("renders the name", () => {
    render(<WorkstationSeat name="coder" />);
    expect(screen.getByText("coder")).toBeInTheDocument();
  });

  it("is a button that fires onClick when clickable", () => {
    const onClick = vi.fn();
    render(
      <WorkstationSeat name="coder" onClick={onClick} ariaLabel="@coder" />,
    );
    const seat = screen.getByRole("button", { name: "@coder" });
    fireEvent.click(seat);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("renders as a non-interactive element without onClick", () => {
    render(<WorkstationSeat name="research" />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders an image avatar with the name as alt text", () => {
    render(
      <WorkstationSeat name="coder" avatarUrl="/api/agents/coder/avatar" />,
    );
    const img = screen.getByRole("img", { name: "coder" });
    expect(img).toHaveAttribute("src", "/api/agents/coder/avatar");
  });

  it("falls back to the emoji when an image avatar fails to load", () => {
    render(
      <WorkstationSeat
        name="coder"
        avatar="🤖"
        avatarUrl="/api/agents/coder/avatar"
      />,
    );
    fireEvent.error(screen.getByRole("img", { name: "coder" }));
    expect(screen.queryByRole("img", { name: "coder" })).toBeNull();
    expect(screen.getByText("🤖")).toBeInTheDocument();
  });

  it("falls back to an uppercased initial when no avatar is given", () => {
    render(<WorkstationSeat name="lin" fallbackInitial="l" />);
    expect(screen.getByText("L")).toBeInTheDocument();
  });

  it("renders a badge and an accessible status dot", () => {
    render(
      <WorkstationSeat
        name="coder"
        badge={<span>队长</span>}
        dotClassName="bg-success"
        dotLabel="在线"
      />,
    );
    expect(screen.getByText("队长")).toBeInTheDocument();
    expect(screen.getByLabelText("在线")).toBeInTheDocument();
  });
});
