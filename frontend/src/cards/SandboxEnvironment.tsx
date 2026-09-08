import { useEffect, useState } from "react";
import { worldApi, apiErrorMessage } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import { CredentialBinding, EnvironmentVariablesEditor, environmentVariablesFromValue, environmentVariablesToValue, type EnvironmentVariableRow } from "./ExecutionConfiguration";

export interface EffectiveEnvironment {
  profile_id: string | null; ready: boolean;
  variables: { name: string; value: string | null; secret: boolean; configured: boolean; source: string; owner: string }[];
}

export function SandboxEnvironment({ card }: { card: WorldCard }) {
  const cards = useWorldStore(s => s.cards);
  const edges = useWorldStore(s => s.edges);
  const catalog = useWorldStore(s => s.catalog);
  const link = edges.find(e => e.target === card.id && e.relationship === "environment.default");
  const configurationEvent = useWorldStore(s => s.events.find(e => e.payload?.scope_kind === "node_document"
    && (e.payload.owner_id === card.id || e.payload.owner_id === link?.source))?.id);
  const [rows, setRows] = useState<EnvironmentVariableRow[]>([]);
  const [revision, setRevision] = useState<number>();
  const [bindings, setBindings] = useState<Record<string, boolean>>({});
  const [effective, setEffective] = useState<EffectiveEnvironment>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function refresh() {
    const [status, resolved] = await Promise.all([worldApi.getCredentialBindings(card.id), worldApi.sandboxWorkspace<EffectiveEnvironment>(card.id, "configuration")]);
    setBindings(status); setEffective(resolved);
  }
  async function reload() {
    try {
      const doc = await worldApi.getNodeDocument(card.id);
      setRows(environmentVariablesFromValue(doc.value)); setRevision(doc.revision);
      await refresh(); setError("");
    } catch (e) { setError(apiErrorMessage(e)); }
  }
  useEffect(() => { void reload(); }, [card.id]);
  useEffect(() => { void refresh().catch(e => setError(apiErrorMessage(e))); }, [link?.source, configurationEvent]);
  async function save() {
    setBusy(true); setError("");
    try {
      const doc = await worldApi.nodeDocumentAction(card.id, "replace", environmentVariablesToValue(rows), revision);
      setRevision(doc.revision); await refresh(); setNotice("Applies to the next command.");
    } catch (e) { setError(apiErrorMessage(e)); }
    finally { setBusy(false); }
  }
  async function changeProfile(id: string) {
    setBusy(true); setError("");
    try {
      if (link) {
        await worldApi.deleteEdge(link.id);
        useWorldStore.setState(s => ({ edges: s.edges.filter(e => e.id !== link.id) }));
      }
      if (id) {
        const edge = await worldApi.createEdge({ source: id, target: card.id, relationship: "environment.default", direction: "forward" });
        useWorldStore.setState(s => ({ edges: [...s.edges, edge] }));
      }
      await refresh();
    } catch (e) { setError(apiErrorMessage(e)); }
    finally { setBusy(false); }
  }
  return <details className="sandbox-settings card-section"><summary>Environment variables</summary>
    <div className="sandbox-config-form execution-config">
      <label className="field-label">Default profile
        <select value={link?.source ?? ""} disabled={busy} onChange={e => void changeProfile(e.target.value)}>
          <option value="">No linked profile</option>
          {link && !cards.some(c => c.id === link.source) && <option value={link.source}>Linked profile · {link.source}</option>}
          {cards.filter(c => catalog.node_types.find(d => d.id === c.type)?.traits.includes("core.environment") && !c.equipment).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </label>
      <p className="sandbox-help" title="A command-specific profile replaces the linked profile. Private Agent equipment is never shared automatically.">Shared by executions in this Sandbox. Local values override the profile.</p>
      <EnvironmentVariablesEditor rows={rows} onChange={setRows} disabled={busy || revision === undefined} />
      <div className="editor-actions"><button className="primary-button" disabled={busy || revision === undefined} onClick={() => void save()}>Save environment</button>
        <button className="secondary-button" disabled={busy} onClick={() => void reload()}>Reload environment</button></div>
      {notice && <p className="sandbox-help" role="status">{notice}</p>}
      {error && <p className="sandbox-error" role="alert">{error}</p>}
      {Object.keys(bindings).length > 0 && <p className="sandbox-help">Secrets stay on this host and are readable by authorized commands.</p>}
      {Object.entries(bindings).map(([reference, configured]) => <CredentialBinding key={reference} id={card.id} reference={reference}
        configured={configured} revision={revision ?? 0} changed={refresh} />)}
      <details className="sandbox-settings"><summary>Effective values · {effective ? (effective.ready ? "Ready" : "Needs attention") : "Loading…"}</summary>
        {effective?.variables.map(v => <div key={v.name} className="sandbox-variable"><code>{v.name}</code> = {v.secret ? (v.configured ? "•••• · bound" : "Unbound secret") : v.value} <small>{v.source}</small></div>)}
        {effective?.variables.length === 0 && <p className="sandbox-help">No variables configured.</p>}
      </details>
    </div>
  </details>;
}
