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

function CredentialBinding({ id, reference, configured, revision, changed }: {
  id: string; reference: string; configured: boolean; revision: number; changed(): Promise<void>;
}) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function bind(secret: string | null) {
    setBusy(true); setError("");
    // Credentials live only in this input until submission, never in saved UI drafts.
    setValue("");
    try { await worldApi.bindCredential(id, reference, secret, revision); await changed(); }
    catch (reason) { setError(apiErrorMessage(reason)); }
    finally { setBusy(false); }
  }
  return <div className="card-section">
    <label className="field-label"><span>{reference} · {configured ? "Configured" : "Unbound"}</span>
      <input aria-label={`Secret for ${reference}`} type="password" autoComplete="new-password" value={value}
        onChange={(event) => setValue(event.target.value)} disabled={busy} />
    </label>
    <div className="editor-actions">
      <button type="button" className="primary-button" disabled={busy || !value} onClick={() => void bind(value)}>Bind secret</button>
      <button type="button" className="secondary-button" disabled={busy || !configured} onClick={() => void bind(null)}>Unbind</button>
    </div>
    {error && <p role="alert">{error}</p>}
  </div>;
}

function EnvironmentVariablesEditor({ rows, onChange, disabled }: {
  rows: EnvironmentVariableRow[]; onChange(rows: EnvironmentVariableRow[]): void; disabled: boolean;
}) {
  const patch = (id: number, change: Partial<EnvironmentVariableRow>) => onChange(rows.map((row) => row.id === id ? { ...row, ...change } : row));
  return <fieldset className="environment-variables-editor" disabled={disabled}>
    {rows.length === 0 && <p className="variables-empty">No variables yet. Add a normal value or a reference to a private credential.</p>}
    {rows.map((row, index) => <div className="environment-variable-row" key={row.id}>
      <div className="variable-name-row">
        <input aria-label={`Environment variable ${index + 1} name`} value={row.name} placeholder="Variable name, e.g. REGION"
          onChange={(event) => patch(row.id, { name: event.target.value })} />
        <button type="button" className="variable-remove" aria-label={`Remove environment variable ${index + 1}`}
          onClick={() => onChange(rows.filter((item) => item.id !== row.id))}><X size={14} /></button>
      </div>
      <div className="variable-value-row">
        <select aria-label={`Environment variable ${index + 1} type`} value={row.kind}
          onChange={(event) => patch(row.id, { kind: event.target.value as EnvironmentVariableKind })}>
          <option value="value">Value</option><option value="secret">Secret reference</option>
        </select>
        <input aria-label={`Environment variable ${index + 1} value`} value={row.value}
          placeholder={row.kind === "secret" ? "Reference name, e.g. api-token" : "Value"}
          onChange={(event) => patch(row.id, { value: event.target.value })} />
      </div>
    </div>)}
    <button type="button" className="secondary-button" onClick={() => onChange([...rows, newEnvironmentVariable()])}><Plus size={13} /> Add variable</button>
  </fieldset>;
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
      setNotice(`Imported ${file.name}. Review the fields, then save.`);
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
      const next = await worldApi.nodeDocumentAction(card.id, "replace", value, document.revision);
      applyValue(next.value); setDocument(next); await refreshBindings(); setNotice("Configuration saved.");
    } catch (reason) { setError(apiErrorMessage(reason)); }
    finally { setBusy(false); }
  }

  return <div className="expanded-stack execution-config nodrag nopan">
    <section className="card-section">
      <div className="section-heading execution-config-heading">
        <span>{environment ? "Environment variables" : "Destination configuration"}</span>
        <button type="button" className="secondary-button compact-button" disabled={busy} onClick={() => importInput.current?.click()}><Upload size={13} /> Import JSON</button>
        <input ref={importInput} className="execution-config-file" type="file" accept="application/json,.json"
          aria-label="Import execution configuration JSON" onChange={(event) => void importJson(event.target.files?.[0])} />
      </div>
      <p>Select this card explicitly for each command. Connecting or equipping it grants access.</p>
      {environment ? <>
        <p>Add ordinary values here. For sensitive values, choose Secret reference, save, then bind the credential below.</p>
        <EnvironmentVariablesEditor rows={environmentRows} onChange={setEnvironmentRows} disabled={busy || !document} />
      </> : <>
        <p>Authentication belongs in an Environment Profile. Add provider-specific options as named settings.</p>
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
      <div className="editor-actions">
        <button type="button" className="primary-button" disabled={busy || !document} onClick={() => void save()}>Save configuration</button>
        <button type="button" className="secondary-button" disabled={busy} onClick={() => void reload()}>Reload</button>
      </div>
      {notice && <p className="execution-config-notice" role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
    {environment && <section className="card-section">
      <div className="section-heading"><span>Private credential bindings</span></div>
      <p>Bindings belong to this card on this host. Copies require rebinding. Command code can read injected secrets.</p>
      {Object.entries(bindings).map(([reference, configured]) => <CredentialBinding key={`${card.id}:${reference}`}
        id={card.id} reference={reference} configured={configured} revision={document?.revision ?? 0} changed={refreshBindings} />)}
    </section>}
  </div>;
}
