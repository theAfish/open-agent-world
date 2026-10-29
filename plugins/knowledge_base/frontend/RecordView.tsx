import { Modal, t } from "@oaw/plugin-api";
import { useState } from "react";
import { Expand } from "lucide-react";
import type { ReactNode } from "react";

/** A JSON Schema property, as far as the table needs to read it. */
export type SchemaProperty = { type?: string | string[]; enum?: unknown[]; description?: string;
  title?: string; unit?: string; items?: SchemaProperty; properties?: Record<string, SchemaProperty> };
export type Definition = { properties?: Record<string, SchemaProperty>; required?: string[]; items?: SchemaProperty };
export type Validation = { valid?: boolean; errors?: string[]; missing?: string[] } | null | undefined;

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isPrimitive = (value: unknown) => value === null || typeof value !== "object";

/** Compact text for a cell: primitives as people read them, structures summarised.
 * The full JSON travels in the cell's title so nothing is lost in the grid. */
export const formatCell = (value: unknown): string => {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "boolean") return value ? "✓" : "✗";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: 6 }) : String(value);
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    if (value.every(isPrimitive)) return value.map(formatCell).join(", ");
    return t("{v0} items", { v0: value.length });
  }
  if (isObject(value)) return `{${Object.keys(value).length}}`;
  return String(value);
};

/** Everything a person might type to find a record: each primitive, however nested. */
export const flattenValues = (value: unknown, into: string[] = []): string[] => {
  if (value === undefined || value === null) return into;
  if (Array.isArray(value)) { value.forEach(item => flattenValues(item, into)); return into; }
  if (isObject(value)) { Object.values(value).forEach(item => flattenValues(item, into)); return into; }
  into.push(String(value));
  return into;
};

const propertyLabel = (key: string, property?: SchemaProperty) =>
  property?.unit ? `${property.title || key} (${property.unit})` : (property?.title || key);

type InspectedValue = { label: string; value: unknown; description?: string };
type Inspect = (value: InspectedValue) => void;

/** Cells stay small; the original, unformatted value is available on hover and click. */
function CompactValue({ value, property, label, onInspect }: { value: unknown; property?: SchemaProperty; label: string; onInspect: Inspect }) {
  const full = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return <button type="button" className="knowledge-cell-preview" title={full} aria-label={t("View {v0}", { v0: label })}
    onClick={() => onInspect({ value, label, description: property?.description })}><span>{formatCell(value)}</span><Expand size={12} aria-hidden="true" /></button>;
}

/** Each array has its own scroll boundary; nested content never expands a table cell. */
function ArrayTable({ value, property, label, onInspect }: { value: Record<string, unknown>[]; property?: SchemaProperty; label: string; onInspect: Inspect }) {
  const itemProperties = property?.items?.properties ?? {};
  const columns = [...Object.keys(itemProperties), ...Array.from(new Set(value.flatMap(item => Object.keys(item)))).filter(key => !(key in itemProperties))];
  return <div className="knowledge-kv-scroll" role="region" aria-label={t("{v0} table", { v0: label })} tabIndex={0}>
    <table className="knowledge-kv-table knowledge-array-table" style={{ width: Math.max(1, columns.length) * 180 + 46 }}>
      <colgroup><col style={{ width: 46 }} />{columns.map(column => <col key={column} />)}</colgroup>
      <thead><tr><th scope="col">#</th>{columns.map(column => <th key={column} scope="col" title={[propertyLabel(column, itemProperties[column]), itemProperties[column]?.description].filter(Boolean).join("\n")}>
        <span>{propertyLabel(column, itemProperties[column])}</span></th>)}</tr></thead>
      <tbody>{value.map((item, index) => <tr key={index}>
        <th scope="row" className="knowledge-kv-index"><button type="button" aria-label={t("View row {v0}", { v0: index + 1 })} onClick={() => onInspect({ label: `${label} · ${index + 1}`, value: item })}>{index + 1}</button></th>
        {columns.map(column => <td key={column} className={typeof item[column] === "number" ? "knowledge-kv-number" : undefined}>
          <Value value={item[column]} property={itemProperties[column]} label={`${label} · ${index + 1} · ${propertyLabel(column, itemProperties[column])}`} compact onInspect={onInspect} />
        </td>)}
      </tr>)}</tbody>
    </table>
  </div>;
}

function Value({ value, property, label, onInspect, compact = false, depth = 0 }: { value: unknown; property?: SchemaProperty; label: string; onInspect: Inspect; compact?: boolean; depth?: number }) {
  if (value === undefined || value === null || value === "") return <span className="knowledge-kv-empty">—</span>;
  if (typeof value === "boolean") return <span className={`knowledge-kv-bool is-${value}`}>{value ? "✓ true" : "✗ false"}</span>;
  if (typeof value === "number") return <span className="knowledge-kv-number" title={String(value)}>{formatCell(value)}</span>;
  if (compact || (typeof value === "string" && (value.length > 160 || value.includes("\n")))) return <CompactValue value={value} property={property} label={label} onInspect={onInspect} />;
  if (typeof value === "string") return <span className="knowledge-kv-text">{value}</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="knowledge-kv-empty">{t("empty list")}</span>;
    if (value.every(isPrimitive) && (value.length > 4 || formatCell(value).length > 160)) return <CompactValue value={value} property={property} label={label} onInspect={onInspect} />;
    if (value.every(isPrimitive)) return <span className="knowledge-chips">
      {value.map((item, index) => <span key={index} className="knowledge-chip">{formatCell(item)}</span>)}</span>;
    if (value.every(isObject) && depth < 3) return <ArrayTable value={value} property={property} label={label} onInspect={onInspect} />;
    return <CompactValue value={value} property={property} label={label} onInspect={onInspect} />;
  }
  if (isObject(value) && depth < 3) {
    const entries = Object.entries(value);
    if (!entries.length) return <span className="knowledge-kv-empty">{t("empty")}</span>;
    const nested = property?.properties ?? {};
    return <table className="knowledge-kv-table knowledge-kv-nested"><tbody>
      {entries.map(([key, item]) => <tr key={key}>
        <th scope="row">{propertyLabel(key, nested[key])}</th>
        <td><Value value={item} property={nested[key]} label={`${label} · ${propertyLabel(key, nested[key])}`} onInspect={onInspect} depth={depth + 1} /></td>
      </tr>)}
    </tbody></table>;
  }
  return <CompactValue value={value} property={property} label={label} onInspect={onInspect} />;
}

/** A record's data as a key/value table in schema order, then whatever the record
 * carries that the schema does not name. A required field the record lacks is
 * flagged in place, so the validation message above has somewhere to point. */
export function RecordTable({ definition, data, validation }: {
  definition: Definition | undefined; data: Record<string, unknown> | unknown[]; validation?: Validation;
}) {
  const [inspected, setInspected] = useState<InspectedValue>();
  const properties = definition?.properties ?? {};
  const required = new Set(definition?.required ?? []);
  const missing = new Set(validation?.missing ?? []);
  const keys = Array.isArray(data) ? [] : [...Object.keys(properties), ...Object.keys(data).filter(key => !(key in properties))];
  if (!keys.length && !Array.isArray(data)) return <p className="knowledge-empty">{t("This record has no data")}</p>;
  return <div className="knowledge-record-data">{Array.isArray(data) ? <Value value={data} property={{ items: definition?.items }} label={t("Results")} onInspect={setInspected} /> : <table className="knowledge-kv-table">
    <tbody>
      {keys.map(key => {
        const property = properties[key];
        const value = data[key];
        const absent = value === undefined || value === null || value === "";
        const flagged = absent && (required.has(key) || missing.has(key));
        if (Array.isArray(value) && value.length > 0 && value.every(isObject)) return <tr key={key} className="knowledge-kv-array-row"><td colSpan={2}>
          <div className="knowledge-array-heading"><strong title={property?.description}>{propertyLabel(key, property)}</strong><span>{t("{v0} items", { v0: value.length })}</span></div>
          <ArrayTable value={value} property={property} label={propertyLabel(key, property)} onInspect={setInspected} />
        </td></tr>;
        return <tr key={key} className={flagged ? "is-missing" : undefined}>
          <th scope="row">
            <span className="knowledge-kv-label">
              <span>{propertyLabel(key, property)}{required.has(key) ? <span className="knowledge-kv-required" title={t("Required")}> *</span> : null}</span>
              {property?.description && <small>{property.description}</small>}
              {!property && <small>{t("Not in the schema")}</small>}
            </span>
          </th>
          <td>{flagged ? <span className="knowledge-kv-missing">{t("missing")}</span> : <Value value={value} property={property} label={propertyLabel(key, property)} onInspect={setInspected} />}</td>
        </tr>;
      })}
    </tbody>
  </table>}
    {inspected && <Modal title={inspected.label} className="knowledge-modal knowledge-value-dialog" onClose={() => setInspected(undefined)}>
      {inspected.description && <p className="knowledge-meta">{inspected.description}</p>}
      {typeof inspected.value === "string" ? <div className="knowledge-value-fulltext">{inspected.value}</div> : <pre className="knowledge-value-json">{JSON.stringify(inspected.value, null, 2)}</pre>}
    </Modal>}
  </div>;
}

export type GridRecord = { id: string; name: string; status: "draft" | "published"; data: Record<string, unknown>;
  validation?: Validation; bundle?: string; updated_at: string };

/** Every filtered record as one row of a spreadsheet: fixed columns, then one per
 * top-level schema property (plus any key some record carries outside the schema).
 * Clicking a row hands it back to the list, which opens it there. */
export function RecordGrid({ definition, records, showBundle, onOpen, header }: {
  definition: Definition | undefined; records: GridRecord[]; showBundle: boolean;
  onOpen: (id: string) => void; header?: ReactNode;
}) {
  const properties = definition?.properties ?? {};
  const columns = [...Object.keys(properties),
    ...Array.from(new Set(records.flatMap(record => Object.keys(record.data)))).filter(key => !(key in properties))];
  return <div className="knowledge-record-grid" role="region" aria-label={t("Records as a table")}>
    {header}
    <table>
      <thead><tr>
        <th scope="col" className="knowledge-grid-sticky">{t("Name")}</th>
        <th scope="col">{t("Status")}</th>
        {showBundle && <th scope="col">{t("Upload")}</th>}
        {columns.map(column => <th key={column} scope="col" title={properties[column]?.description}>
          {propertyLabel(column, properties[column])}</th>)}
      </tr></thead>
      <tbody>
        {records.map(record => <tr key={record.id} tabIndex={0} onClick={() => onOpen(record.id)}
          onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(record.id); } }}
          className={record.validation?.valid === false ? "is-invalid" : undefined}>
          <th scope="row" className="knowledge-grid-sticky">{record.name}</th>
          <td><span className={`knowledge-badge knowledge-status-${record.status}`}>{record.status === "published" ? t("Published") : t("Draft")}</span></td>
          {showBundle && <td className="knowledge-kv-text">{record.bundle ?? "—"}</td>}
          {columns.map(column => {
            const value = record.data[column];
            const structured = typeof value === "object" && value !== null;
            return <td key={column} title={structured ? JSON.stringify(value, null, 2) : undefined}
              className={typeof value === "number" ? "knowledge-kv-number" : undefined}>{formatCell(value)}</td>;
          })}
        </tr>)}
        {!records.length && <tr><td colSpan={columns.length + 2 + (showBundle ? 1 : 0)} className="knowledge-empty">{t("No records match")}</td></tr>}
      </tbody>
    </table>
  </div>;
}
