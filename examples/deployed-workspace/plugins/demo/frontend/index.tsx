import { useEffect, useState } from "react";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";

function Notes({ card, host }: PluginViewProps) {
  const [text, setText] = useState("");
  const [revision, setRevision] = useState<number>();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    void host.readDocument().then(snapshot => {
      if (!active) return;
      setText((snapshot.value as { text: string }).text); setRevision(snapshot.revision);
    }).catch(error => { if (active) setMessage(error.message); });
    return () => { active = false; };
  }, [card.id]);
  const canSave = !host.deployment || host.deployment.document_actions.includes("save");
  return <div className="node-workspace">
    <h3>{String(card.config.heading ?? "Workspace notes")}</h3>
    <textarea aria-label="Workspace note" value={text} readOnly={!canSave} onChange={event => setText(event.target.value)} rows={8} />
    {canSave && <button className="primary-button" disabled={busy || revision === undefined} onClick={async () => {
      setBusy(true); setMessage("");
      try { const result = await host.documentAction("save", { text }, revision); setRevision(result.revision); setMessage("Saved"); }
      catch (error) { setMessage((error as Error).message); }
      finally { setBusy(false); }
    }}>Save note</button>}
    {(!host.deployment || host.deployment.downloads.includes("text")) && <a href={host.documentDownloadUrl("text")}>Download note</a>}
    {!host.deployment && <p>Connection: {String(card.config.internal_connection)}</p>}
    {message && <p role="status">{message}</p>}
  </div>;
}

export default { apiVersion: 1, views: { notes: Notes } } satisfies FrontendPlugin;
