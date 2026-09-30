// @vitest-environment jsdom
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
  it("keeps provider thinking separate from the final answer", () => {
    useLocale.setState({ locale: "en" });
    const thinking: RunActivityState = { seen: [], truncated: false, items: [
      { id: "thought", type: "agent_progress", kind: "model_reasoning", role: "atom_sculptor",
        model_request: 1, text: "Provider reasoning", truncated: false },
      { id: "answer", type: "agent_message", text: "Final answer" },
    ] };
    const view = render(<RunActivityStream activity={thinking} />);
    const details = view.container.querySelector(".run-model-reasoning") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(within(details).getByText("Model thinking · atom sculptor #1")).toBeTruthy();
    expect(within(details).getByText("Provider reasoning")).toBeTruthy();
    expect(screen.getByText("Final answer")).toBeTruthy();
  });

  it("opens live thinking automatically and keeps it open after completion", () => {
    useLocale.setState({ locale: "en" });
    const item = { id: "thought", type: "agent_progress" as const, kind: "model_reasoning",
      role: "planner", model_request: 1, text: "First", streaming: true };
    const view = render(<RunActivityStream active activity={{ seen: [], truncated: false, items: [item] }} />);
    const details = view.container.querySelector(".run-model-reasoning") as HTMLDetailsElement;
    expect(details.open).toBe(true);
    view.rerender(<RunActivityStream activity={{ seen: [], truncated: false,
      items: [{ ...item, text: "First second", streaming: false }] }} />);
    expect(details.open).toBe(true);
    expect(within(details).getByText("First second")).toBeTruthy();
  });

  it("labels interrupted reasoning as an incomplete preview", () => {
    useLocale.setState({ locale: "en" });
    render(<RunActivityStream activity={{ seen: [], truncated: false, items: [
      { id: "thought", type: "agent_progress", kind: "model_reasoning",
        role: "structure_builder", model_request: 3, text: "Partial", streaming: false, interrupted: true },
    ] }} />);
    expect(screen.getByText("Model thinking · structure builder #3 · Incomplete preview")).toBeTruthy();
  });

  it("repairs legacy token-by-token line breaks only in saved thinking", () => {
    useLocale.setState({ locale: "en" });
    const oldText = "Let\n\n me\n\n plan\n\n the\n\n request.\n\n Then\n\n delegate\n\n the\n\n task.";
    const item = { id: "thought", type: "agent_progress" as const, kind: "model_reasoning",
      role: "planner", text: oldText, streaming: false };
    const view = render(<RunActivityStream activity={{ seen: [], truncated: false, items: [item] }} />);
    expect(view.container.querySelector(".run-model-reasoning pre")?.textContent)
      .toBe("Let me plan the request. Then delegate the task.");
    view.rerender(<RunActivityStream activity={{ seen: [], truncated: false,
      items: [{ ...item, streaming: true }] }} />);
    expect(view.container.querySelector(".run-model-reasoning pre")?.textContent).toBe(oldText);
  });

  it("preserves genuine paragraph breaks in saved thinking", () => {
    const text = "First complete paragraph.\n\nSecond complete paragraph.";
    const view = render(<RunActivityStream activity={{ seen: [], truncated: false, items: [
      { id: "thought", type: "agent_progress", kind: "model_reasoning", text, streaming: false },
    ] }} />);
    expect(view.container.querySelector(".run-model-reasoning pre")?.textContent).toBe(text);
  });

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
