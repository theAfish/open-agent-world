import { t } from "@oaw/plugin-api";
import { useState } from "react";
import type { ReactNode } from "react";

type Property = { type?: string | string[]; enum?: unknown[]; description?: string; title?: string };

const pretty = (value: unknown) => JSON.stringify(value, null, 2);

const propertyTypes = (property: Property) => {
  const value = property.type;
  return new Set(Array.isArray(value) ? value : value ? [value] : []);
};

/** One input per schema property, typed from the JSON Schema; anything the form
 * cannot type (objects, arrays, untyped fields) is edited as JSON in place. The raw
 * JSON toggle beside the form stays the escape hatch for everything else. */
export function RecordForm({ definition, value, disabled, onChange }: {
  definition: Record<string, unknown>; value: Record<string, unknown>; disabled: boolean;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const properties = (definition.properties ?? {}) as Record<string, Property>;
  const required = new Set((definition.required as string[] | undefined) ?? []);
  const [jsonDrafts, setJsonDrafts] = useState<Record<string, string>>({});
  const [jsonErrors, setJsonErrors] = useState<Record<string, boolean>>({});
  const set = (key: string, next: unknown) => {
    const copy = { ...value };
    if (next === undefined) delete copy[key]; else copy[key] = next;
    onChange(copy);
  };
  const extra = Object.keys(value).filter(key => !(key in properties));
  const jsonField = (key: string, label: ReactNode) => {
    const text = jsonDrafts[key] ?? pretty(value[key] ?? null);
    return <label key={key} className="knowledge-record-field">{label}
      <textarea value={text} disabled={disabled} rows={3} spellCheck={false}
        aria-invalid={jsonErrors[key] || undefined}
        onChange={event => {
          const next = event.target.value;
          setJsonDrafts({ ...jsonDrafts, [key]: next });
          try { set(key, next.trim() ? JSON.parse(next) : undefined); setJsonErrors({ ...jsonErrors, [key]: false }); }
          catch { setJsonErrors({ ...jsonErrors, [key]: true }); }
        }} />
      {jsonErrors[key] && <small className="knowledge-error">{t("Not valid JSON")}</small>}
    </label>;
  };
  return <div className="knowledge-record-form">
    {Object.entries(properties).map(([key, property]) => {
      const types = propertyTypes(property);
      const label = <span className="knowledge-record-label">
        <span>{property.title || key}{required.has(key) ? " *" : ""}</span>
        {property.description && <small>{property.description}</small>}
      </span>;
      const current = value[key];
      if (property.enum) {
        return <label key={key} className="knowledge-record-field">{label}
          <select value={current === undefined ? "" : String(current)} disabled={disabled}
            onChange={event => set(key, event.target.value === "" ? undefined
              : property.enum!.find(option => String(option) === event.target.value))}>
            <option value="">—</option>
            {property.enum.map(option => <option key={String(option)} value={String(option)}>{String(option)}</option>)}
          </select></label>;
      }
      if (types.has("boolean") && types.size <= 2) {
        return <label key={key} className="knowledge-record-field">{label}
          <select value={current === undefined || current === null ? "" : String(current)} disabled={disabled}
            onChange={event => set(key, event.target.value === "" ? undefined : event.target.value === "true")}>
            <option value="">—</option><option value="true">true</option><option value="false">false</option>
          </select></label>;
      }
      if ((types.has("number") || types.has("integer")) && !types.has("string") && !types.has("object") && !types.has("array")) {
        return <label key={key} className="knowledge-record-field">{label}
          <input type="number" step={types.has("integer") ? 1 : "any"} disabled={disabled}
            value={typeof current === "number" ? current : ""}
            onChange={event => set(key, event.target.value === "" ? undefined
              : types.has("integer") ? parseInt(event.target.value, 10) : Number(event.target.value))} /></label>;
      }
      if (types.has("string") && !types.has("object") && !types.has("array")) {
        return <label key={key} className="knowledge-record-field">{label}
          <input type="text" disabled={disabled} value={typeof current === "string" ? current : current === undefined || current === null ? "" : String(current)}
            onChange={event => set(key, event.target.value === "" ? undefined : event.target.value)} /></label>;
      }
      return jsonField(key, label);
    })}
    {extra.map(key => jsonField(key, <span className="knowledge-record-label"><span>{key}</span>
      <small>{t("Not in the schema")}</small></span>))}
  </div>;
}
