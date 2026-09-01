import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PersonaWorkbenchHome } from "./persona-workbench-home";

describe("PersonaWorkbenchHome", () => {
  it("renders the trading workbench and its real app entry", () => {
    render(
      <MemoryRouter>
        <PersonaWorkbenchHome personaId="market_researcher" />
      </MemoryRouter>,
    );

    expect(screen.getByText("交易研究台")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /打开模拟交易/ })).toHaveAttribute(
      "href",
      "/workspace/paper-trading",
    );
  });

  it("renders the media production lanes and video library entry", () => {
    render(
      <MemoryRouter>
        <PersonaWorkbenchHome personaId="aoi" />
      </MemoryRouter>,
    );

    expect(screen.getByText("AI 影视工作台")).toBeInTheDocument();
    expect(screen.getByText("素材")).toBeInTheDocument();
    expect(screen.getByText("制作")).toBeInTheDocument();
    expect(screen.getByText("交付")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /打开视频素材库/ }),
    ).toHaveAttribute(
      "href",
      "/workspace/storage?surface=company&library=videos",
    );
  });
});
