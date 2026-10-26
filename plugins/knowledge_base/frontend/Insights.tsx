import { t, WorkspaceEmptyState, WorkspaceToolbar } from "@oaw/plugin-api";
import type { PluginViewProps } from "@oaw/plugin-api";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ArrowRight, Check, CheckCircle2, LoaderCircle, Network, Pencil, Sparkles, Table2 } from "lucide-react";
import { GraphMap } from "./GraphMap";
import type { Entity, Relation } from "./GraphMap";
import "./ResultsFlow.css";

/** One insight as ``knowledge_insights`` returns it, evidence included. */
export type Insight = {
  id: string; group_id: string | null; schema_id: string | null; kind: InsightKind; title: string;
  statement: string; fields: string[]; confidence: number | null; properties: Record<string, unknown>;
  schema_ids: string[]; status: "draft" | "published"; revision: number; note: string | null; run_id: string | null;
  created_by: string | null; created_at: string; updated_at: string;
  evidence: { record_id: string; role: string; name: string | null; stale: boolean }[];
};
export type InsightKind = "anomaly" | "rule" | "trend" | "correlation" | "threshold" | "note";
export const KINDS: InsightKind[] = ["anomaly", "rule", "trend", "correlation", "threshold", "note"];
type FieldStats = {
  field: string; kind: "numeric" | "categorical" | "text" | "empty"; count: number;
  mean?: number | null; std?: number | null; min?: number; max?: number; median?: number | null;
  outliers?: { record_id: string; name: string | null; value: number; z: number }[];
  counts?: Record<string, number>; distinct?: number;
};
type Correlation = { a: string; b: string; r: number; n: number };
export type Statistics = { records: number; truncated: boolean; fields: FieldStats[]; correlations: Correlation[] };
type SchemaStatistics = { schema_id: string; schema_name: string; record_count: number; statistics: Statistics };
type CrossSchema = { schemas: string[]; matched: number; matched_names: string[]; correlations: Correlation[] };
/** ``knowledge_insights`` with operation="stats": one block per schema in scope, and
 * what the specimens several schemas share by name show across them. */
type ScopeStatistics = { schema_ids: string[]; statistics_by_schema: SchemaStatistics[]; cross_schema: CrossSchema | null };
type Graph = { entities: Entity[]; relations: Relation[]; truncated: boolean };
type Pending = { args: Record<string, unknown>; reasons: string[]; ids: string[] };
type Schema = { id: string; name: string };
type Agent = { id: string; name: string };

type Props = {
  host: PluginViewProps["host"]; cardId: string; deployed: boolean; busy: boolean;
  /** Bumped by the parent's refresh so this section reloads with the rest of the card. */
  version: number;
  schemas: Schema[]; schemaId: string; onSchemaChange: (id: string) => void;
  /** Published record counts per schema, for the extract block's summary line. */
  publishedCounts: Record<string, number>;
  groupArgs: () => Record<string, unknown>; groupFilter: () => Record<string, unknown>;
  call: (action: string, args?: Record<string, unknown>, confirm?: boolean) => Promise<Record<string, unknown>>;
  perform: (work: () => Promise<void>) => Promise<void>;
  request: (path: string, body: unknown, deployed: boolean) => Promise<Record<string, unknown>>;
  labAgent?: Agent; canRunAgent: boolean; selectedModel: string;
  modelSelect: ReactNode; groupSelect: ReactNode; refreshControls: ReactNode; banner: ReactNode;
  setNotice: (value: string) => void; setError: (value: string) => void;
  /** Jump to one record where it lives (Experiments, or Literature → Records). */
  onOpenRecord: (recordId: string) => void;
  onOpenExperiments?: () => void;
  kindLabel: (kind: InsightKind) => string;
  /** Which section's schemas the open scope ("all schemas") takes; experiment by default. */
  schemaKind?: "literature" | "experiment";
  /** Mounted inside another section (Literature → Records): no toolbar of its own. */
  embedded?: boolean;
};

const shortId = (value: string) => value.length > 13 ? `${value.slice(0, 8)}…` : value;
const num = (value: number | null | undefined, digits = 3) =>
  value === null || value === undefined ? "—" : Number.isInteger(value) ? String(value) : value.toFixed(digits);
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

export function InsightsSection({
  host, cardId, deployed, busy, version, schemas, schemaId, onSchemaChange, publishedCounts,
  groupArgs, groupFilter, call, perform, request, labAgent, canRunAgent, selectedModel,
  modelSelect, groupSelect, refreshControls, banner, setNotice, setError, onOpenRecord, onOpenExperiments, kindLabel,
  schemaKind = "experiment", embedded = false,
}: Props) {
  const [insights, setInsights] = useState<Insight[]>([]);
  const [stats, setStats] = useState<ScopeStatistics>();
  const [statsError, setStatsError] = useState("");
  const [graph, setGraph] = useState<Graph>();
  const [entity, setEntity] = useState("");
  const [kindFilter, setKindFilter] = useState<"" | InsightKind>("");
  const [editingId, setEditingId] = useState("");
  const [draft, setDraft] = useState<{ kind: InsightKind; title: string; statement: string; note: string }>();
  const [pending, setPending] = useState<Pending>();
  const [replace, setReplace] = useState(false);
  // A running agent extraction: which agent to watch until it is idle again.
  const [extracting, setExtracting] = useState<{ agent_id: string; schema_id: string }>();
  const [requesting, setRequesting] = useState(false);
  const generation = useRef(0);
  const draftPanel = useRef<HTMLElement>(null);
  const publishedPanel = useRef<HTMLElement>(null);

  // "" is the open scope: every experiment schema with published records, read
  // together, with cross-schema statistics over the specimens they share by name.
  const currentSchema = schemas.find(item => item.id === schemaId);
  const published = currentSchema ? (publishedCounts[currentSchema.id] ?? 0)
    : Object.values(publishedCounts).reduce((sum, count) => sum + count, 0);
  const processing = !!extracting || requesting;
  const scopeArgs = useCallback((): Record<string, unknown> =>
    schemaId ? { schema_id: schemaId } : { schema_kind: schemaKind }, [schemaId, schemaKind]);

  const load = useCallback(async () => {
    const current = ++generation.current;
    const [list, map, computed] = await Promise.allSettled([
      call("insights", { operation: "list", limit: 200, ...scopeArgs(), ...groupArgs() }),
      call("graph", { layer: "experiment", limit: 500, ...groupFilter() }),
      call("insights", { operation: "stats", ...scopeArgs(), ...groupArgs() }),
    ]);
    if (generation.current !== current) return;
    if (list.status === "fulfilled") setInsights((list.value.insights as Insight[]) ?? []);
    if (map.status === "fulfilled") setGraph(map.value as unknown as Graph);
    if (computed.status === "fulfilled") {
      setStats(computed.value as unknown as ScopeStatistics); setStatsError("");
    } else { setStats(undefined); setStatsError(message(computed.reason)); }
    const failure = [list, map].find(result => result.status === "rejected");
    if (failure) throw (failure as PromiseRejectedResult).reason;
  }, [call, scopeArgs, groupArgs, groupFilter]);

  useEffect(() => { void perform(load); }, [load, perform, version]);
  useEffect(() => () => { generation.current += 1; }, []);

  // Two ways to extract, as for records: a connected Lab assistant is a real Agent run
  // the card watches until idle; otherwise one JSON-mode call through the host bridge.
  const scopeLabel = currentSchema ? currentSchema.name : t("all schemas");
  const extract = () => perform(async () => {
    const scope = { schema_ids: currentSchema ? [currentSchema.id] : [], schema_kind: schemaKind, ...groupArgs() };
    if (labAgent && host.runAgent && !deployed) {
      const requestBody = { ...scope, schema_names: currentSchema ? [currentSchema.name] : schemas.map(item => item.name),
        knowledge_card: cardId, replace_drafts: replace };
      await host.runAgent(labAgent.id, [
        "Perform the insight extraction described by the JSON data below.",
        "INSIGHT EXTRACTION REQUEST", JSON.stringify(requestBody), "END INSIGHT EXTRACTION REQUEST",
        "Call knowledge_insight_prompt with exactly these schema_ids (an empty list means every schema of schema_kind with published records), schema_kind and group_id / all_groups,",
        "read every table (follow has_more with offset), propose insights grounded in the statistics and the records — including what the cross-schema",
        "statistics show — citing record_ids as evidence, and save them in one knowledge_insight_save call with the same scope",
        `and replace_drafts=${replace ? "true" : "false"}, then report what you proposed and what was unclear.`,
      ].join("\n"));
      setExtracting({ agent_id: labAgent.id, schema_id: schemaId });
      setNotice(t("{v0} is extracting insights over {v1}…", { v0: labAgent.name, v1: scopeLabel }));
      return;
    }
    if (!selectedModel) throw new Error(deployed
      ? t("This deployment has no default model configured yet")
      : t("Configure a model connection first, or connect a Lab assistant agent"));
    setRequesting(true);
    const result = await request(`knowledge/${cardId}/insights`, { ...scope, model: selectedModel, replace_drafts: replace }, deployed)
      .finally(() => setRequesting(false));
    const saved = result.insights as unknown[];
    const warnings = result.warnings as string[];
    setNotice(result.truncated
      ? t("Proposed {v0} insight(s) from the first part of a very long record table", { v0: saved.length })
      : warnings.length
        ? t("Proposed {v0} insight(s) · {v1}", { v0: saved.length, v1: warnings.join("; ") })
        : t("Proposed {v0} insight(s) over {v1} · review and publish them below", { v0: saved.length, v1: scopeLabel }));
    await load();
  });

  useEffect(() => {
    if (!extracting) return;
    let active = true;
    const timer = window.setInterval(() => {
      host.getAgentInfo(extracting.agent_id).then(async info => {
        const state = info as { status?: string; active_run_id?: string | null };
        if (!active || state.status === "running" || state.active_run_id) return;
        active = false; window.clearInterval(timer);
        setExtracting(undefined);
        setNotice(t("The Lab assistant finished · review the proposed insights below"));
        await perform(load);
      }).catch(() => undefined);
    }, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [extracting, host, perform, load, setNotice]);

  const beginEdit = (item: Insight) => {
    setEditingId(item.id);
    setDraft({ kind: item.kind, title: item.title, statement: item.statement, note: item.note ?? "" });
  };

  const saveEdit = (item: Insight) => draft && perform(async () => {
    if (!draft.title.trim()) throw new Error(t("Give the insight a title first"));
    if (!draft.statement.trim()) throw new Error(t("Write the statement first"));
    await call("insight_update", { insight_id: item.id, kind: draft.kind, title: draft.title.trim(),
      statement: draft.statement.trim(), note: draft.note, expected_revision: item.revision });
    setEditingId(""); setDraft(undefined);
    setNotice(t("Changes saved"));
    await load();
  });

  const publish = (ids: string[], confirmed?: Pending) => perform(async () => {
    const args = confirmed?.args ?? { operation: "publish", insight_ids: ids };
    const result = await call("insight_review", args, !!confirmed);
    if (result.status === "confirmation_required") {
      setPending({ args, reasons: (result.reasons as string[]) ?? [], ids });
      return;
    }
    setPending(undefined);
    const written = result.graph as { entities: number; relations: number };
    setNotice(t("Published {v0} insight(s): {v1} nodes and {v2} edges are now in the graph", {
      v0: ids.length, v1: written.entities, v2: written.relations }));
    await load();
  });

  const unpublish = (item: Insight) => perform(async () => {
    await call("insight_review", { operation: "unpublish", insight_id: item.id });
    if (entity && graph?.entities.find(node => node.id === entity)?.properties.insight_id === item.id) setEntity("");
    setNotice(t("\"{v0}\" is a draft again and is out of the graph", { v0: item.title }));
    await load();
  });

  const remove = (item: Insight) => perform(async () => {
    await call("insight_review", { operation: "delete", insight_id: item.id });
    if (editingId === item.id) setEditingId("");
    setNotice(t("Insight deleted"));
    await load();
  });

  const expand = (id: string) => {
    setEntity(id);
    void perform(async () => {
      const result = await call("graph", { operation: "traverse", entity_id: id, max_depth: 2, layer: "experiment", ...groupFilter() });
      setGraph(result as unknown as Graph);
    });
  };

  const drafts = insights.filter(item => item.status === "draft" && (!kindFilter || item.kind === kindFilter));
  const publishedInsights = insights.filter(item => item.status === "published" && (!kindFilter || item.kind === kindFilter));
  const draftCount = insights.filter(item => item.status === "draft").length;
  const publishedInsightCount = insights.length - draftCount;
  const selectedEntity = graph?.entities.find(item => item.id === entity);
  const selectedInsight = selectedEntity && insights.find(item => item.id === selectedEntity.properties.insight_id);
  const where = schemaKind === "literature" ? t("above") : t("in Experiments");
  const blocker = !published ? (currentSchema ? t("Publish some records of this schema {v0} first.", { v0: where })
      : t("Publish some records {v0} first.", { v0: where }))
    : processing ? t("An extraction is already running.")
    : (!canRunAgent && !selectedModel) ? (deployed ? t("This deployment has no default model configured yet.")
      : t("Configure a model connection in OAW Settings, or connect an Agent to this card with the Knowledge experiment relationship."))
    : "";
  const statBlocks = (stats?.statistics_by_schema ?? []).filter(block => block.statistics.records > 0);
  const totalRecords = statBlocks.reduce((sum, block) => sum + block.statistics.records, 0);

  const evidenceChips = (item: Insight) => <span className="knowledge-insight-evidence">
    {item.evidence.map(link => <button key={link.record_id} type="button" className="knowledge-chip" disabled={busy}
      title={t("{v0} · open the record", { v0: link.role.replace("_", " ") })}
      data-stale={link.stale || undefined} onClick={() => onOpenRecord(link.record_id)}>
      {link.name ?? shortId(link.record_id)}{link.stale && <small>{t("unpublished")}</small>}</button>)}
  </span>;

  const kindBadge = (kind: InsightKind) => <span className={`knowledge-kind knowledge-kind-${kind}`}>{kindLabel(kind)}</span>;

  const schemaSelect = <label className="knowledge-groupselect">
    <span className="knowledge-visually-hidden">{t("Insight schema")}</span>
    <select aria-label={t("Insight schema")} value={schemaId} disabled={busy || processing || !schemas.length}
      onChange={event => { onSchemaChange(event.target.value); setEntity(""); setEditingId(""); }}>
      {!schemas.length ? <option value="">{t("No schema yet")}</option> : <option value="">{t("All schemas (cross-schema)")}</option>}
      {schemas.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select>
  </label>;
  const kindFilterBar = <span className="knowledge-kind-filter" role="group" aria-label={t("Kind")}>
    <button type="button" aria-pressed={!kindFilter} disabled={busy} onClick={() => setKindFilter("")}>{t("All kinds")}</button>
    {KINDS.map(kind => <button key={kind} type="button" aria-pressed={kindFilter === kind} disabled={busy}
      onClick={() => setKindFilter(kind)}>{kindLabel(kind)}</button>)}
  </span>;

  return <>
    {embedded
      ? <div className="knowledge-formbar knowledge-embedded-toolbar">{schemaSelect}{!!insights.length && kindFilterBar}</div>
      : <WorkspaceToolbar label={t("Insights")} className="knowledge-section-toolbar">
        {groupSelect}
        {schemaSelect}
        <div className="knowledge-heading-actions">{!!insights.length && kindFilterBar}{refreshControls}</div>
      </WorkspaceToolbar>}
    {!embedded && banner}
    <ol className="knowledge-result-steps" aria-label={t("Insight workflow")}>
      <li data-state={published ? "complete" : "current"}><button type="button" disabled={busy || !onOpenExperiments} onClick={onOpenExperiments}>
        <span className="knowledge-result-step-icon">{published ? <Check size={17} /> : <Table2 size={17} />}</span>
        <span><strong>{t("Published records")}</strong><small>{t("{v0} ready", { v0: published })}</small></span></button></li>
      <li data-state={processing ? "running" : draftCount ? "current" : publishedInsightCount ? "complete" : published ? "current" : undefined}><button type="button" disabled={busy || !draftCount}
        onClick={() => { setKindFilter(""); draftPanel.current?.scrollIntoView?.({ block: "start", behavior: "auto" }); }}>
        <span className="knowledge-result-step-icon">{processing ? <LoaderCircle size={17} /> : <Sparkles size={17} />}</span>
        <span><strong>{t("Review insights")}</strong><small>{t("{v0} to review", { v0: draftCount })}</small></span></button></li>
      <li data-state={publishedInsightCount ? "complete" : undefined}><button type="button" disabled={busy || !publishedInsightCount}
        onClick={() => { setKindFilter(""); publishedPanel.current?.scrollIntoView?.({ block: "start", behavior: "auto" }); }}>
        <span className="knowledge-result-step-icon">{publishedInsightCount ? <Check size={17} /> : <Network size={17} />}</span>
        <span><strong>{t("Published graph")}</strong><small>{t("{v0} published", { v0: publishedInsightCount })}</small></span></button></li>
    </ol>

    {!published && !insights.length && <WorkspaceEmptyState className="knowledge-results-start" icon={<Table2 size={30} />} title={t("Insights come from published records")}
      description={t("Review and publish your records to start finding patterns.")}
      actions={onOpenExperiments && <button type="button" className="knowledge-primary" disabled={busy} onClick={onOpenExperiments}>
        {schemaKind === "literature" ? t("Review records") : t("Open experiments")}<ArrowRight size={15} /></button>} />}

    {!!published && <div className="knowledge-result-action" data-running={processing || undefined}>
      <span className="knowledge-result-action-icon">{processing ? <LoaderCircle size={23} /> : <Sparkles size={23} />}</span>
      <div><strong>{processing ? t("Finding patterns…") : t("Find patterns in your records")}</strong>
        <span>{t("{v0} published record(s)", { v0: published })}{currentSchema ? ` · ${currentSchema.name}` : ""}</span></div>
      <button type="button" className="knowledge-primary" disabled={busy || !!blocker} onClick={() => void extract()}>
        <Sparkles size={14} />{processing ? t("Extracting…") : t("Extract insights")}</button>
      {processing && <progress aria-label={t("Extracting insights")} />}
    </div>}

    {!!published && <details className="knowledge-disclosure knowledge-workflow knowledge-results-setup">
      <summary>{t("Extraction options")}</summary>
      <p className="knowledge-meta">{currentSchema
        ? t("{v0} published record(s) of \"{v1}\" are read as one table, with statistics the card computes over them.", { v0: published, v1: currentSchema.name })
        : t("{v0} published record(s) across every schema are read together — one table per schema, plus what specimens that appear in several schemas (matched by name) show across them. Pick one schema in the toolbar to narrow the scope.", { v0: published })}</p>
      <p className="knowledge-meta">{canRunAgent && labAgent
        ? t("Runs the connected {v0}, which reads the table and the statistics and proposes draft insights itself.", { v0: labAgent.name })
        : t("Extraction runs as one model call through the selected model.")}</p>
      {!canRunAgent && !deployed && modelSelect}
      {deployed && <p className="knowledge-meta">{t("Model: {v0}", { v0: selectedModel || t("none configured") })}</p>}
      <div className="knowledge-formbar">
        <label className="knowledge-checkrow"><input type="checkbox" checked={replace} disabled={busy || processing} onChange={event => setReplace(event.target.checked)} />
          {currentSchema ? t("Replace this schema's draft insights (published ones stay)") : t("Replace the cross-schema draft insights (published ones stay)")}</label>
      </div>
      {blocker && <p className="knowledge-empty">{blocker}</p>}
    </details>}

    {(!!published || !!statsError) && <details className="knowledge-disclosure knowledge-results-statistics">
      <summary>{t("Statistics")} {stats && <span className="knowledge-total">{totalRecords}</span>}</summary>
      {statsError && <p role="alert" className="knowledge-error">{statsError}</p>}
      {stats && !totalRecords && <p className="knowledge-empty">{t("No published records yet.")}</p>}
      {!!totalRecords && <p className="knowledge-meta">{t("Over {v0} published record(s). Computed by the card, not a model.", { v0: totalRecords })}</p>}
      {stats?.cross_schema && statBlocks.length > 1 && <section className="knowledge-stats-block">
        <h4>{t("Across schemas")}</h4>
        <p className="knowledge-meta">{t("{v0} specimen(s) appear in more than one schema, matched by record name.", { v0: stats.cross_schema.matched })}</p>
        {!stats.cross_schema.correlations.length
          ? <p className="knowledge-empty">{stats.cross_schema.matched < 5
            ? t("At least five shared specimens are needed before fields of different schemas can be correlated.")
            : t("No correlation with |r| ≥ 0.6 between fields of different schemas.")}</p>
          : <ul className="knowledge-list knowledge-compact">
            {stats.cross_schema.correlations.map(item => <li key={`${item.a}:${item.b}`} className="knowledge-static-row">
              <span>{item.a} ↔ {item.b}</span><small>r = {num(item.r, 2)} · n = {item.n}</small></li>)}
          </ul>}
      </section>}
      {statBlocks.map(block => {
        const numericFields = block.statistics.fields.filter(item => item.kind === "numeric");
        const categoricalFields = block.statistics.fields.filter(item => item.kind === "categorical");
        const outliers = numericFields.flatMap(item => (item.outliers ?? []).map(outlier => ({ ...outlier, field: item.field })));
        return <section key={block.schema_id} className="knowledge-stats-block">
          {statBlocks.length > 1 && <h4>{block.schema_name} <span className="knowledge-total">{block.statistics.records}</span></h4>}
          {block.statistics.truncated && <p className="knowledge-meta">{t("Only the first 500 records are counted.")}</p>}
          {!!numericFields.length && <div className="knowledge-record-grid knowledge-stats"><table>
            <thead><tr><th>{t("Field")}</th><th>{t("n")}</th><th>{t("Mean")}</th><th>{t("Std")}</th><th>{t("Min")}</th>
              <th>{t("Median")}</th><th>{t("Max")}</th><th>{t("Outliers")}</th></tr></thead>
            <tbody>{numericFields.map(item => <tr key={item.field}>
              <th scope="row">{item.field}</th><td>{item.count}</td><td>{num(item.mean)}</td><td>{num(item.std)}</td>
              <td>{num(item.min)}</td><td>{num(item.median)}</td><td>{num(item.max)}</td><td>{item.outliers?.length ?? 0}</td>
            </tr>)}</tbody>
          </table></div>}
          {!!outliers.length && <><h5>{t("Outliers (|z| > 2.5)")}</h5><ul className="knowledge-list knowledge-compact">
            {outliers.map(item => <li key={`${item.field}:${item.record_id}`}>
              <button type="button" disabled={busy} onClick={() => onOpenRecord(item.record_id)}>
                <span>{item.name ?? shortId(item.record_id)} · {item.field} = {num(item.value)}</span><small>z = {num(item.z, 2)}</small></button>
            </li>)}
          </ul></>}
          {!!block.statistics.correlations.length && <><h5>{t("Correlations (|r| ≥ 0.6)")}</h5><ul className="knowledge-list knowledge-compact">
            {block.statistics.correlations.map(item => <li key={`${item.a}:${item.b}`} className="knowledge-static-row">
              <span>{item.a} ↔ {item.b}</span><small>r = {num(item.r, 2)} · n = {item.n}</small></li>)}
          </ul></>}
          {!!categoricalFields.length && <><h5>{t("Categories")}</h5><ul className="knowledge-list knowledge-compact">
            {categoricalFields.map(item => <li key={item.field} className="knowledge-static-row">
              <span>{item.field}</span><small>{Object.entries(item.counts ?? {}).map(([value, count]) => `${value} ×${count}`).join(" · ")}</small></li>)}
          </ul></>}
        </section>;
      })}
    </details>}

    {!!insights.length && <section ref={draftPanel} className="knowledge-insight-block knowledge-result-review">
      <div className="knowledge-result-heading"><h4><CheckCircle2 size={16} />{t("Draft insights")} {!!drafts.length && <span className="knowledge-total">{drafts.length}</span>}</h4></div>
      {!drafts.length && <p className="knowledge-empty">{insights.length ? t("No drafts for this filter.") : t("Nothing proposed yet. Extract insights above.")}</p>}
      {drafts.length > 1 && (pending && pending.ids.length > 1
        ? <div role="alert" className="knowledge-confirm">
          <strong>{t("Publish {v0} insights?", { v0: pending.ids.length })}</strong>
          <p>{pending.reasons.join(" ")}</p>
          <div className="knowledge-formbar">
            <button type="button" className="knowledge-primary" disabled={busy} onClick={() => void publish(pending.ids, pending)}>{t("Confirm and publish all")}</button>
            <button type="button" disabled={busy} onClick={() => setPending(undefined)}>{t("Cancel")}</button>
          </div>
        </div>
        : <div className="knowledge-formbar">
          <button type="button" disabled={busy || drafts.some(item => item.evidence.some(link => link.stale))}
            onClick={() => void publish(drafts.map(item => item.id))}>{t("Publish all {v0}", { v0: drafts.length })}</button>
        </div>)}
      <ul className="knowledge-list knowledge-insights">
        {drafts.map(item => <li key={item.id} className="knowledge-insight knowledge-result-draft">
          {editingId === item.id && draft ? <div className="knowledge-record-form">
            <label className="knowledge-record-field">{t("Kind")}
              <select value={draft.kind} disabled={busy} onChange={event => setDraft({ ...draft, kind: event.target.value as InsightKind })}>
                {KINDS.map(kind => <option key={kind} value={kind}>{kindLabel(kind)}</option>)}</select></label>
            <label className="knowledge-record-field">{t("Title")}<input value={draft.title} disabled={busy} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
            <label className="knowledge-record-field">{t("Statement")}<textarea rows={3} value={draft.statement} disabled={busy} onChange={event => setDraft({ ...draft, statement: event.target.value })} /></label>
            <label className="knowledge-record-field">{t("Note")}<input value={draft.note} disabled={busy} placeholder={t("What was unclear…")} onChange={event => setDraft({ ...draft, note: event.target.value })} /></label>
            <p className="knowledge-meta">{t("You can also ask the Lab assistant to change this insight in the conversation — refer to it as \"{v0}\".", { v0: item.title })}</p>
            <div className="knowledge-formbar">
              <button type="button" className="knowledge-primary" disabled={busy} onClick={() => void saveEdit(item)}>{t("Save changes")}</button>
              <button type="button" disabled={busy} onClick={() => { setEditingId(""); setDraft(undefined); }}>{t("Cancel")}</button>
            </div>
          </div> : <>
            <div className="knowledge-insight-head">{kindBadge(item.kind)}<strong>{item.title}</strong>
              {item.schema_ids.length > 1 && <span className="knowledge-chip-schema">{t("across {v0} schemas", { v0: item.schema_ids.length })}</span>}
              {!currentSchema && item.schema_ids.length === 1 && <span className="knowledge-chip-schema">{schemas.find(schema => schema.id === item.schema_ids[0])?.name ?? shortId(item.schema_ids[0])}</span>}
              {item.confidence !== null && <small>{t("confidence {v0}", { v0: num(item.confidence, 2) })}</small>}</div>
            <p>{item.statement}</p>
            <p className="knowledge-meta">
              {!!item.fields.length && <>{item.fields.join(", ")} · </>}
              {t("revision {v0} · by {v1}", { v0: item.revision, v1: item.created_by ?? "—" })}{item.note ? ` · ${item.note}` : ""}
            </p>
            {evidenceChips(item)}
            {pending?.ids.length === 1 && pending.ids[0] === item.id ? <div role="alert" className="knowledge-confirm">
              <strong>{t("Publish this insight?")}</strong>
              <p>{pending.reasons.join(" ")}</p>
              <div className="knowledge-formbar">
                <button type="button" className="knowledge-primary" disabled={busy} onClick={() => void publish([item.id], pending)}>{t("Confirm and publish")}</button>
                <button type="button" disabled={busy} onClick={() => setPending(undefined)}>{t("Cancel")}</button>
              </div>
            </div> : <div className="knowledge-formbar">
              <button type="button" disabled={busy} onClick={() => beginEdit(item)}><Pencil size={13} />{t("Edit")}</button>
              <button type="button" className="knowledge-primary" disabled={busy || item.evidence.some(link => link.stale)}
                title={item.evidence.some(link => link.stale) ? t("A cited record is no longer published") : undefined}
                onClick={() => void publish([item.id])}>{t("Publish")}</button>
              <button type="button" className="knowledge-danger" disabled={busy} onClick={() => void remove(item)}>{t("Delete")}</button>
            </div>}
          </>}
        </li>)}
      </ul>
    </section>}

    {(!!insights.length || !!graph?.entities.length) && <section ref={publishedPanel} className="knowledge-insight-block knowledge-result-published">
      <div className="knowledge-result-heading"><h4><Network size={16} />{t("Published insights")} {!!publishedInsights.length && <span className="knowledge-total">{publishedInsights.length}</span>}</h4></div>
      {!graph?.entities.length
        ? <WorkspaceEmptyState className="knowledge-graph-empty" icon={<Network size={28} />} title={t("Nothing published yet")}
          description={t("Review a draft and publish it to connect its evidence here.")} />
        : <>
          <p className="knowledge-meta">{t("{v0} nodes · {v1} edges{v2}", {
            v0: graph.entities.length, v1: graph.relations.length, v2: graph.truncated ? t(" · result limited") : "" })}</p>
          <div className="knowledge-graph">
            <GraphMap graphKey={`insights:${cardId}`} entities={graph.entities} relations={graph.relations} selected={entity}
              onSelect={setEntity} onExpand={expand} />
            <div className="knowledge-graphside">
              <ul className="knowledge-list knowledge-insights">
                {publishedInsights.map(item => {
                  const nodeId = graph.entities.find(node => node.properties.insight_id === item.id)?.id ?? "";
                  return <li key={item.id}>
                    <button type="button" aria-pressed={!!nodeId && entity === nodeId} disabled={busy} onClick={() => nodeId && setEntity(nodeId)}>
                      <span>{kindBadge(item.kind)} {item.title}</span><small>{item.evidence.length} {t("records")}</small></button>
                  </li>;
                })}
              </ul>
              {selectedEntity && <div className="knowledge-detail">
                <h4>{selectedEntity.name}</h4>
                <p className="knowledge-meta">{selectedEntity.type}</p>
                {selectedInsight && <>
                  <p>{selectedInsight.statement}</p>
                  {evidenceChips(selectedInsight)}
                  <div className="knowledge-formbar">
                    <button type="button" disabled={busy} onClick={() => expand(selectedEntity.id)}>{t("Show neighbours")}</button>
                    <button type="button" disabled={busy} onClick={() => void unpublish(selectedInsight)}>{t("Unpublish")}</button>
                  </div>
                </>}
                {!selectedInsight && selectedEntity.type === "experiment_record" && typeof selectedEntity.properties.record_id === "string" &&
                  <div className="knowledge-formbar">
                    <button type="button" disabled={busy} onClick={() => expand(selectedEntity.id)}>{t("Show neighbours")}</button>
                    <button type="button" disabled={busy} onClick={() => onOpenRecord(selectedEntity.properties.record_id as string)}>{t("Open record")}</button>
                  </div>}
                {!selectedInsight && selectedEntity.type !== "experiment_record" &&
                  <button type="button" disabled={busy} onClick={() => expand(selectedEntity.id)}>{t("Show neighbours")}</button>}
                <ul className="knowledge-relations">
                  {graph.relations.filter(relation => relation.source_id === selectedEntity.id || relation.target_id === selectedEntity.id).map(relation => {
                    const name = (id: string) => graph.entities.find(item => item.id === id)?.name ?? shortId(id);
                    return <li key={relation.id}>{name(relation.source_id)} →{relation.type.replaceAll("_", " ")}→ {name(relation.target_id)}</li>;
                  })}
                </ul>
              </div>}
            </div>
          </div>
        </>}
    </section>}
  </>;
}
