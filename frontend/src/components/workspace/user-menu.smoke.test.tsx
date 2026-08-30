/* Implementation note. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// ─── Mocks · isolate UserMenu from the real auth / query layers ──
//
// Each describe below overrides ``useAuth`` to return a different
// shape (loading · guest · logged-in) so we exercise every return
// branch. The query hooks stay simple — they just need to return
// a ``.data`` shape the memo can destructure without throwing.

type AuthShape = {
  isLoading: boolean;
  user: { user_id: string; username: string } | null;
  authStatus: { enabled: boolean } | null;
  logout: () => Promise<void>;
  isAuthenticated: boolean;
};

const mockAuth = { current: null as AuthShape | null };

vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => mockAuth.current,
}));
vi.mock("@/core/oct/hooks", () => ({
  useOctLink: () => ({ data: null }),
  useRefreshOctCredits: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      auth: {
        logoutSuccess: "ok",
        logoutFailed: "fail",
        login: "登录",
        logout: "退出",
        profile: "资料",
      },
      credits: { refreshed: "ok", refreshFailed: "fail" },
      userMenu: {
        guestLabel: "Guest",
        loginPrompt: "登录",
      },
    },
    locale: "zh",
    setLocale: () => Promise.resolve(),
  }),
}));
// SidebarMenu* components live in @/components/ui/sidebar and pull
// in Radix · bypass by stubbing to plain divs · keeps this test
// focused on UserMenu's own logic.
vi.mock("@/components/ui/sidebar", () => {
  const passthrough = ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  );
  return {
    SidebarMenu: passthrough,
    SidebarMenuItem: passthrough,
    SidebarMenuButton: ({
      children,
      ...rest
    }: React.HTMLAttributes<HTMLButtonElement>) => (
      <button {...rest}>{children}</button>
    ),
  };
});

import { UserMenu } from "./user-menu";

let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderMenu() {
  return render(
    <MemoryRouter>
      <UserMenu />
    </MemoryRouter>,
  );
}

describe("UserMenu · auth state transitions", () => {
  it("renders nothing (and no hook error) in the loading state", () => {
    mockAuth.current = {
      isLoading: true,
      user: null,
      authStatus: { enabled: true },
      logout: async () => {},
      isAuthenticated: false,
    };
    const { container } = renderMenu();
    expect(container.textContent).toBe("");
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("renders the guest login prompt without hook errors", () => {
    mockAuth.current = {
      isLoading: false,
      user: null,
      authStatus: { enabled: true },
      logout: async () => {},
      isAuthenticated: false,
    };
    renderMenu();
    expect(screen.getByText("登录")).toBeInTheDocument();
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("renders the logged-in menu without hook errors", () => {
    mockAuth.current = {
      isLoading: false,
      user: { user_id: "u1", username: "alice" },
      authStatus: { enabled: true },
      logout: async () => {},
      isAuthenticated: true,
    };
    renderMenu();
    // Username chip is inside a DropdownMenu trigger · either the
    // Implementation note.
    // We just need the component to render something non-empty.
    const { container } = renderMenu();
    expect(container.textContent?.length ?? 0).toBeGreaterThan(0);
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("survives the guest → logged-in transition (re-render) without hook-count mismatch", () => {
    // This is THE test that would have failed before the fix.
    // First render · guest (early return path) · hook count = N
    mockAuth.current = {
      isLoading: false,
      user: null,
      authStatus: { enabled: true },
      logout: async () => {},
      isAuthenticated: false,
    };
    const { rerender } = renderMenu();

    // Flip to logged-in · this path calls all hooks · N+1 would crash.
    // After the fix · useMemo sits above the guards · N stays consistent.
    mockAuth.current = {
      isLoading: false,
      user: { user_id: "u1", username: "alice" },
      authStatus: { enabled: true },
      logout: async () => {},
      isAuthenticated: true,
    };
    rerender(
      <MemoryRouter>
        <UserMenu />
      </MemoryRouter>,
    );

    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });
});
