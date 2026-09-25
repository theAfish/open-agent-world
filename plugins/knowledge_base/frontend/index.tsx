import { t, useLocale, WorkspaceSection, useWorkspaceSections } from "@oaw/plugin-api";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";
import { useCallback, useEffect, useRef, useState } from "react";
import { useWorldStore } from "../../../frontend/src/state/worldStore";
import { availableModels } from "../../../frontend/src/state/modelConnections";
import { GraphMap } from "./GraphMap";
import "./style.css";

type Settings = { collection_name: string; pdf_engine: string; mineru_base_url: string };
type Overview = { collection: { id: string; name: string }; settings: Settings; engines: string[];
  counts: Record<string, number>; active_jobs: number };
type Source = { id: string; filename: string; media_type: string; size: number; created_at: string;
  markdown: { artifact_id: string; size: number; engine: string | null } | null;
  record_id: string | null; projections: number };
type Extracted = { record_id: string; filename: string | null; engine: string | null;
  total_characters: number; offset: number; markdown: string; has_more: boolean };
type SchemaItem = { id: string; name: string; description: string | null; domain: string; version: number };
type SchemaDetail = SchemaItem & { definition: Record<string, unknown>; system_prompt: string;
  field_descriptions: Record<string, unknown> | null };
type Projection = { id: string; schema_id: string; record_id: string; status: string;
  valid: boolean | null; extracted_at: string; summary: string };
type ProjectionDetail = { id: string; schema_id: string; record_id: string; status: string;
  validation: { valid?: boolean; errors?: string[]; missing?: string[] } | null; notes: string | null;
  data: Record<string, unknown>; evidence: { id: string; source_id: string; artifact_id: string }[] };
type DraftItem = { id: string; status: string; revision: number; created_by: string; updated_at: string };
type DraftDetail = { id: string; status: string; revision: number; created_by: string;
  graph: { entities?: unknown[]; relations?: unknown[] }; evidence_ids: string[] };
type Entity = { id: string; type: string; name: string; properties: Record<string, unknown> };
type Relation = { id: string; type: string; source_id: string; target_id: string };
type Graph = { entities: Entity[]; relations: Relation[]; truncated: boolean };
type Job = { id: string; kind: string; status: string; progress: number | null; message: string | null;
  error: string | null; source_id: string | null; created_at: string; completed_at: string | null };
type JobEvent = { event: string; step: string | null; message: string | null; level: string | null;
  occurred_at: string };
/** An approval the card asked the person to confirm before it publishes a fact. */
type Pending = { args: Record<string, unknown>; reasons: string[] };

const ACTIVE = new Set(["QUEUED", "RUNNING", "PENDING", "RETRYING"]);
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const shortId = (value: string) => value.length > 13 ? `${value.slice(0, 8)}…` : value;
const bytes = (value: number) => value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KiB` : `${(value / 1024 / 1024).toFixed(1)} MiB`;
const pretty = (value: unknown) => JSON.stringify(value, null, 2);

const fileBase64 = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader();
  reader.onerror = () => reject(reader.error ?? new Error("File read failed"));
  reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
  reader.readAsDataURL(file);
});

/** The projection call needs a model, so it goes through the host bridge, not the plugin. */
async function request(path: string, body: unknown) {
  const response = await fetch(`/api/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : t("The projection request failed"));
  return data as Record<string, unknown>;
}

function Preview({ host, card }: PluginViewProps) {
  useLocale();
  const [summary, setSummary] = useState<Overview>();
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    host.resourceAction("overview", {}).then(value => { if (active) setSummary(value as Overview); })
      .catch(reason => { if (active) setError(message(reason)); });
    return () => { active = false; };
  }, [host, card.id]);
  return <div className="knowledge-preview">
    <span className="knowledge-badge">{t("Knowledge base")}</span>
    {error ? <p role="alert" title={error}>{t("Knowledge base unavailable")}</p>
      : !summary ? <p role="status">{t("Opening knowledge base…")}</p>
      : <>
        <strong>{summary.counts.facts} {summary.counts.facts === 1 ? t("published fact") : t("published facts")}</strong>
        <p>{t("{v0} documents · {v1} entities · {v2} awaiting review", {
          v0: summary.counts.sources, v1: summary.counts.entities, v2: summary.counts.pending_review })}</p>
      </>}
  </div>;
}

export function Workspace({ host, card }: PluginViewProps) {
  useLocale();
  const sections = useWorkspaceSections();
  const [overview, setOverview] = useState<Overview>();
  const [sources, setSources] = useState<Source[]>([]);
  const [schemas, setSchemas] = useState<SchemaItem[]>([]);
  const [projections, setProjections] = useState<Projection[]>([]);
  const [drafts, setDrafts] = useState<DraftItem[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [graph, setGraph] = useState<Graph>();

  const [selectedSource, setSelectedSource] = useState("");
  const [extracted, setExtracted] = useState<Extracted>();
  const [documentError, setDocumentError] = useState("");
  const [schemaId, setSchemaId] = useState("");
  const [editor, setEditor] = useState<{ id: string; name: string; description: string; system_prompt: string; definition: string }>();
  const [chosen, setChosen] = useState<string[]>([]);
  const [projection, setProjection] = useState<ProjectionDetail>();
  const [draftDetail, setDraftDetail] = useState<DraftDetail>();
  const [reviewNotes, setReviewNotes] = useState("");
  const [pending, setPending] = useState<Pending>();
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState("");
  const [entity, setEntity] = useState("");
  const [jobDetail, setJobDetail] = useState<{ job: Job; events: JobEvent[] }>();
  const [settings, setSettings] = useState<Settings>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const generation = useRef(0);
  const documentSequence = useRef(0);
  const catalog = useWorldStore(state => state.modelCatalog);
  const legacyModels = useWorldStore(state => state.modelSettings.models);
  const models = catalog.revision > 0
    ? availableModels({ ...catalog, connections: catalog.connections.filter(c => c.adapter === "openai" || c.adapter === "legacy") })
    : legacyModels.map(value => ({ value, label: value }));
  const [model, setModel] = useState("");
  const selectedModel = models.some(item => item.value === model) ? model : (models[0]?.value ?? "");

  const call = useCallback((action: string, args: Record<string, unknown> = {}, confirm?: boolean) =>
    host.resourceAction(action, args, confirm), [host]);

  const refresh = useCallback(async () => {
    const current = generation.current;
    const [summary, sourceList, schemaList, projectionList, draftList, jobList] = await Promise.all([
      call("overview"), call("sources", { limit: 100 }), call("schemas", { operation: "list" }),
      call("projections", { limit: 100 }), call("draft", { operation: "list" }), call("jobs", { limit: 20 }),
    ]);
    if (generation.current !== current) return;
    setOverview(summary as Overview);
    setSettings((summary as Overview).settings);
    setSources((sourceList.sources as Source[]) ?? []);
    setSchemas((schemaList.schemas as SchemaItem[]) ?? []);
    setProjections((projectionList.projections as Projection[]) ?? []);
    setDrafts((draftList.drafts as DraftItem[]) ?? []);
    setJobs((jobList.jobs as Job[]) ?? []);
  }, [call]);

  const loadGraph = useCallback(async (args: Record<string, unknown> = {}) => {
    const current = generation.current;
    const result = await call("graph", args) as Graph;
    if (generation.current === current) setGraph(result);
  }, [call]);

  const perform = useCallback(async (work: () => Promise<void>) => {
    const current = generation.current;
    setBusy(true); setError("");
    try { await work(); }
    catch (reason) { if (generation.current === current) setError(message(reason)); }
    finally { if (generation.current === current) setBusy(false); }
  }, []);

  /** Walk outward from one entity; the map keeps whatever is already placed. */
  const expand = useCallback((id: string) => {
    setEntity(id);
    void perform(() => loadGraph({ operation: "traverse", entity_id: id, max_depth: 2 }));
  }, [loadGraph, perform]);

  useEffect(() => {
    void perform(async () => { await refresh(); await loadGraph(); });
    return () => { generation.current += 1; documentSequence.current += 1; };
  }, [card.id, perform, refresh, loadGraph]);

  // A conversion job runs on the card's own thread, so poll until nothing is active.
  useEffect(() => {
    if (!jobs.some(job => ACTIVE.has(job.status))) return;
    const timer = setTimeout(() => { void refresh().catch(reason => setError(message(reason))); }, 1500);
    return () => clearTimeout(timer);
  }, [jobs, refresh]);

  useEffect(() => {
    const sequence = ++documentSequence.current;
    setExtracted(undefined); setDocumentError("");
    if (!selectedSource) return;
    call("markdown", { source_id: selectedSource, limit: 40000 }).then(value => {
      if (sequence === documentSequence.current) setExtracted(value as Extracted);
    }).catch(reason => { if (sequence === documentSequence.current) setDocumentError(message(reason)); });
  }, [call, selectedSource]);

  const upload = (file: File) => perform(async () => {
    const content = await fileBase64(file);
    await call("ingest", { filename: file.name, content_base64: content,
      media_type: file.type || "application/octet-stream" });
    setNotice(t("Converting {v0} to markdown…", { v0: file.name }));
    await refresh();
  });

  const extend = () => extracted && perform(async () => {
    const next = await call("markdown", { source_id: selectedSource,
      offset: extracted.offset + extracted.markdown.length, limit: 40000 }) as Extracted;
    setExtracted({ ...next, offset: extracted.offset, markdown: extracted.markdown + next.markdown });
  });

  const project = () => extracted && perform(async () => {
    if (!schemaId) throw new Error(t("Choose an extraction schema first"));
    if (!selectedModel) throw new Error(t("Configure a model connection first"));
    const result = await request(`knowledge/${card.id}/project`, {
      schema_id: schemaId, model: selectedModel, record_id: extracted.record_id });
    const saved = result.projection as { id: string; validation?: { valid?: boolean } };
    setNotice(result.truncated
      ? t("Projected a truncated document; only the first part was sent to the model")
      : saved.validation?.valid === false
        ? t("Projection saved but it does not satisfy the schema; open it to see why")
        : t("Projection {v0} saved", { v0: shortId(saved.id) }));
    await refresh();
  });

  const saveSchema = () => editor && perform(async () => {
    let definition: unknown;
    try { definition = JSON.parse(editor.definition); }
    catch { throw new Error(t("The schema definition must be valid JSON")); }
    const payload = { name: editor.name, description: editor.description || null,
      system_prompt: editor.system_prompt, definition };
    const result = editor.id
      ? await call("schemas", { operation: "update", schema_id: editor.id, ...payload })
      : await call("schemas", { operation: "create", ...payload });
    setSchemaId((result.schema as SchemaItem).id);
    setEditor(undefined);
    await refresh();
  });

  const openSchema = (id: string) => perform(async () => {
    const result = await call("schemas", { operation: "get", schema_id: id });
    const detail = result.schema as SchemaDetail;
    setEditor({ id: detail.id, name: detail.name, description: detail.description ?? "",
      system_prompt: detail.system_prompt, definition: pretty(detail.definition) });
  });

  const openProjection = (id: string) => perform(async () => {
    setProjection((await call("projections", { projection_id: id })).projection as ProjectionDetail);
  });

  const loadDraft = useCallback(async (id: string) => {
    const current = generation.current;
    const result = await call("draft", { operation: "get", draft_id: id });
    if (generation.current !== current) return;
    setPending(undefined);
    setDraftDetail(result.draft as DraftDetail);
  }, [call]);

  const buildDraft = () => perform(async () => {
    const result = await call("draft", { operation: "create", projection_ids: chosen });
    const built = result.draft as { id: string; entities: number; relations: number };
    setNotice(t("Draft ready for review: {v0} entities, {v1} relations", {
      v0: built.entities, v1: built.relations }));
    setChosen([]);
    await refresh();
    await loadDraft(built.id);
  });

  const decide = (operation: "submit" | "approve" | "reject", confirmed?: Pending) =>
    draftDetail && perform(async () => {
      const args = confirmed?.args ?? { operation, draft_id: draftDetail.id,
        expected_revision: draftDetail.revision, notes: reviewNotes || null };
      const result = await call("review", args, !!confirmed);
      if (result.status === "confirmation_required") {
        setPending({ args, reasons: (result.reasons as string[]) ?? [] });
        return;
      }
      setPending(undefined); setReviewNotes("");
      const published = result.graph as { entities: number; relations: number } | undefined;
      setNotice(published
        ? t("Published as fact: {v0} entities and {v1} relations are now in the graph", {
            v0: published.entities, v1: published.relations })
        : t("Draft {v0}", { v0: String(result.decision).toLowerCase() }));
      await refresh();
      await loadGraph();
      await loadDraft(draftDetail.id);
    });

  const openJob = (id: string) => perform(async () => {
    const result = await call("jobs", { job_id: id });
    setJobDetail({ job: result.job as Job, events: (result.events as JobEvent[]) ?? [] });
  });

  const saveSettings = (patch: Partial<Settings>) => perform(async () => {
    const result = await call("settings", patch);
    setSettings(result.settings as Settings);
    await refresh();
  });

  const counts = overview?.counts ?? {};
  const current = settings ?? { collection_name: "", pdf_engine: "auto", mineru_base_url: "" };
  const markdownInline = sections.isInline("markdown");
  const currentSource = sources.find(item => item.id === selectedSource);
  const running = jobs.filter(job => ACTIVE.has(job.status));
  // A search can drop the selected entity; the panel follows what is on the map.
  const selectedEntity = graph?.entities.find(item => item.id === entity);

  return <div className="knowledge-app nodrag nowheel" aria-label={t("{v0} knowledge base", { v0: card.name })}>
    <header className="knowledge-toolbar">
      <span className="knowledge-badge">{t("Knowledge base")}</span>
      <span className="knowledge-counts">
        {t("{v0} documents · {v1} projections · {v2} facts · {v3} entities", {
          v0: counts.sources ?? 0, v1: counts.projections ?? 0,
          v2: counts.facts ?? 0, v3: counts.entities ?? 0 })}
      </span>
      {!!running.length && <span className="knowledge-running" role="status">
        {t("{v0} conversion running", { v0: running.length })}</span>}
      <details className="knowledge-settings">
        <summary>{t("Settings")}</summary>
        <label>{t("Collection name")}
          <input value={current.collection_name} disabled={busy}
            onChange={event => setSettings({ ...current, collection_name: event.target.value })}
            onBlur={event => void saveSettings({ collection_name: event.target.value })} />
        </label>
        <label>{t("PDF engine")}
          <select value={current.pdf_engine} disabled={busy}
            onChange={event => void saveSettings({ pdf_engine: event.target.value })}>
            {["auto", "pymupdf4llm", "mineru", "text"].map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label>{t("MinerU base URL")}
          <input value={current.mineru_base_url} disabled={busy} placeholder="https://mineru.net"
            onChange={event => setSettings({ ...current, mineru_base_url: event.target.value })}
            onBlur={event => void saveSettings({ mineru_base_url: event.target.value })} />
        </label>
        <small>{t("The MinerU token comes from the OAW_MINERU_TOKEN environment variable, never from this card.")}</small>
        <p>{t("Available engines: {v0}", { v0: (overview?.engines ?? []).join(", ") || "—" })}</p>
      </details>
      <button type="button" disabled={busy} onClick={() => void perform(async () => {
        setNotice(""); await refresh(); await loadGraph();
      })}>{t("Refresh")}</button>
    </header>
    {error && <p role="alert" className="knowledge-error">{error}</p>}
    {notice && <p role="status" className="knowledge-notice">{notice}</p>}

    <div className="knowledge-grid">
      <WorkspaceSection id="sources" title={t("Sources")} className="knowledge-section">
        <h3>{t("Sources")}</h3>
        <label className="knowledge-upload">{t("Add a document")}
          <input type="file" disabled={busy} onChange={event => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void upload(file);
          }} />
        </label>
        <small>{t("PDF, markdown, text, CSV or JSON up to 32 MiB. Conversion runs in the background.")}</small>
        {!sources.length && <p className="knowledge-empty">{t("Nothing uploaded yet.")}</p>}
        <ul className="knowledge-list">
          {sources.map(item => <li key={item.id}>
            <button type="button" aria-pressed={selectedSource === item.id}
              onClick={() => { setSelectedSource(item.id); setSchemaId(schemaId || schemas[0]?.id || ""); }}>
              <span>{item.filename}</span>
              <small>{bytes(item.size)} · {item.markdown
                ? t("markdown via {v0}", { v0: item.markdown.engine ?? "?" })
                : t("awaiting conversion")} · {t("{v0} projections", { v0: item.projections })}</small>
            </button>
          </li>)}
        </ul>
      </WorkspaceSection>

      <WorkspaceSection id="markdown" title={t("Markdown")} className={`knowledge-section${markdownInline ? " knowledge-wide" : ""}`}>
        <h3>{t("Markdown")}</h3>
        {!currentSource ? <p className="knowledge-empty">{t("Select a source to read its extracted markdown.")}</p> : <>
          <div className="knowledge-projectbar">
            <label>{t("Extraction schema")}
              <select value={schemaId} disabled={busy} onChange={event => setSchemaId(event.target.value)}>
                <option value="">{t("Choose a schema")}</option>
                {schemas.map(item => <option key={item.id} value={item.id}>{item.name} v{item.version}</option>)}
              </select>
            </label>
            <label>{t("Model")}
              <select value={selectedModel} disabled={busy || !models.length} onChange={event => setModel(event.target.value)}>
                {!models.length && <option value="">{t("No model configured")}</option>}
                {models.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            <button type="button" className="knowledge-primary" disabled={busy || !extracted || !schemaId || !selectedModel}
              onClick={() => void project()}>{busy ? t("Working…") : t("Project to JSON")}</button>
          </div>
          {documentError ? <p className="knowledge-empty">{documentError}</p>
            : !extracted ? <p role="status">{t("Loading markdown…")}</p> : <>
            <p className="knowledge-meta">{t("{v0} · {v1} characters · engine {v2}", {
              v0: extracted.filename ?? currentSource.filename, v1: extracted.total_characters,
              v2: extracted.engine ?? "?" })}</p>
            <pre className="knowledge-markdown">{extracted.markdown}</pre>
            {extracted.has_more && <button type="button" disabled={busy} onClick={() => void extend()}>{t("Load more")}</button>}
          </>}
        </>}
      </WorkspaceSection>

      <WorkspaceSection id="schemas" title={t("Schemas")} className="knowledge-section">
        <h3>{t("Schemas")}</h3>
        <p className="knowledge-meta">{t("A schema is a JSON Schema plus the system prompt used to extract it.")}</p>
        <ul className="knowledge-list">
          {schemas.map(item => <li key={item.id}>
            <button type="button" aria-pressed={editor?.id === item.id} disabled={busy} onClick={() => void openSchema(item.id)}>
              <span>{item.name}</span><small>v{item.version} · {item.description || item.domain}</small>
            </button>
          </li>)}
        </ul>
        {!editor && <button type="button" disabled={busy} onClick={() => setEditor({
          id: "", name: "", description: "", system_prompt: "",
          definition: pretty({ type: "object", required: [], properties: { entities: { type: "array", items: { type: "object" } }, relations: { type: "array", items: { type: "object" } } } }),
        })}>{t("New schema")}</button>}
        {editor && <div className="knowledge-form">
          <label>{t("Name")}<input value={editor.name} disabled={busy}
            onChange={event => setEditor({ ...editor, name: event.target.value })} /></label>
          <label>{t("Description")}<input value={editor.description} disabled={busy}
            onChange={event => setEditor({ ...editor, description: event.target.value })} /></label>
          <label>{t("System prompt")}<textarea value={editor.system_prompt} disabled={busy} rows={3}
            onChange={event => setEditor({ ...editor, system_prompt: event.target.value })} /></label>
          <label>{t("JSON Schema definition")}<textarea value={editor.definition} disabled={busy} rows={8} spellCheck={false}
            onChange={event => setEditor({ ...editor, definition: event.target.value })} /></label>
          <div className="knowledge-formbar">
            <button type="button" className="knowledge-primary" disabled={busy || !editor.name || !editor.system_prompt}
              onClick={() => void saveSchema()}>{editor.id ? t("Save schema") : t("Create schema")}</button>
            <button type="button" disabled={busy} onClick={() => setEditor(undefined)}>{t("Cancel")}</button>
          </div>
        </div>}
      </WorkspaceSection>

      <WorkspaceSection id="projections" title={t("Projections")} className="knowledge-section">
        <h3>{t("Projections")}</h3>
        <p className="knowledge-meta">{t("Structured extractions. A projection is a candidate until a person approves a draft built from it.")}</p>
        {!projections.length && <p className="knowledge-empty">{t("No projections yet.")}</p>}
        <ul className="knowledge-list">
          {projections.map(item => <li key={item.id} className="knowledge-checkrow">
            <label><input type="checkbox" checked={chosen.includes(item.id)} disabled={busy}
              onChange={event => setChosen(event.target.checked
                ? [...chosen, item.id] : chosen.filter(value => value !== item.id))} />
              <span className="knowledge-visually-hidden">{t("Select projection {v0}", { v0: shortId(item.id) })}</span>
            </label>
            <button type="button" aria-pressed={projection?.id === item.id} disabled={busy}
              onClick={() => void openProjection(item.id)}>
              <span>{item.summary || shortId(item.id)}</span>
              <small>{item.valid === false ? t("invalid") : t("valid")} · {schemas.find(s => s.id === item.schema_id)?.name ?? shortId(item.schema_id)}</small>
            </button>
          </li>)}
        </ul>
        <button type="button" className="knowledge-primary" disabled={busy || !chosen.length}
          onClick={() => void buildDraft()}>{t("Build draft from {v0} selected", { v0: chosen.length })}</button>
        {projection && <div className="knowledge-detail">
          <h4>{t("Projection {v0}", { v0: shortId(projection.id) })}</h4>
          {projection.validation?.valid === false && <p role="alert" className="knowledge-error">
            {(projection.validation.errors ?? projection.validation.missing ?? []).join("; ") || t("Does not satisfy the schema")}</p>}
          <p className="knowledge-meta">{t("Evidence: {v0}", {
            v0: projection.evidence.map(link => shortId(link.source_id)).join(", ") || "—" })}</p>
          <pre>{pretty(projection.data)}</pre>
        </div>}
      </WorkspaceSection>

      <WorkspaceSection id="review" title={t("Review")} className="knowledge-section">
        <h3>{t("Review")}</h3>
        <p className="knowledge-meta">{t("Approving publishes a fact revision and is the only thing that writes the graph.")}</p>
        {!drafts.length && <p className="knowledge-empty">{t("No drafts yet. Select projections to build one.")}</p>}
        <ul className="knowledge-list">
          {drafts.map(item => <li key={item.id}>
            <button type="button" aria-pressed={draftDetail?.id === item.id} disabled={busy}
              onClick={() => void perform(() => loadDraft(item.id))}>
              <span>{shortId(item.id)}</span><small>{item.status} · {t("revision {v0}", { v0: item.revision })} · {item.created_by}</small>
            </button>
          </li>)}
        </ul>
        {draftDetail && <div className="knowledge-detail">
          <h4>{t("Draft {v0}", { v0: shortId(draftDetail.id) })}</h4>
          <p className="knowledge-meta">{t("{v0} · {v1} entities · {v2} relations · {v3} evidence links", {
            v0: draftDetail.status, v1: draftDetail.graph.entities?.length ?? 0,
            v2: draftDetail.graph.relations?.length ?? 0, v3: draftDetail.evidence_ids.length })}</p>
          <pre>{pretty(draftDetail.graph)}</pre>
          <label>{t("Review notes")}<textarea value={reviewNotes} rows={2} disabled={busy}
            onChange={event => setReviewNotes(event.target.value)} /></label>
          {pending ? <div role="alert" className="knowledge-confirm">
            <strong>{t("Publish this draft as fact?")}</strong>
            <p>{pending.reasons.join(" ")}</p>
            <button type="button" className="knowledge-primary" disabled={busy}
              onClick={() => void decide("approve", pending)}>{t("Confirm and publish")}</button>
            <button type="button" disabled={busy} onClick={() => setPending(undefined)}>{t("Cancel")}</button>
          </div> : <div className="knowledge-formbar">
            <button type="button" disabled={busy || draftDetail.status !== "DRAFT"}
              onClick={() => void decide("submit")}>{t("Submit for review")}</button>
            {/* mkb only allows approve/reject once a draft has moved past DRAFT into IN_REVIEW. */}
            <button type="button" className="knowledge-primary" disabled={busy || draftDetail.status !== "IN_REVIEW"}
              onClick={() => void decide("approve")}>{t("Approve")}</button>
            <button type="button" disabled={busy || draftDetail.status !== "IN_REVIEW"}
              onClick={() => void decide("reject")}>{t("Reject")}</button>
          </div>}
        </div>}
      </WorkspaceSection>

      <WorkspaceSection id="graph" title={t("Graph")} className="knowledge-section knowledge-wide">
        <h3>{t("Knowledge graph")}</h3>
        <div className="knowledge-formbar">
          <label>{t("Filter by name")}<input value={filter} disabled={busy}
            onChange={event => setFilter(event.target.value)} /></label>
          <button type="button" disabled={busy} onClick={() => void perform(() =>
            loadGraph(filter.trim() ? { name_contains: filter.trim() } : {}))}>{t("Search")}</button>
          <button type="button" disabled={busy} onClick={() => void perform(async () => {
            setFilter(""); setEntity(""); await loadGraph();
          })}>{t("Show everything")}</button>
        </div>
        {!graph?.entities.length ? <p className="knowledge-empty">{t("The graph is empty until a draft is approved.")}</p> : <>
          <p className="knowledge-meta">{t("{v0} entities · {v1} relations{v2}", {
            v0: graph.entities.length, v1: graph.relations.length,
            v2: graph.truncated ? t(" · result limited") : "" })}</p>
          <div className="knowledge-graph">
            <GraphMap entities={graph.entities} relations={graph.relations} selected={entity}
              onSelect={setEntity} onExpand={expand} />
            <div className="knowledge-graphside">
              <ul className="knowledge-list">
                {graph.entities.map(item => <li key={item.id}>
                  <button type="button" aria-pressed={entity === item.id} disabled={busy}
                    onClick={() => setEntity(item.id)}>
                    <span>{item.name}</span><small>{item.type}</small>
                  </button>
                </li>)}
              </ul>
              {selectedEntity && <div className="knowledge-detail">
                <h4>{selectedEntity.name}</h4>
                <p className="knowledge-meta">{selectedEntity.type}</p>
                <button type="button" disabled={busy} onClick={() => expand(selectedEntity.id)}>
                  {t("Show neighbours")}</button>
                {!!Object.keys(selectedEntity.properties ?? {}).length && <pre>{pretty(selectedEntity.properties)}</pre>}
                <ul className="knowledge-relations">
                  {graph.relations.filter(relation => relation.source_id === selectedEntity.id
                      || relation.target_id === selectedEntity.id).map(relation => {
                    const name = (id: string) => graph.entities.find(item => item.id === id)?.name ?? shortId(id);
                    return <li key={relation.id}>{name(relation.source_id)} →{relation.type}→ {name(relation.target_id)}</li>;
                  })}
                </ul>
              </div>}
            </div>
          </div>
        </>}
      </WorkspaceSection>

      <WorkspaceSection id="jobs" title={t("Jobs")} className="knowledge-section">
        <h3>{t("Conversion jobs")}</h3>
        {!jobs.length && <p className="knowledge-empty">{t("No jobs yet.")}</p>}
        <ul className="knowledge-list">
          {jobs.map(job => <li key={job.id}>
            <button type="button" aria-pressed={jobDetail?.job.id === job.id} disabled={busy}
              onClick={() => void openJob(job.id)}>
              <span>{shortId(job.id)} · {job.status}</span>
              <small>{job.error || job.message || job.kind}</small>
            </button>
          </li>)}
        </ul>
        {jobDetail && <div className="knowledge-detail">
          <h4>{t("Job {v0}", { v0: shortId(jobDetail.job.id) })}</h4>
          {jobDetail.job.error && <p role="alert" className="knowledge-error">{jobDetail.job.error}</p>}
          <ul className="knowledge-events">
            {jobDetail.events.map((event, index) => <li key={index}>
              <code>{event.step ?? event.event}</code> {event.message}
            </li>)}
          </ul>
        </div>}
      </WorkspaceSection>
    </div>
  </div>;
}

export default { apiVersion: 1, views: { preview: Preview, workspace: Workspace } } satisfies FrontendPlugin;
