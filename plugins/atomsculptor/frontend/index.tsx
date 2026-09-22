import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type ReactElement } from "react";
import { useFileViewer } from "@oaw/plugin-api";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";
import { downloadStructure, exportStructure, parseStructure, type Atom, type InterfaceCandidate, type Structure, type StructureFormat } from "./formats";
import { ELEMENT_COLORS, StructureViewport, type CameraProjection, type CameraView, type EditorMode, type ObservationCameraView, type StructureViewportCapture } from "./legacy/StructureViewport";
import { addLayer as createLayer, applyLattice, deleteActiveLayers, expandSelection, extractSelection, invertSelection, mergeActiveLayers, selectByTypes, useLayerLattice, wrapToCell } from "./operations";
import { minimumImageDistance } from "./periodic";
import "./workspace.css";

type Snapshot = { structure: Structure; revision: number };
type StructureLinks = { agent_id?: string | null; sandbox_id?: string | null; skill_id?: string | null };
const clone = (value: Structure) => JSON.parse(JSON.stringify(value)) as Structure;
const ELEMENTS = "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split(" ");
const decodeFileData = (data: string) => new TextDecoder().decode(Uint8Array.from(atob(data), character => character.charCodeAt(0)));
const structureExtension = (format: StructureFormat) => format === "poscar" ? "vasp" : format;
const triple = (label: string, raw: string): number[] => {
  const values = raw.split(/[\s,;]+/).filter(Boolean).map(Number);
  if (values.length !== 3 || values.some(value => !Number.isFinite(value))) throw new Error(`${label} must contain three numbers.`);
  return values;
};
const integer = (label: string, raw: string): number => {
  const value = Number(raw);
  if (!Number.isInteger(value)) throw new Error(`${label} must be an integer.`);
  return value;
};
const decimal = (label: string, raw: string): number => {
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new Error(`${label} must be a number.`);
  return value;
};
const matrix = (label: string, raw: string): number[][] => {
  const values = raw.split(/[\s,]+/).filter(Boolean).map(Number);
  if (values.length !== 9 || values.some(value => !Number.isFinite(value))) throw new Error(`${label} must contain nine finite numbers.`);
  return [values.slice(0, 3), values.slice(3, 6), values.slice(6, 9)];
};
const multiplyMatrices = (left: number[][], right: number[][]) => left.map((row, rowIndex) => row.map((_, columnIndex) => left[rowIndex].reduce((sum, value, index) => sum + value * right[index][columnIndex], 0)));
type SaveFileHandle = { createWritable: () => Promise<{ write: (content: string) => Promise<void>; close: () => Promise<void> }> };
type SavePickerWindow = Window & { showSaveFilePicker?: (options: { suggestedName: string; types: Array<{ description: string; accept: Record<string, string[]> }> }) => Promise<SaveFileHandle> };

function AtomField({ label, value, numeric, onCommit }: {
  label: string; value: string | number; numeric?: boolean; onCommit: (value: string) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => { setDraft(String(value)); }, [value]);
  return <input aria-label={label} type={numeric ? "number" : "text"} value={draft} maxLength={numeric ? undefined : 3} step={numeric ? "0.01" : undefined}
    onChange={event => setDraft(event.target.value)} onBlur={() => onCommit(draft)}
    onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); }} />;
}

function ViewportReadout({ structure, selected, hoveredAtom, mode, distance }: {
  structure: Structure; selected: Set<number>; hoveredAtom: Atom | null; mode: EditorMode; distance: number | null;
}) {
  const focus = hoveredAtom ?? (selected.size === 1 ? structure.atoms.find(atom => selected.has(atom.id)) ?? null : null);
  return <>
    <div className="atomsculptor-atom-info" aria-live="polite">
      {focus ? <><strong>{focus.symbol} <small>#{focus.id}</small></strong><span>{focus.x.toFixed(4)}, {focus.y.toFixed(4)}, {focus.z.toFixed(4)} Å</span></>
        : <><strong>{selected.size ? `${selected.size} atoms selected` : "Structure ready"}</strong><span>{mode === "measure" ? "Choose two atoms to measure" : "Click to select · ⇧ click to add"}</span></>}
    </div>
    <footer className="atomsculptor-statusbar"><span>{structure.source_name || "Untitled structure"}</span><span>{structure.atoms.length} atoms</span><span>{selected.size} selected</span>{distance !== null && <span>Distance {distance.toFixed(4)} Å</span>}</footer>
  </>;
}

type ToolIconName = "select" | "box" | "measure" | "move" | "rotate" | "scale" | "add" | "delete" | "copy" | "cut" | "paste" | "undo" | "redo" | "import" | "reset";
function ToolIcon({ name }: { name: ToolIconName }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  const art: Record<ToolIconName, ReactElement> = {
    select: <><circle cx="12" cy="12" r="7" /><ellipse cx="12" cy="12" rx="10" ry="3.5" transform="rotate(-25 12 12)" /></>,
    box: <rect x="4" y="4" width="16" height="16" rx="1" strokeDasharray="3 2" />,
    measure: <><path d="M5 18 18 5" /><path d="m5 14 2 2m2-6 2 2m2-6 2 2m2-6 2 2" /></>,
    move: <><path d="m5 9-3 3 3 3m4-10 3-3 3 3m0 14-3 3-3-3m10-7 3-3-3-3M2 12h20M12 2v20" /></>,
    rotate: <><path d="M20 12a8 8 0 1 1-3.3-6.5" /><path d="M20 4v5h-5" /></>,
    scale: <><path d="M4 9V4h5m6 16h5v-5M4 4l16 16" /></>,
    add: <><path d="M12 5v14M5 12h14" /></>,
    delete: <><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13" /></>,
    copy: <><rect x="8" y="7" width="11" height="13" rx="2" /><path d="M5 16V5a2 2 0 0 1 2-2h8" /></>,
    cut: <><path d="m6 4 12 16M18 4 6 20" /><circle cx="6" cy="5" r="2" /><circle cx="6" cy="19" r="2" /></>,
    paste: <><path d="M9 5V3h6v2" /><rect x="6" y="5" width="12" height="16" rx="2" /><path d="M9 12h6m-3-3v6" /></>,
    undo: <><path d="M9 7 4 12l5 5" /><path d="M5 12h9a6 6 0 0 1 6 6" /></>,
    redo: <><path d="m15 7 5 5-5 5" /><path d="M19 12h-9a6 6 0 0 0-6 6" /></>,
    import: <><path d="M12 21V9m-5 5 5-5 5 5" /><path d="M5 4h14" /></>,
    reset: <><circle cx="12" cy="12" r="8" /><path d="M12 2v5m0 10v5M2 12h5m10 0h5" /></>,
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true" {...common}>{art[name]}</svg>;
}

function Preview({ card, host }: PluginViewProps) {
  const [structure, setStructure] = useState<Structure | null>(null);
  useEffect(() => { void host.readDocument().then(result => setStructure(result.value as Structure)); }, [host]);
  return <p className="atomsculptor-summary">{structure ? `${structure.atoms.length} atoms · ${structure.selected_atom_ids.length} selected` : card.name}</p>;
}

function Workspace({ card, host, level }: PluginViewProps) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [undo, setUndo] = useState<Snapshot[]>([]);
  const [redo, setRedo] = useState<Snapshot[]>([]);
  const [clipboard, setClipboard] = useState<Omit<Atom, "id" | "layer_id">[]>([]);
  const [mode, setMode] = useState<EditorMode>("select");
  const [cameraView, setCameraView] = useState<CameraView>("iso");
  const [projection, setProjection] = useState<CameraProjection>("perspective");
  const [cameraNonce, setCameraNonce] = useState(0);
  const [measurement, setMeasurement] = useState<number[]>([]);
  const [selectionVisible, setSelectionVisible] = useState(true);
  const [hoveredAtomId, setHoveredAtomId] = useState<number | null>(null);
  const [agents, setAgents] = useState<Array<{ id: string; name: string }>>([]);
  const [sandboxes, setSandboxes] = useState<Array<{ id: string; name: string }>>([]);
  const [skills, setSkills] = useState<Array<{ id: string; name: string }>>([]);
  const [addPanelOpen, setAddPanelOpen] = useState(false);
  const [newSymbol, setNewSymbol] = useState("H");
  const [newCoordinates, setNewCoordinates] = useState("0, 0, 0");
  const [latticePanelOpen, setLatticePanelOpen] = useState(false);
  const [latticeMode, setLatticeMode] = useState<"scale" | "real">("scale");
  const [latticeDraft, setLatticeDraft] = useState("");
  const [latticeScaleDraft, setLatticeScaleDraft] = useState("1, 0, 0, 0, 1, 0, 0, 0, 1");
  const [scaleWithLattice, setScaleWithLattice] = useState(true);
  const [builderPanel, setBuilderPanel] = useState<"" | "surface" | "supercell" | "interface" | "molecule" | "export">("");
  const [surfaceParams, setSurfaceParams] = useState({ miller: "1 0 0", layers: "3", vacuum: "10", conventional: true });
  const [supercellParams, setSupercellParams] = useState({ repeats: "2 2 2" });
  const [interfaceParams, setInterfaceParams] = useState({ second: "", miller1: "1 0 0", miller2: "1 0 0", gap: "2.0", vacuum: "10", thickness1: "4", thickness2: "4", candidates: "3" });
  const [moleculeParams, setMoleculeParams] = useState({ smiles: "CCO" });
  const [exportParams, setExportParams] = useState({ format: "extxyz" as StructureFormat, name: "" });
  const [candidateChoice, setCandidateChoice] = useState("1");
  const [trackTasks, setTrackTasks] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const importRef = useRef<HTMLInputElement>(null);
  const viewportCapture = useRef<StructureViewportCapture | null>(null);
  const setViewportCapture = useCallback((capture: StructureViewportCapture | null) => { viewportCapture.current = capture; }, []);
  const reload = useCallback(async () => {
    try { const result = await host.readDocument(); setSnapshot({ structure: result.value as Structure, revision: result.revision }); setError(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }, [host]);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => { void Promise.all([host.listCards(["core.agent"]), host.listCards(["core.sandbox"]), host.listCards(["oaw.skill"])]).then(([nextAgents, nextSandboxes, nextSkills]) => { setAgents(nextAgents); setSandboxes(nextSandboxes); setSkills(nextSkills); }).catch(reason => setError(reason instanceof Error ? reason.message : String(reason))); }, [host]);
  const commit = useCallback(async (next: Structure, previous = snapshot) => {
    if (!previous) return;
    setSaving(true);
    try {
      const result = await host.documentAction("replace_structure", { structure: next }, previous.revision);
      setUndo(history => [...history, previous]); setRedo([]);
      setSnapshot({ structure: result.value as Structure, revision: result.revision }); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setSaving(false); }
  }, [host, snapshot]);
  const setSelection = useCallback(async (atomIds: number[]) => {
    if (!snapshot) return;
    const selected = [...new Set(atomIds)].filter(id => snapshot.structure.atoms.some(atom => atom.id === id));
    if (selected.length === snapshot.structure.selected_atom_ids.length && selected.every(id => snapshot.structure.selected_atom_ids.includes(id))) return;
    try { const result = await host.documentAction("select_atoms", { atom_ids: selected }, snapshot.revision); setSelectionVisible(true); setSnapshot({ structure: result.value as Structure, revision: result.revision }); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }, [host, snapshot]);
  const setLayers = useCallback(async (layerIds: string[]) => {
    if (!snapshot) return;
    try { const result = await host.documentAction("select_layers", { layer_ids: layerIds }, snapshot.revision); setSnapshot({ structure: result.value as Structure, revision: result.revision }); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }, [host, snapshot]);
  const restore = async (kind: "undo" | "redo") => {
    if (!snapshot) return;
    const history = kind === "undo" ? undo : redo; const prior = history.at(-1); if (!prior) return;
    setSaving(true);
    try {
      const result = await host.documentAction("replace_structure", { structure: prior.structure }, snapshot.revision);
      if (kind === "undo") { setUndo(items => items.slice(0, -1)); setRedo(items => [...items, snapshot]); }
      else { setRedo(items => items.slice(0, -1)); setUndo(items => [...items, snapshot]); }
      setSnapshot({ structure: result.value as Structure, revision: result.revision });
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setSaving(false); }
  };
  const insertAtom = (x: number, y: number, z: number) => {
    if (!snapshot) return;
    if (!/^[A-Z][a-z]?$/.test(newSymbol.trim())) { setError("Enter a valid element symbol."); return; }
    const next = clone(snapshot.structure); const id = Math.max(-1, ...next.atoms.map(atom => atom.id)) + 1;
    const layerId = next.active_layer_ids?.[0] ?? next.layers.find(layer => layer.kind === "atoms")?.id ?? "atoms";
    next.atoms.push({ id, symbol: newSymbol.trim(), x, y, z, layer_id: layerId }); next.selected_atom_ids = [id]; setAddPanelOpen(false); void commit(next);
  };
  const addAtom = () => {
    const [x, y, z] = newCoordinates.split(/[\s,]+/).filter(Boolean).map(Number); if (![x, y, z].every(Number.isFinite)) { setError("Coordinates must contain three finite numbers."); return; } insertAtom(x, y, z);
  };
  const deleteSelected = () => {
    if (!snapshot || !snapshot.structure.selected_atom_ids.length) return;
    const selected = new Set(snapshot.structure.selected_atom_ids); const next = clone(snapshot.structure);
    next.atoms = next.atoms.filter(atom => !selected.has(atom.id)); next.bonds = (next.bonds ?? []).filter(bond => !selected.has(bond.first_atom_id) && !selected.has(bond.second_atom_id)); next.selected_atom_ids = []; void commit(next);
  };
  const copy = () => setClipboard((snapshot?.structure.atoms ?? []).filter(atom => snapshot?.structure.selected_atom_ids.includes(atom.id)).map(({ symbol, x, y, z }) => ({ symbol, x, y, z })));
  const paste = () => {
    if (!snapshot || !clipboard.length) return;
    const next = clone(snapshot.structure); let id = Math.max(-1, ...next.atoms.map(atom => atom.id)) + 1;
    const layerId = next.active_layer_ids?.[0] ?? next.layers.find(layer => layer.kind === "atoms")?.id ?? "atoms";
    const created = clipboard.map(atom => ({ ...atom, id: id++, layer_id: layerId })); next.atoms.push(...created); next.selected_atom_ids = created.map(atom => atom.id); void commit(next);
  };
  const addLayer = () => {
    if (!snapshot) return;
    void commit(createLayer(snapshot.structure));
  };
  const toggleLayer = (id: string) => {
    if (!snapshot) return;
    const next = clone(snapshot.structure); next.layers = next.layers.map(layer => layer.id === id ? { ...layer, visible: !layer.visible } : layer); void commit(next);
  };
  const importFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; event.target.value = ""; if (!file) return;
    try { await commit(parseStructure(await file.text(), file.name)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : `Could not import ${file.name}`); }
  };
  const updateAtom = (atomId: number, field: "symbol" | "x" | "y" | "z", value: string) => {
    if (!snapshot) return;
    if (field !== "symbol" && !Number.isFinite(Number(value))) { setError("Coordinates must be finite numbers."); return; }
    const next = clone(snapshot.structure);
    next.atoms = next.atoms.map(atom => atom.id !== atomId ? atom : { ...atom, [field]: field === "symbol" ? value : Number(value) });
    void commit(next);
  };
  const replaceAtoms = (atoms: Atom[]) => {
    if (!snapshot) return;
    const next = clone(snapshot.structure); next.atoms = atoms; void commit(next);
  };
  const runStructureOperation = (operation: (value: Structure) => Structure | null, emptyMessage: string) => {
    if (!snapshot) return; const next = operation(snapshot.structure); if (!next) { setError(emptyMessage); return; } void commit(next);
  };
  const chooseTypes = () => { const raw = window.prompt("Elements to select (comma-separated)", "C,O"); if (raw !== null) void setSelection(selectByTypes(snapshot!.structure, raw)); };
  const chooseRadius = () => { const raw = window.prompt("Expand selected atoms by radius (Å)", "1.5"); if (raw === null) return; const radius = Number(raw); if (!Number.isFinite(radius) || radius <= 0) { setError("Radius must be a positive number."); return; } void setSelection(expandSelection(snapshot!.structure, radius)); };
  const chooseLattice = () => {
    if (!snapshot) return; setLatticeMode("scale"); setLatticeScaleDraft("1, 0, 0, 0, 1, 0, 0, 0, 1"); setLatticeDraft((snapshot.structure.cell ?? [[1, 0, 0], [0, 1, 0], [0, 0, 1]]).flat().join(", ")); setLatticePanelOpen(true);
  };
  const applyLatticePanel = () => {
    try {
      const cell = latticeMode === "scale" ? multiplyMatrices(snapshot?.structure.cell ?? [[1, 0, 0], [0, 1, 0], [0, 0, 1]], matrix("Scale matrix", latticeScaleDraft)) : matrix("Cell matrix", latticeDraft);
      runStructureOperation(value => applyLattice(value, cell, scaleWithLattice), "Could not apply lattice."); setLatticePanelOpen(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };
  const links = card.config as StructureLinks;
  const updateLink = (field: keyof StructureLinks, id: string) => { void host.updateConfig({ agent_id: links.agent_id ?? null, sandbox_id: links.sandbox_id ?? null, skill_id: links.skill_id ?? null, [field]: id || null }); };
  // The browser never issues commands. It sends one declarative, delimited
  // JSON request; the linked Agent's live graph grants decide what may run.
  const launchRequest = (operation: string, parameters: Record<string, unknown>, note = "") => {
    if (!links.agent_id) { setError("Link an AtomSculptor Agent before starting a modelling operation."); return; }
    const request = {
      operation,
      structure_card: card.id,
      parameters,
      sandbox_hint: links.sandbox_id ?? null,
      skill_hint: links.skill_id ?? null,
      track_on_task_board: trackTasks,
    };
    void host.runAgent(links.agent_id, [
      "Perform the AtomSculptor operation described by the JSON data below.",
      "ATOMSCULPTOR REQUEST",
      JSON.stringify(request),
      "END ATOMSCULPTOR REQUEST",
      note,
    ].filter(Boolean).join("\n")).catch(reason => setError(reason instanceof Error ? reason.message : String(reason)));
  };
  const runSurfacePanel = () => {
    try { launchRequest("surface", { miller_indices: triple("Miller indices", surfaceParams.miller).map(Math.round), layers: integer("Layers", surfaceParams.layers), vacuum: decimal("Vacuum", surfaceParams.vacuum), need_conventional: surfaceParams.conventional }, "Prefer the surface-builder Skill and report the output path."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };
  const runSupercellPanel = () => {
    try { launchRequest("supercell", { repetitions: triple("Repetitions", supercellParams.repeats).map(Math.round) }, "Prefer the supercell-builder Skill and report the output path."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };
  const runInterfacePanel = () => {
    try {
      if (!interfaceParams.second.trim()) throw new Error("Describe the second (substrate) structure.");
      launchRequest("interface", {
        second_structure: interfaceParams.second.trim(),
        miller_1: triple("Film Miller indices", interfaceParams.miller1).map(Math.round),
        miller_2: triple("Substrate Miller indices", interfaceParams.miller2).map(Math.round),
        gap: decimal("Gap", interfaceParams.gap),
        vacuum_between: decimal("Vacuum", interfaceParams.vacuum),
        thickness_1: decimal("Film thickness", interfaceParams.thickness1),
        thickness_2: decimal("Substrate thickness", interfaceParams.thickness2),
        max_interfaces: integer("Candidates", interfaceParams.candidates),
      }, "Prefer the interface-builder Skill. Write numbered candidate files, summarize each, and stop for the user's choice.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };
  const runMoleculePanel = () => {
    if (!moleculeParams.smiles.trim()) { setError("Enter a SMILES string."); return; }
    launchRequest("molecule", { smiles: moleculeParams.smiles.trim() }, "Prefer the molecular-structure-creation Skill. Add the molecule to the current structure when it makes chemical sense.");
  };
  const runExportPanel = () => {
    const name = (exportParams.name.trim() || structure.source_name || "structure").replace(/[/\\?%*:|"<>\s]+/g, "_");
    launchRequest("export", { format: exportParams.format, file_name: `${name}.${structureExtension(exportParams.format)}`, publish_artifact: true }, "Write the file into your authorized Sandbox and publish it as an Artifact when an artifact collection is connected.");
  };
  const saveAs = async (format: StructureFormat) => {
    if (!snapshot) return;
    const name = `${snapshot.structure.source_name || "structure"}.${structureExtension(format)}`;
    const content = exportStructure(snapshot.structure, format);
    try {
      const picker = (window as SavePickerWindow).showSaveFilePicker;
      if (!picker) { downloadStructure(name, content); return; }
      const handle = await picker({ suggestedName: name, types: [{ description: `${format.toUpperCase()} structure`, accept: { "text/plain": [`.${structureExtension(format)}`] } }] });
      const writable = await handle.createWritable(); await writable.write(content); await writable.close();
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };
  const adoptCandidate = () => {
    try { launchRequest("interface_select", { candidate: integer("Candidate", candidateChoice) }, "Inspect the structure's recorded interface_candidates, find this exact candidate ID and convert only its stored file_name back into this structure."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };
  const selectableIds = snapshot?.structure.atoms.filter(atom => snapshot.structure.layers.find(layer => layer.id === atom.layer_id)?.visible !== false).map(atom => atom.id) ?? [];
  const openedFile = useFileViewer(card.id);
  const importOpenedFile = async () => {
    if (!openedFile.file) return;
    try {
      const result = await host.readFile(openedFile.file.reference);
      await commit(parseStructure(decodeFileData(result.data), openedFile.file.name));
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };
  useEffect(() => host.onDocumentChange(change => {
    if (change.nodeId !== card.id || !change.actorId) return;
    void reload();
    host.openWorkspace(card.id);
  }), [card.id, host, reload]);
  useEffect(() => {
    // Compact card bodies may be mounted elsewhere on the canvas.  Only the
    // explicit workspace surface can offer its rendered structure to a model.
    if (level !== "workspace") return;
    return host.registerVisualCapture("atomsculptor.structure-viewport", async request => {
      if (!snapshot || request.documentRevision !== snapshot.revision) {
        throw new Error("The structure changed before its viewport could be captured.");
      }
      const capture = viewportCapture.current;
      if (!capture) throw new Error("Open a rendered 3D structure before requesting visual observation.");
      const requested = request.captureOptions?.view;
      const observationView: ObservationCameraView = requested === "iso" || requested === "x" || requested === "y" || requested === "z" || requested === "current" ? requested : "current";
      const image = await capture(request.maxImageDimension, observationView);
      const visibleLayerIds = new Set(snapshot.structure.layers.filter(layer => layer.visible).map(layer => layer.id));
      const selectedAtomIds = new Set(snapshot.structure.selected_atom_ids);
      const renderedElements = [...new Set(snapshot.structure.atoms
        .filter(atom => visibleLayerIds.has(atom.layer_id) && (selectionVisible || !selectedAtomIds.has(atom.id)))
        .map(atom => atom.symbol))];
      const element_colors = Object.fromEntries(renderedElements.map(symbol => [symbol, ELEMENT_COLORS[symbol] ?? ELEMENT_COLORS.default]));
      return {
        dataBase64: image.dataBase64,
        metadata: {
          document_revision: snapshot.revision,
          selected_atom_ids: snapshot.structure.selected_atom_ids.slice(0, 500),
          selected_count: snapshot.structure.selected_atom_ids.length,
          editor_mode: mode,
          camera_view: observationView === "current" ? cameraView : observationView,
          requested_camera_view: observationView,
          projection,
          rendered_width: image.width,
          rendered_height: image.height,
          element_colors,
        },
      };
    });
  }, [cameraView, host, level, mode, projection, selectionVisible, snapshot]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) return;
      const key = event.key.toLowerCase(); const command = event.ctrlKey || event.metaKey;
      if (command && key === "a") { event.preventDefault(); void setSelection(selectableIds); return; }
      if (command && key === "c") { event.preventDefault(); copy(); return; }
      if (command && key === "x") { event.preventDefault(); copy(); deleteSelected(); return; }
      if (command && key === "v") { event.preventDefault(); paste(); return; }
      if (command && key === "z") { event.preventDefault(); void restore(event.shiftKey ? "redo" : "undo"); return; }
      if (command && key === "y") { event.preventDefault(); void restore("redo"); return; }
      if (event.key === "1") { setMode("select"); return; }
      if (event.key === "2") { setMode("box"); return; }
      if (key === "t") { setMode("translate"); return; }
      if (key === "r") { setMode("rotate"); return; }
      if (key === "e") { setMode("scale"); return; }
      if (key === "x") { setCameraView("x"); setCameraNonce(value => value + 1); return; }
      if (key === "y") { setCameraView("y"); setCameraNonce(value => value + 1); return; }
      if (key === "z") { setCameraView("z"); setCameraNonce(value => value + 1); return; }
      if (event.key === "Escape") { void setSelection([]); return; }
      if (event.key === "Delete" || event.key === "Backspace") { deleteSelected(); }
    };
    window.addEventListener("keydown", onKeyDown); return () => window.removeEventListener("keydown", onKeyDown);
  }, [copy, deleteSelected, paste, restore, selectableIds, setSelection]);
  const measureDistance = useMemo(() => {
    if (!snapshot || measurement.length !== 2) return null;
    const [first, second] = measurement.map(id => snapshot.structure.atoms.find(atom => atom.id === id));
    return first && second ? minimumImageDistance(first, second, snapshot.structure) : null;
  }, [measurement, snapshot]);
  if (error) return <section className="atomsculptor-workspace"><p role="alert">{error}</p><button onClick={() => void reload()}>Reload structure</button></section>;
  if (!snapshot) return <p role="status">Loading structure…</p>;
  const { structure } = snapshot; const activeLayerIds = structure.active_layer_ids ?? []; const active = new Set(activeLayerIds); const selected = new Set(structure.selected_atom_ids); const interfaceCandidates: InterfaceCandidate[] = structure.interface_candidates ?? [];
  const visibleAtoms = structure.atoms.filter(atom => structure.layers.find(layer => layer.id === atom.layer_id)?.visible !== false);
  const hoveredAtom = hoveredAtomId === null ? null : structure.atoms.find(atom => atom.id === hoveredAtomId) ?? null;
  return <section className="atomsculptor-workspace atomsculptor-legacy-shell" aria-label="AtomSculptor structure editor">
    <header className="atomsculptor-header"><div className="atomsculptor-brand"><span aria-hidden="true">◈</span><div><strong>AtomSculptor</strong><small>{structure.source_name || card.name} · OAW revision {snapshot.revision}</small></div></div><span className="atomsculptor-runtime-state"><i />OAW document synced</span></header>
    <div className="atomsculptor-editor-toolbar" role="toolbar" aria-label="Structure tools">
      <button className={`atomsculptor-tool-button ${mode === "select" ? "active" : ""}`} title="Select atoms" aria-label="Select atoms" onClick={() => { setMode("select"); setMeasurement([]); }}><ToolIcon name="select" /></button>
      <button className={`atomsculptor-tool-button ${mode === "box" ? "active" : ""}`} title="Box select" aria-label="Box select" onClick={() => setMode("box")}><ToolIcon name="box" /></button>
      <button className={`atomsculptor-tool-button ${mode === "measure" ? "active" : ""}`} title="Measure distance" aria-label="Measure distance" onClick={() => { setMode("measure"); setMeasurement([]); }}><ToolIcon name="measure" /></button>
      <button className={`atomsculptor-tool-button ${mode === "translate" ? "active" : ""}`} title="Move selected atoms" aria-label="Move selected atoms" disabled={!selected.size} onClick={() => setMode("translate")}><ToolIcon name="move" /></button>
      <button className={`atomsculptor-tool-button ${mode === "rotate" ? "active" : ""}`} title="Rotate selected atoms" aria-label="Rotate selected atoms" disabled={!selected.size} onClick={() => setMode("rotate")}><ToolIcon name="rotate" /></button>
      <button className={`atomsculptor-tool-button ${mode === "scale" ? "active" : ""}`} title="Scale selected atoms" aria-label="Scale selected atoms" disabled={!selected.size} onClick={() => setMode("scale")}><ToolIcon name="scale" /></button><i />
      <button className={`atomsculptor-tool-button ${addPanelOpen ? "active" : ""}`} title="Add atom" aria-label="Add atom" disabled={saving} onClick={() => setAddPanelOpen(value => !value)}><ToolIcon name="add" /></button><button className="atomsculptor-tool-button" title="Delete selected atoms" aria-label="Delete selected atoms" disabled={!selected.size || saving} onClick={deleteSelected}><ToolIcon name="delete" /></button>
      <button className="atomsculptor-tool-button" title="Copy selected atoms" aria-label="Copy selected atoms" disabled={!selected.size} onClick={copy}><ToolIcon name="copy" /></button><button className="atomsculptor-tool-button" title="Cut selected atoms" aria-label="Cut selected atoms" disabled={!selected.size || saving} onClick={() => { copy(); deleteSelected(); }}><ToolIcon name="cut" /></button><button className="atomsculptor-tool-button" title="Paste atoms" aria-label="Paste atoms" disabled={!clipboard.length || saving} onClick={paste}><ToolIcon name="paste" /></button>
      <button className="atomsculptor-tool-button" title="Undo" aria-label="Undo" disabled={!undo.length || saving} onClick={() => void restore("undo")}><ToolIcon name="undo" /></button><button className="atomsculptor-tool-button" title="Redo" aria-label="Redo" disabled={!redo.length || saving} onClick={() => void restore("redo")}><ToolIcon name="redo" /></button><i />
      <button className="atomsculptor-tool-button" title="Import structure" aria-label="Import structure" disabled={saving} onClick={() => importRef.current?.click()}><ToolIcon name="import" /></button><select aria-label="Export structure" defaultValue="" onChange={event => { const [destination, format] = event.target.value.split(":") as ["download" | "save", StructureFormat]; if (format) { if (destination === "save") void saveAs(format); else downloadStructure(`${structure.source_name || "structure"}.${structureExtension(format)}`, exportStructure(structure, format)); event.target.value = ""; } }}><option value="" disabled>Export…</option><optgroup label="Download"><option value="download:xyz">XYZ</option><option value="download:extxyz">ExtXYZ</option><option value="download:lxyz">LXYZ</option><option value="download:cif">CIF</option><option value="download:poscar">POSCAR</option><option value="download:pdb">PDB</option><option value="download:sdf">SDF</option><option value="download:mol2">MOL2</option><option value="download:json">JSON</option></optgroup><optgroup label="Choose location"><option value="save:xyz">Save XYZ as…</option><option value="save:extxyz">Save ExtXYZ as…</option><option value="save:cif">Save CIF as…</option><option value="save:poscar">Save POSCAR as…</option><option value="save:json">Save JSON as…</option></optgroup></select>
      <i /><button className="atomsculptor-tool-button" title="Reset view" aria-label="Reset view" onClick={() => { setCameraView("iso"); setCameraNonce(value => value + 1); }}><ToolIcon name="reset" /></button><button className="atomsculptor-axis-button" title="View along X" onClick={() => { setCameraView("x"); setCameraNonce(value => value + 1); }}>X</button><button className="atomsculptor-axis-button" title="View along Y" onClick={() => { setCameraView("y"); setCameraNonce(value => value + 1); }}>Y</button><button className="atomsculptor-axis-button" title="View along Z" onClick={() => { setCameraView("z"); setCameraNonce(value => value + 1); }}>Z</button><button className="atomsculptor-projection-button" title="Toggle camera projection" onClick={() => setProjection(value => value === "perspective" ? "orthographic" : "perspective")}>{projection === "perspective" ? "P" : "O"}</button>
      <input ref={importRef} className="atomsculptor-file-input" type="file" accept=".json,.xyz,.extxyz,.lxyz,.cif,.mcif,.vasp,.pdb,.sdf,.mol,.mol2,text/plain,application/json" onChange={event => void importFile(event)} />
      <button className="atomsculptor-tool-button" title={selectionVisible ? "Hide selected atoms" : "Show selected atoms"} aria-label={selectionVisible ? "Hide selected atoms" : "Show selected atoms"} disabled={!selected.size} onClick={() => setSelectionVisible(value => !value)}>{selectionVisible ? "◉" : "○"}</button>
    </div>
    <div className="atomsculptor-editor-grid">
      <aside className="atomsculptor-inspector"><section><h3>OAW links</h3><label>Agent<select value={links.agent_id ?? ""} onChange={event => updateLink("agent_id", event.target.value)}><option value="">None</option>{agents.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Sandbox<select value={links.sandbox_id ?? ""} onChange={event => updateLink("sandbox_id", event.target.value)}><option value="">None</option>{sandboxes.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Skill<select value={links.skill_id ?? ""} onChange={event => updateLink("skill_id", event.target.value)}><option value="">None</option>{skills.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><div className="atomsculptor-button-row">{links.agent_id && <button onClick={() => host.openWorkspace(links.agent_id!)}>Open Agent</button>}{links.sandbox_id && <button onClick={() => host.openWorkspace(links.sandbox_id!)}>Open Sandbox</button>}{links.skill_id && <button onClick={() => host.openWorkspace(links.skill_id!)}>Open Skill</button>}</div></section>
      <section><h3>Opened file</h3><p className="atomsculptor-hint">{openedFile.file ? openedFile.file.name : openedFile.sources.length ? "Open a structure file in a connected Sandbox window." : "Connect a Sandbox with Follow opened files, then open a file in its native tree."}</p><div className="atomsculptor-button-row"><button disabled={!openedFile.file || saving} onClick={() => void importOpenedFile()}>Import opened file</button>{openedFile.file && <button onClick={() => host.openWorkspace(openedFile.file!.reference.source_id)}>Open source</button>}</div></section>
      <section><h3>Model</h3><div className="atomsculptor-button-row">{(["surface", "supercell", "interface", "molecule", "export"] as const).map(kind => <button key={kind} className={builderPanel === kind ? "active" : ""} onClick={() => setBuilderPanel(builderPanel === kind ? "" : kind)}>{kind === "surface" ? "Surface" : kind === "supercell" ? "Supercell" : kind === "interface" ? "Interface" : kind === "molecule" ? "SMILES" : "Publish"}</button>)}</div><label className="atomsculptor-check"><input type="checkbox" checked={trackTasks} onChange={event => setTrackTasks(event.target.checked)} /> Track on Task Board</label>
      {builderPanel === "surface" && <div className="atomsculptor-builder-panel"><label>Miller (h k l)<input value={surfaceParams.miller} onChange={event => setSurfaceParams({ ...surfaceParams, miller: event.target.value })} /></label><label>Layers<input value={surfaceParams.layers} onChange={event => setSurfaceParams({ ...surfaceParams, layers: event.target.value })} /></label><label>Vacuum (Å)<input value={surfaceParams.vacuum} onChange={event => setSurfaceParams({ ...surfaceParams, vacuum: event.target.value })} /></label><label className="atomsculptor-check"><input type="checkbox" checked={surfaceParams.conventional} onChange={event => setSurfaceParams({ ...surfaceParams, conventional: event.target.checked })} /> Conventional cell first</label><div className="atomsculptor-button-row"><button onClick={runSurfacePanel}>Build surface</button></div></div>}
      {builderPanel === "supercell" && <div className="atomsculptor-builder-panel"><label>Repetitions (n1 n2 n3)<input value={supercellParams.repeats} onChange={event => setSupercellParams({ repeats: event.target.value })} /></label><div className="atomsculptor-button-row"><button onClick={runSupercellPanel}>Build supercell</button></div></div>}
      {builderPanel === "interface" && <div className="atomsculptor-builder-panel"><label>Substrate / second structure<input value={interfaceParams.second} onChange={event => setInterfaceParams({ ...interfaceParams, second: event.target.value })} placeholder="path in Sandbox, description, or card ID" /></label><label>Film Miller<input value={interfaceParams.miller1} onChange={event => setInterfaceParams({ ...interfaceParams, miller1: event.target.value })} /></label><label>Substrate Miller<input value={interfaceParams.miller2} onChange={event => setInterfaceParams({ ...interfaceParams, miller2: event.target.value })} /></label><label>Gap (Å)<input value={interfaceParams.gap} onChange={event => setInterfaceParams({ ...interfaceParams, gap: event.target.value })} /></label><label>Vacuum (Å)<input value={interfaceParams.vacuum} onChange={event => setInterfaceParams({ ...interfaceParams, vacuum: event.target.value })} /></label><label>Film layers<input value={interfaceParams.thickness1} onChange={event => setInterfaceParams({ ...interfaceParams, thickness1: event.target.value })} /></label><label>Substrate layers<input value={interfaceParams.thickness2} onChange={event => setInterfaceParams({ ...interfaceParams, thickness2: event.target.value })} /></label><label>Candidates<input value={interfaceParams.candidates} onChange={event => setInterfaceParams({ ...interfaceParams, candidates: event.target.value })} /></label><div className="atomsculptor-button-row"><button onClick={runInterfacePanel}>Build candidates</button></div>{interfaceCandidates.length > 0 && <div className="atomsculptor-interface-candidates" role="list" aria-label="Interface candidates">{interfaceCandidates.map(candidate => <button key={candidate.id} type="button" role="listitem" className={candidateChoice === String(candidate.id) ? "selected" : ""} onClick={() => setCandidateChoice(String(candidate.id))}><strong>#{candidate.id} · {candidate.formula || "Interface"}</strong><span>ε {candidate.von_mises_strain == null ? "—" : `${(candidate.von_mises_strain * 100).toFixed(2)}%`} · A {candidate.area == null ? "—" : `${candidate.area.toFixed(1)} Å²`}</span><span>{candidate.atom_count} atoms · T{candidate.termination_index ?? "—"}</span></button>)}</div>}<label>Use candidate #<input value={candidateChoice} onChange={event => setCandidateChoice(event.target.value)} /></label><button disabled={!interfaceCandidates.some(candidate => candidate.id === Number(candidateChoice))} onClick={adoptCandidate}>Write candidate back</button></div>}
      {builderPanel === "molecule" && <div className="atomsculptor-builder-panel"><label>SMILES<input value={moleculeParams.smiles} onChange={event => setMoleculeParams({ smiles: event.target.value })} /></label><div className="atomsculptor-button-row"><button onClick={runMoleculePanel}>Add molecule</button></div></div>}
      {builderPanel === "export" && <div className="atomsculptor-builder-panel"><label>Format<select value={exportParams.format} onChange={event => setExportParams({ ...exportParams, format: event.target.value as StructureFormat })}><option value="extxyz">ExtXYZ</option><option value="xyz">XYZ</option><option value="cif">CIF</option><option value="poscar">POSCAR</option><option value="json">JSON</option></select></label><label>File name (without extension)<input value={exportParams.name} onChange={event => setExportParams({ ...exportParams, name: event.target.value })} placeholder={structure.source_name || "structure"} /></label><div className="atomsculptor-button-row"><button onClick={runExportPanel}>Write to Sandbox + publish</button></div></div>}
      <p className="atomsculptor-hint">Runs through the linked Agent; its graph connections authorize the Sandbox and Skill. Dependencies, output files, progress and failures appear in OAW's native Sandbox UI.</p></section><section><h3>Layers</h3><button onClick={addLayer} disabled={saving}>+ Add layer</button><div className="atomsculptor-button-row"><button onClick={() => runStructureOperation(deleteActiveLayers, "Select layers to delete; one atom layer must remain.")}>Delete</button><button onClick={() => runStructureOperation(mergeActiveLayers, "Select at least two atom layers to merge.")}>Merge</button><button disabled={!selected.size} onClick={() => runStructureOperation(extractSelection, "Select atoms to extract.")}>Extract</button></div>{structure.layers.map(layer => <div className="atomsculptor-layer-row" key={layer.id}><button className={active.has(layer.id) ? "selected" : ""} onClick={() => void setLayers(active.has(layer.id) ? [...active].filter(id => id !== layer.id) : [...active, layer.id])}>{layer.name}</button><button aria-label={`Toggle ${layer.name}`} onClick={() => toggleLayer(layer.id)}>{layer.visible ? "◉" : "○"}</button></div>)}</section><section><h3>Selection</h3><p>{selected.size} atom{selected.size === 1 ? "" : "s"}</p><div className="atomsculptor-button-row"><button onClick={chooseTypes}>By element</button><button disabled={!selected.size} onClick={chooseRadius}>Expand</button><button onClick={() => void setSelection(invertSelection(structure))}>Invert</button></div><p className="atomsculptor-hint">Click selects; Shift-drag draws a selection box.</p>{hoveredAtom && <p className="atomsculptor-hover">#{hoveredAtom.id} · {hoveredAtom.symbol}<br />{hoveredAtom.x.toFixed(4)}, {hoveredAtom.y.toFixed(4)}, {hoveredAtom.z.toFixed(4)} Å</p>}</section><section><h3>Lattice</h3><div className="atomsculptor-button-row"><button onClick={chooseLattice}>Edit cell</button><button onClick={() => runStructureOperation(wrapToCell, "A non-singular cell is required to wrap atoms.")}>Wrap</button>{activeLayerIds.length === 1 && <button onClick={() => runStructureOperation(value => useLayerLattice(value, activeLayerIds[0]), "This layer has no lattice metadata.")}>Use layer cell</button>}</div>{latticePanelOpen && <div className="atomsculptor-lattice-panel"><div className="atomsculptor-button-row"><button className={latticeMode === "scale" ? "active" : ""} onClick={() => setLatticeMode("scale")}>Scale matrix</button><button className={latticeMode === "real" ? "active" : ""} onClick={() => setLatticeMode("real")}>Cell matrix</button></div><label>{latticeMode === "scale" ? "Scale matrix" : "Cell matrix"}<input value={latticeMode === "scale" ? latticeScaleDraft : latticeDraft} onChange={event => latticeMode === "scale" ? setLatticeScaleDraft(event.target.value) : setLatticeDraft(event.target.value)} /></label><label className="atomsculptor-check"><input type="checkbox" checked={scaleWithLattice} onChange={event => setScaleWithLattice(event.target.checked)} /> Scale atoms</label><div className="atomsculptor-button-row"><button onClick={applyLatticePanel}>Apply</button><button onClick={() => setLatticePanelOpen(false)}>Cancel</button></div></div>}</section><section className={mode === "measure" ? "" : "muted"}><h3>Distance</h3><p>{measureDistance === null ? "Choose two atoms" : `${measureDistance.toFixed(4)} Å`}</p></section></aside>
      <main className="atomsculptor-canvas-panel">{visibleAtoms.length ? <StructureViewport structure={structure} mode={mode} cameraView={cameraView} projection={projection} cameraNonce={cameraNonce} selectionVisible={selectionVisible} onSelect={ids => void setSelection(ids)} onMeasure={setMeasurement} onHover={setHoveredAtomId} onAdd={position => { insertAtom(position.x, position.y, position.z); setMode("select"); }} onTransform={replaceAtoms} onCaptureReady={setViewportCapture} /> : <p className="atomsculptor-empty">Add an atom, import a structure, or ask an Agent to create one.</p>}<ViewportReadout structure={structure} selected={selected} hoveredAtom={hoveredAtom} mode={mode} distance={measureDistance} />{addPanelOpen && <section className="atomsculptor-add-panel"><h3>Add atom</h3><div className="atomsculptor-periodic">{ELEMENTS.map(symbol => <button className={newSymbol === symbol ? "selected" : ""} key={symbol} title={symbol} onClick={() => setNewSymbol(symbol)}>{symbol}</button>)}</div><label>Coordinates (Å)<input value={newCoordinates} onChange={event => setNewCoordinates(event.target.value)} placeholder="x, y, z" /></label><div className="atomsculptor-button-row"><button onClick={addAtom}>Add</button><button onClick={() => { setAddPanelOpen(false); setMode("add"); }}>Place in canvas</button><button onClick={() => setAddPanelOpen(false)}>Cancel</button></div></section>}</main>
      <aside className="atomsculptor-atoms-panel"><h3>Atoms</h3><div className="atomsculptor-atom-list">{visibleAtoms.map(atom => <div className={selected.has(atom.id) ? "atom-row selected" : "atom-row"} key={atom.id}><button title="Click to select; Shift/⌘-click to add or remove" onClick={event => { const additive = event.shiftKey || event.metaKey || event.ctrlKey; void setSelection(additive ? (selected.has(atom.id) ? [...selected].filter(id => id !== atom.id) : [...selected, atom.id]) : [atom.id]); }}>#{atom.id}</button><AtomField label={`Element ${atom.id}`} value={atom.symbol} onCommit={value => updateAtom(atom.id, "symbol", value)} /><AtomField label={`X ${atom.id}`} value={atom.x} numeric onCommit={value => updateAtom(atom.id, "x", value)} /><AtomField label={`Y ${atom.id}`} value={atom.y} numeric onCommit={value => updateAtom(atom.id, "y", value)} /><AtomField label={`Z ${atom.id}`} value={atom.z} numeric onCommit={value => updateAtom(atom.id, "z", value)} /></div>)}</div></aside>
    </div>
  </section>;
}

export default { apiVersion: 1, views: { preview: Preview, workspace: Workspace } } satisfies FrontendPlugin;
