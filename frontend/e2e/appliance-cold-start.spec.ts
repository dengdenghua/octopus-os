import { expect, test, type Page, type Route } from "@playwright/test";

const AUTH_STATUS = {
  enabled: true,
  jwt_available: true,
  allow_registration: false,
  exempt_paths: [],
};

async function fulfillJson(
  route: Route,
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
) {
  await route.fulfill({
    status,
    contentType: "application/json; charset=utf-8",
    headers,
    body: JSON.stringify(body),
  });
}

async function stubLoginDependencies(page: Page) {
  await page.route("**/api/auth/me", (route) =>
    fulfillJson(route, 401, { detail: "Not authenticated" }),
  );
}

async function stubApplianceLoginRequired(page: Page, onRequest: () => void) {
  await page.route("**/api/appliance/auth/status", (route) => {
    onRequest();
    return fulfillJson(route, 200, {
      authRequired: true,
      authenticated: false,
      role: null,
    });
  });
}

test("recovers the login screen after the appliance backend starts", async ({
  page,
}) => {
  let statusRequests = 0;
  let applianceStatusRequests = 0;
  let backendReady = false;

  await stubLoginDependencies(page);
  await stubApplianceLoginRequired(page, () => {
    applianceStatusRequests += 1;
  });
  await page.route("**/api/auth/status", (route) => {
    statusRequests += 1;
    if (!backendReady) {
      return fulfillJson(
        route,
        503,
        {
          detail: {
            code: "appliance_starting",
            message: "Echo OS 正在启动，请稍后重试",
          },
        },
        { "Retry-After": "1", "Cache-Control": "no-store" },
      );
    }
    return fulfillJson(route, 200, AUTH_STATUS);
  });

  await page.goto("/#/desktop");

  await expect(
    page.getByRole("heading", { name: "系统服务正在启动" }),
  ).toBeVisible();
  expect(applianceStatusRequests).toBe(0);
  backendReady = true;

  await expect(page.getByRole("textbox", { name: "用户名" })).toBeVisible({
    timeout: 5_000,
  });
  expect(statusRequests).toBeGreaterThanOrEqual(2);
  expect(applianceStatusRequests).toBe(1);
});

test("keeps an unrelated backend 503 as an explicit retryable error", async ({
  page,
}) => {
  let statusRequests = 0;

  await stubLoginDependencies(page);
  await page.route("**/api/auth/status", (route) => {
    statusRequests += 1;
    return fulfillJson(route, 503, { detail: "database unavailable" });
  });
  let applianceStatusRequests = 0;
  await stubApplianceLoginRequired(page, () => {
    applianceStatusRequests += 1;
  });

  await page.goto("/#/desktop");

  await expect(
    page.getByRole("heading", { name: "暂时无法连接系统服务" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "重试连接" })).toBeVisible();
  await page.waitForTimeout(2_250);
  expect(statusRequests).toBe(1);
  expect(applianceStatusRequests).toBe(0);
});

test("keeps the desktop closed when the appliance session probe fails", async ({
  page,
}) => {
  await stubLoginDependencies(page);
  await page.route("**/api/auth/status", (route) =>
    fulfillJson(route, 200, AUTH_STATUS),
  );
  await page.route("**/api/appliance/auth/status", (route) =>
    fulfillJson(route, 503, { detail: "appliance unavailable" }),
  );

  await page.goto("/#/desktop");

  await expect(
    page.getByRole("heading", { name: "暂时无法连接系统服务" }),
  ).toBeVisible();
  await expect(page.getByRole("textbox", { name: "用户名" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重试连接" })).toBeVisible();
});
