import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
const identity = vi.hoisted(() => ({ actor: "one" }));
vi.mock("@/core/auth/api", () => ({ currentActorId: () => identity.actor }));
import { availableDesktopWorkbenchApps } from "./desktop-apps";
import { WORKBENCH_BUILTIN_APPS } from "./apps";
import {
  resetModuleStateCache,
  setModuleAvailabilitySnapshot,
  setModuleEnabled,
  useModuleAvailabilitySnapshot,
} from "../modules/enabled-modules";

beforeEach(() => {
  identity.actor = "one";
  localStorage.clear();
  resetModuleStateCache();
});

it("hides old account availability before a new inventory arrives", () => {
  setModuleAvailabilitySnapshot({ design: true });
  const hook = renderHook(() =>
    availableDesktopWorkbenchApps(useModuleAvailabilitySnapshot()),
  );
  expect(hook.result.current.some((app) => app.id === "design")).toBe(true);
  identity.actor = "two";
  hook.rerender();
  expect(hook.result.current.every((app) => app.delivery === "core")).toBe(
    true,
  );
});

it("uses the registry identity, route and name for desktop launchers", () => {
  const apps = availableDesktopWorkbenchApps(new Map([["design", true]]));
  expect(apps.find((app) => app.id === "design")).toBe(
    WORKBENCH_BUILTIN_APPS.find((app) => app.id === "design"),
  );
  expect(apps.some((app) => app.id === "local-database")).toBe(true);
  expect(apps.some((app) => app.id === "paper-trading")).toBe(false);
});

it("hiding a sidebar shortcut does not remove an installed desktop application", () => {
  setModuleEnabled("design", false, "general");
  expect(
    availableDesktopWorkbenchApps(new Map([["design", true]])).some(
      (app) => app.id === "design",
    ),
  ).toBe(true);
});

it("removes a disabled or unverified remote app from mounted launcher data", () => {
  const hook = renderHook(() =>
    availableDesktopWorkbenchApps(useModuleAvailabilitySnapshot()),
  );
  expect(hook.result.current.every((app) => app.delivery === "core")).toBe(
    true,
  );
  act(() => setModuleAvailabilitySnapshot({ design: true }));
  expect(hook.result.current.some((app) => app.id === "design")).toBe(true);
  act(() => setModuleAvailabilitySnapshot({ design: false }));
  expect(hook.result.current.some((app) => app.id === "design")).toBe(false);
  act(() => setModuleAvailabilitySnapshot(null));
  expect(hook.result.current.every((app) => app.delivery === "core")).toBe(
    true,
  );
});
