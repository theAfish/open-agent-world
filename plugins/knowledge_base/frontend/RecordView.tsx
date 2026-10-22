import { t } from "@oaw/plugin-api";
import type { ReactNode } from "react";

/** A JSON Schema property, as far as the table needs to read it. */
export type SchemaProperty = { type?: string | string[]; enum?: unknown[]; description?: string;
  title?: string; unit?: string; items?: SchemaProperty; properties?: Record<string, SchemaProperty> };
export type Definition = { properties?: Record<string, SchemaProperty>; required?: string[] };
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

/** One value, rendered as richly as its shape allows: primitives inline, arrays of
 * primitives as chips, arrays of objects as a nested table (columns = union of keys),
 * objects as a nested key/value table. */
function Value({ value, property, depth = 0 }: { value: unknown; property?: SchemaProperty; depth?: number }) {
  if (value === undefined || value === null || value === "") return <span className="knowledge-kv-empty">—</span>;
  if (typeof value === "boolean") return <span className={`knowledge-kv-bool is-${value}`}>{value ? "✓ true" : "✗ false"}</span>;
  if (typeof value === "number") return <span className="knowledge-kv-number">{formatCell(value)}</span>;
  if (typeof value === "string") return <span className="knowledge-kv-text">{value}</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="knowledge-kv-empty">{t("empty list")}</span>;
    if (value.every(isPrimitive)) return <span className="knowledge-chips">
      {value.map((item, index) => <span key={index} className="knowledge-chip">{formatCell(item)}</span>)}</span>;
    if (value.every(isObject) && depth < 3) {
      const columns = Array.from(new Set(value.flatMap(item => Object.keys(item as object))));
      const itemProperties = property?.items?.properties ?? {};
      return <div className="knowledge-kv-scroll"><table className="knowledge-kv-table knowledge-kv-nested">
        <thead><tr><th scope="col">#</th>{columns.map(column => <th key={column} scope="col">{propertyLabel(column, itemProperties[column])}</th>)}</tr></thead>
        <tbody>{(value as Record<string, unknown>[]).map((item, index) => <tr key={index}>
          <td className="knowledge-kv-index">{index + 1}</td>
          {columns.map(column => <td key={column}><Value value={item[column]} property={itemProperties[column]} depth={depth + 1} /></td>)}
        </tr>)}</tbody>
      </table></div>;
    }
    return <pre className="knowledge-kv-json">{JSON.stringify(value, null, 2)}</pre>;
  }
  if (isObject(value) && depth < 3) {
    const entries = Object.entries(value);
    if (!entries.length) return <span className="knowledge-kv-empty">{t("empty")}</span>;
    const nested = property?.properties ?? {};
    return <table className="knowledge-kv-table knowledge-kv-nested"><tbody>
      {entries.map(([key, item]) => <tr key={key}>
        <th scope="row">{propertyLabel(key, nested[key])}</th>
        <td><Value value={item} property={nested[key]} depth={depth + 1} /></td>
      </tr>)}
    </tbody></table>;
  }
  return <pre className="knowledge-kv-json">{JSON.stringify(value, null, 2)}</pre>;
}

/** A record's data as a key/value table in schema order, then whatever the record
 * carries that the schema does not name. A required field the record lacks is
 * flagged in place, so the validation message above has somewhere to point. */
export function RecordTable({ definition, data, validation }: {
  definition: Definition | undefined; data: Record<string, unknown>; validation?: Validation;
}) {
  const properties = definition?.properties ?? {};
  const required = new Set(definition?.required ?? []);
  const missing = new Set(validation?.missing ?? []);
  const keys = [...Object.keys(properties), ...Object.keys(data).filter(key => !(key in properties))];
  if (!keys.length) return <p className="knowledge-empty">{t("This record has no data")}</p>;
  return <table className="knowledge-kv-table">
    <tbody>
      {keys.map(key => {
        const property = properties[key];
        const value = data[key];
        const absent = value === undefined || value === null || value === "";
        const flagged = absent && (required.has(key) || missing.has(key));
        return <tr key={key} className={flagged ? "is-missing" : undefined}>
          <th scope="row">
            <span className="knowledge-kv-label">
              <span>{propertyLabel(key, property)}{required.has(key) ? <span className="knowledge-kv-required" title={t("Required")}> *</span> : null}</span>
              {property?.description && <small>{property.description}</small>}
              {!property && <small>{t("Not in the schema")}</small>}
            </span>
          </th>
          <td>{flagged ? <span className="knowledge-kv-missing">{t("missing")}</span> : <Value value={value} property={property} />}</td>
        </tr>;
      })}
    </tbody>
  </table>;
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
