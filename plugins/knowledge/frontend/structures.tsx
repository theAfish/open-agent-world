import { useCallback, useEffect, useRef, useState } from "react";
import type { PluginViewProps } from "@oaw/plugin-api";
import { Sources, WriteLog, message, type Source } from "./shared";
import "./structures.css";

type Cell = { a: number; b: number; c: number; alpha: number; beta: number; gamma: number };
type ExternalRef = { database: string; id: string; url: string | null; verified: false } | null;
export type Structure = {
  id: number; record: string; name: string; formula: string; reduced: string; chemsys: string;
  spacegroup: { number: number | null; symbol: string | null }; crystal_system: string | null;
  cell: Cell; volume: number; nsites: number; sites_basis: "cell" | "asymmetric unit"; volume_per_site: number | null;
  properties: Record<string, string | number | boolean>; note: string; status: "active" | "retracted";
  retracted_reason?: string; created_by: string; updated_at: string;
  provenance?: { citations: number; stale: number; external_ref: ExternalRef };
  external_ref?: ExternalRef; sources?: Source[]; cif?: string;
};
type Summary = { active: number; retracted: number; chemsys: { chemsys: string; count: number }[];
  crystal_systems: { crystal_system: string; count: number }[] };
type Filters = { formula: string; chemsys: string; within: boolean; spacegroup: string; crystal_system: string; retracted: boolean };

const SYSTEMS = ["triclinic", "monoclinic", "orthorhombic", "tetragonal", "trigonal", "hexagonal", "cubic"];
const fixed = (value: number, digits = 4) => Number(value.toFixed(digits)).toString();
const spacegroup = (item: Structure) => item.spacegroup.symbol
  ? `${item.spacegroup.symbol}${item.spacegroup.number ? ` (${item.spacegroup.number})` : ""}` : "unknown";

function sourceLabel(item: Structure) {
  const provenance = item.provenance;
  if (!provenance) return "";
  const parts = [];
  if (provenance.citations) parts.push(`${provenance.citations} citation${provenance.citations === 1 ? "" : "s"}${provenance.stale ? ` (${provenance.stale} stale)` : ""}`);
  if (provenance.external_ref) parts.push(`${provenance.external_ref.database} ${provenance.external_ref.id}`);
  return parts.join(" · ") || "user";
}

export function Preview({ host, card }: PluginViewProps) {
  const [summary, setSummary] = useState<Summary>();
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    host.resourceAction("ui_summary", {}).then(value => { if (active) setSummary(value as Summary); })
      .catch(reason => { if (active) setError(message(reason)); });
    return () => { active = false; };
  }, [host, card.id]);
  return <div className="knowledge-preview">
    <span className="knowledge-badge">Structure database</span>
    {error ? <p role="alert" title={error}>Database unavailable</p> : !summary ? <p role="status">Loading…</p> : <>
      <strong>{summary.active} {summary.active === 1 ? "structure" : "structures"}</strong>
      {summary.retracted > 0 && <span className="knowledge-muted">{summary.retracted} retracted</span>}
      {summary.chemsys.length > 0 && <ul className="structures-chips" aria-label="Top chemical systems">
        {summary.chemsys.slice(0, 5).map(item => <li key={item.chemsys}>{item.chemsys} <small>{item.count}</small></li>)}
      </ul>}
    </>}
  </div>;
}

export function Workspace({ host, card }: PluginViewProps) {
  const [tab, setTab] = useState<"browse" | "add" | "log">("browse");
  const [filters, setFilters] = useState<Filters>({ formula: "", chemsys: "", within: false, spacegroup: "", crystal_system: "", retracted: false });
  const [results, setResults] = useState<{ total: number; structures: Structure[] }>();
  const [selected, setSelected] = useState<number>();
  const [error, setError] = useState("");
  const sequence = useRef(0);

  const search = useCallback(async (current: Filters) => {
    const request = ++sequence.current;
    setError("");
    const args: Record<string, unknown> = { limit: 100, include_retracted: current.retracted };
    if (current.formula.trim()) args.formula = current.formula.trim();
    if (current.chemsys.trim()) { args.chemsys = current.chemsys.trim(); args.chemsys_match = current.within ? "within" : "exact"; }
    if (current.spacegroup.trim()) args.spacegroup = current.spacegroup.trim();
    if (current.crystal_system) args.crystal_system = current.crystal_system;
    try {
      const value = await host.resourceAction("ui_search", args) as { total: number; structures: Structure[] };
      if (request === sequence.current) setResults(value);
    } catch (reason) {
      if (request === sequence.current) setError(message(reason));
    }
  }, [host]);

  useEffect(() => { void search(filters); }, [search, card.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = () => void search(filters);
  const set = (patch: Partial<Filters>) => setFilters(value => ({ ...value, ...patch }));

  return <div className="knowledge-app structures-app nodrag nowheel" aria-label={`${card.name} structure database`}>
    <div className="knowledge-tabs" role="tablist" aria-label="Structure database views">
      {([["browse", "Structures"], ["add", "Add CIF"], ["log", "Log"]] as const).map(([id, label]) =>
        <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>)}
    </div>
    {tab === "log" && <WriteLog host={host} />}
    {tab === "add" && <AddStructure host={host} onAdded={id => { setTab("browse"); setSelected(id); refresh(); }} />}
    {tab === "browse" && <>
      <form className="structures-filters" onSubmit={event => { event.preventDefault(); refresh(); }}>
        <label>Formula<input value={filters.formula} placeholder="NaCl" onChange={event => set({ formula: event.target.value })} /></label>
        <label>Chemical system<input value={filters.chemsys} placeholder="Li-Fe-O" onChange={event => set({ chemsys: event.target.value })} /></label>
        <label className="structures-check"><input type="checkbox" checked={filters.within} onChange={event => set({ within: event.target.checked })} /> within</label>
        <label>Space group<input value={filters.spacegroup} placeholder="Fm-3m or 225" onChange={event => set({ spacegroup: event.target.value })} /></label>
        <label>Crystal system<select value={filters.crystal_system} onChange={event => set({ crystal_system: event.target.value })}>
          <option value="">any</option>{SYSTEMS.map(name => <option key={name}>{name}</option>)}</select></label>
        <label className="structures-check"><input type="checkbox" checked={filters.retracted} onChange={event => set({ retracted: event.target.checked })} /> retracted</label>
        <button type="submit">Search</button>
      </form>
      {error && <p role="alert">{error}</p>}
      {!results && !error && <p role="status">Loading structures…</p>}
      {results && <p className="knowledge-muted" role="status">{results.total} {results.total === 1 ? "structure" : "structures"}{results.total > results.structures.length ? `, first ${results.structures.length} shown` : ""}</p>}
      {results && results.structures.length === 0 && <p className="knowledge-muted">No structures match. Agents add them with add_structure; you can add a CIF yourself.</p>}
      <div className="structures-layout">
        {results && results.structures.length > 0 && <div className="structures-scroll"><table className="knowledge-table structures-table">
          <thead><tr><th>Name</th><th>Formula</th><th>Space group</th><th>System</th><th>a</th><th>b</th><th>c</th><th>Sites</th><th>Source</th></tr></thead>
          <tbody>{results.structures.map(item => <tr key={item.id} aria-selected={selected === item.id}
            className={item.status === "retracted" ? "structures-retracted" : undefined} onClick={() => setSelected(item.id)}>
            <td><button type="button" className="structures-link" onClick={() => setSelected(item.id)}>{item.name}</button></td>
            <td>{item.formula}</td><td>{spacegroup(item)}</td><td>{item.crystal_system ?? "—"}</td>
            <td>{fixed(item.cell.a)}</td><td>{fixed(item.cell.b)}</td><td>{fixed(item.cell.c)}</td>
            <td title={item.sites_basis === "cell" ? "Sites in the unit cell" : "Asymmetric unit only (no symmetry operations in the CIF)"}>
              {item.nsites}{item.sites_basis === "cell" ? "" : "*"}</td>
            <td>{sourceLabel(item)}</td>
          </tr>)}</tbody></table></div>}
        {selected !== undefined && <Detail key={selected} host={host} id={selected} onClose={() => setSelected(undefined)} onChanged={refresh} />}
      </div>
    </>}
  </div>;
}

function Detail({ host, id, onClose, onChanged }: Pick<PluginViewProps, "host"> & { id: number; onClose: () => void; onChanged: () => void }) {
  const [item, setItem] = useState<Structure>();
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const [copied, setCopied] = useState(false);
  const [view3d, setView3d] = useState(false);
  const [similar, setSimilar] = useState<{ method: string; results: (Structure & { kind: string; cell_distance: number })[] }>();
  const load = useCallback(() => host.resourceAction("ui_detail", { id, include_cif: true })
    .then(value => setItem(value as Structure)).catch(reason => setError(message(reason))), [host, id]);
  useEffect(() => { void load(); }, [load]);

  async function retract() {
    setError("");
    try {
      await host.resourceAction("ui_retract", { id, reason: reason.trim() });
      setReason(""); await load(); onChanged();
    } catch (failure) { setError(message(failure)); }
  }
  function download() {
    if (!item?.cif) return;
    const url = URL.createObjectURL(new Blob([item.cif], { type: "chemical/x-cif" }));
    const anchor = Object.assign(document.createElement("a"), { href: url, download: `${item.name.replace(/[^\w.-]+/g, "_") || "structure"}.cif` });
    anchor.click();
    URL.revokeObjectURL(url);
  }
  async function findSimilar() {
    setError("");
    try { setSimilar(await host.resourceAction("ui_similar", { id, limit: 10 }) as typeof similar); } catch (failure) { setError(message(failure)); }
  }
  async function copy() {
    if (!item?.cif) return;
    try { await navigator.clipboard.writeText(item.cif); setCopied(true); } catch (failure) { setError(message(failure)); }
  }

  return <section className="structures-detail" aria-label="Structure detail">
    <header><strong>{item?.name ?? `Structure ${id}`}</strong><button type="button" onClick={onClose} aria-label="Close detail">×</button></header>
    {error && <p role="alert">{error}</p>}
    {!item && !error && <p role="status">Loading…</p>}
    {item && <>
      {item.status === "retracted" && <p className="structures-banner">Retracted: {item.retracted_reason}</p>}
      <dl className="structures-facts">
        <dt>Formula</dt><dd>{item.formula} <span className="knowledge-muted">({item.reduced})</span></dd>
        <dt>Space group</dt><dd>{spacegroup(item)}{item.crystal_system ? `, ${item.crystal_system}` : ""}</dd>
        <dt>Cell</dt><dd>a {fixed(item.cell.a)} Å, b {fixed(item.cell.b)} Å, c {fixed(item.cell.c)} Å<br />
          α {fixed(item.cell.alpha, 3)}°, β {fixed(item.cell.beta, 3)}°, γ {fixed(item.cell.gamma, 3)}°</dd>
        <dt>Volume</dt><dd>{fixed(item.volume, 3)} Å³{item.volume_per_site ? ` (${fixed(item.volume_per_site, 3)} Å³ per site)` : ""}</dd>
        <dt>Sites</dt><dd>{item.nsites} {item.sites_basis === "cell" ? "in the cell" : "in the asymmetric unit (no symmetry operations in the CIF)"}</dd>
        <dt>Added by</dt><dd>{item.created_by}, updated {item.updated_at}</dd>
      </dl>
      {Object.keys(item.properties).length > 0 && <table className="knowledge-table" aria-label="Properties"><tbody>
        {Object.entries(item.properties).map(([key, value]) => <tr key={key}><th>{key}</th><td>{String(value)}</td></tr>)}</tbody></table>}
      {item.note && <p className="structures-note">{item.note}</p>}
      <h4>Provenance</h4>
      {item.external_ref && <p className="structures-external">
        {item.external_ref.database} {item.external_ref.url
          ? <a href={item.external_ref.url} target="_blank" rel="noreferrer noopener">{item.external_ref.id}</a> : item.external_ref.id}
        <span className="knowledge-tag">external reference, not verified</span></p>}
      {(item.sources?.length || !item.external_ref) ? <Sources sources={item.sources ?? []} /> : null}
      <h4>Similar structures</h4>
      {!similar ? <div><button type="button" onClick={() => void findSimilar()}>Find similar</button></div> : <>
        <p className="knowledge-muted">{similar.method}</p>
        {similar.results.length ? <ul className="structures-similar">{similar.results.map(entry => <li key={entry.id}>
          {entry.name} <span className="knowledge-muted">{entry.formula}, {spacegroup(entry)}: {entry.kind}, distance {entry.cell_distance}</span>
        </li>)}</ul> : <p className="knowledge-muted">No stored structure shares this formula or prototype.</p>}
      </>}
      <h4>CIF</h4>
      <div className="structures-actions">
        <button type="button" onClick={() => void copy()}>{copied ? "Copied" : "Copy CIF"}</button>
        <button type="button" onClick={download}>Download CIF</button>
        <button type="button" aria-pressed={view3d} onClick={() => setView3d(value => !value)}>{view3d ? "Hide 3D" : "Show 3D"}</button>
      </div>
      {view3d && item.cif && <Structure3D cif={item.cif} />}
      <pre className="structures-cif" aria-label="CIF text">{item.cif}</pre>
      {item.status === "active" && <form className="structures-retract" onSubmit={event => { event.preventDefault(); void retract(); }}>
        <input aria-label="Retraction reason" value={reason} placeholder="Reason, e.g. duplicate of 12" onChange={event => setReason(event.target.value)} />
        <button type="submit" disabled={reason.trim().length < 3}>Retract</button>
      </form>}
    </>}
  </section>;
}

function base64(text: string) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (let start = 0; start < bytes.length; start += 0x8000) binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  return btoa(binary);
}

/** Rendered by the Structure viewer plugin's MatterViz adapter, loaded only when asked for. */
function Structure3D({ cif }: { cif: string }) {
  const target = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let dispose: (() => void) | undefined;
    let cancelled = false;
    void import("../../structure_viewer/frontend/render").then(({ renderStructure }) => {
      if (cancelled || !target.current) return;
      dispose = renderStructure(target.current, { name: "structure.cif", data: base64(cif) }, text => { if (!cancelled) setError(text); });
    }).catch(failure => { if (!cancelled) setError(message(failure)); });
    return () => { cancelled = true; dispose?.(); };
  }, [cif]);
  return <div className="structures-3d nodrag nopan nowheel">
    <div ref={target} className="structures-3d-canvas" />
    {error && <p role="alert" className="structures-3d-error">{error}</p>}
  </div>;
}

function AddStructure({ host, onAdded }: Pick<PluginViewProps, "host"> & { onAdded: (id: number) => void }) {
  const [cif, setCif] = useState("");
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [database, setDatabase] = useState("");
  const [entry, setEntry] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [added, setAdded] = useState<{ id: number; warnings: string[] }>();

  async function submit() {
    setBusy(true); setError(""); setAdded(undefined);
    try {
      const args: Record<string, unknown> = { cif, name: name.trim(), note: note.trim() };
      if (database.trim() && entry.trim()) args.external_ref = { database: database.trim(), id: entry.trim() };
      const result = await host.resourceAction("ui_add", args) as { id: number; warnings: string[] };
      setCif(""); setName(""); setNote("");
      if (result.warnings.length) setAdded(result);  // Keep warnings (e.g. a likely duplicate) in view.
      else onAdded(result.id);
    } catch (failure) { setError(message(failure)); } finally { setBusy(false); }
  }

  return <form className="structures-add" onSubmit={event => { event.preventDefault(); void submit(); }}>
    <p className="knowledge-muted">Structures you add carry no Paper citations; name the database entry if the CIF came from one.</p>
    <label>CIF file<input type="file" accept=".cif,.mcif,chemical/x-cif,text/plain" onChange={event => {
      const file = event.target.files?.[0];
      if (!file) return;
      if (file.size > 200_000) { setError("CIF files are limited to 200,000 characters."); return; }
      void file.text().then(text => { setCif(text); if (!name) setName(file.name.replace(/\.m?cif$/i, "")); });
    }} /></label>
    <label>CIF text<textarea aria-label="CIF text" value={cif} spellCheck={false} maxLength={200_000} onChange={event => setCif(event.target.value)} placeholder="data_..." /></label>
    <div className="structures-add-row">
      <label>Name<input value={name} onChange={event => setName(event.target.value)} placeholder="formula and space group" /></label>
      <label>Database<input value={database} onChange={event => setDatabase(event.target.value)} placeholder="COD, ICSD, Materials Project" /></label>
      <label>Entry id<input value={entry} onChange={event => setEntry(event.target.value)} placeholder="1000041" /></label>
    </div>
    <label>Note<input value={note} onChange={event => setNote(event.target.value)} /></label>
    {error && <p role="alert">{error}</p>}
    {added && <div className="structures-banner" role="status">
      <p>Added structure {added.id}, with warnings:</p>
      <ul>{added.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
      <button type="button" onClick={() => onAdded(added.id)}>Open structure {added.id}</button>
    </div>}
    <button type="submit" disabled={busy || !cif.trim()}>{busy ? "Adding…" : "Add structure"}</button>
  </form>;
}
