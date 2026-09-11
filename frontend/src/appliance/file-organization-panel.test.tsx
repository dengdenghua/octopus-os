import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FileOrganizationPanel } from "./file-organization-panel";
import { downloadFile } from "./files";
import {
  baseUrl,
  deferred,
  jsonResponse,
  organizationPlan,
  organizationPlanList,
  organizationResult,
  planId,
  undoId,
} from "./file-organization.test-support";

vi.mock("./files", () => ({
  downloadFile: vi.fn().mockResolvedValue(undefined),
}));

const key = "echo:files:organization:last:receipts";
type Handler = (
  url: string,
  init?: RequestInit,
) => Response | Promise<Response> | undefined;
function server(handler: Handler = () => undefined) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const response = handler(url, init);
    if (response) return response;
    if (url.startsWith(`${baseUrl}?`))
      return jsonResponse(
        organizationPlanList([], {
          path: new URL(url, "http://test").searchParams.get("path") ?? "",
        }),
      );
    if (url === baseUrl) return jsonResponse(organizationPlan());
    if (url === "/api/appliance/approvals") {
      const body = JSON.parse(String(init?.body));
      return jsonResponse({
        approvalToken: "one-shot-token",
        expiresIn: 90,
        action: body.action,
        target: body.target,
      });
    }
    if (url === `${baseUrl}/${planId}/apply`)
      return jsonResponse(organizationResult());
    return jsonResponse({ detail: "Not Found" }, 404);
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

beforeEach(() => {
  localStorage.clear();
  vi.mocked(downloadFile).mockClear();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function preview() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "生成整理预览" }));
  await screen.findByText("拟移动至：receipts/2026/09/invoice.txt");
  return user;
}
async function approve(
  user: ReturnType<typeof userEvent.setup>,
  label = "确认整理计划",
  confirm = "开始整理",
) {
  await user.click(screen.getByRole("button", { name: label }));
  await user.type(
    screen.getByLabelText("设备管理员密码"),
    "synthetic-password",
  );
  await user.click(screen.getByRole("button", { name: confirm }));
}

function searchablePlan() {
  const original = organizationPlan();
  const invoice = {
    ...original.entries[0]!,
    evidence: {
      ...original.entries[0]!.evidence,
      amountCandidates: [
        { label: "价税合计", value: "128.50", snippet: "价税合计：CNY 128.50" },
      ],
    },
  };
  return organizationPlan({
    entries: [
      invoice,
      {
        ...invoice,
        entryId: "hotel",
        source: "receipts/hotel.pdf",
        target: "receipts/2026/08/hotel.pdf",
        date: "2026-08-12",
        amount: "1234.50",
        evidence: {
          dateCandidates: [
            {
              label: "开票日期",
              value: "2026-08-12",
              snippet: "开票日期：2026年8月12日",
            },
          ],
          amountCandidates: [
            {
              label: "价税合计",
              value: "1234.50",
              snippet: "价税合计：CNY 1,234.50",
            },
          ],
        },
      },
      original.entries[1]!,
    ],
    summary: { ...original.summary, scanned: 3, ready: 2 },
  });
}

function searchableResult() {
  const original = organizationResult();
  return organizationResult({
    counts: { ...original.counts, moved: 2 },
    // Deliberately reverse the plan order: evidence must bind by entryId.
    results: [
      {
        ...original.results[0]!,
        entryId: "hotel",
        source: "receipts/hotel.pdf",
        target: "receipts/2026/08/hotel.pdf",
        actualPath: "receipts/2026/08/hotel.pdf",
      },
      {
        ...original.results[0]!,
        actualPath: "receipts/verified/invoice.txt",
      },
    ],
  });
}

describe("search within the loaded organization plan", () => {
  it.each([
    ["INVOICE.TXT", "invoice.txt"],
    ["2026-08", "hotel.pdf"],
    ["1234.50", "hotel.pdf"],
    ["1,234.50", "hotel.pdf"],
    ["１２８．５０", "invoice.txt"],
    ["2026-09 CNY 128.50", "invoice.txt"],
  ])(
    "locally finds %s without changing the full plan counts",
    async (query, filename) => {
      const fetch = server((url) =>
        url === baseUrl ? jsonResponse(searchablePlan()) : undefined,
      );
      render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
      const user = await preview();
      const calls = fetch.mock.calls.length;
      await user.type(
        screen.getByRole("searchbox", { name: "在本计划中查找" }),
        query,
      );
      const list = within(screen.getByRole("region", { name: "文件整理预览" }));
      expect(list.getAllByRole("article")).toHaveLength(1);
      expect(list.getByText(`receipts/${filename}`)).toBeInTheDocument();
      expect(list.getByText("预览清单：显示 1 / 3 项")).toBeInTheDocument();
      expect(screen.getByText("共扫描 3 个文件")).toBeInTheDocument();
      expect(screen.getByText("· 可处理 2")).toBeInTheDocument();
      expect(screen.getByText(/不搜索其他计划或全盘文件/)).toBeInTheDocument();
      expect(fetch).toHaveBeenCalledTimes(calls);
      expect(localStorage.length).toBe(1);
      expect(localStorage.getItem(key)).toBe(planId);
    },
  );

  it("combines query with pending-review filtering, supports no match and clears only the query", async () => {
    server((url) =>
      url === baseUrl ? jsonResponse(searchablePlan()) : undefined,
    );
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    const user = await preview();
    await user.type(screen.getByRole("searchbox"), "invoice");
    await user.click(screen.getByRole("button", { name: "待确认 1 个" }));
    expect(
      screen.getByText("本计划的待确认文件中没有匹配项"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "清空查找" }));
    expect(screen.getByRole("searchbox")).toHaveValue("");
    const list = within(screen.getByRole("region", { name: "文件整理预览" }));
    expect(list.getAllByRole("article")).toHaveLength(1);
    expect(list.getByText("receipts/unknown.pdf")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "显示全部文件" }));
    await user.type(screen.getByRole("searchbox"), "no-such-invoice");
    expect(
      screen.getByText("本计划的预览清单中没有匹配文件"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认整理计划" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "清空查找" }));
    expect(list.getAllByRole("article")).toHaveLength(3);
  });

  it("approves the entire plan while filtered and keeps evidence and verified-original downloads consistent in results", async () => {
    const fetch = server((url) => {
      if (url === baseUrl) return jsonResponse(searchablePlan());
      if (url.endsWith("/apply")) return jsonResponse(searchableResult());
    });
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    const user = await preview();
    await user.type(screen.getByRole("searchbox"), "128.50");
    const previewList = within(
      screen.getByRole("region", { name: "文件整理预览" }),
    );
    expect(previewList.getByText("金额：128.50 CNY")).toBeInTheDocument();
    expect(previewList.getByText("查看金额依据")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认整理计划" }));
    expect(
      screen.getByText(
        /批准整份计划中可执行的 2 个文件，不限于当前查找或筛选显示的项目/,
      ),
    ).toBeInTheDocument();
    await user.type(
      screen.getByLabelText("设备管理员密码"),
      "synthetic-password",
    );
    await user.click(screen.getByRole("button", { name: "开始整理" }));
    const results = within(
      await screen.findByRole("region", { name: "文件整理结果" }),
    );
    expect(screen.getByRole("searchbox")).toHaveValue("128.50");
    expect(results.getAllByRole("article")).toHaveLength(1);
    expect(results.getByText("执行结果：显示 1 / 2 项")).toBeInTheDocument();
    expect(results.getByText(/已移动 2 · 冲突 0/)).toBeInTheDocument();
    expect(results.getByText("开票日期：2026-09-01")).toBeInTheDocument();
    expect(results.getByText("金额：128.50 CNY")).toBeInTheDocument();
    expect(results.getByText(/价税合计：CNY 128.50/)).toBeInTheDocument();
    expect(results.queryByText("金额：1234.50 CNY")).not.toBeInTheDocument();
    const reveal = results.getByRole("link", { name: "在文件夹中显示" });
    const params = new URLSearchParams(
      reveal.getAttribute("href")!.split("?")[1],
    );
    expect(params.get("desktopAction")).toBe("files.reveal");
    expect(params.get("path")).toBe("receipts/verified/invoice.txt");
    expect(
      fetch.mock.calls.find(([url]) => url.endsWith("/apply"))?.[1]?.body,
    ).toBe("{}");
    expect(
      JSON.parse(
        String(
          fetch.mock.calls.find(
            ([url]) => url === "/api/appliance/approvals",
          )?.[1]?.body,
        ),
      ),
    ).toMatchObject({ target: planId });
    await user.click(results.getByRole("button", { name: "下载原件" }));
    expect(downloadFile).toHaveBeenCalledWith(
      "receipts/verified/invoice.txt",
      "invoice.txt",
      undefined,
      { organizationOriginal: { planId, entryId: "first" } },
    );
    await user.click(screen.getByRole("button", { name: "查看全部预览清单" }));
    const expanded = within(
      screen.getByRole("region", { name: "文件整理预览" }),
    );
    expect(expanded.getAllByRole("article")).toHaveLength(1);
    expect(expanded.getByText("金额：128.50 CNY")).toBeInTheDocument();
    await user.clear(screen.getByRole("searchbox"));
    await user.type(screen.getByRole("searchbox"), "1,234.50");
    expect(results.getByText("receipts/hotel.pdf")).toBeInTheDocument();
    expect(results.getByText("开票日期：2026-08-12")).toBeInTheDocument();
    expect(results.getByText(/价税合计：CNY 1,234.50/)).toBeInTheDocument();
    expect(expanded.getByText("receipts/hotel.pdf")).toBeInTheDocument();
    await user.clear(screen.getByRole("searchbox"));
    await user.type(screen.getByRole("searchbox"), "unknown.pdf");
    expect(
      results.getByText(/本计划的执行结果中没有匹配文件/),
    ).toBeInTheDocument();
    expect(expanded.getByText("receipts/unknown.pdf")).toBeInTheDocument();
    expect(
      results.queryByRole("button", { name: "下载原件" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "清空查找" }));
    expect(results.getAllByRole("article")).toHaveLength(2);
  });

  it("never borrows evidence or an original location from another entry with the same filename", async () => {
    server((url) =>
      url.endsWith("/apply")
        ? jsonResponse(
            organizationResult({
              state: "uncertain",
              executionComplete: false,
              counts: {
                ...organizationResult().counts,
                moved: 0,
                uncertain: 1,
              },
              results: [
                {
                  ...organizationResult().results[0]!,
                  entryId: "unmatched",
                  status: "uncertain",
                  committed: null,
                  actualPath: null,
                },
              ],
            }),
          )
        : undefined,
    );
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    const user = await preview();
    await approve(user);
    const results = within(
      await screen.findByRole("region", { name: "文件整理结果" }),
    );
    expect(results.getByText("receipts/invoice.txt")).toBeInTheDocument();
    expect(results.queryByText(/开票日期：/)).not.toBeInTheDocument();
    expect(results.queryByText(/金额：/)).not.toBeInTheDocument();
    expect(
      results.queryByRole("button", { name: "下载原件" }),
    ).not.toBeInTheDocument();
    await user.type(screen.getByRole("searchbox"), "128.50");
    expect(results.queryByRole("article")).not.toBeInTheDocument();
    expect(downloadFile).not.toHaveBeenCalled();
  });

  it("clears the query when choosing a different plan and when changing directory", async () => {
    const nextId = "d".repeat(64);
    const next = organizationPlan({
      planId: nextId,
      approval: { action: "files.organize.apply", target: nextId },
    });
    server((url) => {
      if (url === `${baseUrl}?path=receipts`)
        return jsonResponse(organizationPlanList([next]));
      if (url === `${baseUrl}/${nextId}`) return jsonResponse(next);
      if (url === `${baseUrl}/${undoId}`)
        return jsonResponse(
          organizationPlan({
            ...next,
            planId: undoId,
            path: "other",
            approval: { action: "files.organize.apply", target: undoId },
          }),
        );
    });
    const view = render(
      <FileOrganizationPanel path="receipts" onClose={vi.fn()} />,
    );
    const user = await preview();
    await user.type(screen.getByRole("searchbox"), "no-match");
    await user.click(
      screen.getByRole("button", { name: `查看整理计划 ${nextId}` }),
    );
    await screen.findByText("拟移动至：receipts/2026/09/invoice.txt");
    expect(screen.getByRole("searchbox")).toHaveValue("");
    expect(localStorage.getItem(key)).toBe(nextId);
    await user.type(screen.getByRole("searchbox"), "still-no-match");
    localStorage.setItem("echo:files:organization:last:other", undoId);
    view.rerender(<FileOrganizationPanel path="other" onClose={vi.fn()} />);
    await screen.findByText("拟移动至：receipts/2026/09/invoice.txt");
    expect(screen.getByRole("searchbox")).toHaveValue("");
  });
});

describe("NAS document organization flow", () => {
  it("rejects a restored result whose direction differs from the saved plan", async () => {
    localStorage.setItem(key, planId);
    server((url) => {
      if (url === `${baseUrl}/${planId}`)
        return jsonResponse(organizationPlan());
      if (url.endsWith("/result"))
        return jsonResponse(organizationResult({ direction: "undo" }));
    });
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    await screen.findByText("执行记录与计划不一致，请重新读取");
    expect(
      screen.queryByRole("region", { name: "文件整理结果" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "下载原件" }),
    ).not.toBeInTheDocument();
  });
  it("requires a new approval to finish records without presenting verified file moves as fully completed", async () => {
    let applies = 0;
    const fetch = server((url) => {
      if (url.endsWith("/apply")) {
        applies += 1;
        return jsonResponse(
          applies === 1
            ? organizationResult({
                state: "partial",
                executionComplete: false,
                finalizationPending: true,
                auditRecorded: false,
                taskRecorded: true,
                receiptRecorded: true,
              })
            : organizationResult({
                finalizationPending: false,
                auditRecorded: true,
                taskRecorded: true,
                receiptRecorded: true,
              }),
        );
      }
    });
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    const user = await preview();
    await approve(user);
    await screen.findByText("文件已处理，记录尚未完成");
    expect(
      screen.getByText(/重新审批补齐记录，已提交文件不会重复移动/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("可确认的文件已处理，其余文件待确认"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "停止后续处理" }),
    ).not.toBeInTheDocument();
    await approve(user, "重新审批补齐记录");
    await screen.findByText("可确认的文件已处理，其余文件待确认");
    expect(
      fetch.mock.calls.filter(([url]) => url === "/api/appliance/approvals"),
    ).toHaveLength(2);
    expect(applies).toBe(2);
  });

  it("does not retry finalization while any file outcome remains unknown", async () => {
    server((url) =>
      url.endsWith("/apply")
        ? jsonResponse(
            organizationResult({
              state: "uncertain",
              executionComplete: false,
              finalizationPending: true,
              counts: {
                ...organizationResult().counts,
                moved: 0,
                uncertain: 1,
              },
              results: [
                {
                  ...organizationResult().results[0]!,
                  status: "uncertain",
                  committed: null,
                  actualPath: null,
                },
              ],
            }),
          )
        : undefined,
    );
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    const user = await preview();
    await approve(user);
    await screen.findByText("部分结果待核实");
    expect(
      screen.queryByRole("button", { name: "重新审批补齐记录" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/文件结果已核实/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "下载原件" }),
    ).not.toBeInTheDocument();
  });
  it("discovers an Agent-created plan without selecting it, then requires independent approval", async () => {
    const fetch = server((url) => {
      if (url === `${baseUrl}?path=receipts`)
        return jsonResponse(organizationPlanList([organizationPlan()]));
      if (url === `${baseUrl}/${planId}`)
        return jsonResponse(organizationPlan());
    });
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    const user = userEvent.setup();
    const choice = await screen.findByRole("button", {
      name: `查看整理计划 ${planId}`,
    });
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      `${baseUrl}?path=receipts`,
    ]);
    expect(localStorage.length).toBe(0);
    expect(
      screen.queryByRole("button", { name: "确认整理计划" }),
    ).not.toBeInTheDocument();
    await user.click(choice);
    await screen.findByText("拟移动至：receipts/2026/09/invoice.txt");
    expect(fetch.mock.calls.every(([, init]) => !init?.method)).toBe(true);
    expect(localStorage.getItem(key)).toBe(planId);
    await approve(user);
    await screen.findByText("可确认的文件已处理，其余文件待确认");
    expect(
      fetch.mock.calls.filter(([url]) => url.endsWith("/apply")),
    ).toHaveLength(1);
  });

  it("allows exact-ID discovery when the bounded list is incomplete without selecting another directory", async () => {
    const fetch = server((url) => {
      if (url.startsWith(`${baseUrl}?`))
        return jsonResponse(organizationPlanList([], { complete: false }));
      if (url === `${baseUrl}/${planId}`)
        return jsonResponse(organizationPlan({ path: "other" }));
      if (url === `${baseUrl}/${undoId}`)
        return jsonResponse(
          organizationPlan({
            planId: undoId,
            approval: { action: "files.organize.apply", target: undoId },
          }),
        );
    });
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    const user = userEvent.setup();
    await screen.findByText(/仅显示本次可检索范围内/);
    const input = screen.getByLabelText("完整计划编号");
    await user.type(input, planId);
    await user.click(screen.getByRole("button", { name: "按编号查看" }));
    await screen.findByText("此计划不属于当前目录，请打开对应目录后查看");
    expect(localStorage.length).toBe(0);
    expect(
      fetch.mock.calls.some(([url]) => url === `${baseUrl}/${planId}/result`),
    ).toBe(false);
    await user.clear(input);
    await user.type(input, undoId);
    await user.click(screen.getByRole("button", { name: "按编号查看" }));
    await screen.findByText("拟移动至：receipts/2026/09/invoice.txt");
    expect(localStorage.getItem(key)).toBe(undoId);
    expect(fetch.mock.calls.every(([, init]) => !init?.method)).toBe(true);
  });

  it("clears summaries on a denied refresh and never treats the failure as an empty list", async () => {
    let denied = false;
    server((url) => {
      if (url.startsWith(`${baseUrl}?`))
        return denied
          ? jsonResponse(
              { detail: { error: "access_denied", message: "目录权限已变化" } },
              403,
            )
          : jsonResponse(organizationPlanList([organizationPlan()]));
    });
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    await screen.findByRole("button", { name: `查看整理计划 ${planId}` });
    denied = true;
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "刷新计划列表" }));
    await screen.findByText("当前账户无权执行此操作，请重新检查目录权限");
    expect(
      screen.queryByRole("button", { name: `查看整理计划 ${planId}` }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("本次未找到可显示的计划。"),
    ).not.toBeInTheDocument();
  });

  it("ignores a selected plan arriving after the directory changed", async () => {
    const pending = deferred<Response>();
    const fetch = server((url) => {
      if (url === `${baseUrl}?path=receipts`)
        return jsonResponse(organizationPlanList([organizationPlan()]));
      if (url === `${baseUrl}/${planId}`) return pending.promise;
    });
    const view = render(
      <FileOrganizationPanel path="receipts" onClose={vi.fn()} />,
    );
    await userEvent
      .setup()
      .click(
        await screen.findByRole("button", { name: `查看整理计划 ${planId}` }),
      );
    expect(screen.getByRole("button", { name: "生成整理预览" })).toBeDisabled();
    view.rerender(<FileOrganizationPanel path="other" onClose={vi.fn()} />);
    await screen.findByText("本次未找到可显示的计划。");
    await act(async () => pending.resolve(jsonResponse(organizationPlan())));
    expect(
      screen.queryByText("拟移动至：receipts/2026/09/invoice.txt"),
    ).not.toBeInTheDocument();
    expect(localStorage.length).toBe(0);
    expect(fetch.mock.calls.some(([url]) => url.endsWith("/result"))).toBe(
      false,
    );
  });
  it("previews exact destinations and evidence without moving; separately approves apply and undo", async () => {
    const undo = organizationPlan({
      planId: undoId,
      direction: "undo",
      sourcePlanId: planId,
      approval: { action: "files.organize.undo", target: undoId },
      entries: [
        {
          ...organizationPlan().entries[0]!,
          source: "receipts/2026/09/invoice.txt",
          target: "receipts/invoice.txt",
        },
      ],
      summary: { ...organizationPlan().summary, scanned: 1, needsReview: 0 },
    });
    const fetch = server((url) => {
      if (url.endsWith("/undo-plan")) return jsonResponse(undo);
      if (url === `${baseUrl}/${undoId}/apply`)
        return jsonResponse(
          organizationResult({
            planId: undoId,
            operationId: undoId,
            direction: "undo",
            reviewCount: 0,
            results: [
              {
                ...organizationResult().results[0]!,
                source: "receipts/2026/09/invoice.txt",
                target: "receipts/invoice.txt",
                actualPath: "receipts/invoice.txt",
              },
            ],
          }),
        );
    });
    const onChanged = vi.fn();
    render(
      <FileOrganizationPanel
        path="receipts"
        onClose={vi.fn()}
        onChanged={onChanged}
      />,
    );
    const user = await preview();
    expect(
      fetch.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(1);
    expect(
      screen.getByText("开票日期：2026年9月1日", { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getByText(/未取得可用文字/)).toBeInTheDocument();
    expect(localStorage.getItem(key)).toBe(planId);
    expect(localStorage.length).toBe(1);
    await approve(user);
    await screen.findByText("可确认的文件已处理，其余文件待确认");
    expect(
      JSON.parse(
        String(
          fetch.mock.calls.find(
            ([url]) => url === "/api/appliance/approvals",
          )?.[1]?.body,
        ),
      ),
    ).toEqual({
      action: "files.organize.apply",
      target: planId,
      password: "synthetic-password",
    });
    expect(
      fetch.mock.calls.find(([url]) => url.endsWith("/apply"))?.[1],
    ).toMatchObject({
      body: "{}",
      headers: { "X-Echo-Approval": "one-shot-token" },
    });
    await user.click(screen.getByRole("button", { name: "待确认 1 个" }));
    expect(screen.getByText(/未取得可用文字/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "显示全部文件" }));
    expect(
      screen.getByText("拟移动至：receipts/2026/09/invoice.txt"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "下载原件" }));
    expect(downloadFile).toHaveBeenCalledWith(
      "receipts/2026/09/invoice.txt",
      "invoice.txt",
      undefined,
      { organizationOriginal: { planId, entryId: "first" } },
    );
    await user.click(screen.getByRole("button", { name: "预览撤销本次操作" }));
    await screen.findByText(
      "撤销仅恢复文件原位置，整理时创建的空年月目录会保留。",
    );
    await screen.findByText("拟移动至：receipts/invoice.txt");
    expect(
      fetch.mock.calls.filter(([url]) => url.endsWith("/apply")),
    ).toHaveLength(1);
    await approve(user, "确认撤销计划", "执行撤销");
    await screen.findByText("本次撤销已完成");
    const approvals = fetch.mock.calls.filter(
      ([url]) => url === "/api/appliance/approvals",
    );
    expect(JSON.parse(String(approvals[1]?.[1]?.body))).toMatchObject({
      action: "files.organize.undo",
      target: undoId,
    });
    expect(localStorage.getItem(key)).toBe(undoId);
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it.each([
    { scanComplete: false },
    { blockers: ["scan_incomplete"] },
    { ready: false },
  ])(
    "does not allow approval of an incomplete or blocked plan: %j",
    async (overrides) => {
      const fetch = server((url) =>
        url === baseUrl ? jsonResponse(organizationPlan(overrides)) : undefined,
      );
      render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
      await preview();
      expect(
        screen.getByRole("button", { name: "确认整理计划" }),
      ).toBeDisabled();
      expect(
        fetch.mock.calls.filter(([, init]) => init?.method === "POST"),
      ).toHaveLength(1);
    },
  );

  it("keeps password rejection retryable and never calls apply", async () => {
    const fetch = server((url) =>
      url === "/api/appliance/approvals"
        ? jsonResponse({ detail: "denied" }, 403)
        : undefined,
    );
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    await approve(await preview());
    await screen.findByText("设备管理员密码不正确，操作未执行");
    expect(fetch.mock.calls.some(([url]) => url.endsWith("/apply"))).toBe(
      false,
    );
    expect(screen.getByRole("button", { name: "开始整理" })).toBeEnabled();
  });

  it("shows conflicts and retries the same plan only after a fresh approval", async () => {
    let attempts = 0;
    const mixed = organizationResult({
      state: "partial",
      counts: {
        moved: 0,
        conflicts: 1,
        failed: 0,
        pending: 0,
        uncertain: 0,
        skipped: 0,
      },
      results: [
        {
          ...organizationResult().results[0]!,
          status: "conflict",
          committed: false,
          reason: "destination_exists",
          actualPath: "receipts/invoice.txt",
        },
      ],
    });
    const fetch = server((url) =>
      url.endsWith("/apply")
        ? jsonResponse(++attempts === 1 ? mixed : organizationResult())
        : undefined,
    );
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    const user = await preview();
    await approve(user);
    await screen.findByText("目标已有同名文件，未覆盖");
    expect(screen.queryByText("本次整理已完成")).not.toBeInTheDocument();
    await approve(user, "重试未完成项");
    await screen.findByText("可确认的文件已处理，其余文件待确认");
    expect(
      fetch.mock.calls.filter(([url]) => url === "/api/appliance/approvals"),
    ).toHaveLength(2);
    expect(
      fetch.mock.calls
        .filter(([url]) => url.endsWith("/apply"))
        .map(([url]) => url),
    ).toEqual([`${baseUrl}/${planId}/apply`, `${baseUrl}/${planId}/apply`]);
  });

  it("does not invent original links or offer retries for uncertain locations", async () => {
    server((url) =>
      url.endsWith("/apply")
        ? jsonResponse(
            organizationResult({
              state: "uncertain",
              counts: {
                moved: 0,
                conflicts: 0,
                failed: 0,
                pending: 0,
                uncertain: 1,
                skipped: 0,
              },
              results: [
                {
                  ...organizationResult().results[0]!,
                  status: "uncertain",
                  committed: null,
                  actualPath: null,
                  recoveryPaths: ["receipts/.pending-recovery"],
                  reason: "recovery_required",
                },
              ],
            }),
          )
        : undefined,
    );
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    await approve(await preview());
    await screen.findByText("部分结果待核实");
    expect(
      screen.queryByRole("button", { name: "下载原件" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "重试未完成项" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("保留的恢复副本：receipts/.pending-recovery"),
    ).toBeInTheDocument();
    expect(downloadFile).not.toHaveBeenCalled();
  });

  it("recovers a committed result after the apply response is lost", async () => {
    server((url) => {
      if (url.endsWith("/apply"))
        return Promise.reject(new TypeError("connection closed"));
      if (url.endsWith("/result")) return jsonResponse(organizationResult());
    });
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    await approve(await preview());
    await screen.findByText("可确认的文件已处理，其余文件待确认");
    expect(
      screen.queryByRole("button", { name: "重试未完成项" }),
    ).not.toBeInTheDocument();
    expect(localStorage.getItem(key)).toBe(planId);
  });

  it("preserves an explicit apply conflict and permits confirmation again when no receipt exists", async () => {
    server((url) =>
      url.endsWith("/apply")
        ? jsonResponse(
            {
              detail: {
                error: "plan_busy",
                message: "计划正在被其他请求检查，请稍后重试",
              },
            },
            409,
          )
        : undefined,
    );
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    await approve(await preview());
    await screen.findByText("计划正在被其他请求检查，请稍后重试");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "确认整理计划" }),
      ).toBeEnabled(),
    );
    expect(screen.queryByText("本次整理已完成")).not.toBeInTheDocument();
  });

  it("reopens with server-authorized receipts, storing only the plan ID", async () => {
    localStorage.setItem(key, planId);
    const fetch = server((url) => {
      if (url === `${baseUrl}/${planId}`)
        return jsonResponse(organizationPlan());
      if (url.endsWith("/result")) return jsonResponse(organizationResult());
    });
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    await screen.findByText("可确认的文件已处理，其余文件待确认");
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      `${baseUrl}?path=receipts`,
      `${baseUrl}/${planId}`,
      `${baseUrl}/${planId}/result`,
    ]);
    expect(localStorage.getItem(key)).toBe(planId);
    expect(fetch.mock.calls.every(([, init]) => !init?.method)).toBe(true);
  });

  it("does not treat a local discovery pointer as permission to read another member's files", async () => {
    localStorage.setItem(key, planId);
    server((url) =>
      url === `${baseUrl}/${planId}`
        ? jsonResponse({ detail: "forbidden" }, 403)
        : undefined,
    );
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    await screen.findByText("当前账户无权执行此操作，请重新检查目录权限");
    expect(screen.queryByText("receipts/invoice.txt")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "确认整理计划" }),
    ).not.toBeInTheDocument();
  });

  it("ignores old directory preview responses after navigation", async () => {
    const old = deferred<Response>();
    server((url, init) =>
      url === baseUrl
        ? JSON.parse(String(init?.body)).path === "receipts"
          ? old.promise
          : jsonResponse(
              organizationPlan({
                path: "new",
                planId: undoId,
                approval: { action: "files.organize.apply", target: undoId },
                entries: [],
              }),
            )
        : undefined,
    );
    const view = render(
      <FileOrganizationPanel path="receipts" onClose={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "生成整理预览" }));
    view.rerender(<FileOrganizationPanel path="new" onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "生成整理预览" }));
    await screen.findByText("此目录没有可整理文件");
    await act(async () => {
      old.resolve(jsonResponse(organizationPlan()));
    });
    expect(screen.queryByText("receipts/invoice.txt")).not.toBeInTheDocument();
    expect(localStorage.getItem(key)).toBeNull();
  });

  it("does not execute a late approval after the panel is closed", async () => {
    const approval = deferred<Response>();
    const fetch = server((url) =>
      url === "/api/appliance/approvals" ? approval.promise : undefined,
    );
    const view = render(
      <FileOrganizationPanel path="receipts" onClose={vi.fn()} />,
    );
    await approve(await preview());
    view.unmount();
    await act(async () => {
      approval.resolve(
        jsonResponse({ approvalToken: "late-token", expiresIn: 90 }),
      );
    });
    expect(fetch.mock.calls.some(([url]) => url.endsWith("/apply"))).toBe(
      false,
    );
  });

  it("cancels only future work and keeps committed moves; duplicate cancellation is disabled", async () => {
    const applied = deferred<Response>();
    const stopped = deferred<Response>();
    const fetch = server((url) => {
      if (url.endsWith("/apply")) return applied.promise;
      if (url.endsWith("/cancel")) return stopped.promise;
    });
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    const user = await preview();
    await approve(user);
    const cancel = await screen.findByRole("button", { name: "停止后续处理" });
    await user.dblClick(cancel);
    expect(
      fetch.mock.calls.filter(([url]) => url.endsWith("/cancel")),
    ).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "正在请求停止…" }),
    ).toBeDisabled();
    await act(async () => {
      stopped.resolve(
        jsonResponse(
          organizationResult({
            state: "cancelled",
            counts: { ...organizationResult().counts, pending: 1 },
          }),
        ),
      );
    });
    await screen.findByText("已停止后续处理");
    expect(screen.getByText(/已完成的移动保留/)).toBeInTheDocument();
    await act(async () => {
      applied.resolve(
        jsonResponse(
          organizationResult({ state: "running", executionComplete: false }),
        ),
      );
    });
    expect(screen.getByText("已停止后续处理")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "停止后续处理" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "预览撤销本次操作" }),
    ).toBeEnabled();
  });

  it("does not let an old in-flight status read overwrite the final apply receipt", async () => {
    const applied = deferred<Response>();
    const poll = deferred<Response>();
    const fetch = server((url) => {
      if (url.endsWith("/apply")) return applied.promise;
      if (url.endsWith("/result")) return poll.promise;
    });
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    const user = await preview();
    await approve(user);
    await user.click(screen.getByRole("button", { name: "刷新结果" }));
    expect(fetch.mock.calls.some(([url]) => url.endsWith("/result"))).toBe(
      true,
    );
    await act(async () => {
      applied.resolve(jsonResponse(organizationResult()));
    });
    await screen.findByText("可确认的文件已处理，其余文件待确认");
    await act(async () => {
      poll.resolve(
        jsonResponse(
          organizationResult({ state: "running", executionComplete: false }),
        ),
      );
    });
    expect(screen.queryByText("正在处理文件")).not.toBeInTheDocument();
    expect(
      screen.getByText("可确认的文件已处理，其余文件待确认"),
    ).toBeInTheDocument();
  });

  it("exports the actual result receipt without persisting it in browser storage", async () => {
    server();
    const create = vi.fn().mockReturnValue("blob:receipt");
    const revoke = vi.fn();
    vi.stubGlobal(
      "URL",
      Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }),
    );
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    const user = await preview();
    await approve(user);
    await screen.findByText("可确认的文件已处理，其余文件待确认");
    await user.click(screen.getByRole("button", { name: "下载结果清单" }));
    expect(create).toHaveBeenCalledWith(expect.any(Blob));
    expect(click).toHaveBeenCalledOnce();
    expect(localStorage.length).toBe(1);
    expect(localStorage.getItem(key)).toBe(planId);
  });

  it("automatically polls running work until the authoritative terminal receipt arrives", async () => {
    const fetch = server((url) => {
      if (url.endsWith("/apply"))
        return jsonResponse(
          organizationResult({
            state: "running",
            executionComplete: false,
            counts: { ...organizationResult().counts, moved: 0, pending: 1 },
          }),
        );
      if (url.endsWith("/result")) return jsonResponse(organizationResult());
    });
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    await preview();
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const user = userEvent.setup();
    await approve(user);
    expect(screen.getByText("正在处理文件")).toBeInTheDocument();
    expect(
      fetch.mock.calls.filter(([url]) => url.endsWith("/result")),
    ).toHaveLength(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(
      screen.getByText("可确认的文件已处理，其余文件待确认"),
    ).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(
      fetch.mock.calls.filter(([url]) => url.endsWith("/result")),
    ).toHaveLength(1);
  });

  it("keeps a failed cancellation visible while running status is refreshed", async () => {
    server((url) => {
      if (url.endsWith("/apply") || url.endsWith("/result"))
        return jsonResponse(
          organizationResult({ state: "running", executionComplete: false }),
        );
      if (url.endsWith("/cancel"))
        return jsonResponse(
          {
            detail: {
              error: "cancel_unavailable",
              message: "当前执行暂时无法停止，请继续核实结果",
            },
          },
          409,
        );
    });
    render(<FileOrganizationPanel path="receipts" onClose={vi.fn()} />);
    const user = await preview();
    await approve(user);
    await user.click(screen.getByRole("button", { name: "停止后续处理" }));
    await screen.findByText("当前执行暂时无法停止，请继续核实结果");
    await user.click(screen.getByRole("button", { name: "刷新结果" }));
    expect(
      screen.getByText("当前执行暂时无法停止，请继续核实结果"),
    ).toBeInTheDocument();
    expect(screen.queryByText("已停止后续处理")).not.toBeInTheDocument();
  });
});
