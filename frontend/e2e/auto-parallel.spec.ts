import { expect, test } from "./fixtures";

/**
 * E2E: 自动拆解并行（auto-parallel）→ workbench 渲染并行子任务 tile。
 *
 * Prerequisites: backend on :8000 (config.local.yaml, 真实模型 kimi-k3),
 * frontend on :3000。
 *
 * 触发条件：goal >= 40 字符且命中并行信号（分别/、 等），主循环 Phase 4.6
 * 通过 plan_auto_parallel 拆成 >=2 个子查询，run_auto_parallel 用 orchestrator
 * 并行 dispatch，桥接层把 task_update 转成 SubagentItem 并发射 item/* 事件，
 * 前端 workbench 据此渲染并行子任务 tile。
 *
 * 断言目标：workbench 面板（aria-label「Agent 工作台」）内出现多个 subagent
 * tile（auto-parallel 的子任务 subagent_name 固定为 general-purpose）。
 */
test.describe("Auto-parallel workbench rendering", () => {
  test("sends a parallel goal and workbench renders subagent tiles", async ({
    authedPage: page,
  }) => {
    test.setTimeout(120_000);
    await page.goto("/#/workspace/realtime/new?agent=general");
    await page.waitForLoadState("domcontentloaded");

    // 输入框就绪
    const input = page.getByTestId("chat-composer-input");
    await expect(input).toBeVisible({ timeout: 20_000 });

    // 命中 plan_auto_parallel 启发式：>=40 字符 + 「分别」/「、」平行信号
    const goal =
      "请分别调研A股科技板块、美股科技板块、港股科技板块的近期市场表现，" +
      "并各自给出三只值得关注的代表性股票及简要理由。";
    await input.fill(goal);
    await input.press("Enter");

    // general 单 agent 非协作模式下 workbench 默认折叠，需手动打开右侧面板。
    // header 的 toggle 按钮 aria-label 为 panelToggle.open（zh-CN「打开右侧窗口」/ en「Open right panel」）。
    const openRight = page.getByRole("button", {
      name: /Open right panel|打开右侧窗口/,
    });
    await expect(openRight).toBeVisible({ timeout: 20_000 });
    await openRight.click();

    // workbench 面板出现（empty shell / kanban 都挂 aria-label「Agent 工作台」/「Agent workbench」）
    const workbench = page.getByRole("region", {
      name: /Agent 工作台|Agent workbench/,
    });
    await expect(workbench).toBeVisible({ timeout: 30_000 });

    // 子智能体（subagents）区块默认收起，且仅在 agentTiles 非空时才渲染。
    // 点击「Subagents / 子智能体」标题展开，让 auto-parallel 的 subagent tile 文本可见。
    const subagentsToggle = workbench.getByRole("button", {
      name: /Subagents|子智能体/,
    });
    await expect(subagentsToggle).toBeVisible({ timeout: 90_000 });
    await subagentsToggle.click();

    // 等待 auto-parallel 的 subagent tile 渲染进 workbench：
    // orchestrator 拆出的子任务 subagent_name 固定为 general-purpose，
    // 出现 >=2 个即证明并行子任务 tile 已渲染（而非停留在空态/仅在跑主循环）。
    await expect
      .poll(
        async () => {
          const text = await workbench.innerText();
          return (text.match(/general-purpose/g) ?? []).length;
        },
        { timeout: 90_000, intervals: [2_000, 3_000, 5_000] },
      )
      .toBeGreaterThanOrEqual(2);
  });
});
