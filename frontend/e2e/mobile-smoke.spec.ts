import type { Locator, Page } from "@playwright/test";
import { expect, test } from "./fixtures";

async function expectNoHorizontalOverflow(
  page: Page,
  locator: Locator,
  label: string,
) {
  const overflow = await locator.evaluate((node) => {
    const rect = node.getBoundingClientRect();
    return {
      left: rect.left,
      right: rect.right,
      viewportWidth: window.innerWidth,
      bodyScrollWidth: document.documentElement.scrollWidth,
    };
  });

  expect(
    overflow.left,
    `${label} should not overflow left`,
  ).toBeGreaterThanOrEqual(-1);
  expect(
    overflow.right,
    `${label} should not overflow right`,
  ).toBeLessThanOrEqual(overflow.viewportWidth + 1);
  expect(
    overflow.bodyScrollWidth,
    `${label} should not create page-level horizontal scroll`,
  ).toBeLessThanOrEqual(overflow.viewportWidth + 1);
}

test.describe("Mobile workspace smoke", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("Settings dialog stays usable across core sections", async ({
    authedPage: page,
  }) => {
    await page.goto("/#/workspace/agents");
    await page.waitForLoadState("domcontentloaded");

    const settingsButton = page.getByRole("button", {
      name: /^(设置|Settings)$/,
    });
    if (!(await settingsButton.isVisible())) {
      await page
        .getByRole("button", { name: /打开侧栏菜单|Open sidebar menu/ })
        .click();
    }
    await expect(settingsButton).toBeVisible({ timeout: 15_000 });
    await settingsButton.click();

    const dialog = page.getByRole("dialog", { name: /设置|Settings/ });
    await expect(dialog).toBeVisible();
    await expectNoHorizontalOverflow(page, dialog, "settings dialog");

    const sectionStrip = dialog.getByTestId("settings-section-scroll");
    const dialogLayout = await dialog.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      const current = node.querySelectorAll('[aria-current="page"]');
      return {
        top: rect.top,
        bottom: rect.bottom,
        height: rect.height,
        viewportHeight: window.innerHeight,
        scrollWidth: node.scrollWidth,
        clientWidth: node.clientWidth,
        currentCount: current.length,
      };
    });
    const sectionStripLayout = await sectionStrip.evaluate((node) => ({
      scrollWidth: node.scrollWidth,
      clientWidth: node.clientWidth,
    }));
    expect(dialogLayout.top).toBeGreaterThanOrEqual(15);
    expect(dialogLayout.bottom).toBeLessThanOrEqual(
      dialogLayout.viewportHeight - 15,
    );
    expect(dialogLayout.scrollWidth).toBeLessThanOrEqual(
      dialogLayout.clientWidth + 1,
    );
    expect(dialogLayout.currentCount).toBe(1);
    expect(sectionStripLayout.scrollWidth).toBeGreaterThan(
      sectionStripLayout.clientWidth,
    );
    const sectionTargetHeights = await sectionStrip
      .getByRole("button")
      .evaluateAll((buttons) =>
        buttons.map((button) => button.getBoundingClientRect().height),
      );
    expect(Math.min(...sectionTargetHeights)).toBeGreaterThanOrEqual(44);

    const resizeHandle = dialog.getByRole("separator", {
      name: /拖动调整大小|Drag to resize/,
    });
    await expect(resizeHandle).toBeHidden();

    const sections = [
      /^(账户|Account)$/,
      /^(订阅与用量|Plan & Usage)$/,
      /^(外观|Appearance)$/,
      /^(通知|Notification)$/,
      /^(记忆|Memory)$/,
      /^(工具与集成|Tools & integrations)$/,
      /^(自动化与安全|Automation & security)$/,
      /^(运行诊断|Run diagnostics)$/,
      /^(关于|About)$/,
    ];
    for (const sectionName of sections) {
      const section = dialog.getByRole("button", { name: sectionName });
      await section.scrollIntoViewIfNeeded();
      await section.click();
      await expect(section).toHaveAttribute("aria-current", "page");
      await expectNoHorizontalOverflow(
        page,
        dialog,
        `settings section ${sectionName.source}`,
      );
    }

    const aboutSection = dialog.getByRole("button", {
      name: /^(关于|About)$/,
    });
    await expect(aboutSection).toHaveAttribute("aria-current", "page");
    await expect(dialog.getByText("Apache License 2.0").first()).toBeVisible();
    await expect(dialog.getByText("MIT License")).toHaveCount(0);

    const closeButton = dialog.getByRole("button", {
      name: /^(关闭|Close)$/,
    });
    await expect(closeButton).toBeVisible();
    expect(
      await closeButton.evaluate(
        (button) => button.getBoundingClientRect().height,
      ),
    ).toBeGreaterThanOrEqual(44);
    await closeButton.click();
    await expect(dialog).toBeHidden();
  });

  test("Realtime composer fits mobile width", async ({
    authedPage: page,
  }) => {
    await page.goto("/#/workspace/realtime/new");
    await page.waitForLoadState("domcontentloaded");

    const composer = page.getByTestId("chat-composer");
    await expect(composer).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("chat-composer-input")).toBeVisible();
    await expectNoHorizontalOverflow(page, composer, "realtime composer");
  });

  test("Agents category chips scroll within the viewport", async ({
    authedPage: page,
  }) => {
    await page.goto("/#/workspace/agents");
    await page.waitForLoadState("domcontentloaded");

    const search = page.getByTestId("agents-search-input");
    await expect(search).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("agents-loading-skeleton")).toBeHidden({
      timeout: 20_000,
    });
    const chips = page.getByTestId("agents-category-scroll");
    await expect(chips).toBeVisible();
    await expectNoHorizontalOverflow(page, chips, "agents category chips");
  });

  test("Skills list and search fit mobile width", async ({
    authedPage: page,
  }) => {
    await page.goto("/#/workspace/skills");
    await page.waitForLoadState("domcontentloaded");

    await expect(page).toHaveURL(/#\/workspace\/agents\?surface=chat&tab=skills/);
    await expect(
      page.getByRole("tab", { name: /技能|Skills|Skill Market/i }),
    ).toBeVisible();
    const search = page.getByTestId("agents-search-input");
    await expect(search).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByRole("button", { name: /^(全部|All)$/ }).first(),
    ).toBeVisible();
    await expectNoHorizontalOverflow(page, search, "skills search");
  });
});
