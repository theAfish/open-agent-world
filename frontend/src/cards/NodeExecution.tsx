import { t, useLocale } from "../i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import { Play, Square } from "lucide-react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { PublishedReferences, type ArtifactReference } from "./Artifacts";

export interface ExecutionSnapshot {
  status: string;
  active: boolean;
  error: string | null;
  executors: { id: string; name: string }[];
  items: { id: string; ready: boolean; retryable: boolean; agent_id: string | null }[];
  attempts: { item_id: string; agent_id: string; run_id: string | null; status: string; error: string | null; artifacts?: ArtifactReference[] }[];
}

/** Shared host UI: no assumptions about DAGs, task fields or acceptance rules. */
export function useNodeExecution(nodeId: string, onChanged: () => Promise<void>) {
  const [state, setState] = useState<ExecutionSnapshot>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const sequence = useRef(0);
  const eventId = useWorldStore((s) => s.events.find((event) =>
    (event.payload.scope_kind === "node_document" && event.payload.owner_id === nodeId)
    || event.type.startsWith("edge_") || event.type === "card_updated" || event.type === "card_deleted")?.id);
  const socketState = useWorldStore((s) => s.socketState);
  const reload = useCallback(async () => {
    const request = ++sequence.current;
    try { const next = await worldApi.getNodeExecution(nodeId); if (request === sequence.current) setState(next); }
    catch (e) { if (request === sequence.current) setError(apiErrorMessage(e)); }
  }, [nodeId]);
  useEffect(() => { void reload(); return () => { sequence.current++; }; }, [reload, eventId, socketState]);
  useEffect(() => {
    if (!state?.active) return;
    const timer = window.setInterval(() => { void reload(); }, 1000);
    return () => window.clearInterval(timer);
  }, [reload, state?.active]);
  const act = async (operation: () => Promise<ExecutionSnapshot>) => {
    setBusy(true); setError(""); ++sequence.current;
    try { await operation(); await reload(); await onChanged(); }
    catch (e) { setError(apiErrorMessage(e)); }
    finally { setBusy(false); }
  };
  return { state, busy, error, run: (revision: number, itemId?: string) => act(() => worldApi.startNodeExecution(nodeId, revision, itemId)),
    stop: () => act(() => worldApi.stopNodeExecution(nodeId)) };
}

export function NodeExecutionControls({ execution, revision, readyCount, disabled = false, titleForItem }: {
  execution: ReturnType<typeof useNodeExecution>; revision?: number; readyCount: number; disabled?: boolean;
  titleForItem: (id: string) => string;
}) {
  useLocale();
  const { state, busy, error, run, stop } = execution;
  const unassigned = state?.items.some((item) => item.ready && !state.executors.some((agent) => agent.id === item.agent_id));
  return <div className="node-execution-controls">
    <div className="task-board-toolbar">
      <span role="status">{state?.active ? t("Running work") : state?.status === "idle" ? t("Ready") : state?.status ?? t("Loading execution...")}</span>
      {state?.active ? <button type="button" className="secondary-button" disabled={busy} onClick={() => void stop()}><Square size={13} /> {t("Stop execution")}</button>
        : <button type="button" className="primary-button" disabled={busy || disabled || unassigned || revision === undefined || !readyCount || !state?.executors.length} onClick={() => void run(revision!)}><Play size={13} /> {t("Run ready work")}</button>}
    </div>
    {unassigned && !!state?.executors.length && <p className="task-board-help">{t("Assign connected executors to ready work before running.")}</p>}
    {(error || state?.error) && <p role="alert" className="task-board-error">{error || state?.error}</p>}
    {state?.attempts.map(attempt => <PublishedReferences key={attempt.run_id ?? attempt.item_id} references={attempt.artifacts} />)}
    {!!state?.attempts.length && <details className="execution-history"><summary>{t("Execution history (")}{state.attempts.length})</summary>
      <ol>{state.attempts.slice(-30).reverse().map((attempt, index) => <li key={attempt.run_id ?? index}>
        <strong>{titleForItem(attempt.item_id)}</strong><span>{attempt.status} · {state.executors.find((agent) => agent.id === attempt.agent_id)?.name ?? t("Previous executor")}</span>
        {attempt.error && <p>{attempt.error}</p>}<small>{t("Run:")} {attempt.run_id ?? t("Admission interrupted")}</small>
      </li>)}</ol>
    </details>}
  </div>;
}

/** Baseline workspace for executable plugins without a specialized renderer. */
export function WorkSourceWorkspace({ nodeId }: { nodeId: string }) {
  useLocale();
  const [revision, setRevision] = useState<number>();
  const [error, setError] = useState("");
  const reload = useCallback(async () => {
    try { setRevision((await worldApi.getNodeDocument(nodeId)).revision); setError(""); }
    catch (e) { setError(apiErrorMessage(e)); }
  }, [nodeId]);
  const execution = useNodeExecution(nodeId, reload);
  useEffect(() => { void reload(); }, [reload, execution.state]);
  return <div className="work-source-workspace">
    <p>{t("The plugin defines readiness and result acceptance. Connect an executor using this plugin's execution relationship.")}</p>
    {error && <p role="alert">{error}</p>}
    <NodeExecutionControls execution={execution} revision={revision} readyCount={execution.state?.items.filter((item) => item.ready).length ?? 0} titleForItem={(id) => id} />
    <ul>{execution.state?.items.map((item) => <li key={item.id}><strong>{item.id}</strong> · {item.ready ? t("Ready") : item.retryable ? t("Retry available") : t("Waiting or complete")}
      {(item.ready || item.retryable) && <button className="secondary-button" disabled={execution.busy || execution.state?.active || revision === undefined} onClick={() => void execution.run(revision!, item.id)}>{item.retryable ? t("Retry") : t("Run")}</button>}
    </li>)}</ul>
  </div>;
}
