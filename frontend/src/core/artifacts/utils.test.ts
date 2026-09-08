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
  it("keeps an absolute source path out of the attachment reader", () => {
    expect(urlOfArtifact({ filepath: "/out.pdf", threadId: "t1" })).toBe(
      "http://localhost:8001/api/fs/content?path=%2Fout.pdf&thread_id=t1",
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
      "http://localhost:8001/api/fs/content?download=true&path=%2Fout.pdf&thread_id=t1",
    );
  });

  it.each([
    "/data/Invoices/2026/invoice #1?.pdf",
    "C:\\授权目录\\invoice #1.pdf",
    "reports/README.md",
    "/data/workspaces/another-thread/output/final/report.pdf",
  ])("preserves source identity and task scope for %s", (filepath) => {
    const url = new URL(
      urlOfArtifact({
        filepath,
        threadId: "current-thread",
        officePreview: true,
      }),
    );
    expect(url.pathname).toBe("/api/fs/content");
    expect(url.searchParams.get("path")).toBe(filepath);
    expect(url.searchParams.get("thread_id")).toBe("current-thread");
    expect(url.searchParams.get("office_preview")).toBe("true");
  });

  it("keeps bare attachment names on the upload API and encodes them", () => {
    expect(urlOfArtifact({ filepath: "invoice #1.pdf", threadId: "t 1" })).toBe(
      "http://localhost:8001/api/threads/t%201/artifacts/invoice%20%231.pdf",
    );
  });

  it("normalizes actual Windows upload metadata before selecting a reader", () => {
    expect(
      urlOfArtifact({
        filepath: "C:\\Echo\\workspaces\\t1\\upload\\invoice.pdf",
        threadId: "t1",
      }),
    ).toBe(
      "http://localhost:8001/api/workspace-resources/workspace-file%3Av1%3AdDE%3AdXBsb2Fk%3AaW52b2ljZS5wZGY",
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
      "http://localhost:8001/api/workspace-resources/workspace-file%3Av1%3AdDE%3AZmluYWw%3AcmVwb3J0cy9vdXQgZmlsZS5tZA",
    );
    expect(
      urlOfArtifact({ filepath: ref, threadId: "t1", download: true }),
    ).toBe(
      "http://localhost:8001/api/workspace-resources/workspace-file%3Av1%3AdDE%3AZmluYWw%3AcmVwb3J0cy9vdXQgZmlsZS5tZA?download=true",
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
      "http://localhost:8001/api/workspace-resources/workspace-file%3Av1%3AdDE%3AZmluYWw%3AZGVjay5wcHR4?office_preview=true&office_fidelity_preview=true",
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

  it("normalizes workspace resource identities without losing the thread scope", () => {
    expect(
      normalizeWorkspaceArtifactRef(
        "workspace-file:v1:dGhyZWFkLTE:ZmluYWw:cmVwb3J0Lm1k",
        "thread-1",
      ),
    ).toBe("workspace-output:final:report.md");
    expect(
      normalizeWorkspaceArtifactRef(
        "workspace-file:v1:dGhyZWFkLTE:ZmluYWw:cmVwb3J0Lm1k",
        "other-thread",
      ),
    ).toBe("workspace-file:v1:dGhyZWFkLTE:ZmluYWw:cmVwb3J0Lm1k");
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

  it("shows a readable path for workspace resource identities", () => {
    expect(
      artifactDisplayPath("workspace-file:v1:dGhyZWFkLTE:ZmluYWw:cmVwb3J0Lm1k"),
    ).toBe("report.md");
  });
});

describe("resolveArtifactURL", () => {
  it("builds absolute artifact URL", () => {
    expect(resolveArtifactURL("/report.html", "t2")).toBe(
      "http://localhost:8001/api/fs/content?path=%2Freport.html&thread_id=t2",
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
