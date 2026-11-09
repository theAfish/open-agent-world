import { t } from "@oaw/plugin-api";
import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { DraftInput } from "./DraftInput";
import { RecordForm } from "./RecordForm";
import { SchemaNode } from "./SchemaNode";
import {
  KINDS, addProperty, choicesText, isRequired, isValidKey, kindOf, moveProperty, parseDefinition, propertiesOf,
  removeProperty, renameProperty, setChoices, setKind, setProperty, setRequired, setText, textOf,
} from "./schemaModel";
import type { Definition, Kind, Prop } from "./schemaModel";

const pretty = (value: unknown) => JSON.stringify(value, null, 2);

const KIND_LABELS: Record<Kind, () => string> = {
  string: () => t("Text"), number: () => t("Number"), integer: () => t("Integer"), boolean: () => t("Yes/No"),
  "number[]": () => t("List of numbers"), "string[]": () => t("List of text"), choice: () => t("One of…"),
  custom: () => t("Custom (JSON)"),
};

type RowProps = {
  definition: Definition; name: string; prop: Prop; first: boolean; last: boolean; disabled: boolean;
  update: (next: Definition) => void;
};

function PropertyRow({ definition, name, prop, first, last, disabled, update }: RowProps) {
  const kind = kindOf(prop);
  const change = (next: Prop) => update(setProperty(definition, name, next));
  const rename = (next: string) => {
    if (next === name) return;
    if (!isValidKey(next)) return t("Names start with a letter or underscore and use only letters, digits and underscores");
    if (next in propertiesOf(definition)) return t("A property named {v0} already exists", { v0: next });
    update(renameProperty(definition, name, next));
  };
  const actions = <span className="knowledge-property-actions">
    <button type="button" aria-label={t("Move {v0} up", { v0: name })} disabled={disabled || first}
      onClick={() => update(moveProperty(definition, name, -1))}><ArrowUp size={14} /></button>
    <button type="button" aria-label={t("Move {v0} down", { v0: name })} disabled={disabled || last}
      onClick={() => update(moveProperty(definition, name, 1))}><ArrowDown size={14} /></button>
    <button type="button" aria-label={t("Remove property {v0}", { v0: name })} disabled={disabled}
      onClick={() => update(removeProperty(definition, name))}><Trash2 size={14} /></button>
  </span>;
  const required = <label className="knowledge-check"><input type="checkbox" aria-label={t("Required")}
    checked={isRequired(definition, name)} disabled={disabled}
    onChange={event => update(setRequired(definition, name, event.target.checked))} />{t("Required")}</label>;
  const nameInput = <DraftInput label={t("Name")} placeholder={t("Name")} value={name} disabled={disabled} onCommit={rename} />;

  if (kind === "custom") {
    return <li className="knowledge-property" aria-label={name}>
      <div className="knowledge-property-main is-custom">
        {nameInput}
        <span />
        {required}{actions}
      </div>
      <SchemaNode node={prop} disabled={disabled} onChange={change} />
    </li>;
  }
  return <li className="knowledge-property" aria-label={name}>
    <div className="knowledge-property-main">
      {nameInput}
      <select aria-label={t("Value type")} value={kind} disabled={disabled}
        onChange={event => change(setKind(prop, event.target.value as Exclude<Kind, "custom">))}>
        {KINDS.map(option => <option key={option} value={option}>{KIND_LABELS[option]()}</option>)}
      </select>
      <DraftInput label={t("Unit")} placeholder={t("Unit")} value={textOf(prop, "unit")} disabled={disabled}
        onCommit={text => { change(setText(prop, "unit", text.trim())); }} />
      {required}{actions}
    </div>
    {kind === "choice" && <DraftInput label={t("Allowed values (comma-separated)")} placeholder={t("Allowed values (comma-separated)")}
      value={choicesText(prop)} disabled={disabled} onCommit={text => { change(setChoices(prop, text)); }} />}
    <input aria-label={t("Description")} placeholder={t("Description")} value={textOf(prop, "description")}
      disabled={disabled} onChange={event => change(setText(prop, "description", event.target.value))} />
  </li>;
}

type Props = {
  /** The definition as JSON text — the single source of truth shared by the form and the JSON view. */
  value: string; onChange: (text: string) => void; disabled: boolean;
};

/** Edits a record schema as a list of properties — each one a value / unit / description entry —
 * with the raw JSON as a fallback view. */
export function PropertyBuilder({ value, onChange, disabled }: Props) {
  const [mode, setMode] = useState<"form" | "json">("form");
  const [error, setError] = useState<string>();
  const [preview, setPreview] = useState<Record<string, unknown>>({});
  const parsed = useMemo(() => parseDefinition(value), [value]);
  const definition = "definition" in parsed ? parsed.definition : undefined;
  const showForm = mode === "form" && !!definition;
  const update = (next: Definition) => onChange(pretty(next));

  const showJson = () => { setError(undefined); setMode("json"); };
  const tryForm = () => {
    if (definition) { setError(undefined); setMode("form"); return; }
    setError("error" in parsed && parsed.error === "json"
      ? t("The JSON is not valid, so the form cannot show it")
      : t("The definition must be an object with a properties map"));
  };

  const entries = definition ? Object.entries(propertiesOf(definition)) : [];
  return <div className="knowledge-property-builder">
    <div className="knowledge-formbar">
      <strong>{t("Properties")}</strong>
      <span className="knowledge-segmented" role="group" aria-label={t("Schema editor view")}>
        <button type="button" aria-pressed={showForm} disabled={disabled} onClick={tryForm}>{t("Form")}</button>
        <button type="button" aria-pressed={!showForm} disabled={disabled} onClick={showJson}>{t("JSON")}</button>
      </span>
    </div>
    {error && <small className="knowledge-error" role="alert">{error}</small>}
    {showForm && definition ? <>
      {!entries.length && <small className="knowledge-meta">{t("No properties yet. Add the first field each entry needs.")}</small>}
      <ul className="knowledge-property-list">
        {entries.map(([name, prop], index) => <PropertyRow key={name} definition={definition} name={name} prop={prop}
          first={index === 0} last={index === entries.length - 1} disabled={disabled} update={update} />)}
      </ul>
      <div className="knowledge-formbar">
        <button type="button" disabled={disabled} onClick={() => update(addProperty(definition))}>
          <Plus size={14} />{t("Add property")}</button>
      </div>
      <details className="knowledge-property-preview"><summary>{t("Entry preview")}</summary>
        <RecordForm definition={definition} value={preview} disabled={false} onChange={setPreview} />
      </details>
    </> : <label>{t("Schema definition (JSON)")}
      <textarea aria-label={t("Schema definition (JSON)")} value={value} disabled={disabled} rows={12} spellCheck={false}
        onChange={event => onChange(event.target.value)} /></label>}
  </div>;
}
