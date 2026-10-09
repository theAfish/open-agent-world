import { t, useLocale, NetworkMap, type NetworkData, type NetworkMapHandle } from "@oaw/plugin-api";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";
import "./workspace.css";
import { TaskBoard, TaskPreview } from "./TaskBoard";

type Knowledge = { id: string; title: string; type: string; summary: string; content?: string; tags?: string[]; aliases?: string[]; trust: number; verification: string; refinement: string; owner?: string; provenance?: object; usage_count?: number };
type Result = { nodes: Knowledge[]; edges: { id: string; source: string; target: string; relation: string }[]; next_offset: number | null; total: number; statistics: Record<string, number> };
type Detail = { entry: Knowledge; resources: { skill_node_id: string; path: string; files: string[] }[]; relationships: Result["edges"] };
const kinds = ["capability", "procedure", "heuristic", "memory"];
const relations = ["dependency", "prerequisite", "refinement_of", "related_workflow", "derived_from", "heuristic_for", "related_memory", "replacement"];

function Workspace(props: PluginViewProps) {
  useLocale(); return <GraphWorkspace {...props} />; }
function GraphWorkspace({ card, host }: PluginViewProps) {
  useLocale();
  const [nodes, setNodes] = useState<Knowledge[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const map = useRef<NetworkMapHandle>(null);
  const [edges, setEdges] = useState<Result["edges"]>([]);
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [relation, setRelation] = useState("");
  const [source, setSource] = useState("");
  const [memory, setMemory] = useState(true);
  const [trusted, setTrusted] = useState(false);
  const [mode, setMode] = useState("browse");
  const [editing, setEditing] = useState(false);
  const [savedDetail, setSavedDetail] = useState<Detail | null>(null);
  const [detailRevision, setDetailRevision] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [resourcePreview, setResourcePreview] = useState<{ path: string; content: string } | null>(null);
  const [navigation, setNavigation] = useState(false);
  const [inspector, setInspector] = useState(false);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [toolsets, setToolsets] = useState<{ id: string; name: string }[]>([]);
  const [toolset, setToolset] = useState("");
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [connectTarget, setConnectTarget] = useState("");
  const [connectRelation, setConnectRelation] = useState("related_workflow");
  const [evidence, setEvidence] = useState("");
  const [notice, setNotice] = useState("");
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const sequence = useRef(0);
  const inspectSequence = useRef(0);
  const workspaceRef = useRef<HTMLElement>(null);
  const run = async (work: () => Promise<void>) => { setBusy(true); setError(""); try { await work(); } catch (e) { setError(String(e)); } finally { setBusy(false); } };
  const load = useCallback(async (ids: string[] = [], append = false, offset = 0) => {
    const request = ++sequence.current;
    const response = await host.documentAction(ids.length ? "expand" : "search", { query, ids, types: type ? [type] : [], relations: relation ? [relation] : [], source, include_memory: memory, min_trust: trusted ? 0.7 : 0, review: mode === "review", limit: 100, offset });
    if (request !== sequence.current) return;
    const data = response.value as Result;
    setRevision(response.revision); setResult(data);
    setNodes(old => append ? [...new Map([...old, ...data.nodes].map(n => [n.id, n])).values()] : data.nodes);
    setEdges(old => append ? [...new Map([...old, ...data.edges].map(e => [e.id, e])).values()] : data.edges);
  }, [host, query, type, relation, source, memory, trusted, mode]);
  useEffect(() => { void run(() => load()); }, [card, type, relation, memory, trusted, mode]);
  useEffect(() => () => { sequence.current++; inspectSequence.current++; }, [card.id]);
  const select = async (id: string) => {
    const request = ++inspectSequence.current;
    if (!nodes.some(node => node.id === id)) await load([id], true);
    if (request !== inspectSequence.current) return;
    setSelectedIds([id]);
    setEditing(false); setConfirmDelete(false); setResourcePreview(null); setInspector(true); setDetail(null);
    const response = await host.documentAction("inspect", { entry_id: id });
    if (request !== inspectSequence.current) return;
    setDetail(response.value as Detail); setSavedDetail(response.value as Detail); setDetailRevision(response.revision); setRevision(response.revision);

  };
  const mutate = async (operation: string, args: Record<string, unknown>) => {
    const response = await host.documentAction(operation, args, detailRevision);
    setEditing(false); setConfirmDelete(false); setRevision(response.revision);
    await load();
    const created = !args.entry_id && operation === 'edit' ? (response.value as { entries?: Knowledge[] }).entries?.at(-1)?.id : undefined;
    const id = args.entry_id ?? created ?? detail?.entry.id;
    if (operation === 'delete_entry') { setDetail(null); setSavedDetail(null); setInspector(false); setSelectedIds([]); }
    else if (id) await select(String(id));
    else { setDetail(null); setSavedDetail(null); }
  };
  const focus = () => map.current?.focus(selectedIds);
  const arrange = () => map.current?.arrange();
  const network = useMemo<NetworkData>(() => ({ nodes: nodes.map(n => ({ id: n.id, label: n.title, kind: n.type, tags: n.tags })),
    edges: edges.map(e => ({ id: e.id, source: e.source, target: e.target, label: e.relation.replaceAll('_', ' ') })) }), [nodes, edges]);
  const inspectEntry = detail?.entry;
  const newEntry = () => {
    inspectSequence.current += 1;
    setInspector(true); setEditing(true); setConfirmDelete(false); setSavedDetail(null); setDetailRevision(revision);
    setDetail({ entry: { id: "", title: t("New knowledge"), type: "capability", summary: "", content: "", owner: "user", trust: 0.5, verification: "unverified", refinement: "pending" }, resources: [], relationships: [] });
  };
  const selected = nodes.filter(node => selectedIds.includes(node.id));
  return <section ref={workspaceRef} className="kdg-workspace nodrag nowheel" aria-label={t("Know-Do Graph workspace")} onPointerDown={e => e.stopPropagation()} onClick={e => e.stopPropagation()}>
    <header className="kdg-toolbar">
      <button onClick={() => setNavigation(!navigation)} aria-label={t("Toggle navigator")} aria-expanded={navigation}>{t("Browse")}</button>
      <form onSubmit={e => { e.preventDefault(); void run(() => load()); }}><input aria-label={t("Search knowledge")} value={query} onChange={e => setQuery(e.target.value)} placeholder={t("Search knowledge…")} /><button disabled={busy}>{t("Search")}</button></form>
      <span className="kdg-count">{nodes.length} {t("entries")}</span>
      <button onClick={arrange} disabled={!nodes.length || busy} title={t("Group connected entries and fit the graph")}>{t("Arrange")}</button>
      <button onClick={newEntry} disabled={busy}>{t("New entry")}</button>
    </header>
    {selected.length > 0 && <div className="kdg-selection-actions" aria-label={t("Selection actions")}><span>{selected.length} {t("selected")}</span><button onClick={focus}>{t("Focus selection")}</button>
      <button onClick={() => { const ids = new Set(selectedIds); if (ids.size) { setNodes(ns => ns.filter(n => ids.has(n.id))); setEdges(es => es.filter(e => ids.has(e.source) && ids.has(e.target))); } }}>{t("Isolate")}</button>
      <button onClick={() => setInspector(!inspector)} aria-label={t("Toggle inspector")}>{t("Inspector")}</button>
    </div>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div className="kdg-regions">
      {navigation && <aside className="kdg-navigator">
        <label>{t("Mode")}<select aria-label={t("Workspace mode")} value={mode} onChange={e => { setMode(e.target.value); setDetail(null); setEditing(false); }}><option value="browse">{t("Browse")}</option><option value="review">{t("Review memories")}</option></select></label>
        <label>{t("Knowledge type")}<select aria-label={t("Knowledge type")} value={type} onChange={e => setType(e.target.value)}><option value="">{t("All types")}</option>{kinds.map(k => <option key={k}>{k}</option>)}</select></label>
        <label>{t("Relationship")}<select value={relation} onChange={e => setRelation(e.target.value)}><option value="">{t("All relations")}</option>{relations.map(k => <option key={k}>{k}</option>)}</select></label>
        <label>{t("Source filter")}<input value={source} onChange={e => setSource(e.target.value)} onBlur={() => void run(() => load())} /></label>
        <label><input type="checkbox" checked={memory} onChange={e => setMemory(e.target.checked)} />{t("Show memories")}</label>
        <label><input type="checkbox" checked={trusted} onChange={e => setTrusted(e.target.checked)} />{t("Trust ≥ 0.7")}</label>
        <p>{result?.total ?? 0} {t("matches ·")} {nodes.length} {t("visible")}</p>
        <a href={host.documentDownloadUrl("snapshots")}>{t("Download source snapshots")}</a>
        {result?.next_offset != null && <button onClick={() => void run(() => load([], true, result.next_offset!))}>{t("Load more")}</button>}
        <details><summary>{t("Assimilate a Toolset")}</summary><p>{t("Consume its world card after committing knowledge and a recoverable package snapshot.")}</p>
          <button onClick={() => void run(async () => { setToolsets(await host.listCards(["oaw.skill-package"])); })}>{t("Choose Toolset")}</button>
          <select aria-label={t("Toolset to assimilate")} value={toolset} onChange={e => { setToolset(e.target.value); setPreview(null); }}><option value="">{t("Choose…")}</option>{toolsets.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
          <button disabled={!toolset || busy} onClick={() => void run(async () => { const original = await host.readDocument(toolset); const current = await host.documentAction("statistics", {}); setPreview(await host.transform("assimilate", { source_id: toolset, source_revision: original.revision, expected_revision: current.revision })); })}>{t("Preview assimilation")}</button>
          {preview && <div role="dialog" aria-label={t("Confirm assimilation")}><p>{t("Assimilate “")}{String(preview.source_name)}{t("” and consume its card?")}</p><button disabled={busy} onClick={() => void run(async () => { await host.transform("assimilate", { source_id: toolset, source_revision: preview.source_revision, expected_revision: preview.revision, confirm: true }); setPreview(null); setToolset(""); setNotice(t("Toolset assimilated. Source package preserved in snapshots.")); await load(); })}>{t("Confirm assimilation")}</button><button onClick={() => setPreview(null)}>{t("Cancel")}</button></div>}
        </details>
      </aside>}
      <main className="kdg-canvas"><NetworkMap ref={map} graphKey={`matcreator:${card.id}`} data={network} selected={selectedIds} search={false}
        onSelectionChange={setSelectedIds} onSelect={id => void run(() => select(id))}
        onExpand={id => void run(() => load([id], true))}
        onContextMenu={(id, point) => { const element = workspaceRef.current!; const rect = element.getBoundingClientRect();
          setMenu({ id, x: (point.x - rect.left) * element.clientWidth / rect.width, y: (point.y - rect.top) * element.clientHeight / rect.height }); void run(() => select(id)); }}
        label={t("Know-Do Graph map")} /></main>
      {inspector && <aside className="kdg-inspector" aria-label={t("Knowledge details")}><button className="kdg-inspector-close" aria-label={t("Close inspector")} onClick={() => { inspectSequence.current += 1; setInspector(false); setEditing(false); setConfirmDelete(false); }}>{t("Close")}</button>{inspectEntry ? <>
        <h3>{inspectEntry.title}</h3><p>{inspectEntry.type} · {inspectEntry.verification} {t("· trust")} {inspectEntry.trust}</p>
        {inspectEntry.id && <div className="kdg-entry-actions"><button disabled={busy} onClick={() => void run(() => load([inspectEntry.id], true))}>{t("Expand neighborhood")}</button>
          {!editing && <button disabled={busy} onClick={() => { setEditing(true); setConfirmDelete(false); }}>{t("Edit entry")}</button>}
          {!editing && <button className="kdg-delete" disabled={busy} onClick={() => setConfirmDelete(true)}>{t("Delete entry")}</button>}
        </div>}
        {confirmDelete && <div className="kdg-delete-confirm" role="dialog" aria-label={t("Delete knowledge entry")}><p>{t("Delete “")}{inspectEntry.title}{t("” and its graph relationships? Source snapshots and Skill resources are kept.")}</p><button className="kdg-delete" disabled={busy} onClick={() => void run(() => mutate('delete_entry', { entry_id: inspectEntry.id }))}>{t("Confirm delete")}</button><button disabled={busy} onClick={() => setConfirmDelete(false)}>{t("Cancel")}</button></div>}
        <p>{inspectEntry.summary}</p>
        {!editing && <div className="kdg-content">{inspectEntry.content}</div>}
        {editing && <form onSubmit={e => { e.preventDefault(); void run(() => mutate("edit", { entry_id: inspectEntry.id || undefined, title: inspectEntry.title, type: inspectEntry.type, content: inspectEntry.content, summary: inspectEntry.summary, tags: inspectEntry.tags, aliases: inspectEntry.aliases, trust: inspectEntry.trust, verification: inspectEntry.verification })); }}><label>{t("Title")}<input required maxLength={200} value={inspectEntry.title} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, title: e.target.value } })} /></label>
          <label>{t("Entry type")}<select value={inspectEntry.type} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, type: e.target.value } })}>{kinds.map(kind => <option key={kind}>{kind}</option>)}</select></label>
          <label>{t("Summary")}<textarea aria-label={t("Summary")} value={inspectEntry.summary} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, summary: e.target.value } })} /></label>
          <label>{t("Tags")}<input value={inspectEntry.tags?.join(", ") ?? ""} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, tags: e.target.value.split(",").map(s => s.trim()) } })} /></label>
          <label>{t("Aliases")}<input value={inspectEntry.aliases?.join(", ") ?? ""} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, aliases: e.target.value.split(",").map(s => s.trim()) } })} /></label>
          <label>{t("Trust")}<input type="number" min="0" max="1" step="0.1" value={inspectEntry.trust} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, trust: Number(e.target.value) } })} /></label>
          <label>{t("Verification")}<select value={inspectEntry.verification} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, verification: e.target.value } })}>{["unverified", "reviewed", "tested"].map(status => <option key={status}>{status}</option>)}</select></label>
          <label>{t("Content")}<textarea aria-label={t("Content")} value={inspectEntry.content} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, content: e.target.value } })} /></label>
          <div className="kdg-entry-actions"><button type="submit" disabled={busy}>{t("Save changes")}</button><button type="button" disabled={busy} onClick={() => { setDetail(savedDetail); setEditing(false); if (!savedDetail) setInspector(false); }}>{t("Cancel editing")}</button></div>
          </form>}
          {!editing && inspectEntry.id && <details><summary>{t("Edit relationships")}</summary>
          <label>{t("Connect to")}<select value={connectTarget} onChange={e => setConnectTarget(e.target.value)}><option value="">{t("Choose entry…")}</option>{nodes.filter(n => n.id !== inspectEntry.id).map(n => <option key={n.id} value={n.id}>{n.title}</option>)}</select></label>
          <select aria-label={t("New relationship type")} value={connectRelation} onChange={e => setConnectRelation(e.target.value)}>{relations.map(r => <option key={r}>{r}</option>)}</select>
          <button disabled={!connectTarget || busy} onClick={() => void run(() => mutate("connect", { source: inspectEntry.id, target: connectTarget, relation: connectRelation }))}>{t("Commit relationship")}</button></details>}
        {mode === "review" && !editing && <><label>{t("Reviewed knowledge")}<textarea value={inspectEntry.content} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, content: e.target.value } })} /></label><label>{t("Evidence")}<input value={evidence} onChange={e => setEvidence(e.target.value)} /></label><button disabled={!evidence || busy} onClick={() => void run(() => mutate("distill", { memory_ids: [inspectEntry.id], title: inspectEntry.title, content: inspectEntry.content, evidence }))}>{t("Distill to Heuristic")}</button></>}
        <h4>{t("Resources")}</h4>{detail.resources.map(resource => <details key={`${resource.skill_node_id}:${resource.path}`}><summary>{resource.path}</summary>{resource.files.map(path => <button disabled={busy} key={path} onClick={() => void run(async () => { const response = await host.documentAction("inspect", { entry_id: inspectEntry.id, resource_path: path }); const value = response.value as { path: string; content: unknown }; setResourcePreview({ path: value.path, content: typeof value.content === 'string' ? value.content : JSON.stringify(value.content, null, 2) }); })}>{path}</button>)}</details>)}
        {resourcePreview && <section aria-label={t("Resource preview")}><h4>{resourcePreview.path}</h4><button onClick={() => setResourcePreview(null)}>{t("Close resource")}</button><pre>{resourcePreview.content}</pre></section>}
        <h4>{t("Relationships")}</h4>{detail.relationships.map(edge => <div key={edge.id}><button disabled={busy} onClick={() => void run(() => select(edge.source === inspectEntry.id ? edge.target : edge.source))}>{edge.relation}</button>{!editing && <button className="kdg-delete" disabled={busy} onClick={() => { if (window.confirm(t("Delete {v0} relationship?", { v0: String(edge.relation) }))) void run(() => mutate("delete_relationship", { edge_id: edge.id })); }}>{t("Delete relationship")}</button>}</div>)}
        <details><summary>{t("Provenance and usage")}</summary><p>{t("Uses:")} {inspectEntry.usage_count ?? 0} · {inspectEntry.refinement}</p><pre>{JSON.stringify(inspectEntry.provenance, null, 2)}</pre></details>
      </> : <p>{t("Select an entry to inspect it. Double-click to expand its neighborhood.")}</p>}</aside>}
    </div>
    {menu && <div role="menu" style={{ position: "absolute", left: Math.max(0, Math.min(menu.x, (workspaceRef.current?.clientWidth ?? 400) - 190)), top: Math.max(0, Math.min(menu.y, (workspaceRef.current?.clientHeight ?? 400) - 90)), zIndex: 200, background: "var(--surface-solid)", padding: 8 }}><button role="menuitem" onClick={() => { void run(() => load([menu.id], true)); setMenu(null); }}>{t("Expand neighborhood")}</button><button role="menuitem" onClick={() => { map.current?.focus([menu.id]); setMenu(null); }}>{t("Focus")}</button></div>}
  </section>;
}
export default { apiVersion: 1, views: { workspace: Workspace, tasks: TaskBoard, 'tasks-preview': TaskPreview } } satisfies FrontendPlugin;
