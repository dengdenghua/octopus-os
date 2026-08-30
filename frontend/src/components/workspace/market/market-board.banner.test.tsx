import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { MarketBoard } from "./market-board";

const BANNER_KEY = "echo.market.assets-banner-dismissed.v1";

describe("MarketBoard 资产引导横幅", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("默认显示横幅与前往按钮", async () => {
    renderWithProviders(<MarketBoard />);
    expect(
      screen.getByText(/这里是社区好物/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /前往统一资产/ }),
    ).toBeInTheDocument();
  });

  it("点击关闭后横幅消失并持久化", async () => {
    renderWithProviders(<MarketBoard />);
    await userEvent.click(screen.getByRole("button", { name: "关闭提示" }));
    expect(screen.queryByText(/这里是社区好物/)).not.toBeInTheDocument();
    expect(window.localStorage.getItem(BANNER_KEY)).toBe("1");
  });

  it("已关闭过则不再显示", () => {
    window.localStorage.setItem(BANNER_KEY, "1");
    renderWithProviders(<MarketBoard />);
    expect(screen.queryByText(/这里是社区好物/)).not.toBeInTheDocument();
  });
});
