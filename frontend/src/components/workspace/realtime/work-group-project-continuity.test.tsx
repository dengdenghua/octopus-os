import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GroupHumanInviteButton } from "@/components/workspace/collab";
import { renderWithProviders } from "@/test/harness";

import { ChatHeaderAgentBadge } from "./chat-header-agent-badge";
import { ProjectGroupHeaderBadge } from "./project-group-header-badge";

vi.mock("../collab/invite-dialog", () => ({
  InviteDialog: ({ open, roomId }: { open: boolean; roomId: string }) =>
    open ? <div data-testid="work-group-invite-dialog">{roomId}</div> : null,
}));

describe("project-capable work group continuity", () => {
  it("keeps the group title, AI members and human invite beside the project marker", () => {
    const onOpenWorkbench = vi.fn();
    renderWithProviders(
      <header aria-label="工作群头部">
        <ChatHeaderAgentBadge
          agent={null}
          agentId="planner"
          collaborators={[
            {
              agent_id: "planner",
              name: "planner",
              display_name: "小章",
              role: "tl",
            },
            {
              agent_id: "researcher",
              name: "researcher",
              display_name: "研究员",
              role: "member",
            },
          ]}
        />
        <h2>发布讨论群</h2>
        <ProjectGroupHeaderBadge
          name="秋季发布"
          status="running"
          onOpenWorkbench={onOpenWorkbench}
        />
        <GroupHumanInviteButton roomId="room-release" />
      </header>,
      { locale: "zh-CN" },
    );

    expect(
      screen.getByRole("heading", { name: "发布讨论群" }),
    ).toBeInTheDocument();
    expect(screen.getByTitle("小章、研究员")).toBeInTheDocument();
    expect(screen.getByTestId("project-capability-badge")).toHaveTextContent(
      "项目已开启·进行中",
    );

    fireEvent.click(
      screen.getByRole("button", { name: "打开项目工作台：秋季发布" }),
    );
    expect(onOpenWorkbench).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole("button", { name: "邀请真人" }));
    expect(screen.getByTestId("work-group-invite-dialog")).toHaveTextContent(
      "room-release",
    );
  });
});
