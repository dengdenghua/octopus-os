import { act, render, screen } from "@testing-library/react";
import { useState } from "react";
import { beforeAll, describe, expect, test, vi } from "vitest";

import "../../../../extensions/echo-browser-relay/dom-actions.js";

interface DomActionApi {
  run(action: string, params?: Record<string, unknown>): Promise<Record<string, unknown>>;
}

function domActions(): DomActionApi {
  return (
    globalThis as typeof globalThis & {
      __ECHO_DOM_ACTIONS__: DomActionApi;
    }
  ).__ECHO_DOM_ACTIONS__;
}

beforeAll(() => {
  Object.defineProperty(globalThis, "CSS", {
    configurable: true,
    value: { escape: (value: string) => value.replace(/[^a-zA-Z0-9_-]/g, "\\$&") },
  });
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
    () =>
      ({
        x: 0,
        y: 0,
        top: 0,
        left: 0,
        right: 120,
        bottom: 24,
        width: 120,
        height: 24,
        toJSON: () => ({}),
      }) as DOMRect,
  );
});

describe("Chrome relay DOM actions", () => {
  test("updates an actual React controlled input", async () => {
    function ControlledInput() {
      const [value, setValue] = useState("");
      return (
        <>
          <input
            id="react-query"
            aria-label="Query"
            value={value}
            onChange={(event) => setValue(event.currentTarget.value)}
          />
          <output data-testid="mirror">{value}</output>
        </>
      );
    }
    render(<ControlledInput />);

    await act(async () => {
      await domActions().run("type", {
        selector: "#react-query",
        text: "echo",
        clear: true,
      });
    });

    expect(screen.getByLabelText("Query")).toHaveValue("echo");
    expect(screen.getByTestId("mirror")).toHaveTextContent("echo");
  });

  test("press Enter submits a React form when synthetic keys have no native default", async () => {
    function SearchForm() {
      const [submits, setSubmits] = useState(0);
      return (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setSubmits((value) => value + 1);
          }}
        >
          <input id="search-box" aria-label="Search" />
          <output data-testid="submits">{submits}</output>
        </form>
      );
    }
    render(<SearchForm />);

    await act(async () => {
      await domActions().run("press", {
        selector: "#search-box",
        key: "Enter",
      });
    });

    expect(screen.getByTestId("submits")).toHaveTextContent("1");
  });

  test("state exposes verified selectors and never returns password values", async () => {
    render(
      <main>
        <h1>Account</h1>
        <button data-testid="save-button">Save</button>
        <input aria-label="Email" defaultValue="person@example.test" />
        <input aria-label="Password" type="password" defaultValue="secret" />
      </main>,
    );

    const state = await domActions().run("state", { max_items: 10 });
    const buttons = state.buttons as Array<Record<string, unknown>>;
    const inputs = state.inputs as Array<Record<string, unknown>>;

    expect(buttons[0]).toMatchObject({
      name: "Save",
      selector: '[data-testid="save-button"]',
      selectorUnique: true,
    });
    expect(inputs.find((input) => input.name === "Email")?.value).toBe(
      "person@example.test",
    );
    expect(inputs.find((input) => input.name === "Password")?.value).toBeNull();
  });
});
