import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderWithProviders } from "@/test/harness";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

import { AgentMessageHeader } from "./agent-message-header";

describe("AgentMessageHeader", () => {
  it("renders agent display name", () => {
    renderWithProviders(<AgentMessageHeader agentDisplayName="Coder" />);
    expect(screen.getByText("Coder")).toBeInTheDocument();
  });

  it("renders fallback initial when no avatar", () => {
    renderWithProviders(<AgentMessageHeader agentDisplayName="Coder" />);
    expect(screen.getByText("C")).toBeInTheDocument();
  });

  it("renders emoji icon before initial when provided", () => {
    renderWithProviders(
      <AgentMessageHeader agentDisplayName="Coder" icon="💻" />,
    );
    expect(screen.getByText("💻")).toBeInTheDocument();
  });

  it("renders avatar image when avatarUrl is provided", () => {
    renderWithProviders(
      <AgentMessageHeader
        agentDisplayName="Coder"
        avatarUrl="/api/agents/coder/avatar"
      />,
    );
    const img = screen.getByRole("img", { name: "Coder" });
    expect(img).toBeInTheDocument();
    expect(img.getAttribute("src")).toBe(
      "http://localhost:8001/api/agents/coder/avatar",
    );
  });

  it("renders TL badge when role is tl", () => {
    renderWithProviders(
      <AgentMessageHeader agentDisplayName="Lead" role="tl" />,
    );
    expect(screen.getByText("TL")).toBeInTheDocument();
  });

  it("does not render TL badge when role is member", () => {
    renderWithProviders(
      <AgentMessageHeader agentDisplayName="Worker" role="member" />,
    );
    expect(screen.queryByText("TL")).not.toBeInTheDocument();
  });

  it("does not render TL badge when role is undefined", () => {
    renderWithProviders(<AgentMessageHeader agentDisplayName="Worker" />);
    expect(screen.queryByText("TL")).not.toBeInTheDocument();
  });
});
