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

describe("realtime unified right panel contract", () => {
  it("routes utility views and the workbench through one secondary surface", () => {
    const layout = sourceBetween(
      "<ChatPageLayout",
      "\n            />\n          </ChatBox>",
    );

    expect(layout.match(/secondaryPanel=\{/g)).toHaveLength(1);
    expect(layout).not.toContain("sidebar={");
    expect(layout).not.toContain("showSidebar=");
    expect(layout).not.toContain("sidebarWidth=");
    expect(layout).toContain('secondaryPanelWidth="min(500px, 38vw)"');
    expect(layout).toContain("onSecondaryClose={closeUnifiedRightPanel}");

    const teachRepeatIndex = layout.indexOf("showTeachRepeatPanel ?");
    const automationIndex = layout.indexOf(
      "isEchoAssistant && showAutomationPanel ?",
    );
    const researchIndex = layout.indexOf("showResearchHistory ?");
    const planIndex = layout.indexOf("showAgentPlan ?");
    const workbenchIndex = layout.indexOf("showAgentWorkbench ?");
    expect(teachRepeatIndex).toBeLessThan(automationIndex);
    expect(automationIndex).toBeLessThan(researchIndex);
    expect(researchIndex).toBeLessThan(planIndex);
    expect(planIndex).toBeLessThan(workbenchIndex);
  });

  it("dismisses only the temporary utility so the prior workbench can return", () => {
    const activePanel = sourceBetween(
      "const hasResearchPanel",
      "const openAgentPanel = useCallback",
    );
    expect(activePanel).toContain("showTeachRepeatPanel");
    expect(activePanel).toContain("isEchoAssistant && showAutomationPanel");

    const closeHandler = sourceBetween(
      "const closeUnifiedRightPanel = useCallback",
      "const closeRightPanel = closeUnifiedRightPanel",
    );
    expect(closeHandler).toContain("setShowTeachRepeatPanel(false)");
    expect(closeHandler).toContain("setShowAutomationPanel(false)");
    expect(closeHandler).toContain("setShowResearchHistory(false)");
    expect(closeHandler).toContain("setShowResearch(false)");
    expect(closeHandler).toContain("setShowAgentPlan(false)");
    expect(closeHandler).toContain("closeAgentWorkbenchPanel()");

    const planOpener = sourceBetween(
      "const openAgentPlanPanel = useCallback",
      "const openPreviewPanel = useCallback",
    );
    expect(planOpener).not.toContain("setAgentWorkbenchManuallyOpened");
    expect(planOpener).not.toContain("setAgentWorkbenchDismissed");
    expect(planOpener).not.toContain("setAgentWorkbenchTab(");
  });

  it("closes special utilities before every explicit surface switch", () => {
    const openerBoundaries = [
      ["openAgentPanel", "openArtifactsPanel"],
      ["openArtifactsPanel", "openWorkbenchArtifact"],
      ["openWorkbenchArtifact", "openFinalArtifactPanel"],
      ["openAgentPlanPanel", "openPreviewPanel"],
      ["openPreviewPanel", "openResearchPanel"],
      ["openResearchPanel", "openResearchHistoryPanel"],
      ["openResearchHistoryPanel", "closeAgentWorkbenchPanel"],
    ] as const;

    for (const [opener, next] of openerBoundaries) {
      expect(
        sourceBetween(
          `const ${opener} = useCallback`,
          `const ${next} = useCallback`,
        ),
        opener,
      ).toContain("closeSpecialUtilityPanels();");
    }

    expect(pageSource).toContain(
      "const closeRightPanel = closeUnifiedRightPanel;",
    );
    expect(pageSource).toContain("onClick={toggleAutomationPanel}");
    expect(pageSource).toContain("openTeachRepeatPanel();");
  });
});
