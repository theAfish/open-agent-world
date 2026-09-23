import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import type { PluginViewProps } from "@oaw/plugin-api";
import { Sources, WriteLog, message, type Source } from "./shared";
import "./facts.css";

type Conditions = Record<string, string | number | boolean>;
export type Fact = {
  id: number; key: string; material: string; formula: string | null; reduced: string | null; chemsys: string | null;
  property: string; value: number | null; value_max: number | null; value_text: string | null; unit: string;
  conditions: Conditions; method: string; note: string; status: "active" | "retracted"; retract_reason?: string;
  created_by: string; created_at: string; updated_by?: string; updated_at?: string; sources: Source[];
  normalized?: { value: number; value_max: number | null; unit: string; dimension: string };
};
type Result = { facts: Fact[]; total: number; offset: number; truncated: boolean; notes?: string[] };
type Vocabulary = {
  totals: { facts: number; materials: number; properties: number; retracted: number };
  properties: { property: string; count: number; units: Record<string, number> }[];
  materials: { material: string; reduced: string | null; count: number }[];
};
type Filters = { material: string; property: string; chemsys: string; within: boolean; min: string; max: string; unit: string; method: string; retracted: boolean };

const PAGE = 50;
const EMPTY: Filters = { material: "", property: "", chemsys: "", within: false, min: "", max: "", unit: "", method: "", retracted: false };
const number = (text: string) => text.trim() === "" ? undefined : Number(text);
const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

export function valueText(fact: Pick<Fact, "value" | "value_max" | "value_text" | "unit">) {
  if (fact.value === null) return fact.value_text ?? "";
  const range = fact.value_max === null ? `${fact.value}` : `${fact.value}–${fact.value_max}`;
  return fact.unit ? `${range} ${fact.unit}` : range;
}

const conditionText = (conditions: Conditions) => Object.entries(conditions).map(([key, value]) => `${key}: ${value}`).join(", ");

/** Turns the filter form into query_facts arguments (only filters that are set). */
export function queryArguments(filters: Filters, offset = 0): Record<string, unknown> {
  const args: Record<string, unknown> = { limit: PAGE, offset };
  for (const name of ["material", "property", "method"] as const) if (filters[name].trim()) args[name] = filters[name].trim();
  if (filters.chemsys.trim()) { args.chemsys = filters.chemsys.trim(); if (filters.within) args.chemsys_mode = "within"; }
  const min = number(filters.min), max = number(filters.max);
  if (min !== undefined) args.min_value = min;
  if (max !== undefined) args.max_value = max;
  if (min !== undefined || max !== undefined) args.unit = filters.unit.trim();
  if (filters.retracted) args.include_retracted = true;
  return args;
}

export function Preview({ host, card }: PluginViewProps) {
  const [vocabulary, setVocabulary] = useState<Vocabulary>();
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    host.resourceAction("ui_vocabulary", { limit: 3 }).then(value => { if (active) setVocabulary(value as Vocabulary); })
      .catch(reason => { if (active) setError(message(reason)); });
    return () => { active = false; };
  }, [host, card.id]);
  return <div className="knowledge-preview">
    <span className="knowledge-badge">Fact table</span>
    {error ? <p role="alert" title={error}>Fact table unavailable</p> : !vocabulary ? <p role="status">Loading…</p> : <>
      <strong>{plural(vocabulary.totals.facts, "fact")}</strong>
      <span className="knowledge-muted">{plural(vocabulary.totals.materials, "material")} · {vocabulary.totals.properties} {vocabulary.totals.properties === 1 ? "property" : "properties"}</span>
      {vocabulary.properties.length > 0 && <span className="knowledge-muted facts-preview-list">{vocabulary.properties.map(item => item.property).join(", ")}</span>}
    </>}
  </div>;
}

export function Workspace({ host }: PluginViewProps) {
  const [tab, setTab] = useState<"facts" | "log">("facts");
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [result, setResult] = useState<Result>();
  const [vocabulary, setVocabulary] = useState<Vocabulary>();
  const [selected, setSelected] = useState<number>();
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");
  const sequence = useRef(0);

  const load = useCallback(async (next: Filters, offset = 0) => {
    const current = ++sequence.current;
    setError("");
    try {
      const [facts, vocab] = await Promise.all([host.resourceAction("ui_query", queryArguments(next, offset)),
        host.resourceAction("ui_vocabulary", { limit: 100 })]);
      if (current !== sequence.current) return;
      setResult(facts as Result);
      setVocabulary(vocab as Vocabulary);
    } catch (reason) {
      if (current === sequence.current) setError(message(reason));
    }
  }, [host]);

  useEffect(() => { void load(EMPTY); }, [load]);
  const search = (event?: FormEvent) => { event?.preventDefault(); void load(filters); };
  const refine = (patch: Partial<Filters>) => { const next = { ...filters, ...patch }; setFilters(next); void load(next); };
  const set = (name: keyof Filters) => (event: { target: HTMLInputElement }) =>
    setFilters(value => ({ ...value, [name]: event.target.type === "checkbox" ? event.target.checked : event.target.value }));
  const fact = result?.facts.find(item => item.id === selected);
  const changed = () => void load(filters, result?.offset ?? 0);

  return <div className="knowledge-app facts-app">
    <div className="knowledge-tabs" role="tablist">
      <button role="tab" aria-selected={tab === "facts"} onClick={() => setTab("facts")}>Facts</button>
      <button role="tab" aria-selected={tab === "log"} onClick={() => setTab("log")}>Log</button>
    </div>
    {tab === "log" ? <WriteLog host={host} /> : <div className="facts-layout">
      <main className="facts-main">
        <form className="facts-filters" onSubmit={search} aria-label="Filter facts">
          <input aria-label="Material" placeholder="Material or formula" value={filters.material} onChange={set("material")} />
          <input aria-label="Property" placeholder="Property" list="facts-properties" value={filters.property} onChange={set("property")} />
          <datalist id="facts-properties">{vocabulary?.properties.map(item => <option key={item.property} value={item.property} />)}</datalist>
          <input aria-label="Chemical system" placeholder="Li-Fe-P-O" value={filters.chemsys} onChange={set("chemsys")} />
          <label className="facts-check"><input type="checkbox" checked={filters.within} onChange={set("within")} /> within</label>
          <input aria-label="Minimum value" placeholder="min" inputMode="decimal" value={filters.min} onChange={set("min")} />
          <input aria-label="Maximum value" placeholder="max" inputMode="decimal" value={filters.max} onChange={set("max")} />
          <input aria-label="Unit" placeholder="unit" value={filters.unit} onChange={set("unit")} />
          <input aria-label="Method" placeholder="Method" value={filters.method} onChange={set("method")} />
          <label className="facts-check"><input type="checkbox" checked={filters.retracted} onChange={set("retracted")} /> retracted</label>
          <button type="submit">Search</button>
          <button type="button" onClick={() => { setFilters(EMPTY); void load(EMPTY); }}>Clear</button>
          <button type="button" onClick={() => setAdding(value => !value)} aria-expanded={adding}>Add fact</button>
        </form>
        {adding && <AddFact host={host} onAdded={() => { setAdding(false); changed(); }} />}
        {error && <p role="alert">{error}</p>}
        {result?.notes?.map((note, index) => <p key={index} className="facts-note">{note}</p>)}
        {!result ? <p role="status">Loading facts…</p> : !result.facts.length ? <p className="knowledge-muted">No facts match.</p> :
          <FactTable facts={result.facts} selected={selected} onSelect={setSelected} />}
        {result && result.total > PAGE && <div className="facts-pager">
          <button disabled={result.offset === 0} onClick={() => void load(filters, Math.max(result.offset - PAGE, 0))}>Previous</button>
          <span className="knowledge-muted">{result.offset + 1}–{result.offset + result.facts.length} of {result.total}</span>
          <button disabled={!result.truncated} onClick={() => void load(filters, result.offset + PAGE)}>Next</button>
        </div>}
        {fact && <FactDetail key={fact.id} host={host} fact={fact} onChanged={changed} onClose={() => setSelected(undefined)} />}
      </main>
      <aside className="facts-vocabulary" aria-label="Vocabulary">
        <h3>Properties</h3>
        <ul>{vocabulary?.properties.map(item => <li key={item.property}>
          <button onClick={() => refine({ property: item.property })}>{item.property}</button>
          <span className="knowledge-muted">{item.count} · {Object.keys(item.units).filter(Boolean).join(", ")}</span></li>)}</ul>
        <h3>Materials</h3>
        <ul>{vocabulary?.materials.map(item => <li key={item.reduced ?? item.material}>
          <button onClick={() => refine({ material: item.reduced ?? item.material })}>{item.material}</button>
          <span className="knowledge-muted">{item.count}</span></li>)}</ul>
        {vocabulary && vocabulary.totals.retracted > 0 && <p className="knowledge-muted">{vocabulary.totals.retracted} retracted</p>}
      </aside>
    </div>}
  </div>;
}

function FactTable({ facts, selected, onSelect }: { facts: Fact[]; selected?: number; onSelect: (id: number) => void }) {
  return <table className="knowledge-table facts-table"><thead><tr>
    <th>Material</th><th>Property</th><th>Value</th><th>Conditions</th><th>Method</th><th>Sources</th></tr></thead>
    <tbody>{facts.map(fact => {
      const stale = fact.sources.filter(source => source.status === "stale").length;
      return <tr key={fact.id} aria-selected={fact.id === selected} className={fact.status === "retracted" ? "facts-retracted" : undefined}>
        <td><button className="facts-link" onClick={() => onSelect(fact.id)}>{fact.material}</button>
          {fact.reduced && fact.reduced !== fact.material && <div className="knowledge-muted">{fact.reduced}</div>}</td>
        <td>{fact.property}</td>
        <td>{valueText(fact)}{fact.status === "retracted" && <span className="knowledge-tag">retracted</span>}</td>
        <td>{conditionText(fact.conditions)}</td>
        <td>{fact.method}</td>
        <td>{fact.sources.length ? <>{fact.sources.map(source => source.cite).slice(0, 2).join(", ")}{fact.sources.length > 2 ? ` +${fact.sources.length - 2}` : ""}
          {stale > 0 && <span className="knowledge-tag knowledge-stale">{stale} stale</span>}</> : <span className="knowledge-muted">user</span>}</td>
      </tr>;
    })}</tbody></table>;
}

function FactDetail({ host, fact, onChanged, onClose }: { host: PluginViewProps["host"]; fact: Fact; onChanged: () => void; onClose: () => void }) {
  const [value, setValue] = useState(fact.value === null ? "" : String(fact.value));
  const [unit, setUnit] = useState(fact.unit);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (action: string, args: Record<string, unknown>) => {
    setBusy(true); setError("");
    try { await host.resourceAction(action, args); onChanged(); } catch (reason) { setError(message(reason)); } finally { setBusy(false); }
  };
  const revise = () => {
    const changes: Record<string, unknown> = {};
    if (value.trim() !== (fact.value === null ? "" : String(fact.value))) changes.value = number(value);
    if (unit !== fact.unit) changes.unit = unit;
    void run("ui_revise", { fact: fact.id, changes, note });
  };
  return <section className="facts-detail" aria-label={`Fact ${fact.id}`}>
    <header><strong>{fact.material} · {fact.property} = {valueText(fact)}</strong><button onClick={onClose} aria-label="Close detail">×</button></header>
    <dl>
      {fact.formula && <><dt>Composition</dt><dd>{fact.reduced} ({fact.chemsys})</dd></>}
      {!fact.reduced && <><dt>Composition</dt><dd className="knowledge-muted">not a formula; stored as text</dd></>}
      {fact.normalized && fact.normalized.unit !== fact.unit && <><dt>Normalised</dt><dd>{valueText({ ...fact.normalized, value_text: null })}</dd></>}
      {Object.keys(fact.conditions).length > 0 && <><dt>Conditions</dt><dd>{conditionText(fact.conditions)}</dd></>}
      {fact.method && <><dt>Method</dt><dd>{fact.method}</dd></>}
      {fact.note && <><dt>Note</dt><dd>{fact.note}</dd></>}
      <dt>Recorded</dt><dd>{fact.created_by} · {fact.created_at}</dd>
      {fact.updated_at && <><dt>Updated</dt><dd>{fact.updated_by} · {fact.updated_at}</dd></>}
      {fact.status === "retracted" && <><dt>Retracted</dt><dd>{fact.retract_reason}</dd></>}
      <dt>Sources</dt><dd><Sources sources={fact.sources} /></dd>
    </dl>
    {fact.status === "active" && <div className="facts-actions">
      <fieldset disabled={busy}><legend>Revise</legend>
        <input aria-label="Revised value" inputMode="decimal" value={value} onChange={event => setValue(event.target.value)} />
        <input aria-label="Revised unit" value={unit} onChange={event => setUnit(event.target.value)} />
        <input aria-label="Revision note" placeholder="Why (logged)" value={note} onChange={event => setNote(event.target.value)} />
        <button onClick={revise} disabled={note.trim().length < 3}>Save revision</button>
        {fact.sources.length > 0 && <span className="knowledge-muted">Changing the value or unit removes its citations (logged).</span>}
      </fieldset>
      <fieldset disabled={busy}><legend>Retract</legend>
        <input aria-label="Retraction reason" placeholder="Reason (kept for audit)" value={reason} onChange={event => setReason(event.target.value)} />
        <button onClick={() => void run("ui_retract", { facts: [fact.id], reason })} disabled={reason.trim().length < 3}>Retract</button>
      </fieldset>
    </div>}
    {error && <p role="alert">{error}</p>}
  </section>;
}

function AddFact({ host, onAdded }: { host: PluginViewProps["host"]; onAdded: () => void }) {
  const [form, setForm] = useState({ material: "", formula: "", property: "", value: "", value_max: "", value_text: "", unit: "", method: "", conditions: "", note: "" });
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const field = (name: keyof typeof form, label: string, placeholder = "") =>
    <input aria-label={label} placeholder={placeholder || label} value={form[name]} onChange={event => setForm(value => ({ ...value, [name]: event.target.value }))} />;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    const conditions: Conditions = {};
    for (const line of form.conditions.split(/[\n;]/)) {
      const [key, ...rest] = line.split(/[:=]/);
      if (key.trim() && rest.length) conditions[key.trim()] = rest.join(":").trim();
    }
    const fact: Record<string, unknown> = { material: form.material, property: form.property, unit: form.unit, method: form.method, note: form.note, conditions };
    if (form.formula.trim()) fact.formula = form.formula;
    if (number(form.value) !== undefined) fact.value = number(form.value);
    if (number(form.value_max) !== undefined) fact.value_max = number(form.value_max);
    if (form.value_text.trim()) fact.value_text = form.value_text;
    try {
      const result = await host.resourceAction("ui_add", { fact }) as { warnings?: string[] };
      setWarnings(result.warnings ?? []);
      if (!result.warnings?.length) onAdded();
    } catch (reason) { setError(message(reason)); }
  };
  return <form className="facts-add" onSubmit={submit} aria-label="Add fact">
    <p className="knowledge-muted">Facts you enter carry no citations and are shown as entered by the user.</p>
    <div className="facts-add-grid">
      {field("material", "New material", "Material")}{field("formula", "New formula", "Formula (optional)")}{field("property", "New property", "Property, e.g. band_gap")}
      {field("value", "New value", "Value")}{field("value_max", "New value max", "Upper bound (optional)")}{field("value_text", "New value text", "Qualitative value")}
      {field("unit", "New unit", "Unit")}{field("method", "New method", "Method")}{field("conditions", "New conditions", "temperature: 300 K; c_rate: 0.1C")}
      {field("note", "New note", "Note")}
    </div>
    {warnings.length > 0 ? <div className="facts-note"><p>Saved, with notes:</p>{warnings.map((warning, index) => <p key={index}>{warning}</p>)}
      <button type="button" onClick={onAdded}>Done</button></div>
      : <button type="submit" disabled={!form.material.trim() || !form.property.trim()}>Save fact</button>}
    {error && <p role="alert">{error}</p>}
  </form>;
}
