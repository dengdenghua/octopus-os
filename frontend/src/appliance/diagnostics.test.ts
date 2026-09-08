import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchDiagnosticBundle, fetchServiceHealth } from "./diagnostics";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("diagnostic bundle download", () => {
  it("accepts the bounded ZIP response and its safe server filename", async () => {
    const blob = new Blob(["safe diagnostics"], { type: "application/zip" });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(blob, {
        status: 200,
        headers: {
          "Content-Type": "application/zip",
          "Content-Disposition":
            'attachment; filename="echo-diagnostics-20260908T123456Z.zip"',
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchDiagnosticBundle();

    expect(result.filename).toBe("echo-diagnostics-20260908T123456Z.zip");
    expect(result.blob.size).toBeGreaterThan(0);
    expect(result.blob.size).toBeLessThanOrEqual(512 * 1024);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/diagnostics/bundle",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("does not trust an arbitrary attachment filename", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(new Blob(["zip"], { type: "application/zip" }), {
          headers: {
            "Content-Type": "application/zip",
            "Content-Disposition": 'attachment; filename="../../startup.exe"',
          },
        }),
      ),
    );

    await expect(fetchDiagnosticBundle()).resolves.toMatchObject({
      filename: "echo-diagnostics.zip",
    });
  });

  it("rejects an empty or incorrectly typed response", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("", { headers: { "Content-Type": "application/json" } }),
        ),
    );

    await expect(fetchDiagnosticBundle()).rejects.toThrow(
      "诊断包响应格式不正确",
    );
  });
});

describe("system service health", () => {
  it("accepts the bounded service summary", async () => {
    const payload = {
      schema: "echo.appliance-diagnostics-services.v1",
      state: "critical",
      available: true,
      checkedAt: "2026-09-09T01:02:03Z",
      counts: { monitored: 7, expected: 5, active: 4, failed: 1, restarts: 5 },
      alerts: {
        total: 1,
        bySeverity: { critical: 1 },
        codes: ["service.restart_storm"],
      },
    };
    const fetchMock = vi.fn().mockResolvedValue(Response.json(payload));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchServiceHealth()).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/diagnostics/services",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("rejects malformed or oversized service fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json({
          schema: "echo.appliance-diagnostics-services.v1",
          state: "healthy",
          available: true,
          checkedAt: null,
          counts: {
            monitored: 7,
            expected: 5,
            active: 5,
            failed: 0,
            restarts: -1,
          },
          alerts: { total: 0, bySeverity: {}, codes: [] },
        }),
      ),
    );

    await expect(fetchServiceHealth()).rejects.toThrow(
      "系统服务健康响应格式不正确",
    );
  });
});
