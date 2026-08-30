import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import {
  WORKBENCH_BUILTIN_APPS,
  resetWorkspaceWebShortcutCache,
  setWorkspaceWebShortcut,
  useWorkspaceWebShortcuts,
  workspaceWebAppRoute,
} from "./apps";

describe("built-in workbench apps", () => {
  it("ships Narrative Studio as a removable runtime plugin application", () => {
    expect(
      WORKBENCH_BUILTIN_APPS.find((app) => app.id === "narrative"),
    ).toEqual({
      id: "narrative",
      moduleId: "narrative",
      name: "叙事工坊",
      description: "角色、世界观、剧情分支与正典协作",
      workspaceRoute: "/workspace/narrative",
      launchUrl: "echo://workspace/narrative",
      icon: "narrative",
      delivery: "remote",
      cloudId: "workbench_narrative",
      packageId: "narrative_studio",
      runtimePlugin: "narrative_studio",
    });
  });

  it("keeps the narrative icon and native browser mount wired", () => {
    const browserHome = readFileSync(
      resolve("src/components/browser/browser-home.tsx"),
      "utf8",
    );
    const browserTab = readFileSync(
      resolve("src/components/browser/webview-tab.tsx"),
      "utf8",
    );
    const sidebar = readFileSync(
      resolve("src/components/workspace/workspace-sidebar.tsx"),
      "utf8",
    );
    const hub = readFileSync(
      resolve("src/components/workspace/agents/agent-world-unified.tsx"),
      "utf8",
    );

    expect(browserHome).toContain("narrative: BookOpenIcon");
    expect(sidebar).toContain("narrative: BookOpenIcon");
    expect(hub).toContain("narrative: BookOpenIcon");
    expect(browserTab).toContain("<RemoteWorkbenchSurface");
    expect(browserTab).not.toContain(
      'import("@/app/workspace/narrative/page")',
    );
  });
});

describe("workspace web shortcuts", () => {
  beforeEach(() => {
    localStorage.clear();
    resetWorkspaceWebShortcutCache();
  });

  it("pins, updates and removes a browser app for the workspace sidebar", () => {
    const { result } = renderHook(() => useWorkspaceWebShortcuts());

    act(() => {
      setWorkspaceWebShortcut(
        { name: "ChatGPT", url: "https://chatgpt.com/" },
        true,
      );
    });
    expect(result.current).toEqual([
      {
        id: "web:https://chatgpt.com/",
        name: "ChatGPT",
        url: "https://chatgpt.com/",
      },
    ]);

    act(() => {
      setWorkspaceWebShortcut(
        { name: "GPT", url: "https://chatgpt.com/" },
        true,
      );
    });
    expect(result.current).toHaveLength(1);
    expect(result.current[0]?.name).toBe("GPT");

    act(() => {
      setWorkspaceWebShortcut(
        { name: "GPT", url: "https://chatgpt.com/" },
        false,
      );
    });
    expect(result.current).toEqual([]);
  });

  it("rejects unsafe non-web targets", () => {
    const { result } = renderHook(() => useWorkspaceWebShortcuts());
    act(() => {
      setWorkspaceWebShortcut(
        { name: "Bad", url: "javascript:alert(1)" },
        true,
      );
    });
    expect(result.current).toEqual([]);
  });

  it("builds a workspace-local embedded app route", () => {
    expect(
      workspaceWebAppRoute({
        name: "ChatGPT",
        url: "https://chatgpt.com/?model=gpt-5",
      }),
    ).toBe(
      "/workspace/web-app?url=https%3A%2F%2Fchatgpt.com%2F%3Fmodel%3Dgpt-5&title=ChatGPT",
    );
  });
});
