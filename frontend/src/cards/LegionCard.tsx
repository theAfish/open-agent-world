import { t, useLocale } from "../i18n";
import { ModelSelect } from "./ModelSelect";
import { NodeToolbar, Position, useReactFlow, type NodeProps } from "@xyflow/react";
import { Layers3, PanelsTopLeft, Pause, Play, RefreshCw, Save, Settings2, X } from "lucide-react";
import { useLegionWorkspace } from '../state/legionWorkspace';
import { useEffect, useRef, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { AddSelectedMembers, ContainerActions, ContainerFrame, ContainerMembers } from "./ContainerFrame";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import type { CanvasNode } from "./types";
import { SharedVariablesEditor, variablesFromValue, variablesToValue, type VariableRow } from "./SharedVariablesEditor";

export function LegionCardNode({ data, selected }: NodeProps<CanvasNode>) {
  useLocale();
  const card = data.card;
  const { getViewport, setViewport } = useReactFlow();
  const sidebarRef = useRef<HTMLElement>(null);
  const settingsButtonRef = useRef<HTMLButtonElement>(null);
  const cards = useWorldStore((s) => s.cards);
  const updateCard = useWorldStore((s) => s.updateCard);
  const createLegion = useWorldStore((s) => s.createLegion);
  const members = cards.filter((c) => c.parent_id === card.id);
  const [instruction, setInstruction] = useState(String(card.config.instruction ?? ""));
  const [model, setModel] = useState(String(card.config.model_override ?? ""));
  const [rows, setRows] = useState<VariableRow[]>([]);
  const [dirty, setDirty] = useState(false);
  const [name, setName] = useState(card.name);
  const [revision, setRevision] = useState<number>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [mode, setMode] = useState(String(card.config.mode ?? 'team'));
  const positionBusy = useWorldStore(s => s.positionCommitBusy);
  const dragging = useNodeSurfaceStore(s => s.dragging);
  const team = mode === 'team';
  useEffect(() => setMode(String(card.config.mode ?? 'team')), [card.config.mode]);
  const changeMode = async (enabled: boolean) => {
    setBusy(true);
    setMode(enabled ? 'team' : 'group');
    try { await updateCard(card.id, { config: { mode: enabled ? 'team' : 'group' } }); }
    finally {
      setMode(String(useWorldStore.getState().cards.find(c => c.id === card.id)?.config.mode ?? 'team'));
      setBusy(false);
    }
  };
  useEffect(() => {
    if (!settingsOpen) return;
    // Reveal the attached sidebar by panning only; never move or resize member nodes.
    const frame = requestAnimationFrame(() => {
      const panel = sidebarRef.current;
      const bounds = panel?.getBoundingClientRect();
      const canvas = panel?.closest('.react-flow')?.getBoundingClientRect();
      if (!bounds || !canvas) return;
      const dx = bounds.right > canvas.right - 16 ? canvas.right - 16 - bounds.right : Math.max(0, canvas.left + 16 - bounds.left);
      const dy = bounds.bottom > canvas.bottom - 200 ? canvas.bottom - 200 - bounds.bottom : Math.max(0, canvas.top + 16 - bounds.top);
      if (dx || dy) {
        const viewport = getViewport();
        void setViewport({ ...viewport, x: viewport.x + dx, y: viewport.y + dy });
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [settingsOpen, getViewport, setViewport]);
  const closeSettings = () => { setSettingsOpen(false); settingsButtonRef.current?.focus({ preventScroll: true }); };
  useEffect(() => setName(card.name), [card.name]);
  useEffect(() => setInstruction(String(card.config.instruction ?? "")), [card.config.instruction]);
  useEffect(() => setModel(String(card.config.model_override ?? "")), [card.config.model_override]);
  useEffect(() => {
    if (!settingsOpen || revision !== undefined) return;
    let active = true;
    worldApi.getLegionState(card.id).then((state) => {
      if (active) { setRows(variablesFromValue(state.value)); setDirty(false); setRevision(state.revision); }
    }).catch((e) => { if (active) setError(apiErrorMessage(e)); });
    return () => { active = false; };
  }, [card.id, settingsOpen]);
  const refresh = async () => {
    setBusy(true); setError(""); setSaved(false);
    try { const state = await worldApi.getLegionState(card.id); setRows(variablesFromValue(state.value)); setDirty(false); setRevision(state.revision); }
    catch (e) { setError(apiErrorMessage(e)); }
    finally { setBusy(false); }
  };
  const persistVariables = async () => {
    if (!dirty) return;
    if (revision === undefined) throw new Error(t("Wait for shared variables to load before saving."));
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
      if (!name.trim()) throw new Error(t("Give your Legion a name before saving it to the library."));
      await persistVariables();
      const config = { instruction, model_override: model.trim() };
      await updateCard(card.id, { name: name.trim(), config });
      const current = useWorldStore.getState().cards.find((c) => c.id === card.id);
      if (!current || current.name !== name.trim() || current.config.instruction !== instruction || current.config.model_override !== model.trim()) {
        throw new Error(t("Team settings were not saved. Try again before saving to the library."));
      }
      await useWorldStore.getState().waitForPositionCommits();
      const result = await createLegion({ name: current.name, description: String(current.config.description ?? ""),
        nodeIds: [card.id, ...useWorldStore.getState().cards.filter((c) => c.parent_id === card.id).map((c) => c.id)] });
      if (!result) throw new Error(t("Legion was not saved. Check the notification and try again."));
    } catch (e) { setError(apiErrorMessage(e)); }
    finally { setBusy(false); }
  };
  return <ContainerFrame card={card} selected={selected} className="legion-container" label={t("{v0} team space", { v0: String(card.name) })} header={<>
      <Layers3 size={24} />
      <div><span>{team ? t("LEGION · TEAM SPACE") : t("LEGION · GROUP")}</span><input className="nodrag nopan" aria-label={t("Legion name")} value={name} onChange={(e) => setName(e.target.value)}
        onBlur={(e) => { const name = e.target.value.trim(); if (name && name !== card.name) void updateCard(card.id, { name }); }} /></div>
      <span className="legion-count">{members.length} {t("members")}{team && card.config.paused ? ` · ${t("Paused")}` : ''}</span>
      <AddSelectedMembers card={card} />
      <button data-tutorial="legion-open" className="secondary-button nodrag nopan" onClick={() => useLegionWorkspace.getState().open(card.id)}><PanelsTopLeft size={14} /> {t('Workspace mode')}</button>
      <button ref={settingsButtonRef} onFocus={event => event.stopPropagation()} className="secondary-button nodrag nopan" aria-expanded={settingsOpen} aria-controls={`legion-settings-${card.id}`} onClick={() => setSettingsOpen(open => !open)}><Settings2 size={14} /> {t("Legion settings")}</button>
      <button className="primary-button nodrag nopan" disabled={members.length === 0 || busy || positionBusy || dragging}
        onClick={() => void saveTemplate()} title={t("Save nodes, connections, positions and display states (1–4).") }><Save size={14} /> {t("Save to library")}</button>
      <ContainerActions card={card} busy={busy} deleteLabel="Delete Legion and members" />
    </>}>
    {error && !settingsOpen && <p className="legion-save-error" role="alert">{error}</p>}
    {settingsOpen && <NodeToolbar nodeId={card.id} isVisible position={Position.Right} align="start" offset={12}><aside ref={sidebarRef} id={`legion-settings-${card.id}`} className="legion-settings-sidebar nodrag nopan nowheel" aria-label={t("Legion settings")}
      onFocus={event => event.stopPropagation()} onPointerDown={event => event.stopPropagation()} onClick={event => event.stopPropagation()}
      onKeyDown={event => { event.stopPropagation(); if (event.key === 'Escape') closeSettings(); }}>
      <header><div><strong>{card.name}</strong><small>{t("Legion settings")}</small></div><button autoFocus className="icon-button" aria-label={t("Close Legion settings")} onClick={closeSettings}><X size={18} /></button></header>
    <div className="legion-controls">
      <label className="legion-mode-toggle"><input type="checkbox" checked={team} disabled={busy} onChange={e => void changeMode(e.target.checked)} /><span>{t("Enable shared team settings")}</span></label>
      <p className="legion-help">{team ? t("Member Agents receive these settings and shared variables when starting a Run.") : t("Group only: organize cards without sharing instructions, models or variables. Saved team settings stay inactive.")}</p>
      {team && <>
      <label className="field-label"><span>{t("Team instruction")}</span><textarea rows={4} value={instruction} maxLength={16000}
        placeholder={t("Shared purpose and coordination rules")}
        onChange={(e) => setInstruction(e.target.value)} onBlur={() => { if (instruction !== card.config.instruction) void updateCard(card.id, { config: { instruction } }); }} /></label>
      <div className="field-label"><span>{t("Team model override")}</span><ModelSelect label={t("Team model override")} value={model} allowEmpty onChange={value => {
        setModel(value); if (value !== card.config.model_override) void updateCard(card.id, { config: { model_override: value } });
      }} /></div>
      <p className="legion-help">{t("Settings apply when a member starts its next Run.")}</p>
      <button className="secondary-button" onClick={() => void updateCard(card.id, { config: { paused: !card.config.paused } })}
        title={t("Pause prevents new member Runs; active Runs continue.")}>
        {card.config.paused ? <Play size={14} /> : <Pause size={14} />}{card.config.paused ? t("Resume team") : t("Pause team")}
      </button>
      <label className="field-label"><span>{t("Member state access")}</span><select value={String(card.config.shared_state_access ?? "read_write")}
        onChange={(e) => void updateCard(card.id, { config: { shared_state_access: e.target.value } })}>
        <option value="read_write">{t("Read and write")}</option><option value="read_only">{t("Read only")}</option>
      </select></label>
      <SharedVariablesEditor rows={rows} disabled={busy || revision === undefined} onChange={(value) => { setRows(value); setDirty(true); setSaved(false); }} />
      <div className="action-row"><button className="secondary-button" disabled={busy} onClick={() => void refresh()} title={t("Replace this draft with the latest saved variables")}><RefreshCw size={13} /> {t("Reload")}</button>
        <button className="primary-button" disabled={busy || revision === undefined || !dirty} onClick={() => void saveState()}><Save size={13} /> {saved ? t("Saved") : t("Save variables")}</button></div>
      {dirty && <p className="legion-help">{t("Unsaved variables. Saving to the library saves these changes too.")}</p>}
      </>}
      {error && <p className="legion-error" role="alert">{error}</p>}
      <div className="section-heading"><span>{t("Members")}</span><small>{t("independent connections")}</small></div>
      <ContainerMembers card={card} />
      <p className="legion-help">{t("Save nodes, connections, positions and display states (1–4).")}</p>
    </div>
    </aside></NodeToolbar>}
    {members.length === 0 && <div className="legion-empty">{t("Select this Legion and other cards, then choose “Add selected cards”.")}<br />{t("Member nodes can connect to the world outside.")}</div>}
  </ContainerFrame>;
}
