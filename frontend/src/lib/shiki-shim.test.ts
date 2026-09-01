import { describe, expect, it } from "vitest";

import {
  bundledLanguages,
  codeToHtml,
  createHighlighter,
} from "./shiki-shim";

describe("shiki-shim smoke", () => {
  it("highlights a whitelisted language", async () => {
    const html = await codeToHtml("const answer: number = 42", {
      lang: "typescript",
      theme: "one-light",
    });
    expect(html).toContain("<pre");
    expect(html).toContain("answer");
    // real highlighting produces colored spans
    expect(html).toContain("style=");
  });

  it("falls back to plain text for non-whitelisted languages", async () => {
    const html = await codeToHtml("(lambda (x) x)", {
      lang: "emacs-lisp",
      theme: "one-dark-pro",
    });
    expect(html).toContain("<pre");
    expect(html).toContain("lambda");
  });

  it("createHighlighter accepts plain name strings (streamdown contract)", async () => {
    const highlighter = await createHighlighter({
      themes: ["github-light"],
      langs: ["python"],
    });
    const pyHtml = highlighter.codeToHtml("print(1)", {
      lang: "python",
      theme: "github-light",
    });
    expect(pyHtml).toContain("<pre");

    // loadLanguage by name must also work (streamdown lazy-load path)
    await highlighter.loadLanguage("rust");
    const rustHtml = highlighter.codeToHtml("fn main() {}", {
      lang: "rust",
      theme: "github-light",
    });
    expect(rustHtml).toContain("<pre");
  });

  it("exposes bundledLanguages for support probing", () => {
    expect(Object.hasOwn(bundledLanguages, "typescript")).toBe(true);
    expect(Object.hasOwn(bundledLanguages, "emacs-lisp")).toBe(false);
  });
});
