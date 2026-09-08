import { QueryClient } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { useArtifactContent } from "./hooks";

vi.mock("@/components/workspace/messages/context", () => ({
  useThread: () => ({ isMock: false, thread: { isLoading: false } }),
}));
vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer source-token" }),
  currentActorId: () => "source-actor",
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

function Probe({ filepath = "/authorized/invoice.md" }: { filepath?: string }) {
  const result = useArtifactContent({
    filepath,
    threadId: "t1",
    enabled: true,
  });
  return (
    <>
      <div data-testid="content">{result.content}</div>
      <div data-testid="state">
        {result.isLoading ? "checking" : result.error ? "failed" : "ready"}
      </div>
    </>
  );
}

afterEach(() => vi.unstubAllGlobals());

it("rechecks a reopened original and does not display cached source bytes while access is checked", async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(new Response("old original"))
    .mockReturnValueOnce(pending)
    .mockResolvedValue(new Response("denied", { status: 403 }));
  vi.stubGlobal("fetch", fetchMock);
  const queryClient = new QueryClient();
  const first = renderWithProviders(<Probe />, { queryClient });
  await waitFor(() =>
    expect(screen.getByTestId("content")).toHaveTextContent("old original"),
  );
  first.unmount();
  renderWithProviders(<Probe />, { queryClient });
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(screen.getByTestId("content")).toBeEmptyDOMElement();
  expect(screen.getByTestId("state")).toHaveTextContent("checking");
  await act(async () => finish(new Response("denied", { status: 403 })));
  await waitFor(() =>
    expect(screen.getByTestId("state")).toHaveTextContent("failed"),
  );
  expect(screen.getByTestId("content")).toBeEmptyDOMElement();
  queryClient.clear();
});

it("retains the existing scoped final-output cache contract", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response("final output"));
  vi.stubGlobal("fetch", fetchMock);
  const queryClient = new QueryClient();
  const first = renderWithProviders(
    <Probe filepath="workspace-output:final:result.md" />,
    { queryClient },
  );
  await waitFor(() =>
    expect(screen.getByTestId("content")).toHaveTextContent("final output"),
  );
  first.unmount();
  renderWithProviders(<Probe filepath="workspace-output:final:result.md" />, {
    queryClient,
  });
  expect(screen.getByTestId("content")).toHaveTextContent("final output");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  queryClient.clear();
});
