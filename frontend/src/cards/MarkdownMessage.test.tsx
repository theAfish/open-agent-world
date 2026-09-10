// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { MarkdownMessage } from "./MarkdownMessage";

afterEach(cleanup);

it("renders Markdown blocks and GFM inside one message bubble", () => {
  const { container } = render(<MarkdownMessage content={[
    "## Progress", "", "**Ready** and `inline`", "", "- First", "- Second", "",
    "```ts", "const answer = 42;", "```", "",
    "| Name | Value |", "| --- | --- |", "| Result | 42 |", "",
    "[Docs](https://example.com)",
  ].join("\n")} />);
  expect(screen.getByRole("heading", { name: "Progress" })).toBeTruthy();
  expect(container.querySelector("strong")?.textContent).toBe("Ready");
  expect(screen.getAllByRole("listitem")).toHaveLength(2);
  expect(container.querySelector("pre code")?.textContent).toContain("const answer = 42;");
  expect(screen.getByRole("table")).toBeTruthy();
  expect(screen.getByRole("link").getAttribute("rel")).toBe("noopener noreferrer");
  expect(container.querySelectorAll(".conversation-markdown")).toHaveLength(1);
});

it("does not execute HTML or unsafe Markdown links and accepts incomplete output", () => {
  const { container, rerender } = render(<MarkdownMessage content={'<script>alert(1)</script>\n\n[bad](javascript:alert)'} />);
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("a")?.getAttribute("href")).not.toContain("javascript:");
  rerender(<MarkdownMessage content={"Working **on"} />);
  expect(screen.getByText("Working **on")).toBeTruthy();
  rerender(<MarkdownMessage content={"Working **on it**"} />);
  expect(container.querySelector("strong")?.textContent).toBe("on it");
});
