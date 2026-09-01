import { describe, expect, it } from "vitest";

import type { AgentModeName } from "@/components/workspace/mode-selector";
import {
  classifyModeIntent,
  DEFAULT_HIGH,
  DEFAULT_MEDIUM,
} from "./intent-classifier";

describe("classifyModeIntent", () => {
  it("resolves a clear coding request to develop / auto", () => {
    const r = classifyModeIntent(["帮我写一个排序函数并跑通"]);
    expect(r.mode).toBe("develop");
    expect(r.handle).toBe("auto");
    expect(r.confidence).toBeGreaterThanOrEqual(DEFAULT_HIGH);
    expect(r.signals.length).toBeGreaterThan(0);
  });

  it("resolves an English coding request to develop", () => {
    const r = classifyModeIntent(["implement a new API endpoint"]);
    expect(r.mode).toBe("develop");
    expect(r.handle).toBe("auto");
  });

  it("resolves a security review request to audit", () => {
    const r = classifyModeIntent(["审查一下这段代码的安全性，有没有注入漏洞"]);
    expect(r.mode).toBe("audit");
    expect(r.handle).toBe("auto");
  });

  it("resolves a UI request to uxui", () => {
    const r = classifyModeIntent(["帮我把这个界面改好看一点，调整一下配色和圆角"]);
    expect(r.mode).toBe("uxui");
    expect(r.handle).toBe("auto");
  });

  it("returns none with zero confidence when nothing matches", () => {
    const r = classifyModeIntent(["今天天气怎么样"]);
    expect(r.handle).toBe("none");
    expect(r.confidence).toBe(0);
    expect(r.signals).toHaveLength(0);
  });

  it("weights the most recent message more heavily", () => {
    // Old message: review intent; new message: coding intent. The newer
    // coding signal should win despite the older weaker review one.
    const r = classifyModeIntent([
      "帮我重构这个函数",
      "之前你帮我审查过代码质量",
    ]);
    expect(r.mode).toBe("develop");
  });

  it("suggests (not auto) for a single weak/ambiguous signal", () => {
    // A lone "检查一下" hits the audit lexicon but carries too little
    // absolute strength to justify silently switching modes.
    const r = classifyModeIntent(["帮我检查一下这个项目"]);
    expect(r.handle).toBe("suggest");
    expect(r.confidence).toBeGreaterThanOrEqual(DEFAULT_MEDIUM);
  });

  it("falls back to the provided fallback mode when nothing matches", () => {
    const r = classifyModeIntent([], {}, "uxui");
    expect(r.mode).toBe("uxui");
    expect(r.handle).toBe("none");
  });

  it("considers at most MAX_MESSAGES (5) messages", () => {
    const base = [
      "今天天气怎么样",
      "帮我查个资料",
      "谢谢",
      "还行",
      "嗯",
      "继续",
    ];
    // Only the final coding message is within the window; the rest are void.
    const r = classifyModeIntent([...base, "帮我写个函数"]);
    expect(r.mode).toBe("develop");
  });

  it("ignores markdown code fences when classifying", () => {
    const r = classifyModeIntent(["```js\nconst inject = 1;\n``` 帮我查一下"]);
    // "inject" is an audit term; it must be stripped inside the fence so the
    // generic "查一下" query does not accidentally classify as audit.
    expect(r.mode).not.toBe("audit");
  });

  it("returns a valid AgentModeName for mode", () => {
    const modes: AgentModeName[] = ["develop", "audit", "uxui"];
    const r = classifyModeIntent(["写个组件"]);
    expect(modes).toContain(r.mode);
  });
});
