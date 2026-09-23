// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { PluginViewProps } from "./sdk";
import { Preview, Workspace } from "../../../plugins/knowledge/frontend/vectors";

afterEach(cleanup);
const status = { mode: "lexical", embedding: { configured: false, model: null, endpoint: null }, passages: 12, page_passages: 11, notes: 1,
  with_vectors: 0, without_vectors: 12, max_passages: 20000, models: [], hint: "No embedding service is configured: search is lexical (BM25) only.",
  papers: [{ paper: "paper-1", page_passages: 11, notes: 1, pages: 3, with_vectors: 0, stale: 0, updated_at: "2026-01-01" }],
  jobs: [{ id: "j1", state: "interrupted", op: "ingest", papers: ["paper-1"], model: "m", passages: 11, embedded: 0, error: "Interrupted", started_at: "2026-01-01", finished_at: null, started_by: "a" }] };
const hit = { passage: 7, kind: "page", paper: "paper-1", page: 3, heading: "", cite: "paper-1#p3", text: "Supported by the NSFC.",
  score: 0.0164, scores: { lexical_rank: 1, bm25: 2.5 }, stale: false };

function setup() {
  const action = vi.fn(async (name: string, _args: Record<string, unknown>): Promise<Record<string, unknown>> => {
    if (name === "ui_status") return status;
    if (name === "ui_search") return { mode: "lexical", model: null, results: [hit], warning: "No embedding service is configured, so this is a lexical (BM25) search." };
    if (name === "ui_remove") return { removed: 11, passages: [], missing: [] };
    return { entries: [] };
  });
  const props = { card: { id: "vec" }, host: { resourceAction: action } } as unknown as PluginViewProps;
  return { action, props };
}

it("previews counts and the honest search mode", async () => {
  const { props } = setup();
  render(<Preview {...props} />);
  expect(await screen.findByText("12 passages · 1 Paper")).toBeTruthy();
  expect(screen.getByText("lexical search (BM25)")).toBeTruthy();
});

it("searches with a mode, shows scores and removes a Paper's pages after confirmation", async () => {
  const { action, props } = setup();
  render(<Workspace {...props} />);
  expect(await screen.findByText("lexical only")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Search passages"), { target: { value: "NSFC" } });
  fireEvent.change(screen.getByLabelText("Search mode"), { target: { value: "hybrid" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByText("paper-1#p3")).toBeTruthy();
  expect(screen.getByText(/BM25 #1 \(2\.50\)/)).toBeTruthy();
  expect(screen.getByText(/so this is a lexical/)).toBeTruthy();
  expect(action).toHaveBeenCalledWith("ui_search", { query: "NSFC", limit: 20, mode: "hybrid" });

  fireEvent.click(screen.getByRole("tab", { name: "Papers (1)" }));
  fireEvent.click(screen.getByRole("button", { name: "Remove pages of paper-1" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm remove" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("ui_remove", { papers: ["paper-1"], note: "Removed in the workspace" }));

  fireEvent.click(screen.getByRole("tab", { name: "Jobs" }));
  expect(screen.getByText("interrupted")).toBeTruthy();
});

it("keeps a search hit and shows the error when removing it fails", async () => {
  const { action, props } = setup();
  action.mockImplementation(async (name: string) => {
    if (name === "ui_status") return status;
    if (name === "ui_search") return { mode: "lexical", model: null, results: [hit] };
    if (name === "ui_remove") throw new Error("Store is busy");
    return { entries: [] };
  });
  render(<Workspace {...props} />);
  await screen.findByText("lexical only");
  fireEvent.change(screen.getByLabelText("Search passages"), { target: { value: "NSFC" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  fireEvent.click(await screen.findByRole("button", { name: "Remove passage 7" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Store is busy");
  expect(screen.getByText("paper-1#p3")).toBeTruthy();
});
