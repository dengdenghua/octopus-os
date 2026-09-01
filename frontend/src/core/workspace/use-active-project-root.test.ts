import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  activateProjectRoot,
  useActiveProjectRoot,
} from "./use-active-project-root";

describe("active project root", () => {
  afterEach(() => {
    window.localStorage.removeItem("echo:recentWorkdirs");
    window.history.replaceState(null, "", "/");
    window.location.hash = "";
  });

  it("reads workspace_path from a hash-router URL", () => {
    window.location.hash =
      "#/workspace/knowledge?surface=chat&workspace_path=%2Fprojects%2Falpha";

    const { result } = renderHook(() => useActiveProjectRoot());

    expect(result.current).toBe("/projects/alpha");
  });

  it("activates and remembers a project for every project-aware surface", () => {
    const { result } = renderHook(() => useActiveProjectRoot());

    act(() => activateProjectRoot("/projects/beta"));

    expect(result.current).toBe("/projects/beta");
    expect(
      JSON.parse(localStorage.getItem("echo:recentWorkdirs") ?? "[]"),
    ).toEqual(["/projects/beta"]);
  });
});
