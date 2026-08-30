import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  mkdtempSync,
  mkdirSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import desktopProtocol from "./desktop-protocol.cjs";

let tempRoot;
let distRoot;

beforeEach(() => {
  tempRoot = mkdtempSync(path.join(tmpdir(), "echo-protocol-"));
  distRoot = path.join(tempRoot, "dist");
  mkdirSync(path.join(distRoot, "community"), { recursive: true });
  writeFileSync(path.join(distRoot, "index.html"), "<main>Echo</main>");
  writeFileSync(
    path.join(distRoot, "community", "memory-video(1).jpg"),
    "image",
  );
});

afterEach(() => {
  rmSync(tempRoot, { recursive: true, force: true });
});

describe("desktop renderer origin", () => {
  it("accepts only the fixed app scheme and host", () => {
    expect(
      desktopProtocol.isDesktopAppURL("echo-app://app/index.html"),
    ).toBe(true);
    expect(
      desktopProtocol.isDesktopAppURL("echo-app://evil/index.html"),
    ).toBe(false);
    expect(desktopProtocol.isDesktopAppURL("https://app/index.html")).toBe(
      false,
    );
  });

  it("accepts only credential-free loopback backend origins", () => {
    expect(
      desktopProtocol.normalizeLoopbackBackendBaseURL("http://127.0.0.1:8000/"),
    ).toBe("http://127.0.0.1:8000");
    expect(
      desktopProtocol.normalizeLoopbackBackendBaseURL("http://[::1]:8000"),
    ).toBe("http://[::1]:8000");

    for (const denied of [
      "https://api.example.com",
      "http://127.0.0.1.evil.test:8000",
      "http://user:secret@127.0.0.1:8000",
      "http://127.0.0.1:8000/api",
    ]) {
      expect(() =>
        desktopProtocol.normalizeLoopbackBackendBaseURL(denied),
      ).toThrow(/loopback/);
    }
  });
});

describe("bundled asset confinement", () => {
  it("maps / and absolute community media inside dist", () => {
    expect(desktopProtocol.resolveDesktopAssetPath(distRoot, "/")).toBe(
      realpathSync(path.join(distRoot, "index.html")),
    );
    expect(
      desktopProtocol.resolveDesktopAssetPath(
        distRoot,
        "/community/memory-video(1).jpg",
      ),
    ).toBe(
      realpathSync(path.join(distRoot, "community", "memory-video(1).jpg")),
    );
  });

  it("rejects traversal, encoded backslashes, and escaping symlinks", () => {
    const outside = path.join(tempRoot, "secret.txt");
    writeFileSync(outside, "secret");
    symlinkSync(outside, path.join(distRoot, "escape.txt"));

    expect(
      desktopProtocol.resolveDesktopAssetPath(distRoot, "/%2e%2e/secret.txt"),
    ).toBeNull();
    expect(
      desktopProtocol.resolveDesktopAssetPath(
        distRoot,
        "/community%5c..%5csecret.txt",
      ),
    ).toBeNull();
    expect(
      desktopProtocol.resolveDesktopAssetPath(distRoot, "/escape.txt"),
    ).toBeNull();
  });
});

describe("desktop protocol handler", () => {
  function makeHandler(fetchImpl) {
    return desktopProtocol.createDesktopProtocolHandler({
      distRoot,
      backendBaseURL: "http://127.0.0.1:8765",
      fetchImpl,
    });
  }

  it("serves absolute community paths from the packaged renderer", async () => {
    const fetchImpl = vi.fn(async () => new Response("image"));
    const response = await makeHandler(fetchImpl)(
      new Request("echo-app://app/community/memory-video(1).jpg"),
    );

    expect(response.status).toBe(200);
    expect(fileURLToPath(fetchImpl.mock.calls[0][0])).toBe(
      realpathSync(path.join(distRoot, "community", "memory-video(1).jpg")),
    );
  });

  it("proxies native /api and /api/plugins URLs only to loopback", async () => {
    const fetchImpl = vi.fn(async () =>
      Response.json({ ok: true }, { headers: { "X-Backend": "echo" } }),
    );
    const handler = makeHandler(fetchImpl);

    const response = await handler(
      new Request(
        "echo-app://app/api/plugins/paper-trading/page?view=compact",
        {
          headers: {
            Authorization: "Bearer renderer-token",
            Origin: "echo-app://app",
            Referer: "echo-app://app/index.html",
          },
        },
      ),
    );

    expect(response.status).toBe(200);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [target, init] = fetchImpl.mock.calls[0];
    expect(target).toBe(
      "http://127.0.0.1:8765/api/plugins/paper-trading/page?view=compact",
    );
    expect(init.headers.get("authorization")).toBe("Bearer renderer-token");
    expect(init.headers.has("origin")).toBe(false);
    expect(init.headers.has("referer")).toBe(false);
    expect(response.headers.has("access-control-allow-origin")).toBe(false);
  });

  it("does not mistake lookalike paths or foreign hosts for backend routes", async () => {
    const fetchImpl = vi.fn(async () => new Response("unexpected"));
    const handler = makeHandler(fetchImpl);

    const lookalike = await handler(
      new Request("echo-app://app/api.evil/health"),
    );
    const foreign = await handler(new Request("echo-app://evil/api/health"));

    expect(lookalike.status).toBe(404);
    expect(foreign.status).toBe(403);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("streams non-GET request bodies through the loopback proxy", async () => {
    let forwardedBody = "";
    const fetchImpl = vi.fn(async (_target, init) => {
      forwardedBody = await new Response(init.body).text();
      return new Response(null, { status: 204 });
    });
    const response = await makeHandler(fetchImpl)(
      new Request("echo-app://app/api/test-body", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: 7 }),
      }),
    );

    expect(response.status).toBe(204);
    expect(forwardedBody).toBe('{"value":7}');
    expect(fetchImpl.mock.calls[0][1].duplex).toBe("half");
  });

  it("rewrites backend redirects back onto the renderer origin", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(null, {
          status: 307,
          headers: { Location: "/api/health/" },
        }),
    );
    const response = await makeHandler(fetchImpl)(
      new Request("echo-app://app/api/health"),
    );

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(
      "echo-app://app/api/health/",
    );
  });
});
