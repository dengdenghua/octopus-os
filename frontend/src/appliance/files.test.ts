import { beforeEach, describe, expect, it, vi } from "vitest";

import { authHeader } from "@/appliance/auth";

import {
  downloadFile,
  emptyTrash,
  FILE_SERVICE_UNAVAILABLE_MESSAGE,
  FileServiceUnavailableError,
  fetchStorageUsage,
  listDir,
  RESUMABLE_UPLOAD_THRESHOLD_BYTES,
  uploadFile,
} from "./files";

vi.mock("@/appliance/auth", () => ({
  authHeader: vi.fn(),
}));

class MockXhr {
  static instances: MockXhr[] = [];

  method = "";
  url = "";
  status = 200;
  responseText = "";
  response: Blob = new Blob();
  responseType: XMLHttpRequestResponseType = "";
  sentBody: Document | XMLHttpRequestBodyInit | null = null;
  headers: Record<string, string> = {};
  upload: { onprogress: ((event: ProgressEvent) => void) | null } = {
    onprogress: null,
  };
  onprogress: ((event: ProgressEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  onload: (() => void) | null = null;

  constructor() {
    MockXhr.instances.push(this);
  }

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }

  send(body: Document | XMLHttpRequestBodyInit | null = null) {
    this.sentBody = body;
  }
}

beforeEach(() => {
  vi.mocked(authHeader).mockReturnValue({
    Authorization: "Bearer local-test",
  });
  MockXhr.instances = [];
  localStorage.clear();
  Reflect.deleteProperty(window, "showSaveFilePicker");
  vi.stubGlobal("XMLHttpRequest", MockXhr);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          availableBytes: 1024,
          reserveBytes: 128,
          maxUploadBytes: 2048,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
});

describe("NAS file transfers", () => {
  it("reads the bounded local storage usage projection", async () => {
    const payload = {
      schema: "echo.storage.usage.v1",
      readOnly: true,
      generatedAt: 1,
      disk: {
        totalBytes: 100,
        usedBytes: 25,
        freeBytes: 75,
        reserveBytes: 10,
        availableForUploadsBytes: 65,
        usedPercent: 25,
      },
      library: {
        logicalBytes: 12,
        files: 2,
        directories: 1,
        scannedEntries: 3,
        maxEntries: 200000,
        truncated: false,
        skippedLinks: 0,
      },
      categories: [],
      topFolders: [],
      trash: { bytes: 0, files: 0 },
      uploads: { reservedBytes: 0, active: 0 },
      quotas: [],
    };
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(fetchStorageUsage(true)).resolves.toEqual(payload);
    expect(fetch).toHaveBeenCalledWith(
      "/api/appliance/files/usage?fresh=true",
      { headers: { Authorization: "Bearer local-test" } },
    );
  });

  it("turns an absent appliance route into a bounded service state", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Not Found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const request = listDir("");
    await expect(request).rejects.toBeInstanceOf(FileServiceUnavailableError);
    await expect(request).rejects.toThrow(FILE_SERVICE_UNAVAILABLE_MESSAGE);
  });

  it("uploads with auth, destination directory and byte progress", async () => {
    const progress = vi.fn();
    const file = new File(["echo"], "report.txt", { type: "text/plain" });
    const promise = uploadFile("docs", file, progress);
    await vi.waitFor(() => expect(MockXhr.instances).toHaveLength(1));
    const xhr = MockXhr.instances[0]!;

    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe("/api/appliance/files/upload");
    expect(xhr.headers.Authorization).toBe("Bearer local-test");
    expect(xhr.sentBody).toBeInstanceOf(FormData);
    expect((xhr.sentBody as FormData).get("path")).toBe("docs");
    expect((xhr.sentBody as FormData).get("size")).toBe("4");
    expect(((xhr.sentBody as FormData).get("file") as File).name).toBe(
      "report.txt",
    );

    xhr.upload.onprogress?.(
      new ProgressEvent("progress", {
        loaded: 2,
        total: 4,
        lengthComputable: true,
      }),
    );
    xhr.responseText = JSON.stringify({
      ok: true,
      entry: {
        name: "report.txt",
        path: "docs/report.txt",
        kind: "file",
        size: 4,
        mtime: 1,
      },
      sha256: "a".repeat(64),
      hashVerified: false,
    });
    xhr.onload?.();

    await expect(promise).resolves.toMatchObject({
      entry: { path: "docs/report.txt" },
    });
    expect(progress).toHaveBeenCalledWith({ loaded: 2, total: 4, percent: 50 });
    expect(fetch).toHaveBeenCalledWith(
      "/api/appliance/files/upload/preflight",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          path: "docs",
          filename: "report.txt",
          size: 4,
          overwrite: false,
        }),
      }),
    );
  });

  it("resumes a large upload from the server offset after a lost chunk response", async () => {
    vi.stubGlobal("crypto", {
      subtle: {
        digest: vi.fn().mockResolvedValue(new Uint8Array(32).fill(7).buffer),
      },
    });
    const size = RESUMABLE_UPLOAD_THRESHOLD_BYTES;
    const file = new File([new Uint8Array(size)], "archive.bin", {
      type: "application/octet-stream",
      lastModified: 123,
    });
    const sessionId = "a".repeat(32);
    let chunkAttempted = false;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/upload/preflight")) {
          return new Response(JSON.stringify({ ok: true }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/upload/sessions") && init?.method === "POST") {
          return new Response(
            JSON.stringify({
              ok: true,
              sessionId,
              target: "docs/archive.bin",
              expectedBytes: size,
              uploadedBytes: 0,
              chunkBytes: size,
              sha256Expected: false,
              fingerprint: "07".repeat(32),
              updatedAt: 1,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/chunk") && init?.method === "PUT") {
          chunkAttempted = true;
          throw new TypeError("connection reset after server commit");
        }
        if (url.endsWith(`/upload/sessions/${sessionId}`) && !init?.method) {
          return new Response(
            JSON.stringify({
              ok: true,
              sessionId,
              target: "docs/archive.bin",
              expectedBytes: size,
              uploadedBytes: size,
              chunkBytes: size,
              sha256Expected: false,
              fingerprint: "07".repeat(32),
              updatedAt: 2,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/complete") && init?.method === "POST") {
          return new Response(
            JSON.stringify({
              ok: true,
              entry: {
                name: "archive.bin",
                path: "docs/archive.bin",
                kind: "file",
                size,
                mtime: 3,
              },
              sha256: "b".repeat(64),
              hashVerified: false,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        throw new Error(`unexpected request: ${init?.method || "GET"} ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const progress = vi.fn();

    const receipt = await uploadFile("docs", file, progress, {
      retryDelayMs: 0,
    });

    expect(receipt.entry.path).toBe("docs/archive.bin");
    expect(chunkAttempted).toBe(true);
    expect(MockXhr.instances).toHaveLength(0);
    expect(progress).toHaveBeenLastCalledWith({
      loaded: size,
      total: size,
      percent: 100,
    });
    expect(
      localStorage.getItem(`echo.upload.session.${"07".repeat(32)}`),
    ).toBeNull();
  });

  it("does not retry a quota 507 for a resumable upload", async () => {
    vi.stubGlobal("crypto", {
      subtle: {
        digest: vi.fn().mockResolvedValue(new Uint8Array(32).fill(8).buffer),
      },
    });
    const size = RESUMABLE_UPLOAD_THRESHOLD_BYTES;
    const file = new File([new Uint8Array(size)], "quota.bin", {
      type: "application/octet-stream",
      lastModified: 456,
    });
    const sessionId = "b".repeat(32);
    let chunkCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/upload/preflight")) {
          return new Response(JSON.stringify({ ok: true }), { status: 200 });
        }
        if (url.endsWith("/upload/sessions") && init?.method === "POST") {
          return new Response(
            JSON.stringify({
              ok: true,
              sessionId,
              target: "docs/quota.bin",
              expectedBytes: size,
              uploadedBytes: 0,
              chunkBytes: size,
              sha256Expected: false,
              fingerprint: "08".repeat(32),
              updatedAt: 1,
            }),
            { status: 200 },
          );
        }
        if (url.endsWith("/chunk") && init?.method === "PUT") {
          chunkCalls += 1;
          return new Response(
            JSON.stringify({
              detail: {
                message: "共享目录 'docs' 已达到容量配额",
                path: "docs",
              },
            }),
            { status: 507, headers: { "Content-Type": "application/json" } },
          );
        }
        throw new Error(`unexpected request: ${init?.method || "GET"} ${url}`);
      }),
    );

    await expect(
      uploadFile("docs", file, undefined, { retries: 3, retryDelayMs: 0 }),
    ).rejects.toThrow("共享目录 'docs' 已达到容量配额");
    expect(chunkCalls).toBe(1);
  });

  it("downloads with auth and saves the returned blob", async () => {
    const createObjectURL = vi.fn(() => "blob:echo-download");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    const promise = downloadFile("docs/report.txt", "report.txt");
    const xhr = MockXhr.instances[0]!;
    xhr.response = new Blob(["echo"]);
    xhr.onload?.();
    await promise;

    expect(xhr.method).toBe("GET");
    expect(xhr.url).toContain("docs%2Freport.txt");
    expect(xhr.headers.Authorization).toBe("Bearer local-test");
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:echo-download");
  });

  it("delegates cookie-authenticated fallback downloads to the browser without a Blob", async () => {
    vi.mocked(authHeader).mockReturnValue({});
    const createObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL });
    let clickedHref = "";
    let clickedFilename = "";
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function () {
        clickedHref = this.getAttribute("href") || "";
        clickedFilename = this.download;
      },
    );

    await downloadFile("family/archive.iso", "archive.iso");

    expect(MockXhr.instances).toHaveLength(0);
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
    expect(clickedHref).toBe(
      "/api/appliance/files/download?path=family%2Farchive.iso",
    );
    expect(clickedHref).not.toMatch(/token|authorization|bearer/i);
    expect(clickedFilename).toBe("archive.iso");
  });

  it("streams downloads to a file handle and resumes with Range after interruption", async () => {
    const written: Uint8Array[] = [];
    const writable = {
      write: vi.fn(async (chunk: Uint8Array) => written.push(chunk)),
      seek: vi.fn(async () => undefined),
      truncate: vi.fn(async () => undefined),
      close: vi.fn(async () => undefined),
      abort: vi.fn(async () => undefined),
    };
    const picker = vi.fn().mockResolvedValue({
      createWritable: vi.fn().mockResolvedValue(writable),
    });
    Object.defineProperty(window, "showSaveFilePicker", {
      configurable: true,
      value: picker,
    });
    const encoder = new TextEncoder();
    let firstPull = true;
    const interrupted = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (firstPull) {
          firstPull = false;
          controller.enqueue(encoder.encode("echo"));
        } else {
          controller.error(new Error("network interrupted"));
        }
      },
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(interrupted, {
          status: 200,
          headers: { "Content-Length": "8" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(encoder.encode(" nas"), {
          status: 206,
          headers: { "Content-Range": "bytes 4-7/8" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const progress = vi.fn();

    await downloadFile("docs/report.txt", "report.txt", progress, {
      retryDelayMs: 0,
    });

    expect(MockXhr.instances).toHaveLength(0);
    expect(picker).toHaveBeenCalledWith({
      suggestedName: "report.txt",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1]![1]?.headers).toMatchObject({
      Authorization: "Bearer local-test",
      Range: "bytes=4-",
    });
    expect(
      written.map((chunk) => new TextDecoder().decode(chunk)).join(""),
    ).toBe("echo nas");
    expect(writable.seek).toHaveBeenCalledWith(4);
    expect(writable.close).toHaveBeenCalledOnce();
    expect(writable.abort).not.toHaveBeenCalled();
    expect(progress).toHaveBeenLastCalledWith({
      loaded: 8,
      total: 8,
      percent: 100,
    });
  });

  it("sends the one-shot approval token only on physical deletion", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true, emptied: 1 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await emptyTrash("one-shot.signature");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/files/trash/empty",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: "Bearer local-test",
          "X-Echo-Approval": "one-shot.signature",
        }),
      }),
    );
  });
});
