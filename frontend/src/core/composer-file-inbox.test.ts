import { beforeEach, expect, it, vi } from "vitest";
const identity = vi.hoisted(() => ({ actor: "one" }));
vi.mock("@/core/auth/api", () => ({ currentActorId: () => identity.actor }));
import {
  clearComposerFiles,
  consumeComposerFiles,
  queueComposerFiles,
  quoteDatabaseFiles,
} from "./composer-file-inbox";

it("retains literal file names and encodes an explicit destination", () => {
  quoteDatabaseFiles(["/data/report .txt "], "task/with?#");
  expect(window.location.hash).toBe("#/workspace/realtime/task%2Fwith%3F%23");
  expect(consumeComposerFiles("task/with?#")).toEqual([
    { path: "/data/report .txt ", sourceLabel: "本地数据库" },
  ]);
});

it("keeps a database reference inside the workbench host", () => {
  const original = window.location.hash;
  try {
    window.location.hash = "#/workspace/storage?presentation=workbench";
    quoteDatabaseFiles(["report.pdf"]);
    expect(window.location.hash).toBe(
      "#/workspace/realtime/echo-assistant?agent=echo&presentation=workbench",
    );
  } finally {
    window.location.hash = original;
  }
});

beforeEach(() => {
  identity.actor += "x";
  clearComposerFiles();
});

it("waits for the intended composer and consumes each reference once", () => {
  queueComposerFiles("target", [
    { path: "C:/资料/报告.pdf", sourceLabel: "本地数据库" },
  ]);
  expect(consumeComposerFiles()).toEqual([]);
  expect(consumeComposerFiles("other")).toEqual([]);
  expect(consumeComposerFiles("target")).toEqual([
    { path: "C:/资料/报告.pdf", sourceLabel: "本地数据库" },
  ]);
  expect(consumeComposerFiles("target")).toEqual([]);
});

it("carries a stable resource identity into the intended composer", () => {
  quoteDatabaseFiles(["docs/report.pdf"], "target", [
    "appliance-file:v1:source:report",
  ]);
  expect(consumeComposerFiles("target")).toEqual([
    {
      path: "docs/report.pdf",
      sourceLabel: "本地数据库",
      resourceId: "appliance-file:v1:source:report",
    },
  ]);
});

it("does not deliver references across accounts", () => {
  queueComposerFiles("target", [{ path: "private.txt" }]);
  identity.actor = "different-account";
  expect(consumeComposerFiles("target")).toEqual([]);
});

it("clears references when an authentication session ends", () => {
  queueComposerFiles("target", [{ path: "session-private.txt" }]);
  clearComposerFiles();
  expect(consumeComposerFiles("target")).toEqual([]);
});

it("expires abandoned references instead of injecting them much later", () => {
  const clock = vi.spyOn(Date, "now").mockReturnValue(1_000);
  try {
    queueComposerFiles("target", [{ path: "old.txt" }]);
    clock.mockReturnValue(301_001);
    expect(consumeComposerFiles("target")).toEqual([]);
  } finally {
    clock.mockRestore();
  }
});
