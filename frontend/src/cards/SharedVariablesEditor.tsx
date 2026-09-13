import { t, useLocale } from "../i18n";
import { Plus, X } from "lucide-react";

export type VariableType = "text" | "number" | "boolean" | "json";
export interface VariableRow { id: number; name: string; type: VariableType; value: string }
let rowId = 0;
export const newVariable = (): VariableRow => ({ id: ++rowId, name: "", type: "text", value: "" });

export function variablesFromValue(value: Record<string, unknown>): VariableRow[] {
  return Object.entries(value).map(([name, item]) => ({
    id: ++rowId, name,
    type: typeof item === "string" ? "text" : typeof item === "number" ? "number" : typeof item === "boolean" ? "boolean" : "json",
    value: typeof item === "string" ? item : JSON.stringify(item),
  }));
}

export function variablesToValue(rows: VariableRow[]): Record<string, unknown> {
  const names = new Set<string>();
  return Object.fromEntries(rows.map((row) => {
    const name = row.name.trim();
    if (!name) throw new Error(t("Give each variable a name, or remove the empty row."));
    if (names.has(name)) throw new Error(t("The variable “{v0}” appears twice. Use a different name.", { v0: String(name) }));
    names.add(name);
    let value: unknown = row.value;
    if (row.type === "number") {
      value = Number(row.value);
      if (!row.value.trim() || !Number.isFinite(value)) throw new Error(t("Enter a valid number for “{v0}”.", { v0: String(name) }));
    } else if (row.type === "boolean") value = row.value === "true";
    else if (row.type === "json") {
      try { value = JSON.parse(row.value); }
      catch { throw new Error(t("Check the JSON value for “{v0}”.", { v0: String(name) })); }
    }
    return [name, value];
  }));
}

export function SharedVariablesEditor({ rows, onChange, disabled }: {
  rows: VariableRow[]; onChange: (rows: VariableRow[]) => void; disabled: boolean;
}) {
  useLocale();
  const patch = (id: number, change: Partial<VariableRow>) => onChange(rows.map((row) => row.id === id ? { ...row, ...change } : row));
  return <fieldset className="shared-variables-editor" disabled={disabled}>
    <legend>{t("Shared variables")}</legend>
    <p className="legion-help">{t("Values all members can use, such as a goal, language or iteration limit.")}</p>
    {rows.length === 0 && <p className="variables-empty">{t("No variables yet. Add only what your team needs.")}</p>}
    {rows.map((row, index) => <div className="shared-variable-row" key={row.id}>
      <div className="variable-name-row"><input aria-label={t("Variable {v0} name", { v0: String(index + 1) })} value={row.name} placeholder={t("Variable name, e.g. goal")}
        onChange={(event) => patch(row.id, { name: event.target.value })} />
        <button type="button" className="variable-remove" aria-label={t("Remove variable {v0}", { v0: String(index + 1) })} onClick={() => onChange(rows.filter((item) => item.id !== row.id))}><X size={14} /></button></div>
      <div className="variable-value-row"><select aria-label={t("Variable {v0} type", { v0: String(index + 1) })} value={row.type} onChange={(event) => {
        const type = event.target.value as VariableType;
        patch(row.id, { type, value: type === "boolean" ? "false" : type === "number" ? "0" : type === "json" ? JSON.stringify(row.value) : row.value });
      }}><option value="text">{t("Text")}</option><option value="number">{t("Number")}</option><option value="boolean">{t("On / off")}</option><option value="json">{t("JSON")}</option></select>
      {row.type === "boolean" ? <select aria-label={t("Variable {v0} value", { v0: String(index + 1) })} value={row.value} onChange={(event) => patch(row.id, { value: event.target.value })}>
        <option value="true">{t("On")}</option><option value="false">{t("Off")}</option></select>
        : <textarea rows={row.type === "json" ? 3 : 2} aria-label={t("Variable {v0} value", { v0: String(index + 1) })} value={row.value} placeholder={row.type === "number" ? "10" : row.type === "json" ? '["first", "second"]' : t("Value")}
          onChange={(event) => patch(row.id, { value: event.target.value })} />}</div>
    </div>)}
    <button type="button" className="secondary-button" onClick={() => onChange([...rows, newVariable()])}><Plus size={13} /> {t("Add variable")}</button>
  </fieldset>;
}
