import { describe, expect, it } from "vitest";

import {
  basename,
  isAbsolutePath,
  joinPath,
  normalizePath,
} from "./path-utils";

describe("joinPath", () => {
  it("joins base and relative with forward slash", () => {
    expect(joinPath("/home/user", "file.txt")).toBe("/home/user/file.txt");
  });

  it("strips trailing slashes from base", () => {
    expect(joinPath("/home/user/", "file.txt")).toBe("/home/user/file.txt");
    expect(joinPath("/home/user///", "file.txt")).toBe("/home/user/file.txt");
  });

  it("strips trailing backslashes from base", () => {
    expect(joinPath("C:\\Users\\test\\", "file.txt")).toBe(
      "C:\\Users\\test/file.txt",
    );
  });
});

describe("normalizePath", () => {
  it("converts backslashes to forward slashes", () => {
    expect(normalizePath("C:\\Users\\test\\file.txt")).toBe(
      "C:/Users/test/file.txt",
    );
  });

  it("leaves forward slashes unchanged", () => {
    expect(normalizePath("/home/user/file.txt")).toBe("/home/user/file.txt");
  });

  it("handles mixed separators", () => {
    expect(normalizePath("C:\\Users/test\\file.txt")).toBe(
      "C:/Users/test/file.txt",
    );
  });
});

describe("isAbsolutePath", () => {
  it("detects Windows absolute paths", () => {
    expect(isAbsolutePath("C:\\Users\\test")).toBe(true);
    expect(isAbsolutePath("D:/work/project")).toBe(true);
  });

  it("detects POSIX absolute paths", () => {
    expect(isAbsolutePath("/home/user/project")).toBe(true);
  });

  it("rejects relative paths", () => {
    expect(isAbsolutePath("project/src")).toBe(false);
    expect(isAbsolutePath("./project")).toBe(false);
    expect(isAbsolutePath("src")).toBe(false);
  });
});

describe("basename", () => {
  it("extracts filename from forward-slash path", () => {
    expect(basename("/home/user/file.txt")).toBe("file.txt");
  });

  it("extracts filename from backslash path", () => {
    expect(basename("C:\\Users\\test\\file.txt")).toBe("file.txt");
  });

  it("returns the string itself when no separator", () => {
    expect(basename("file.txt")).toBe("file.txt");
  });

  it("handles trailing separator", () => {
    expect(basename("/home/user/dir/")).toBe("");
  });
});
