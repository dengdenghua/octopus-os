import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { BROWSER_HOME_URL, BrowserStoreProvider } from "./browser-store";
import { TabBar } from "./tab-bar";

const pageTabs = Array.from({ length: 6 }, (_, index) => ({
  id: `page-${index + 1}`,
  url: `https://example.com/page-${index + 1}`,
  title: `项目页面 ${index + 1}`,
  isLoading: false,
  device: "desktop",
}));

function seedCrowdedSession() {
  window.localStorage.setItem(
    "echo:browser-state",
    JSON.stringify({
      tabs: [
        {
          id: "home",
          url: BROWSER_HOME_URL,
          title: "AI 浏览器桌面",
          isLoading: false,
          device: "desktop",
        },
        {
          id: "new-home",
          url: BROWSER_HOME_URL,
          title: "AI 浏览器桌面",
          isLoading: false,
          device: "desktop",
        },
        ...pageTabs,
      ],
      closedTabs: [],
      activeId: "page-6",
      copilotOpen: false,
      copilotWidth: 380,
      homeSeeded: true,
    }),
  );
}

describe("browser multi-tab bar", () => {
  beforeEach(() => {
    window.localStorage.clear();
    seedCrowdedSession();
  });

  it("keeps home, tab search and new-tab controls outside the scroll strip", () => {
    renderWithProviders(
      <BrowserStoreProvider>
        <TabBar />
      </BrowserStoreProvider>,
    );

    const scrollStrip = screen.getByTestId("browser-scrollable-tabs");
    const home = screen.getByTestId("browser-home-tab");
    const tabSearch = screen.getByTestId("browser-all-tabs-trigger");
    const newTab = screen.getByTestId("browser-new-tab");

    expect(scrollStrip).not.toContainElement(home);
    expect(scrollStrip).not.toContainElement(tabSearch);
    expect(scrollStrip).not.toContainElement(newTab);
    expect(screen.getAllByTestId("browser-home-tab")).toHaveLength(1);
    expect(within(scrollStrip).getAllByTestId("browser-page-tab")).toHaveLength(
      7,
    );
  });

  it("searches all open tabs and activates a result", () => {
    renderWithProviders(
      <BrowserStoreProvider>
        <TabBar />
      </BrowserStoreProvider>,
    );

    fireEvent.click(screen.getByTestId("browser-all-tabs-trigger"));
    const search = screen.getByPlaceholderText(/Search tabs|搜索标签页/);
    fireEvent.change(search, { target: { value: "项目页面 2" } });

    const result = within(screen.getByRole("dialog")).getByText("项目页面 2");
    fireEvent.click(result);

    expect(screen.queryByPlaceholderText(/Search tabs|搜索标签页/)).toBeNull();
    expect(screen.getByRole("button", { name: "项目页面 2" })).toHaveAttribute(
      "data-active",
      "true",
    );
  });
});
