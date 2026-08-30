import { expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderWithProviders } from "@/test/harness";

import {
  MessageAttachment,
  MessageBranch,
  MessageBranchContent,
  MessageBranchNext,
  MessageBranchPage,
  MessageBranchPrevious,
  MessageBranchSelector,
} from "./message";

it("localizes message branch navigation without dropping caller attributes", async () => {
  const { container } = renderWithProviders(
    <MessageBranch>
      <MessageBranchContent>
        <div key="first">第一个版本</div>
        <div key="second">第二个版本</div>
      </MessageBranchContent>
      <MessageBranchSelector className="branch-controls" from="assistant">
        <MessageBranchPrevious />
        <MessageBranchPage />
        <MessageBranchNext className="next-version" />
      </MessageBranchSelector>
    </MessageBranch>,
    { locale: "zh-CN" },
  );

  expect(
    await screen.findByRole("button", { name: "上一个回复版本" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "下一个回复版本" }),
  ).toBeInTheDocument();
  expect(screen.getByText("第 1 / 2 个版本")).toBeInTheDocument();
  expect(container.querySelector(".branch-controls")).toHaveAttribute(
    "data-from",
    "assistant",
  );
  expect(screen.getByRole("button", { name: "下一个回复版本" })).toHaveClass(
    "next-version",
  );
});

it("localizes unnamed image attachments and their remove action", () => {
  const onRemove = vi.fn();
  renderWithProviders(
    <MessageAttachment
      data={{
        type: "file",
        mediaType: "image/png",
        url: "data:image/png;base64,iVBORw0KGgo=",
      }}
      onRemove={onRemove}
    />,
    { locale: "zh-CN" },
  );

  expect(screen.getByRole("img", { name: "图片附件" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "移除附件" })).toBeInTheDocument();
});
