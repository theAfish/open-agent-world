// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RunLogModal, repeatedCall } from "../../../plugins/knowledge_base/frontend/RunLogModal";

vi.mock("@oaw/plugin-api", async () => ({
  t: (value: string, params?: Record<string, unknown>) => value.replace(/\{(v\d+)\}/g, (_, key: string) => String(params?.[key])),
  hostApiBase: "/api",
  Modal: (await import("../components/Modal")).Modal,
}));

const tool = (n: number, args = '{"knowledge": "kb", "projection_id": "p"}', name = "knowledge_projection_prompt") => ({
  id: `tool-${n}`, name, state: "returned", role: "agent", started_at: `2026-11-02T06:33:${String(10 + n).padStart(2, "0")}Z`,
  returned_at: `2026-11-02T06:33:${String(11 + n).padStart(2, "0")}Z`, arguments_preview: args });
const model = (n: number) => ({ id: `model-${n}`, state: "returned", role: "agent", model_request: n, input_tokens: 9000 + n, output_tokens: 40,
  started_at: `2026-11-02T06:33:${String(10 + n).padStart(2, "0")}Z`, returned_at: `2026-11-02T06:33:${String(11 + n).padStart(2, "0")}Z` });

function serve(routes: Record<string, unknown>) {
  const fetchMock = vi.fn(async (url: string) => {
    const hit = Object.entries(routes).find(([key]) => url.startsWith(`/api${key}`));
    return { ok: !!hit, json: async () => hit?.[1] };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("flags consecutive identical calls, and only those", () => {
  const rows = [1, 2, 3, 4].map(n => ({ ...tool(n), kind: "tools" as const }));
  expect(repeatedCall(rows)).toEqual({ name: "knowledge_projection_prompt", count: 4 });
  expect(repeatedCall([tool(1), tool(2), { ...tool(3), arguments_preview: '{"offset": 6000}' }, tool(4)].map(row => ({ ...row, kind: "tools" as const })))).toBeUndefined();
  expect(repeatedCall([...[1, 2].map(n => ({ ...tool(n), kind: "tools" as const })), { ...model(1), name: "", kind: "models" as const }])).toBeUndefined();
});

it("shows an agent run's model requests and tool calls, warns about a repeated call, and opens a call's input and output", async () => {
  const fetchMock = serve({
    "/runs?agent_id=agent-a": [{ run_id: "run-live", status: "running", created_at: "2026-11-02T06:33:00Z" }],
    "/runs/run-live/invocations/models/model-1": { response: { text: "I will read the next window" } },
    "/runs/run-live/invocations/tools/tool-2": { arguments: { projection_id: "p" }, response: { markdown: "Fatigue crack growth…", has_more: true } },
    "/runs/run-live/invocations/models": { items: [model(1), model(2)], total: 2 },
    "/runs/run-live/invocations/tools": { items: [1, 2, 3, 4].map(n => tool(n)), total: 4 } });
  const onStop = vi.fn();
  render(<RunLogModal agentId="agent-a" events={[]} stopping={false} canStop onStop={onStop} onClose={() => undefined} />);
  const list = await screen.findByRole("list", { name: "Model requests and tool calls" });
  // The live run is chosen without being told which, and both kinds interleave by time.
  expect(within(list).getAllByRole("listitem").map(item => item.getAttribute("data-kind"))).toEqual(["models", "tools", "models", "tools", "tools", "tools"]);
  expect(screen.getByText("Running")).toBeInTheDocument();
  expect((await screen.findByRole("alert")).textContent).toContain("knowledge_projection_prompt 4 times in a row");
  // Details are read only when a row is opened.
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/invocations/tools/tool-2"))).toBe(false);
  fireEvent.click(within(list).getAllByRole("button", { name: /knowledge_projection_prompt/ })[1]);
  expect((await screen.findAllByText(/Fatigue crack growth/))[0]).toBeInTheDocument();
  expect(screen.getByText("Input")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Stop extraction" }));
  expect(onStop).toHaveBeenCalledTimes(1);
});

it("shows what a host model streamed when there is no agent, and offers Stop only while something can be stopped", async () => {
  serve({});
  const events = [{ at: "2026-11-02T06:30:00Z", stage: "projection", message: "Reading original Markdown", done: 0, total: 2 },
    { at: "2026-11-02T06:30:20Z", stage: "projection", message: "Saving the result", done: 1, total: 2 }];
  const view = render(<RunLogModal events={events} stopping={false} canStop={false} onStop={() => undefined} onClose={() => undefined} />);
  expect(screen.getByText(/Reading original Markdown/).textContent).toContain("0/2");
  expect(screen.getByText(/Saving the result/).textContent).toContain("1/2");
  expect(screen.queryByRole("button", { name: "Stop extraction" })).toBeNull();
  view.rerender(<RunLogModal events={events} stopping canStop onStop={() => undefined} onClose={() => undefined} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Stopping…" })).toBeDisabled());
});
