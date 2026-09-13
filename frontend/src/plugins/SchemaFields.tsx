import { t, useLocale } from "../i18n";

interface Property {
  type?: string; title?: string; description?: string; format?: string;
  enum?: (string | number)[]; const?: unknown; default?: unknown;
}

/** Shared scalar schema controls; persistence and error presentation belong to the caller. */
export function SchemaFields({ schema, config, save }: {
  schema?: Record<string, unknown>; config: Record<string, unknown>;
  save(key: string, value: unknown): Promise<void>;
}) {
  useLocale();
  const properties = (schema?.properties ?? {}) as Record<string, Property>;
  const fields = Object.entries(properties).filter(([, field]) => field.const === undefined && ["string", "number", "integer", "boolean"].includes(field.type ?? ""));
  return <>{fields.map(([key, field]) => <label className="field-label" key={key}>
    <span>{t(field.title ?? key)}</span>
    {field.enum ? <select aria-label={t(field.title ?? key)} value={String(config[key] ?? field.default ?? "")}
      onChange={(event) => void save(key, field.enum!.find((value) => String(value) === event.target.value))}>
      {field.enum.map((value) => <option key={String(value)} value={String(value)}>{t(String(value))}</option>)}
    </select> : field.type === "boolean" ? <input type="checkbox" checked={Boolean(config[key] ?? field.default)} onChange={(event) => void save(key, event.target.checked)} />
      : field.format === "textarea" ? <textarea key={String(config[key])} defaultValue={String(config[key] ?? field.default ?? "")} rows={3} onBlur={(event) => void save(key, event.target.value)} />
        : <input key={String(config[key])} type={field.type === "string" ? "text" : "number"}
          defaultValue={String(config[key] ?? field.default ?? "")}
          onBlur={(event) => {
            const value = field.type === "string" ? event.target.value : Number(event.target.value);
            if (typeof value !== "number" || Number.isFinite(value)) void save(key, value);
          }} />}
    {field.description && <small>{t(field.description)}</small>}
  </label>)}</>;
}
