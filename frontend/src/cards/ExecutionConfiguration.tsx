import { useEffect, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import "./executionConfiguration.css";

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

/** The same document editor also supports plugin-declared structured target fields. */
export function ExecutionConfigurationBody({ card }: { card: WorldCard }) {
  const definition = useWorldStore((state) => state.catalog.node_types.find((item) => item.id === card.type));
  const environment = definition?.traits.includes("core.environment") ?? false;
  const [document, setDocument] = useState<Awaited<ReturnType<typeof worldApi.getNodeDocument>>>();
  const [draft, setDraft] = useState("");
  const [bindings, setBindings] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function refreshBindings() { if (environment) setBindings(await worldApi.getCredentialBindings(card.id)); }
  async function reload() {
    setBusy(true);
    try {
      const next = await worldApi.getNodeDocument(card.id);
      setDocument(next); setDraft(JSON.stringify(next.value, null, 2));
      await refreshBindings(); setError("");
    } catch (reason) { setError(apiErrorMessage(reason)); }
    finally { setBusy(false); }
  }
  useEffect(() => { void reload(); }, [card.id]);
  async function save() {
    if (!document) return;
    setBusy(true); setError("");
    try {
      const value: unknown = JSON.parse(draft);
      if (!value || Array.isArray(value) || typeof value !== "object") throw new Error("Configuration must be a JSON object.");
      const next = await worldApi.nodeDocumentAction(card.id, "replace", value as Record<string, unknown>, document.revision);
      setDocument(next); setDraft(JSON.stringify(next.value, null, 2)); await refreshBindings();
    } catch (reason) { setError(apiErrorMessage(reason)); }
    finally { setBusy(false); }
  }
  return <div className="expanded-stack execution-config nodrag nopan">
    <section className="card-section">
      <div className="section-heading"><span>{environment ? "Environment variables" : "Destination configuration"}</span></div>
      <p>Select this card explicitly for each command. Connecting or equipping it grants access.</p>
      {environment ? <p>Use ordinary strings or secret references, for example <code>{'{"variables":{"REGION":"test","API_TOKEN":{"secret_ref":"api-token"}}}'}</code>. Save references before binding secrets below.</p>
        : <p>Edit name, provider_id and structured config. Store authentication in an Environment Profile.</p>}
      <label className="field-label"><span>Configuration JSON</span>
        <textarea aria-label="Execution configuration JSON" className="nowheel" rows={10} value={draft}
          onChange={(event) => setDraft(event.target.value)} disabled={busy || !document} spellCheck={false} />
      </label>
      <div className="editor-actions">
        <button type="button" className="primary-button" disabled={busy || !document} onClick={() => void save()}>Save configuration</button>
        <button type="button" className="secondary-button" disabled={busy} onClick={() => void reload()}>Reload</button>
      </div>
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
