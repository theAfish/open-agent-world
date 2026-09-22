import { useCallback, useEffect, useRef, useState } from "react";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";
import "./style.css";

type SchemaObject = { type: string; name: string; table: string; sql: string | null };
type Schema = { schema_version: number; objects: SchemaObject[]; truncated: boolean;
  columns?: { name: string; type: string; notnull: number; default: unknown; pk: number; hidden: number }[];
  foreign_keys?: { from: string; table: string; to: string; on_delete: string; on_update: string }[];
  indexes?: { name: string; unique: boolean; columns: (string | null)[] }[] };
type Result = { status: string; columns?: string[]; rows?: unknown[][]; truncated?: boolean;
  changes?: number; elapsed_ms?: number; schema_version?: number; reasons?: string[] };
type Pending = { args: Record<string, unknown>; reasons: string[] };
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const quote = (name: string) => `"${name.replaceAll('"', '""')}"`;

function valueText(value: unknown): string {
  if (value === null) return "NULL";
  if (typeof value === "object" && value && "type" in value && value.type === "blob")
    return `BLOB · ${"bytes" in value ? value.bytes : "?"} bytes`;
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function Preview({ host, card }: PluginViewProps) {
  const [schema, setSchema] = useState<Schema>();
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    host.resourceAction("inspect", {}).then(value => { if (active) setSchema(value as Schema); })
      .catch(reason => { if (active) setError(message(reason)); });
    return () => { active = false; };
  }, [host, card.id]);
  const tables = schema?.objects.filter(item => item.type === "table") ?? [];
  return <div className="sqlite-preview">
    <span className="sqlite-badge">SQLite · persistent</span>
    {error ? <p role="alert" title={error}>Database unavailable</p> : !schema ? <p role="status">Loading database…</p> : <>
      <strong>{tables.length}{schema.truncated ? "+" : ""} {tables.length === 1 ? "table" : "tables"}</strong>
      {!tables.length && <p>Ready for your data</p>}
    </>}
  </div>;
}

export function Database({ host, card }: PluginViewProps) {
  const [schema, setSchema] = useState<Schema>();
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Schema>();
  const [sql, setSql] = useState("SELECT name, type FROM sqlite_schema\nWHERE name NOT GLOB 'sqlite_*'\nORDER BY type, name;");
  const [parameters, setParameters] = useState("[]");
  const [readOnly, setReadOnly] = useState(true);
  const [result, setResult] = useState<Result>();
  const [pending, setPending] = useState<Pending>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"results" | "schema">("results");
  const generation = useRef(0);
  const detailSequence = useRef(0);
  const refreshSequence = useRef(0);
  const running = useRef(false);

  const refresh = useCallback(async () => {
    const current = generation.current;
    const sequence = ++refreshSequence.current;
    const value = await host.resourceAction("inspect", {}) as Schema;
    if (generation.current === current && sequence === refreshSequence.current) setSchema(value);
    return value;
  }, [host]);

  useEffect(() => {
    const current = generation.current;
    void refresh().catch(reason => { if (generation.current === current) setError(message(reason)); });
    return () => { generation.current += 1; detailSequence.current += 1; };
  }, [refresh, card.id]);

  useEffect(() => {
    const sequence = ++detailSequence.current;
    setDetail(undefined);
    if (!selected) return;
    if (!schema?.objects.some(item => item.name === selected)) { setSelected(""); return; }
    host.resourceAction("inspect", { table: selected }).then(value => {
      if (sequence === detailSequence.current) setDetail(value as Schema);
    }).catch(reason => { if (sequence === detailSequence.current) setError(message(reason)); });
  }, [host, selected, schema]);

  async function run(override?: string, confirmed?: Pending) {
    if (running.current || !schema) return;
    running.current = true;
    setBusy(true); setError(""); setPending(undefined);
    const current = generation.current;
    try {
      const parsed = JSON.parse(parameters);
      if (!parsed || typeof parsed !== "object") throw new Error("Parameters must be a JSON array or object.");
      const args = confirmed?.args ?? { sql: override ?? sql, parameters: parsed, max_rows: 200,
        ...(!readOnly && !override ? { schema_version: schema.schema_version } : {}) };
      const next = await host.resourceAction(confirmed || (!readOnly && !override) ? "admin" : "query", args, !!confirmed) as Result;
      if (generation.current !== current) return;
      if (next.status === "confirmation_required") {
        setPending({ args, reasons: next.reasons ?? [] });
        return;
      }
      setResult(next); setTab("results");
      await refresh();
    } catch (reason) {
      if (generation.current === current) { setError(message(reason)); setResult(undefined); }
    } finally {
      running.current = false;
      if (generation.current === current) setBusy(false);
    }
  }

  const tables = schema?.objects.filter(item => ["table", "view"].includes(item.type)) ?? [];
  const currentObject = schema?.objects.find(item => item.name === selected);
  return <div className="sqlite-app nodrag nowheel" aria-label={`${card.name} database`}>
    <header className="sqlite-toolbar">
      <span className="sqlite-badge">SQLite</span>
      <span>{tables.length} tables & views</span>
      <button type="button" disabled={busy} onClick={() => {
        setPending(undefined); setError(""); void refresh().catch(reason => setError(message(reason)));
      }}>Refresh</button>
    </header>
    <div className="sqlite-layout">
      <aside className="sqlite-sidebar" aria-label="Database tables">
        <h3>Tables & views</h3>
        {!schema && !error && <p role="status">Loading…</p>}
        {schema && !tables.length && <p>No tables yet. Inspect your data needs, then create a table for each distinct entity.</p>}
        {tables.map(item => <button type="button" key={item.name} aria-pressed={selected === item.name}
          onClick={() => { setSelected(item.name); setTab("schema"); }} title={item.name}>
          <span>{item.name}</span><small>{item.type}</small>
        </button>)}
        {schema?.truncated && <p>Schema list limited to 500 objects.</p>}
      </aside>
      <main className="sqlite-main">
        <label className="sqlite-sql-label" htmlFor={`sql-${card.id}`}>SQL editor</label>
        <textarea id={`sql-${card.id}`} value={sql} spellCheck={false} disabled={busy}
          onChange={event => { setSql(event.target.value); setPending(undefined); }}
          onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); void run(); } }} />
        <details className="sqlite-parameters"><summary>Bound parameters</summary>
          <label>JSON array or object<textarea aria-label="SQL parameters" value={parameters} disabled={busy}
            onChange={event => { setParameters(event.target.value); setPending(undefined); }} spellCheck={false} /></label>
          <small>Use ? with an array, or :name with an object. Values are bound separately from SQL.</small>
        </details>
        <div className="sqlite-runbar">
          <label><input type="checkbox" checked={readOnly} disabled={busy} onChange={event => {
            setReadOnly(event.target.checked); setPending(undefined);
          }} /> Read only</label>
          <small>One statement · Ctrl/⌘ Enter</small>
          <button className="sqlite-run" type="button" disabled={busy || !schema || !sql.trim()} onClick={() => void run()}>
            {busy ? "Running…" : readOnly ? "Run query" : "Run SQL"}
          </button>
        </div>
        {error && <p role="alert" className="sqlite-error">{error}</p>}
        {pending && <div role="alert" className="sqlite-confirm">
          <strong>Confirm database change</strong>
          <p>{pending.reasons.join(". ")}. Review the affected rows before proceeding.</p>
          <pre>{String(pending.args.sql)}</pre>
          <button type="button" disabled={busy} onClick={() => void run(undefined, pending)}>Confirm and run</button>
          <button type="button" onClick={() => setPending(undefined)}>Cancel</button>
        </div>}
        <div className="sqlite-tabs" role="tablist" aria-label="Database details">
          <button type="button" role="tab" aria-selected={tab === "results"} onClick={() => setTab("results")}>Results</button>
          <button type="button" role="tab" aria-selected={tab === "schema"} onClick={() => setTab("schema")}>Schema</button>
          {selected && <button type="button" disabled={busy} onClick={() => {
            const query = `SELECT * FROM ${quote(selected)} LIMIT 200;`; setSql(query); setPending(undefined); void run(query);
          }}>Browse rows</button>}
        </div>
        <section className="sqlite-output" role="tabpanel" aria-label={tab === "results" ? "SQL results" : "Table schema"}>
          {tab === "results" ? result ? <>
            <p className="sqlite-result-meta" role="status">{result.rows?.length ?? 0} rows shown · {result.changes ?? 0} changes · {result.elapsed_ms} ms
              {result.truncated ? " · Result limited; narrow your query or use LIMIT / OFFSET." : ""}</p>
            {!!result.columns?.length && <div className="sqlite-table-scroll"><table><thead><tr>
              {result.columns.map((name, index) => <th key={index}>{name}</th>)}
            </tr></thead><tbody>{result.rows?.map((row, index) => <tr key={index}>
              {row.map((value, column) => <td key={column} className={value === null ? "sqlite-null" : ""}>{valueText(value)}</td>)}
            </tr>)}</tbody></table></div>}
          </> : <p className="sqlite-empty">Run a query or select a table to explore your database.</p> : selected ? <>
            <h3>{selected}</h3>
            {!detail ? <p>Loading schema…</p> : <>
              <div className="sqlite-table-scroll"><table><thead><tr><th>Column</th><th>Type</th><th>Constraints</th><th>Default</th></tr></thead>
                <tbody>{detail.columns?.map(column => <tr key={column.name}><td>{column.name}</td><td>{column.type || "—"}</td>
                  <td>{[column.pk ? `PK ${column.pk}` : "", column.notnull ? "NOT NULL" : "", column.hidden ? "generated" : ""].filter(Boolean).join(" · ")}</td>
                  <td>{column.default === null ? "—" : valueText(column.default)}</td></tr>)}</tbody></table></div>
              {!!detail.foreign_keys?.length && <><h4>Foreign keys</h4>{detail.foreign_keys.map((fk, index) => <p key={index}>{fk.from} → {fk.table}.{fk.to} · delete {fk.on_delete} · update {fk.on_update}</p>)}</>}
              {!!detail.indexes?.length && <><h4>Indexes</h4>{detail.indexes.map(index => <p key={index.name}>{index.name} ({index.columns.join(", ")}) {index.unique ? "· unique" : ""}</p>)}</>}
            </>}
            <h4>Definition</h4><pre>{currentObject?.sql}</pre>
            {schema?.objects.filter(item => item.type === "trigger" && item.table === selected).map(item => <pre key={item.name}>{item.sql}</pre>)}
          </> : <><p className="sqlite-empty">Select a table or view to see its columns, keys and definition.</p>
            {schema?.objects.map(item => <pre key={item.name}>{item.sql}</pre>)}</>}
        </section>
      </main>
    </div>
  </div>;
}

export default { apiVersion: 1, views: { preview: Preview, database: Database } } satisfies FrontendPlugin;
