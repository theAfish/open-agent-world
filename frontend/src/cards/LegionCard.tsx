import { NodeResizeControl, type NodeProps } from "@xyflow/react";
import { Layers3, Pause, Play, RefreshCw, Save, Trash2, Ungroup } from "lucide-react";
import { useEffect, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import type { CanvasNode } from "./types";
import { SharedVariablesEditor, variablesFromValue, variablesToValue, type VariableRow } from "./SharedVariablesEditor";

export function LegionCardNode({ data, selected }: NodeProps<CanvasNode>) {
  const card = data.card;
  const cards = useWorldStore((s) => s.cards);
  const selectedIds = useWorldStore((s) => s.selectedCardIds);
  const updateCard = useWorldStore((s) => s.updateCard);
  const setMembership = useWorldStore((s) => s.setLegionMembership);
  const createLegion = useWorldStore((s) => s.createLegion);
  const deleteCards = useWorldStore((s) => s.deleteCards);
  const dissolveLegion = useWorldStore((s) => s.dissolveLegion);
  const members = cards.filter((c) => c.parent_id === card.id);
  const candidates = cards.filter((c) => selectedIds.includes(c.id) && c.type !== "legion" && c.parent_id !== card.id && !c.ephemeral);
  const [instruction, setInstruction] = useState(String(card.config.instruction ?? ""));
  const [model, setModel] = useState(String(card.config.model_override ?? ""));
  const [rows, setRows] = useState<VariableRow[]>([]);
  const [dirty, setDirty] = useState(false);
  const [name, setName] = useState(card.name);
  const [revision, setRevision] = useState<number>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => setName(card.name), [card.name]);
  useEffect(() => setInstruction(String(card.config.instruction ?? "")), [card.config.instruction]);
  useEffect(() => setModel(String(card.config.model_override ?? "")), [card.config.model_override]);
  useEffect(() => {
    let active = true;
    worldApi.getLegionState(card.id).then((state) => {
      if (active) { setRows(variablesFromValue(state.value)); setDirty(false); setRevision(state.revision); }
    }).catch((e) => { if (active) setError(apiErrorMessage(e)); });
    return () => { active = false; };
  }, [card.id]);
  const refresh = async () => {
    setBusy(true); setError(""); setSaved(false);
    try { const state = await worldApi.getLegionState(card.id); setRows(variablesFromValue(state.value)); setDirty(false); setRevision(state.revision); }
    catch (e) { setError(apiErrorMessage(e)); }
    finally { setBusy(false); }
  };
  const persistVariables = async () => {
    if (!dirty) return;
    if (revision === undefined) throw new Error("Wait for shared variables to load before saving.");
    const value = variablesToValue(rows);
    const state = await worldApi.saveLegionState(card.id, value, revision);
    setRevision(state.revision); setDirty(false); setSaved(true);
  };
  const saveState = async () => {
    setBusy(true); setError(""); setSaved(false);
    try { await persistVariables(); }
    catch (e) { setError(apiErrorMessage(e)); }
    finally { setBusy(false); }
  };
  const saveTemplate = async () => {
    setBusy(true); setError("");
    try {
      if (!name.trim()) throw new Error("Give your Legion a name before saving it to the library.");
      await persistVariables();
      const config = { instruction, model_override: model.trim() };
      await updateCard(card.id, { name: name.trim(), config });
      const current = useWorldStore.getState().cards.find((c) => c.id === card.id);
      if (!current || current.name !== name.trim() || current.config.instruction !== instruction || current.config.model_override !== model.trim()) {
        throw new Error("Team settings were not saved. Try again before saving to the library.");
      }
      await createLegion({ name: current.name, description: String(current.config.description ?? ""),
        nodeIds: [card.id, ...useWorldStore.getState().cards.filter((c) => c.parent_id === card.id).map((c) => c.id)] });
    } catch (e) { setError(apiErrorMessage(e)); }
    finally { setBusy(false); }
  };
  return <section className={`legion-container ${selected ? "is-selected" : ""}`} data-card-id={card.id} data-card-type="legion" aria-label={`${card.name} team space`}>
    {selected && <NodeResizeControl position="bottom-right" minWidth={800} minHeight={550} maxWidth={4096} maxHeight={4096}
      onResizeEnd={(_event, params) => void updateCard(card.id, { size: { width: params.width, height: params.height } })} />}
    <header className="legion-drag-region legion-container-header">
      <Layers3 size={24} />
      <div><span>LEGION · TEAM SPACE</span><input className="nodrag nopan" aria-label="Legion name" value={name} onChange={(e) => setName(e.target.value)}
        onBlur={(e) => { const name = e.target.value.trim(); if (name && name !== card.name) void updateCard(card.id, { name }); }} /></div>
      <span className="legion-count">{members.length} members · {card.config.paused ? "Paused" : "Active"}</span>
      <button className="secondary-button nodrag nopan" onClick={() => void updateCard(card.id, { config: { paused: !card.config.paused } })}
        title="Pause prevents new member Runs; active Runs continue.">
        {card.config.paused ? <Play size={14} /> : <Pause size={14} />}{card.config.paused ? "Resume team" : "Pause team"}
      </button>
      <button className="secondary-button nodrag nopan" disabled={busy} title="Remove the team container; keep members and their connections"
        onClick={async () => { setBusy(true); try { await dissolveLegion(card.id); } finally { setBusy(false); } }}><Ungroup size={14} /> Dissolve</button>
      <button className="secondary-button nodrag nopan" disabled={busy} aria-label="Delete Legion and members" title="Delete this Legion and all its members. Ctrl+Z to undo."
        onClick={async () => { setBusy(true); try { await deleteCards([card.id, ...members.map((c) => c.id)]); } finally { setBusy(false); } }}><Trash2 size={14} /></button>
    </header>
    <aside className="legion-controls nodrag nopan nowheel">
      <label className="field-label"><span>Team instruction</span><textarea rows={4} value={instruction} maxLength={16000}
        placeholder="Shared purpose and coordination rules"
        onChange={(e) => setInstruction(e.target.value)} onBlur={() => { if (instruction !== card.config.instruction) void updateCard(card.id, { config: { instruction } }); }} /></label>
      <label className="field-label"><span>Team model override</span><input value={model} placeholder="Use each agent’s model" maxLength={200}
        onChange={(e) => setModel(e.target.value)} onBlur={() => { if (model !== card.config.model_override) void updateCard(card.id, { config: { model_override: model.trim() } }); }} /></label>
      <p className="legion-help">Settings apply when a member starts its next Run.</p>
      <label className="field-label"><span>Member state access</span><select value={String(card.config.shared_state_access ?? "read_write")}
        onChange={(e) => void updateCard(card.id, { config: { shared_state_access: e.target.value } })}>
        <option value="read_write">Read and write</option><option value="read_only">Read only</option>
      </select></label>
      <SharedVariablesEditor rows={rows} disabled={busy || revision === undefined} onChange={(value) => { setRows(value); setDirty(true); setSaved(false); }} />
      <div className="action-row"><button className="secondary-button" disabled={busy} onClick={() => void refresh()} title="Replace this draft with the latest saved variables"><RefreshCw size={13} /> Reload</button>
        <button className="primary-button" disabled={busy || revision === undefined || !dirty} onClick={() => void saveState()}><Save size={13} /> {saved ? "Saved" : "Save variables"}</button></div>
      {dirty && <p className="legion-help">Unsaved variables. Saving to the library saves these changes too.</p>}
      {error && <p className="legion-error" role="alert">{error}</p>}
      <div className="section-heading"><span>Members</span><small>independent connections</small></div>
      <div className="legion-member-list">{members.map((member) => <div key={member.id}><span title={member.name}>{member.name}</span>
        <button onClick={() => void setMembership([member.id], null)} aria-label={`Detach ${member.name}`}>Detach</button></div>)}</div>
      {candidates.length > 0 && <button className="secondary-button" onClick={() => void setMembership(candidates.map((c) => c.id), card.id)}>Add {candidates.length} selected cards</button>}
      <p className="legion-help">Save this team to the library when ready. New copies include its settings and shared variables.</p>
      <div className="action-row"><button className="primary-button" disabled={members.length === 0 || busy || revision === undefined}
        onClick={() => void saveTemplate()}><Save size={13} /> Save to library</button></div>
    </aside>
    {members.length === 0 && <div className="legion-empty">Select this Legion and other cards, then choose “Add selected cards”.<br />Member nodes can connect to the world outside.</div>}
  </section>;
}
