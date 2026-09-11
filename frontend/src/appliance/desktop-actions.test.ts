import { describe, expect, it } from "vitest";
import {
  desktopActionHref,
  parseDesktopAction,
  revealFileRequest,
} from "./desktop-actions";

describe("desktop read-only actions", () => {
  it("round trips photo references with their search context", () => {
    const action = {
      type: "photos.reveal" as const,
      path: "旅行/海边 & 日落.jpg",
      query: "海边",
    };
    expect(
      parseDesktopAction(desktopActionHref(action).split("?")[1]!),
    ).toEqual(action);
    expect(
      parseDesktopAction("desktopAction=photos.reveal&path=../secret"),
    ).toBeNull();
  });
  it("reveals a file in its parent directory, including a root file", () => {
    expect(revealFileRequest("家庭/照片.jpg")).toEqual({
      path: "家庭",
      selectedPath: "家庭/照片.jpg",
    });
    expect(revealFileRequest("readme.txt")).toEqual({
      path: "",
      selectedPath: "readme.txt",
    });
    for (const path of ["", "dir/", "../secret", "C:\\secret"])
      expect(revealFileRequest(path)).toBeNull();
  });
  it("round trips Chinese queries and special characters", () => {
    const action = { type: "photos.search" as const, query: "旅行 & 海边" };
    expect(
      parseDesktopAction(desktopActionHref(action).split("?")[1]!),
    ).toEqual(action);
  });
  it("supports relative NAS folders and root", () => {
    for (const path of ["", "家庭/旅行"]) {
      const action = { type: "files.open" as const, path };
      expect(
        parseDesktopAction(desktopActionHref(action).split("?")[1]!),
      ).toEqual(action);
    }
  });
  it("rejects host paths, traversal and mutating commands", () => {
    for (const path of [
      "../secret",
      "/etc",
      "D:\\data",
      "a/../b",
      "a\u0000b",
    ]) {
      expect(
        parseDesktopAction(
          new URLSearchParams({ desktopAction: "files.open", path }).toString(),
        ),
      ).toBeNull();
    }
    expect(
      parseDesktopAction("desktopAction=files.delete&path=family"),
    ).toBeNull();
    expect(parseDesktopAction("desktopAction=photos.search&query=")).toBeNull();
  });
});
