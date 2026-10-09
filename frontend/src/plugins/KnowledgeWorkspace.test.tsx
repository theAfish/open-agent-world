// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { PluginViewProps } from "./sdk";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard, WorldEdge } from "../types/world";
import plugin from "../../../plugins/knowledge_base/frontend";

vi.mock("@oaw/plugin-api", () => ({
  t: (value: string, params?: Record<string, unknown>) => params
    ? value.replace(/\{(v\d+)\}/g, (_, key: string) => String(params[key]))
    : value,
  useLocale: () => undefined,
  useNestedFlowGestures: () => null,
  useWorkspaceSections: () => ({ isInline: () => true }),
  WorkspaceSection: ({ title, children }: { title: string; children: ReactNode }) =>
    <section aria-label={title}>{children}</section>,
}));

const SOURCE = { id: "source-1", filename: "sintering.md", media_type: "text/markdown", size: 2048,
  created_at: "2026-09-23T00:00:00Z", markdown: null, record_id: null, projections: 0 };
const CONVERTED = { ...SOURCE, markdown: { artifact_id: "artifact-1", size: 2048, engine: "text" },
  record_id: "record-1" };
const SCHEMA = { id: "schema-1", name: "Process graph", description: "Samples and processes",
  domain: "materials", kind: "literature", version: 1 };
const EXPERIMENT_SCHEMA = { id: "schema-2", name: "Conductivity run", description: null,
  domain: "materials", kind: "experiment", version: 1 };
const EXPERIMENT_PROJECTION = { id: "projection-1", schema_id: "schema-2", record_id: "record-1",
  status: "COMPLETED", valid: true, extracted_at: "2026-09-23T00:00:00Z", summary: "A1" };
const DOCUMENT = { record_id: "record-1", filename: "sintering.md", engine: "text",
  total_characters: 42, offset: 0, markdown: "# Sintering of Si3N4", has_more: false };
const DRAFT = { id: "draft-1", status: "DRAFT", revision: 1, created_by: "user:desktop",
  updated_at: "2026-09-23T00:00:00Z" };

const overview = (counts: Record<string, number> = {}, graphSchemaId: string | null = null) => ({
  collection: { id: "collection-1", name: "Knowledge base" },
  settings: { collection_name: "Knowledge base", pdf_engine: "auto", mineru_base_url: "" },
  engines: ["text"],
  counts: { sources: 1, records: 1, schemas: 1, projections: 0, drafts: 1, pending_review: 1,
    facts: 0, entities: 0, relations: 0, ...counts },
  active_jobs: 0,
  graph_schema_id: graphSchemaId,
});
const EMPTY: Record<string, Record<string, unknown>> = {
  sources: { sources: [] }, schemas: { schemas: [] }, projections: { projections: [] },
  draft: { drafts: [] }, jobs: { jobs: [] }, graph: { entities: [], relations: [], truncated: false },
};

type Action = (name: string, args: Record<string, unknown>, confirm?: boolean) =>
  Promise<Record<string, unknown> | undefined>;

/** Renders the workspace over a stub card, answering whatever the test leaves out. */
const open = (handler: Action, overrides: { card?: Record<string, unknown>; host?: Record<string, unknown> } = {}) => {
  const action = vi.fn(async (name: string, args: Record<string, unknown>, confirm?: boolean) =>
    await handler(name, args, confirm) ?? (name === "overview" ? overview() : EMPTY[name] ?? {}));
  const Workspace = plugin.views.workspace;
  const props = { card: { id: "card-1", name: "Knowledge", config: { default_model: "" }, ...overrides.card },
    host: { resourceAction: action, ...overrides.host } };
  render(<Workspace {...props as unknown as PluginViewProps} />);
  return action;
};

const ORIGINAL_UPDATE_CARD = useWorldStore.getState().updateCard;

it("saves a token through its private HTTP endpoint and keeps it when Settings reopens", async () => {
  let saved = false;
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "PUT") saved = JSON.parse(String(init.body)).value !== null;
    return { ok: true, json: async () => ({ configured: saved, source: saved ? "card" : null }) };
  });
  vi.stubGlobal("fetch", fetchMock);
  const action = open(async () => undefined);
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  const input = await screen.findByLabelText("MinerU token") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "ui-private-token" } });
  fireEvent.click(screen.getByRole("button", { name: "Save token" }));
  await screen.findByText("Saved securely");
  expect(input.value).toBe("");
  expect(fetchMock).toHaveBeenCalledWith("/api/knowledge/card-1/mineru-token", expect.objectContaining({
    method: "PUT", body: JSON.stringify({ value: "ui-private-token" }) }));
  expect(JSON.stringify(action.mock.calls)).not.toContain("ui-private-token");

  fireEvent.click(screen.getByRole("button", { name: "Literature" }));
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  await screen.findByText("Saved securely");
  expect((screen.getByLabelText("MinerU token") as HTMLInputElement).value).toBe("");
  expect((screen.getByRole("button", { name: "Save token" }) as HTMLButtonElement).disabled).toBe(true);
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Remove token" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/knowledge/card-1/mineru-token",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ value: null }) })));
});

it("creates a new group while another group is selected", async () => {
  const group = { id: "group-1", name: "Existing", source_count: 0, is_default: false };
  const action = open(async (name, args) => {
    if (name === "overview") return { ...overview(), groups: [group] };
    if (name === "groups" && args.operation === "create") return { group: { ...group, id: "group-2", name: args.name } };
    return undefined;
  });
  await screen.findByRole("option", { name: "Existing" });
  expect(screen.getAllByLabelText("Group")).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("Group"), { target: { value: "group-1" } });
  fireEvent.click(screen.getByLabelText("Manage groups"));
  await waitFor(() => expect((screen.getByRole("button", { name: "New group" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "New group" }));
  fireEvent.change(screen.getByLabelText("Group name"), { target: { value: "New collection" } });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("groups", { operation: "create", name: "New collection" }, undefined));
  expect(action.mock.calls.some(([name, args]) => name === "groups" && args.operation === "rename")).toBe(false);
});

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ configured: false, source: null }) })));
  // The Graph section draws a React Flow map, which measures itself on mount.
  vi.stubGlobal("ResizeObserver", class {
    observe() {} unobserve() {} disconnect() {}
  });
  useWorldStore.setState({ modelCatalog: { revision: 1, default_model: null, connections: [{
    id: "connection-1", name: "OpenAI", adapter: "openai", base_url: "", enabled: true,
    auth_mode: "api_key", api_key_configured: true,
    models: [{ id: "model-1", name: "gpt-4o", model_id: "gpt-4o", enabled: true }],
  }] } });
});
afterEach(() => {
  cleanup(); vi.unstubAllGlobals();
  // A test may stand in for edges/cards/updateCard to exercise the Settings
  // section's "Connected agents" picker; every other test expects the real,
  // empty-by-default world, so put it back.
  useWorldStore.setState({ edges: [], cards: [], updateCard: ORIGINAL_UPDATE_CARD });
});

it("still shows sources and groups when an older release has not published \"experiments\"", async () => {
  // A release published before Experiments existed grants no "experiments" resource
  // action at all; the deployment bridge answers with a 404-shaped rejection. That
  // must not blank out every other section's data (regression: refresh() used to
  // bundle every call into one Promise.all, so one rejection lost everything).
  const action = open(async name => {
    if (name === "overview") return overview({ entities: 0 });
    if (name === "sources") return { sources: [CONVERTED] };
    if (name === "experiments") throw new Error("This operation is not published");
    return undefined;
  });

  const region = within(await screen.findByRole("region", { name: "Sources" }));
  expect(await region.findByRole("button", { name: /sintering\.md/ })).toBeTruthy();
  await waitFor(() => expect(action).toHaveBeenCalledWith("experiments", expect.anything(), undefined));
});

it("uploads a document and reads its markdown once a batch process job finishes", async () => {
  let uploaded = false;
  let converted = false;
  const action = open(async name => {
    if (name === "sources") return { sources: [!uploaded ? SOURCE : converted ? CONVERTED : SOURCE] };
    if (name === "ingest") {
      uploaded = true;
      return { source: { id: "source-1", filename: "sintering.md" }, group_id: "collection-1" };
    }
    if (name === "process") {
      converted = true;
      return { jobs: [{ source_id: "source-1", filename: "sintering.md",
        job: { id: "job-1", status: "QUEUED" } }] };
    }
    if (name === "jobs") return { jobs: [{ id: "job-1", kind: "pipeline",
      status: converted ? "COMPLETED" : "QUEUED", progress: 1, message: "markdown ready",
      error: null, source_id: "source-1", created_at: "2026-09-23T00:00:00Z", completed_at: null }] };
    if (name === "markdown") return DOCUMENT;
    return undefined;
  });

  expect(await screen.findByRole("button", { name: /sintering\.md.*awaiting conversion/ })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Add documents"), {
    target: { files: [new File(["# Sintering"], "sintering.md", { type: "text/markdown" })] } });
  await waitFor(() => expect(action).toHaveBeenCalledWith("ingest", expect.objectContaining({
    filename: "sintering.md", media_type: "text/markdown" }), undefined));

  // Uploading never converts on its own: select it and process it explicitly.
  fireEvent.click(screen.getByLabelText("Select sintering.md"));
  fireEvent.click(await screen.findByRole("button", { name: "Process 1 selected" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith(
    "process", { source_ids: ["source-1"] }, undefined));

  fireEvent.click(await screen.findByRole("button", { name: /sintering\.md.*Ready/ }));
  expect(await screen.findByRole("heading", { name: "Sintering of Si3N4" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Markdown" }));
  expect(await screen.findByText("# Sintering of Si3N4")).toBeTruthy();
});

it("uploads selected files even when resetting the file input clears its FileList", async () => {
  const action = open(async () => undefined);
  const input = await screen.findByLabelText("Add documents") as HTMLInputElement;
  const file = new File(["%PDF-1.4"], "sample.pdf", { type: "application/pdf" });
  const selected: File[] = [file];
  Object.defineProperty(input, "files", { get: () => selected });
  Object.defineProperty(input, "value", {
    get: () => selected.length ? "C:\\fakepath\\sample.pdf" : "",
    set: (value: string) => { if (value === "") selected.length = 0; },
  });

  fireEvent.change(input);

  await waitFor(() => expect(action).toHaveBeenCalledWith("ingest", expect.objectContaining({
    filename: "sample.pdf", media_type: "application/pdf" }), undefined));
});

it("searches converted documents and shows attributable excerpts", async () => {
  const action = open(async (name, args) => {
    if (name === "search") return { query: args.query, results: [
      { source_id: "source-1", filename: "sintering.md", record_id: "record-1",
        group_id: "group-1", group_name: "Knowledge base",
        heading_path: "Sintering of Si3N4 > Method", excerpt: "Sintered at 1750 C for 2 hours." },
    ] };
    return undefined;
  });
  const region = within(await screen.findByRole("region", { name: "Sources" }));

  fireEvent.change(region.getByPlaceholderText("Search converted documents…"), {
    target: { value: "sintering temperature" } });
  fireEvent.click(region.getByRole("button", { name: "Search" }));

  await waitFor(() => expect(action).toHaveBeenCalledWith(
    "search", expect.objectContaining({ query: "sintering temperature" }), undefined));
  expect(await region.findByText(/sintering\.md.*Knowledge base/)).toBeTruthy();
  expect(await region.findByText(/Sintering of Si3N4 > Method.*Sintered at 1750 C/)).toBeTruthy();
});

it("shows a search error inline instead of crashing the workspace", async () => {
  const action = open(async name => {
    if (name === "search") throw new Error("search index is not ready");
    return undefined;
  });
  const region = within(await screen.findByRole("region", { name: "Sources" }));

  fireEvent.change(region.getByPlaceholderText("Search converted documents…"), {
    target: { value: "anything" } });
  fireEvent.click(region.getByRole("button", { name: "Search" }));

  expect(await region.findByText("search index is not ready")).toBeTruthy();
  expect(action).toHaveBeenCalled();
});

it("projects the selected document through the host bridge, never the plugin", async () => {
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({
    projection: { id: "projection-1", validation: { valid: true } },
    truncated: false, record_id: "record-1" }) }));
  vi.stubGlobal("fetch", fetchMock);
  const action = open(async name => {
    if (name === "sources") return { sources: [CONVERTED] };
    if (name === "schemas") return { schemas: [SCHEMA] };
    if (name === "markdown") return DOCUMENT;
    return undefined;
  });

  fireEvent.click(await screen.findByRole("button", { name: /sintering\.md/ }));
  await screen.findByRole("heading", { name: "Sintering of Si3N4" });
  fireEvent.change(screen.getByLabelText("Extraction schema"), { target: { value: "schema-1" } });
  expect((screen.getByLabelText("Model") as HTMLSelectElement).value).toBe("oaw:model:model-1");
  fireEvent.click(screen.getByRole("button", { name: "Project to JSON" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/knowledge/card-1/project");
  expect(JSON.parse(String(init.body))).toEqual({
    schema_id: "schema-1", model: "oaw:model:model-1", record_id: "record-1" });
  expect(action.mock.calls.some(([name]) => name === "save_projection")).toBe(false);
  expect(await within(await screen.findByRole("region", { name: "Sources" }))
    .findByText("Projection projection-1 saved")).toBeTruthy();
});

it("assembles an experiment record from selected projections through the host bridge", async () => {
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({
    record: { id: "record-x", name: "A1", status: "draft", revision: 1,
      data: { sample_id: "A1", conductivity: 1.3 }, conflicts: [] } }) }));
  vi.stubGlobal("fetch", fetchMock);
  const action = open(async name => {
    if (name === "schemas") return { schemas: [EXPERIMENT_SCHEMA] };
    if (name === "projections") return { projections: [EXPERIMENT_PROJECTION] };
    if (name === "experiments") return { experiments: [] };
    return undefined;
  });

  fireEvent.click(await screen.findByRole("button", { name: "Experiment" }));
  const region = within(await screen.findByRole("region", { name: "Experiments" }));
  fireEvent.click(region.getByLabelText("Select projection projection-1"));
  fireEvent.click(region.getByRole("button", { name: "Assemble experiment record from 1 selected" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/knowledge/card-1/assemble");
  expect(JSON.parse(String(init.body))).toEqual({
    projection_ids: ["projection-1"], model: "oaw:model:model-1" });
  expect(action.mock.calls.some(([name]) => name === "experiment_save")).toBe(false);
  expect(await region.findByText(/Experiment record "A1" assembled/)).toBeTruthy();
});

it("keeps the graph empty until the person confirms the approval", async () => {
  let inReview = false;
  let published = false;
  const action = open(async (name, args, confirm) => {
    if (name === "overview") return overview({ entities: published ? 2 : 0 }, "schema-1");
    if (name === "draft" && args.operation === "list") return { drafts: [DRAFT] };
    if (name === "draft" && args.operation === "get") return { draft: { ...DRAFT,
      status: inReview ? "IN_REVIEW" : DRAFT.status,
      graph: { entities: [{ name: "Si3N4" }, { name: "Sintering" }], relations: [{ type: "processed_by" }] },
      evidence_ids: ["evidence-1"] } };
    if (name === "review" && args.operation === "submit") {
      inReview = true;
      return { decision: "IN_REVIEW" };
    }
    if (name === "review" && args.operation === "approve") {
      if (!confirm) return { status: "confirmation_required",
        reasons: ["Approval publishes this draft as a permanent fact revision."] };
      published = true;
      return { decision: "APPROVED", fact: { id: "fact-1" }, graph: { entities: 2, relations: 1 } };
    }
    if (name === "graph" && published) return { truncated: false, relations: [],
      entities: [{ id: "entity-1", type: "material", name: "Si3N4", properties: {} }] };
    return undefined;
  });

  // The whole project/draft/review/publish pipeline lives in Graph now, gated on
  // a graph schema being chosen — "schema-1" above is what unlocks it here.
  fireEvent.click(await screen.findByRole("tab", { name: "Graph" }));
  fireEvent.click(await screen.findByRole("button", { name: /draft-1/ }));
  // Approving requires IN_REVIEW; "Submit for review" is what mkb needs to get there.
  fireEvent.click(await screen.findByRole("button", { name: "Submit for review" }));
  fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
  expect(await screen.findByText(/Approval publishes this draft/)).toBeTruthy();
  expect(screen.getByText("The graph is empty until a draft is approved.")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Confirm and publish" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("review", expect.objectContaining({
    operation: "approve", draft_id: "draft-1", expected_revision: 1 }), true));
  expect(await screen.findByRole("button", { name: /Si3N4/ })).toBeTruthy();
});

it("surfaces a failed conversion with the step that failed", async () => {
  const JOB = { id: "job-1", kind: "pipeline", status: "FAILED", progress: null, message: null,
    error: "No markdown engine handles scan.bin", source_id: "source-1",
    created_at: "2026-09-23T00:00:00Z", completed_at: null };
  open(async name => {
    if (name === "jobs") return { jobs: [JOB], job: JOB, events: [{ event: "step.failed",
      step: "convert", message: "No markdown engine handles scan.bin", level: "ERROR",
      occurred_at: "2026-09-23T00:00:00Z" }] };
    return undefined;
  });

  fireEvent.click(await screen.findByRole("button", { name: /job-1.*FAILED/ }));
  expect(await screen.findByText("convert")).toBeTruthy();
  expect(screen.getAllByRole("alert").some(node => node.textContent?.includes("scan.bin"))).toBe(true);
});

it("projects several selected, already-converted sources against one schema in a batch", async () => {
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({
    projection: { id: "projection-x", validation: { valid: true } } }) }));
  vi.stubGlobal("fetch", fetchMock);
  const OTHER = { ...CONVERTED, id: "source-2", filename: "other.md", record_id: "record-2" };
  const action = open(async name => {
    if (name === "sources") return { sources: [CONVERTED, OTHER] };
    if (name === "schemas") return { schemas: [SCHEMA] };
    return undefined;
  });
  fireEvent.click(await screen.findByRole("tab", { name: "Projections" }));
  const region = within(await screen.findByRole("region", { name: "Projections" }));

  fireEvent.change(region.getByLabelText("Custom schema"), { target: { value: "schema-1" } });
  fireEvent.click(await region.findByLabelText("Select sintering.md"));
  fireEvent.click(region.getByLabelText("Select other.md"));
  fireEvent.click(region.getByRole("button", { name: "Project 2 selected" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const recordIds = (fetchMock.mock.calls as unknown as [string, RequestInit][]).map(([, init]) =>
    JSON.parse(String(init.body)).record_id);
  expect(recordIds.sort()).toEqual(["record-1", "record-2"]);
  expect(action.mock.calls.some(([name]) => name === "save_projection")).toBe(false);
  expect(await region.findByText("Projected 2 source(s)")).toBeTruthy();
});

it("lets an engineer pick a connected agent's model from the Knowledge card's Settings", async () => {
  const updateCard = vi.fn();
  useWorldStore.setState({
    edges: [{ id: "edge-1", source: "agent-1", target: "card-1",
      relationship: "knowledge.base.read", direction: "forward" } as WorldEdge],
    cards: [{ id: "agent-1", type: "agent", name: "Librarian", position: { x: 0, y: 0 },
      size: { width: 240, height: 180 }, expanded: false, status: "available",
      config: { model: "oaw:model:model-1" } } as WorldCard],
    updateCard,
  });
  open(async () => undefined);
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  const region = within(await screen.findByRole("region", { name: "Settings" }));

  expect(await region.findByText("Librarian")).toBeTruthy();
  fireEvent.change(region.getByLabelText("Librarian’s model"), { target: { value: "oaw:default" } });
  expect(updateCard).toHaveBeenCalledWith("agent-1", { config: { model: "oaw:default" } });
});

it("hides engineering settings and posts through the deployment bridge when deployed", async () => {
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({
    projection: { id: "projection-1", validation: { valid: true } },
    truncated: false, record_id: "record-1" }) }));
  vi.stubGlobal("fetch", fetchMock);
  const action = open(async name => {
    if (name === "sources") return { sources: [CONVERTED] };
    if (name === "schemas") return { schemas: [SCHEMA] };
    if (name === "markdown") return DOCUMENT;
    return undefined;
  }, {
    card: { config: { default_model: "oaw:model:model-1" } },
    host: { deployment: { config_fields: ["default_model"], document_fields: [], summary_fields: [],
      document_actions: [], downloads: [], resource_actions: {}, execution: false } },
  });

  // The Settings section is engineering-only and never mounts once deployed.
  expect(screen.queryByRole("region", { name: "Settings" })).toBeNull();
  expect(screen.queryByLabelText("Collection name")).toBeNull();

  fireEvent.click(await screen.findByRole("button", { name: /sintering\.md/ }));
  await screen.findByRole("heading", { name: "Sintering of Si3N4" });
  // No live picker either: the deployment shows the model fixed at publish time.
  expect(screen.queryByLabelText("Model")).toBeNull();
  expect(await screen.findByText("Model: oaw:model:model-1")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Extraction schema"), { target: { value: "schema-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Project to JSON" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/runtime-app/workspace/knowledge/card-1/project");
  expect(JSON.parse(String(init.body))).toEqual({
    schema_id: "schema-1", model: "oaw:model:model-1", record_id: "record-1" });
  expect(await within(await screen.findByRole("region", { name: "Sources" }))
    .findByText("Projection projection-1 saved")).toBeTruthy();
});
