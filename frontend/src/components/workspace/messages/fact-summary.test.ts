import { describe, expect, test } from "vitest";

import { extractFactSummary } from "./fact-summary";

describe("extractFactSummary 结构化分支", () => {
  test("path 字段：取路径末段", () => {
    expect(
      extractFactSummary("read_file", { path: "/src/app/index.ts" }),
    ).toEqual({ kind: "path", value: "index.ts" });
  });

  test("file / filePath 字段：同为 path 分支，兼容 Windows 分隔符", () => {
    expect(extractFactSummary("read_file", { file: "README.md" })).toEqual({
      kind: "path",
      value: "README.md",
    });
    expect(
      extractFactSummary("read_file", { filePath: "C:\\repo\\src\\main.py" }),
    ).toEqual({ kind: "path", value: "main.py" });
  });

  test("path 以分隔符结尾：取最后一个非空段", () => {
    expect(extractFactSummary("ls", { path: "/var/log/" })).toEqual({
      kind: "path",
      value: "log",
    });
  });

  test("count / total 字段：数量事实", () => {
    expect(extractFactSummary("glob", { count: 12 })).toEqual({
      kind: "count",
      value: "12",
    });
    expect(extractFactSummary("glob", { total: 3 })).toEqual({
      kind: "count",
      value: "3",
    });
  });

  test("status / state 字段：状态事实", () => {
    expect(extractFactSummary("task", { status: "completed" })).toEqual({
      kind: "status",
      value: "completed",
    });
    expect(extractFactSummary("task", { state: "done" })).toEqual({
      kind: "status",
      value: "done",
    });
  });

  test("title / name 字段：标题事实", () => {
    expect(extractFactSummary("web_fetch", { title: "季度报告" })).toEqual({
      kind: "title",
      value: "季度报告",
    });
    expect(
      extractFactSummary("web_fetch", { name: "fact-summary.ts" }),
    ).toEqual({ kind: "title", value: "fact-summary.ts" });
  });

  test("数组结果：共 N 项（空数组为 0 项）", () => {
    expect(extractFactSummary("glob", [{ a: 1 }, { a: 2 }])).toEqual({
      kind: "count",
      value: "2",
    });
    expect(extractFactSummary("glob", [])).toEqual({
      kind: "count",
      value: "0",
    });
  });

  test("字段优先级：path 先于 count，count 先于 status", () => {
    expect(
      extractFactSummary("read_file", { path: "/a/b.ts", count: 5 }),
    ).toEqual({ kind: "path", value: "b.ts" });
    expect(extractFactSummary("task", { count: 2, status: "done" })).toEqual({
      kind: "count",
      value: "2",
    });
  });

  test("类型不符的字段跳过：字符串 count 不算数量，数字 path 不算路径", () => {
    expect(extractFactSummary("glob", { count: "5" })).toBeNull();
    expect(extractFactSummary("glob", { path: 123, count: 2 })).toEqual({
      kind: "count",
      value: "2",
    });
  });

  test("空字符串字段视为缺失", () => {
    expect(extractFactSummary("read_file", { path: "   " })).toBeNull();
  });

  test("短文本结果：直接作为事实（≤120 字符）", () => {
    expect(extractFactSummary("write_file", "写入完成")).toEqual({
      kind: "text",
      value: "写入完成",
    });
    expect(extractFactSummary("write_file", "a".repeat(120))).toEqual({
      kind: "text",
      value: `${"a".repeat(79)}…`,
    });
  });
});

describe("extractFactSummary 非结构化输入返回 null", () => {
  test("空值：null / undefined", () => {
    expect(extractFactSummary("read_file", null)).toBeNull();
    expect(extractFactSummary("read_file", undefined)).toBeNull();
  });

  test("标量：纯数字 / 布尔", () => {
    expect(extractFactSummary("read_file", 42)).toBeNull();
    expect(extractFactSummary("read_file", true)).toBeNull();
  });

  test("长字符串（>120 字符）", () => {
    expect(extractFactSummary("exec_shell", "a".repeat(121))).toBeNull();
  });

  test("空白字符串", () => {
    expect(extractFactSummary("exec_shell", "   ")).toBeNull();
  });

  test("无可用字段的对象", () => {
    expect(extractFactSummary("exec_shell", { foo: 1, bar: "x" })).toBeNull();
    expect(extractFactSummary("exec_shell", {})).toBeNull();
  });
});

describe("extractFactSummary 截断行为", () => {
  test("value 超 80 字符：保留前 79 字符并以 … 结尾", () => {
    const result = extractFactSummary("web_fetch", { title: "b".repeat(100) });
    expect(result).not.toBeNull();
    expect(result?.kind).toBe("title");
    expect(result?.value).toBe(`${"b".repeat(79)}…`);
    expect(result?.value).toHaveLength(80);
  });

  test("短文本结果 81~120 字符：同样按 80 字符截断", () => {
    const result = extractFactSummary("write_file", "c".repeat(100));
    expect(result).toEqual({ kind: "text", value: `${"c".repeat(79)}…` });
  });

  test("恰好 80 字符不截断", () => {
    const result = extractFactSummary("web_fetch", { title: "d".repeat(80) });
    expect(result).toEqual({ kind: "title", value: "d".repeat(80) });
  });
});

describe("extractFactSummary 回归边界", () => {
  test("读取的源码包含 error handling 时不误判失败", () => {
    expect(
      extractFactSummary(
        "read_file",
        "export function errorHandling() {\n  return 'failed request';\n}\n",
      ),
    ).toEqual({ kind: "lines", value: "3" });
  });

  test("明确的结构化失败与退出码保持语义化，不携带中文值", () => {
    expect(extractFactSummary("run_command", { success: false })).toEqual({
      kind: "failed",
      value: "",
    });
    expect(extractFactSummary("run_command", { exit_code: 7 })).toEqual({
      kind: "exit_code",
      value: "7",
    });
    expect(extractFactSummary("run_command", "exit code: 7")).toEqual({
      kind: "exit_code",
      value: "7",
    });
  });

  test("duration 数值按毫秒转换", () => {
    expect(extractFactSummary("task", { duration: 500 })).toEqual({
      kind: "duration",
      value: "500ms",
    });
    expect(extractFactSummary("task", { duration: 1000 })).toEqual({
      kind: "duration",
      value: "1.0s",
    });
  });

  test("shell 摘要不公开本机路径或密钥", () => {
    expect(
      extractFactSummary("exec_shell", "token=sk-secret-value"),
    ).toBeNull();
    expect(
      extractFactSummary("exec_shell", "/Users/example/private.txt"),
    ).toBeNull();
  });

  test("大小写 shell 工具仍进入 shell 分支，thread_list 不误判为读取", () => {
    expect(extractFactSummary("Bash", { stdout: "ok", stderr: "" })).toEqual({
      kind: "text",
      value: "ok",
    });
    expect(extractFactSummary("thread_list", { title: "Threads" })).toEqual({
      kind: "title",
      value: "Threads",
    });
  });
});
