import { describe, expect, it } from "vitest";

import { getAboutMarkdown } from "./about-content";

describe("localized About content", () => {
  it.each(["en-US", "zh-CN", "ja-JP", "ko-KR"])(
    "keeps %s aligned with the shipped license and frontend stack",
    (locale) => {
      const content = getAboutMarkdown(locale);
      expect(content).toContain("Apache License 2.0");
      expect(content).toContain("React");
      expect(content).toContain("Vite");
      expect(content).not.toContain("MIT License");
      expect(content).not.toContain("Next.js");
    },
  );
});
