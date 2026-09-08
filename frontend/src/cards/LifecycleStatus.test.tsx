// @vitest-environment jsdom
import React from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LifecycleStatus } from "./LifecycleStatus";
import { worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

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
