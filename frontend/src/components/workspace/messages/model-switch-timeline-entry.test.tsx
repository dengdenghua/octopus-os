import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ModelSwitchTimelineEntry } from "./model-switch-timeline-entry";

describe("ModelSwitchTimelineEntry", () => {
  it("renders a quiet centered model-change status", () => {
    render(<ModelSwitchTimelineEntry modelName="gpt-5.6-sol" />);

    const entry = screen.getByRole("status", {
      name: "模型已切换为 gpt-5.6-sol",
    });
    expect(entry).toHaveTextContent("模型已切换为 gpt-5.6-sol");
    expect(entry.querySelectorAll('[aria-hidden="true"]')).toHaveLength(2);
  });
});
