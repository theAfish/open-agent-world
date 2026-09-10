import { useEffect, useRef, useState } from "react";
import { Plus, Upload, X } from "lucide-react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import { settingsFromValue, settingsToValue, SkillDefaultsEditor, type SettingRow } from "./SkillDefaultsEditor";
import "./executionConfiguration.css";

type EnvironmentVariableKind = "value" | "secret";
export interface EnvironmentVariableRow { id: number; name: string; kind: EnvironmentVariableKind; value: string }
let environmentRowId = 0;
const newEnvironmentVariable = (): EnvironmentVariableRow => ({ id: ++environmentRowId, name: "", kind: "value", value: "" });

function objectValue(value: unknown, label: string): Record<string, unknown> {
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(`${label} must be a JSON object.`);
  return value as Record<string, unknown>;
}

function requireOnlyKeys(value: Record<string, unknown>, allowed: string[], label: string) {
  const extra = Object.keys(value).filter((key) => !allowed.includes(key));
  if (extra.length) throw new Error(`${label} contains unsupported field${extra.length === 1 ? "" : "s"}: ${extra.join(", ")}.`);
}

export function environmentVariablesFromValue(value: unknown): EnvironmentVariableRow[] {
  const root = objectValue(value, "Configuration");
  requireOnlyKeys(root, ["variables"], "Configuration");
  const variables = objectValue(root.variables ?? {}, "variables");
  return Object.entries(variables).map(([name, item]) => {
    const secret = item && !Array.isArray(item) && typeof item === "object" && typeof (item as Record<string, unknown>).secret_ref === "string";
    if (typeof item !== "string" && !secret) throw new Error(`Variable “${name}” must be a text value or secret reference.`);
    if (secret) requireOnlyKeys(item as Record<string, unknown>, ["secret_ref"], `Secret reference “${name}”`);
    return { id: ++environmentRowId, name, kind: secret ? "secret" : "value", value: secret ? String((item as Record<string, unknown>).secret_ref) : String(item) };
  });
}

export function environmentVariablesToValue(rows: EnvironmentVariableRow[]): Record<string, unknown> {
  const names = new Set<string>();
  const variables = Object.fromEntries(rows.map((row) => {
    const name = row.name.trim();
    if (!name) throw new Error("Give each environment variable a name, or remove the empty row.");
    if (names.has(name)) throw new Error(`The environment variable “${name}” appears twice.`);
    names.add(name);
    const value = row.value.trim();
    if (row.kind === "secret" && !value) throw new Error(`Give “${name}” a secret reference name.`);
    return [name, row.kind === "secret" ? { secret_ref: value } : row.value];
  }));
  return { variables };
}

export function EnvironmentVariablesEditor({ rows, onChange, disabled, secrets, onSecretsChange, bindings }: {
  rows: EnvironmentVariableRow[]; onChange(rows: EnvironmentVariableRow[]): void; disabled: boolean;
  secrets: Record<string, string>; onSecretsChange(secrets: Record<string, string>): void; bindings: Record<string, boolean>;
}) {
  const patch = (id: number, change: Partial<EnvironmentVariableRow>) => onChange(rows.map((row) => row.id === id ? { ...row, ...change } : row));
  return <fieldset className="environment-variables-editor" disabled={disabled}>
    {rows.length === 0 && <p className="variables-empty">No variables.</p>}
    {rows.map((row, index) => <div className="environment-variable-row" key={row.id}>
      <div className="variable-name-row">
        <input aria-label={`Environment variable ${index + 1} name`} value={row.name} placeholder="Name, e.g. REGION"
          onChange={(event) => patch(row.id, { name: event.target.value })} />
        <button type="button" className="variable-remove" aria-label={`Remove environment variable ${index + 1}`}
          onClick={() => onChange(rows.filter((item) => item.id !== row.id))}><X size={14} /></button>
      </div>
      <div className="variable-value-row">
        <select aria-label={`Environment variable ${index + 1} type`} value={row.kind}
          onChange={(event) => patch(row.id, { kind: event.target.value as EnvironmentVariableKind,
            value: event.target.value === "secret" ? crypto.randomUUID() : "" })}>
          <option value="value">Value</option><option value="secret">Secret</option>
        </select>
        <input aria-label={`Environment variable ${index + 1} value`} value={row.kind === "secret" ? secrets[row.value] ?? "" : row.value}
          type={row.kind === "secret" ? "password" : "text"} autoComplete="off"
          placeholder={row.kind === "secret" ? (bindings[row.value] ? "Configured — enter to replace" : "Enter secret") : "Value"}
          onChange={(event) => row.kind === "secret"
            ? onSecretsChange({ ...secrets, [row.value]: event.target.value })
            : patch(row.id, { value: event.target.value })} />
      </div>
      {row.kind === "secret" && <small>{secrets[row.value] ? "Will be saved securely" : bindings[row.value] ? "Configured" : "Enter a secret before saving"}</small>}
    </div>)}
    <button type="button" className="secondary-button" onClick={() => onChange([...rows, newEnvironmentVariable()])}><Plus size={13} /> Add variable</button>
  </fieldset>;
}

export async function saveEnvironmentRows(id: string, rows: EnvironmentVariableRow[], secrets: Record<string, string>, bindings: Record<string, boolean>, revision: number) {
  const value = environmentVariablesToValue(rows);
  const updates: Record<string, string> = {};
  for (const row of rows) {
    if (row.kind !== "secret") continue;
    if (secrets[row.value]) updates[row.value] = secrets[row.value];
    else if (!bindings[row.value]) throw new Error(`Enter a secret for ${row.name}, or remove the unused variable.`);
  }
  return worldApi.saveEnvironment(id, value, updates, revision);
}

/** The same document editor also supports plugin-declared structured target fields. */
export function ExecutionConfigurationBody({ card }: { card: WorldCard }) {
  const definition = useWorldStore((state) => state.catalog.node_types.find((item) => item.id === card.type));
  const environment = definition?.traits.includes("core.environment") ?? false;
  const [document, setDocument] = useState<Awaited<ReturnType<typeof worldApi.getNodeDocument>>>();
  const [environmentRows, setEnvironmentRows] = useState<EnvironmentVariableRow[]>([]);
  const [targetName, setTargetName] = useState("");
  const [providerId, setProviderId] = useState("");
  const [targetRows, setTargetRows] = useState<SettingRow[]>([]);
  const [bindings, setBindings] = useState<Record<string, boolean>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const importInput = useRef<HTMLInputElement>(null);

  function applyValue(value: unknown) {
    const root = objectValue(value, "Configuration");
    if (environment) {
      setEnvironmentRows(environmentVariablesFromValue(root));
      return;
    }
    requireOnlyKeys(root, ["name", "provider_id", "config"], "Configuration");
    if (root.name !== undefined && typeof root.name !== "string") throw new Error("name must be text.");
    if (root.provider_id !== undefined && typeof root.provider_id !== "string") throw new Error("provider_id must be text.");
    const config = objectValue(root.config ?? {}, "config");
    setTargetName(String(root.name ?? ""));
    setProviderId(String(root.provider_id ?? ""));
    setTargetRows(settingsFromValue(config));
  }

  async function refreshBindings() { if (environment) setBindings(await worldApi.getCredentialBindings(card.id)); }
  async function reload() {
    setSecrets({});
    setBusy(true);
    try {
      const next = await worldApi.getNodeDocument(card.id);
      applyValue(next.value); setDocument(next);
      await refreshBindings(); setError(""); setNotice("");
    } catch (reason) { setError(apiErrorMessage(reason)); }
    finally { setBusy(false); }
  }
  useEffect(() => { void reload(); }, [card.id]);

  async function importJson(file: File | undefined) {
    if (!file) return;
    setError(""); setNotice("");
    try {
      applyValue(JSON.parse(await file.text()));
      setNotice(`Imported ${file.name}. Save to apply.`);
    } catch (reason) { setError(apiErrorMessage(reason)); }
    finally { if (importInput.current) importInput.current.value = ""; }
  }

  async function save() {
    if (!document) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const value = environment ? environmentVariablesToValue(environmentRows) : {
        name: targetName, provider_id: providerId, config: settingsToValue(targetRows),
      };
      const next = environment
        ? await saveEnvironmentRows(card.id, environmentRows, secrets, bindings, document.revision)
        : await worldApi.nodeDocumentAction(card.id, "replace", value, document.revision);
      setSecrets({});
      applyValue(next.value); setDocument(next); await refreshBindings(); setNotice("Configuration saved.");
    } catch (reason) { setError(apiErrorMessage(reason)); }
    finally { setBusy(false); }
  }

  return <div className="expanded-stack execution-config">
    <section className="card-section">
      <div className="section-heading execution-config-heading">
        <span>{environment ? "Environment variables" : "Destination configuration"}</span>
        <button type="button" className="secondary-button compact-button" disabled={busy} onClick={() => importInput.current?.click()}><Upload size={13} /> Import JSON</button>
        <input ref={importInput} className="execution-config-file" type="file" accept="application/json,.json"
          aria-label="Import execution configuration JSON" onChange={(event) => void importJson(event.target.files?.[0])} />
      </div>
      {environment ? <>
        <EnvironmentVariablesEditor rows={environmentRows} onChange={setEnvironmentRows} disabled={busy || !document}
          secrets={secrets} onSecretsChange={setSecrets} bindings={bindings} />
      </> : <>
        <div className="execution-target-fields">
          <label className="field-label"><span>Name</span><input value={targetName} disabled={busy || !document} placeholder="Display name"
            onChange={(event) => setTargetName(event.target.value)} /></label>
          <label className="field-label"><span>Provider ID</span><input value={providerId} disabled={busy || !document} placeholder="Provider identifier"
            onChange={(event) => setProviderId(event.target.value)} /></label>
        </div>
        <fieldset className="execution-target-settings" disabled={busy || !document}>
          <legend>Provider settings</legend>
          <SkillDefaultsEditor rows={targetRows} onChange={setTargetRows} />
        </fieldset>
      </>}
      <details className="execution-config-help">
        <summary>Usage details</summary>
        <p>Select this card for each command. Connecting or equipping it grants access.</p>
        <p>{environment
          ? "Choose Secret and enter the credential. Save stores it securely for commands. Leave a configured secret blank to keep it."
          : "Keep credentials in an Environment Profile. Add provider options as named settings."}</p>
      </details>
      <div className="editor-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={() => void reload()}>Reload</button>
        <button type="button" className="primary-button" disabled={busy || !document} onClick={() => void save()}>Save</button>
      </div>
      {notice && <p className="execution-config-notice" role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  </div>;
}
