import { afterEach, describe, expect, test, vi } from "vitest";

import {
  getBackendBaseURL,
  getBackendTransportBaseURL,
  getBackendWebSocketBaseURL,
  getControlPlaneBaseURL,
  getEchoBaseURL,
  getPublicAssetURL,
} from ".";

const ORIGINAL_LOCATION = window.location;

function setLocation(url: string) {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: new URL(url),
  });
}

describe("backend base URL resolution", () => {
  afterEach(() => {
    window.sessionStorage.clear();
    vi.unstubAllEnvs();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: ORIGINAL_LOCATION,
    });
    delete window.echo;
  });

  test("uses Vite proxy-relative URLs in dev mode by default", () => {
    setLocation("http://localhost:3000/#/workspace/agents");

    expect(getBackendBaseURL()).toBe("");
    expect(getControlPlaneBaseURL()).toBe("");
    expect(getEchoBaseURL()).toBe("/api");
    expect(getBackendWebSocketBaseURL()).toBe("ws://localhost:3000");
  });

  test("keeps authenticated control-plane requests on the current loopback origin", () => {
    setLocation("http://127.0.0.1:3000/#/workspace/agents");

    expect(getBackendBaseURL()).toBe("");
    expect(getControlPlaneBaseURL()).toBe("");
  });

  test("lets runtime backend query param override dev proxy defaults", () => {
    setLocation(
      "http://localhost:3000/?echoBackend=http%3A%2F%2F127.0.0.1%3A8000%2F#/workspace/realtime/new",
    );

    expect(getBackendBaseURL()).toBe("http://127.0.0.1:8000");
    expect(getControlPlaneBaseURL()).toBe("http://127.0.0.1:8000");
    expect(getEchoBaseURL()).toBe("http://127.0.0.1:8000/api");
    expect(window.sessionStorage.getItem("echoBackend")).toBe(
      "http://127.0.0.1:8000",
    );
  });

  test("reads runtime backend query param from hash-router routes", () => {
    setLocation(
      "http://localhost:3000/#/workspace/realtime/new?echoBackend=http%3A%2F%2Flocalhost%3A8001%2F",
    );

    expect(getBackendBaseURL()).toBe("http://localhost:8001");
    expect(getEchoBaseURL()).toBe("http://localhost:8001/api");
    expect(window.sessionStorage.getItem("echoBackend")).toBe(
      "http://localhost:8001",
    );
  });

  test("prefers shell query runtime backend over hash route query", () => {
    setLocation(
      "http://localhost:3000/?echoBackend=http%3A%2F%2F127.0.0.1%3A8000%2F#/workspace/realtime/new?echoBackend=http%3A%2F%2Flocalhost%3A8001",
    );

    expect(getBackendBaseURL()).toBe("http://127.0.0.1:8000");
    expect(getEchoBaseURL()).toBe("http://127.0.0.1:8000/api");
  });

  test("lets Electron-injected runtime backend override dev proxy defaults", () => {
    setLocation("http://localhost:3000/#/workspace/realtime/new");
    window.echo = {
      backendBaseURL: "http://127.0.0.1:8765/",
      isElectron: true,
    };

    expect(getBackendBaseURL()).toBe("http://127.0.0.1:8765");
    expect(getBackendTransportBaseURL()).toBe("http://127.0.0.1:8765");
    expect(getBackendWebSocketBaseURL()).toBe("ws://127.0.0.1:8765");
    expect(getEchoBaseURL()).toBe("http://127.0.0.1:8765/api");
  });

  test("keeps packaged Electron HTTP on its app origin and WebSockets on loopback", () => {
    setLocation(
      "echo-app://app/index.html?echoBackend=http%3A%2F%2Fevil.example#/workspace/realtime/new",
    );
    window.echo = {
      backendBaseURL: "http://127.0.0.1:8765/",
      isElectron: true,
    };

    expect(getBackendBaseURL()).toBe("");
    expect(getEchoBaseURL()).toBe("/api");
    expect(getBackendTransportBaseURL()).toBe("http://127.0.0.1:8765");
    expect(getBackendWebSocketBaseURL()).toBe("ws://127.0.0.1:8765");
  });

  test("resolves bundled community assets through Vite's public base", () => {
    expect(getPublicAssetURL("/community/memory-video(1).jpg")).toBe(
      "/community/memory-video(1).jpg",
    );
  });

  test("rejects unsafe runtime backend protocols", () => {
    setLocation(
      "http://localhost:3000/?echoBackend=javascript%3Aalert%281%29#/workspace/agents",
    );

    expect(getBackendBaseURL()).toBe("");
    expect(getEchoBaseURL()).toBe("/api");
    expect(window.sessionStorage.getItem("echoBackend")).toBeNull();
  });
});
