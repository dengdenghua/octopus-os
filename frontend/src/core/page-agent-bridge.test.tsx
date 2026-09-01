import { act, render, screen } from "@testing-library/react";
import { useState } from "react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { installPageAgentBridge } from "./page-agent-bridge";

beforeAll(() => {
  if (!globalThis.CSS) {
    Object.defineProperty(globalThis, "CSS", {
      configurable: true,
      value: {},
    });
  }
  if (!globalThis.CSS.escape) {
    globalThis.CSS.escape = (value: string) =>
      value.replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  }
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
    () =>
      ({
        x: 0,
        y: 0,
        top: 0,
        left: 0,
        right: 240,
        bottom: 40,
        width: 240,
        height: 40,
        toJSON: () => ({}),
      }) as DOMRect,
  );
});

describe("page agent bridge", () => {
  it("updates React controlled inputs through the native value setter", async () => {
    function ControlledInput() {
      const [value, setValue] = useState("");
      return (
        <>
          <input
            aria-label="Email"
            value={value}
            onChange={(event) => setValue(event.currentTarget.value)}
          />
          <output data-testid="mirror">{value}</output>
        </>
      );
    }

    render(<ControlledInput />);
    installPageAgentBridge();

    const field = window
      .__echoPageAgent!.snapshot()
      .fields.find((candidate) => candidate.label === "Email");
    expect(field).toBeDefined();

    await act(async () => {
      await window.__echoPageAgent!.run({
        type: "input",
        id: field!.id,
        text: "person@example.test",
      });
    });

    expect(screen.getByLabelText("Email")).toHaveValue("person@example.test");
    expect(screen.getByTestId("mirror")).toHaveTextContent(
      "person@example.test",
    );
  });
});
