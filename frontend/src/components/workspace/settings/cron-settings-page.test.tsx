import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { CronSettingsPage } from "./cron-settings-page";

const toastMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: toastMocks }));

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock.mockImplementation(() => jsonResponse([]));
});

describe("CronSettingsPage", () => {
  it("uses persistent labels, schedule guidance, and inline validation", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CronSettingsPage />, { locale: "zh-CN" });

    await screen.findByText("暂无定时任务");
    await user.click(screen.getByRole("button", { name: "添加任务" }));

    expect(screen.getByRole("textbox", { name: "任务名称" })).toHaveAttribute(
      "placeholder",
      "例如：每小时同步报告",
    );
    expect(screen.getByRole("textbox", { name: "执行命令" })).toHaveAttribute(
      "placeholder",
      "例如：python scripts/report.py",
    );
    expect(
      screen.getByRole("textbox", { name: "执行频率（Cron）" }),
    ).toHaveValue("0 * * * *");
    expect(
      screen.getByText(
        "按当前设备时区执行，依次填写分钟、小时、日期、月份和星期。",
      ),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "创建" }));
    expect(screen.getByText("任务名称不能为空")).toBeInTheDocument();
    expect(screen.getByText("执行命令不能为空")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "任务名称" })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("submits the form from the keyboard with trimmed values", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") {
          return jsonResponse({ ok: true });
        }
        return jsonResponse([]);
      },
    );

    renderWithProviders(<CronSettingsPage />, { locale: "zh-CN" });
    await screen.findByText("暂无定时任务");
    await user.click(screen.getByRole("button", { name: "添加任务" }));

    await user.type(
      screen.getByRole("textbox", { name: "任务名称" }),
      "  每小时报告  ",
    );
    const command = screen.getByRole("textbox", { name: "执行命令" });
    await user.type(command, "  python report.py  ");
    command.focus();
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([, init]) => init?.method === "POST"),
      ).toBe(true);
    });
    const postCall = fetchMock.mock.calls.find(
      ([, init]) => init?.method === "POST",
    );
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
      name: "每小时报告",
      command: "python report.py",
      cron_expression: "0 * * * *",
    });
    expect(toastMocks.success).toHaveBeenCalledWith("定时任务已创建");
  });

  it("shows a localized recoverable load error instead of backend text", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(() => jsonResponse({}, 500));

    renderWithProviders(<CronSettingsPage />, { locale: "zh-CN" });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("加载定时任务失败");
    expect(
      screen.queryByText("Failed to load cron jobs: 500"),
    ).not.toBeInTheDocument();

    await user.click(within(alert).getByRole("button", { name: "刷新" }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("localizes task status and deletes names through an encoded URL", async () => {
    const user = userEvent.setup();
    let jobs: unknown[] = [
      {
        name: "日报 / 上海",
        command: "python report.py",
        cron_expression: "0 9 * * *",
        last_status: "completed",
      },
    ];
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "DELETE") {
          jobs = [];
          return jsonResponse({ ok: true });
        }
        return jsonResponse(jobs);
      },
    );

    renderWithProviders(<CronSettingsPage />, { locale: "zh-CN" });

    await screen.findByText("日报 / 上海");
    expect(screen.getByText("上次: 已完成")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", {
        name: "删除定时任务：日报 / 上海",
      }),
    );

    const dialog = await screen.findByRole("dialog", {
      name: "删除定时任务",
    });
    await user.click(within(dialog).getByRole("button", { name: "删除" }));

    await waitFor(() => {
      const deleteCall = fetchMock.mock.calls.find(
        ([, init]) => init?.method === "DELETE",
      );
      expect(String(deleteCall?.[0])).toContain(
        "/api/cron/%E6%97%A5%E6%8A%A5%20%2F%20%E4%B8%8A%E6%B5%B7",
      );
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.getByText("暂无定时任务")).toBeInTheDocument();
    });
  });
});
