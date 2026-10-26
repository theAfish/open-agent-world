import { t, WorkspaceEmptyState, WorkspaceToolbar } from "@oaw/plugin-api";
import type { PluginViewProps } from "@oaw/plugin-api";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ArrowRight, Check, CheckCircle2, ChevronDown, ChevronRight, FileText, LoaderCircle, Pencil, Sparkles, Table2 } from "lucide-react";
import { InsightsSection } from "./Insights";
import type { InsightKind } from "./Insights";
import { RecordForm } from "./RecordForm";
import { RecordTable, flattenValues } from "./RecordView";
import type { Definition } from "./RecordView";
import { SchemaEditor } from "./SchemaEditor";
import type { SchemaDetail, SchemaItem } from "./SchemaEditor";
import "./ResultsFlow.css";

export type RecordItem = { id: string; group_id: string; schema_id: string; bundle_id: string | null; name: string;
  data: Record<string, unknown>; validation: { valid?: boolean; errors?: string[]; missing?: string[] } | null;
  note: string | null; status: "draft" | "published";
  revision: number; created_by: string | null; created_at: string; updated_at: string };
type RecordDetail = RecordItem & { sources: { source_id: string; filename: string | null }[] };
type SourceItem = { id: string; filename: string; group_name: string | null; records: number };
type Pending = { args: Record<string, unknown>; reasons: string[]; record_id: string };
type Agent = { id: string; name: string };

type Props = {
  host: PluginViewProps["host"]; cardId: string; deployed: boolean; busy: boolean; version: number;
  /** Literature-kind schemas, and the records read with them (no upload behind them). */
  schemas: SchemaItem[]; records: RecordItem[]; sources: SourceItem[];
  schemaId: string; onSchemaChange: (id: string) => void;
  activeGroup: string; groupCount: number;
  groupArgs: () => Record<string, unknown>; groupFilter: () => Record<string, unknown>;
  call: (action: string, args?: Record<string, unknown>, confirm?: boolean) => Promise<Record<string, unknown>>;
  perform: (work: () => Promise<void>) => Promise<void>;
  request: (path: string, body: unknown, deployed: boolean) => Promise<Record<string, unknown>>;
  refresh: () => Promise<void>;
  /** The agent for record extraction (Knowledge extract) and for insights (Knowledge experiment). */
  extractAgent?: Agent; labAgent?: Agent; selectedModel: string;
  modelSelect: ReactNode; groupSelect: ReactNode; refreshControls: ReactNode; banner: ReactNode;
  setNotice: (value: string) => void; setError: (value: string) => void;
  onOpenSource: (sourceId: string) => void;
  onAddSources?: () => void;
  kindLabel: (kind: InsightKind) => string;
  /** A record another part of the card asked to open here (an insight's evidence). */
  focusRecordId: string; onFocused: () => void;
};

const shortId = (value: string) => value.length > 13 ? `${value.slice(0, 8)}…` : value;
const pretty = (value: unknown) => JSON.stringify(value, null, 2);

/** Literature → Records: the schemas that read structured data out of sources, the
 * records they produced, and the insights over those records. The record list is
 * the Experiment tab's in miniature — rows unfold to a table, a draft edits in place,
 * publishing asks once — without the upload machinery a literature source has no use for. */
export function RecordsSection({
  host, cardId, deployed, busy, version, schemas, records, sources, schemaId, onSchemaChange, activeGroup, groupCount,
  groupArgs, groupFilter, call, perform, request, refresh, extractAgent, labAgent, selectedModel,
  modelSelect, groupSelect, refreshControls, banner, setNotice, setError, onOpenSource, onAddSources, kindLabel, focusRecordId, onFocused,
}: Props) {
  const [schemaDetails, setSchemaDetails] = useState<Record<string, SchemaDetail>>({});
  const [picked, setPicked] = useState<string[]>([]);
  const [replace, setReplace] = useState(false);
  const [extracting, setExtracting] = useState<{ agent_id: string }>();
  const [requesting, setRequesting] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [details, setDetails] = useState<Record<string, RecordDetail>>({});
  const [editingId, setEditingId] = useState("");
  const [draft, setDraft] = useState<{ name: string; note: string; data: Record<string, unknown>; json: string; raw: boolean }>();
  const [pending, setPending] = useState<Pending>();
  const [status, setStatus] = useState<"" | "draft" | "published">("");
  const [query, setQuery] = useState("");
  const [insightSchemaId, setInsightSchemaId] = useState("");
  const generation = useRef(0);
  const schemaPanel = useRef<HTMLDetailsElement>(null);
  const extractionPanel = useRef<HTMLDetailsElement>(null);
  const recordPanel = useRef<HTMLElement>(null);
  const insightPanel = useRef<HTMLElement>(null);

  const currentSchema = schemas.find(item => item.id === schemaId);
  const scoped = records.filter(item => !currentSchema || item.schema_id === currentSchema.id);
  const draftCount = scoped.filter(item => item.status === "draft").length;
  const approvedCount = scoped.length - draftCount;
  const processing = !!extracting || requesting;
  const reveal = (panel: HTMLDetailsElement | null) => {
    if (!panel) return;
    panel.open = true;
    panel.scrollIntoView?.({ block: "nearest", behavior: "auto" });
  };
  const showRecords = (nextStatus: "" | "draft" | "published" = "draft") => {
    setStatus(nextStatus); setQuery("");
    recordPanel.current?.scrollIntoView?.({ block: "start", behavior: "auto" });
  };

  // Definitions by schema id: the tables and the edit form need the properties.
  useEffect(() => {
    const wanted = schemas.map(item => item.id).filter(id => !(id in schemaDetails));
    if (!wanted.length) return;
    const current = generation.current;
    Promise.allSettled(wanted.map(id => call("schemas", { operation: "get", schema_id: id }))).then(results => {
      if (generation.current !== current) return;
      const loaded: Record<string, SchemaDetail> = {};
      results.forEach(result => { if (result.status === "fulfilled") { const detail = result.value.schema as SchemaDetail; loaded[detail.id] = detail; } });
      if (Object.keys(loaded).length) setSchemaDetails(previous => ({ ...previous, ...loaded }));
    });
    // schemaDetails is what this effect fills; keying on the schema list is enough.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [call, schemas]);
  useEffect(() => () => { generation.current += 1; }, []);
  useEffect(() => {
    const available = new Set(sources.map(item => item.id));
    setPicked(previous => {
      const next = previous.filter(id => available.has(id));
      return next.length === previous.length ? previous : next;
    });
  }, [sources]);

  const loadDetail = useCallback(async (id: string) => {
    const current = generation.current;
    const result = await call("experiments", { operation: "get", record_id: id });
    if (generation.current !== current) return;
    setDetails(previous => ({ ...previous, [id]: result.experiment as RecordDetail }));
  }, [call]);

  const toggle = (id: string) => {
    setExpanded(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; });
    if (!expanded.has(id) && !details[id]) void loadDetail(id).catch(reason => setError(String(reason)));
  };

  useEffect(() => {
    if (!focusRecordId) return;
    setStatus(""); setQuery("");
    if (records.some(item => item.id === focusRecordId)) {
      if (currentSchema && records.find(item => item.id === focusRecordId)?.schema_id !== currentSchema.id) onSchemaChange("");
      if (!expanded.has(focusRecordId)) toggle(focusRecordId);
      onFocused();
    }
    // Runs when the focus request or the record list changes; toggle/expanded are what it drives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusRecordId, records]);

  const extract = () => perform(async () => {
    if (!currentSchema) throw new Error(t("Choose a schema first"));
    const sourceIds = picked.filter(id => sources.some(item => item.id === id));
    if (!sourceIds.length) throw new Error(t("Select at least one converted source first"));
    if (extractAgent && host.runAgent && !deployed) {
      const body = { schema_id: currentSchema.id, schema_name: currentSchema.name, source_ids: sourceIds, knowledge_card: cardId, replace_drafts: replace };
      await host.runAgent(extractAgent.id, [
        "Perform the record extraction described by the JSON data below.",
        "RECORD EXTRACTION REQUEST", JSON.stringify(body), "END RECORD EXTRACTION REQUEST",
        "For each source_id: call knowledge_record_extract_prompt with this schema_id and that source_id, read the whole document",
        "(follow has_more with offset), produce one record per specimen, material or case the document reports values for,",
        `and save them with one knowledge_record_save call for that source with replace_drafts=${replace ? "true" : "false"}`,
        "(an empty list when the document reports nothing for the schema). Then report what you saved and what was unclear.",
      ].join("\n"));
      setExtracting({ agent_id: extractAgent.id });
      setNotice(t("{v0} is reading {v1} source(s) with \"{v2}\"…", { v0: extractAgent.name, v1: sourceIds.length, v2: currentSchema.name }));
      return;
    }
    if (!selectedModel) throw new Error(deployed
      ? t("This deployment has no default model configured yet")
      : t("Configure a model connection first, or connect an Agent with the Knowledge extract relationship"));
    setRequesting(true);
    const result = await request(`knowledge/${cardId}/records`, {
      schema_id: currentSchema.id, source_ids: sourceIds, model: selectedModel, replace_drafts: replace }, deployed)
      .finally(() => setRequesting(false));
    const saved = result.records as unknown[];
    const invalid = result.invalid as number;
    setNotice(invalid
      ? t("Read {v0} record(s); {v1} do not satisfy the schema — open them to see why", { v0: saved.length, v1: invalid })
      : t("Read {v0} record(s) from {v1} source(s) · review and publish them below", { v0: saved.length, v1: sourceIds.length }));
    setPicked([]);
    await refresh();
  });

  useEffect(() => {
    if (!extracting) return;
    let active = true;
    const timer = window.setInterval(() => {
      host.getAgentInfo(extracting.agent_id).then(async info => {
        const state = info as { status?: string; active_run_id?: string | null };
        if (!active || state.status === "running" || state.active_run_id) return;
        active = false; window.clearInterval(timer);
        setExtracting(undefined); setPicked([]);
        setNotice(t("The agent finished · review the records below"));
        await perform(refresh);
      }).catch(() => undefined);
    }, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [extracting, host, perform, refresh, setNotice]);

  const beginEdit = (record: RecordItem) => {
    const data = details[record.id]?.data ?? record.data;
    setEditingId(record.id); setPending(undefined);
    setDraft({ name: record.name, note: record.note ?? "", data, json: pretty(data), raw: false });
  };

  const saveEdit = (record: RecordItem) => draft && perform(async () => {
    let data = draft.data;
    if (draft.raw) {
      try { data = JSON.parse(draft.json); }
      catch { throw new Error(t("The record data must be valid JSON")); }
    }
    if (!draft.name.trim()) throw new Error(t("Name the record first"));
    await call("experiment_update", { record_id: record.id, name: draft.name.trim(), data, note: draft.note, expected_revision: record.revision });
    setEditingId(""); setDraft(undefined);
    setNotice(t("Changes saved"));
    await refresh(); await loadDetail(record.id);
  });

  const publish = (record: RecordItem, confirmed?: Pending) => perform(async () => {
    const args = confirmed?.args ?? { operation: "publish", record_id: record.id, expected_revision: record.revision };
    const result = await call("experiment_review", args, !!confirmed);
    if (result.status === "confirmation_required") {
      setPending({ record_id: record.id, args, reasons: (result.reasons as string[]) ?? [] });
      return;
    }
    setPending(undefined);
    setNotice(t("Record \"{v0}\" published", { v0: record.name }));
    await refresh(); await loadDetail(record.id);
  });

  const unpublish = (record: RecordItem) => perform(async () => {
    await call("experiment_review", { operation: "unpublish", record_id: record.id, expected_revision: record.revision });
    setNotice(t("Record \"{v0}\" is a draft again", { v0: record.name }));
    await refresh(); await loadDetail(record.id);
  });

  const remove = (record: RecordItem) => perform(async () => {
    await call("experiment_review", { operation: "delete", record_id: record.id });
    if (editingId === record.id) setEditingId("");
    setNotice(t("Record deleted"));
    await refresh();
  });

  const needle = query.trim().toLowerCase();
  const schemaName = (id: string) => schemas.find(item => item.id === id)?.name ?? shortId(id);
  const filtered = scoped.filter(item => (!status || item.status === status) && (!needle
    || [item.name, item.note ?? "", schemaName(item.schema_id), ...flattenValues(item.data)].join("\n").toLowerCase().includes(needle)));
  const publishedCounts = records.reduce<Record<string, number>>((counts, item) => {
    if (item.status === "published") counts[item.schema_id] = (counts[item.schema_id] ?? 0) + 1;
    return counts;
  }, {});
  const canRunAgent = !deployed && typeof host.runAgent === "function" && !!extractAgent;
  const blocker = !currentSchema ? t("Choose a schema above to extract records with.")
    : processing ? t("An extraction is already running.")
    : (!canRunAgent && !selectedModel) ? (deployed ? t("This deployment has no default model configured yet.")
      : t("Configure a model connection in OAW Settings, or connect an Agent to this card with the Knowledge extract relationship."))
    : "";

  return <>
    <WorkspaceToolbar label={t("Records")} className="knowledge-section-toolbar">
      {groupSelect}
      <label className="knowledge-groupselect knowledge-result-schema" data-needed={!currentSchema && !!sources.length || undefined}>
        <span className="knowledge-visually-hidden">{t("Record schema")}</span>
        <select aria-label={t("Record schema")} value={currentSchema?.id ?? ""} disabled={busy || processing} onChange={event => onSchemaChange(event.target.value)}>
          <option value="">{schemas.length ? t("All schemas") : t("No schema yet")}</option>
          {schemas.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      <div className="knowledge-heading-actions">{refreshControls}</div>
    </WorkspaceToolbar>
    {banner}

    <ol className="knowledge-result-steps" aria-label={t("Record workflow")}>
      <li data-state={sources.length ? "complete" : "current"}><button type="button" disabled={busy || (!sources.length && !onAddSources)}
        onClick={() => sources.length ? reveal(extractionPanel.current ?? schemaPanel.current) : onAddSources?.()}>
        <span className="knowledge-result-step-icon">{sources.length ? <Check size={17} /> : <FileText size={17} />}</span>
        <span><strong>{t("Sources")}</strong><small>{t("{v0} ready", { v0: sources.length })}</small></span></button></li>
      <li data-state={processing ? "running" : scoped.length ? "complete" : sources.length ? "current" : undefined}><button type="button" disabled={busy || !sources.length}
        onClick={() => reveal(schemas.length ? extractionPanel.current : schemaPanel.current)}>
        <span className="knowledge-result-step-icon">{processing ? <LoaderCircle size={17} /> : <Table2 size={17} />}</span>
        <span><strong>{t("Extract records")}</strong><small>{currentSchema?.name ?? t("Choose fields")}</small></span></button></li>
      <li data-state={draftCount ? "current" : approvedCount ? "complete" : undefined}><button type="button" disabled={busy || !scoped.length}
        onClick={() => showRecords(draftCount ? "draft" : "published")}>
        <span className="knowledge-result-step-icon">{approvedCount && !draftCount ? <Check size={17} /> : <CheckCircle2 size={17} />}</span>
        <span><strong>{t("Review and publish")}</strong><small>{draftCount ? t("{v0} to review", { v0: draftCount }) : t("{v0} published", { v0: approvedCount })}</small></span></button></li>
    </ol>

    {!sources.length && !scoped.length && <WorkspaceEmptyState className="knowledge-results-start" icon={<FileText size={30} />}
      title={t("Compare what your papers report")}
      description={t("Start with a converted source, then choose the fields to collect.")}
      actions={onAddSources && <button type="button" className="knowledge-primary" disabled={busy} onClick={onAddSources}>
        {t("Add sources")}<ArrowRight size={15} /></button>} />}

    {(!!sources.length || !!schemas.length) && <details ref={schemaPanel} className="knowledge-disclosure knowledge-workflow knowledge-results-setup" open={!schemas.length || undefined}>
      <summary>{t("Schemas")} {!!schemas.length && <span className="knowledge-total">{schemas.length}</span>}</summary>
      <p className="knowledge-empty">{t("Choose the fields you want to compare.")}</p>
      <SchemaEditor busy={busy || processing} schemas={schemas} kind="literature" call={call} perform={perform} compact
        onSaved={async saved => { setSchemaDetails(previous => { const next = { ...previous }; delete next[saved.id]; return next; }); onSchemaChange(saved.id); await refresh(); }} />
    </details>}

    {!!schemas.length && !!sources.length && <details ref={extractionPanel} className="knowledge-disclosure knowledge-workflow knowledge-results-setup" open={!scoped.length || undefined}>
      <summary>{t("Extract records")}{!!picked.length && <span className="knowledge-total">{t("{v0} selected", { v0: picked.length })}</span>}</summary>
      <p className="knowledge-meta">{canRunAgent && extractAgent
        ? t("Runs the connected {v0}, which reads each source against the schema and saves draft records itself.", { v0: extractAgent.name })
        : t("Reads each selected source against the schema with one model call through the selected model.")}</p>
      {!canRunAgent && !deployed && modelSelect}
      {deployed && <p className="knowledge-meta">{t("Model: {v0}", { v0: selectedModel || t("none configured") })}</p>}
      {!sources.length ? <p className="knowledge-empty">{t("Convert a source in Sources first.")}</p> : <ul className="knowledge-list knowledge-result-sources">
        {sources.map(item => <li key={item.id} className="knowledge-checkrow" data-selected={picked.includes(item.id) || undefined}>
          <label className="knowledge-result-source"><input type="checkbox" checked={picked.includes(item.id)} disabled={busy || processing}
            aria-label={t("Select {v0}", { v0: item.filename })}
            onChange={event => { const checked = event.target.checked; setPicked(previous => checked ? [...previous, item.id] : previous.filter(value => value !== item.id)); }} />
          <FileText size={19} aria-hidden="true" />
          <span className="knowledge-checkrow-label"><span>{item.filename}</span>
            <small>{!activeGroup && item.group_name && groupCount > 1 ? `${item.group_name} · ` : ""}{t("{v0} records", { v0: item.records })}</small></span>
          </label>
        </li>)}
      </ul>}
      {processing && <div role="status" className="knowledge-results-running"><LoaderCircle size={17} />{t("Reading the selected sources…")}<progress /></div>}
      <div className="knowledge-formbar">
        <button type="button" className="knowledge-primary" disabled={busy || !!blocker || !picked.length} onClick={() => void extract()}>
          <Sparkles size={14} />{processing ? t("Extracting…") : t("Extract from {v0} selected", { v0: picked.length })}</button>
        <label className="knowledge-checkrow"><input type="checkbox" checked={replace} disabled={busy || processing} onChange={event => setReplace(event.target.checked)} />
          {t("Replace these sources' draft records for this schema (published ones stay)")}</label>
      </div>
      {blocker && <p className="knowledge-empty">{blocker}</p>}
    </details>}

    {!!schemas.length && (!!sources.length || !!scoped.length) && <section ref={recordPanel} className="knowledge-insight-block knowledge-result-review">
      <div className="knowledge-result-heading"><h4>{t("Records")} {!!scoped.length && <span className="knowledge-total">{scoped.length}</span>}</h4>
        {!!approvedCount && <button type="button" disabled={busy} onClick={() => insightPanel.current?.scrollIntoView?.({ block: "start", behavior: "auto" })}>
          <Sparkles size={14} />{t("Explore insights")}<ArrowRight size={14} /></button>}</div>
      {!!scoped.length && <div className="knowledge-formbar knowledge-record-filters">
        <input type="search" value={query} disabled={busy} aria-label={t("Filter records")} placeholder={t("Filter records…")}
          onChange={event => setQuery(event.target.value)} />
        <span className="knowledge-kind-filter" role="group" aria-label={t("Status")}>
          {(["", "draft", "published"] as const).map(value => <button key={value} type="button" aria-pressed={status === value} disabled={busy}
            onClick={() => setStatus(value)}>{value === "" ? t("All") : value === "draft" ? t("Drafts") : t("Published")}
            <span className="knowledge-result-count">{value === "" ? scoped.length : value === "draft" ? draftCount : approvedCount}</span></button>)}
        </span>
      </div>}
      {!scoped.length && !!sources.length && <WorkspaceEmptyState className="knowledge-results-start is-compact" icon={<Table2 size={25} />}
        title={t("Your comparison starts here")}
        actions={<button type="button" disabled={busy} onClick={() => reveal(extractionPanel.current)}>{t("Select sources")}<ArrowRight size={14} /></button>} />}
      <ul className="knowledge-record-rows">
        {filtered.map(record => {
          const open = expanded.has(record.id);
          const detail = details[record.id];
          const definition = schemaDetails[record.schema_id]?.definition as Definition | undefined;
          return <li key={record.id} className={`knowledge-record-row${open ? " is-open" : ""}`}>
            <button type="button" className="knowledge-record-summary" aria-expanded={open} disabled={busy} onClick={() => toggle(record.id)}>
              {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <span className="knowledge-record-title">{record.name}</span>
              <small>
                <span className={`knowledge-badge knowledge-status-${record.status}`}>{record.status === "published" ? t("Published") : t("Draft")}</span>
                {!currentSchema && ` · ${schemaName(record.schema_id)}`}
                {record.validation?.valid === false && <span className="knowledge-error"> · {t("invalid")}</span>}
              </small>
            </button>
            {open && <div className="knowledge-record-body">
              {editingId === record.id && draft ? <>
                <label className="knowledge-record-field">{t("Name")}<input value={draft.name} disabled={busy} onChange={event => setDraft({ ...draft, name: event.target.value })} /></label>
                <div className="knowledge-formbar">
                  <button type="button" aria-pressed={draft.raw} disabled={busy} onClick={() => setDraft({ ...draft, raw: !draft.raw, json: draft.raw ? draft.json : pretty(draft.data) })}>
                    <Table2 size={13} />{draft.raw ? t("Form") : t("JSON")}</button>
                </div>
                {draft.raw
                  ? <textarea rows={10} value={draft.json} disabled={busy} spellCheck={false} aria-label={t("Record data")}
                      onChange={event => setDraft({ ...draft, json: event.target.value })} />
                  : <RecordForm definition={(definition ?? { properties: {} }) as Record<string, unknown>} value={draft.data} disabled={busy}
                      onChange={next => setDraft({ ...draft, data: next })} />}
                <label className="knowledge-record-field">{t("Note")}<input value={draft.note} disabled={busy} onChange={event => setDraft({ ...draft, note: event.target.value })} /></label>
                <div className="knowledge-formbar">
                  <button type="button" className="knowledge-primary" disabled={busy} onClick={() => void saveEdit(record)}>{t("Save changes")}</button>
                  <button type="button" disabled={busy} onClick={() => { setEditingId(""); setDraft(undefined); }}>{t("Cancel")}</button>
                </div>
              </> : <>
                {record.validation?.valid === false && <p role="alert" className="knowledge-error">
                  {(record.validation.errors ?? record.validation.missing ?? []).join("; ") || t("Does not satisfy the schema")}</p>}
                <RecordTable definition={definition} data={record.data} validation={record.validation} />
                <p className="knowledge-meta">
                  {t("revision {v0} · by {v1}", { v0: record.revision, v1: record.created_by ?? "—" })}{record.note ? ` · ${record.note}` : ""}
                </p>
                {!!detail?.sources.length && <span className="knowledge-insight-evidence">
                  {detail.sources.map(link => <button key={link.source_id} type="button" className="knowledge-chip" disabled={busy}
                    title={t("Open the source")} onClick={() => onOpenSource(link.source_id)}>{link.filename ?? shortId(link.source_id)}</button>)}
                </span>}
                {pending?.record_id === record.id ? <div role="alert" className="knowledge-confirm">
                  <strong>{t("Publish this record?")}</strong>
                  <p>{pending.reasons.join(" ")}</p>
                  <div className="knowledge-formbar">
                    <button type="button" className="knowledge-primary" disabled={busy} onClick={() => void publish(record, pending)}>{t("Confirm and publish")}</button>
                    <button type="button" disabled={busy} onClick={() => setPending(undefined)}>{t("Cancel")}</button>
                  </div>
                </div> : <div className="knowledge-formbar">
                  {record.status === "draft" ? <>
                    <button type="button" disabled={busy} onClick={() => beginEdit(record)}><Pencil size={13} />{t("Edit")}</button>
                    <button type="button" className="knowledge-primary" disabled={busy || record.validation?.valid === false}
                      title={record.validation?.valid === false ? t("Fix the record before publishing") : undefined}
                      onClick={() => void publish(record)}>{t("Publish")}</button>
                    <button type="button" className="knowledge-danger" disabled={busy} onClick={() => void remove(record)}>{t("Delete")}</button>
                  </> : <button type="button" disabled={busy} onClick={() => void unpublish(record)}>{t("Unpublish")}</button>}
                </div>}
              </>}
            </div>}
          </li>;
        })}
        {!!scoped.length && !filtered.length && <li className="knowledge-empty">{t("No records match")}</li>}
      </ul>
    </section>}

    {!!schemas.length && !!scoped.length && <section ref={insightPanel} className="knowledge-insight-block knowledge-embedded-insights">
      <h4>{t("Insights")}</h4>
      <InsightsSection host={host} cardId={cardId} deployed={deployed} busy={busy} version={version} embedded schemaKind="literature"
        schemas={schemas} schemaId={schemas.some(item => item.id === insightSchemaId) ? insightSchemaId : ""} onSchemaChange={setInsightSchemaId}
        publishedCounts={publishedCounts} groupArgs={groupArgs} groupFilter={groupFilter} call={call} perform={perform} request={request}
        labAgent={labAgent} canRunAgent={!deployed && typeof host.runAgent === "function" && !!labAgent} selectedModel={selectedModel}
        modelSelect={modelSelect} groupSelect={groupSelect} refreshControls={refreshControls} banner={null}
        setNotice={setNotice} setError={setError} kindLabel={kindLabel}
        onOpenExperiments={() => showRecords("draft")}
        onOpenRecord={id => { if (!expanded.has(id)) toggle(id); }} />
    </section>}
  </>;
}
