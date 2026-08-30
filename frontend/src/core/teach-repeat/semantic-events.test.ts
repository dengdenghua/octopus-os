import { describe, expect, it } from "vitest";

import { buildSemanticRecordingEvent } from "./semantic-events";

function eventFor(type: string, target: HTMLElement): Event {
  const event = new Event(type, { bubbles: true });
  Object.defineProperty(event, "target", { value: target });
  return event;
}

describe("semantic recording events", () => {
  it("captures a stable semantic click target", () => {
    const button = document.createElement("button");
    button.setAttribute("aria-label", "发布");
    button.textContent = "发布内容";
    document.body.appendChild(button);

    const captured = buildSemanticRecordingEvent(eventFor("click", button));
    expect(captured?.target).toMatchObject({
      tag: "button",
      aria_label: "发布",
      text: "发布内容",
    });
    button.remove();
  });

  it("redacts passwords before they leave the browser", () => {
    const input = document.createElement("input");
    input.type = "password";
    input.value = "do-not-store";
    const captured = buildSemanticRecordingEvent(eventFor("input", input));
    expect(captured?.data).toEqual({
      value: "[REDACTED]",
      value_length: 12,
      sensitive: true,
    });
  });

  it("ignores recorder controls", () => {
    const privateRoot = document.createElement("div");
    privateRoot.dataset.recorderPrivate = "true";
    const button = document.createElement("button");
    privateRoot.appendChild(button);
    expect(buildSemanticRecordingEvent(eventFor("click", button))).toBeNull();
  });
});
