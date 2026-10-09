import { t, useLocale } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { Plus, Upload, X } from "lucide-react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { surfaceDraftKey, useSurfaceDraft, useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useHydrationLease } from '../canvas/useCardRendering';
import type { WorldCard } from "../types/world";
import { settingsFromValue, settingsToValue, SkillDefaultsEditor, type SettingRow } from "./SkillDefaultsEditor";
import "./executionConfiguration.css";
import { FolderPathInput } from "../shell/FolderPathInput";
import type { EnvironmentFolderBinding } from "../api/client";

type EnvironmentVariableKind = "value" | "secret" | "path";
export interface EnvironmentVariableRow { id: number; name: string; kind: EnvironmentVariableKind; value: string; folderPath?: string; access?: "read_only" | "read_write" }
let environmentRowId = 0;
const NO_SECRETS: Record<string, string> = {};
const newEnvironmentVariable = (): EnvironmentVariableRow => ({ id: ++environmentRowId, name: "", kind: "value", value: "" });

function objectValue(value: unknown, label: string): Record<string, unknown> {
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(t("{v0} must be a JSON object.", { v0: String(label) }));
  return value as Record<string, unknown>;
}

function requireOnlyKeys(value: Record<string, unknown>, allowed: string[], label: string) {
  const extra = Object.keys(value).filter((key) => !allowed.includes(key));
  if (extra.length) throw new Error(t("{v0} contains unsupported field{v1}: {v2}.", { v0: String(label), v1: String(extra.length === 1 ? "" : "s"), v2: String(extra.join(", ")) }));
}

export function environmentVariablesFromValue(value: unknown, folders: Record<string, EnvironmentFolderBinding> = {}): EnvironmentVariableRow[] {
  const root = objectValue(value, t("Configuration"));
  requireOnlyKeys(root, ["variables"], t("Configuration"));
  const variables = objectValue(root.variables ?? {}, "variables");
  return Object.entries(variables).map(([name, item]) => {
    const object = item && !Array.isArray(item) && typeof item === "object" ? item as Record<string, unknown> : undefined;
    const pathKind = typeof object?.path_ref === "string" ? "path" : typeof object?.folder_ref === "string" ? "folder" : typeof object?.file_ref === "string" ? "file" : undefined;
    if (pathKind) {
      requireOnlyKeys(object!, [`${pathKind}_ref`], t("Path (file or folder)"));
      const reference = String(object![`${pathKind}_ref`]);
      return { id: ++environmentRowId, name, kind: "path", value: reference,
        folderPath: folders[reference]?.path ?? "", access: folders[reference]?.access ?? "read_only" };
    }
    const secret = item && !Array.isArray(item) && typeof item === "object" && typeof (item as Record<string, unknown>).secret_ref === "string";
    if (typeof item !== "string" && !secret) throw new Error(t("Variable “{v0}” must be a text value or secret reference.", { v0: String(name) }));
    if (secret) requireOnlyKeys(item as Record<string, unknown>, ["secret_ref"], t("Secret reference “{v0}”", { v0: String(name) }));
    return { id: ++environmentRowId, name, kind: secret ? "secret" : "value", value: secret ? String((item as Record<string, unknown>).secret_ref) : String(item) };
  });
}

export function environmentVariablesToValue(rows: EnvironmentVariableRow[]): Record<string, unknown> {
  const names = new Set<string>();
  const variables = Object.fromEntries(rows.map((row) => {
    const name = row.name.trim();
    if (!name) throw new Error(t("Give each environment variable a name, or remove the empty row."));
    if (names.has(name.toUpperCase())) throw new Error(t("The environment variable “{v0}” appears twice.", { v0: String(name) }));
    names.add(name.toUpperCase());
    const value = row.value.trim();
    if (row.kind === "secret" && !value) throw new Error(t("Give “{v0}” a secret reference name.", { v0: String(name) }));
    return [name, row.kind === "path" ? { [`${row.kind}_ref`]: value } : row.kind === "secret" ? { secret_ref: value } : row.value];
  }));
  return { variables };
}

function EnvironmentPathInput({ value, onChange, disabled, label, access }: {
  value: string; onChange(value: string): void; disabled: boolean; label: string; access: "read_only" | "read_write";
}) {
  const [inspection, setInspection] = useState<{ source: string; kind?: "folder" | "file"; error?: string }>();
  useEffect(() => {
    if (!value.trim()) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void worldApi.inspectEnvironmentPath(value.trim(), controller.signal).then(
        result => { if (!controller.signal.aborted) setInspection({ source: value, kind: result.kind }); },
        error => { if (!controller.signal.aborted) setInspection({ source: value, error: apiErrorMessage(error) }); },
      );
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [value]);
  const current = inspection?.source === value ? inspection : undefined;
  return <>
    <FolderPathInput kind="path" value={value} disabled={disabled} onChange={onChange} label={label} placeholder={t("Choose a file or folder on this host")} />
    {value.trim() && current?.error && <small role="alert" className="sandbox-error">{current.error}</small>}
    <small>{access === "read_only" ? t("Read only allows reading and copying into the working folder. Source files cannot be changed.")
      : current?.kind === "file" ? t("Commands can read and change this file's contents. To rename or replace files, choose their folder.")
      : current?.kind === "folder" ? t("Commands can create, change and delete files in this folder.")
      : t("Read and write applies to the selected file or folder.")} {t("Paths are converted automatically for the Sandbox runtime.")}</small>
  </>;
}

export function EnvironmentVariablesEditor({ rows, onChange, disabled, secrets, onSecretsChange, bindings, allowSecrets = true }: {
  rows: EnvironmentVariableRow[]; onChange(rows: EnvironmentVariableRow[]): void; disabled: boolean;
  secrets: Record<string, string>; onSecretsChange(secrets: Record<string, string>): void; bindings: Record<string, boolean>;
  allowSecrets?: boolean;
}) {
  useLocale();
  const latestRows = useRef(rows);
  const latestOnChange = useRef(onChange);
  latestRows.current = rows;
  latestOnChange.current = onChange;
  const patch = (id: number, change: Partial<EnvironmentVariableRow>) => {
    if (!latestRows.current.some(row => row.id === id)) return;
    latestOnChange.current(latestRows.current.map((row) => row.id === id ? { ...row, ...change } : row));
  };
  return <fieldset className="environment-variables-editor" disabled={disabled}>
    {rows.length === 0 && <p className="variables-empty">{t("No variables.")}</p>}
    {rows.map((row, index) => <div className="environment-variable-row" key={row.id}>
      <div className="variable-name-row">
        <input aria-label={t("Environment variable {v0} name", { v0: String(index + 1) })} value={row.name} placeholder={t("Name, e.g. REGION")}
          onChange={(event) => patch(row.id, { name: event.target.value })} />
        <button type="button" className="variable-remove" aria-label={t("Remove environment variable {v0}", { v0: String(index + 1) })}
          onClick={() => onChange(rows.filter((item) => item.id !== row.id))}><X size={14} /></button>
      </div>
      <div className="variable-value-row">
        <select aria-label={t("Environment variable {v0} type", { v0: String(index + 1) })} value={row.kind}
          onChange={(event) => patch(row.id, { kind: event.target.value as EnvironmentVariableKind,
            value: event.target.value === "value" ? "" : crypto.randomUUID(),
            folderPath: event.target.value === "path" && row.kind === "value" ? row.value : "", access: "read_only" })}>
          <option value="value">{t("Value")}</option>{allowSecrets && <option value="secret">{t("Secret")}</option>}<option value="path">{t("Path")}</option>
        </select>
        {row.kind === "path" ? <select className="variable-folder-access" aria-label={t("Environment variable {v0} access", { v0: String(index + 1) })}
          value={row.access ?? "read_only"} onChange={event => patch(row.id, { access: event.target.value as EnvironmentVariableRow["access"] })}>
          <option value="read_only">{t("Read only")}</option><option value="read_write">{t("Read and write")}</option>
        </select> : <input aria-label={t("Environment variable {v0} value", { v0: String(index + 1) })} value={row.kind === "secret" ? secrets[row.value] ?? "" : row.value}
          type={row.kind === "secret" ? "password" : "text"} autoComplete="off"
          placeholder={row.kind === "secret" ? (bindings[row.value] ? t("Configured — enter to replace") : t("Enter secret")) : t("Value")}
          onChange={(event) => row.kind === "secret"
            ? onSecretsChange({ ...secrets, [row.value]: event.target.value })
            : patch(row.id, { value: event.target.value })} />}
      </div>
      {(row.kind === "path") && <>
        <EnvironmentPathInput key={row.value} value={row.folderPath ?? ""} disabled={disabled} onChange={folderPath => patch(row.id, { folderPath })}
          label={t("Environment variable {v0} path", { v0: String(index + 1) })} access={row.access ?? "read_only"} />
      </>}
      {row.kind === "secret" && <small>{secrets[row.value] ? t("Will be saved securely") : bindings[row.value] ? t("Configured") : t("Enter a secret before saving")}</small>}
    </div>)}
    <button type="button" className="secondary-button" onClick={() => onChange([...rows, newEnvironmentVariable()])}><Plus size={13} /> {t("Add variable")}</button>
  </fieldset>;
}

export async function saveEnvironmentRows(id: string, rows: EnvironmentVariableRow[], secrets: Record<string, string>, bindings: Record<string, boolean>, revision: number) {
  const value = environmentVariablesToValue(rows);
  const folders = environmentFolderUpdates(rows);
  return Object.keys(folders).length
    ? worldApi.saveEnvironment(id, value, environmentSecretUpdates(rows, secrets, bindings), revision, folders)
    : worldApi.saveEnvironment(id, value, environmentSecretUpdates(rows, secrets, bindings), revision);
}

export function environmentFolderUpdates(rows: EnvironmentVariableRow[]): Record<string, EnvironmentFolderBinding> {
  return Object.fromEntries(rows.filter(row => row.kind === "path").map(row => {
    if (!row.folderPath?.trim()) throw new Error(t("Choose a file or folder for {v0}, or remove the unused variable.", { v0: row.name }));
    return [row.value, { path: row.folderPath.trim(), access: row.access ?? "read_only" }];
  }));
}

export function environmentSecretUpdates(rows: EnvironmentVariableRow[], secrets: Record<string, string>, bindings: Record<string, boolean>) {
  const updates: Record<string, string> = {};
  for (const row of rows) {
    if (row.kind !== "secret") continue;
    if (secrets[row.value]) updates[row.value] = secrets[row.value];
    else if (!bindings[row.value]) throw new Error(t("Enter a secret for {v0}, or remove the unused variable.", { v0: String(row.name) }));
  }
  return updates;
}

/** The same document editor also supports plugin-declared structured target fields. */
export function ExecutionConfigurationBody({ card }: { card: WorldCard }) {
  useLocale();
  const definition = useWorldStore((state) => state.catalog.node_types.find((item) => item.id === card.type));
  const environment = definition?.traits.includes("core.environment") ?? false;
  const key = (field: string) => surfaceDraftKey(card.id, `execution-${field}`);
  const [document, setDocument] = useSurfaceDraft<Awaited<ReturnType<typeof worldApi.getNodeDocument>> | undefined>(key('document'), undefined);
  const [environmentRows, setEnvironmentRows] = useSurfaceDraft<EnvironmentVariableRow[]>(key('environment'), []);
  const [targetName, setTargetName] = useSurfaceDraft(key('name'), '');
  const [providerId, setProviderId] = useSurfaceDraft(key('provider'), '');
  const [targetRows, setTargetRows] = useSurfaceDraft<SettingRow[]>(key('settings'), []);
  const [dirty, setDirty] = useSurfaceDraft(key('dirty'), false);
  const [bindings, setBindings] = useState<Record<string, boolean>>({});
  const secrets = useNodeSurfaceStore(s => s.privateDrafts[card.id]) ?? NO_SECRETS;
  const setSecrets = (value: Record<string, string>) => useNodeSurfaceStore.getState().setPrivateDraft(card.id, value);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  useHydrationLease(card.id, 'configuration-operation', busy || dirty);
  const importInput = useRef<HTMLInputElement>(null);

  function applyValue(value: unknown, folders: Record<string, EnvironmentFolderBinding> = {}) {
    const root = objectValue(value, t("Configuration"));
    if (environment) {
      setEnvironmentRows(environmentVariablesFromValue(root, folders));
      return;
    }
    requireOnlyKeys(root, ["name", "provider_id", "config"], t("Configuration"));
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
      const folders = environment && environmentVariablesFromValue(next.value).some(row => row.kind === "path") ? await worldApi.getEnvironmentFolderBindings(card.id) : {};
      applyValue(next.value, folders); setDocument(next); setDirty(false);
      await refreshBindings(); setError(""); setNotice("");
    } catch (reason) { setError(apiErrorMessage(reason)); }
    finally { setBusy(false); }
  }
  useEffect(() => { if (!dirty) void reload(); else void refreshBindings(); }, [card.id]);

  async function importJson(file: File | undefined) {
    if (!file) return;
    setError(""); setNotice("");
    try {
      applyValue(JSON.parse(await file.text()));
      setDirty(true);
      setNotice(t("Imported {v0}. Save to apply.", { v0: String(file.name) }));
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
      applyValue(next.value, environment ? environmentFolderUpdates(environmentRows) : {}); setDocument(next); setDirty(false); await refreshBindings(); setNotice(t("Configuration saved."));
    } catch (reason) { setError(apiErrorMessage(reason)); }
    finally { setBusy(false); }
  }

  return <div className="expanded-stack execution-config">
    <section className="card-section">
      <div className="section-heading execution-config-heading">
        <span>{environment ? t("Environment variables") : t("Destination configuration")}</span>
        <button type="button" className="secondary-button compact-button" disabled={busy} onClick={() => importInput.current?.click()}><Upload size={13} /> {t("Import JSON")}</button>
        <input ref={importInput} className="execution-config-file" type="file" accept="application/json,.json"
          aria-label={t("Import execution configuration JSON")} onChange={(event) => void importJson(event.target.files?.[0])} />
      </div>
      {environment ? <>
        <EnvironmentVariablesEditor rows={environmentRows} onChange={rows => { setEnvironmentRows(rows); setDirty(true); }} disabled={busy || !document}
          secrets={secrets} onSecretsChange={value => { setSecrets(value); setDirty(true); }} bindings={bindings} />
      </> : <>
        <div className="execution-target-fields">
          <label className="field-label"><span>{t("Name")}</span><input value={targetName} disabled={busy || !document} placeholder={t("Display name")}
            onChange={(event) => { setTargetName(event.target.value); setDirty(true); }} /></label>
          <label className="field-label"><span>{t("Provider ID")}</span><input value={providerId} disabled={busy || !document} placeholder={t("Provider identifier")}
            onChange={(event) => { setProviderId(event.target.value); setDirty(true); }} /></label>
        </div>
        <fieldset className="execution-target-settings" disabled={busy || !document}>
          <legend>{t("Provider settings")}</legend>
          <SkillDefaultsEditor rows={targetRows} onChange={rows => { setTargetRows(rows); setDirty(true); }} />
        </fieldset>
      </>}
      <details className="execution-config-help">
        <summary>{t("Usage details")}</summary>
        <p>{t("Select this card for each command. Connecting or equipping it grants access.")}</p>
        <p>{environment
          ? t("Choose Secret and enter the credential. Save stores it securely for commands. Leave a configured secret blank to keep it.")
          : t("Keep credentials in an Environment Profile. Add provider options as named settings.")}</p>
      </details>
      <div className="editor-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={() => void reload()}>{t("Reload")}</button>
        <button type="button" className="primary-button" disabled={busy || !document} onClick={() => void save()}>{t("Save")}</button>
      </div>
      {notice && <p className="execution-config-notice" role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  </div>;
}
