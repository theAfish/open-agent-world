// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { PluginViewProps } from "./sdk";
import { useWorldStore } from "../state/worldStore";
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
  domain: "materials", version: 1 };
const DOCUMENT = { record_id: "record-1", filename: "sintering.md", engine: "text",
  total_characters: 42, offset: 0, markdown: "# Sintering of Si3N4", has_more: false };
const DRAFT = { id: "draft-1", status: "DRAFT", revision: 1, created_by: "user:desktop",
  updated_at: "2026-09-23T00:00:00Z" };

const overview = (counts: Record<string, number> = {}) => ({
  collection: { id: "collection-1", name: "Knowledge base" },
  settings: { collection_name: "Knowledge base", pdf_engine: "auto", mineru_base_url: "" },
  engines: ["text"],
  counts: { sources: 1, records: 1, schemas: 1, projections: 0, drafts: 1, pending_review: 1,
    facts: 0, entities: 0, relations: 0, ...counts },
  active_jobs: 0,
});
const EMPTY: Record<string, Record<string, unknown>> = {
  sources: { sources: [] }, schemas: { schemas: [] }, projections: { projections: [] },
  draft: { drafts: [] }, jobs: { jobs: [] }, graph: { entities: [], relations: [], truncated: false },
};

type Action = (name: string, args: Record<string, unknown>, confirm?: boolean) =>
  Promise<Record<string, unknown> | undefined>;

/** Renders the workspace over a stub card, answering whatever the test leaves out. */
const open = (handler: Action) => {
  const action = vi.fn(async (name: string, args: Record<string, unknown>, confirm?: boolean) =>
    await handler(name, args, confirm) ?? (name === "overview" ? overview() : EMPTY[name] ?? {}));
  const Workspace = plugin.views.workspace;
  const props = { card: { id: "card-1", name: "Knowledge" }, host: { resourceAction: action } };
  render(<Workspace {...props as unknown as PluginViewProps} />);
  return action;
};

beforeEach(() => {
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
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("uploads a document and reads its markdown once the conversion job finishes", async () => {
  let converted = false;
  const action = open(async name => {
    if (name === "sources") return { sources: [converted ? CONVERTED : SOURCE] };
    if (name === "ingest") {
      converted = true;
      return { source: { id: "source-1" }, job: { id: "job-1", status: "QUEUED" } };
    }
    if (name === "jobs") return { jobs: [{ id: "job-1", kind: "pipeline",
      status: converted ? "COMPLETED" : "QUEUED", progress: 1, message: "markdown ready",
      error: null, source_id: "source-1", created_at: "2026-09-23T00:00:00Z", completed_at: null }] };
    if (name === "markdown") return DOCUMENT;
    return undefined;
  });

  expect(await screen.findByRole("button", { name: /sintering\.md.*awaiting conversion/ })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Add a document"), {
    target: { files: [new File(["# Sintering"], "sintering.md", { type: "text/markdown" })] } });
  await waitFor(() => expect(action).toHaveBeenCalledWith("ingest", expect.objectContaining({
    filename: "sintering.md", media_type: "text/markdown" }), undefined));

  fireEvent.click(await screen.findByRole("button", { name: /sintering\.md.*markdown via text/ }));
  expect(await screen.findByText("# Sintering of Si3N4")).toBeTruthy();
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
  await screen.findByText("# Sintering of Si3N4");
  fireEvent.change(screen.getByLabelText("Extraction schema"), { target: { value: "schema-1" } });
  expect((screen.getByLabelText("Model") as HTMLSelectElement).value).toBe("oaw:model:model-1");
  fireEvent.click(screen.getByRole("button", { name: "Project to JSON" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/knowledge/card-1/project");
  expect(JSON.parse(String(init.body))).toEqual({
    schema_id: "schema-1", model: "oaw:model:model-1", record_id: "record-1" });
  expect(action.mock.calls.some(([name]) => name === "save_projection")).toBe(false);
  expect(await screen.findByText("Projection projection-1 saved")).toBeTruthy();
});

it("keeps the graph empty until the person confirms the approval", async () => {
  let inReview = false;
  let published = false;
  const action = open(async (name, args, confirm) => {
    if (name === "overview") return overview({ entities: published ? 2 : 0 });
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
