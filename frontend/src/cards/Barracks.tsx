import { type NodeProps } from "@xyflow/react";
import { Bot, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import { AddSelectedMembers, ContainerActions, ContainerFrame } from "./ContainerFrame";
import type { CanvasNode } from "./types";
import { PublishedReferences, type ArtifactReference } from "./Artifacts";
import "./barracks.css";

export interface SummonedInstance {
  id: string; name: string; status: string; result: string; error: string | null;
  entry_agent_id: string; node_ids: string[]; run_id: string | null; reclaimed: boolean;
  workspace_id?: string;
  artifacts?: ArtifactReference[];
}
export interface SummonableAgent { id: string; name: string; description: string; equipment_count: number }
export interface SummoningSnapshot {
  agents: SummonableAgent[]; instances: SummonedInstance[]; instructions: string;
  policy: { max_depth: number; max_concurrent: number; max_instances: number };
}

function useBarracks(id: string) {
  const eventId = useWorldStore((state) => state.events[0]?.id);
  const socketState = useWorldStore((state) => state.socketState);
  const [snapshot, setSnapshot] = useState<SummoningSnapshot>();
  const [error, setError] = useState("");
  const reload = useCallback(async () => {
    try { setSnapshot(await worldApi.getSummoning(id)); }
    catch (error) { setError(apiErrorMessage(error)); }
  }, [id]);
  useEffect(() => { const timer = setTimeout(() => void reload(), 100); return () => clearTimeout(timer); }, [reload, eventId, socketState]);
  return { snapshot, reload, error, setError };
}

export function BarracksContainerNode({ data, selected }: NodeProps<CanvasNode>) {
  const card = data.card;
  const [editing, setEditing] = useState(false);
  return <ContainerFrame card={card} selected={selected} className="skill-container barracks-container" label={`${card.name} barracks`} header={<>
    <Bot size={24} /><div><span>AGENT BARRACKS · OPEN SPACE</span><strong>{card.name}</strong></div>
    <button className="secondary-button nodrag nopan" onClick={() => setEditing(true)}>Open barracks</button>
    <AddSelectedMembers card={card} /><ContainerActions card={card} />
  </>}>
    <p className="skill-container-hint">Drag Agents into or out of this space. Each Agent keeps its equipment and shared connections. Connect an Agent's equipped Summoning skill to this space to call its Agents.</p>
    {editing && createPortal(<div className="skill-container-editor nodrag nopan nowheel" role="dialog" aria-label={`${card.name} workspace`}>
      <button className="skill-container-close" aria-label="Close barracks" onClick={() => { setEditing(false); }}><X size={16} /></button>
      <BarracksBody card={card} workspace />
    </div>, document.body)}
  </ContainerFrame>;
}

export function BarracksBody({ card, workspace = false }: { card: WorldCard; workspace?: boolean }) {
  const { snapshot, reload, error, setError } = useBarracks(card.id);
  const selectCards = useWorldStore((state) => state.selectCards);
  const refreshWorld = useWorldStore((state) => state.refreshWorld);
  const [busy, setBusy] = useState(false);
  const [task, setTask] = useState("");
  const [target, setTarget] = useState("");
  const [settings, setSettings] = useState<{ value: Record<string, unknown>; revision: number }>();
  const mutate = async (operation: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await operation(); await reload(); await refreshWorld(); }
    catch (error) { setError(apiErrorMessage(error)); }
    finally { setBusy(false); }
  };
  const edit = () => void mutate(async () => {
    const current = await worldApi.getNodeDocument(card.id);
    setSettings({ ...current, value: { ...current.value, name: card.name } });
  });
  const act = (action: string, instance?: SummonedInstance) => void mutate(() => worldApi.summoningAction(card.id, {
    action, instance_id: instance?.id, agent_id: target || snapshot?.agents[0]?.id,
    ...(["summon", "message"].includes(action) ? { prompt: task } : {}),
  }));
  return <div className={`skill-toolbox barracks-body nowheel ${workspace ? "is-workspace" : ""}`}>
    <header className="toolbox-heading"><div><span className="toolbox-eyebrow">AGENT BARRACKS</span><h3>{card.name}</h3></div><Bot size={28} /></header>
    <p className="toolbox-help">Agents in this space are live blueprints. Summoning copies their current configuration and private equipment, with fresh workspaces. External connections remain shared.</p>
    <div className="toolbox-toolbar">
      <button className="secondary-button" disabled={busy} onClick={edit}>Barracks settings</button>
      <button className="secondary-button" disabled={busy} onClick={() => void reload()}>Refresh</button>
    </div>
    {error && <p className="barracks-error" role="alert">{error}</p>}
    {settings && <form className="barracks-form" onSubmit={(event) => { event.preventDefault(); void mutate(async () => {
      const fields = { name: settings.value.name, instructions: settings.value.instructions, policy: settings.value.policy };
      await worldApi.nodeDocumentAction(card.id, "configure", fields, settings.revision);
      await useWorldStore.getState().updateCard(card.id, { name: String(settings.value.name) });
      setSettings(undefined);
    }); }}>
      <label>Name<input required value={String(settings.value.name)} onChange={(e) => setSettings({ ...settings, value: { ...settings.value, name: e.target.value } })} /></label>
      <label>Instructions for connected Agents<textarea value={String(settings.value["instructions"] ?? "")} onChange={(e) => setSettings({ ...settings, value: { ...settings.value, ["instructions"]: e.target.value } })} /></label>
      <div className="barracks-policy">{([["max_depth", "Maximum summon depth", 16], ["max_concurrent", "Concurrent summoned Runs", 32], ["max_instances", "Instances per root task", 100]] as const).map(([key, label, max]) => <label key={key}>{label}<input type="number" min={1} max={max} required value={(settings.value.policy as SummoningSnapshot["policy"])[key]} onChange={(e) => setSettings({ ...settings, value: { ...settings.value, policy: { ...settings.value.policy as object, [key]: Number(e.target.value) } } })} /></label>)}</div>
      <p className="toolbox-help">Nested summons share the root task's limits. A connected library can tighten them.</p>
      <div className="toolbox-toolbar"><button className="primary-button" disabled={busy}>Save settings</button><button type="button" className="secondary-button" onClick={() => setSettings(undefined)}>Cancel</button></div>
    </form>}
    <div className="barracks-agents">{snapshot?.agents.map((agent) => <article key={agent.id}>
      <strong>{agent.name}</strong><p>{agent.description || "Configure this Agent on the canvas."}</p>
      <small>{agent.equipment_count} equipment items</small>
      <button className="secondary-button" onClick={() => selectCards([agent.id])}>Select Agent</button>
    </article>)}</div>
    {!snapshot?.agents.length && <p className="toolbox-help">Drag a configured Agent into this space to make it available.</p>}
    <form className="barracks-form" onSubmit={(event) => { event.preventDefault(); act("summon"); }}>
      <h4>Try an Agent</h4>
      <label>Agent<select value={target || snapshot?.agents[0]?.id || ""} onChange={(e) => setTarget(e.target.value)}>{snapshot?.agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
      <label>Task / follow-up<textarea required placeholder="Describe the task for the entry Agent" value={task} onChange={(e) => setTask(e.target.value)} /></label>
      <button className="primary-button" disabled={busy || !snapshot?.agents.length || !task.trim()}>Summon new instance</button>
    </form>
    <h4>Instances</h4><p className="toolbox-help">Results and workspaces stay available after a Run. Follow up reuses the same nodes. Reclaim deletes the instance and its summoned descendants, including their workspaces.</p>
    <div className="barracks-instances">{snapshot?.instances.slice().reverse().map((instance) => <article key={instance.id}>
      <details open={instance.status === "running"}><summary>{instance.name} · {instance.status}</summary>
        <p className="barracks-result">{instance.result || instance.error || "No result yet."}</p>
        <PublishedReferences references={instance.artifacts} />
        <small>Run: {instance.run_id || "Not started"}</small>
      </details>
      {!instance.reclaimed && <div className="toolbox-toolbar">
        <button className="secondary-button" onClick={() => selectCards(instance.node_ids)}>Select nodes</button>
        {instance.workspace_id && <button className="secondary-button" onClick={() => selectCards([instance.workspace_id!])}>Select workspace</button>}
        <button className="secondary-button" disabled={busy || !task.trim() || instance.status === "running"} onClick={() => act("message", instance)}>Follow up</button>
        <button className="secondary-button" disabled={busy || !["running", "waiting", "paused"].includes(instance.status)} onClick={() => act("stop", instance)}>Stop</button>
        <button className="secondary-button" disabled={busy} onClick={() => act("reclaim", instance)}>Reclaim instance</button>
      </div>}
    </article>)}</div>
  </div>;
}
