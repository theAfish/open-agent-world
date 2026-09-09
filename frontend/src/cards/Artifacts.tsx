import { useEffect, useRef, useState, type ReactNode } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import type { WorldCard } from "../types/world";

export interface ArtifactVersion {
  version_id: string; artifact_id: string; name: string; state: string; size_bytes: number;
  created_at: string; error?: string; cleanup?: string; cleanup_error?: string;
  retention: { retained: boolean; owner: string; reason: string };
  provenance: { agent_id?: string; agent_name?: string; run_id?: string; sandbox_name: string; selected_paths: string[] };
  manifest: { path: string; directory: boolean; size: number; sha256?: string }[];
}

export interface ArtifactReference { version_id: string; name: string; state: string; collection_id?: string }

export function PublishedReferences({ references }: { references?: ArtifactReference[] }) {
  return <>{references?.map(ref => <p key={ref.version_id}>{ref.name} · {ref.state} · {ref.version_id}
    {ref.collection_id && <button className="secondary-button" onClick={() => useNodeSurfaceStore.getState().openWorkspace(ref.collection_id!)}>Inspect artifact</button>}</p>)}</>;
}

export function PublishFiles({ card, paths, children }: { card: WorldCard; paths: string[]; children?: ReactNode }) {
  const cards = useWorldStore(s => s.cards);
  const [collection, setCollection] = useState("");
  const [finalized, setFinalized] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const request = useRef<{ fingerprint: string; key: string }>();
  async function publish() {
    setBusy(true); setMessage("");
    try {
      const fingerprint = JSON.stringify([collection, paths]);
      if (request.current?.fingerprint !== fingerprint) request.current = { fingerprint, key: crypto.randomUUID() };
      const result = await worldApi.artifacts<ArtifactVersion>(collection, "versions", {
        sandbox_id: card.id, paths, finalized, name: paths.length === 1 ? paths[0].split("/").at(-1) : `${card.name} outputs`, request_key: request.current.key,
      });
      setMessage(result.state === "ready" ? `Published ${result.name} · ${result.version_id}` : `${result.state}: ${result.error ?? "Inspect collection for details"}`);
      if (result.state !== "staging") request.current = undefined;
    } catch (error) { setMessage(apiErrorMessage(error)); }
    finally { setBusy(false); }
  }
  return <details className="artifact-publish">
    <summary>Publish selected files ({paths.length})</summary>
    {children}
    <p>{paths.join(", ") || "Select files or directories in the file tree."}</p>
    <label>Collection <select aria-label="Publication collection" value={collection} onChange={e => setCollection(e.target.value)}>
      <option value="">Choose a collection</option>{cards.filter(c => c.type === "core.artifact-collection").map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
    </select></label>
    <button className="secondary-button" disabled={busy} onClick={() => void useWorldStore.getState().createCard("core.artifact-collection", { x: card.position.x + 360, y: card.position.y }).then(c => { if (c) setCollection(c.id); })}>New collection</button>
    <label><input type="checkbox" checked={finalized} onChange={e => setFinalized(e.target.checked)} />Files are finalized; external writers are paused</label>
    <button className="primary-button" disabled={busy || !collection || !finalized || !paths.length} onClick={() => void publish()}>{busy ? "Publishing…" : "Publish retained version"}</button>
    {collection && <button className="secondary-button" onClick={() => useNodeSurfaceStore.getState().openWorkspace(collection)}>Inspect collection</button>}
    {message && <p role="status">{message}</p>}
  </details>;
}

export function ArtifactCollection({ card }: { card: WorldCard }) {
  const socket = useWorldStore(s => s.socketState);
  const event = useWorldStore(s => s.events.find(e => e.type === "artifact_updated" && e.node_id === card.id)?.id);
  const cards = useWorldStore(s => s.cards);
  const [versions, setVersions] = useState<ArtifactVersion[]>([]);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState("");
  const [sandbox, setSandbox] = useState("");
  const [destination, setDestination] = useState("");
  const [retained, setRetained] = useState<ArtifactVersion[]>([]);
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  async function refresh() { setVersions(await worldApi.artifacts<ArtifactVersion[]>(card.id)); }
  useEffect(() => { let live = true;
    void worldApi.artifacts<ArtifactVersion[]>(card.id).then(value => { if (live) setVersions(value); }).catch(e => { if (live) setError(apiErrorMessage(e)); });
    return () => { live = false; };
  }, [card.id, socket, event]);
  useEffect(() => {
    if (!versions.some(v => v.state === "staging" || v.state === "deleting")) return;
    const timer = window.setInterval(() => void refresh().catch(e => setError(apiErrorMessage(e))), 2000);
    return () => window.clearInterval(timer);
  }, [versions, card.id]);
  async function action(operation: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await operation(); await refresh(); }
    catch (e) { setError(apiErrorMessage(e)); }
    finally { setBusy(false); }
  }
  return <div className="artifact-collection nowheel">
    <header><h3>Published versions</h3><button className="secondary-button" onClick={() => void action(refresh)}>Refresh</button></header>
    <p>Retained independently of producers. Removing a reference preserves its stored content.</p>
    {error && <p role="alert">{error}</p>}
    <label>Copy into Sandbox <select value={sandbox} onChange={e => setSandbox(e.target.value)}><option value="">Choose workspace</option>{cards.filter(c => c.type === "sandbox").map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
    <label>New destination directory <input value={destination} onChange={e => setDestination(e.target.value)} /></label>
    {versions.map(v => <article key={v.version_id}>
      <h4>{v.name} · {v.state}</h4><p>{v.size_bytes.toLocaleString()} bytes · {v.created_at}</p>
      <p>Version {v.version_id}<br />Artifact {v.artifact_id}</p>
      <p>Produced by {v.provenance.agent_name ?? "User"} in {v.provenance.sandbox_name}{v.provenance.run_id ? ` · Run ${v.provenance.run_id}` : ""}</p>
      <p>Retention: {v.retention.retained ? v.retention.reason : "released"} · {v.retention.owner}</p>
      {(v.error || v.cleanup_error) && <p role="alert">{v.error ?? v.cleanup_error}</p>}
      <details><summary>Content manifest ({v.manifest.length})</summary><ul>{v.manifest.map(file => <li key={file.path}>
        {file.path}{file.directory ? "/" : ` · ${file.size} bytes`}
        {file.sha256 && <small> SHA-256 {file.sha256}</small>}
        {!file.directory && v.state === "ready" && <>
          <button className="secondary-button" onClick={() => void action(async () => {
            const result = await worldApi.artifacts<{ text?: string; state: string; truncated?: boolean }>(card.id, `versions/${v.version_id}/preview?${new URLSearchParams({ path: file.path })}`);
            setPreview(`${file.path}\n${result.text ?? result.state}${result.truncated ? "\n[Preview limited to 64 KiB]" : ""}`);
          })}>Preview</button>
          <a href={worldApi.artifactDownloadUrl(card.id, v.version_id, file.path)} download>Download</a>
        </>}
      </li>)}</ul></details>
      <button className="secondary-button" disabled={busy || !sandbox || !destination || v.state !== "ready"} onClick={() => void action(() => worldApi.artifacts(card.id, `versions/${v.version_id}/materialize`, { sandbox_id: sandbox, destination }))}>Copy version to workspace</button>
      <button className="secondary-button" disabled={busy} onClick={() => void action(() => worldApi.artifacts(card.id, `references/${v.version_id}`, undefined, "DELETE"))}>Remove reference</button>
      {confirm === v.version_id ? <button className="danger-button" disabled={busy} onClick={() => void action(() => worldApi.artifacts(card.id, `versions/${v.version_id}`, undefined, "DELETE"))}>Confirm release and delete stored content</button>
        : <button className="secondary-button" disabled={busy || v.state === "deleted"} onClick={() => setConfirm(v.version_id)}>Release retained content…</button>}
    </article>)}
    {preview && <pre className="artifact-preview">{preview}</pre>}
    <details><summary>Restore retained references</summary>
      <button className="secondary-button" onClick={() => void action(async () => setRetained(await worldApi.retainedArtifacts<ArtifactVersion[]>()))}>Browse retained versions</button>
      {retained.filter(v => v.retention.retained && !versions.some(r => r.version_id === v.version_id)).map(v => <p key={v.version_id}>{v.name} · {v.version_id}
        <button className="secondary-button" onClick={() => void action(() => worldApi.artifacts(card.id, `references/${v.version_id}`, undefined, "PUT"))}>Add reference</button></p>)}
    </details>
  </div>;
}
