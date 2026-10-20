// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { useLocale } from "../i18n";
import { AgentRunHistory } from "./AgentRunHistory";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

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
  expect(runs).toHaveBeenCalledWith("agent");
  const details = view.container.querySelector(".run-activity-history") as HTMLDetailsElement;
  act(() => { details.open = true; fireEvent(details, new Event("toggle", { bubbles: true })); });
  await waitFor(() => expect(view.container.querySelector(".run-model-reasoning pre")?.textContent).toBe("persisted Agent thinking"));
  expect(archive).toHaveBeenCalledWith("native-run", 0);
});
