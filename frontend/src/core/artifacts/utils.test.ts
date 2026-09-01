import { describe, expect, it, vi } from "vitest";

vi.mock("../config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

import {
  artifactDisplayPath,
  extractArtifactsFromThread,
  parseWorkspaceOutputRef,
  normalizeWorkspaceArtifactRef,
  resolveArtifactURL,
  urlOfArtifact,
  workspaceOutputRef,
} from "./utils";

describe("urlOfArtifact", () => {
  it("builds non-mock URL", () => {
    expect(urlOfArtifact({ filepath: "/out.pdf", threadId: "t1" })).toBe(
      "http://localhost:8001/api/threads/t1/artifacts/out.pdf",
    );
  });

  it("builds mock URL", () => {
    expect(
      urlOfArtifact({ filepath: "/out.pdf", threadId: "t1", isMock: true }),
    ).toBe("http://localhost:8001/mock/api/threads/t1/artifacts/out.pdf");
  });

  it("appends download param", () => {
    expect(
      urlOfArtifact({ filepath: "/out.pdf", threadId: "t1", download: true }),
    ).toBe(
      "http://localhost:8001/api/threads/t1/artifacts/out.pdf?download=true",
    );
  });

  it("builds mock URL with download", () => {
    expect(
      urlOfArtifact({
        filepath: "/out.pdf",
        threadId: "t1",
        isMock: true,
        download: true,
      }),
    ).toBe(
      "http://localhost:8001/mock/api/threads/t1/artifacts/out.pdf?download=true",
    );
  });

  it("builds workspace output URLs", () => {
    const ref = workspaceOutputRef({
      area: "final",
      relativePath: "reports/out file.md",
    });
    expect(urlOfArtifact({ filepath: ref, threadId: "t1" })).toBe(
      "http://localhost:8001/api/threads/t1/outputs/reports/out%20file.md?area=final",
    );
    expect(
      urlOfArtifact({ filepath: ref, threadId: "t1", download: true }),
    ).toBe(
      "http://localhost:8001/api/threads/t1/outputs/reports/out%20file.md?area=final&download=true",
    );
  });

  it("requests a safe office preview for workspace outputs", () => {
    const ref = workspaceOutputRef({
      area: "final",
      relativePath: "deck.pptx",
    });
    expect(
      urlOfArtifact({
        filepath: ref,
        threadId: "t1",
        officePreview: true,
        officeFidelityPreview: true,
      }),
    ).toBe(
      "http://localhost:8001/api/threads/t1/outputs/deck.pptx?area=final&office_preview=true&office_fidelity_preview=true",
    );
  });
});

describe("normalizeWorkspaceArtifactRef", () => {
  it("converts an absolute final report path to a scoped output ref", () => {
    expect(
      normalizeWorkspaceArtifactRef(
        "/Users/me/project/data/workspaces/thread-1/output/final/nas-report.md",
        "thread-1",
      ),
    ).toBe("workspace-output:final:nas-report.md");
  });

  it("converts a relative final report path to a scoped output ref", () => {
    expect(
      normalizeWorkspaceArtifactRef("output/final/nas-report.md", "thread-1"),
    ).toBe("workspace-output:final:nas-report.md");
  });

  it("does not reinterpret a path from another thread", () => {
    const filepath =
      "/Users/me/data/workspaces/thread-other/output/final/report.md";
    expect(normalizeWorkspaceArtifactRef(filepath, "thread-1")).toBe(filepath);
  });
});

describe("workspace output refs", () => {
  it("round trips workspace output refs", () => {
    const ref = workspaceOutputRef({
      area: "stages",
      relativePath: "/stage-1/notes.md",
    });

    expect(ref).toBe("workspace-output:stages:stage-1/notes.md");
    expect(parseWorkspaceOutputRef(ref)).toEqual({
      area: "stages",
      relativePath: "stage-1/notes.md",
    });
    expect(artifactDisplayPath(ref)).toBe("stage-1/notes.md");
  });
});

describe("resolveArtifactURL", () => {
  it("builds absolute artifact URL", () => {
    expect(resolveArtifactURL("/report.html", "t2")).toBe(
      "http://localhost:8001/api/threads/t2/artifacts/report.html",
    );
  });
});

describe("extractArtifactsFromThread", () => {
  it("returns artifacts from thread values", () => {
    const thread = { values: { artifacts: ["/a.txt", "/b.txt"] } } as any;
    expect(extractArtifactsFromThread(thread)).toEqual(["/a.txt", "/b.txt"]);
  });

  it("returns empty array when no artifacts", () => {
    const thread = { values: {} } as any;
    expect(extractArtifactsFromThread(thread)).toEqual([]);
  });
});
