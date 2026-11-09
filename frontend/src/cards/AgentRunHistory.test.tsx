// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { useLocale } from "../i18n";
import { AgentRunHistory } from "./AgentRunHistory";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("opens the same native Run thinking archive from the Agent work area", async () => {
  useLocale.setState({ locale: "en" });
  const runs = vi.spyOn(worldApi, "agentRuns").mockResolvedValue([{ run_id: "native-run", agent_id: "agent",
    status: "failed", started_at: "2026-10-06T01:00:00Z", finished_at: "2026-10-06T01:00:05Z", lifecycle: {} }]);
  const archive = vi.spyOn(worldApi, "getRunReasoning").mockResolvedValue({ items: [{
    run_id: "native-run", provider_message_id: "thought:1", kind: "model_reasoning", role: "builder",
    text: "persisted Agent thinking", text_length: 24, preview_truncated: false, streaming: false,
    interrupted: true, truncated: false, revision: 1,
    created_at: "2026-10-06T01:00:00Z", updated_at: "2026-10-06T01:00:01Z",
  }], next_offset: null });
  const view = render(<AgentRunHistory agentId="agent" active={false} />);
  expect(runs).not.toHaveBeenCalled();
  const outer = view.container.querySelector("details") as HTMLDetailsElement;
  act(() => { outer.open = true; fireEvent(outer, new Event("toggle", { bubbles: true })); });
  await waitFor(() => expect(view.container.querySelector(".run-activity-history")).toBeTruthy());
  expect(runs).toHaveBeenCalledWith("agent", undefined);
  const details = view.container.querySelector(".run-activity-history") as HTMLDetailsElement;
  act(() => { details.open = true; fireEvent(details, new Event("toggle", { bubbles: true })); });
  await waitFor(() => expect(view.container.querySelector(".run-model-reasoning pre")?.textContent).toBe("persisted Agent thinking"));
  expect(archive).toHaveBeenCalledWith("native-run", 0);
});

it("requests older summaries by cursor only when Load more is selected", async () => {
  const page = Array.from({ length: 10 }, (_, index) => ({ run_id: `run-${index}`, agent_id: "agent", status: "succeeded", lifecycle: {} }));
  const runs = vi.spyOn(worldApi, "agentRuns").mockResolvedValueOnce(page).mockResolvedValueOnce([
    { run_id: "older", agent_id: "agent", status: "succeeded", lifecycle: {} },
  ]);
  const archive = vi.spyOn(worldApi, "getRunReasoning");
  const view = render(<AgentRunHistory agentId="agent" active={false} />);
  const outer = view.container.querySelector("details")!;
  act(() => { outer.open = true; fireEvent(outer, new Event("toggle")); });
  fireEvent.click(await view.findByText("Load more"));
  await waitFor(() => expect(view.container.querySelectorAll(".run-activity-history")).toHaveLength(11));
  expect(runs).toHaveBeenLastCalledWith("agent", "run-9");
  expect(archive).not.toHaveBeenCalled();
  expect(view.queryByText("Load more")).toBeNull();
});


it("keeps truncated oldest rows reachable after an exhausted history receives a new Run", async () => {
  const page = (newest: number, count: number) => Array.from({ length: count }, (_, index) => ({
    run_id: `run-${newest - index}`, agent_id: "agent", status: "succeeded", lifecycle: {},
  }));
  const runs = vi.spyOn(worldApi, "agentRuns").mockResolvedValueOnce(page(10, 10))
    .mockResolvedValueOnce(page(0, 1)).mockResolvedValueOnce(page(11, 10)).mockResolvedValueOnce(page(0, 1));
  const view = render(<AgentRunHistory agentId="agent" active={false} />);
  const outer = view.container.querySelector("details")!;
  await act(async () => { outer.open = true; fireEvent(outer, new Event("toggle")); });
  await act(async () => fireEvent.click(view.getByText("Load more")));
  expect(view.container.querySelectorAll(".run-activity-history")).toHaveLength(11);
  expect(view.queryByText("Load more")).toBeNull();
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(view.container.querySelectorAll(".run-activity-history")).toHaveLength(11);
  await act(async () => fireEvent.click(view.getByText("Load more")));
  expect(runs).toHaveBeenLastCalledWith("agent", "run-1");
  expect(view.container.querySelectorAll(".run-activity-history")).toHaveLength(12);
  expect(view.queryByText("Load more")).toBeNull();
});

it("restarts the cursor when a refreshed recent page no longer overlaps loaded history", async () => {
  const page = (newest: number) => Array.from({ length: 10 }, (_, index) => ({
    run_id: `run-${newest - index}`, agent_id: "agent", status: "succeeded", lifecycle: {},
  }));
  const runs = vi.spyOn(worldApi, "agentRuns").mockResolvedValueOnce(page(19)).mockResolvedValueOnce(page(9))
    .mockResolvedValueOnce(page(34)).mockResolvedValueOnce(page(24));
  const view = render(<AgentRunHistory agentId="agent" active={false} />);
  const outer = view.container.querySelector("details")!;
  await act(async () => { outer.open = true; fireEvent(outer, new Event("toggle")); });
  await act(async () => fireEvent.click(view.getByText("Load more")));
  expect(view.container.querySelectorAll(".run-activity-history")).toHaveLength(20);
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(view.container.querySelectorAll(".run-activity-history")).toHaveLength(10);
  await act(async () => fireEvent.click(view.getByText("Load more")));
  expect(runs).toHaveBeenLastCalledWith("agent", "run-25");
  expect(view.container.querySelectorAll(".run-activity-history")).toHaveLength(20);
});

it("refreshes an older displayed running Run and stops its archive polling after completion", async () => {
  vi.useFakeTimers();
  const head = Array.from({ length: 10 }, (_, index) => ({
    run_id: `recent-${index}`, agent_id: "agent", status: "succeeded", lifecycle: {},
  }));
  const older = { run_id: "older-running", agent_id: "agent", status: "running", lifecycle: {} };
  vi.spyOn(worldApi, "agentRuns").mockResolvedValueOnce(head).mockResolvedValueOnce([older]).mockResolvedValue(head);
  const summary = vi.spyOn(worldApi, "runSummary").mockResolvedValue({ ...older, status: "succeeded" });
  const thinking = vi.spyOn(worldApi, "getRunReasoning").mockResolvedValue({ items: [], next_offset: null });
  const invocations = vi.spyOn(worldApi, "getRunInvocations").mockResolvedValue({ items: [], next_offset: null });
  const view = render(<AgentRunHistory agentId="agent" active={false} />);
  const outer = view.container.querySelector("details")!;
  await act(async () => { outer.open = true; fireEvent(outer, new Event("toggle")); });
  await act(async () => fireEvent.click(view.getByText("Load more")));
  expect(view.container.querySelectorAll(".run-activity-stream")).toHaveLength(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(thinking).toHaveBeenCalledTimes(2);
  expect(summary).not.toHaveBeenCalled();
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(summary).toHaveBeenCalledTimes(1);
  expect(summary).toHaveBeenCalledWith("older-running");
  expect(view.container.querySelectorAll(".run-activity-history")).toHaveLength(11);
  expect(view.container.querySelectorAll(".run-activity-stream")).toHaveLength(0);
  const archiveCalls = invocations.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(thinking).toHaveBeenCalledTimes(2);
  expect(invocations).toHaveBeenCalledTimes(archiveCalls);
  await act(async () => { outer.open = false; fireEvent(outer, new Event("toggle")); });
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(summary).toHaveBeenCalledTimes(1);
});
