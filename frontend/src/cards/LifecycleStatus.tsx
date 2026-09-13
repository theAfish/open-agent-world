import { t, useLocale } from "../i18n";
import { useEffect, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";

interface RunSnapshot {
  run_id: string; agent_id: string; status: string; error?: string;
  lifecycle: { owner_id?: string; cancellation_policy?: string; holds_capacity?: boolean; execution?: string;
    awaiting?: string; cleanup?: string; cleanup_reason?: string; cancellation_requested?: boolean };
  artifacts: { version_id: string; name: string; state: string; collection_id?: string }[];
}
interface Snapshot {
  runs: RunSnapshot[];
  commands: { id: string; caller: string; state: string; run_id?: string; cleanup_error?: string }[];
  node_cleanup: { node_id: string; last_error?: string; attempts: number }[];
  artifact_cleanup: { version_id: string; cleanup_error?: string }[];
  instances: { id: string; cleanup?: string; cleanup_error?: string }[];
}

export function LifecycleStatus({ agentId }: { agentId?: string }) {
  useLocale();
  const socket = useWorldStore(s => s.socketState);
  const event = useWorldStore(s => s.events.find(e => e.type.startsWith("run_") || e.type.startsWith("tool_") || e.type === "artifact_updated")?.id);
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let live = true;
    void worldApi.lifecycle<Snapshot>().then(value => { if (live) { setSnapshot(value); setError(""); } }).catch(e => { if (live) setError(apiErrorMessage(e)); });
    return () => { live = false; };
  }, [agentId, socket, event, refresh]);
  const active = snapshot?.runs.some(r => ["created", "running", "waiting"].includes(r.status) || r.lifecycle.cleanup === "pending") || !!snapshot?.commands.length;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setRefresh(n => n + 1), 2000);
    return () => window.clearInterval(timer);
  }, [active]);
  async function action(operation: () => Promise<unknown>) {
    try { await operation(); setRefresh(n => n + 1); }
    catch (e) { setError(apiErrorMessage(e)); setRefresh(n => n + 1); }
  }
  return <section className="lifecycle-status">
    <header><strong>{t("Execution and cleanup")}</strong><button className="secondary-button" onClick={() => setRefresh(n => n + 1)}>{t("Refresh state")}</button></header>
    {error && <p role="alert">{error}</p>}
    {snapshot?.runs.filter(r => !agentId || r.agent_id === agentId).slice(-30).reverse().map(run => <article key={run.run_id}>
      <strong>{run.status} {t("· Run")} {run.run_id.slice(0, 8)}</strong>
      <p>{t("Owner")} {run.lifecycle.owner_id ?? run.agent_id} · {run.lifecycle.holds_capacity ? t("holds Agent capacity") : t("capacity released")}</p>
      {run.lifecycle.awaiting && <p>{t("Awaiting")} {run.lifecycle.awaiting}</p>}
      <p>{t("Execution:")} {run.lifecycle.execution ?? "unknown"} {t("· Cleanup:")} {run.lifecycle.cleanup ?? "none"}</p>
      {(run.lifecycle.cleanup_reason || run.error) && <p>{run.lifecycle.cleanup_reason ?? run.error}</p>}
      {(["created", "running", "waiting"].includes(run.status) || ["pending", "failed"].includes(run.lifecycle.cleanup ?? "")) &&
        <button className="secondary-button" onClick={() => void action(() => worldApi.cancelRun(run.run_id))}>{run.lifecycle.cancellation_requested ? t("Retry cancellation cleanup") : t("Cancel Run")}</button>}
      {run.artifacts.map(a => <p key={a.version_id}>{a.name} · {a.state} · {a.version_id}
        {a.collection_id && <button className="secondary-button" onClick={() => useNodeSurfaceStore.getState().openWorkspace(a.collection_id!)}>{t("Inspect artifact")}</button>}</p>)}
    </article>)}
    {!agentId && snapshot && <>
      {snapshot.commands.map(c => <p key={c.id}>{t("Command")} {c.id} · {c.caller} · {c.state}{c.cleanup_error ? ` · ${c.cleanup_error}` : ""}</p>)}
      {snapshot.node_cleanup.map(c => <p key={c.node_id}>{t("Node cleanup")} {c.node_id} · {c.last_error ?? "pending"} · {c.attempts} {t("attempts")}</p>)}
      {snapshot.artifact_cleanup.map(c => <p key={c.version_id}>{t("Artifact cleanup")} {c.version_id} · {c.cleanup_error ?? "pending"}</p>)}
      {snapshot.instances.filter(c => c.cleanup === "failed" || c.cleanup === "pending").map(c => <p key={c.id}>{t("Instance cleanup")} {c.id} · {c.cleanup_error ?? c.cleanup}</p>)}
      {(!!snapshot.node_cleanup.length || !!snapshot.artifact_cleanup.length || snapshot.instances.some(c => c.cleanup === "failed")) &&
        <button className="secondary-button" onClick={() => void action(() => worldApi.lifecycle(true))}>{t("Retry resource cleanup")}</button>}
    </>}
  </section>;
}
