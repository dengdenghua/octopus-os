import { beforeEach, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { Route, Routes, useLocation } from "react-router-dom";

import { renderWithProviders } from "@/test/harness";

import { ProtectedRoute } from "./protected-route";

let authState = {
  isLoading: true,
  authStatus: null as { enabled: boolean } | null,
  isAuthenticated: false,
};

vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => authState,
}));

beforeEach(() => {
  authState = {
    isLoading: true,
    authStatus: null,
    isAuthenticated: false,
  };
});

function CurrentLocation() {
  const location = useLocation();
  return <div>{`${location.pathname}${location.search}${location.hash}`}</div>;
}

it("shows a localized workspace loading state", () => {
  renderWithProviders(
    <Routes>
      <Route element={<ProtectedRoute />}>
        <Route path="/" element={<div>工作区</div>} />
      </Route>
    </Routes>,
    { locale: "zh-CN" },
  );

  expect(screen.getByText("正在加载工作区...")).toBeInTheDocument();
  expect(screen.queryByText("Loading workspace")).not.toBeInTheDocument();
});

it("keeps the complete invite URL when redirecting to login", async () => {
  authState = {
    isLoading: false,
    authStatus: { enabled: true },
    isAuthenticated: false,
  };

  renderWithProviders(
    <Routes>
      <Route element={<ProtectedRoute />}>
        <Route path="/workspace/team/join" element={<div>加入</div>} />
      </Route>
      <Route path="/login" element={<CurrentLocation />} />
    </Routes>,
    {
      initialRoute:
        "/workspace/team/join?token=secret-token&thread=thread-1#details",
    },
  );

  expect(
    await screen.findByText(
      "/login?returnTo=%2Fworkspace%2Fteam%2Fjoin%3Ftoken%3Dsecret-token%26thread%3Dthread-1%23details",
    ),
  ).toBeInTheDocument();
});
