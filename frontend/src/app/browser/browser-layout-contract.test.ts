import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const browserSource = readFileSync(
  join(process.cwd(), "src/app/browser/page.tsx"),
  "utf8",
);
const urlBarSource = readFileSync(
  join(process.cwd(), "src/components/browser/url-bar.tsx"),
  "utf8",
);
const assistantSource = readFileSync(
  join(process.cwd(), "src/components/browser/assistant-panel.tsx"),
  "utf8",
);
const homeSource = readFileSync(
  join(process.cwd(), "src/components/browser/browser-home.tsx"),
  "utf8",
);
const webviewSource = readFileSync(
  join(process.cwd(), "src/components/browser/webview-tab.tsx"),
  "utf8",
);

describe("AI browser layout hierarchy", () => {
  it("places the web stage before the right-side AI workbench", () => {
    const contentStart = browserSource.indexOf(
      '<div className="flex min-h-0 flex-1 overflow-hidden">',
    );
    const contentEnd = browserSource.indexOf(
      "</div>\n        </div>",
      contentStart,
    );
    const content = browserSource.slice(contentStart, contentEnd);

    expect(content.indexOf("ref={stageRef}")).toBeLessThan(
      content.indexOf("state.copilotOpen"),
    );
    expect(content).toContain("border-l border-border-subtle");
    expect(assistantSource).toContain("dragRef.current.startX - ev.clientX");
    expect(assistantSource).toContain('className="absolute left-0');
  });

  it("keeps the primary toolbar compact and moves secondary actions into more", () => {
    expect(urlBarSource).toContain('className="flex h-12');
    expect(urlBarSource).toContain("canAttachScreenshot=");
    expect(urlBarSource).toContain("onGoHome={goHome}");
    expect(urlBarSource).toContain("<BrowserActionsMenu");

    const toolbarStart = urlBarSource.indexOf('className="flex h-12');
    const addressStart = urlBarSource.indexOf(
      "ref={addressBarRef}",
      toolbarStart,
    );
    const leadingControls = urlBarSource.slice(toolbarStart, addressStart);
    expect(leadingControls).not.toContain("attachScreenshotToNextComposer");
    expect(leadingControls).not.toContain("onClick={goHome}");
  });

  it("keeps desktop editing out of the default home surface", () => {
    expect(urlBarSource).toContain("canCustomizeHome=");
    expect(urlBarSource).toContain("BROWSER_EDIT_HOME_EVENT");
    expect(homeSource).toContain(
      "window.addEventListener(BROWSER_EDIT_HOME_EVENT, enterEditMode)",
    );
    expect(webviewSource).not.toContain(
      "window.addEventListener(BROWSER_EDIT_HOME_EVENT, enterEditMode)",
    );

    expect(homeSource).not.toContain("{wt.editDesktop}");
    expect(webviewSource).not.toContain("{wt.editDesktop}");
    expect(homeSource).toContain("{wt.finishEditing}");
    expect(webviewSource).not.toContain("{wt.finishEditing}");
  });

  it("surfaces detected localhost previews beside recent visits", () => {
    expect(homeSource).toContain("detectLocalServices({ excludePorts })");
    expect(homeSource).toContain("{bp.localServices}");
    expect(homeSource).toContain("localServices.slice(0, 6).map");
    expect(homeSource).toContain("onClick={() => onOpen(service.url)}");
    expect(browserSource).toContain("isLocalPreviewUrl(activeTabUrl)");
    expect(browserSource).toContain('data-testid="local-preview-toolbar"');
    expect(browserSource).toContain("patchTab(activeTab.id, { device })");
    expect(browserSource).toContain("activeHandle?.reload()");
  });
});
