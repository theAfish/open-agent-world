import { Boxes, Download, FilePlus2, Plus, RefreshCw, Settings2, Trash2, Wrench, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { apiErrorMessage, nodeDocumentDownloadUrl, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import { SkillDefaultsEditor, settingsFromValue, settingsToValue, type SettingRow } from "./SkillDefaultsEditor";
import { SkillFilesEditor, type SkillFiles } from "./SkillFilesEditor";

interface Skill {
  id: string; name: string; description: string; instructions: string;
  node_id?: string | null;
  files: SkillFiles; directories?: string[]; defaults: Record<string, unknown>;
}
interface Package {
  package_id: string; version: string; name: string; description: string; author: string;
  instructions: string; skills: Skill[]; source: { plugin_id: string; version: string } | null;
}
interface Snapshot { value: Package; revision: number; summary: { total: number; names: string[] } }
const snapshot = (value: unknown, single = false): Snapshot => {
  const result = value as Snapshot;
  if (!single) return result;
  const skill = result.value as unknown as Skill;
  return { ...result, value: { package_id: "local.skill", version: "0.1.0", name: skill.name, description: skill.description, author: "", instructions: "", source: null, skills: [skill] }, summary: { total: 1, names: [skill.name] } };
};

function useToolbox(id: string, single = false) {
  const eventId = useWorldStore((state) => state.events.find((event) => event.payload.scope_kind === "node_document" && event.payload.owner_id === id)?.id);
  const socketState = useWorldStore((state) => state.socketState);
  const [box, setBox] = useState<Snapshot>();
  const [error, setError] = useState("");
  const accept = useCallback((next: Snapshot) => setBox((current) => !current || next.revision >= current.revision ? next : current), []);
  const reload = useCallback(async () => {
    try { accept(snapshot(await worldApi.getNodeDocument(id), single)); setError(""); }
    catch (error) { setError(apiErrorMessage(error)); }
  }, [id, accept, single]);
  useEffect(() => {
    let active = true;
    worldApi.getNodeDocument(id).then((value) => { if (active) accept(snapshot(value, single)); })
      .catch((error) => { if (active) setError(apiErrorMessage(error)); });
    return () => { active = false; };
  }, [id, eventId, socketState, accept, single]);
  return { box, accept, reload, error, setError };
}

export function SkillToolboxPreview({ card, single = false }: { card: WorldCard; single?: boolean }) {
  const { box, error } = useToolbox(card.id, single);
  if (single) {
    const skill = box?.value.skills[0];
    return <div className="node-preview-summary toolbox-preview">
      <p>{skill?.description || skill?.instructions || error || "Add instructions, settings and files to this skill."}</p>
      <div className="node-preview-metadata"><span><Wrench size={12} /> Skill</span><span>{Object.keys(skill?.files ?? {}).length} files</span></div>
    </div>;
  }
  return <div className="node-preview-summary toolbox-preview">
    <p>{box ? box.value.description || (box.summary.total ? box.summary.names.join(" · ") : "An empty toolbox, ready for your skills.") : error || "Loading skills…"}</p>
    <div className="node-preview-metadata"><span><Boxes size={12} /> {box?.summary.total ?? 0} skills</span>
      <span>{box?.value.source ? `${box.value.author || box.value.source.plugin_id} · v${box.value.source.version}` : "Your collection"}</span></div>
  </div>;
}

export function SkillNodeBody({ card, workspace = false }: { card: WorldCard; workspace?: boolean }) {
  return <SkillToolboxBody card={card} workspace={workspace} single />;
}

export function SkillToolboxBody({ card, workspace = false, single = false }: { card: WorldCard; workspace?: boolean; single?: boolean }) {
  const { box, accept, reload, error, setError } = useToolbox(card.id, single);
  const updateCard = useWorldStore((state) => state.updateCard);
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState("");
  const [skillTab, setSkillTab] = useState<"instructions" | "settings" | "files">("instructions");
  const [draft, setDraft] = useState<{ skill: Skill; defaults: SettingRow[]; revision: number; isNew: boolean }>();
  const [settings, setSettings] = useState<{ value: Package; revision: number }>();
  const editing = !!draft || !!settings;
  const mutate = async (action: string, args: Record<string, unknown>, revision: number) => {
    setBusy(true); setError("");
    try {
      accept(snapshot(await worldApi.nodeDocumentAction(card.id, single ? "replace" : action, args, revision), single));
      if (single && typeof args.name === "string") await updateCard(card.id, { name: args.name });
      return true;
    }
    catch (error) { setError(apiErrorMessage(error)); return false; }
    finally { setBusy(false); }
  };
  const select = (skill: Skill, isNew = false) => {
    setSettings(undefined);
    setSkillTab("instructions");
    setDraft({ skill: { ...skill, files: { ...skill.files } }, defaults: settingsFromValue(skill.defaults), revision: box!.revision, isNew });
  };
  const add = () => select({ id: `skill_${crypto.randomUUID().slice(0, 8)}`, name: "", description: "", instructions: "", defaults: {}, files: {} }, true);
  const patch = (value: Partial<Skill>) => setDraft((current) => current ? { ...current, skill: { ...current.skill, ...value } } : current);
  const saveSkill = async () => {
    if (!draft) return;
    try {
      const defaults = settingsToValue(draft.defaults);
      if (await mutate("upsert", { ...draft.skill, defaults }, draft.revision)) setDraft(undefined);
    } catch (error) { setError(apiErrorMessage(error)); }
  };
  const importInstructions = async (file: File) => {
    try { patch({ instructions: await file.text() }); }
    catch (error) { setError(apiErrorMessage(error)); }
  };
  return <div className={`skill-toolbox nowheel ${workspace ? "is-workspace" : ""}`}>
    <header className="toolbox-heading"><div><span className="toolbox-eyebrow">{single ? "SKILL" : "SKILL TOOLBOX"}</span><h3>{box?.value.name ?? "Loading toolbox…"}</h3></div>{single ? <Wrench size={28} strokeWidth={1.3} /> : <Boxes size={28} strokeWidth={1.3} />}</header>
    <p className="toolbox-help">{box?.value.description || (single ? "Connect an Agent with Use skill to share these instructions, settings and files." : "Collect the tools you use together. Connect an Agent with “Use skills” to let it open the right tool for the job.")}</p>
    {box?.value.source && <p className="toolbox-origin">From {box.value.author || box.value.source.plugin_id} · v{box.value.source.version} · Editable local copy</p>}
    <div className="toolbox-toolbar">
      {!single && <>
      <button className="primary-button" onClick={add} disabled={!box || busy || editing}><Plus size={14} /> Add skill</button>
      <button className="secondary-button" aria-label="Toolbox settings" disabled={!box || busy || editing} onClick={() => setSettings({ value: { ...box!.value }, revision: box!.revision })}><Settings2 size={14} /> Toolbox</button>
      <a className="secondary-button toolbox-export" href={nodeDocumentDownloadUrl(card.id, "plugin")} aria-disabled={!box || busy || editing}
        onClick={(event) => { if (!box || busy || editing) event.preventDefault(); }} title={editing ? "Save or close your draft before exporting" : "Download this toolbox as an installable plugin"}><Download size={14} /> Export plugin</a>
      </>}
      {single && card.parent_id && <button className="secondary-button" disabled={busy || editing} onClick={() => void updateCard(card.id, { parent_id: null })}>Detach skill</button>}
      <button className="secondary-button" aria-label="Reload toolbox" disabled={busy} onClick={() => { setDraft(undefined); setSettings(undefined); void reload(); }}><RefreshCw size={14} /></button>
    </div>
    {error && <p className="toolbox-error" role="alert">{error}</p>}
    {settings && <form className="toolbox-settings toolbox-editor" aria-label="Toolbox settings" onSubmit={async (event) => {
      event.preventDefault(); const { skills: _skills, source: _source, ...metadata } = settings.value;
      if (await mutate("configure", metadata, settings.revision)) setSettings(undefined);
    }}><header><strong>About this toolbox</strong><button type="button" aria-label="Close toolbox settings" disabled={busy} onClick={() => setSettings(undefined)}><X size={16} /></button></header>
      <fieldset disabled={busy}>
        <label>Toolbox name<input required maxLength={120} value={settings.value.name} onChange={(event) => setSettings({ ...settings, value: { ...settings.value, name: event.target.value } })} /></label>
        <label>Description<textarea rows={2} maxLength={500} value={settings.value.description} onChange={(event) => setSettings({ ...settings, value: { ...settings.value, description: event.target.value } })} /></label>
        <label>Shared instructions<textarea rows={5} placeholder="When to use these skills, working conventions, and how the tools fit together." value={settings.value.instructions} onChange={(event) => setSettings({ ...settings, value: { ...settings.value, instructions: event.target.value } })} /></label>
        <div className="toolbox-pair"><label>Author<input maxLength={120} value={settings.value.author} onChange={(event) => setSettings({ ...settings, value: { ...settings.value, author: event.target.value } })} /></label>
          <label>Version<input required pattern="[0-9]+\.[0-9]+\.[0-9]+" value={settings.value.version} onChange={(event) => setSettings({ ...settings, value: { ...settings.value, version: event.target.value } })} /></label></div>
        <label>Package ID<input required maxLength={100} pattern="[a-z][a-z0-9]*([.-][a-z0-9]+)*" value={settings.value.package_id} onChange={(event) => setSettings({ ...settings, value: { ...settings.value, package_id: event.target.value } })} /></label>
        <p className="toolbox-help">Keep the ID for future versions. Choose your own ID when sharing a separate fork. New plugin versions appear in newly created cards; your existing copies stay as you arranged them.</p>
        <button className="primary-button">Save toolbox</button>
      </fieldset>
    </form>}
    <div className="toolbox-layout">
      <section className="toolbox-tools" aria-label="Skills">
        {!single && !!box?.value.skills.length && <input className="toolbox-search" aria-label="Search skills" placeholder="Find a tool…" value={search} onChange={(event) => setSearch(event.target.value)} />}
        {box?.value.skills.length === 0 && <div className="toolbox-empty"><Wrench size={30} strokeWidth={1.3} /><strong>Start with one useful tool.</strong><p>Add a skill, write its instructions, or import a SKILL.md file.</p></div>}
        {box?.value.skills.filter((skill) => `${skill.name} ${skill.description}`.toLowerCase().includes(search.toLowerCase())).map((skill) =>
          <button key={skill.node_id || skill.id} className={`toolbox-tool ${draft?.skill.id === skill.id && draft?.skill.node_id === skill.node_id ? "is-selected" : ""}`} aria-label={`Edit skill ${skill.name}`} disabled={busy || editing} onClick={() => select(skill)}>
            <Wrench size={18} /><span><strong>{skill.name}</strong><small>{skill.description || "Open instructions"}</small><em>{Object.keys(skill.files).length} files</em></span>
          </button>)}
        {!!box?.value.skills.length && box.value.skills.every((skill) => !`${skill.name} ${skill.description}`.toLowerCase().includes(search.toLowerCase())) && <p className="toolbox-help">No skills match your search.</p>}
      </section>
      {draft && <form className="toolbox-editor" aria-label="Skill details" onSubmit={(event) => { event.preventDefault(); void saveSkill(); }}>
        <header><strong>{draft.isNew ? "New skill" : draft.skill.name}</strong><button type="button" aria-label="Close skill details" disabled={busy} onClick={() => setDraft(undefined)}><X size={16} /></button></header>
        <fieldset disabled={busy}>
          <label>Skill name<input required maxLength={120} value={draft.skill.name} onChange={(event) => patch({ name: event.target.value })} /></label>
          <label>When to use<textarea rows={2} maxLength={1000} value={draft.skill.description} onChange={(event) => patch({ description: event.target.value })} /></label>
          <div className="toolbox-skill-tabs" role="group" aria-label="Skill sections">
            <button type="button" aria-pressed={skillTab === "instructions"} onClick={() => setSkillTab("instructions")}>Instructions</button>
            <button type="button" aria-pressed={skillTab === "settings"} onClick={() => setSkillTab("settings")}>Default settings</button>
            <button type="button" aria-pressed={skillTab === "files"} onClick={() => setSkillTab("files")}>Files and folders</button>
          </div>
          {skillTab === "instructions" && <>
            <label>Instructions<textarea className="toolbox-instructions" rows={10} value={draft.skill.instructions} placeholder="Describe how to do this work…" onChange={(event) => patch({ instructions: event.target.value })} /></label>
            <label className="toolbox-file-input"><FilePlus2 size={14} /> Import SKILL.md<input type="file" accept=".md,.txt" aria-label="Import SKILL.md" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importInstructions(file); event.target.value = ""; }} /></label>
          </>}
          {skillTab === "settings" && <section className="toolbox-section" aria-label="Default settings">
            <p className="toolbox-help">Give the skill useful starting values, such as a language, output format, or iteration limit.</p>
            <SkillDefaultsEditor rows={draft.defaults} onChange={(defaults) => setDraft((current) => current ? { ...current, defaults } : current)} />
          </section>}
          <div hidden={skillTab !== "files"}>
            <SkillFilesEditor files={draft.skill.files} directories={draft.skill.directories ?? []}
              onChange={(files, directories) => patch({ files, directories })} onInstructions={(instructions) => patch({ instructions })}
              onError={setError} onBusy={setBusy} />
          </div>
          <div className="toolbox-editor-actions">{!draft.isNew && !single && <button type="button" className="secondary-button" onClick={async () => { if (await mutate("remove", { skill_id: draft.skill.node_id || draft.skill.id }, draft.revision)) setDraft(undefined); }}><Trash2 size={14} /> Detach skill</button>}<button className="primary-button">Save skill</button></div>
        </fieldset>
      </form>}
    </div>
    {!single && !editing && box && <footer className="toolbox-footer"><span>{box.summary.total} skills · v{box.value.version}</span><span>Instructions load on demand</span></footer>}
  </div>;
}
