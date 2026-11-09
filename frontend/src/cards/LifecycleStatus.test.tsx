// @vitest-environment jsdom
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LifecycleDisclosure, LifecycleStatus } from "./LifecycleStatus";
import { worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("restores authoritative cancellation and artifact state after reconnect without submitting a Run", async () => {
  const snapshot = { runs: [{ run_id: "durable-attempt", agent_id: "agent", status: "cancelled",
    lifecycle: { owner_id: "agent", holds_capacity: false, cleanup: "failed", cleanup_reason: "Remote termination unconfirmed" },
    artifacts: [{ version_id: "retained-version", name: "Report", state: "ready" }] }], commands: [], node_cleanup: [], artifact_cleanup: [], instances: [] };
  const load = vi.spyOn(worldApi, "lifecycle").mockResolvedValue(snapshot);
  const run = vi.spyOn(worldApi, "runAgent");
  useWorldStore.setState({ socketState: "closed", events: [] });
  render(<LifecycleStatus agentId="agent" />);
  await screen.findByText("Remote termination unconfirmed");
  expect(screen.getByText(/retained-version/)).toBeTruthy();
  await act(async () => useWorldStore.setState({ socketState: "live" }));
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  expect(run).not.toHaveBeenCalled();
});

it("does not fetch a collapsed panel and stops polling when it closes", async () => {
  vi.useFakeTimers();
  const load = vi.spyOn(worldApi, "lifecycle").mockResolvedValue({ runs: [{ run_id: "active", agent_id: "agent", status: "running", lifecycle: {}, artifacts: [] }], commands: [], node_cleanup: [], artifact_cleanup: [], instances: [] });
  const view = render(<LifecycleDisclosure agentId="agent" />);
  const details = view.container.querySelector("details")!;
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
  expect(load).not.toHaveBeenCalled();
  await act(async () => { details.open = true; fireEvent(details, new Event("toggle")); });
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => { details.open = false; fireEvent(details, new Event("toggle")); });
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(load).toHaveBeenCalledTimes(1);
});

it("coalesces event and manual refreshes while a request is pending", async () => {
  let resolve!: (value: unknown) => void;
  const pending = new Promise(value => { resolve = value; });
  const empty = { runs: [], commands: [], node_cleanup: [], artifact_cleanup: [], instances: [] };
  const load = vi.spyOn(worldApi, "lifecycle").mockReturnValueOnce(pending).mockResolvedValue(empty);
  useWorldStore.setState({ events: [] });
  render(<LifecycleStatus agentId="agent" />);
  fireEvent.click(screen.getByText("Refresh state"));
  act(() => useWorldStore.setState({ events: [{ id: "new-run", type: "run_started", timestamp: "now", payload: {} }] }));
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => resolve(empty));
  expect(load).toHaveBeenCalledTimes(2);
});
