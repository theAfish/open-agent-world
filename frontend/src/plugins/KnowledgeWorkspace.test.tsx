// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { PluginViewProps } from "./sdk";
import { useWorldStore } from "../state/worldStore";
import { TEST_CATALOG } from '../state/catalog.fixture';
import type { WorldCard, WorldEdge } from "../types/world";
import { WorkspaceSectionProvider, type WorkspaceSectionProps, type WorkspaceSectionRegistration } from "../workspace/WorkspaceSection";
import { WorkspaceAccess } from "../workspace/WorkspaceAccess";
import plugin from "../../../plugins/knowledge_base/frontend";

vi.mock("@oaw/plugin-api", async () => {
  const sections = await import("../workspace/WorkspaceSection");
  return ({
  ...await vi.importActual<typeof import('./sdk')>('./sdk'),
  WorkspaceEmptyState: (await import("../workspace/WorkspacePrimitives")).WorkspaceEmptyState,
  WorkspaceToolbar: (await import("../workspace/WorkspacePrimitives")).WorkspaceToolbar,
  t: (value: string, params?: Record<string, unknown>) => params
    ? value.replace(/\{(v\d+)\}/g, (_, key: string) => String(params[key]))
    : value,
  useLocale: () => undefined,
  NetworkMap: () => <div aria-label="Knowledge graph map" />,
  useWorkspaceSections: sections.useWorkspaceSections,
  WorkspaceSection: ({ title, children, ...props }: WorkspaceSectionProps) =>
    <sections.WorkspaceSection {...props} title={title}><section aria-label={title}>{children}</section></sections.WorkspaceSection>,
  });
});

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
const open = (handler: Action, overrides: { card?: Record<string, unknown>; host?: Record<string, unknown>;
  wrapper?: (children: ReactNode) => ReactNode } = {}) => {
  const action = vi.fn(async (name: string, args: Record<string, unknown>, confirm?: boolean) =>
    await handler(name, args, confirm) ?? (name === "overview" ? overview() : EMPTY[name] ?? {}));
  const Workspace = plugin.views.workspace;
  const props = { card: { id: "card-1", name: "Knowledge", config: { default_model: "" }, ...overrides.card },
    host: { resourceAction: action, ...overrides.host } };
  const workspace = <Workspace {...props as unknown as PluginViewProps} />;
  render(overrides.wrapper ? overrides.wrapper(workspace) : workspace);
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
  useWorldStore.setState({ catalog: TEST_CATALOG, modelCatalog: { revision: 1, default_model: null, connections: [{
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

it("keeps a detached reader and its draft mounted while another section opens", async () => {
  const destination = document.createElement("div");
  document.body.append(destination);
  const register = (section: WorkspaceSectionRegistration) => {
    if (section.id === "sources") destination.append(section.host);
    return () => section.host.remove();
  };
  try {
    open(async name => name === "sources" ? { sources: [CONVERTED] }
      : name === "markdown" ? DOCUMENT : undefined, {
      wrapper: children => <WorkspaceSectionProvider cardId="card-1" editing={false}
        detachedSectionIds={new Set(["sources"])} hiddenSectionIds={new Set()} register={register}
        onSelect={() => {}} onDragStart={() => {}} onHide={() => {}}>{children}</WorkspaceSectionProvider>,
    });
    await screen.findByText("Continue alongside this section");
    fireEvent.click(await within(destination).findByRole("button", { name: /sintering\.md/ }));
    await within(destination).findByRole("heading", { name: "Sintering of Si3N4" });
    const reader = destination.querySelector(".knowledge-reader");
    const search = within(destination).getByLabelText("Search converted documents") as HTMLInputElement;
    fireEvent.change(search, { target: { value: "unsent query" } });
    fireEvent.click(screen.getByRole("button", { name: "Schemas" }));
    expect(await screen.findByRole("button", { name: "New schema" })).toBeTruthy();
    expect(destination.querySelector(".knowledge-reader")).toBe(reader);
    expect(within(destination).getByLabelText("Search converted documents")).toBe(search);
    expect(search.value).toBe("unsent query");
    expect(within(destination).getByRole("heading", { name: "Sintering of Si3N4" })).toBeTruthy();
  } finally {
    cleanup();
    destination.remove();
  }
});

it("searches published entities through the collection controls and restores the full graph", async () => {
  const entity = { id: "entity-1", type: "material", name: "Si3N4", properties: { topic: "ceramics" } };
  const action = open(async (name, args) => name === "graph"
    ? { entities: args.name_contains ? [] : [entity], relations: [], truncated: false } : undefined);
  fireEvent.click(screen.getByRole("tab", { name: "Graph" }));
  await screen.findByRole("button", { name: /Si3N4/ });
  fireEvent.click(screen.getByText("Search collection", { selector: "summary" }));
  const input = screen.getByLabelText("Filter by name");
  fireEvent.change(input, { target: { value: "missing material" } });
  fireEvent.submit(input.closest("form")!);
  await waitFor(() => expect(action).toHaveBeenCalledWith("graph", expect.objectContaining({ name_contains: "missing material" }), undefined));
  await screen.findByText("No matches");
  fireEvent.click(screen.getByRole("button", { name: "Show everything" }));
  expect(await screen.findByRole("button", { name: /Si3N4/ })).toBeTruthy();
  expect((input as HTMLInputElement).value).toBe("");
});

it("does not mount a detached section excluded by the deployment grants", async () => {
  const register = vi.fn((_section: WorkspaceSectionRegistration) => () => {});
  open(async name => name === "sources" ? { sources: [CONVERTED] } : undefined, {
    host: { deployment: {} },
    wrapper: children => <WorkspaceAccess.Provider value={{ deployed: true, permissions: { "card-1": ["sources"] },
      plugin_access: { "card-1": { config_fields: [], document_fields: [], summary_fields: [], document_actions: [], downloads: [], resource_actions: {}, execution: false } } }}>
      <WorkspaceSectionProvider cardId="card-1" editing={false} detachedSectionIds={new Set(["schemas", "settings"])}
        hiddenSectionIds={new Set()} register={register} onSelect={() => {}} onDragStart={() => {}} onHide={() => {}}>{children}</WorkspaceSectionProvider>
    </WorkspaceAccess.Provider>,
  });
  await screen.findByRole("button", { name: /sintering\.md/ });
  expect(register.mock.calls.map(([section]) => section.id)).toEqual(["sources"]);
  expect(screen.queryByRole("button", { name: "New schema" })).toBeNull();
  expect(screen.queryByLabelText("MinerU token")).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
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
  fireEvent.click(screen.getByText("Project to JSON", { selector: "summary" }));
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

const BUNDLE = { id: "bundle-1", group_id: "collection-1", schema_id: "schema-2", name: "Batch 7", notes: "",
  status: "ready", run_id: null, created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z",
  files: [{ source_id: "source-9", filename: "batch7.csv", media_type: "text/csv", size: 128,
    state: "converted", job_id: "job-9", error: null }], records: 0 };
const RECORD = { id: "record-x", group_id: "collection-1", schema_id: "schema-2", bundle_id: "bundle-1", name: "A1",
  data: { sample_id: "A1", conductivity: 1.2 }, validation: { valid: true }, note: null, status: "draft",
  revision: 1, created_by: "agent:lab", created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z" };
const EXPERIMENT_DEFINITION = { type: "object", required: ["sample_id"],
  properties: { sample_id: { type: "string" }, conductivity: { type: "number", description: "S/cm" } } };

it("extracts records from an upload through a connected Lab assistant run, then publishes one on confirmation", async () => {
  // The Lab assistant is connected with the experiment relationship; the Librarian
  // (read only) is not offered for extraction.
  useWorldStore.setState({
    cards: [{ id: "card-1", type: "knowledge.base", name: "Knowledge" }, { id: "lab", type: "agent", name: "Lab assistant" },
      { id: "librarian", type: "agent", name: "Librarian" }] as unknown as WorldCard[],
    edges: [{ id: "e1", source: "lab", target: "card-1", relationship: "knowledge.base.experiment" },
      { id: "e2", source: "librarian", target: "card-1", relationship: "knowledge.base.read" }] as unknown as WorldEdge[],
    catalog: TEST_CATALOG,
  });
  let extracted = false;
  let published = false;
  const runAgent = vi.fn(async () => { extracted = true; });
  const getAgentInfo = vi.fn(async () => ({ session_id: "s", status: extracted ? "idle" : "running", active_run_id: null }));
  const action = open(async (name, args, confirm) => {
    if (name === "schemas") return args.operation === "get"
      ? { schema: { ...EXPERIMENT_SCHEMA, definition: EXPERIMENT_DEFINITION, system_prompt: "x", field_descriptions: null } }
      : { schemas: [EXPERIMENT_SCHEMA] };
    if (name === "experiment_bundles" && args.operation === "get") return { bundle: { ...BUNDLE, status: extracted ? "extracted" : "ready", records: extracted ? 1 : 0 } };
    if (name === "experiment_bundles") return { bundles: [{ ...BUNDLE, status: extracted ? "extracted" : "ready", records: extracted ? 1 : 0 }] };
    if (name === "experiments" && args.operation === "get") return { experiment: { ...RECORD, status: published ? "published" : "draft", sources: [{ source_id: "source-9", filename: "batch7.csv" }] } };
    if (name === "experiments") return { experiments: extracted ? [{ ...RECORD, status: published ? "published" : "draft" }] : [] };
    if (name === "experiment_bundle_manage") return { bundle: BUNDLE };
    if (name === "experiment_review") {
      if (!confirm) return { status: "confirmation_required", reasons: ["Publishing marks this record as approved data."] };
      published = true;
      return { experiment: { ...RECORD, status: "published", revision: 2, sources: [] } };
    }
    return undefined;
  }, { host: { runAgent, getAgentInfo } });

  fireEvent.click(await screen.findByRole("button", { name: "Experiment" }));
  const region = within(await screen.findByRole("region", { name: "Experiments" }));
  fireEvent.click(await region.findByRole("tab", { name: /Batch 7/ }));
  fireEvent.click(await region.findByRole("button", { name: "Extract records" }));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Extract records from this upload" }))
    .getByRole("button", { name: "Extract records" }));

  await waitFor(() => expect(runAgent).toHaveBeenCalledTimes(1));
  const [agentId, prompt] = runAgent.mock.calls[0] as unknown as [string, string];
  expect(agentId).toBe("lab");
  expect(prompt).toContain("EXPERIMENT EXTRACTION REQUEST");
  expect(JSON.parse(prompt.split("\n")[2])).toMatchObject({ bundle_id: "bundle-1", schema_id: "schema-2", replace_drafts: false });
  expect(action).toHaveBeenCalledWith("experiment_bundle_manage",
    expect.objectContaining({ operation: "update", bundle_id: "bundle-1", status: "extracting" }), undefined);
  // The card never saves records itself: that is the agent's tool call.
  expect(action.mock.calls.some(([name]) => name === "experiment_save")).toBe(false);

  // The agent goes idle; open its record, then enter the edit form explicitly.
  const record = await region.findByRole("button", { name: /A1/ }, { timeout: 8000 });
  fireEvent.click(record);
  fireEvent.click(await region.findByRole("button", { name: "Edit" }));
  const field = await region.findByLabelText(/conductivity/) as HTMLInputElement;
  expect(field.value).toBe("1.2");
  fireEvent.click(region.getByRole("button", { name: "Cancel" }));

  fireEvent.click(region.getByRole("button", { name: "Publish" }));
  expect(await region.findByText("Publish this record?")).toBeTruthy();
  expect(action.mock.calls.filter(([name, , confirm]) => name === "experiment_review" && confirm)).toHaveLength(0);
  fireEvent.click(region.getByRole("button", { name: "Confirm and publish" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("experiment_review",
    expect.objectContaining({ operation: "publish", record_id: "record-x" }), true));
  expect(await region.findByText(/Record "A1" published/)).toBeTruthy();
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

it("drafts insights over published records and publishes one into the experiment layer on confirmation", async () => {
  const record = { id: "record-1", group_id: "collection-1", schema_id: "schema-2", bundle_id: "bundle-1", name: "B7",
    data: { sample_id: "B7", moisture: 9 }, validation: { valid: true }, note: null, status: "published",
    revision: 2, created_by: "user:desktop", created_at: "2026-10-21T00:00:00Z", updated_at: "2026-10-21T00:00:00Z" };
  const insight = { id: "insight-1", group_id: null, schema_id: "schema-2", schema_ids: ["schema-2"], kind: "anomaly", title: "B7 is wet",
    statement: "B7 moisture is 3.2σ above the batch mean.", fields: ["moisture"], confidence: 0.9, properties: { z: 3.2 },
    status: "draft", revision: 1, note: null, run_id: null, created_by: "user:desktop",
    created_at: "2026-10-21T00:00:00Z", updated_at: "2026-10-21T00:00:00Z",
    evidence: [{ record_id: "record-1", role: "observed_in", name: "B7", stale: false }] };
  let published = false;
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ insights: [insight], warnings: [], truncated: false }) }));
  vi.stubGlobal("fetch", fetchMock);
  const action = open(async (name, args, confirm) => {
    if (name === "schemas" && args.operation === "list") return { schemas: [SCHEMA, EXPERIMENT_SCHEMA] };
    if (name === "schemas" && args.operation === "get") return { schema: { ...EXPERIMENT_SCHEMA, definition: { type: "object", properties: {} }, system_prompt: "x", field_descriptions: null } };
    if (name === "experiments") return { experiments: [record] };
    if (name === "insights" && args.operation === "stats") return { schema_ids: ["schema-2"], cross_schema: null,
      statistics_by_schema: [{ schema_id: "schema-2", schema_name: "Conductivity run", record_count: 1, statistics: { records: 1, truncated: false, correlations: [],
        fields: [{ field: "moisture", kind: "numeric", count: 1, mean: 9, std: 0, min: 9, max: 9, median: 9, outliers: [] }] } }] };
    if (name === "insights") return { insights: [{ ...insight, status: published ? "published" : "draft" }] };
    if (name === "insight_review" && args.operation === "publish") {
      if (!confirm) return { status: "confirmation_required", reasons: ["Publishing writes this insight into the knowledge graph."] };
      published = true;
      return { insights: [{ ...insight, status: "published" }], graph: { entities: 3, relations: 2 } };
    }
    if (name === "graph" && args.layer === "experiment" && published) return { truncated: false, relations: [],
      entities: [{ id: "node-1", type: "anomaly", name: "B7 is wet", properties: { insight_id: "insight-1" } }] };
    return undefined;
  }, { host: { deployment: undefined } });

  fireEvent.click(await screen.findByRole("button", { name: "Experiment" }));
  fireEvent.click(await screen.findByRole("tab", { name: "Insights" }));
  // The Literature map asks for approved facts only; this tab for the experiment layer.
  await waitFor(() => expect(action).toHaveBeenCalledWith("graph", expect.objectContaining({ layer: "literature" }), undefined));
  await waitFor(() => expect(action).toHaveBeenCalledWith("graph", expect.objectContaining({ layer: "experiment" }), undefined));
  const insightsSection = within(await screen.findByRole("region", { name: "Insights" }));
  expect(await insightsSection.findByText("B7 moisture is 3.2σ above the batch mean.")).toBeTruthy();
  expect(insightsSection.getByText("Nothing published yet")).toBeTruthy();

  // The open scope reads every schema; statistics are asked for it as well.
  await waitFor(() => expect(action).toHaveBeenCalledWith("insights", expect.objectContaining({ operation: "stats" }), undefined));
  expect(await insightsSection.findByText(/1 published record\(s\) across every experiment schema/)).toBeTruthy();
  expect(insightsSection.getByText("Mean")).toBeTruthy();
  // Narrowing to one schema narrows the statistics and the extraction scope; the
  // extraction goes through the host bridge when no agent is connected.
  fireEvent.change(insightsSection.getByLabelText("Experiment schema"), { target: { value: "schema-2" } });
  await waitFor(() => expect(action).toHaveBeenCalledWith("insights", expect.objectContaining({ operation: "stats", schema_id: "schema-2" }), undefined));
  expect(await insightsSection.findByText(/1 published record\(s\) of "Conductivity run"/)).toBeTruthy();
  fireEvent.click(insightsSection.getByRole("button", { name: "Extract insights" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/knowledge/card-1/insights", expect.objectContaining({ method: "POST" })));
  expect(JSON.parse(String((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body))).toEqual(
    expect.objectContaining({ schema_ids: ["schema-2"], replace_drafts: false, all_groups: true }));

  fireEvent.click(insightsSection.getByRole("button", { name: "Publish" }));
  expect(await insightsSection.findByText(/Publishing writes this insight/)).toBeTruthy();
  fireEvent.click(insightsSection.getByRole("button", { name: "Confirm and publish" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("insight_review",
    expect.objectContaining({ operation: "publish", insight_ids: ["insight-1"] }), true));
  expect(await insightsSection.findByRole("button", { name: /B7 is wet/ })).toBeTruthy();
  expect(insightsSection.queryByRole("button", { name: "Publish" })).toBeNull();
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

it("extracts records through the host bridge when no Lab assistant is connected", async () => {
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({
    records: [RECORD, { ...RECORD, id: "record-y", name: "A2" }], invalid: 0, bundle_id: "bundle-1", truncated: false }) }));
  vi.stubGlobal("fetch", fetchMock);
  const runAgent = vi.fn();
  const action = open(async (name, args) => {
    if (name === "schemas") return args.operation === "get"
      ? { schema: { ...EXPERIMENT_SCHEMA, definition: EXPERIMENT_DEFINITION, system_prompt: "x", field_descriptions: null } }
      : { schemas: [EXPERIMENT_SCHEMA] };
    if (name === "experiment_bundles") return { bundles: [BUNDLE] };
    if (name === "experiments") return { experiments: [] };
    return undefined;
  }, { host: { runAgent } });

  fireEvent.click(await screen.findByRole("button", { name: "Experiment" }));
  const region = within(await screen.findByRole("region", { name: "Experiments" }));
  fireEvent.click(await region.findByRole("tab", { name: /Batch 7/ }));
  fireEvent.click(await region.findByRole("button", { name: "Extract records" }));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Extract records from this upload" }))
    .getByRole("button", { name: "Extract records" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/knowledge/card-1/extract");
  expect(JSON.parse(String(init.body))).toEqual({ bundle_id: "bundle-1", model: "oaw:model:model-1", replace_drafts: false });
  // No agent run, and the card never saves records itself: the bridge hands them to the plugin.
  expect(runAgent).not.toHaveBeenCalled();
  expect(action.mock.calls.some(([name]) => name === "experiment_save")).toBe(false);
  expect(await region.findByText(/Extracted 2 record\(s\) from "Batch 7"/)).toBeTruthy();
});

it("summarises a failed extraction under the upload and opens its full run log in a window", async () => {
  const run = { run_id: "run-77", agent_id: "lab", status: "failed", created_at: "2026-10-21T06:12:27Z",
    finished_at: "2026-10-21T06:13:11Z", error: "Expecting property name enclosed in double quotes",
    lifecycle: { model_requests: [{ model_request: 1, finish_reason: "STOP", elapsed_ms: 4441 }, { model_request: 2, error_type: "JSONDecodeError" }] } };
  const activity = { items: [
    { id: "a1", type: "agent_progress", timestamp: "2026-10-21T06:12:27Z", text: "agent · model request 1 started" },
    { id: "a2", type: "tool_completed", timestamp: "2026-10-21T06:12:31Z", name: "knowledge_experiment_extract_prompt", success: true },
    { id: "a3", type: "agent_progress", timestamp: "2026-10-21T06:13:11Z", text: "agent · model request 2, attempt 1 interrupted (JSONDecodeError)" }] };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () =>
    url.endsWith("/runs/run-77") ? run : url.includes("/runs/run-77/activity") ? activity : { configured: false, source: null } })));
  open(async (name, args) => {
    if (name === "schemas") return args.operation === "get"
      ? { schema: { ...EXPERIMENT_SCHEMA, definition: EXPERIMENT_DEFINITION, system_prompt: "x", field_descriptions: null } }
      : { schemas: [EXPERIMENT_SCHEMA] };
    if (name === "experiment_bundles") return { bundles: [{ ...BUNDLE, status: "failed", run_id: "run-77" }] };
    if (name === "experiments") return { experiments: [] };
    return undefined;
  });

  fireEvent.click(await screen.findByRole("button", { name: "Experiment" }));
  const region = within(await screen.findByRole("region", { name: "Experiments" }));
  fireEvent.click(await region.findByRole("tab", { name: /Batch 7/ }));

  // The summary is inline; the timeline waits behind "View log".
  const summary = within(await region.findByRole("alert"));
  expect(summary.getByText("Expecting property name enclosed in double quotes")).toBeTruthy();
  expect(summary.getByText(/interrupted mid-stream \(#2 JSONDecodeError\)/)).toBeTruthy();
  expect(region.queryByText("knowledge_experiment_extract_prompt")).toBeNull();

  fireEvent.click(summary.getByRole("button", { name: "View log" }));
  const dialog = within(await screen.findByRole("dialog", { name: /Extraction log · Batch 7/ }));
  expect(await dialog.findByText("knowledge_experiment_extract_prompt")).toBeTruthy();
  expect(dialog.getByText(/model request 2, attempt 1 interrupted/)).toBeTruthy();
  expect(dialog.getByText(/STOP · 4\.4s/)).toBeTruthy();
  // A finished run offers no Stop.
  expect(dialog.queryByRole("button", { name: /Stop/ })).toBeNull();
  fireEvent.click(dialog.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: /Extraction log/ })).toBeNull());
});

it("lets the person stop a running extraction and watch its log live", async () => {
  useWorldStore.setState({
    cards: [{ id: "card-1", type: "knowledge.base", name: "Knowledge" }, { id: "lab", type: "agent", name: "Lab assistant" }] as unknown as WorldCard[],
    edges: [{ id: "e1", source: "lab", target: "card-1", relationship: "knowledge.base.experiment" }] as unknown as WorldEdge[],
    catalog: TEST_CATALOG,
  });
  const stopped: string[] = [];
  const run = { run_id: "run-88", agent_id: "lab", status: "running", created_at: "2099-01-01T00:00:00Z", finished_at: null, error: null, lifecycle: { model_requests: [] } };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST" && url.endsWith("/agents/lab/stop")) { stopped.push(url); return { ok: true, json: async () => ({}) }; }
    return { ok: true, json: async () => url.includes("runs?agent_id=lab") ? [run] : url.endsWith("/runs/run-88") ? run
      : url.includes("/runs/run-88/activity") ? { items: [{ id: "p1", type: "agent_progress", timestamp: "2099-01-01T00:00:01Z", text: "agent · model request 1 started" }] }
      : { configured: false, source: null } };
  }));
  const runAgent = vi.fn(async () => undefined);
  // The agent stays "running" for the whole test, so the completion poller never fires.
  const getAgentInfo = vi.fn(async () => ({ session_id: "s", status: "running", active_run_id: "run-88" }));
  let bundleStatus = "ready", bundleRun: string | null = null;
  open(async (name, args) => {
    if (name === "schemas") return args.operation === "get"
      ? { schema: { ...EXPERIMENT_SCHEMA, definition: EXPERIMENT_DEFINITION, system_prompt: "x", field_descriptions: null } }
      : { schemas: [EXPERIMENT_SCHEMA] };
    if (name === "experiment_bundles") return { bundles: [{ ...BUNDLE, status: bundleStatus, run_id: bundleRun }] };
    if (name === "experiments") return { experiments: [] };
    if (name === "experiment_bundle_manage") { bundleStatus = String(args.status ?? bundleStatus); if (args.run_id) bundleRun = String(args.run_id); return { bundle: BUNDLE }; }
    return undefined;
  }, { host: { runAgent, getAgentInfo } });

  fireEvent.click(await screen.findByRole("button", { name: "Experiment" }));
  const region = within(await screen.findByRole("region", { name: "Experiments" }));
  fireEvent.click(await region.findByRole("tab", { name: /Batch 7/ }));
  fireEvent.click(await region.findByRole("button", { name: "Extract records" }));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Extract records from this upload" }))
    .getByRole("button", { name: "Extract records" }));
  await waitFor(() => expect(runAgent).toHaveBeenCalled());

  // Stop and View log appear while the run is underway; the window is live.
  fireEvent.click(await region.findByRole("button", { name: "View log" }));
  const dialog = within(await screen.findByRole("dialog", { name: /Extraction log/ }));
  expect(await dialog.findByText(/model request 1 started/)).toBeTruthy();
  expect(dialog.getByText(/live/)).toBeTruthy();
  fireEvent.click(dialog.getByRole("button", { name: "Stop extraction" }));
  await waitFor(() => expect(stopped).toEqual([expect.stringMatching(/\/agents\/lab\/stop$/)]));
});

it("stores the vision model as a card setting, blank for the OAW default model", async () => {
  const saved: Record<string, unknown>[] = [];
  const action = open(async (name, args) => {
    if (name === "settings") { saved.push(args); return { settings: { collection_name: "Knowledge base",
      pdf_engine: "auto", mineru_base_url: "", vision_model: (args.vision_model as string) ?? "" } }; }
    return undefined;
  });
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  const region = within(await screen.findByRole("region", { name: "Settings" }));
  const picker = await region.findByLabelText("Vision model for images") as HTMLSelectElement;
  expect(picker.value).toBe("oaw:default");

  fireEvent.change(picker, { target: { value: "oaw:model:model-1" } });
  await waitFor(() => expect(saved).toContainEqual({ vision_model: "oaw:model:model-1" }));
  fireEvent.change(region.getByLabelText("Vision model for images"), { target: { value: "oaw:default" } });
  await waitFor(() => expect(saved).toContainEqual({ vision_model: "" }));
  // The key never passes through the card: only a model reference is stored.
  expect(action.mock.calls.every(([, args]) => !JSON.stringify(args).includes("api_key"))).toBe(true);
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
  fireEvent.click(screen.getByText("Project to JSON", { selector: "summary" }));
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
