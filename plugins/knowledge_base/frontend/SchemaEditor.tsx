import { t, WorkspaceEmptyState } from "@oaw/plugin-api";
import { useState } from "react";
import { Plus } from "lucide-react";

export type SchemaItem = { id: string; name: string; description: string | null; domain: string;
  kind: "literature" | "experiment"; version: number };
export type SchemaDetail = SchemaItem & { definition: Record<string, unknown>; system_prompt: string;
  field_descriptions: Record<string, unknown> | null };
type Kind = "literature" | "experiment";
type Editor = { id: string; name: string; description: string; system_prompt: string; definition: string; kind: Kind };

const pretty = (value: unknown) => JSON.stringify(value, null, 2);

/** A starting point for a record schema: one specimen's fields, typed. */
const TEMPLATE = { type: "object", required: ["name"], properties: {
  name: { type: "string", description: "The specimen, material or sample" },
  value: { type: "number", description: "The measured value" },
  unit: { type: "string" } } };

type Props = {
  busy: boolean;
  /** The schemas this section manages — one kind's, so each section stays in its own lane. */
  schemas: SchemaItem[]; kind: Kind;
  call: (action: string, args?: Record<string, unknown>) => Promise<Record<string, unknown>>;
  perform: (work: () => Promise<void>) => Promise<void>;
  /** Called with the saved schema so the parent refreshes its lists and caches. */
  onSaved: (schema: SchemaItem) => Promise<void> | void;
  /** When set, the toolbar is the parent's: only the "New schema" button is rendered here. */
  compact?: boolean;
};

/** Experiment record fields. General project extraction is configured in Spaces. */
export function SchemaEditor({ busy, schemas, kind, call, perform, onSaved, compact }: Props) {
  const [editor, setEditor] = useState<Editor>();

  const create = () => setEditor({ id: "", name: "", description: "", system_prompt: "", kind, definition: pretty(TEMPLATE) });

  const open = (id: string) => perform(async () => {
    const detail = (await call("schemas", { operation: "get", schema_id: id })).schema as SchemaDetail;
    setEditor({ id: detail.id, name: detail.name, description: detail.description ?? "",
      system_prompt: detail.system_prompt, definition: pretty(detail.definition), kind: detail.kind });
  });

  const save = () => editor && perform(async () => {
    let definition: unknown;
    try { definition = JSON.parse(editor.definition); }
    catch { throw new Error(t("The schema definition must be valid JSON")); }
    const payload = { name: editor.name, description: editor.description || null,
      system_prompt: editor.system_prompt, definition, kind: editor.kind };
    const result = editor.id
      ? await call("schema_manage", { operation: "update", schema_id: editor.id, ...payload })
      : await call("schema_manage", { operation: "create", ...payload });
    setEditor(undefined);
    await onSaved(result.schema as SchemaItem);
  });

  return <div className="knowledge-schema-editor">
    <div className="knowledge-formbar">
      <span className="knowledge-meta">{schemas.length === 1 ? t("1 schema") : t("{v0} schemas", { v0: schemas.length })}</span>
      <button type="button" className={compact ? undefined : "knowledge-primary"} disabled={busy || !!editor} onClick={create}>
        <Plus size={14} />{t("New schema")}</button>
    </div>
    <div className={`knowledge-split${editor ? " has-detail" : ""}`}>
      <div className="knowledge-source-pane">
        {!schemas.length && <WorkspaceEmptyState title={t("No schemas yet")}
          description={kind === "experiment"
            ? t("Create a schema to define the fields each specimen record carries.")
            : t("Create a schema to define the structured data you want to read out of your sources.")} />}
        <ul className="knowledge-list knowledge-record-list">
          {schemas.map(item => <li key={item.id}>
            <button type="button" aria-pressed={editor?.id === item.id} disabled={busy} onClick={() => void open(item.id)}>
              <span>{item.name}</span>
              <small>v{item.version} · {item.description || item.domain}</small>
            </button>
          </li>)}
        </ul>
      </div>
      {editor && <div className="knowledge-form">
        <label>{t("Name")}<input aria-label={t("Name")} value={editor.name} disabled={busy}
          onChange={event => setEditor({ ...editor, name: event.target.value })} /></label>
        <label>{t("Description")}<input aria-label={t("Description")} value={editor.description} disabled={busy}
          onChange={event => setEditor({ ...editor, description: event.target.value })} /></label>
        <label>{t("System prompt")}<textarea aria-label={t("System prompt")} value={editor.system_prompt} disabled={busy} rows={3}
          placeholder={t("What the model should read out, in the words of your field…")}
          onChange={event => setEditor({ ...editor, system_prompt: event.target.value })} /></label>
        <label>{t("JSON Schema definition")}<textarea aria-label={t("JSON Schema definition")} value={editor.definition} disabled={busy} rows={8} spellCheck={false}
          onChange={event => setEditor({ ...editor, definition: event.target.value })} /></label>
        <div className="knowledge-formbar">
          <button type="button" className="knowledge-primary" disabled={busy || !editor.name || !editor.system_prompt}
            onClick={() => void save()}>{editor.id ? t("Save schema") : t("Create schema")}</button>
          <button type="button" disabled={busy} onClick={() => setEditor(undefined)}>{t("Cancel")}</button>
        </div>
      </div>}
    </div>
  </div>;
}
