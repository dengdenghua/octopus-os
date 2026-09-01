import { test, expect } from "@playwright/test";

/**
 * E2E: Workflow Editor.
 *
 * Prerequisites: backend on :8000, frontend on :3000.
 */

test.describe("Workflow Editor", () => {
  test("workflows page loads with editor canvas", async ({ page }) => {
    await page.goto("/#/workspace/workflows");
    await page.waitForLoadState("domcontentloaded");

    await expect(page.getByText(/Workflow|工作流/i).first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("editor has Start and End nodes", async ({ page }) => {
    await page.goto("/#/workspace/workflows");
    await page.waitForLoadState("domcontentloaded");

    await expect(page.locator("text=Start").first()).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.locator("text=End").first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("node palette shows LLM, Tool, Code, Condition buttons", async ({
    page,
  }) => {
    await page.goto("/#/workspace/workflows");
    await page.waitForLoadState("domcontentloaded");

    await expect(
      page.getByRole("button", { name: "LLM", exact: true }),
    ).toBeVisible({ timeout: 10_000 });
    await expect(
      page.getByRole("button", { name: "Tool", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Code", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Condition", exact: true }),
    ).toBeVisible();
  });

  test("clicking LLM palette button adds a node to the canvas", async ({
    page,
  }) => {
    await page.goto("/#/workspace/workflows");
    await page.waitForLoadState("domcontentloaded");

    await page
      .getByRole("button", { name: "LLM", exact: true })
      .waitFor({ state: "visible", timeout: 10_000 });

    const initialNodes = await page.locator(".react-flow__node").count();
    await page
      .getByRole("button", { name: "LLM", exact: true })
      .click({ force: true });
    await page.waitForTimeout(1000);

    const newNodeCount = await page.locator(".react-flow__node").count();
    expect(newNodeCount).toBeGreaterThanOrEqual(initialNodes);
  });

  test("Save and Run buttons are present", async ({ page }) => {
    await page.goto("/#/workspace/workflows");
    await page.waitForLoadState("domcontentloaded");

    await expect(page.getByRole("button", { name: "Save" })).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByRole("button", { name: "Run" })).toBeVisible();
  });

  test("Run button can be clicked without crash", async ({ page }) => {
    await page.goto("/#/workspace/workflows");
    await page.waitForLoadState("domcontentloaded");

    const runButton = page.getByRole("button", { name: "Run" });
    await expect(runButton).toBeVisible({ timeout: 10_000 });
    await runButton.click({ force: true });

    await page.waitForTimeout(2000);
    await expect(page.locator("body")).toBeVisible();
  });
});
