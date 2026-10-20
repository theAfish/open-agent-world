// @vitest-environment jsdom
import { cleanup, act, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLocale } from "../i18n";
import type { RunActivityState } from "../state/runActivity";
import { RunActivityDetails, RunActivityStream } from "./RunActivityStream";
import { worldApi } from "../api/client";
import type { SavedModelThinking, SavedInvocation } from "../types/world";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
beforeEach(() => { vi.spyOn(worldApi, "getRunInvocations").mockResolvedValue({ items: [], next_offset: null }); });

vi.mock("./MarkdownMessage", () => ({ MarkdownMessage: ({ content }: { content: string }) => <p>{content}</p> }));

const activity: RunActivityState = { seen: [], truncated: false, items: [
  { id: "1", type: "agent_progress", text: "Read the configuration" },
  { id: "2", type: "tool_started", name: "read_file", call_id: "c", arguments: { path: "config.json", limit: 100 } },
  { id: "3", type: "agent_progress", text: "Compare its values" },
] };

describe("Run activity surface", () => {
  it.each([false, true])("hides model requests in Conversation activity (history=%s), preserving tools", async history => {
    useLocale.setState({ locale: "en" });
    vi.spyOn(worldApi, "getRunReasoning").mockResolvedValue({ items: [], next_offset: null });
    const view = render(history
      ? <RunActivityDetails run={{ run_id: "r", agent_id: "a", status: "succeeded", tool_count: 1, tool_trace: [] }} activity={activity} showModelRequests={false} />
      : <RunActivityStream runId="r" activity={activity} active showModelRequests={false} />);
    if (history) {
      const details = view.container.querySelector(".run-activity-history") as HTMLDetailsElement;
      act(() => { details.open = true; fireEvent(details, new Event("toggle", { bubbles: true })); });
    }
    await waitFor(() => expect(worldApi.getRunInvocations).toHaveBeenCalledWith("r", "tools", 0));
    expect(worldApi.getRunInvocations).not.toHaveBeenCalledWith("r", "models", expect.anything());
    expect(view.container.querySelector(".run-model-requests")).toBeNull();
    expect(screen.getByText("read_file")).toBeTruthy();
  });

  it("restores archived tools, paginates, and reads full args/results only when opened", async () => {
    useLocale.setState({ locale: "en" });
    vi.spyOn(worldApi, "getRunReasoning").mockResolvedValue({ items: [], next_offset: null });
    const row: SavedInvocation = { id: "tool-call:0:builder:call/1", run_id: "r", role: "builder",
      state: "returned", call_id: "call/1", name: "execute_command", run_attempt: 0,
      started_at: "2026-10-06T01:00:00Z", updated_at: "2026-10-06T01:00:01Z", arguments_preview: "short preview" };
    vi.mocked(worldApi.getRunInvocations).mockImplementation(async (_, kind, offset) =>
      kind === "tools" ? { items: offset === 0 ? [row] : [{ ...row, id: "second", call_id: "second" }], next_offset: offset === 0 ? 50 : null }
      : { items: [], next_offset: null });
    const full = vi.spyOn(worldApi, "getRunInvocation").mockRejectedValueOnce(new Error("Archive unavailable"))
      .mockResolvedValue({ ...row, effective_arguments: { argv: ["python3", "probe.py"], content: "source".repeat(20000) },
        response: { stdout: "complete output tail" }, response_complete: true, truncated: false });
    const view = render(<RunActivityStream runId="r" />);
    await waitFor(() => expect(view.container.querySelectorAll(".run-tool")).toHaveLength(2));
    expect(worldApi.getRunInvocations).toHaveBeenCalledWith("r", "tools", 50);
    expect(full).not.toHaveBeenCalled();
    const tool = view.container.querySelector(".run-tool") as HTMLDetailsElement;
    act(() => { tool.open = true; fireEvent(tool, new Event("toggle")); });
    await waitFor(() => expect(within(tool).getByRole("alert")).toBeTruthy());
    fireEvent.click(within(tool).getByText("Retry"));
    await waitFor(() => expect(within(tool).getByText(/complete output tail/)).toBeTruthy());
    expect(within(tool).getByText("source".repeat(20000))).toBeTruthy();
    expect(full).toHaveBeenCalledWith("r", "tools", row.id);
  });

  it("shows exact per-request tokens, unknown interrupted usage and lazy model inputs", async () => {
    useLocale.setState({ locale: "en" });
    vi.spyOn(worldApi, "getRunReasoning").mockResolvedValue({ items: [], next_offset: null });
    const row: SavedInvocation = { id: "model-request:0:builder:1:1", run_id: "r", role: "builder", state: "returned",
      model_request: 1, model_attempt: 1, run_attempt: 0, input_tokens: 42, output_tokens: 13, finish_reason: "STOP",
      started_at: "2026-10-06T01:00:00Z", updated_at: "2026-10-06T01:00:01Z" };
    vi.mocked(worldApi.getRunInvocations).mockImplementation(async (_, kind) => ({ items: kind === "models" ? [row,
      { ...row, id: "second", model_request: 2, state: "interrupted", input_tokens: null, output_tokens: null, finish_reason: null }] : [], next_offset: null }));
    const full = vi.spyOn(worldApi, "getRunInvocation").mockResolvedValue({ ...row,
      request: { config: { system_instruction: "actual instruction" }, contents: ["full input"] }, response: [{ content: "returned output" }] });
    const view = render(<RunActivityStream runId="r" />);
    await waitFor(() => expect(screen.getByText(/Input tokens: 42 · Output tokens: 13/)).toBeTruthy());
    expect(screen.getByText(/Input tokens: Unknown · Output tokens: Unknown/)).toBeTruthy();
    expect(full).not.toHaveBeenCalled();
    const model = view.container.querySelector(".run-model-request") as HTMLDetailsElement;
    act(() => { model.open = true; fireEvent(model, new Event("toggle")); });
    await waitFor(() => expect(within(model).getByText(/actual instruction/)).toBeTruthy());
    expect(full).toHaveBeenCalledWith("r", "models", row.id);
  });
  it("restores thinking after reload, paginates and fetches full text on opening", async () => {
    useLocale.setState({ locale: "en" });
    const body = "long thought ".repeat(6000) + " complete tail";
    const row: SavedModelThinking = { run_id: "native-run", provider_message_id: "thought:1",
      kind: "model_reasoning", role: "builder", model_request: 1, model_attempt: 1,
      text: body.slice(0, 2000), text_length: body.length, preview_truncated: true,
      streaming: false, interrupted: false, truncated: false, revision: 2,
      created_at: "2026-10-06T01:00:00Z", updated_at: "2026-10-06T01:00:01Z" };
    const pages = vi.spyOn(worldApi, "getRunReasoning")
      .mockResolvedValueOnce({ items: [row], next_offset: 50 })
      .mockResolvedValueOnce({ items: [{ ...row, provider_message_id: "thought:2", model_request: 2, text: "Second thought" }], next_offset: null });
    const full = vi.spyOn(worldApi, "getRunReasoningText").mockResolvedValue({ ...row, text: body, preview_truncated: false });
    const view = render(<RunActivityStream runId="native-run" />);
    await waitFor(() => expect(view.container.querySelectorAll(".run-model-reasoning")).toHaveLength(2));
    expect(pages).toHaveBeenCalledWith("native-run", 50);
    expect(full).not.toHaveBeenCalled();
    const details = view.container.querySelector(".run-model-reasoning") as HTMLDetailsElement;
    act(() => { details.open = true; fireEvent(details, new Event("toggle", { bubbles: true })); });
    await waitFor(() => expect(details.querySelector("pre")?.textContent).toBe(body));
    expect(full).toHaveBeenCalledWith("native-run", "thought:1");
    expect(within(details).queryByText("Thinking text truncated")).toBeNull();
  });

  it("shows archive read failures and allows retry without losing the preview", async () => {
    const row: SavedModelThinking = { run_id: "r", provider_message_id: "id", kind: "model_reasoning",
      text: "saved preview", text_length: 13, preview_truncated: false, streaming: false,
      interrupted: true, truncated: false, revision: 1, created_at: "2026-10-06T01:00:00Z", updated_at: "2026-10-06T01:00:00Z" };
    vi.spyOn(worldApi, "getRunReasoning").mockResolvedValue({ items: [row], next_offset: null });
    vi.spyOn(worldApi, "getRunReasoningText").mockRejectedValueOnce(new Error("Read failed"))
      .mockResolvedValueOnce(row);
    const view = render(<RunActivityStream runId="r" />);
    await waitFor(() => expect(view.container.querySelector(".run-model-reasoning")).toBeTruthy());
    const details = view.container.querySelector(".run-model-reasoning") as HTMLDetailsElement;
    act(() => { details.open = true; fireEvent(details, new Event("toggle", { bubbles: true })); });
    await waitFor(() => expect(within(details).getByRole("alert")).toBeTruthy());
    expect(within(details).getByText("saved preview")).toBeTruthy();
    fireEvent.click(within(details).getByText("Retry"));
    await waitFor(() => expect(within(details).queryByRole("alert")).toBeNull());
  });
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

  it("shows live thinking automatically and respects an explicit collapse", () => {
    useLocale.setState({ locale: "en" });
    const item = { id: "thought", type: "agent_progress" as const, kind: "model_reasoning",
      role: "planner", model_request: 1, text: "First", streaming: true };
    const view = render(<RunActivityStream active activity={{ seen: [], truncated: false, items: [item] }} />);
    const details = view.container.querySelector(".run-model-reasoning") as HTMLDetailsElement;
    expect(details.open).toBe(true);
    act(() => { details.open = false; fireEvent(details, new Event("toggle")); });
    view.rerender(<RunActivityStream activity={{ seen: [], truncated: false,
      items: [{ ...item, text: "First second", streaming: false }] }} />);
    expect(details.open).toBe(false);
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
    expect(within(tool).getByRole('row', { name: 'limit 100' })).toBeTruthy();
    view.rerender(<RunActivityStream active activity={{ ...activity, items: activity.items.map(item => item.id === "2"
      ? { ...item, type: "tool_completed", response: { content: "file contents" } } : item) }} />);
    expect(tool.open).toBe(true);
    expect(within(tool).getByText(/file contents/)).toBeTruthy();
  });
});

describe("Completed Run details", () => {
  it("distinguishes confirmed structure writes from failed final reply delivery", () => {
    useLocale.setState({ locale: "en" });
    render(<RunActivityDetails run={{ run_id: "failed", agent_id: "agent", status: "failed",
      tool_count: 1, tool_trace: [], delivery_state: "failed",
      structure_outcomes: { structure: { phase: "confirmed", revision: 2 } } }} />);
    expect(screen.getByText(/Structure verified; final reply failed/)).toBeTruthy();
  });
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
    expect(within(tool).getByRole('row', { name: 'limit 100' })).toBeTruthy();
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
