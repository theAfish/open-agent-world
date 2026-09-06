import { useSummoningCaptureStore } from "../state/summoningCapture";
import { type NodeProps } from "@xyflow/react";
import { Bot, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { apiErrorMessage, worldApi } from "../api/client";
import { descendants } from "../state/containers";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import { AddSelectedMembers, ContainerActions, ContainerFrame } from "./ContainerFrame";
import type { CanvasNode } from "./types";
import "./barracks.css";

export interface SummonedInstance {
  id: string; name: string; status: string; result: string; error: string | null;
  entry_agent_id: string; node_ids: string[]; run_id: string | null; reclaimed: boolean;
}
export interface CallableSummary {
  id: string; name: string; description: string; entry_agent_key: string;
  nodes: { key: string; name: string; type: string }[];
  shared_bindings: { external_id: string; relationship: string }[];
}
export interface SummoningSnapshot {
  templates: CallableSummary[]; instances: SummonedInstance[]; instructions: string;
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
  const pending = useSummoningCaptureStore((state) => state.pending);
  const closeCapture = useSummoningCaptureStore((state) => state.close);
  const captureRequest = pending?.libraryId === card.id ? pending : undefined;
  const finishCapture = () => { setEditing(true); closeCapture(); };
  return <ContainerFrame card={card} selected={selected} className="skill-container barracks-container" label={`${card.name} barracks`} header={<>
    <Bot size={24} /><div><span>AGENT BARRACKS · OPEN SPACE</span><strong>{card.name}</strong></div>
    <button className="secondary-button nodrag nopan" onClick={() => setEditing(true)}>Open barracks</button>
    <AddSelectedMembers card={card} /><ContainerActions card={card} />
  </>}>
    <p className="skill-container-hint">Drag an Agent or Legion here to save a template. Connect the border to summon any member, or connect one template directly.</p>
    {(editing || captureRequest) && createPortal(<div className="skill-container-editor nodrag nopan nowheel" role="dialog" aria-label={`${card.name} workspace`}>
      <button className="skill-container-close" aria-label="Close barracks" onClick={() => { setEditing(false); closeCapture(); }}><X size={16} /></button>
      <BarracksBody key={captureRequest?.id ?? "browse"} card={card} workspace captureNodeIds={captureRequest?.nodeIds} onCaptureFinished={finishCapture} />
    </div>, document.body)}
  </ContainerFrame>;
}

export function AgentTemplatePreview({ card }: { card: WorldCard }) {
  const { snapshot, error } = useBarracks(card.id);
  const template = snapshot?.templates[0];
  return <div className="node-preview-summary"><p>{template?.description || error || "Saved equipped Agent or team"}</p>
    <div className="node-preview-metadata"><span><Bot size={12} /> {template?.nodes.length ?? 0} nodes</span><span>Independent instance per summon</span></div></div>;
}

export function BarracksBody({ card, workspace = false, captureNodeIds, onCaptureFinished }: { card: WorldCard; workspace?: boolean; captureNodeIds?: string[]; onCaptureFinished?: () => void }) {
  const { snapshot, reload, error, setError } = useBarracks(card.id);
  const catalog = useWorldStore((state) => state.catalog);
  const cards = useWorldStore((state) => state.cards);
  const selectCards = useWorldStore((state) => state.selectCards);
  const refreshWorld = useWorldStore((state) => state.refreshWorld);
  const remove = useWorldStore((state) => state.deleteCards);
  const single = catalog.node_types.find((type) => type.id === card.type)?.traits.includes("ui.agent-template.v1");
  const [capture, setCapture] = useState(!!captureNodeIds);
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
    action, instance_id: instance?.id, template_id: target || snapshot?.templates[0]?.id,
    ...(["summon", "message"].includes(action) ? { prompt: task } : {}),
  }));
  return <div className={`skill-toolbox barracks-body nodrag nopan nowheel ${workspace ? "is-workspace" : ""}`}>
    <header className="toolbox-heading"><div><span className="toolbox-eyebrow">{single ? "CALLABLE TEMPLATE" : "AGENT BARRACKS"}</span><h3>{card.name}</h3></div><Bot size={28} /></header>
    <p className="toolbox-help">{single ? "Each summon restores this saved subgraph and starts its entry Agent. Changes to the original nodes do not change this template." : "A library of equipped Agents and teams. Connect an Agent using Summon agents so it can choose the right template for a task."}</p>
    <div className="toolbox-toolbar">
      {!single && <button className="primary-button" disabled={busy || !!settings} onClick={() => setCapture(true)}>Save selected as template</button>}
      <button className="secondary-button" disabled={busy || capture} onClick={edit}>{single ? "Edit template" : "Barracks settings"}</button>
      <button className="secondary-button" disabled={busy} onClick={() => void reload()}>Refresh</button>
    </div>
    {error && <p className="barracks-error" role="alert">{error}</p>}
    {capture && <CaptureForm card={card} initialNodeIds={captureNodeIds} onClose={() => { setCapture(false); onCaptureFinished?.(); }} onSaved={() => { setCapture(false); onCaptureFinished?.(); void reload(); }} />}
    {settings && <form className="barracks-form" onSubmit={(event) => { event.preventDefault(); void mutate(async () => {
      const fields = single ? { ...settings.value } : { name: settings.value.name, instructions: settings.value.instructions, policy: settings.value.policy };
      await worldApi.nodeDocumentAction(card.id, single ? "replace" : "configure", fields, settings.revision);
      await useWorldStore.getState().updateCard(card.id, { name: String(settings.value.name) });
      setSettings(undefined);
    }); }}>
      <label>Name<input required value={String(settings.value.name)} onChange={(e) => setSettings({ ...settings, value: { ...settings.value, name: e.target.value } })} /></label>
      <label>{single ? "When to use" : "Instructions for connected Agents"}<textarea value={String(settings.value[single ? "description" : "instructions"] ?? "")} onChange={(e) => setSettings({ ...settings, value: { ...settings.value, [single ? "description" : "instructions"]: e.target.value } })} /></label>
      <div className="barracks-policy">{([["max_depth", "Maximum summon depth", 16], ["max_concurrent", "Concurrent summoned Runs", 32], ["max_instances", "Instances per root task", 100]] as const).map(([key, label, max]) => <label key={key}>{label}<input type="number" min={1} max={max} required value={(settings.value.policy as SummoningSnapshot["policy"])[key]} onChange={(e) => setSettings({ ...settings, value: { ...settings.value, policy: { ...settings.value.policy as object, [key]: Number(e.target.value) } } })} /></label>)}</div>
      <p className="toolbox-help">Nested summons share the root task's limits. A connected library can tighten them.</p>
      <div className="toolbox-toolbar"><button className="primary-button" disabled={busy}>Save settings</button><button type="button" className="secondary-button" onClick={() => setSettings(undefined)}>Cancel</button></div>
    </form>}
    <div className="barracks-templates">{snapshot?.templates.map((template) => <article key={template.id}>
      <strong>{template.name}</strong><p>{template.description || "Add a description so Agents know when to use this template."}</p>
      <details><summary>{template.nodes.length} copied nodes · {template.shared_bindings.length} shared connections</summary><ul>{template.nodes.map((node) => <li key={node.key}>{node.name} · {node.type}{node.key === template.entry_agent_key ? " · entry Agent" : ""}</li>)}</ul>
        {template.shared_bindings.map((binding, i) => <p key={i}>Share {binding.external_id === "$library" ? "this library" : cards.find((node) => node.id === binding.external_id)?.name ?? binding.external_id} · {binding.relationship}</p>)}
      </details>
      {!single && <button className="secondary-button" disabled={busy} onClick={() => void mutate(() => remove([template.id]))}>Delete template</button>}
    </article>)}</div>
    {!snapshot?.templates.length && <p className="toolbox-help">Save a single Agent, select an Agent with its tools, or select a Legion. The saved copy appears as a card inside this space.</p>}
    <form className="barracks-form" onSubmit={(event) => { event.preventDefault(); act("summon"); }}>
      <h4>Try a template</h4>
      <label>Template<select value={target || snapshot?.templates[0]?.id || ""} onChange={(e) => setTarget(e.target.value)}>{snapshot?.templates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}</select></label>
      <label>Task / follow-up<textarea required placeholder="Describe the task for the entry Agent" value={task} onChange={(e) => setTask(e.target.value)} /></label>
      <button className="primary-button" disabled={busy || !snapshot?.templates.length || !task.trim()}>Summon new instance</button>
    </form>
    <h4>Instances</h4><p className="toolbox-help">Results and workspaces stay available after a Run. Follow up reuses the same nodes. Reclaim deletes the instance and its summoned descendants, including their workspaces.</p>
    <div className="barracks-instances">{snapshot?.instances.slice().reverse().map((instance) => <article key={instance.id}>
      <details open={instance.status === "running"}><summary>{instance.name} · {instance.status}</summary>
        <p className="barracks-result">{instance.result || instance.error || "No result yet."}</p>
        <small>Run: {instance.run_id || "Not started"}</small>
      </details>
      {!instance.reclaimed && <div className="toolbox-toolbar">
        <button className="secondary-button" onClick={() => selectCards(instance.node_ids)}>Select nodes</button>
        <button className="secondary-button" disabled={busy || !task.trim() || instance.status === "running"} onClick={() => act("message", instance)}>Follow up</button>
        <button className="secondary-button" disabled={busy || !["running", "waiting", "paused"].includes(instance.status)} onClick={() => act("stop", instance)}>Stop</button>
        <button className="secondary-button" disabled={busy} onClick={() => act("reclaim", instance)}>Reclaim instance</button>
      </div>}
    </article>)}</div>
  </div>;
}

function CaptureForm({ card, onClose, onSaved, initialNodeIds }: { card: WorldCard; onClose: () => void; onSaved: () => void; initialNodeIds?: string[] }) {
  const cards = useWorldStore((state) => state.cards);
  const edges = useWorldStore((state) => state.edges);
  const catalog = useWorldStore((state) => state.catalog);
  const selected = useWorldStore((state) => state.selectedCardIds);
  const [copied, setCopied] = useState((initialNodeIds ?? selected).filter((id) => id !== card.id));
  const [modes, setModes] = useState<Record<string, "copy" | "share" | "omit">>({ [card.id]: "share" });
  const [entry, setEntry] = useState("");
  const [name, setName] = useState(initialNodeIds ? cards.find((node) => node.id === initialNodeIds[0])?.name ?? "" : "");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const copyIds = new Set([...copied, ...Object.keys(modes).filter((id) => modes[id] === "copy")].flatMap((id) => [id, ...descendants(cards, id).map((node) => node.id)]));
  const agents = cards.filter((node) => copyIds.has(node.id) && catalog.node_types.find((type) => type.id === node.type)?.traits.includes("core.agent"));
  const dependencies = cards.filter((node) => !copyIds.has(node.id) && edges.some((edge) => (edge.source === node.id && copyIds.has(edge.target)) || (edge.target === node.id && copyIds.has(edge.source))));
  const candidates = cards.filter((node) => node.id !== card.id && catalog.node_types.find((type) => type.id === node.type)?.templateable && !catalog.node_types.find((type) => type.id === node.type)?.traits.includes("oaw.agent-template"));
  return <form className="barracks-form" onSubmit={async (event) => {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const current = await worldApi.getNodeDocument(card.id);
      await worldApi.captureSummoning(card.id, { name, description, node_ids: [...copyIds], entry_agent_id: entry || agents[0]?.id,
        shared_node_ids: dependencies.filter((node) => modes[node.id] === "share").map((node) => node.id), expected_revision: current.revision });
      await useWorldStore.getState().refreshWorld(); onSaved();
    } catch (error) { setError(apiErrorMessage(error)); } finally { setBusy(false); }
  }}>
    <h4>Save callable template</h4>
    {initialNodeIds && <p className="toolbox-help">Save a reusable copy of the dropped nodes. The original nodes and their connections stay in place.</p>}
    <label>Template name<input required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} /></label>
    <label>When to use<textarea maxLength={500} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Explain the task this Agent or team is good at" /></label>
    <fieldset><legend>Copy these nodes</legend><div className="barracks-node-picker">{candidates.map((node) => <label key={node.id}>
      <input type="checkbox" checked={copyIds.has(node.id)} disabled={!copied.includes(node.id) && copyIds.has(node.id) && modes[node.id] !== "copy"} onChange={(e) => { setCopied(e.target.checked ? [...copied, node.id] : copied.filter((id) => id !== node.id)); setModes({ ...modes, [node.id]: "omit" }); }} />{node.name} <small>{node.type}</small>
    </label>)}</div></fieldset>
    <label>Entry Agent<select required value={entry || agents[0]?.id || ""} onChange={(e) => setEntry(e.target.value)}><option value="" disabled>Choose an Agent in the copied nodes</option>{agents.map((node) => <option key={node.id} value={node.id}>{node.name}</option>)}</select></label>
    {!!dependencies.length && <fieldset><legend>Connected resources outside the selection</legend>{dependencies.map((node) => <label className="barracks-binding" key={node.id}><span>{node.name}</span><select aria-label={`Binding for ${node.name}`} value={modes[node.id] ?? "omit"} onChange={(e) => setModes({ ...modes, [node.id]: e.target.value as "copy" | "share" | "omit" })}>
      <option value="omit">Do not include</option><option value="share">Reuse existing node</option>{node.id !== card.id && catalog.node_types.find((type) => type.id === node.type)?.templateable && <option value="copy">Copy into each instance</option>}
    </select></label>)}</fieldset>}
    <p className="toolbox-help">Copied Sandboxes start with fresh workspaces. Shared nodes stay connected to their existing resources. Sharing this barracks lets the entry Agent summon from it recursively.</p>
    {error && <p role="alert" className="barracks-error">{error}</p>}
    <div className="toolbox-toolbar"><button className="primary-button" disabled={busy || !agents.length}>Save template</button><button type="button" className="secondary-button" onClick={onClose}>Cancel</button></div>
  </form>;
}
