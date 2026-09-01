import { describe, expect, it } from "vitest";

import {
  buildOfficeEditPrompt,
  officeArtifactKind,
  officeArtifactSupportsSelection,
} from "./office-edit";

describe("office artifact edit handoff", () => {
  it("recognizes common office artifacts case-insensitively", () => {
    expect(officeArtifactKind("report.DOCX")).toBe("document");
    expect(officeArtifactKind("legacy.DOC")).toBe("document");
    expect(officeArtifactKind("workspace-output:final:model.xlsx")).toBe(
      "spreadsheet",
    );
    expect(officeArtifactKind("deck.pptx?download=true")).toBe("presentation");
    expect(officeArtifactKind("notes.md")).toBeNull();
    expect(officeArtifactSupportsSelection("deck.pptx")).toBe(true);
    expect(officeArtifactSupportsSelection("data.csv")).toBe(true);
    expect(officeArtifactSupportsSelection("data.tsv")).toBe(true);
    expect(officeArtifactSupportsSelection("legacy.ppt")).toBe(false);
    expect(officeArtifactSupportsSelection("report.pdf")).toBe(false);
  });

  it("asks for a real in-place edit rather than a plan artifact", () => {
    const prompt = buildOfficeEditPrompt({
      filepath: "workspace-output:final:deck.pptx",
      displayPath: "deck.pptx",
      kind: "presentation",
      instruction: "把第三页改成风险矩阵",
      selection: {
        node: "slide:3",
        label: "Slide 3",
        text: "Current risks",
      },
    });

    expect(prompt).toContain("把第三页改成风险矩阵");
    expect(prompt).toContain("不要只输出方案、plan.md");
    expect(prompt).toContain("presentations.extract_text");
    expect(prompt).toContain("presentations.replace_text");
    expect(prompt).toContain("应将 slide=3 传给修改工具");
    expect(prompt).toContain("保存到同一路径");
    expect(prompt).toContain("验证文件可以正常打开");
    expect(prompt).toContain('"node": "slide:3"');
    expect(prompt).toContain("不可信数据");
  });

  it("将表格选区路由到原位单元格工具", () => {
    const prompt = buildOfficeEditPrompt({
      filepath: "workspace-output:final:model.xlsx",
      displayPath: "model.xlsx",
      kind: "spreadsheet",
      instruction: "改成 120",
      selection: {
        node: "sheet:2:cell:C5",
        label: "Forecast · C5",
        text: "100",
      },
    });

    expect(prompt).toContain("spreadsheets.workbook_info/read_sheet");
    expect(prompt).toContain("spreadsheets.update_cells");
    expect(prompt).toContain("第 2 个工作表的 C5");
  });

  it("routes CSV edits through text tools instead of the XLSX-only tool", () => {
    const prompt = buildOfficeEditPrompt({
      filepath: "workspace-output:final:data.csv",
      displayPath: "data.csv",
      kind: "spreadsheet",
      instruction: "把第二行金额改成 120",
    });

    expect(prompt).toContain("read_file/read_file_range");
    expect(prompt).toContain("edit_text_file");
    expect(prompt).toContain("不要调用只支持 XLSX");
  });

  it.each([
    ["legacy.doc", "documents.replace_text", ".docx"],
    ["legacy.xls", "spreadsheets.update_cells", ".xlsx"],
    ["legacy.ppt", "presentations.replace_text", ".pptx"],
  ])(
    "does not claim native in-place editing for %s",
    (displayPath, tool, replacement) => {
      const prompt = buildOfficeEditPrompt({
        filepath: `workspace-output:final:${displayPath}`,
        displayPath,
        kind: displayPath.endsWith(".doc")
          ? "document"
          : displayPath.endsWith(".xls")
            ? "spreadsheet"
            : "presentation",
        instruction: "修改内容",
      });

      expect(prompt).toContain(`${tool} 不支持`);
      expect(prompt).toContain(`同名 ${replacement} 新版本`);
      expect(prompt).toContain("不要伪称成功");
    },
  );
});
