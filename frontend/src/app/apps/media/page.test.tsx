import { screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import MediaAppPage from "./page";

vi.mock("@/core/storage/api", () => ({
  createNASIndexJob: vi.fn().mockResolvedValue({ job_id: "job-1" }),
  listNASAlbums: vi
    .fn()
    .mockResolvedValue([
      { label: "旅行", count: 1, cover_asset_id: "photo-1" },
    ]),
  listNASFiles: vi.fn().mockResolvedValue([
    {
      asset_id: "photo-1",
      source_id: "source-1",
      name: "海边.jpg",
      path: "/照片/海边.jpg",
      extension: "jpg",
      kind: "image",
      size: 2048,
      mtime_ns: 1_735_689_600_000_000_000,
      ai_labels: ["旅行"],
    },
  ]),
  loadNASAssetURL: vi.fn().mockResolvedValue("blob:photo-1"),
  startNASService: vi.fn().mockResolvedValue({
    ok: true,
    status: "already_running",
    base_url: "http://127.0.0.1:8000",
  }),
  triggerVideoIndex: vi.fn(),
}));

describe("MediaAppPage", () => {
  test("renders indexed photos and album filters as a standalone app", async () => {
    renderWithProviders(<MediaAppPage kind="image" />, {
      initialRoute: "/apps/photos",
      locale: "zh-CN",
    });

    await waitFor(() =>
      expect(screen.getByText("海边.jpg")).toBeInTheDocument(),
    );
    expect(screen.getByText("旅行 1")).toBeInTheDocument();
    expect(screen.getByText(/本机索引 · 1 项/)).toBeInTheDocument();
  });
});
