import { test, expect } from "@playwright/test";

/**
 * E2E: public shell -> workspace chat surfaces.
 *
 * Prerequisites: backend on :8000, frontend on :3000.
 */

test.describe("Chat golden path", () => {
  test("landing page loads", async ({ page }) => {
    await page.goto("/");

    await expect(page).toHaveTitle("Echo");
    await expect(page.locator("body")).toContainText("Echo Agent OS");
    await expect(page.locator("input").first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("can load the workspace chat route", async ({ page }) => {
    await page.goto("/#/workspace/agents/general/chats/new");
    await page.waitForLoadState("domcontentloaded");

    await expect(page).toHaveURL(/#\/workspace/);
    await expect(page.locator("textarea").first()).toBeVisible({
      timeout: 15_000,
    });
  });

  test("workspace sidebar shows the chat entry", async ({ page }) => {
    await page.goto("/#/workspace/chats/new");
    await page.waitForLoadState("domcontentloaded");

    const chatLink = page.getByRole("link", { name: "Chats" });
    await expect(chatLink).toBeVisible({ timeout: 15_000 });
  });

  test("new chat page has a message input", async ({ page }) => {
    await page.goto("/#/workspace/chats/new");
    await page.waitForLoadState("domcontentloaded");

    const input = page
      .locator(
        "textarea, [contenteditable=true], [role=textbox], input[type=text]",
      )
      .first();
    await expect(input).toBeVisible({ timeout: 15_000 });
  });
});
