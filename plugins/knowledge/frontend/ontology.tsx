import { useCallback, useEffect, useRef, useState } from "react";
import type { PluginViewProps } from "@oaw/plugin-api";
import { Sources, WriteLog, message, type Source } from "./shared";
import "./ontology.css";

type Brief = { id: number; name?: string; kind?: string };
type Entity = { id: number; kind: string; name: string; formula: string | null; reduced: string | null; chemsys: string | null;
  status: string; description: string; aliases: string[]; alias_count: number; relations?: number; score?: number;
  matched?: { by: string; text?: string }[]; reason?: string };
type Relation = { id: number; record: string; subject: Brief; predicate: string; object: Brief; note: string; status: string;
  sources: Source[]; unsourced: boolean; custom_predicate?: boolean; reason?: string };
type Detail = Omit<Entity, "aliases"> & { record: string; sources: Source[];
  aliases: { alias: string; record: string; sources: Source[]; created_by: string }[];
  merged_from: { id: number; name: string; record: string; reason: string | null }[];
  outgoing: Relation[]; incoming: Relation[] };
type Vocabulary = { totals: { entities: number; relations: number; retracted_entities: number; merged_entities: number };
  kinds: { kind: string; entities: number }[]; predicates: { predicate: string; relations: number; unsourced: number }[];
  custom_predicates: { predicate: string; relations: number }[] };

export function Preview({ host, card }: PluginViewProps) {
  const [vocabulary, setVocabulary] = useState<Vocabulary>();
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    host.resourceAction("ui_vocabulary", {}).then(value => { if (active) setVocabulary(value as Vocabulary); })
      .catch(reason => { if (active) setError(message(reason)); });
    return () => { active = false; };
  }, [host, card.id]);
  const kinds = vocabulary?.kinds.filter(item => item.entities) ?? [];
  return <div className="knowledge-preview">
    <span className="knowledge-badge">Ontology</span>
    {error ? <p role="alert" title={error}>Ontology unavailable</p> : !vocabulary ? <p role="status">Loading…</p> : <>
      <strong>{vocabulary.totals.entities} entities · {vocabulary.totals.relations} relations</strong>
      {kinds.length ? <ul className="ontology-counts">{kinds.slice(0, 6).map(item =>
        <li key={item.kind}><span>{item.kind}</span><span>{item.entities}</span></li>)}</ul>
        : <p className="knowledge-muted">Agents add entities and relations here</p>}
    </>}
  </div>;
}

export function Workspace({ host, card }: PluginViewProps) {
  const [tab, setTab] = useState<"entities" | "log">("entities");
  return <div className="knowledge-app ontology-app">
    <div className="knowledge-tabs" role="tablist">
      <button role="tab" aria-selected={tab === "entities"} onClick={() => setTab("entities")}>Entities</button>
      <button role="tab" aria-selected={tab === "log"} onClick={() => setTab("log")}>Log</button>
    </div>
    {tab === "entities" ? <Entities host={host} card={card} /> : <WriteLog host={host} />}
  </div>;
}

function Entities({ host, card }: Pick<PluginViewProps, "host" | "card">) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState("");
  const [kinds, setKinds] = useState<string[]>([]);
  const [entities, setEntities] = useState<Entity[]>();
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<number>();
  const [error, setError] = useState("");
  const sequence = useRef(0);

  const search = useCallback(async () => {
    const current = ++sequence.current;
    try {
      const value = await host.resourceAction("ui_search", { text, kind }) as { entities: Entity[]; total: number };
      if (current === sequence.current) { setEntities(value.entities); setTotal(value.total); setError(""); }
    } catch (reason) { if (current === sequence.current) setError(message(reason)); }
  }, [host, text, kind]);

  useEffect(() => {
    const timer = setTimeout(() => void search(), text ? 250 : 0);
    return () => clearTimeout(timer);
  }, [search, text, card.id]);
  useEffect(() => {
    host.resourceAction("ui_vocabulary", {})
      .then(value => setKinds((value as Vocabulary).kinds.filter(item => item.entities).map(item => item.kind)))
      .catch(() => setKinds([]));
  }, [host, card.id]);

  return <div className="ontology-split">
    <section className="ontology-list" aria-label="Entities">
      <div className="ontology-filters">
        <input aria-label="Search entities" placeholder="Name, alias or formula" value={text} onChange={event => setText(event.target.value)} />
        <select aria-label="Kind" value={kind} onChange={event => setKind(event.target.value)}>
          <option value="">All kinds</option>
          {kinds.map(item => <option key={item} value={item}>{item}</option>)}
        </select>
      </div>
      {error && <p role="alert">{error}</p>}
      {!entities ? <p role="status">Loading…</p> : !entities.length
        ? <p className="knowledge-muted">{text ? "No entity matches." : "No entities yet."}</p>
        : <ul className="ontology-entities">{entities.map(entity => <li key={entity.id}>
          <button aria-pressed={selected === entity.id} onClick={() => setSelected(entity.id)}>
            <span className="ontology-name">{entity.name}</span>
            <span className="knowledge-tag">{entity.kind}</span>
            {entity.formula && <code>{entity.formula}</code>}
            {entity.matched?.[0] && <span className="knowledge-muted ontology-why">{entity.matched[0].by}{entity.matched[0].text ? `: ${entity.matched[0].text}` : ""}</span>}
            {!!entity.aliases.length && <span className="knowledge-muted ontology-aliases">{entity.aliases.join(" · ")}</span>}
          </button></li>)}</ul>}
      {entities && total > entities.length && <p className="knowledge-muted">Showing {entities.length} of {total}; refine the search.</p>}
    </section>
    <section className="ontology-detail" aria-label="Entity detail">
      {selected === undefined ? <p className="knowledge-muted">Select an entity to see its aliases, relations and sources.</p>
        : <EntityDetail host={host} entity={selected} onSelect={setSelected} onChanged={search} />}
    </section>
  </div>;
}

function EntityDetail({ host, entity, onSelect, onChanged }: Pick<PluginViewProps, "host"> & {
  entity: number; onSelect: (id: number) => void; onChanged: () => void }) {
  const [detail, setDetail] = useState<Detail>();
  const [showRetracted, setShowRetracted] = useState(false);
  const [alias, setAlias] = useState("");
  const [error, setError] = useState("");
  const sequence = useRef(0);

  const load = useCallback(async () => {
    const current = ++sequence.current;
    try {
      const value = await host.resourceAction("ui_entity", { entity, include_retracted: showRetracted }) as Detail;
      if (current === sequence.current) { setDetail(value); setError(""); }
    } catch (reason) { if (current === sequence.current) setError(message(reason)); }
  }, [host, entity, showRetracted]);
  useEffect(() => { setDetail(undefined); void load(); }, [load]);

  const act = async (action: string, args: Record<string, unknown>) => {
    try { await host.resourceAction(action, args); await load(); onChanged(); return true; }
    catch (reason) { setError(message(reason)); return false; }
  };

  if (!detail) return error ? <p role="alert">{error}</p> : <p role="status">Loading entity…</p>;
  return <div className="ontology-entity">
    <header>
      <h3>{detail.name} <span className="knowledge-tag">{detail.kind}</span>
        {detail.status !== "active" && <span className="knowledge-tag knowledge-stale">{detail.status}</span>}</h3>
      {detail.status === "active" && <Retract record={detail.record} onRetract={reason => act("ui_retract", { record: detail.record, reason })} />}
    </header>
    {error && <p role="alert">{error}</p>}
    {detail.reason && <p className="knowledge-muted">Reason: {detail.reason}</p>}
    {detail.formula && <p><code>{detail.formula}</code> <span className="knowledge-muted">reduced {detail.reduced ?? "—"} · system {detail.chemsys ?? "—"}</span></p>}
    {detail.description && <p>{detail.description}</p>}
    <Sources sources={detail.sources} />

    <h4>Aliases</h4>
    {detail.aliases.length ? <ul className="ontology-alias-list">{detail.aliases.map(item => <li key={item.record}>
      <strong>{item.alias}</strong>
      {detail.status === "active" && <Retract record={item.record} onRetract={reason => act("ui_retract", { record: item.record, reason })} />}
      <Sources sources={item.sources} />
    </li>)}</ul> : <p className="knowledge-muted">No aliases.</p>}
    {detail.status === "active" && <form className="ontology-inline" onSubmit={async event => {
      event.preventDefault();
      if (alias.trim() && await act("ui_add_alias", { entity: detail.id, alias: alias.trim() })) setAlias("");
    }}>
      <input aria-label="New alias" placeholder="Add an alias" value={alias} onChange={event => setAlias(event.target.value)} />
      <button type="submit" disabled={!alias.trim()}>Add alias</button>
    </form>}
    {!!detail.merged_from.length && <p className="knowledge-muted">Merged from: {detail.merged_from.map(item => `${item.name} (#${item.id})`).join(", ")}</p>}

    <label className="ontology-toggle"><input type="checkbox" checked={showRetracted} onChange={event => setShowRetracted(event.target.checked)} /> Show retracted relations</label>
    <Relations title="Outgoing" relations={detail.outgoing} side="object" onSelect={onSelect}
      onRetract={(record, reason) => act("ui_retract", { record, reason })} />
    <Relations title="Incoming" relations={detail.incoming} side="subject" onSelect={onSelect}
      onRetract={(record, reason) => act("ui_retract", { record, reason })} />
  </div>;
}

function Relations({ title, relations, side, onSelect, onRetract }: { title: string; relations: Relation[]; side: "subject" | "object";
  onSelect: (id: number) => void; onRetract: (record: string, reason: string) => Promise<boolean> }) {
  return <>
    <h4>{title} relations</h4>
    {!relations.length ? <p className="knowledge-muted">None.</p> : <table className="knowledge-table">
      <thead><tr><th>Predicate</th><th>{side === "object" ? "Object" : "Subject"}</th><th>Sources</th><th /></tr></thead>
      <tbody>{relations.map(relation => {
        const other = relation[side];
        return <tr key={relation.id} className={relation.status !== "active" ? "ontology-retracted" : undefined}>
          <td><code>{relation.predicate}</code>{relation.custom_predicate && <span className="knowledge-tag">custom</span>}
            {relation.status !== "active" && <span className="knowledge-tag knowledge-stale">{relation.status}</span>}</td>
          <td><button className="ontology-link" onClick={() => onSelect(other.id)}>{other.name ?? `#${other.id}`}</button>
            {relation.note && <div className="knowledge-muted">{relation.note}</div>}
            {relation.reason && <div className="knowledge-muted">Reason: {relation.reason}</div>}</td>
          <td>{relation.unsourced ? <span className="knowledge-tag knowledge-stale">unsourced</span> : <Sources sources={relation.sources} />}</td>
          <td>{relation.status === "active" && <Retract record={relation.record} onRetract={reason => onRetract(relation.record, reason)} />}</td>
        </tr>;
      })}</tbody></table>}
  </>;
}

/** Retraction needs a reason; the record stays in the store for audit. */
function Retract({ record, onRetract }: { record: string; onRetract: (reason: string) => Promise<boolean> }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  if (!open) return <button className="ontology-retract" aria-label={`Retract ${record}`} onClick={() => setOpen(true)}>Retract</button>;
  return <form className="ontology-inline" onSubmit={async event => {
    event.preventDefault();
    if (await onRetract(reason.trim())) { setOpen(false); setReason(""); }
  }}>
    <input aria-label={`Reason to retract ${record}`} placeholder="Reason" value={reason} autoFocus onChange={event => setReason(event.target.value)} />
    <button type="submit" disabled={reason.trim().length < 3}>Confirm retract</button>
    <button type="button" onClick={() => setOpen(false)}>Cancel</button>
  </form>;
}
