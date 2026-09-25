import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useLocale } from "../i18n";
import type { RunActivityState } from "../state/runActivity";
import { RunActivityDetails, RunActivityStream } from "./RunActivityStream";

vi.mock("./MarkdownMessage", () => ({ MarkdownMessage: ({ content }: { content: string }) => <p>{content}</p> }));

const activity: RunActivityState = { seen: [], truncated: false, items: [
  { id: "1", type: "agent_progress", text: "Read the configuration" },
  { id: "2", type: "tool_started", name: "read_file", call_id: "c", arguments: { path: "config.json", limit: 100 } },
  { id: "3", type: "agent_progress", text: "Compare its values" },
] };

describe("Run activity surface", () => {
  it("puts Stop below the ordered stream without a large running heading", () => {
    useLocale.setState({ locale: "en" });
    const stop = vi.fn();
    const { container } = render(<RunActivityStream activity={activity} active onStop={stop} />);
    expect(container.querySelector(".conversation-run-heading")).toBeNull();
    expect(container.querySelector(".run-activity-items")?.textContent).toMatch(/Read the configuration.*read_file.*Compare its values/);
    const button = screen.getByRole("button", { name: "Stop" });
    expect(button.closest(".run-activity-footer")).toBeTruthy();
    expect(container.querySelector(".run-activity-items")!.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(button);
    expect(stop).toHaveBeenCalledOnce();
  });

  it("mounts tool details only when opened and keeps them open on completion", () => {
    useLocale.setState({ locale: "en" });
    const view = render(<RunActivityStream activity={activity} active />);
    const tool = view.container.querySelector(".run-tool") as HTMLDetailsElement;
    expect(tool.querySelector("pre")).toBeNull();
    act(() => { tool.open = true; fireEvent(tool, new Event("toggle", { bubbles: true })); });
    expect(within(tool).getByText(/"limit": 100/)).toBeTruthy();
    view.rerender(<RunActivityStream active activity={{ ...activity, items: activity.items.map(item => item.id === "2"
      ? { ...item, type: "tool_completed", response: { content: "file contents" } } : item) }} />);
    expect(tool.open).toBe(true);
    expect(within(tool).getByText(/file contents/)).toBeTruthy();
  });
});

describe("Completed Run details", () => {
  it("keeps the final reply outside expanded details while preserving commentary and tools", () => {
    useLocale.setState({ locale: "en" });
    const completed: RunActivityState = { ...activity, items: [
      ...activity.items,
      { id: "4", type: "agent_message", text: "Intermediate explanation" },
      { id: "5", type: "agent_message", text: "Final answer" },
    ] };
    const view = render(<>
      <RunActivityDetails activity={completed} finalReply="Final answer" />
      <p>Final answer</p>
    </>);
    const details = view.container.querySelector(".run-activity-history") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    act(() => { details.open = true; fireEvent(details, new Event("toggle", { bubbles: true })); });
    expect(within(details).queryByText("Final answer")).toBeNull();
    expect(screen.getAllByText("Final answer")).toHaveLength(1);
    expect(within(details).getByText("Intermediate explanation")).toBeTruthy();
    expect(within(details).getByText("Read the configuration")).toBeTruthy();
    expect(within(details).getByText("read_file")).toBeTruthy();
    const tool = details.querySelector(".run-tool") as HTMLDetailsElement;
    act(() => { tool.open = true; fireEvent(tool, new Event("toggle", { bubbles: true })); });
    expect(within(tool).getByText(/"limit": 100/)).toBeTruthy();
    expect(details.open).toBe(true);
    expect(completed.items.at(-1)?.text).toBe("Final answer");
  });

  it("keeps live text visible before the durable reply arrives", () => {
    const pending: RunActivityState = { seen: [], truncated: false,
      items: [{ id: "1", type: "agent_message", text: "Final answer" }] };
    const { container } = render(<RunActivityStream activity={pending} active />);
    expect(within(container).getByText("Final answer")).toBeTruthy();
  });

  it("does not leave an empty disclosure when the final reply was the only activity", () => {
    const { container } = render(<RunActivityDetails finalReply="Final answer" activity={{ seen: [], truncated: false,
      items: [{ id: "1", type: "agent_message", text: "Final answer" }] }} />);
    expect(container.querySelector(".run-activity-history")).toBeNull();
  });
});
