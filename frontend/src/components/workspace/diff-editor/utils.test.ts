import { describe, expect, it } from "vitest";

import {
  alignSideBySide,
  computeLineDiff,
  computeWordDiff,
  getDirectory,
  getFileExtension,
  getFileName,
  getLanguageFromPath,
  groupFilesByDirectory,
  parseUnifiedDiff,
  sortFiles,
  type DiffLine,
  type FileDiff,
} from "./utils";

describe("computeLineDiff", () => {
  it("returns empty hunks for identical text", () => {
    expect(computeLineDiff("hello\nworld", "hello\nworld")).toEqual([]);
  });

  it("detects added lines", () => {
    const hunks = computeLineDiff("a", "a\nb");
    expect(hunks.length).toBeGreaterThan(0);
    const addLines = hunks
      .flatMap((h) => h.lines)
      .filter((l) => l.type === "add");
    expect(addLines.length).toBeGreaterThan(0);
    expect(addLines.some((l) => l.content === "b")).toBe(true);
  });

  it("detects removed lines", () => {
    const hunks = computeLineDiff("a\nb", "a");
    const removeLines = hunks
      .flatMap((h) => h.lines)
      .filter((l) => l.type === "remove");
    expect(removeLines.length).toBeGreaterThan(0);
  });

  it("generates valid hunk headers", () => {
    const hunks = computeLineDiff("a\nb\nc", "a\nx\nc");
    expect(hunks[0]!.header).toMatch(/^@@ -\d+,\d+ \+\d+,\d+ @@$/);
  });
});

describe("computeWordDiff", () => {
  it("returns equal segments for identical lines", () => {
    const { oldSegments, newSegments } = computeWordDiff(
      "hello world",
      "hello world",
    );
    expect(oldSegments.every((s) => s.type === "equal")).toBe(true);
    expect(newSegments.every((s) => s.type === "equal")).toBe(true);
  });

  it("detects word-level changes", () => {
    const { oldSegments, newSegments } = computeWordDiff(
      "hello world",
      "hello earth",
    );
    expect(
      oldSegments.some((s) => s.type === "remove" && s.text === "world"),
    ).toBe(true);
    expect(
      newSegments.some((s) => s.type === "add" && s.text === "earth"),
    ).toBe(true);
  });
});

describe("parseUnifiedDiff", () => {
  it("returns empty for empty input", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
  });

  it("parses a simple unified diff", () => {
    const diff = [
      "--- a/file.txt",
      "+++ b/file.txt",
      "@@ -1,3 +1,3 @@",
      " line1",
      "-old",
      "+new",
      " line3",
    ].join("\n");

    const hunks = parseUnifiedDiff(diff);
    expect(hunks).toHaveLength(1);
    expect(hunks[0]!.oldStart).toBe(1);
    expect(
      hunks[0]!.lines.some((l) => l.type === "remove" && l.content === "old"),
    ).toBe(true);
    expect(
      hunks[0]!.lines.some((l) => l.type === "add" && l.content === "new"),
    ).toBe(true);
  });
});

describe("alignSideBySide", () => {
  it("pairs context lines on both sides", () => {
    const lines: DiffLine[] = [
      { type: "context", content: "same", oldLineNumber: 1, newLineNumber: 1 },
    ];
    const result = alignSideBySide(lines);
    expect(result).toHaveLength(1);
    expect(result[0]!.left).toBe(result[0]!.right);
  });

  it("pairs removals and additions", () => {
    const lines: DiffLine[] = [
      { type: "remove", content: "old", oldLineNumber: 1, newLineNumber: null },
      { type: "add", content: "new", oldLineNumber: null, newLineNumber: 1 },
    ];
    const result = alignSideBySide(lines);
    expect(result).toHaveLength(1);
    expect(result[0]!.left!.content).toBe("old");
    expect(result[0]!.right!.content).toBe("new");
  });

  it("handles unbalanced removals/additions", () => {
    const lines: DiffLine[] = [
      { type: "remove", content: "a", oldLineNumber: 1, newLineNumber: null },
      { type: "remove", content: "b", oldLineNumber: 2, newLineNumber: null },
      { type: "add", content: "c", oldLineNumber: null, newLineNumber: 1 },
    ];
    const result = alignSideBySide(lines);
    expect(result).toHaveLength(2);
    expect(result[1]!.left!.content).toBe("b");
    expect(result[1]!.right).toBeNull();
  });
});

describe("getFileName", () => {
  it("extracts filename from unix path", () => {
    expect(getFileName("/home/user/file.txt")).toBe("file.txt");
  });

  it("extracts filename from windows path", () => {
    expect(getFileName("C:\\Users\\test\\file.txt")).toBe("file.txt");
  });

  it("returns input when no separator", () => {
    expect(getFileName("file.txt")).toBe("file.txt");
  });
});

describe("getDirectory", () => {
  it("extracts directory from path", () => {
    expect(getDirectory("src/components/file.tsx")).toBe("src/components");
  });

  it("returns empty for root-level file", () => {
    expect(getDirectory("file.txt")).toBe("");
  });
});

describe("getFileExtension", () => {
  it("extracts extension", () => {
    expect(getFileExtension("file.tsx")).toBe("tsx");
  });

  it("returns empty for no extension", () => {
    expect(getFileExtension("Makefile")).toBe("");
  });

  it("lowercases extension", () => {
    expect(getFileExtension("file.TSX")).toBe("tsx");
  });
});

describe("getLanguageFromPath", () => {
  it("maps ts to typescript", () => {
    expect(getLanguageFromPath("file.ts")).toBe("typescript");
  });

  it("maps py to python", () => {
    expect(getLanguageFromPath("file.py")).toBe("python");
  });

  it("returns plaintext for unknown extension", () => {
    expect(getLanguageFromPath("file.xyz")).toBe("plaintext");
  });
});

describe("sortFiles", () => {
  const files: FileDiff[] = [
    {
      id: "1",
      filePath: "b.ts",
      status: "modified",
      additions: 5,
      deletions: 2,
      hunks: [],
      originalContent: null,
      newContent: null,
      accepted: null,
      timestamp: 0,
    },
    {
      id: "2",
      filePath: "a.ts",
      status: "added",
      additions: 10,
      deletions: 0,
      hunks: [],
      originalContent: null,
      newContent: null,
      accepted: null,
      timestamp: 0,
    },
    {
      id: "3",
      filePath: "c.ts",
      status: "deleted",
      additions: 0,
      deletions: 8,
      hunks: [],
      originalContent: null,
      newContent: null,
      accepted: null,
      timestamp: 0,
    },
  ];

  it("sorts by name", () => {
    const sorted = sortFiles(files, "name");
    expect(sorted.map((f) => f.filePath)).toEqual(["a.ts", "b.ts", "c.ts"]);
  });

  it("sorts by status (added → modified → deleted)", () => {
    const sorted = sortFiles(files, "status");
    expect(sorted.map((f) => f.status)).toEqual([
      "added",
      "modified",
      "deleted",
    ]);
  });

  it("sorts by changes (most first)", () => {
    const sorted = sortFiles(files, "changes");
    expect(sorted[0]!.filePath).toBe("a.ts");
  });
});

describe("groupFilesByDirectory", () => {
  it("groups files by directory", () => {
    const files: FileDiff[] = [
      {
        id: "1",
        filePath: "src/a.ts",
        status: "added",
        additions: 1,
        deletions: 0,
        hunks: [],
        originalContent: null,
        newContent: null,
        accepted: null,
        timestamp: 0,
      },
      {
        id: "2",
        filePath: "src/b.ts",
        status: "added",
        additions: 1,
        deletions: 0,
        hunks: [],
        originalContent: null,
        newContent: null,
        accepted: null,
        timestamp: 0,
      },
      {
        id: "3",
        filePath: "lib/c.ts",
        status: "added",
        additions: 1,
        deletions: 0,
        hunks: [],
        originalContent: null,
        newContent: null,
        accepted: null,
        timestamp: 0,
      },
    ];
    const groups = groupFilesByDirectory(files);
    expect(groups).toHaveLength(2);
    expect(groups.map((g) => g.directory).sort()).toEqual(["lib", "src"]);
  });

  it("uses (root) for files without directory", () => {
    const files: FileDiff[] = [
      {
        id: "1",
        filePath: "readme.md",
        status: "modified",
        additions: 1,
        deletions: 0,
        hunks: [],
        originalContent: null,
        newContent: null,
        accepted: null,
        timestamp: 0,
      },
    ];
    const groups = groupFilesByDirectory(files);
    expect(groups[0]!.directory).toBe("(root)");
  });
});
