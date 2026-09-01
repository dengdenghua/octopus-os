import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  Card,
  CardHeader,
  CardFooter,
  CardTitle,
  CardDescription,
  CardContent,
  CardAction,
} from "./card";

describe("Card", () => {
  it("renders card with content", () => {
    render(
      <Card>
        <CardContent>Card content</CardContent>
      </Card>,
    );
    expect(screen.getByText("Card content")).toBeInTheDocument();
  });

  it("renders complete card structure", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Card Title</CardTitle>
          <CardDescription>Card description</CardDescription>
        </CardHeader>
        <CardContent>Main content</CardContent>
        <CardFooter>Footer content</CardFooter>
      </Card>,
    );

    expect(screen.getByText("Card Title")).toBeInTheDocument();
    expect(screen.getByText("Card description")).toBeInTheDocument();
    expect(screen.getByText("Main content")).toBeInTheDocument();
    expect(screen.getByText("Footer content")).toBeInTheDocument();
  });

  it("renders card with action", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Title</CardTitle>
          <CardAction>
            <button>Action</button>
          </CardAction>
        </CardHeader>
        <CardContent>Content</CardContent>
      </Card>,
    );

    expect(screen.getByRole("button", { name: "Action" })).toBeInTheDocument();
  });

  it("applies custom className to Card", () => {
    render(<Card className="custom-card">Content</Card>);
    expect(screen.getByText("Content")).toHaveClass("custom-card");
  });

  it("applies custom className to CardHeader", () => {
    render(
      <Card>
        <CardHeader className="custom-header">Header</CardHeader>
      </Card>,
    );
    expect(screen.getByText("Header")).toHaveClass("custom-header");
  });

  it("applies custom className to CardTitle", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle className="custom-title">Title</CardTitle>
        </CardHeader>
      </Card>,
    );
    expect(screen.getByText("Title")).toHaveClass("custom-title");
  });

  it("applies custom className to CardDescription", () => {
    render(
      <Card>
        <CardHeader>
          <CardDescription className="custom-desc">Description</CardDescription>
        </CardHeader>
      </Card>,
    );
    expect(screen.getByText("Description")).toHaveClass("custom-desc");
  });

  it("applies custom className to CardContent", () => {
    render(
      <Card>
        <CardContent className="custom-content">Content</CardContent>
      </Card>,
    );
    expect(screen.getByText("Content")).toHaveClass("custom-content");
  });

  it("applies custom className to CardFooter", () => {
    render(
      <Card>
        <CardFooter className="custom-footer">Footer</CardFooter>
      </Card>,
    );
    expect(screen.getByText("Footer")).toHaveClass("custom-footer");
  });

  it("forwards data attributes", () => {
    render(<Card data-testid="test-card">Content</Card>);
    expect(screen.getByTestId("test-card")).toBeInTheDocument();
  });
});
