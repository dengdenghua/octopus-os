import { expect, it } from "vitest";
import {
  databaseFileRoute,
  databaseResourceRoute,
  directoryBreadcrumbs,
  localFileLocation,
} from "./file-location";

it("preserves Unix backslashes as literal filename characters", () => {
  const path = "/data/team\\archive/report\\draft.txt";
  const route = databaseFileRoute(path, "task")!;
  expect(new URL(route, "https://echo.test").searchParams.get("file")).toBe(
    path,
  );
  expect(localFileLocation(path)?.directory).toBe("/data/team\\archive");
  expect(
    directoryBreadcrumbs("/data/team\\archive").map((item) => item.path),
  ).toEqual(["/", "/data", "/data/team\\archive"]);
});

it("preserves Windows names and URL-significant characters across navigation", () => {
  const route = databaseFileRoute("C:\\资料\\报告 #1%.txt", "task/?#")!;
  const query = new URL(route, "https://echo.test").searchParams;
  expect(query.get("file")).toBe("C:/资料/报告 #1%.txt");
  expect(query.get("sourceThread")).toBe("task/?#");
  expect(localFileLocation(query.get("file")!)?.directory).toBe("C:/资料");
});

it("carries a server resource identity alongside a legacy local path", () => {
  const route = databaseFileRoute(
    "C:/资料/报告.txt",
    "task",
    "workspace-output:final:报告.txt",
    "workspace-file:v1:dGFza:ZmluYWw:cmFwb3J0LnR4dA",
  )!;
  const query = new URL(route, "https://echo.test").searchParams;
  expect(query.get("resource_id")).toBe(
    "workspace-file:v1:dGFza:ZmluYWw:cmFwb3J0LnR4dA",
  );
  expect(query.get("sourceArtifact")).toBe("workspace-output:final:报告.txt");
});

it("retains drive, Unix and network share roots in directory navigation", () => {
  expect(
    directoryBreadcrumbs("C:/资料/子目录").map((item) => item.path),
  ).toEqual(["C:/", "C:/资料", "C:/资料/子目录"]);
  expect(directoryBreadcrumbs("/home/user").map((item) => item.path)).toEqual([
    "/",
    "/home",
    "/home/user",
  ]);
  expect(
    directoryBreadcrumbs("\\\\server\\share\\folder").map((item) => item.path),
  ).toEqual(["//server/share/", "//server/share/folder"]);
  expect(localFileLocation("C:/report.txt")?.directory).toBe("C:/");
});

it("does not guess a local path for workspace references, URLs or traversal", () => {
  for (const path of [
    "workspace-output:final:report.txt",
    "report.txt",
    "https://echo.test/report.txt",
    "/a/../b.txt",
  ]) {
    expect(databaseFileRoute(path, "task")).toBeNull();
  }
});

it("builds a database location from a resource identity without a path", () => {
  const route = databaseResourceRoute(
    "appliance-file:v1:root:report",
    "thread-1",
  );
  expect(route).toContain("resource_id=appliance-file%3Av1%3Aroot%3Areport");
  expect(route).toContain("sourceThread=thread-1");
});
