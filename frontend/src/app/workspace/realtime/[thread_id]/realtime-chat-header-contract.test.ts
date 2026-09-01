import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const pageSource = readFileSync(
  join(process.cwd(), "src/app/workspace/realtime/[thread_id]/page.tsx"),
  "utf8",
);

function sourceBetween(start: string, end: string): string {
  const startIndex = pageSource.indexOf(start);
  expect(startIndex).toBeGreaterThanOrEqual(0);
  const endIndex = pageSource.indexOf(end, startIndex + start.length);
  expect(endIndex).toBeGreaterThan(startIndex);
  return pageSource.slice(startIndex, endIndex);
}

describe("realtime compact chat header contract", () => {
  it("uses one responsive shell for every non-Echo conversation", () => {
    const header = sourceBetween("header={", "messageList={");

    expect(header).toContain("!isEchoAssistant ? (");
    expect(header).toContain("<RealtimeGroupHeaderLayout");
    expect(header).not.toContain("isGroupConversation ? (");
    expect(header).toContain(
      'className="absolute left-3 top-1/2 -translate-y-1/2 md:hidden"',
    );
    expect(pageSource).toContain(
      'embeddedDesignChat\n                  ? "px-3"',
    );
    expect(header).toContain(
      "members={embeddedDesignChat ? null : headerMemberSurface}",
    );
    expect(header).toContain(
      "workbench={embeddedDesignChat ? null : headerActions}",
    );
    expect(pageSource).toContain('getAttribute("data-echo-design-chat")');
    expect(pageSource).toContain(
      "!embeddedDesignChat && selectedCollaborators.length > 0",
    );
    expect(pageSource).toContain(
      "embeddedDesignChat ? null : automationTarget",
    );
    expect(pageSource).toContain(
      "allToolEvents={embeddedDesignChat ? [] : allToolEvents}",
    );
  });

  it("combines the two member domains without merging their counts", () => {
    const memberSurface = sourceBetween(
      "const headerMemberSurface",
      "const headerActions",
    );

    expect(memberSurface).toContain("<RealtimeChatHeaderMemberSurface");
    expect(memberSurface).toContain("aiMembers={headerMemberControl}");
    expect(pageSource).toContain("humanInviteAction={headerHumanInvite}");
  });

  it("uses the persisted header title for overflow sharing", () => {
    const sharing = sourceBetween(
      "const headerShareTitle",
      "const headerWorkbench",
    );

    expect(sharing).toContain("headerThreadTitle");
    expect(sharing).toContain("title: headerShareTitle");
  });

  it("keeps REC independent and mounts the explicit share affordance", () => {
    const actions = sourceBetween("const headerActions", "return (");

    expect(actions).toContain(
      "recording={recorderPluginEnabled ? headerRecorder : null}",
    );
    expect(actions).toContain("share={");
    expect(actions).toContain("<ShareMenu");
    expect(actions).toContain("iconOnly");
    expect(actions).not.toContain("RealtimeChatHeaderOverflowMenu");
  });
});
