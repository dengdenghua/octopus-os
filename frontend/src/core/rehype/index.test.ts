import type { Element, Root } from "hast";
import { describe, expect, it } from "vitest";

import { rehypeSplitWordsIntoSpans } from "./index";

function paragraph(...children: Element["children"]): Root {
  return {
    type: "root",
    children: [{ type: "element", tagName: "p", properties: {}, children }],
  };
}

function transform(tree: Root): Element {
  rehypeSplitWordsIntoSpans()(tree);
  return tree.children[0] as Element;
}

describe("rehypeSplitWordsIntoSpans", () => {
  it("wraps only the latest English word", () => {
    const node = transform(paragraph({ type: "text", value: "A long answer" }));

    expect(node.children).toEqual([
      { type: "text", value: "A long " },
      {
        type: "element",
        tagName: "span",
        properties: { className: ["animate-fade-in", "inline"] },
        children: [{ type: "text", value: "answer" }],
      },
    ]);
  });

  it("preserves trailing whitespace outside the animated unit", () => {
    const node = transform(paragraph({ type: "text", value: "Hello " }));

    expect(node.children).toEqual([
      {
        type: "element",
        tagName: "span",
        properties: { className: ["animate-fade-in", "inline"] },
        children: [{ type: "text", value: "Hello" }],
      },
      { type: "text", value: " " },
    ]);
  });

  it("keeps earlier text nodes unwrapped and animates the CJK tail", () => {
    const node = transform(
      paragraph(
        { type: "text", value: "前文" },
        {
          type: "element",
          tagName: "strong",
          properties: {},
          children: [{ type: "text", value: "重点" }],
        },
        { type: "text", value: "流式渲染" },
      ),
    );

    expect(node.children[0]).toEqual({ type: "text", value: "前文" });
    expect(node.children[2]).toEqual({ type: "text", value: "流式渲" });
    expect(node.children[3]).toMatchObject({
      type: "element",
      tagName: "span",
      children: [{ type: "text", value: "染" }],
    });
  });

  it("keeps long streamed paragraphs to a constant number of text nodes", () => {
    const content = Array.from(
      { length: 500 },
      (_, index) => `word${index}`,
    ).join(" ");
    const node = transform(paragraph({ type: "text", value: content }));

    expect(node.children).toHaveLength(2);
    expect(node.children[0]).toMatchObject({ type: "text" });
    expect(node.children[1]).toMatchObject({
      type: "element",
      children: [{ type: "text", value: "word499" }],
    });
  });

  it("reconstructs the full text when the tail window kicks in", () => {
    const content = `${"lorem ipsum ".repeat(100)}最后一个词`;
    const node = transform(paragraph({ type: "text", value: content }));

    const flattened = (node.children ?? [])
      .map((child) =>
        child.type === "text"
          ? child.value
          : child.type === "element" &&
              child.children[0] &&
              child.children[0].type === "text"
            ? child.children[0].value
            : "",
      )
      .join("");
    expect(flattened).toBe(content);

    const span = node.children.find((c) => c.type === "element");
    expect(span).toMatchObject({
      tagName: "span",
      properties: { className: ["animate-fade-in", "inline"] },
      children: [{ type: "text", value: "词" }],
    });
  });

  it("leaves text nodes beyond the length threshold untouched", () => {
    const content = "x".repeat(9000);
    const node = transform(paragraph({ type: "text", value: content }));

    expect(node.children).toEqual([{ type: "text", value: content }]);
  });

  it("respects a custom maxTextLength option", () => {
    const tree = paragraph({ type: "text", value: "A long answer" });
    rehypeSplitWordsIntoSpans({ maxTextLength: 4 })(tree);
    const node = tree.children[0] as Element;

    expect(node.children).toEqual([{ type: "text", value: "A long answer" }]);
  });
});
