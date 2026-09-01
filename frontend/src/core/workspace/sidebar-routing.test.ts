import { describe, expect, it } from "vitest";

import {
  agentHudHref,
  isAgentSurfaceActive,
  isChatSurfaceRoute,
  isCompanySurfaceActive,
  isNavRouteActive,
  isStorageLibraryRouteActive,
  isStorageRouteActive,
  workspaceAgentReturnRoute,
} from "./sidebar-routing";

describe("sidebar routing helpers", () => {
  it("returns from browser to the last workspace location", () => {
    expect(
      workspaceAgentReturnRoute(
        "/browser",
        "",
        "/workspace/realtime/thread-42?mode=team",
      ),
    ).toBe("/workspace/realtime/thread-42?mode=team");
    expect(
      workspaceAgentReturnRoute(
        "/workspace/agents",
        "?surface=chat",
        "/workspace/realtime/old",
      ),
    ).toBe("/workspace/agents?surface=chat");
    expect(workspaceAgentReturnRoute("/browser", "", "https://bad.test")).toBe(
      "/workspace/realtime/new",
    );
  });

  it("detects chat surface routes", () => {
    expect(isChatSurfaceRoute("/workspace/realtime")).toBe(true);
    expect(isChatSurfaceRoute("/workspace/realtime/new")).toBe(true);
    expect(isChatSurfaceRoute("/workspace/storage")).toBe(false);
  });

  it("detects storage-family routes", () => {
    expect(isStorageRouteActive("/workspace/storage")).toBe(true);
    expect(isStorageRouteActive("/workspace/nas/files")).toBe(true);
    expect(isStorageRouteActive("/workspace/knowledge")).toBe(true);
    expect(isStorageRouteActive("/workspace/agents")).toBe(false);
  });

  it("matches nav routes with prefix semantics", () => {
    expect(isNavRouteActive("/workspace/agents", "/workspace/agents")).toBe(
      true,
    );
    expect(
      isNavRouteActive("/workspace/agents/team", "/workspace/agents"),
    ).toBe(true);
    expect(isNavRouteActive("/workspace/evolution", "/workspace/agents")).toBe(
      false,
    );
  });

  it("activates storage library rows by ?library= param", () => {
    expect(
      isStorageLibraryRouteActive(
        "/workspace/storage?library=files",
        "?library=files",
        "/workspace/storage?library=files",
      ),
    ).toBe(true);
    expect(
      isStorageLibraryRouteActive(
        "/workspace/storage",
        "",
        "/workspace/storage?library=files",
      ),
    ).toBe(false);
  });

  it("defaults company surface unless agent surface is active", () => {
    expect(isCompanySurfaceActive("/workspace/storage")).toBe(true);
    expect(isCompanySurfaceActive("/workspace/agents?surface=chat")).toBe(
      false,
    );
  });
});

describe("agentHudHref", () => {
  it("builds a HUD link without an agent target", () => {
    const href = agentHudHref({ surface: "chat" });
    const params = new URLSearchParams(href.split("?")[1]);
    expect(href.startsWith("/workspace/agents?")).toBe(true);
    expect(params.get("hud")).toBe("1");
    expect(params.get("surface")).toBe("chat");
    expect(params.has("agent")).toBe(false);
    expect(params.has("tab")).toBe(false);
  });

  it("targets a specific agent and tab", () => {
    const params = new URLSearchParams(
      agentHudHref({
        surface: "company",
        tab: "skills",
        agentName: "eve",
      }).split("?")[1],
    );
    expect(params.get("surface")).toBe("company");
    expect(params.get("tab")).toBe("skills");
    expect(params.get("agent")).toBe("eve");
  });

  it("drops a blank agent name and encodes odd ids", () => {
    expect(
      new URLSearchParams(
        agentHudHref({ surface: "chat", agentName: "   " }).split("?")[1],
      ).has("agent"),
    ).toBe(false);
    expect(
      new URLSearchParams(
        agentHudHref({ surface: "chat", agentName: "leon / chronos" }).split(
          "?",
        )[1],
      ).get("agent"),
    ).toBe("leon / chronos");
  });

  it("stays on the agent surface so the sidebar highlight is right", () => {
    const href = agentHudHref({ surface: "chat", agentName: "kane" });
    const [pathname, search] = href.split("?");
    expect(isAgentSurfaceActive(pathname, `?${search}`)).toBe(true);
  });
});
