import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BROWSER_OPEN_URL_ACK_EVENT,
  BROWSER_OPEN_URL_REQUEST_EVENT,
  type BrowserOpenUrlAck,
  type BrowserOpenUrlRequest,
} from "@/components/browser/browser-store";
import { renderWithProviders } from "@/test/harness";
import { setLinkOpenTarget } from "@/core/settings/automation-preferences";
import {
  OPEN_ARTIFACT_EVENT,
  type OpenArtifactDetail,
} from "@/core/artifacts/open-artifact";

import {
  MarkdownContent,
  stabilizeMarkdownTableCodePipes,
} from "./markdown-content";

const mermaidMock = vi.hoisted(() => ({
  initialize: vi.fn(),
  render: vi.fn(),
}));

vi.mock("mermaid-real", () => ({
  default: mermaidMock,
}));

vi.mock("@/components/ai-elements/message", async () => {
  const React = await import("react");
  type MockMessageResponseProps = {
    children: string;
    className?: string;
    components: {
      a?: (props: {
        children?: React.ReactNode;
        href?: string;
      }) => React.ReactNode;
      pre?: (props: { children?: React.ReactNode }) => React.ReactNode;
    };
    isAnimating?: boolean;
    "aria-busy"?: boolean;
  };
  const MessageResponse = React.memo(
    ({
      children,
      className,
      components,
      isAnimating,
      "aria-busy": ariaBusy,
    }: MockMessageResponseProps) => {
      const link = /^\[([^\]]+)]\(([^)]+)\)$/.exec(children.trim());
      if (link && components.a) {
        return React.createElement(
          React.Fragment,
          null,
          components.a({ href: link[2], children: link[1] }),
        );
      }
      const match = /^```([\w-]+)\n([\s\S]*?)\n?```$/.exec(children.trim());
      if (match && components.pre) {
        const language = match[1] ?? "text";
        const code = match[2] ?? "";
        return React.createElement(
          React.Fragment,
          null,
          components.pre({
            children: React.createElement(
              "code",
              { className: `language-${language}` },
              code,
            ),
          }),
        );
      }
      return React.createElement(
        "div",
        {
          "aria-busy": ariaBusy,
          className,
          "data-is-animating": isAnimating ? "true" : "false",
        },
        children,
      );
    },
    // Mirrors the real Streamdown top-level memo, which compares children
    // AND isAnimating (only parsed *blocks* are memoized by content alone).
    // The settle transition therefore re-renders the root without remount.
    (previous, next) =>
      previous.children === next.children &&
      previous.isAnimating === next.isAnimating,
  );
  return {
    MessageResponse,
  };
});

function renderMarkdown(content: string, isLoading = false) {
  return renderWithProviders(
    <MarkdownContent
      content={content}
      isLoading={isLoading}
      remarkPlugins={[]}
      rehypePlugins={[]}
    />,
  );
}

/**
 * Settled code blocks render shiki output, which splits the code text
 * across colored token <span>s — no single text node carries the whole
 * snippet. Match against the <code> element's full textContent instead.
 */
function settledCodeMatches(match: RegExp) {
  return (_content: string, element: Element | null) =>
    element?.tagName === "CODE" &&
    !!element.textContent &&
    match.test(element.textContent);
}

describe("<MarkdownContent /> web links", () => {
  let acknowledge: (event: Event) => void;

  beforeEach(() => {
    window.localStorage.clear();
    window.location.hash = "#/workspace";
    setLinkOpenTarget("in_app");
    acknowledge = (event) => {
      const request = (event as CustomEvent<BrowserOpenUrlRequest>).detail;
      window.dispatchEvent(
        new CustomEvent<BrowserOpenUrlAck>(BROWSER_OPEN_URL_ACK_EVENT, {
          detail: { requestId: request.requestId!, accepted: true },
        }),
      );
    };
    window.addEventListener(BROWSER_OPEN_URL_REQUEST_EVENT, acknowledge);
  });

  afterEach(() => {
    window.removeEventListener(BROWSER_OPEN_URL_REQUEST_EVENT, acknowledge);
  });

  it("routes ordinary markdown links through the saved browser preference", () => {
    renderMarkdown("[Open docs](https://example.com/docs)");

    fireEvent.click(screen.getByRole("link", { name: "Open docs" }));

    expect(window.location.hash).toBe("#/browser");
  });

  it("opens generated office links in the artifact workbench", () => {
    let opened: OpenArtifactDetail | null = null;
    const openArtifact = (event: Event) => {
      opened = (event as CustomEvent<OpenArtifactDetail>).detail;
      event.preventDefault();
    };
    window.addEventListener(OPEN_ARTIFACT_EVENT, openArtifact);
    renderMarkdown("[下载 PPT](out/deck.pptx)");

    fireEvent.click(screen.getByRole("link", { name: "下载 PPT" }));

    expect(opened).toEqual({ path: "workspace-output:final:out/deck.pptx" });
    expect(window.location.hash).toBe("#/workspace");
    window.removeEventListener(OPEN_ARTIFACT_EVENT, openArtifact);
  });
});

describe("<MarkdownContent /> Mermaid", () => {
  beforeEach(() => {
    mermaidMock.initialize.mockReset();
    mermaidMock.render.mockReset();
    mermaidMock.render.mockResolvedValue({
      svg: '<svg role="img"><text>Rendered Mermaid</text></svg>',
    });
  });

  it("keeps Mermaid source visible while the message is streaming", async () => {
    renderMarkdown("```mermaid\ngraph TD\nA-->B\n```", true);

    expect(await screen.findByText("mermaid")).toBeInTheDocument();
    expect(screen.getByText(/graph TD/)).toBeInTheDocument();
    expect(mermaidMock.render).not.toHaveBeenCalled();
  });

  it("renders completed Mermaid fences as SVG", async () => {
    renderMarkdown("```mermaid\ngraph TD\nA-->B\n```");

    await waitFor(() => {
      expect(mermaidMock.render).toHaveBeenCalledWith(
        expect.stringMatching(/^mermaid-chat-/),
        expect.stringContaining("graph TD"),
      );
    });
    expect(await screen.findByText("Rendered Mermaid")).toBeInTheDocument();
  });

  it("settles streaming blocks once without remounting the tree", async () => {
    const content = "```mermaid\ngraph TD\nA-->B\n```";
    const view = renderMarkdown(content, true);

    expect(await screen.findByText("mermaid")).toBeInTheDocument();
    expect(mermaidMock.render).not.toHaveBeenCalled();

    view.rerender(
      <MarkdownContent
        content={content}
        isLoading={false}
        remarkPlugins={[]}
        rehypePlugins={[]}
      />,
    );

    await waitFor(() => {
      expect(mermaidMock.render).toHaveBeenCalledWith(
        expect.stringMatching(/^mermaid-chat-/),
        expect.stringContaining("graph TD"),
      );
    });
    expect(await screen.findByText("Rendered Mermaid")).toBeInTheDocument();
  });
});

describe("<MarkdownContent /> streaming state", () => {
  it("keeps pipes inside inline code from splitting Markdown table cells", () => {
    const source = [
      "| layer | selector | file |",
      "| --- | --- | --- |",
      '| theme | `[data-theme="steel|apricot|mint"]` | `globals.css` |',
    ].join("\n");

    expect(stabilizeMarkdownTableCodePipes(source)).toContain(
      '`[data-theme="steel\\|apricot\\|mint"]`',
    );
  });

  it("does not rewrite pipes inside fenced code examples", () => {
    const source = "```ts\nconst modes = `steel|mint`;\n```";
    expect(stabilizeMarkdownTableCodePipes(source)).toBe(source);
  });

  it("gives long technical content its own responsive layout boundary", () => {
    renderMarkdown("A compact answer");

    expect(screen.getByText("A compact answer")).toHaveClass("chat-markdown");
  });

  it("preserves intentional soft line breaks in compact answers", () => {
    const view = renderMarkdown(
      "schema=echo.regression.v1\nfinal=output/final",
    );

    const markdown = view.container.querySelector(".chat-markdown");
    // Soft breaks are preserved by pre-wrap on the prose blocks, not on the
    // container: the container must stay `normal` so Streamdown's inter-block
    // separator newlines don't render as blank lines.
    expect(markdown).toHaveClass("whitespace-normal");
    expect(markdown?.className).toContain("[&_p]:whitespace-pre-wrap");
    expect(markdown?.textContent).toContain("v1\nfinal=");
  });

  it("does not let inter-block newlines render as blank lines", () => {
    // A heading followed by a table is the shape that broke: Streamdown emits
    // the table as a sibling block and leaves the row separator newlines as a
    // bare text node on the container. Under pre-wrap those became ~89 blank
    // lines of dead space above the table.
    const view = renderMarkdown(
      [
        "## 二、中危发现",
        "",
        "| # | 位置 |",
        "|---|---|",
        "| M1 | a.yaml |",
      ].join("\n"),
    );

    const markdown = view.container.querySelector(".chat-markdown");
    expect(markdown).not.toHaveClass("whitespace-pre-wrap");
    expect(markdown).toHaveClass("whitespace-normal");
  });

  it("hides leaked read-only control tags but preserves the answer", () => {
    renderMarkdown(
      "<read_only>\n</read_only>\n\nPython 与 TypeScript 定义一致。",
    );

    expect(screen.queryByText(/read_only/)).not.toBeInTheDocument();
    expect(
      screen.getByText("Python 与 TypeScript 定义一致。"),
    ).toBeInTheDocument();
  });

  it("hides legacy guard boilerplate from persisted replies", () => {
    renderMarkdown(
      [
        "已确认 teal 主题仅定义 --chart-1。",
        "",
        "---",
        "",
        "质量提示：「code-mode guard」未通过证据门禁。此前给出的收尾答案未满足要求（该次提交是模型自身发起的，未被系统接受）；为避免继续空转，现将已有结果交付。",
      ].join("\n"),
    );

    expect(screen.getByText(/已确认 teal/)).toBeInTheDocument();
    expect(screen.queryByText(/质量提示/)).not.toBeInTheDocument();
    expect(screen.queryByText(/code-mode guard/)).not.toBeInTheDocument();
  });

  it("hides inline leaked read-only control tags without dropping nearby prose", () => {
    renderMarkdown("我先核对实现。<read_only> </read_only> 现在信息已经足够。");

    expect(screen.queryByText(/read_only/)).not.toBeInTheDocument();
    expect(
      screen.getByText("我先核对实现。 现在信息已经足够。"),
    ).toBeInTheDocument();
  });

  it("hides leaked internal renderer component tags outside code fences", () => {
    renderMarkdown("摘要应该在最终回答前展示为 `<TextBlock>`，不是阶段分析。");

    expect(screen.queryByText(/TextBlock/)).not.toBeInTheDocument();
    expect(
      screen.getByText("摘要应该在最终回答前展示为，不是阶段分析。"),
    ).toBeInTheDocument();
  });

  it("keeps read-only tags when they are shown as a code example", async () => {
    renderMarkdown("```xml\n<read_only>\n</read_only>\n```");

    // Settled blocks render through shiki asynchronously — the code text
    // only appears once the highlight is ready (no raw-text flash).
    expect(
      await screen.findByText(settledCodeMatches(/<read_only>/)),
    ).toBeInTheDocument();
  });

  it("keeps internal component tags when they are shown as a fenced code example", async () => {
    renderMarkdown("```tsx\n<TextBlock>hello</TextBlock>\n```");

    expect(
      await screen.findByText(settledCodeMatches(/<TextBlock>/)),
    ).toBeInTheDocument();
  });

  it("keeps markdown controls and assistive technology in the streaming state", () => {
    renderMarkdown("Answer in progress", true);

    const response = screen.getByText("Answer in progress");
    expect(response).toHaveAttribute("data-is-animating", "true");
    expect(response).toHaveAttribute("aria-busy", "true");
  });

  it("settles the markdown renderer when streaming completes", () => {
    renderMarkdown("Final answer");

    const response = screen.getByText("Final answer");
    expect(response).toHaveAttribute("data-is-animating", "false");
    expect(response).not.toHaveAttribute("aria-busy");
  });

  it("toggles from streaming to settled without losing content", () => {
    const content = "Hello world";
    const view = renderMarkdown(content, true);

    expect(screen.getByText(content)).toBeInTheDocument();
    expect(screen.getByText(content)).toHaveAttribute(
      "data-is-animating",
      "true",
    );

    view.rerender(
      <MarkdownContent
        content={content}
        isLoading={false}
        remarkPlugins={[]}
        rehypePlugins={[]}
      />,
    );

    expect(screen.getByText(content)).toBeInTheDocument();
    expect(screen.getByText(content)).toHaveAttribute(
      "data-is-animating",
      "false",
    );
    expect(screen.getByText(content)).not.toHaveAttribute("aria-busy");
  });

  it("does not show aria-busy for completed messages", () => {
    renderMarkdown("Done", false);

    const response = screen.getByText("Done");
    expect(response).not.toHaveAttribute("aria-busy");
  });

  it("renders empty content as null", () => {
    const { container } = renderMarkdown("", false);
    expect(container.firstChild).toBeNull();
  });

  it("renders null content as null", () => {
    const { container } = renderWithProviders(
      <MarkdownContent
        content={null as unknown as string}
        isLoading={false}
        remarkPlugins={[]}
        rehypePlugins={[]}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("preserves content across streaming chunks (appending tokens)", () => {
    const view = renderMarkdown("Hel", true);
    expect(screen.getByText("Hel")).toBeInTheDocument();

    view.rerender(
      <MarkdownContent
        content="Hello"
        isLoading={true}
        remarkPlugins={[]}
        rehypePlugins={[]}
      />,
    );
    expect(screen.getByText("Hello")).toBeInTheDocument();

    view.rerender(
      <MarkdownContent
        content="Hello world"
        isLoading={false}
        remarkPlugins={[]}
        rehypePlugins={[]}
      />,
    );
    expect(screen.getByText("Hello world")).toBeInTheDocument();
    expect(screen.getByText("Hello world")).toHaveAttribute(
      "data-is-animating",
      "false",
    );
  });
});

describe("<MarkdownContent /> code block streaming", () => {
  it("shows code block content immediately during streaming", () => {
    renderMarkdown("```python\nprint('hello')\n```", true);
    expect(screen.getByText(/print\('hello'\)/)).toBeInTheDocument();
  });

  it("renders code language correctly", async () => {
    renderMarkdown("```typescript\nconst x = 1;\n```", false);
    expect(
      await screen.findByText(settledCodeMatches(/const x = 1/)),
    ).toBeInTheDocument();
  });

  it("handles incomplete code fence during streaming", () => {
    renderMarkdown("```javascript\nfunction test() {", true);
    expect(screen.getByText(/function test\(\)/)).toBeInTheDocument();
  });

  it("appends code content across streaming chunks", () => {
    const view = renderMarkdown("```typescript\nconst x = 1;", true);
    expect(screen.getByText(/const x = 1/)).toBeInTheDocument();

    view.rerender(
      <MarkdownContent
        content="```typescript\nconst x = 1;\nconst y = 2;"
        isLoading={true}
        remarkPlugins={[]}
        rehypePlugins={[]}
      />,
    );
    expect(screen.getByText(/const x = 1/)).toBeInTheDocument();
    expect(screen.getByText(/const y = 2/)).toBeInTheDocument();

    view.rerender(
      <MarkdownContent
        content="```typescript\nconst x = 1;\nconst y = 2;\n```"
        isLoading={false}
        remarkPlugins={[]}
        rehypePlugins={[]}
      />,
    );
    expect(screen.getByText(/const x = 1/)).toBeInTheDocument();
    expect(screen.getByText(/const y = 2/)).toBeInTheDocument();
  });

  it("preserves code block from streaming to completion without remount flicker", async () => {
    const content = "```python\ndef hello():\n    print('world')\n```";
    const view = renderMarkdown(content, true);
    const firstCode = screen.getByText(/print\('world'\)/);
    expect(firstCode).toBeInTheDocument();

    view.rerender(
      <MarkdownContent
        content={content}
        isLoading={false}
        remarkPlugins={[]}
        rehypePlugins={[]}
      />,
    );
    // After settling, the block re-renders through shiki; the content
    // reappears once the settled highlight resolves.
    expect(
      await screen.findByText(settledCodeMatches(/print\('world'\)/)),
    ).toBeInTheDocument();
  });
});

describe("<MarkdownContent /> streaming edge cases", () => {
  it("handles only whitespace content", () => {
    const { container } = renderMarkdown("   \n\n  ", true);
    expect(container.firstChild).not.toBeNull();
    const el = container.querySelector('[data-is-animating="true"]');
    expect(el).toBeInTheDocument();
  });

  it("handles content that grows from empty to full", () => {
    const view = renderMarkdown("", true);
    expect(view.container.firstChild).toBeNull();

    view.rerender(
      <MarkdownContent
        content="Hello"
        isLoading={true}
        remarkPlugins={[]}
        rehypePlugins={[]}
      />,
    );
    expect(screen.getByText("Hello")).toBeInTheDocument();

    view.rerender(
      <MarkdownContent
        content="Hello world"
        isLoading={false}
        remarkPlugins={[]}
        rehypePlugins={[]}
      />,
    );
    expect(screen.getByText("Hello world")).toBeInTheDocument();
    expect(screen.getByText("Hello world")).toHaveAttribute(
      "data-is-animating",
      "false",
    );
  });

  it("maintains streaming state across multiple rapid updates", () => {
    const view = renderMarkdown("Line 1", true);
    expect(screen.getByText("Line 1")).toHaveAttribute(
      "data-is-animating",
      "true",
    );

    view.rerender(
      <MarkdownContent
        content="Line 1\nLine 2"
        isLoading={true}
        remarkPlugins={[]}
        rehypePlugins={[]}
      />,
    );
    expect(screen.getByText(/Line 1/)).toBeInTheDocument();
    expect(screen.getByText(/Line 2/)).toBeInTheDocument();

    view.rerender(
      <MarkdownContent
        content="Line 1\nLine 2\nLine 3"
        isLoading={true}
        remarkPlugins={[]}
        rehypePlugins={[]}
      />,
    );
    expect(screen.getByText(/Line 3/)).toBeInTheDocument();
    expect(screen.getByText(/Line 3/)).toHaveAttribute(
      "data-is-animating",
      "true",
    );
  });

  it("transitions from streaming aria-busy to settled cleanly", () => {
    const view = renderMarkdown("Loading...", true);
    const el = screen.getByText("Loading...");
    expect(el).toHaveAttribute("aria-busy", "true");

    view.rerender(
      <MarkdownContent
        content="Loading..."
        isLoading={false}
        remarkPlugins={[]}
        rehypePlugins={[]}
      />,
    );
    const settled = screen.getByText("Loading...");
    expect(settled).not.toHaveAttribute("aria-busy");
    expect(settled).toHaveAttribute("data-is-animating", "false");
  });
});
