import { afterEach, expect, it, vi } from "vitest";
import { locateArtifactRoute } from "./locate";
import { parseWorkspaceResourceId } from "./utils";
vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer test" }),
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8000",
}));
afterEach(() => vi.restoreAllMocks());

it("resolves a scoped output through the authenticated server and preserves its name", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        thread_id: "task",
        path: "C:/files/报告 #1.txt",
        resource_id: "workspace-file:v1:dGFzaw:ZmluYWw:cmVwb3J0LnR4dA",
      }),
    ),
  );
  const route = await locateArtifactRoute(
    "workspace-output:final:报告 #1.txt",
    "task",
  );
  const request = new URL(String(fetcher.mock.calls[0]![0]));
  const resourceId = decodeURIComponent(request.pathname.split("/").at(-1)!);
  expect(parseWorkspaceResourceId(resourceId)).toEqual({
    threadId: "task",
    area: "final",
    relativePath: "报告 #1.txt",
  });
  expect(request.searchParams.get("locate")).toBe("true");
  expect(fetcher.mock.calls[0]![1]).toMatchObject({
    headers: { Authorization: "Bearer test" },
    cache: "no-store",
  });
  expect(new URL(route, window.location.origin).searchParams.get("file")).toBe(
    "C:/files/报告 #1.txt",
  );
  expect(
    new URL(route, window.location.origin).searchParams.get("sourceArtifact"),
  ).toBe("workspace-output:final:报告 #1.txt");
  expect(
    new URL(route, window.location.origin).searchParams.get("resource_id"),
  ).toBe("workspace-file:v1:dGFzaw:ZmluYWw:cmVwb3J0LnR4dA");
});

it("does not guess locations after an authorization error or mismatched response", async () => {
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response("denied", { status: 403 }))
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({ thread_id: "other", path: "/private/file.txt" }),
      ),
    );
  await expect(
    locateArtifactRoute("workspace-output:final:report.txt", "task"),
  ).rejects.toMatchObject({ status: 403 });
  await expect(
    locateArtifactRoute("workspace-output:final:report.txt", "task"),
  ).rejects.toThrow("有效的文件位置");
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("resolves a workspace resource identity through the same scoped endpoint", async () => {
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(
      new Response(
        JSON.stringify({ thread_id: "task", path: "C:/files/report.md" }),
      ),
    );
  const route = await locateArtifactRoute(
    "workspace-file:v1:dGFzaw:ZmluYWw:cmVwb3J0Lm1k",
    "task",
  );
  expect(fetcher).toHaveBeenCalledOnce();
  expect(
    new URL(route, window.location.origin).searchParams.get("sourceArtifact"),
  ).toBe("workspace-output:final:report.md");
});
