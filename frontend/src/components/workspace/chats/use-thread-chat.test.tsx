import { fireEvent, screen, waitFor } from "@testing-library/react";
import { useNavigate } from "react-router-dom";
import { describe, expect, test } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { useThreadChat } from "./use-thread-chat";

function ThreadChatProbe() {
  const navigate = useNavigate();
  const { isNewThread, threadId } = useThreadChat();
  return (
    <div>
      <output data-testid="thread-id">{threadId}</output>
      <output data-testid="is-new">{String(isNewThread)}</output>
      <button
        type="button"
        onClick={() =>
          navigate("/workspace/realtime/t-existing", { replace: false })
        }
      >
        Go existing
      </button>
      <button
        type="button"
        onClick={() => navigate("/workspace/realtime/new")}
      >
        Back to new
      </button>
      <button
        type="button"
        onClick={() =>
          navigate("/workspace/realtime/new", {
            state: { taskNonce: "second" },
          })
        }
      >
        New task
      </button>
      <button
        type="button"
        onClick={() => navigate("/workspace/realtime/new?agent=aoi")}
      >
        Switch to AOI
      </button>
    </div>
  );
}

describe("useThreadChat", () => {
  test("keeps the draft when /new is re-entered without a new-task nonce", async () => {
    renderWithProviders(<ThreadChatProbe />, {
      initialRoute: "/workspace/realtime/new",
    });
    const firstThreadId = screen.getByTestId("thread-id").textContent;
    expect(screen.getByTestId("is-new")).toHaveTextContent("true");

    // A same-route navigation without a nonce (pathId identical) must NOT
    // allocate a fresh thread - the in-progress draft survives.
    fireEvent.click(screen.getByRole("button", { name: "Back to new" }));
    await waitFor(() => {
      expect(screen.getByTestId("thread-id").textContent).toBe(firstThreadId);
    });
    expect(screen.getByTestId("is-new")).toHaveTextContent("true");
  });

  test("allocates a fresh thread when 新建任务 is clicked again", async () => {
    renderWithProviders(<ThreadChatProbe />, {
      initialRoute: "/workspace/realtime/new",
    });
    const firstThreadId = screen.getByTestId("thread-id").textContent;

    // The sidebar's 新建任务 button navigates with a fresh taskNonce.
    // Without it in pathId the identical pathname skipped the reset and the
    // button looked dead - the old draft survived a "new task" click.
    fireEvent.click(screen.getByRole("button", { name: "New task" }));

    await waitFor(() => {
      expect(screen.getByTestId("thread-id").textContent).not.toBe(
        firstThreadId,
      );
    });
    expect(screen.getByTestId("is-new")).toHaveTextContent("true");
  });

  test("allocates a persona-scoped draft when the new-task agent changes", async () => {
    renderWithProviders(<ThreadChatProbe />, {
      initialRoute: "/workspace/realtime/new?agent=coder",
    });
    const coderThreadId = screen.getByTestId("thread-id").textContent;

    fireEvent.click(screen.getByRole("button", { name: "Switch to AOI" }));

    await waitFor(() => {
      expect(screen.getByTestId("thread-id").textContent).not.toBe(
        coderThreadId,
      );
    });
    expect(screen.getByTestId("is-new")).toHaveTextContent("true");
  });
});
