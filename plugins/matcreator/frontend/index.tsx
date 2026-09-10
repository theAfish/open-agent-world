import { useCallback, useEffect, useRef, useState } from "react";
import { ReactFlow, ReactFlowProvider, Background, Controls, useNodesState, useEdgesState, useReactFlow, type Node, type Edge } from "@xyflow/react";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";
import { useNestedFlowGestures } from "@oaw/plugin-api";
import "./workspace.css";
import { mapEdgeTypes } from "./MapEdge";
import { graphLayout } from "./graphLayout";

type Knowledge = { id: string; title: string; type: string; summary: string; content?: string; tags?: string[]; aliases?: string[]; trust: number; verification: string; refinement: string; owner?: string; provenance?: object; usage_count?: number };
type Result = { nodes: Knowledge[]; edges: { id: string; source: string; target: string; relation: string }[]; next_offset: number | null; total: number; statistics: Record<string, number> };
type Detail = { entry: Knowledge; resources: { skill_node_id: string; path: string; files: string[] }[]; relationships: Result["edges"] };
const kinds = ["capability", "procedure", "heuristic", "memory"];
const relations = ["dependency", "prerequisite", "refinement_of", "related_workflow", "derived_from", "heuristic_for", "related_memory", "replacement"];
const layouts = new Map<string, Map<string, { x: number; y: number }>>();

function Workspace(props: PluginViewProps) { return <ReactFlowProvider><GraphWorkspace {...props} /></ReactFlowProvider>; }
function GraphWorkspace({ card, host }: PluginViewProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
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
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null);
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
  const canvasRef = useRef<HTMLElement>(null);
  const flow = useReactFlow();
  const positions = layouts.get(card.id) ?? new Map<string, { x: number; y: number }>();
  layouts.set(card.id, positions);
  const selectionBox = useNestedFlowGestures(canvasRef, dragged => dragged.forEach(node => positions.set(node.id, node.position)));
  const run = async (work: () => Promise<void>) => { setBusy(true); setError(""); try { await work(); } catch (e) { setError(String(e)); } finally { setBusy(false); } };
  const load = useCallback(async (ids: string[] = [], append = false, offset = 0) => {
    const request = ++sequence.current;
    const response = await host.documentAction(ids.length ? "expand" : "search", { query, ids, types: type ? [type] : [], relations: relation ? [relation] : [], source, include_memory: memory, min_trust: trusted ? 0.7 : 0, review: mode === "review", limit: 100, offset });
    if (request !== sequence.current) return;
    const data = response.value as Result;
    setRevision(response.revision); setResult(data);
    const layoutIds = [...new Set([...(append ? flow.getNodes().map(node => node.id) : []), ...data.nodes.map(entry => entry.id)])];
    const topology = append ? [...flow.getEdges(), ...data.edges] : data.edges;
    graphLayout(layoutIds, topology, positions).forEach((position, id) => positions.set(id, position));
    const next = data.nodes.map(entry => ({ id: entry.id, position: positions.get(entry.id)!,
      data: { title: entry.title, label: <><span className="kdg-node-kind">{entry.type}</span><span>{entry.title}</span></> },
      ariaLabel: `${entry.type}: ${entry.title}`, className: `kdg-node kdg-${entry.type}`, style: { width: 110, height: 110 }, connectable: false }));
    setNodes(old => { const selected = new Set(old.filter(n => n.selected).map(n => n.id)); const updated = next.map(n => ({ ...n, selected: selected.has(n.id) })); return append ? [...old.filter(n => !next.some(item => item.id === n.id)), ...updated] : updated; });
    const links = data.edges.map(edge => ({ ...edge, data: { relation: edge.relation.replaceAll("_", " ") }, type: "knowledge" }));
    setEdges(old => append ? [...old.filter(e => !links.some(item => item.id === e.id)), ...links] : links);
    if (!append) requestAnimationFrame(() => requestAnimationFrame(() => void flow.fitView({ padding: 0.2, maxZoom: 1, duration: 150 })));
  }, [host, query, type, relation, source, memory, trusted, mode, setNodes, setEdges, positions]);
  useEffect(() => { void run(() => load()); }, [card, type, relation, memory, trusted, mode]);
  const select = async (id: string) => {
    const request = ++inspectSequence.current;
    setEditing(false); setConfirmDelete(false); setResourcePreview(null); setInspector(true); setDetail(null);
    const response = await host.documentAction("inspect", { entry_id: id });
    if (request !== inspectSequence.current) return;
    setDetail(response.value as Detail); setSavedDetail(response.value as Detail); setDetailRevision(response.revision); setRevision(response.revision);
    setNodes(current => current.map(node => ({ ...node, selected: node.id === id })));
  };
  const mutate = async (operation: string, args: Record<string, unknown>) => {
    const response = await host.documentAction(operation, args, detailRevision);
    setEditing(false); setConfirmDelete(false); setRevision(response.revision);
    await load();
    const created = !args.entry_id && operation === 'edit' ? (response.value as { entries?: Knowledge[] }).entries?.at(-1)?.id : undefined;
    const id = args.entry_id ?? created ?? detail?.entry.id;
    if (operation === 'delete_entry') { setDetail(null); setSavedDetail(null); setInspector(false); positions.delete(String(id)); }
    else if (id) await select(String(id));
    else { setDetail(null); setSavedDetail(null); }
  };
  const focus = () => { const selected = nodes.filter(n => n.selected); if (selected.length) void flow.fitView({ nodes: selected, padding: 0.5, duration: 250 }); };
  const arrange = () => {
    const layout = graphLayout(nodes.map(node => node.id), edges);
    layout.forEach((position, id) => positions.set(id, position));
    setNodes(current => current.map(node => ({ ...node, position: layout.get(node.id)! })));
    requestAnimationFrame(() => void flow.fitView({ padding: 0.2, maxZoom: 1, duration: 200 }));
  };
  const inspectEntry = detail?.entry;
  const newEntry = () => {
    inspectSequence.current += 1;
    setInspector(true); setEditing(true); setConfirmDelete(false); setSavedDetail(null); setDetailRevision(revision);
    setDetail({ entry: { id: "", title: "New knowledge", type: "capability", summary: "", content: "", owner: "user", trust: 0.5, verification: "unverified", refinement: "pending" }, resources: [], relationships: [] });
  };
  const selected = nodes.filter(node => node.selected);
  const active = new Set(hoveredNode ? [hoveredNode] : selected.map(node => node.id));
  const neighbors = new Set(active);
  edges.forEach(edge => { if (active.has(edge.source) || active.has(edge.target)) { neighbors.add(edge.source); neighbors.add(edge.target); } });
  const visibleNodes = nodes.map(node => ({ ...node, className: `${node.className} ${active.size ? neighbors.has(node.id) ? 'is-related' : 'is-muted' : ''}` }));
  const visibleEdges = edges.map(edge => ({ ...edge, className: active.size ? active.has(edge.source) || active.has(edge.target) ? 'is-related' : 'is-muted' : '', data: { ...edge.data, reveal: edge.id === hoveredEdge }, style: { stroke: edge.selected || edge.id === hoveredEdge || active.has(edge.source) || active.has(edge.target) ? 'var(--map-accent)' : 'var(--edge)', strokeWidth: edge.selected || active.has(edge.source) || active.has(edge.target) ? 2 : 1.2, opacity: active.size && !active.has(edge.source) && !active.has(edge.target) ? 0.08 : edge.selected || edge.id === hoveredEdge || active.size ? 0.85 : 0.28 } }));
  return <section ref={workspaceRef} className="kdg-workspace nodrag nowheel" aria-label="Know-Do Graph workspace" onPointerDown={e => e.stopPropagation()} onClick={e => e.stopPropagation()}>
    <header className="kdg-toolbar">
      <button onClick={() => setNavigation(!navigation)} aria-label="Toggle navigator" aria-expanded={navigation}>Browse</button>
      <form onSubmit={e => { e.preventDefault(); void run(() => load()); }}><input aria-label="Search knowledge" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search knowledge…" /><button disabled={busy}>Search</button></form>
      <span className="kdg-count">{nodes.length} entries</span>
      <button onClick={arrange} disabled={!nodes.length || busy} title="Group connected entries and fit the graph">Arrange</button>
      <button onClick={newEntry} disabled={busy}>New entry</button>
    </header>
    <div className="kdg-map-guide"><div className="kdg-legend" aria-label="Knowledge type legend">{kinds.map(kind => <span className={`kdg-${kind}`} key={kind}><i />{kind}</span>)}</div><span>Drag to pan · Shift-drag to select</span></div>
    {selected.length > 0 && <div className="kdg-selection-actions" aria-label="Selection actions"><span>{selected.length} selected</span><button onClick={focus}>Focus selection</button>
      <button onClick={() => { const ids = new Set(nodes.filter(n => n.selected).map(n => n.id)); if (ids.size) { setNodes(ns => ns.filter(n => ids.has(n.id))); setEdges(es => es.filter(e => ids.has(e.source) && ids.has(e.target))); } }}>Isolate</button>
      <button onClick={() => setInspector(!inspector)} aria-label="Toggle inspector">Inspector</button>
    </div>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div className="kdg-regions">
      {navigation && <aside className="kdg-navigator">
        <label>Mode<select aria-label="Workspace mode" value={mode} onChange={e => { setMode(e.target.value); setDetail(null); setEditing(false); }}><option value="browse">Browse</option><option value="review">Review memories</option></select></label>
        <label>Knowledge type<select aria-label="Knowledge type" value={type} onChange={e => setType(e.target.value)}><option value="">All types</option>{kinds.map(k => <option key={k}>{k}</option>)}</select></label>
        <label>Relationship<select value={relation} onChange={e => setRelation(e.target.value)}><option value="">All relations</option>{relations.map(k => <option key={k}>{k}</option>)}</select></label>
        <label>Source filter<input value={source} onChange={e => setSource(e.target.value)} onBlur={() => void run(() => load())} /></label>
        <label><input type="checkbox" checked={memory} onChange={e => setMemory(e.target.checked)} />Show memories</label>
        <label><input type="checkbox" checked={trusted} onChange={e => setTrusted(e.target.checked)} />Trust ≥ 0.7</label>
        <p>{result?.total ?? 0} matches · {nodes.length} visible</p>
        <a href={host.documentDownloadUrl("snapshots")}>Download source snapshots</a>
        {result?.next_offset != null && <button onClick={() => void run(() => load([], true, result.next_offset!))}>Load more</button>}
        <details><summary>Assimilate a Toolset</summary><p>Consume its world card after committing knowledge and a recoverable package snapshot.</p>
          <button onClick={() => void run(async () => { setToolsets(await host.listCards(["oaw.skill-package"])); })}>Choose Toolset</button>
          <select aria-label="Toolset to assimilate" value={toolset} onChange={e => { setToolset(e.target.value); setPreview(null); }}><option value="">Choose…</option>{toolsets.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
          <button disabled={!toolset || busy} onClick={() => void run(async () => { const original = await host.readDocument(toolset); const current = await host.documentAction("statistics", {}); setPreview(await host.transform("assimilate", { source_id: toolset, source_revision: original.revision, expected_revision: current.revision })); })}>Preview assimilation</button>
          {preview && <div role="dialog" aria-label="Confirm assimilation"><p>Assimilate “{String(preview.source_name)}” and consume its card?</p><button disabled={busy} onClick={() => void run(async () => { await host.transform("assimilate", { source_id: toolset, source_revision: preview.source_revision, expected_revision: preview.revision, confirm: true }); setPreview(null); setToolset(""); setNotice("Toolset assimilated. Source package preserved in snapshots."); await load(); })}>Confirm assimilation</button><button onClick={() => setPreview(null)}>Cancel</button></div>}
        </details>
      </aside>}
      <main ref={canvasRef} className="kdg-canvas"><ReactFlow id={`oaw-knowledge-map-${card.id}`} nodes={visibleNodes} edges={visibleEdges} edgeTypes={mapEdgeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        onNodeMouseEnter={(_, node) => setHoveredNode(node.id)} onNodeMouseLeave={() => setHoveredNode(null)}
        onEdgeMouseEnter={(_, edge) => setHoveredEdge(edge.id)} onEdgeMouseLeave={() => setHoveredEdge(null)}
        onNodeClick={(_, node) => void run(() => select(node.id))} onNodeDoubleClick={(_, node) => void run(() => load([node.id], true))}
        onNodeDragStop={(_, node) => positions.set(node.id, node.position)} onNodeContextMenu={(event, node) => { event.preventDefault(); const element = workspaceRef.current!; const rect = element.getBoundingClientRect(); setMenu({ id: node.id, x: (event.clientX - rect.left) * element.clientWidth / rect.width, y: (event.clientY - rect.top) * element.clientHeight / rect.height }); void run(() => select(node.id)); }}
        onPaneClick={() => setMenu(null)}
        noPanClassName="kdg-nopan" noDragClassName="kdg-nodrag" noWheelClassName="kdg-nowheel"
        nodesDraggable={false} panOnDrag={false} zoomOnScroll={false} zoomOnPinch={false} zoomOnDoubleClick={false}
        selectionKeyCode={null} selectionOnDrag={false} autoPanOnNodeDrag={false} autoPanOnSelection={false}
        nodesConnectable={false} deleteKeyCode={null} onlyRenderVisibleElements minZoom={0.1} maxZoom={2} fitView><Background /><Controls showInteractive={false} /></ReactFlow>
        {selectionBox && <div className="kdg-selection-box" style={{ left: selectionBox.x, top: selectionBox.y, width: selectionBox.width, height: selectionBox.height }} />}</main>
      {inspector && <aside className="kdg-inspector" aria-label="Knowledge details"><button className="kdg-inspector-close" aria-label="Close inspector" onClick={() => { inspectSequence.current += 1; setInspector(false); setEditing(false); setConfirmDelete(false); }}>Close</button>{inspectEntry ? <>
        <h3>{inspectEntry.title}</h3><p>{inspectEntry.type} · {inspectEntry.verification} · trust {inspectEntry.trust}</p>
        {inspectEntry.id && <div className="kdg-entry-actions"><button disabled={busy} onClick={() => void run(() => load([inspectEntry.id], true))}>Expand neighborhood</button>
          {!editing && <button disabled={busy} onClick={() => { setEditing(true); setConfirmDelete(false); }}>Edit entry</button>}
          {!editing && <button className="kdg-delete" disabled={busy} onClick={() => setConfirmDelete(true)}>Delete entry</button>}
        </div>}
        {confirmDelete && <div className="kdg-delete-confirm" role="dialog" aria-label="Delete knowledge entry"><p>Delete “{inspectEntry.title}” and its graph relationships? Source snapshots and Skill resources are kept.</p><button className="kdg-delete" disabled={busy} onClick={() => void run(() => mutate('delete_entry', { entry_id: inspectEntry.id }))}>Confirm delete</button><button disabled={busy} onClick={() => setConfirmDelete(false)}>Cancel</button></div>}
        <p>{inspectEntry.summary}</p>
        {!editing && <div className="kdg-content">{inspectEntry.content}</div>}
        {editing && <form onSubmit={e => { e.preventDefault(); void run(() => mutate("edit", { entry_id: inspectEntry.id || undefined, title: inspectEntry.title, type: inspectEntry.type, content: inspectEntry.content, summary: inspectEntry.summary, tags: inspectEntry.tags, aliases: inspectEntry.aliases, trust: inspectEntry.trust, verification: inspectEntry.verification })); }}><label>Title<input required maxLength={200} value={inspectEntry.title} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, title: e.target.value } })} /></label>
          <label>Entry type<select value={inspectEntry.type} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, type: e.target.value } })}>{kinds.map(kind => <option key={kind}>{kind}</option>)}</select></label>
          <label>Summary<textarea aria-label="Summary" value={inspectEntry.summary} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, summary: e.target.value } })} /></label>
          <label>Tags<input value={inspectEntry.tags?.join(", ") ?? ""} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, tags: e.target.value.split(",").map(s => s.trim()) } })} /></label>
          <label>Aliases<input value={inspectEntry.aliases?.join(", ") ?? ""} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, aliases: e.target.value.split(",").map(s => s.trim()) } })} /></label>
          <label>Trust<input type="number" min="0" max="1" step="0.1" value={inspectEntry.trust} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, trust: Number(e.target.value) } })} /></label>
          <label>Verification<select value={inspectEntry.verification} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, verification: e.target.value } })}>{["unverified", "reviewed", "tested"].map(status => <option key={status}>{status}</option>)}</select></label>
          <label>Content<textarea aria-label="Content" value={inspectEntry.content} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, content: e.target.value } })} /></label>
          <div className="kdg-entry-actions"><button type="submit" disabled={busy}>Save changes</button><button type="button" disabled={busy} onClick={() => { setDetail(savedDetail); setEditing(false); if (!savedDetail) setInspector(false); }}>Cancel editing</button></div>
          </form>}
          {!editing && inspectEntry.id && <details><summary>Edit relationships</summary>
          <label>Connect to<select value={connectTarget} onChange={e => setConnectTarget(e.target.value)}><option value="">Choose entry…</option>{nodes.filter(n => n.id !== inspectEntry.id).map(n => <option key={n.id} value={n.id}>{String(n.data.title)}</option>)}</select></label>
          <select aria-label="New relationship type" value={connectRelation} onChange={e => setConnectRelation(e.target.value)}>{relations.map(r => <option key={r}>{r}</option>)}</select>
          <button disabled={!connectTarget || busy} onClick={() => void run(() => mutate("connect", { source: inspectEntry.id, target: connectTarget, relation: connectRelation }))}>Commit relationship</button></details>}
        {mode === "review" && !editing && <><label>Reviewed knowledge<textarea value={inspectEntry.content} onChange={e => setDetail({ ...detail, entry: { ...inspectEntry, content: e.target.value } })} /></label><label>Evidence<input value={evidence} onChange={e => setEvidence(e.target.value)} /></label><button disabled={!evidence || busy} onClick={() => void run(() => mutate("distill", { memory_ids: [inspectEntry.id], title: inspectEntry.title, content: inspectEntry.content, evidence }))}>Distill to Heuristic</button></>}
        <h4>Resources</h4>{detail.resources.map(resource => <details key={`${resource.skill_node_id}:${resource.path}`}><summary>{resource.path}</summary>{resource.files.map(path => <button disabled={busy} key={path} onClick={() => void run(async () => { const response = await host.documentAction("inspect", { entry_id: inspectEntry.id, resource_path: path }); const value = response.value as { path: string; content: unknown }; setResourcePreview({ path: value.path, content: typeof value.content === 'string' ? value.content : JSON.stringify(value.content, null, 2) }); })}>{path}</button>)}</details>)}
        {resourcePreview && <section aria-label="Resource preview"><h4>{resourcePreview.path}</h4><button onClick={() => setResourcePreview(null)}>Close resource</button><pre>{resourcePreview.content}</pre></section>}
        <h4>Relationships</h4>{detail.relationships.map(edge => <div key={edge.id}><button disabled={busy} onClick={() => void run(() => select(edge.source === inspectEntry.id ? edge.target : edge.source))}>{edge.relation}</button>{!editing && <button className="kdg-delete" disabled={busy} onClick={() => { if (window.confirm(`Delete ${edge.relation} relationship?`)) void run(() => mutate("delete_relationship", { edge_id: edge.id })); }}>Delete relationship</button>}</div>)}
        <details><summary>Provenance and usage</summary><p>Uses: {inspectEntry.usage_count ?? 0} · {inspectEntry.refinement}</p><pre>{JSON.stringify(inspectEntry.provenance, null, 2)}</pre></details>
      </> : <p>Select an entry to inspect it. Double-click to expand its neighborhood.</p>}</aside>}
    </div>
    {menu && <div role="menu" style={{ position: "absolute", left: Math.max(0, Math.min(menu.x, (workspaceRef.current?.clientWidth ?? 400) - 190)), top: Math.max(0, Math.min(menu.y, (workspaceRef.current?.clientHeight ?? 400) - 90)), zIndex: 200, background: "var(--surface-solid)", padding: 8 }}><button role="menuitem" onClick={() => { void run(() => load([menu.id], true)); setMenu(null); }}>Expand neighborhood</button><button role="menuitem" onClick={() => { void flow.fitView({ nodes: [{ id: menu.id }], padding: 0.5 }); setMenu(null); }}>Focus</button></div>}
  </section>;
}
export default { apiVersion: 1, views: { workspace: Workspace } } satisfies FrontendPlugin;
