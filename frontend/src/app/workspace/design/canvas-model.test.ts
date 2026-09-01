import { describe, expect, it } from "vitest";

import {
  appendDesignNode,
  connectDesignNodes,
  copyDesignSelection,
  DEFAULT_DESIGN_CANVAS,
  deleteDesignSelection,
  designCanvasRunPrompt,
  disconnectDesignEdge,
  fitDesignMediaNodeDimensions,
  groupDesignNodes,
  parseDesignCanvas,
  mergeDesignCanvases,
  pasteDesignSelection,
  planDesignSelectionDeletion,
  switchDesignCanvasMode,
  tidyDesignCanvas,
  ungroupDesignNode,
} from "./canvas-model";

describe("design canvas model", () => {
  it("fits media nodes to their intrinsic aspect ratio", () => {
    expect(fitDesignMediaNodeDimensions(1080, 1920)).toEqual({
      width: 190,
      height: 363,
    });
    expect(fitDesignMediaNodeDimensions(1920, 1080)).toEqual({
      width: 260,
      height: 171,
    });
    expect(fitDesignMediaNodeDimensions(0, 0)).toEqual({
      width: 236,
      height: 240,
    });
  });

  it("falls back safely for invalid persisted data", () => {
    expect(parseDesignCanvas("not json").nodes).toHaveLength(4);
    expect(parseDesignCanvas('{"version":2}').title).toBe("品牌发布创作流");
  });

  it("connects an appended node to the selected source", () => {
    const next = appendDesignNode(
      DEFAULT_DESIGN_CANVAS,
      {
        id: "plugin-1",
        kind: "plugin",
        title: "视频插件",
        description: "生成视频",
        x: 0,
        y: 0,
      },
      "brief",
    );
    expect(next.edges.at(-1)).toMatchObject({
      source: "brief",
      target: "plugin-1",
    });
  });

  it("creates unique manual workflow edges and can remove them", () => {
    const connected = connectDesignNodes(
      DEFAULT_DESIGN_CANVAS,
      "agent",
      "skill",
    );
    expect(connected.edges).toHaveLength(
      DEFAULT_DESIGN_CANVAS.edges.length + 1,
    );
    expect(connectDesignNodes(connected, "agent", "skill")).toBe(connected);
    expect(connectDesignNodes(connected, "agent", "agent")).toBe(connected);
    expect(connectDesignNodes(connected, "agent", "brief")).toBe(connected);
    const edge = connected.edges.find(
      (item) => item.source === "agent" && item.target === "skill",
    )!;
    expect(disconnectDesignEdge(connected, edge.id).edges).not.toContainEqual(
      edge,
    );
  });

  it("tidies workflow columns and creates an executable prompt", () => {
    const tidy = tidyDesignCanvas(DEFAULT_DESIGN_CANVAS);
    const brief = tidy.nodes.find((node) => node.id === "brief")!;
    const output = tidy.nodes.find((node) => node.id === "output")!;
    expect(output.x).toBeGreaterThan(brief.x);
    expect(designCanvasRunPrompt(tidy)).toContain("创作画布");
    expect(designCanvasRunPrompt(tidy)).toContain("创作需求 → 视觉导演");
  });

  it("supports the canvas tidy modes exposed by the workspace", () => {
    const horizontal = tidyDesignCanvas(DEFAULT_DESIGN_CANVAS, "horizontal");
    expect(horizontal.nodes[1].x).toBeGreaterThan(horizontal.nodes[0].x);
    expect(horizontal.nodes[1].y).toBe(horizontal.nodes[0].y);

    const vertical = tidyDesignCanvas(DEFAULT_DESIGN_CANVAS, "vertical");
    expect(vertical.nodes[1].x).toBe(vertical.nodes[0].x);
    expect(vertical.nodes[1].y).toBeGreaterThan(vertical.nodes[0].y);

    const grid = tidyDesignCanvas(DEFAULT_DESIGN_CANVAS, "grid");
    expect(new Set(grid.nodes.map((node) => node.y)).size).toBeGreaterThan(1);
  });

  it("keeps independent positions for freeform and workflow layouts", () => {
    const freeform = switchDesignCanvasMode(DEFAULT_DESIGN_CANVAS, "freeform");
    const moved = {
      ...freeform,
      nodes: freeform.nodes.map((node, index) =>
        index === 0 ? { ...node, x: 900, y: 700 } : node,
      ),
    };
    const workflow = switchDesignCanvasMode(moved, "workflow");
    expect(workflow.nodes[0]).toMatchObject({ x: 40, y: 160 });
    const restored = switchDesignCanvasMode(workflow, "freeform");
    expect(restored.nodes[0]).toMatchObject({ x: 900, y: 700 });
  });

  it("groups selected nodes in a real movable frame and can dissolve it", () => {
    const grouped = groupDesignNodes(
      DEFAULT_DESIGN_CANVAS,
      ["brief", "agent"],
      "group-1",
    );
    const group = grouped.nodes.find((node) => node.id === "group-1")!;
    expect(group).toMatchObject({
      kind: "group",
      childIds: ["brief", "agent"],
    });
    expect(group.width).toBeGreaterThan(236);
    const tidied = tidyDesignCanvas(grouped, "horizontal");
    const tidiedGroup = tidied.nodes.find((node) => node.id === "group-1")!;
    const child = tidied.nodes.find((node) => node.id === "brief")!;
    expect(tidiedGroup.x).toBeLessThan(child.x);
    expect(tidiedGroup.childIds).toEqual(["brief", "agent"]);
    expect(ungroupDesignNode(grouped, "group-1").nodes).toHaveLength(
      DEFAULT_DESIGN_CANVAS.nodes.length,
    );
  });

  it("keeps attached stickers with their target while tidying", () => {
    const withSticker = appendDesignNode(DEFAULT_DESIGN_CANVAS, {
      id: "sticker-1",
      kind: "sticker",
      title: "跟随目标",
      description: "跟随创作需求",
      emoji: "✨",
      attachedTo: "brief",
      x: 250,
      y: 130,
      width: 56,
      height: 56,
    });
    const beforeTarget = withSticker.nodes.find((node) => node.id === "brief")!;
    const beforeSticker = withSticker.nodes.find(
      (node) => node.id === "sticker-1",
    )!;
    const tidied = tidyDesignCanvas(withSticker, "vertical");
    const target = tidied.nodes.find((node) => node.id === "brief")!;
    const sticker = tidied.nodes.find((node) => node.id === "sticker-1")!;
    expect(sticker.x - target.x).toBe(beforeSticker.x - beforeTarget.x);
    expect(sticker.y - target.y).toBe(beforeSticker.y - beforeTarget.y);
  });

  it("copies and pastes groups, attached stickers, and internal edges together", () => {
    const withSticker = appendDesignNode(DEFAULT_DESIGN_CANVAS, {
      id: "sticker-1",
      kind: "sticker",
      title: "跟随目标",
      description: "跟随创作需求",
      emoji: "✨",
      attachedTo: "brief",
      x: 250,
      y: 130,
      width: 56,
      height: 56,
    });
    const grouped = groupDesignNodes(
      withSticker,
      ["brief", "agent"],
      "group-1",
    );
    const clipboard = copyDesignSelection(grouped, ["group-1"]);
    expect(clipboard.nodes.map((node) => node.id)).toEqual(
      expect.arrayContaining(["brief", "agent", "group-1", "sticker-1"]),
    );
    expect(clipboard.edges).toContainEqual(
      expect.objectContaining({ source: "brief", target: "agent" }),
    );

    const pasted = pasteDesignSelection(grouped, clipboard, "test", 32);
    const copiedGroup = pasted.document.nodes.find(
      (node) => node.id === "group-1-copy-test",
    )!;
    const copiedSticker = pasted.document.nodes.find(
      (node) => node.id === "sticker-1-copy-test",
    )!;
    expect(copiedGroup.childIds).toEqual([
      "brief-copy-test",
      "agent-copy-test",
    ]);
    expect(copiedSticker.attachedTo).toBe("brief-copy-test");
    expect(copiedSticker.x).toBe(282);
    expect(pasted.document.edges).toContainEqual(
      expect.objectContaining({
        source: "brief-copy-test",
        target: "agent-copy-test",
      }),
    );

    const centered = pasteDesignSelection(grouped, clipboard, "center", 0, {
      x: 500,
      y: 400,
    });
    const centeredNodes = centered.document.nodes.filter((node) =>
      centered.nodeIds.includes(node.id),
    );
    const minX = Math.min(
      ...centeredNodes
        .filter((node) => node.kind !== "sticker")
        .map((node) => node.x),
    );
    const maxX = Math.max(
      ...centeredNodes
        .filter((node) => node.kind !== "sticker")
        .map((node) => node.x + (node.width ?? 236)),
    );
    expect((minX + maxX) / 2).toBe(500);
  });

  it("preserves incoming workflow relations when copying a downstream node", () => {
    const clipboard = copyDesignSelection(DEFAULT_DESIGN_CANVAS, ["output"]);
    expect(clipboard.inheritedEdges).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ source: "agent", target: "output" }),
        expect.objectContaining({ source: "skill", target: "output" }),
      ]),
    );
    const pasted = pasteDesignSelection(
      DEFAULT_DESIGN_CANVAS,
      clipboard,
      "inherited",
    );
    expect(pasted.document.edges).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source: "agent",
          target: "output-copy-inherited",
        }),
        expect.objectContaining({
          source: "skill",
          target: "output-copy-inherited",
        }),
      ]),
    );
  });

  it("expands grouped deletion and protects high-blast canvas changes", () => {
    let document = groupDesignNodes(
      DEFAULT_DESIGN_CANVAS,
      ["brief", "agent"],
      "group-1",
    );
    document = appendDesignNode(document, {
      id: "sticker-1",
      kind: "sticker",
      title: "跟随目标",
      description: "跟随创作需求",
      emoji: "✨",
      attachedTo: "brief",
      x: 0,
      y: 0,
    });
    const groupPlan = planDesignSelectionDeletion(document, ["group-1"]);
    expect(groupPlan.nodeIds).toEqual(
      expect.arrayContaining(["group-1", "brief", "agent", "sticker-1"]),
    );
    const deleted = deleteDesignSelection(document, ["group-1"]);
    expect(deleted.nodes.map((node) => node.id)).not.toEqual(
      expect.arrayContaining(["group-1", "brief", "agent", "sticker-1"]),
    );

    const manyNodes = Array.from({ length: 10 }, (_, index) => ({
      id: `bulk-${index}`,
      kind: "text" as const,
      title: `节点 ${index}`,
      description: "批量删除保护",
      x: index * 20,
      y: 0,
    }));
    const bulkDocument = {
      ...DEFAULT_DESIGN_CANVAS,
      nodes: manyNodes,
      edges: [],
    };
    expect(
      planDesignSelectionDeletion(
        bulkDocument,
        manyNodes.slice(0, 6).map((node) => node.id),
      ).highBlast,
    ).toBe(true);
    expect(
      planDesignSelectionDeletion(
        bulkDocument,
        manyNodes.slice(0, 5).map((node) => node.id),
      ).highBlast,
    ).toBe(false);
  });

  it("assigns stable per-workflow ordinals to copied ComfyUI templates", () => {
    const source = appendDesignNode(DEFAULT_DESIGN_CANVAS, {
      id: "comfy-1",
      kind: "comfyui",
      title: "基础文生图",
      description: "运行本机工作流",
      binding: { type: "workflow", id: "text-to-image" },
      x: 0,
      y: 0,
    });
    const clipboard = copyDesignSelection(source, ["comfy-1"]);
    const first = pasteDesignSelection(source, clipboard, "first");
    const firstCopy = first.document.nodes.find(
      (node) => node.id === "comfy-1-copy-first",
    );
    expect(firstCopy).toMatchObject({
      title: "基础文生图 副本 1",
      copyOrdinal: 1,
    });
    const second = pasteDesignSelection(first.document, clipboard, "second");
    expect(
      second.document.nodes.find((node) => node.id === "comfy-1-copy-second"),
    ).toMatchObject({ title: "基础文生图 副本 2", copyOrdinal: 2 });
  });

  it("preserves a concrete ComfyUI workflow binding for Agent execution", () => {
    const document = appendDesignNode(DEFAULT_DESIGN_CANVAS, {
      id: "comfy-1",
      kind: "comfyui",
      title: "基础文生图",
      description: "运行本机工作流",
      binding: { type: "workflow", id: "text-to-image" },
      x: 0,
      y: 0,
    });
    expect(designCanvasRunPrompt(document)).toContain("workflow:text-to-image");
  });

  it("preserves project asset identity and workspace provenance", () => {
    const document = appendDesignNode(DEFAULT_DESIGN_CANVAS, {
      id: "asset-1",
      kind: "video",
      title: "发布会成片",
      description: "项目交付产物",
      binding: { type: "asset", id: "ART-1" },
      asset: {
        id: "ART-1",
        kind: "video",
        path: "outputs/launch.mp4",
        projectId: "project-1",
      },
      x: 0,
      y: 0,
    });
    const restored = parseDesignCanvas(JSON.stringify(document));
    expect(restored.nodes.at(-1)?.asset?.projectId).toBe("project-1");
    expect(designCanvasRunPrompt(restored)).toContain("asset:ART-1");
    expect(designCanvasRunPrompt(restored)).toContain("outputs/launch.mp4");
  });
});

describe("mergeDesignCanvases", () => {
  it("combines edits to different nodes without a conflict", () => {
    const base = structuredClone(DEFAULT_DESIGN_CANVAS);
    const local = structuredClone(base);
    const remote = structuredClone(base);
    local.nodes[0].title = "本地需求";
    remote.nodes[1].title = "远端导演";

    const merged = mergeDesignCanvases(base, local, remote);

    expect(merged.conflicts).toEqual([]);
    expect(merged.document.nodes[0].title).toBe("本地需求");
    expect(merged.document.nodes[1].title).toBe("远端导演");
  });

  it("reports concurrent edits to the same node and keeps a local preview", () => {
    const base = structuredClone(DEFAULT_DESIGN_CANVAS);
    const local = structuredClone(base);
    const remote = structuredClone(base);
    local.nodes[0].title = "我的版本";
    remote.nodes[0].title = "成员版本";

    const merged = mergeDesignCanvases(base, local, remote);

    expect(merged.conflicts).toEqual(["node:brief"]);
    expect(merged.document.nodes[0].title).toBe("我的版本");
  });

  it("preserves independent additions and deletions", () => {
    const base = structuredClone(DEFAULT_DESIGN_CANVAS);
    const local = structuredClone(base);
    const remote = structuredClone(base);
    local.nodes = local.nodes.filter((node) => node.id !== "skill");
    remote.nodes.push({
      id: "remote-image",
      kind: "image",
      title: "成员图片",
      description: "远端加入",
      x: 10,
      y: 10,
    });

    const merged = mergeDesignCanvases(base, local, remote);

    expect(merged.conflicts).toEqual([]);
    expect(merged.document.nodes.some((node) => node.id === "skill")).toBe(
      false,
    );
    expect(
      merged.document.nodes.some((node) => node.id === "remote-image"),
    ).toBe(true);
  });
});
