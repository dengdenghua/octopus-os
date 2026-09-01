import { expect, test } from "./fixtures";

/**
 * E2E: realtime workspace shell smoke
 *
 * Prerequisites: backend on :8000 (local-auth allow_any_username), frontend on :3000.
 *
 * 这里只验证登录、路由、输入入口和失败恢复壳层。它不启动模型，也不宣称
 * 覆盖 WebSocket delta、首 token 或流式 Markdown；这些生命周期由 realtime
 * reducer/client/hook 集成回归覆盖，真实模型路径由部署 smoke 单独执行。
 */
test.describe("Realtime workspace shell", () => {
  test("authenticated workspace renders chat composer and timeline container", async ({
    authedPage: page,
  }) => {
    await page.goto("/#/workspace/realtime/new?agent=general");
    await page.waitForLoadState("domcontentloaded");

    // 路由未被重定向到 login
    await expect(page).toHaveURL(/#\/workspace\/realtime\/new/);

    // 输入框可见 — 对话发送入口就绪
    await expect(page.getByTestId("chat-composer-input")).toBeVisible({
      timeout: 15_000,
    });
  });

  test("workspace sidebar and composer remain usable after login", async ({
    authedPage: page,
  }) => {
    await page.goto("/#/workspace/realtime/new");
    await page.waitForLoadState("domcontentloaded");

    await expect(page).toHaveURL(/#\/workspace\/realtime\/new/);
    await expect(
      page.getByRole("button", { name: /^(新建任务|New task)$/i }),
    ).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("chat-composer-input")).toBeVisible();
  });

  test("a missing deep-linked thread settles into a recoverable empty state", async ({
    authedPage: page,
  }) => {
    const missingThreadId = `e2e-missing-${Date.now()}`;
    await page.goto(`/#/workspace/realtime/${missingThreadId}`);
    await page.waitForLoadState("domcontentloaded");

    await expect(page).toHaveURL(
      new RegExp(`#\\/workspace\\/realtime\\/${missingThreadId}$`),
    );
    await expect(page.getByText(/还没有消息|No messages yet/i)).toBeVisible({
      timeout: 15_000,
    });
    // A missing id is represented as a recoverable local empty thread. It is
    // not a transport failure, so the composer stays usable and no retry-only
    // error state is required.
    await expect(page.getByTestId("chat-composer-input")).toBeVisible();
    await expect(
      page.getByTestId("conversation-activity-pulse"),
    ).not.toBeVisible();
  });
});
