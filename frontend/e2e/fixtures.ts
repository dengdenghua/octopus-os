import { expect, test as base, type Page } from "@playwright/test";

function apiPath(url: string): string | null {
  try {
    const parsed = new URL(url);
    return parsed.pathname.startsWith("/api/") ? parsed.pathname : null;
  } catch {
    return url.startsWith("/api/") ? url : null;
  }
}

function shouldIgnoreRequestFailure(errorText: string): boolean {
  return /net::ERR_ABORTED/i.test(errorText);
}

function shouldIgnoreConsoleError(text: string): boolean {
  // 404 (missing resource) and 403 (permission gated in e2e env) are
  // backend/data-setup issues, not frontend regressions.
  if (
    /Failed to load resource: the server responded with a status of 40[34]/i.test(
      text,
    )
  ) {
    return true;
  }
  return /ResizeObserver loop completed with undelivered notifications/i.test(
    text,
  );
}

// Prepare one workspace page across both supported E2E lanes:
// - developer UI lane: local auth is enabled, so log in and inject the token;
// - isolated full-stack lane: auth is disabled in config.e2e.yaml, so the
//   workspace is already accessible and no fabricated credential is needed.
async function prepareWorkspaceAuth(
  page: Page,
  username = "e2e-tester",
): Promise<void> {
  const authStatus = await page.request.get("/api/auth/status");
  if (!authStatus.ok()) {
    throw new Error(
      `auth status failed: ${authStatus.status()} ${await authStatus.text()}`,
    );
  }
  const status = (await authStatus.json()) as { enabled?: boolean };
  if (!status.enabled) return;

  const res = await page.request.post("/api/auth/local/login", {
    data: { username },
  });
  if (!res.ok()) {
    throw new Error(
      `local-auth login failed: ${res.status()} ${await res.text()}`,
    );
  }
  const data = (await res.json()) as {
    access_token: string;
    user: unknown;
  };
  if (!data.access_token) {
    throw new Error("local-auth login returned no access_token");
  }
  await injectAuthIntoPage(page, data.access_token, data.user);
}

// Inject the auth token into localStorage so the SPA accepts the session.
// Keys must match frontend/src/core/auth/api.ts.
async function injectAuthIntoPage(page: Page, token: string, user: unknown) {
  await page.addInitScript(
    ({ token, user, ts }) => {
      window.localStorage.setItem("echo_auth_token", token);
      window.localStorage.setItem("echo_user", JSON.stringify(user));
      window.localStorage.setItem("echo_auth_ts", String(ts));
    },
    { token, user, ts: Date.now() },
  );
}

export const test = base.extend({
  page: async ({ page }, run) => {
    const issues: string[] = [];

    page.on("pageerror", (error) => {
      issues.push(`pageerror: ${error.message}`);
    });

    page.on("console", (message) => {
      if (message.type() !== "error") {
        return;
      }
      const text = message.text();
      if (shouldIgnoreConsoleError(text)) {
        return;
      }
      const location = message.location();
      const where = location.url
        ? `${location.url}:${location.lineNumber}:${location.columnNumber}`
        : "unknown location";
      issues.push(`console error at ${where}: ${text}`);
    });

    page.on("requestfailed", (request) => {
      const path = apiPath(request.url());
      if (!path) {
        return;
      }
      const errorText = request.failure()?.errorText || "unknown failure";
      if (shouldIgnoreRequestFailure(errorText)) {
        return;
      }
      issues.push(`API request failed ${path}: ${errorText}`);
    });

    page.on("response", (response) => {
      const status = response.status();
      if (status < 500) {
        return;
      }
      const path = apiPath(response.url());
      if (!path) {
        return;
      }
      issues.push(`API ${status} response ${path}`);
    });

    await run(page);
    await page.waitForTimeout(100);
    expect(issues, issues.join("\n")).toEqual([]);
  },

  // Authenticated page: logs in via local-auth then injects the token so
  // workspace routes are accessible instead of redirecting to /login. It is
  // also safe in the isolated full-stack config where auth is disabled.
  authedPage: async ({ page }, run) => {
    await prepareWorkspaceAuth(page);
    await run(page);
  },
});

export { expect };
