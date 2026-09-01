import { afterEach, describe, expect, test, vi } from "vitest";

import { loopbackOriginRedirectURL } from "./loopback-origin";

describe("loopback origin normalization", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  test("keeps canonical localhost untouched", () => {
    expect(
      loopbackOriginRedirectURL(
      "http://localhost:3000/#/workspace/agents?surface=chat",
      ),
    ).toBeNull();
  });

  test("redirects 127 loopback to canonical localhost and preserves route", () => {
    expect(
      loopbackOriginRedirectURL(
      "http://127.0.0.1:3000/#/workspace/agents?surface=chat",
      ),
    ).toBe(
      "http://localhost:3000/#/workspace/agents?surface=chat",
    );
  });

  test("allows deployments to choose a different canonical loopback host", () => {
    vi.stubEnv("VITE_CANONICAL_LOOPBACK_HOST", "127.0.0.1");
    expect(
      loopbackOriginRedirectURL(
        "http://localhost:3000/#/workspace/realtime/new",
      ),
    ).toBe("http://127.0.0.1:3000/#/workspace/realtime/new");
  });

  test("can be disabled for special local deployments", () => {
    vi.stubEnv("VITE_DISABLE_LOOPBACK_ORIGIN_NORMALIZATION", "true");
    expect(
      loopbackOriginRedirectURL(
        "http://127.0.0.1:3000/#/workspace/realtime/new",
      ),
    ).toBeNull();
  });

  test("treats canonical host none as an opt-out", () => {
    vi.stubEnv("VITE_CANONICAL_LOOPBACK_HOST", "none");
    expect(
      loopbackOriginRedirectURL(
        "http://127.0.0.1:3000/#/workspace/realtime/new",
      ),
    ).toBeNull();
  });
});
