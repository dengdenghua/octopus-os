import type { Page } from "@playwright/test";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "./fixtures";

const backendPort = process.env.GATEWAY_PORT || "18000";
const backendBase = `http://127.0.0.1:${backendPort}`;
const frontendPort = process.env.FRONTEND_PORT || "13000";
const e2eDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(e2eDir, "../..");
const rawE2eStateRoot =
  process.env.ECHO_E2E_STATE_ROOT || "test-results/full-stack-state";
const e2eStateRoot = isAbsolute(rawE2eStateRoot)
  ? resolve(rawE2eStateRoot)
  : resolve(repoRoot, rawE2eStateRoot);
const e2eDataDir = join(e2eStateRoot, "data");
const frontendOrigins = [
  `http://127.0.0.1:${frontendPort}`,
  `http://localhost:${frontendPort}`,
];

async function fetchFromPage(page: Page, path: string) {
  return page.evaluate(async (requestPath) => {
    const response = await fetch(requestPath);
    return {
      ok: response.ok,
      status: response.status,
      body: await response.json(),
    };
  }, path);
}

async function reactFill(page: Page, selector: string, text: string) {
  const el = page.locator(selector).filter({ visible: true }).first();
  await el.waitFor({ state: "visible", timeout: 15_000 });
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

async function inputValue(page: Page, selector: string) {
  return page.evaluate((targetSelector) => {
    const node = document.querySelector(targetSelector);
    if (
      node instanceof HTMLInputElement ||
      node instanceof HTMLTextAreaElement
    ) {
      return node.value;
    }
    return null;
  }, selector);
}

async function waitForThreadState(
  page: Page,
  threadId: string,
  predicate: (state: Record<string, unknown>) => boolean,
) {
  return expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${backendBase}/api/threads/${encodeURIComponent(threadId)}/state`,
        );
        if (!response.ok()) {
          return null;
        }
        const state = (await response.json()) as Record<string, unknown>;
        return predicate(state) ? state : null;
      },
      { intervals: [500, 1000, 1500, 2000], timeout: 30_000 },
    )
    .not.toBeNull();
}

async function waitForRealtimeThreadId(
  page: Page,
  timeoutMs: number,
): Promise<string | null> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      return extractRealtimeThreadId(page.url());
    } catch {
      await page.waitForTimeout(250);
    }
  }
  return null;
}

async function submitRealtimePrompt(
  page: Page,
  prompt: string,
): Promise<string> {
  const input = page.locator('[data-testid="chat-composer-input"]').first();
  const sendButton = page.getByTestId("chat-send-button");
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    await input.waitFor({ state: "visible", timeout: 15_000 });
    if (
      (await inputValue(page, '[data-testid="chat-composer-input"]')) !== prompt
    ) {
      await reactFill(page, '[data-testid="chat-composer-input"]', prompt);
    }
    await expect(sendButton).toBeEnabled({ timeout: 10_000 });
    if (attempt === 1) {
      await sendButton.click();
    } else {
      await input.press("Enter");
    }
    const threadId = await waitForRealtimeThreadId(
      page,
      attempt === 3 ? 45_000 : 15_000,
    );
    if (threadId) return threadId;
  }
  throw new Error(
    `realtime prompt did not create a thread; url=${page.url()} input=${String(
      await inputValue(page, '[data-testid="chat-composer-input"]'),
    )}`,
  );
}

function threadStateMessages(state: Record<string, unknown>): unknown[] {
  const values = state.values;
  if (
    values &&
    typeof values === "object" &&
    Array.isArray((values as { messages?: unknown }).messages)
  ) {
    return (values as { messages: unknown[] }).messages;
  }
  return Array.isArray(state.messages) ? state.messages : [];
}

function extractRealtimeThreadId(url: string): string {
  const match = /#\/workspace\/realtime\/([^/?#]+)/.exec(url);
  if (!match?.[1] || match[1] === "new") {
    throw new Error(`expected realtime thread URL, got ${url}`);
  }
  return decodeURIComponent(match[1]);
}

function normalizedPath(value: unknown): string {
  return String(value || "").replace(/\\/g, "/");
}

test.describe("Full-stack golden smoke", () => {
  test("backend, Vite proxy, and workspace shell are all live", async ({
    page,
    request,
  }) => {
    const directStatus = await request.get(`${backendBase}/api/status`);
    expect(directStatus.ok()).toBeTruthy();
    const directBody = await directStatus.json();
    expect(directBody.version).toBeTruthy();
    expect(directBody.capabilities.fastapi).toBe(true);

    const originSnapshots: Array<{
      origin: string;
      status: Record<string, unknown>;
      auth: Record<string, unknown>;
      agentNames: string[];
    }> = [];

    for (const origin of frontendOrigins) {
      const directProxyStatus = await request.get(`${origin}/api/status`);
      expect(directProxyStatus.ok()).toBeTruthy();
      const directProxyBody = await directProxyStatus.json();
      expect(directProxyBody.version).toBe(directBody.version);

      await page.goto(`${origin}/`);
      const proxiedStatus = await fetchFromPage(page, "/api/status");
      expect(proxiedStatus.ok).toBe(true);
      expect(proxiedStatus.body.version).toBe(directBody.version);

      const authStatus = await fetchFromPage(page, "/api/auth/status");
      expect(authStatus.ok).toBe(true);
      expect(typeof authStatus.body.enabled).toBe("boolean");

      const selfCheck = await fetchFromPage(page, "/api/runtime/self-check");
      expect(selfCheck.ok).toBe(true);
      expect(selfCheck.body.frontend).toMatchObject({
        canonical_origin: `http://localhost:${frontendPort}`,
        proxy_target: backendBase,
        proxy_targets_backend: true,
      });
      expect(normalizedPath(selfCheck.body.paths?.runtime_root)).toBe(
        normalizedPath(e2eStateRoot),
      );
      expect(normalizedPath(selfCheck.body.paths?.data_dir)).toBe(
        normalizedPath(e2eDataDir),
      );
      expect(normalizedPath(selfCheck.body.paths?.echo_home_env)).toBe(
        normalizedPath(e2eStateRoot),
      );
      expect(normalizedPath(selfCheck.body.paths?.echo_data_dir_env)).toBe(
        normalizedPath(e2eDataDir),
      );

      const agents = await fetchFromPage(page, "/api/agents");
      expect(agents.ok).toBe(true);
      expect(Array.isArray(agents.body)).toBe(true);
      expect(
        agents.body.some(
          (agent: { name?: string }) => agent.name === "general",
        ),
      ).toBe(true);
      originSnapshots.push({
        origin,
        status: proxiedStatus.body,
        auth: authStatus.body,
        agentNames: agents.body
          .map((agent: { name?: string }) => agent.name)
          .filter(Boolean)
          .sort(),
      });

      await page.goto(`${origin}/#/workspace/agents?surface=chat`);
      await page.waitForLoadState("domcontentloaded");
      await expect(
        page.getByPlaceholder(
          /搜索角色、应用或 Skills|Search roles, apps or Skills/i,
        ),
      ).toBeVisible({ timeout: 20_000 });

      await page.goto(`${origin}/#/workspace/intelligence?surface=chat`);
      await page.waitForLoadState("domcontentloaded");
      const intelligenceSurface = page.getByTestId("intelligence-panel").or(
        page.getByRole("heading", {
          name: /订阅暂时不可用|Subscriptions unavailable/i,
        }),
      );
      await expect(intelligenceSurface).toBeVisible({ timeout: 20_000 });
    }
    expect(originSnapshots).toHaveLength(2);
    expect(originSnapshots[1].status).toMatchObject({
      version: originSnapshots[0].status.version,
    });
    expect(originSnapshots[1].auth).toEqual(originSnapshots[0].auth);
    expect(originSnapshots[1].agentNames).toEqual(
      originSnapshots[0].agentNames,
    );

    await page.goto(
      `${frontendOrigins[0]}/#/workspace/agents/general/chats/new`,
    );
    await page.waitForLoadState("domcontentloaded");
    await expect(page).toHaveURL(
      new RegExp(
        `^http://localhost:${frontendPort}/#\\/workspace\\/realtime\\/new$`,
      ),
    );
    await expect(page.getByTestId("chat-composer-input")).toBeVisible({
      timeout: 20_000,
    });

    await page.getByTestId("chat-tools-trigger").click();
    await page.getByTestId("chat-commands-submenu").hover();
    await expect(page.getByTestId("chat-insert-codex-plan")).toBeVisible();
    await page.getByTestId("chat-insert-codex-plan").click();
    await expect(page.getByTestId("composer-command-prefix")).toHaveText(
      "Plan",
    );
    await expect(page.getByTestId("chat-composer-input")).toHaveValue("");
    await expect(page.getByTestId("chat-send-button")).toBeDisabled();

    const agents = await request.get(`${backendBase}/api/agents`);
    expect(agents.ok()).toBeTruthy();
    const agentsBody = await agents.json();
    expect(Array.isArray(agentsBody)).toBe(true);
    expect(
      agentsBody.some((agent: { name?: string }) => agent.name === "general"),
    ).toBe(true);
  });

  test("realtime new thread sends, persists, and resumes after refresh", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    const origin = frontendOrigins[0];
    const prompt = `Reply directly with one short sentence: full-stack realtime smoke ${Date.now()}`;

    await page.goto(`${origin}/#/workspace/realtime/new`);
    await page.waitForLoadState("domcontentloaded");
    const chatModeToggle = page.getByTestId("chat-mode-toggle");
    await expect(chatModeToggle).toBeVisible({ timeout: 20_000 });
    if ((await chatModeToggle.getAttribute("aria-pressed")) !== "true") {
      await chatModeToggle.click();
    }
    await expect(chatModeToggle).toHaveAttribute("aria-pressed", "true");

    await reactFill(page, '[data-testid="chat-composer-input"]', prompt);
    const threadId = await submitRealtimePrompt(page, prompt);

    await waitForThreadState(page, threadId, (state) => {
      const messages = threadStateMessages(state);
      return messages.some((message) =>
        JSON.stringify(message).includes(prompt),
      );
    });

    await waitForThreadState(page, threadId, (state) => {
      const messages = threadStateMessages(state);
      return messages.length >= 2;
    });

    await page.reload();
    await page.waitForLoadState("domcontentloaded");
    await expect(page.getByTestId("chat-composer-input")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText(prompt, { exact: true })).toBeVisible({
      timeout: 20_000,
    });
  });
});
