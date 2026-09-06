import { Plus, Trash2 } from "lucide-react";

type SettingKind = "text" | "number" | "boolean" | "group" | "list" | "empty";
export interface SettingRow { id: string; name: string; kind: SettingKind; value: string; children: SettingRow[] }
const newSetting = (name = "", value: unknown = ""): SettingRow => ({
  id: crypto.randomUUID(), name,
  kind: value === null ? "empty" : Array.isArray(value) ? "list" : typeof value === "object" ? "group" : typeof value === "number" ? "number" : typeof value === "boolean" ? "boolean" : "text",
  value: typeof value === "object" ? "" : String(value),
  children: value !== null && typeof value === "object" ? Object.entries(value).map(([key, item]) => newSetting(key, item)) : [],
});
export const settingsFromValue = (value: Record<string, unknown>) => Object.entries(value).map(([name, item]) => newSetting(name, item));

function settingValue(row: SettingRow): unknown {
  if (row.kind === "group") return settingsToValue(row.children);
  if (row.kind === "list") return row.children.map(settingValue);
  if (row.kind === "empty") return null;
  if (row.kind === "boolean") return row.value === "true";
  if (row.kind === "number") {
    if (!row.value.trim() || !Number.isFinite(Number(row.value))) throw new Error(`Enter a number for ${row.name || "this setting"}.`);
    return Number(row.value);
  }
  return row.value;
}
export function settingsToValue(rows: SettingRow[]): Record<string, unknown> {
  const names = new Set<string>();
  return Object.fromEntries(rows.map((row) => {
    const name = row.name.trim();
    if (!name) throw new Error("Give each setting a name or remove the empty row.");
    if (names.has(name)) throw new Error(`The setting “${name}” appears twice.`);
    names.add(name);
    return [name, settingValue(row)];
  }));
}

export function SkillDefaultsEditor({ rows, onChange, list = false, prefix = "Setting" }: {
  rows: SettingRow[]; onChange: (rows: SettingRow[]) => void; list?: boolean; prefix?: string;
}) {
  const patch = (id: string, change: Partial<SettingRow>) => onChange(rows.map((row) => row.id === id ? { ...row, ...change } : row));
  return <div className="skill-defaults-editor">
    {rows.map((row, index) => {
      const label = `${prefix} ${index + 1}`;
      return <div className="skill-setting" key={row.id} role="group" aria-label={list ? label : row.name || label}>
        <div className="skill-setting-heading">
          {list ? <span>Item {index + 1}</span> : <input aria-label={`${label} name`} placeholder="Name, e.g. language" value={row.name} onChange={(event) => patch(row.id, { name: event.target.value })} />}
          <select aria-label={`${label} type`} value={row.kind} onChange={(event) => {
            const kind = event.target.value as SettingKind;
            patch(row.id, { kind, value: kind === "number" ? "0" : kind === "boolean" ? "false" : "", children: [] });
          }}><option value="text">Text</option><option value="number">Number</option><option value="boolean">On / off</option><option value="group">Group</option><option value="list">List</option><option value="empty">Empty</option></select>
          <button type="button" className="skill-file-icon" aria-label={`Remove ${label.toLowerCase()}`} onClick={() => onChange(rows.filter((item) => item.id !== row.id))}><Trash2 size={14} /></button>
        </div>
        {row.kind === "group" || row.kind === "list" ? <SkillDefaultsEditor rows={row.children} list={row.kind === "list"} prefix={label} onChange={(children) => patch(row.id, { children })} />
          : row.kind === "boolean" ? <label className="skill-setting-switch"><input aria-label={`${label} value`} type="checkbox" checked={row.value === "true"} onChange={(event) => patch(row.id, { value: String(event.target.checked) })} />{row.value === "true" ? "On" : "Off"}</label>
          : row.kind === "empty" ? <span className="toolbox-help">No value</span>
          : <input aria-label={`${label} value`} type={row.kind === "number" ? "number" : "text"} step="any" value={row.value} placeholder="Value" onChange={(event) => patch(row.id, { value: event.target.value })} />}
      </div>;
    })}
    <button type="button" className="secondary-button" onClick={() => onChange([...rows, newSetting()])}><Plus size={13} /> {list ? "Add item" : "Add setting"}</button>
  </div>;
}
