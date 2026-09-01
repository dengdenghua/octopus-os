import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

/**
 * E2E regression lockdown · 2026-04-24
 *
 * Every spec here pins a real bug that was found by the manual
 * browser-tour regression pass and fixed. If one of these goes red
 * in CI, a past bug came back.
 *
 * Prerequisites: backend on :8000, frontend on :3000.
 *
 * Bug index:
 *   #1 · Intelligence subscriptions use the real router and clean up test data
 *   #2 · Cost tab always 0 tokens · direct_llm path didn't emit budget_commit
 *   #5 · <Header> async Client Component warning (RSC code in SPA)
 *   #6 · Team invite page leaked hard-coded Chinese in en-US locale
 *   workflow-as-skill · UI save → /api/skills shows workflow_<id[:8]>
 */

const BACKEND = "http://127.0.0.1:8000";

// ═══════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════

/**
 * React-controlled inputs in this app don't always pick up
 * Playwright's ``fill()`` event sequence · the chat composer and
 * intel topic box both re-render from state so a value set via the
 * DOM alone leaves React thinking the field is still empty (send
 * button stays disabled, add handler sees "").
 *
 * Fix matches what the hand-rolled JS shim did during the manual
 * browser-regression pass: grab the native ``value`` setter off
 * ``HTMLTextAreaElement.prototype`` / ``HTMLInputElement.prototype``
 * and dispatch an ``input`` event so React's onChange fires.
 */
async function reactFill(
  page: Page,
  selector: string,
  text: string,
): Promise<void> {
  const el = page.locator(selector).filter({ visible: true }).first();
  await el.waitFor({ state: "visible", timeout: 10_000 });
  await el.evaluate((node: Element, value: string) => {
    const proto =
      node instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (!setter) throw new Error("no native value setter");
    setter.call(node, value);
    node.dispatchEvent(new Event("input", { bubbles: true }));
  }, text);
}

async function deleteSubscriptionsByTopic(
  request: APIRequestContext,
  topic: string,
): Promise<void> {
  const listed = await request.get(`${BACKEND}/api/intelligence/subscriptions`);
  if (!listed.ok()) return;
  const body = await listed.json();
  const subscriptions = body.subscriptions ?? [];
  await Promise.all(
    subscriptions
      .filter((item: { topic?: string }) => item.topic === topic)
      .map((item: { id: string }) =>
        request.delete(
          `${BACKEND}/api/intelligence/subscriptions/${encodeURIComponent(item.id)}`,
        ),
      ),
  );
}

async function sendChatMessage(page: Page, text: string): Promise<void> {
  await page.goto("/#/workspace/chats/new");
  await page.waitForLoadState("domcontentloaded");

  await reactFill(page, "textarea", text);

  // Wait a beat so React re-render + button enable transition
  // settles before clicking Send.
  await page.waitForTimeout(200);
  const sendBtn = page.getByRole("button", { name: "Send" });
  await sendBtn.click();

  // Wait for URL to change to a real thread id · the router navigates
  // to /workspace/chats/<generated-id> as soon as the first user
  // message gets posted · robust signal that the send went through.
  await page.waitForURL(/#\/workspace\/chats\/[^/]+$/, { timeout: 20_000 });
  // Then give the SSE reply ~20s to commit into journal · this is
  // the actual signal the Bug#2 assertion is watching for.
  await page.waitForTimeout(20_000);
}

// ═══════════════════════════════════════════════════════════
// Bug #2 · Cost tab was always 0 tokens
// ═══════════════════════════════════════════════════════════

test.describe("Bug#2 regression · Cost tab reflects real chat cost", () => {
  test("chat then observability/cost shows non-zero tokens", async ({
    page,
  }) => {
    test.setTimeout(60_000); // real LLM call · give it room
    await sendChatMessage(page, "Answer with one short sentence: 2+2=?");

    // Ask backend directly rather than scroll through observability
    // tabs · keeps the assertion robust to tab ordering / label
    // changes while still proving the journal really got populated.
    const resp = await page.request.get(
      `${BACKEND}/api/budget/summary?limit=5`,
    );
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(
      body.total_tokens,
      "budget_commit should be written by direct_llm path · see _commit_direct_llm_cost",
    ).toBeGreaterThan(0);
    expect(body.commit_count).toBeGreaterThan(0);
  });
});

// ═══════════════════════════════════════════════════════════
// workflow-as-skill · UI save registers skill
// ═══════════════════════════════════════════════════════════

test.describe("workflow-as-skill · saving registers a skill", () => {
  test("POST /api/workflow-editor then /api/skills lists workflow_<id>", async ({
    request,
  }) => {
    const created = await request.post(`${BACKEND}/api/workflow-editor`, {
      data: {
        name: "e2e-regression-wf",
        description: "lockdown spec · expect skill reg",
        definition: {
          nodes: [
            { id: "start-1", type: "start", data: {} },
            { id: "tool-1", type: "tool", data: { path: "." } },
            { id: "end-1", type: "end", data: {} },
          ],
          edges: [
            { source: "start-1", target: "tool-1" },
            { source: "tool-1", target: "end-1" },
          ],
        },
      },
    });
    expect(created.ok()).toBeTruthy();
    const wf = await created.json();
    const wfId = wf.id as string;
    const expectedSkill = `workflow_${wfId.slice(0, 8)}`;

    try {
      const skills = await request.get(`${BACKEND}/api/skills`);
      expect(skills.ok()).toBeTruthy();
      const body = await skills.json();
      const items = body.skills ?? body;
      const names: string[] = items.map((s: { name: string }) => s.name);
      expect(names).toContain(expectedSkill);
    } finally {
      // Cleanup · keep /api/skills catalog tidy between runs
      await request.delete(`${BACKEND}/api/workflow-editor/${wfId}`);
    }
  });
});

// ═══════════════════════════════════════════════════════════
// Bug #1 · Intelligence subscriptions are real and self-cleaning
// ═══════════════════════════════════════════════════════════

test.describe("Bug#1 regression · Intelligence subscriptions", () => {
  test("POST creates a real subscription and deletes the E2E record", async ({
    request,
  }) => {
    const topic = `e2e-api-${Date.now()}`;
    try {
      const resp = await request.post(
        `${BACKEND}/api/intelligence/subscriptions`,
        {
          data: {
            topic,
            display_name: topic,
            keywords: ["e2e", "cleanup"],
            cadence: "daily",
            sources: ["web", "news"],
          },
        },
      );
      expect(resp.ok()).toBeTruthy();
      const body = await resp.json();
      expect(body._stub).not.toBe(true);
      expect(body.topic).toBe(topic);
      expect(body.id).toMatch(/^sub_/);

      const listed = await request.get(
        `${BACKEND}/api/intelligence/subscriptions`,
      );
      expect(listed.ok()).toBeTruthy();
      const listedBody = await listed.json();
      expect(
        (listedBody.subscriptions ?? []).some(
          (item: { topic?: string }) => item.topic === topic,
        ),
      ).toBe(true);
    } finally {
      await deleteSubscriptionsByTopic(request, topic);
    }
  });

  test("UI creates a subscription through the current draft flow and cleans it up", async ({
    page,
  }) => {
    const topic = `e2e-ui-${Date.now()}`;
    await page.goto("/#/workspace/intelligence");
    await page.waitForLoadState("domcontentloaded");

    try {
      await reactFill(
        page,
        "textarea",
        `Create a temporary subscription named ${topic} for E2E cleanup verification.`,
      );
      await page
        .getByRole("button", { name: /生成订阅草案|Generate Draft/ })
        .click();

      const createButton = page.getByRole("button", {
        name: /创建这个订阅|Create Subscription/,
      });
      await expect(createButton).toBeVisible({ timeout: 10_000 });
      await reactFill(page, "input", topic);
      await createButton.click();

      await expect(
        page.getByText(/订阅添加成功|Subscription added/i),
      ).toBeVisible({ timeout: 5000 });

      const listed = await page.request.get(
        `${BACKEND}/api/intelligence/subscriptions`,
      );
      expect(listed.ok()).toBeTruthy();
      const listedBody = await listed.json();
      expect(
        (listedBody.subscriptions ?? []).some(
          (item: { topic?: string }) => item.topic === topic,
        ),
      ).toBe(true);
    } finally {
      await deleteSubscriptionsByTopic(page.request, topic);
    }
  });
});

// ═══════════════════════════════════════════════════════════
// Bug #6 · Team join page respects active locale
// ═══════════════════════════════════════════════════════════

test.describe("Bug#6 regression · Team join page i18n", () => {
  test("en-US invite error state does not leak hard-coded Chinese", async ({
    page,
  }) => {
    await page.context().addCookies([
      {
        name: "locale",
        value: "en-US",
        domain: "localhost",
        path: "/",
      },
    ]);

    await page.goto("/#/workspace/team/join");
    await page.waitForLoadState("domcontentloaded");

    await expect(
      page.getByRole("heading", { name: "Join team" }),
    ).toBeVisible();
    await expect(
      page.getByText("The invite link is missing a token."),
    ).toBeVisible();

    const visibleText = await page
      .getByRole("heading", { name: "Join team" })
      .locator("xpath=ancestor::div[contains(@class, 'max-w-md')][1]")
      .innerText();
    expect(visibleText).not.toMatch(/[\u4e00-\u9fff]/);
  });
});

// ═══════════════════════════════════════════════════════════
// Bug #5 · /about no "async Client Component" warning
// ═══════════════════════════════════════════════════════════

test.describe("Bug#5 regression · /about free of async-RSC warnings", () => {
  test("console has no 'async Client Component' warning", async ({ page }) => {
    const warnings: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "warning" || msg.type() === "error") {
        warnings.push(msg.text());
      }
    });
    page.on("pageerror", (err) => warnings.push(err.message));

    await page.goto("/#/about");
    await page.waitForLoadState("networkidle");
    // Give React warnings a beat to surface during hydration
    await page.waitForTimeout(1500);

    const asyncWarnings = warnings.filter((w) =>
      /async Client Component|is an async Client/.test(w),
    );
    expect(asyncWarnings, asyncWarnings.join("\n\n")).toEqual([]);
  });
});
