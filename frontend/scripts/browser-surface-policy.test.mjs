import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const webviewSource = await readFile(
  new URL("../src/components/browser/webview-tab.tsx", import.meta.url),
  "utf8",
);

test("the browser home route has one maintained implementation", () => {
  assert.match(
    webviewSource,
    /import\("\.\/browser-home"\)/,
  );
  assert.match(webviewSource, /<BrowserHome\b/);
  assert.doesNotMatch(webviewSource, /LegacyBrowserDesktopHome/);
  assert.doesNotMatch(webviewSource, /interface BrowserDesktopApp/);
});
