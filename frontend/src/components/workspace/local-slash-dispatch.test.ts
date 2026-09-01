import { describe, expect, test, vi } from "vitest";

import {
  __test,
  tryLocalSlash,
  type LocalSlashContext,
} from "./local-slash-dispatch";

describe("parseSlash", () => {
  const { parseSlash } = __test;

  test("returns null for non-slash input", () => {
    expect(parseSlash("hello")).toBeNull();
    expect(parseSlash("")).toBeNull();
    expect(parseSlash("  ")).toBeNull();
  });

  test("rejects path-like inputs", () => {
    // A user pasting a path should reach the LLM, not be misread as a command.
    expect(parseSlash("/usr/local/bin")).toBeNull();
    expect(parseSlash("/home/me/file.txt")).toBeNull();
  });

  test("parses bare commands and lowercases", () => {
    expect(parseSlash("/clear")).toEqual({ cmd: "clear", arg: "" });
    expect(parseSlash("  /CLEAR  ")).toEqual({ cmd: "clear", arg: "" });
  });

  test("splits args on first space", () => {
    expect(parseSlash("/mode plan")).toEqual({ cmd: "mode", arg: "plan" });
    expect(parseSlash("/model gpt-4o-mini")).toEqual({
      cmd: "model",
      arg: "gpt-4o-mini",
    });
  });
});

describe("tryLocalSlash", () => {
  test("returns false on plain prose so it falls through to LLM", () => {
    const ctx: LocalSlashContext = { onClear: vi.fn() };
    expect(tryLocalSlash("how do I sort this list?", ctx)).toBe(false);
    expect(ctx.onClear).not.toHaveBeenCalled();
  });

  test("/clear dispatches when wired and consumes the input", () => {
    const onClear = vi.fn();
    expect(tryLocalSlash("/clear", { onClear })).toBe(true);
    expect(onClear).toHaveBeenCalledOnce();
  });

  test("/clear without callback falls through (don't silently swallow)", () => {
    expect(tryLocalSlash("/clear", {})).toBe(false);
  });

  test("/mode plan switches reasoning mode", () => {
    const onModeChange = vi.fn();
    expect(tryLocalSlash("/mode plan", { onModeChange })).toBe(false);
    // "plan" is not a ReasoningMode — falls through. Compose with /permission instead.
    expect(onModeChange).not.toHaveBeenCalled();

    expect(tryLocalSlash("/mode react", { onModeChange })).toBe(true);
    expect(onModeChange).toHaveBeenCalledWith("react");
  });

  test("/mode code is a valid project/code reasoning mode", () => {
    const onModeChange = vi.fn();
    expect(tryLocalSlash("/mode code", { onModeChange })).toBe(true);
    expect(onModeChange).toHaveBeenCalledWith("code");
  });

  test("/mode rejects unknown values rather than guessing", () => {
    const onModeChange = vi.fn();
    expect(tryLocalSlash("/mode bogus", { onModeChange })).toBe(false);
    expect(onModeChange).not.toHaveBeenCalled();
  });

  test("/model passes the raw arg through", () => {
    const onModelChange = vi.fn();
    expect(tryLocalSlash("/model claude-sonnet-4-6", { onModelChange })).toBe(
      true,
    );
    expect(onModelChange).toHaveBeenCalledWith("claude-sonnet-4-6");
  });

  test("/model with no arg falls through (no model name to apply)", () => {
    const onModelChange = vi.fn();
    expect(tryLocalSlash("/model", { onModelChange })).toBe(false);
    expect(onModelChange).not.toHaveBeenCalled();
  });

  test("/permission and /perm map to onPermissionModeChange", () => {
    const onPermissionModeChange = vi.fn();
    expect(
      tryLocalSlash("/permission default", { onPermissionModeChange }),
    ).toBe(true);
    expect(onPermissionModeChange).toHaveBeenLastCalledWith("default");

    expect(
      tryLocalSlash("/perm bypassPermissions", { onPermissionModeChange }),
    ).toBe(true);
    expect(onPermissionModeChange).toHaveBeenLastCalledWith(
      "bypassPermissions",
    );

    expect(tryLocalSlash("/permission plan", { onPermissionModeChange })).toBe(
      true,
    );
    expect(onPermissionModeChange).toHaveBeenLastCalledWith("plan");
  });

  test("/permission accepts legacy sandbox/full aliases", () => {
    const onPermissionModeChange = vi.fn();
    expect(
      tryLocalSlash("/permission sandbox", { onPermissionModeChange }),
    ).toBe(true);
    expect(onPermissionModeChange).toHaveBeenLastCalledWith("default");

    expect(tryLocalSlash("/perm full", { onPermissionModeChange })).toBe(true);
    expect(onPermissionModeChange).toHaveBeenLastCalledWith(
      "bypassPermissions",
    );
  });

  test("panel aliases dispatch to onSwitchPanel", () => {
    const onSwitchPanel = vi.fn();
    expect(tryLocalSlash("/wiki", { onSwitchPanel })).toBe(true);
    expect(onSwitchPanel).toHaveBeenLastCalledWith("wiki");

    // record/replay both land on the teach-repeat panel.
    expect(tryLocalSlash("/record", { onSwitchPanel })).toBe(true);
    expect(onSwitchPanel).toHaveBeenLastCalledWith("teach-repeat");
    expect(tryLocalSlash("/replay", { onSwitchPanel })).toBe(true);
    expect(onSwitchPanel).toHaveBeenLastCalledWith("teach-repeat");
  });

  test("/skills, /pack, /meta open the skill catalog inside Hub", () => {
    // window.location.hash is the navigation channel — verify it
    // changes for both arg-less and arg forms. JSDOM gives us a
    // working location object.
    const original = window.location.hash;
    try {
      expect(tryLocalSlash("/skills", {})).toBe(true);
      expect(window.location.hash).toBe(
        "#/workspace/agents?surface=chat&tab=skills",
      );
      expect(tryLocalSlash("/pack bug-hunt", {})).toBe(true);
      expect(window.location.hash).toBe(
        "#/workspace/agents?surface=chat&tab=skills&q=bug-hunt",
      );
      expect(tryLocalSlash("/meta security audit", {})).toBe(true);
      // Spaces in args are URL-encoded — picker hands the rest of
      // the line through verbatim, the tab reads it back.
      expect(window.location.hash).toBe(
        "#/workspace/agents?surface=chat&tab=skills&q=security%20audit",
      );
    } finally {
      window.location.hash = original;
    }
  });

  test("/settings opens the unified settings dialog", () => {
    const openSettings = vi.fn();
    window.addEventListener("echo:open-settings", openSettings);
    try {
      expect(tryLocalSlash("/settings", {})).toBe(true);
      expect(openSettings).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("echo:open-settings", openSettings);
    }
  });

  test("unknown command falls through (server-defined commands stay LLM-bound)", () => {
    // /commit, /review, /test etc. are still server-driven prompts;
    // the dispatcher must not pretend to handle them.
    expect(tryLocalSlash("/commit", { onSwitchPanel: vi.fn() })).toBe(false);
    expect(tryLocalSlash("/review", { onSwitchPanel: vi.fn() })).toBe(false);
    expect(tryLocalSlash("/customcmd arg", {})).toBe(false);
  });

  test("/personality requires a preset name", () => {
    const onPersonalityApply = vi.fn();
    expect(tryLocalSlash("/personality", { onPersonalityApply })).toBe(false);
    expect(tryLocalSlash("/personality pirate", { onPersonalityApply })).toBe(
      true,
    );
    expect(onPersonalityApply).toHaveBeenCalledWith("pirate");
  });
});
