import { expect, test } from "./fixtures";

/**
 * Visual regression: pixel-level baselines for the key workspace surfaces.
 *
 * This complements the DOM-behaviour tests (message-group / message-list
 * suites) by catching style drift that assertions cannot: accent colours,
 * pill badges, three-tier network cards, layout shifts.
 *
 * Baselines are committed under this spec's `-snapshots/` directory. To
 * refresh them after an intentional design change:
 *   npx playwright test visual-regression --update-snapshots
 *
 * Only chromium runs these: cross-browser font rasterisation produces
 * meaningless diffs; a stable platform baseline is what we want.
 */
test.skip(
  ({ browserName }) => browserName !== "chromium",
  "视觉回归仅 chromium:跨浏览器字体渲染差异会产生无意义 diff",
);

test.describe("Visual regression · workspace surfaces", () => {
  // These screenshots share the same local thread/backend state. Running the
  // flow serially avoids concurrent hydration requests turning a stable
  // empty screen into a transient skeleton and producing false diffs.
  test.describe.configure({ mode: "serial" });

  test("chat composer surface (new realtime thread)", async ({
    authedPage: page,
  }) => {
    await page.goto("/#/workspace/realtime/new?agent=general");
    await page.waitForLoadState("domcontentloaded");

    await expect(page).toHaveURL(/#\/workspace\/realtime\/new/);
    await expect(page.getByTestId("chat-composer-input")).toBeVisible({
      timeout: 15_000,
    });
    // Wait a beat for layout/theme to settle before freezing the frame.
    await expect
      .poll(
        async () => {
          const box = await page
            .getByTestId("chat-composer-input")
            .boundingBox();
          return box?.y;
        },
        { timeout: 5_000 },
      )
      .toBeDefined();
    await expect(
      page.getByRole("heading", { name: /Hello, I am .+|你好，我是/ }),
    ).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.locator(".animate-skeleton-entrance")).toHaveCount(0, {
      timeout: 15_000,
    });

    await expect(page).toHaveScreenshot("chat-composer.png", {
      maxDiffPixelRatio: 0.02,
      animations: "disabled",
    });
  });

  test("missing deep-linked thread settles into recoverable empty state", async ({
    authedPage: page,
  }) => {
    await page.goto("/#/workspace/realtime/does-not-exist-thread");
    await page.waitForLoadState("domcontentloaded");

    await expect(page.getByTestId("chat-composer-input")).toBeVisible({
      timeout: 15_000,
    });
    // The composer mounts before the missing-thread recovery finishes. Wait
    // for the settled empty state so the screenshot cannot capture the
    // transient skeleton or an intermediate error frame.
    await expect(page.getByText(/还没有消息|No messages yet/i)).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("chat-composer-input")).toBeVisible();

    await expect(page).toHaveScreenshot("recoverable-empty-state.png", {
      maxDiffPixelRatio: 0.02,
      animations: "disabled",
    });
  });

  test("workspace shell with sidebar navigation", async ({
    authedPage: page,
  }) => {
    await page.goto("/#/workspace/realtime/new");
    await page.waitForLoadState("domcontentloaded");

    await expect(
      page.getByRole("button", { name: /^(新建任务|New task)$/i }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("chat-composer-input")).toBeVisible();

    await expect(page).toHaveScreenshot("workspace-shell.png", {
      maxDiffPixelRatio: 0.02,
      animations: "disabled",
    });
  });

  test("agent workbench uses a right drawer at 1024px", async ({
    authedPage: page,
  }) => {
    await page.setViewportSize({ width: 1024, height: 720 });
    await page.goto("/#/workspace/realtime/new?agent=general");
    await page.waitForLoadState("domcontentloaded");

    await expect(page.getByTestId("chat-composer-input")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("button", { name: "Open right panel" }).click();

    const workbench = page.getByRole("dialog", {
      name: "Agent workbench",
    });
    await expect(workbench).toBeVisible();
    await expect(workbench).toHaveAttribute(
      "data-secondary-panel-presentation",
      "desktop-drawer",
    );

    await expect(page).toHaveScreenshot("workbench-drawer-1024.png", {
      maxDiffPixelRatio: 0.02,
      animations: "disabled",
    });
  });
});
