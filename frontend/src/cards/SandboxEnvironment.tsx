import { t, useLocale } from "../i18n";
import { useEffect, useState } from "react";
import { worldApi, apiErrorMessage } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import { useHydrationLease } from '../canvas/useCardRendering';
import type { WorldCard } from "../types/world";
import { EnvironmentVariablesEditor, environmentVariablesFromValue, environmentFolderUpdates, saveEnvironmentRows, type EnvironmentVariableRow } from "./ExecutionConfiguration";

export interface EffectiveEnvironment {
  profile_id: string | null; ready: boolean;
  variables: { name: string; value: string | null; secret: boolean; folder?: boolean; kind?: "folder" | "file"; access?: "read_only" | "read_write"; error?: string | null; configured: boolean; source: string; owner: string }[];
}
const EMPTY_SECRETS: Record<string, string> = {};

export function SandboxEnvironment({ card }: { card: WorldCard }) {
  useLocale();
  const cards = useWorldStore(s => s.cards);
  const edges = useWorldStore(s => s.edges);
  const catalog = useWorldStore(s => s.catalog);
  const link = edges.find(e => e.target === card.id && e.relationship === "environment.default");
  const configurationEvent = useWorldStore(s => s.events.find(e => e.payload?.scope_kind === "node_document"
    && (e.payload.owner_id === card.id || e.payload.owner_id === link?.source))?.id);
  const [savedRows, setSavedRows] = useState<EnvironmentVariableRow[]>([]);
  const [revision, setRevision] = useState<number>();
  const draftKey = `sandbox-environment:${card.id}`;
  const rawDraft = useNodeSurfaceStore(s => s.drafts[draftKey]);
  const draft = rawDraft ? JSON.parse(rawDraft) as { rows: EnvironmentVariableRow[]; revision: number } : undefined;
  const rows = draft?.rows ?? savedRows;
  const setRows = (rows: EnvironmentVariableRow[]) => useNodeSurfaceStore.getState().setDraft(draftKey, JSON.stringify({ rows, revision: draft?.revision ?? revision }));
  const [bindings, setBindings] = useState<Record<string, boolean>>({});
  // Private host memory survives view hydration; never persisted or exposed as a plugin draft.
  const secrets = useNodeSurfaceStore(s => s.privateDrafts[card.id]) ?? EMPTY_SECRETS;
  const setSecrets = (value: Record<string, string>) => useNodeSurfaceStore.getState().setPrivateDraft(card.id, value);
  const [effective, setEffective] = useState<EffectiveEnvironment>();
  const [busy, setBusy] = useState(false);
  useHydrationLease(card.id, 'sandbox-environment-edit', busy || !!draft || Object.keys(secrets).length > 0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function refresh() {
    if (card.ephemeral) return;
    const [status, resolved] = await Promise.all([worldApi.getCredentialBindings(card.id), worldApi.sandboxWorkspace<EffectiveEnvironment>(card.id, "configuration")]);
    setBindings(status); setEffective(resolved);
  }
  async function reload(reset = true) {
    if (card.ephemeral) return;
    if (reset) setSecrets({});
    try {
      const doc = await worldApi.getNodeDocument(card.id);
      const parsed = environmentVariablesFromValue(doc.value);
      const folders = parsed.some(row => row.kind === "path") ? await worldApi.getEnvironmentFolderBindings(card.id) : {};
      setSavedRows(environmentVariablesFromValue(doc.value, folders)); setRevision(doc.revision);
      await refresh(); setError("");
    } catch (e) { setError(apiErrorMessage(e)); }
  }
  useEffect(() => { void reload(false); }, [card.id, card.ephemeral]);
  useEffect(() => { void refresh().catch(e => setError(apiErrorMessage(e))); }, [card.id, card.ephemeral, link?.source, configurationEvent]);
  async function save() {
    setBusy(true); setError("");
    try {
      const doc = await saveEnvironmentRows(card.id, rows, secrets, bindings, (draft?.revision ?? revision)!);
      setSecrets({});
      setRevision(doc.revision); setSavedRows(environmentVariablesFromValue(doc.value, environmentFolderUpdates(rows)));
      useNodeSurfaceStore.getState().setDraft(draftKey, "");
      await refresh(); setNotice(t("Applies to the next command."));
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
  return <details className="sandbox-settings card-section"><summary>{t("Environment variables")}</summary>
    <div className="sandbox-config-form execution-config">
      <label className="field-label">{t("Default profile")} <select value={link?.source ?? ""} disabled={busy} onChange={e => void changeProfile(e.target.value)}>
          <option value="">{t("No linked profile")}</option>
          {link && !cards.some(c => c.id === link.source) && <option value={link.source}>{t("Linked profile ·")} {link.source}</option>}
          {cards.filter(c => catalog.node_types.find(d => d.id === c.type)?.traits.includes("core.environment") && !c.equipment).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </label>
      <p className="sandbox-help" title={t("A command-specific profile replaces the linked profile. Private Agent equipment is never shared automatically.")}>{t("Shared by executions in this Sandbox. Local values override the profile.")}</p>
      <EnvironmentVariablesEditor rows={rows} onChange={setRows} disabled={busy || revision === undefined}
        secrets={secrets} onSecretsChange={setSecrets} bindings={bindings} />
      <div className="editor-actions"><button className="primary-button" disabled={busy || revision === undefined} onClick={() => void save()}>{t("Save environment")}</button>
        <button className="secondary-button" disabled={busy} onClick={() => { useNodeSurfaceStore.getState().setDraft(draftKey, ""); void reload(); }}>{t("Reload environment")}</button></div>
      {draft && <p className="sandbox-help">{t("Unsaved environment changes")}</p>}
      {notice && <p className="sandbox-help" role="status">{notice}</p>}
      {error && <p className="sandbox-error" role="alert">{error}</p>}
      {Object.keys(bindings).length > 0 && <p className="sandbox-help">{t("Secrets stay on this host and are readable by authorized commands.")}</p>}
      <details className="sandbox-settings"><summary>{t("Effective values ·")} {effective ? (effective.ready ? t("Ready") : t("Needs attention")) : t("Loading…")}</summary>
        {effective?.variables.map(v => <div key={v.name} className="sandbox-variable"><code>{v.name}</code> = {v.secret ? (v.configured ? "•••• · bound" : t("Unbound secret")) : v.folder ? (v.value ?? t(v.kind === "file" ? "Unbound file" : "Unbound folder")) : v.value} <small>{v.source}{v.folder && ` / ${t(v.access === "read_write" ? "Read and write" : "Read only")}`}</small>{v.error && <small className="sandbox-error">{v.error}</small>}</div>)}
        {effective?.variables.length === 0 && <p className="sandbox-help">{t("No variables configured.")}</p>}
      </details>
    </div>
  </details>;
}
