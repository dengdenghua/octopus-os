import { describe, expect, it } from "vitest";

import {
  aggregateIconName,
  getActionDisplay,
  getActionIcon,
} from "./action-display";
import {
  FilePlus2Icon,
  PencilLineIcon,
  SquareTerminalIcon,
  WrenchIcon,
} from "lucide-react";

describe("getActionDisplay", () => {
  it("maps edit_file to edit_file label + 文件名", () => {
    const d = getActionDisplay("edit_file", { file_path: "src/auth.ts" });
    expect(d.labelKey).toBe("edit_file");
    expect(d.object).toBe("auth.ts");
    expect(d.iconName).toBe("pencil-line");
    expect(d.workbenchTab).toBe("diff");
    expect(d.aggregateKind).toBe("file_write");
  });

  it("maps write_file to create_file label + 文件名", () => {
    const d = getActionDisplay("write_file", { path: "/tmp/test.js" });
    expect(d.labelKey).toBe("create_file");
    expect(d.object).toBe("test.js");
    expect(d.iconName).toBe("file-plus");
  });

  it("maps apply_patch to edit_file label", () => {
    const d = getActionDisplay("apply_patch", {});
    expect(d.labelKey).toBe("edit_file");
    expect(d.aggregateKind).toBe("file_write");
  });

  it("maps run_command to run_command label + 命令摘要", () => {
    const d = getActionDisplay("run_command", { command: "npm test -- --run" });
    expect(d.labelKey).toBe("run_command");
    expect(d.object).toContain("npm test");
    expect(d.workbenchTab).toBe("terminal");
    expect(d.aggregateKind).toBe("command");
  });

  it("maps shell_command to run_command label", () => {
    const d = getActionDisplay("shell_command", { cmd: "ls -la" });
    expect(d.labelKey).toBe("run_command");
    expect(d.object).toBe("ls -la");
    expect(d.workbenchTab).toBe("terminal");
  });

  it("maps read_file to read_file label + 文件名", () => {
    const d = getActionDisplay("read_file", { file_path: "package.json" });
    expect(d.labelKey).toBe("read_file");
    expect(d.object).toBe("package.json");
    expect(d.aggregateKind).toBe("file_read");
  });

  it("unwraps the nested input carried by child-agent MCP events", () => {
    const d = getActionDisplay("read_file", {
      server: "subagent",
      tool: "read_file",
      arguments: {
        agent_id: "schema_reader",
        input: { path: "output/final/agent-regression.json" },
      },
    });
    expect(d.labelKey).toBe("read_file");
    expect(d.object).toBe("agent-regression.json");
  });

  it("presents report as a product action instead of an internal tool name", () => {
    const d = getActionDisplay("report", {
      arguments: { input: { output: "done" } },
    });
    expect(d.labelKey).toBe("submit_result");
    expect(d.verb).toBe("提交结果");
    expect(d.iconName).toBe("check-circle");
  });

  it("maps ls/list_cwd to view_directory label", () => {
    expect(getActionDisplay("ls", {}).labelKey).toBe("view_directory");
    expect(getActionDisplay("list_cwd", {}).labelKey).toBe("view_directory");
  });

  it("maps glob/grep to search_files label", () => {
    const d = getActionDisplay("grep", { pattern: "useState" });
    expect(d.labelKey).toBe("search_files");
    expect(d.aggregateKind).toBe("file_read");
  });

  it("maps provider glob aliases without leaking the raw tool name", () => {
    const d = getActionDisplay("glob_files", { pattern: "**/*.tsx" });
    expect(d.labelKey).toBe("search_files");
    expect(d.object).toBe("**/*.tsx");
    expect(`${d.labelKey} ${d.object}`).not.toContain("glob_files");
  });

  it("maps web_search to search_web label", () => {
    const d = getActionDisplay("web_search", {
      query: "react hooks best practices",
    });
    expect(d.labelKey).toBe("search_web");
    expect(d.object).toContain("react hooks");
    expect(d.workbenchTab).toBe("browser");
    expect(d.aggregateKind).toBe("web_search");
  });

  it("maps web_fetch to browse_web label", () => {
    const d = getActionDisplay("web_fetch", {
      url: "https://example.com/docs",
    });
    expect(d.labelKey).toBe("browse_web");
    expect(d.aggregateKind).toBe("web_search");
  });

  it("maps browser_navigate to browser_navigate label", () => {
    const d = getActionDisplay("browser_navigate", {
      url: "https://google.com",
    });
    expect(d.labelKey).toBe("browser_navigate");
    expect(d.workbenchTab).toBe("browser");
    expect(d.aggregateKind).toBe("browser");
  });

  it("maps browser_click to browser_click label", () => {
    expect(getActionDisplay("browser_click", {}).labelKey).toBe("browser_click");
  });

  it("maps browser_type to browser_type label", () => {
    expect(getActionDisplay("browser_type", {}).labelKey).toBe("browser_type");
  });

  it("maps todo_write to update_plan label", () => {
    const d = getActionDisplay("todo_write", {});
    expect(d.labelKey).toBe("update_plan");
    expect(d.workbenchTab).toBe("agent");
    expect(d.aggregateKind).toBe("todo");
  });

  it("maps capability tools to a human-readable capability action", () => {
    const d = getActionDisplay("use_capability", {
      capability: "deep_research",
    });
    expect(d.labelKey).toBe("use_capability");
    expect(d.object).toBe("deep_research");
    expect(d.iconName).toBe("book-open");
    expect(d.workbenchTab).toBe("agent");
    expect(d.aggregateKind).toBe("other");
  });

  it("maps teammate/subagent tools to delegate_task label", () => {
    const d = getActionDisplay("spawn_agent", { agent_name: "coder" });
    expect(d.labelKey).toBe("delegate_task");
    expect(d.object).toContain("coder");
    expect(d.aggregateKind).toBe("teammate");
  });

  it("uses the first named role for parallel teammate delegation", () => {
    const d = getActionDisplay("call_agent_parallel", {
      specs: [
        { agent_id: "researcher", prompt: "Collect evidence" },
        { agent_id: "reviewer", prompt: "Review evidence" },
      ],
    });
    expect(d.labelKey).toBe("delegate_task");
    expect(d.object).toContain("Research Specialist");
    expect(d.object).toContain("等");
    expect(d.aggregateKind).toBe("teammate");
  });

  it("maps delete/remove tools to delete_file label", () => {
    const d = getActionDisplay("delete_file", { path: "tmp.txt" });
    expect(d.labelKey).toBe("delete_file");
    expect(d.aggregateKind).toBe("other");
  });

  it("does not classify rm substrings or ClickHouse tools as destructive UI actions", () => {
    expect(getActionDisplay("transform_image", {}).labelKey).toBe("raw");
    expect(getActionDisplay("warm_up_cache", {}).labelKey).toBe("raw");
    expect(getActionDisplay("charm_render", {}).labelKey).toBe("raw");
    expect(getActionDisplay("clickhouse_query", {}).aggregateKind).toBe(
      "other",
    );
  });

  it("uses the final directory name for paths ending in a separator", () => {
    expect(
      getActionDisplay("list_cwd", { path: "src/components/" }).object,
    ).toBe("components");
  });

  it("maps unknown tools to raw label with camelCase verb fallback", () => {
    const d = getActionDisplay("my_custom_tool", {});
    expect(d.labelKey).toBe("raw");
    expect(d.verb).toBe("My Custom Tool");
    expect(d.iconName).toBe("wrench");
    expect(d.aggregateKind).toBe("other");
  });

  it("truncates long file paths", () => {
    const longPath =
      "a/very/long/path/to/some/deeply/nested/file/component.tsx";
    const d = getActionDisplay("edit_file", { file_path: longPath });
    expect(d.object.length).toBeLessThan(longPath.length);
    expect(d.object).toBe("component.tsx");
  });
});

describe("getActionIcon", () => {
  it("returns matching Lucide icons", () => {
    expect(getActionIcon("file-plus")).toBe(FilePlus2Icon);
    expect(getActionIcon("pencil-line")).toBe(PencilLineIcon);
    expect(getActionIcon("square-terminal")).toBe(SquareTerminalIcon);
    expect(getActionIcon("unknown")).toBe(WrenchIcon);
  });
});

describe("aggregateIconName", () => {
  it("returns matching icon names", () => {
    expect(aggregateIconName("file_write")).toBe("pencil-line");
    expect(aggregateIconName("command")).toBe("square-terminal");
    expect(aggregateIconName("web_search")).toBe("globe");
    expect(aggregateIconName("other")).toBe("wrench");
  });
});
