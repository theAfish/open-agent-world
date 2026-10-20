import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { TransformControls } from "three/examples/jsm/controls/TransformControls.js";
import type { Atom, Structure } from "../formats";
import { minimumImageDisplacement } from "../periodic";

export type EditorMode = "select" | "box" | "measure" | "add" | "translate" | "rotate" | "scale";
export type CameraView = "iso" | "x" | "y" | "z";
export type CameraProjection = "perspective" | "orthographic";
export type ObservationCameraView = "current" | CameraView;
export type StructureViewportCapture = (maxImageDimension: number, view?: ObservationCameraView) => Promise<{ dataBase64: string; width: number; height: number }>;
type Props = { structure: Structure; mode: EditorMode; cameraView: CameraView; projection: CameraProjection; cameraNonce: number; selectionVisible: boolean; onSelect: (ids: number[]) => void; onMeasure: (ids: number[]) => void; onHover: (id: number | null) => void; onAdd: (position: { x: number; y: number; z: number }) => void; onTransform: (atoms: Atom[]) => void; onCaptureReady?: (capture: StructureViewportCapture | null) => void };
const TRANSFORM_MODES = new Set<EditorMode>(["translate", "rotate", "scale"]);
const directionForView = (view: CameraView) => view === "x" ? new THREE.Vector3(1, 0, 0) : view === "y" ? new THREE.Vector3(0, 1, 0) : view === "z" ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(1, 1, .7).normalize();
// Jmol/CPK palette and covalent radii retained from the original viewer.
/** Jmol/CPK colours shared by the renderer and visual-observation metadata. */
export const ELEMENT_COLORS: Record<string, string> = {
  H: "#ffffff", He: "#d9ffff", Li: "#cc80ff", Be: "#bfe1a3", B: "#ffb5b5",
  C: "#404040", N: "#3050f8", O: "#ff0d0d", F: "#90e050", Ne: "#b3e3f5",
  Na: "#ab5cf2", Mg: "#8aff00", Al: "#bfa6a6", Si: "#f0c8a0", P: "#ff8000",
  S: "#ffff30", Cl: "#1ff01f", Ar: "#80d1e3", K: "#8f40d4", Ca: "#3dff00",
  Sc: "#6699ff", Ti: "#bfc2c7", V: "#a6a6ff", Cr: "#8a99c7", Mn: "#9c7ac7",
  Fe: "#e06633", Co: "#f090a0", Ni: "#50d050", Cu: "#c88033", Zn: "#7d80b0",
  Ga: "#c28f8f", Ge: "#668f8f", As: "#9e4fb5", Se: "#ffa100", Br: "#a62929",
  Kr: "#5cb8d1", Rb: "#702eb0", Sr: "#00e676", Y: "#94ffff", Zr: "#94e0e0",
  Nb: "#73c2c9", Mo: "#54b5b5", Ru: "#2f6f6f", Rh: "#c3c3c3", Pd: "#006985",
  Ag: "#c0c0c0", Cd: "#ffd700", In: "#a67573", Sn: "#668080", Sb: "#9e63b5",
  Te: "#d47a00", I: "#940094", Xe: "#429eb0", Cs: "#57178f", Ba: "#00c900",
  La: "#70d4ff", Ce: "#ffffc7", Nd: "#c2ffbd", Sm: "#ffd2a6", Eu: "#ffc0cb",
  Gd: "#aaffc3", Tb: "#d3cfff", Dy: "#ffdfba", Ho: "#ffd4b6", Er: "#b0e0e6",
  Tm: "#c6d7ff", Yb: "#ffd1dc", Lu: "#d0d0ff", Hf: "#4dc2ff", Ta: "#4da6ff",
  W: "#3399ff", Re: "#267f99", Os: "#266f7a", Ir: "#175487", Pt: "#d0d0e0",
  Au: "#ffd123", Hg: "#b8b8d0", Tl: "#a6544d", Pb: "#575961", Bi: "#9c5cb3",
  default: "#ff1493",
};
const RADII: Record<string, number> = {
  H: .31, C: .77, N: .75, O: .73, F: .71, P: 1.06, S: 1.02, Cl: .99, Br: 1.14, I: 1.33,
  Li: 1.28, Na: 1.66, K: 2.03, Ca: 1.74, Mg: 1.41, Al: 1.21, Si: 1.17, Fe: 1.25, Cu: 1.28,
  Zn: 1.22, Ag: 1.44, Au: 1.44, Pt: 1.39, Pd: 1.31, Ti: 1.47, Co: 1.25, Ni: 1.24, Mn: 1.29,
  Cr: 1.29, He: .28, Ne: .58, Ar: 1.06, Xe: 1.31, Kr: 1.16, default: 1.2,
};
const VDW_RADII: Record<string, number> = {
  H: 1.20, He: 1.40, Li: 1.82, Be: 1.53, B: 1.92, C: 1.70, N: 1.55, O: 1.52, F: 1.47, Ne: 1.54,
  Na: 2.27, Mg: 1.73, Al: 1.84, Si: 2.10, P: 1.80, S: 1.80, Cl: 1.75, Ar: 1.88, K: 2.75, Ca: 2.31,
  Sc: 2.11, Ti: 2.00, V: 2.00, Cr: 2.00, Mn: 2.00, Fe: 2.00, Co: 2.00, Ni: 1.63, Cu: 1.40, Zn: 1.39,
  Ga: 1.87, Ge: 2.11, As: 1.85, Se: 1.90, Br: 1.85, Kr: 2.02, Rb: 3.03, Sr: 2.49, Y: 2.00, Zr: 2.16,
  Nb: 2.07, Mo: 2.10, Ru: 2.05, Rh: 2.00, Pd: 2.05, Ag: 1.72, Cd: 1.58, In: 1.93, Sn: 2.17, Sb: 2.06,
  Te: 2.06, I: 1.98, Xe: 2.16, Cs: 3.43, Ba: 2.68, La: 2.07, Ce: 2.04, Nd: 2.01, Sm: 2.06, Eu: 2.00,
  Gd: 1.95, Tb: 1.90, Dy: 1.88, Ho: 1.87, Er: 1.88, Tm: 1.90, Yb: 1.94, Lu: 1.87, Hf: 2.16, Ta: 2.15,
  W: 2.10, Re: 2.05, Os: 2.00, Ir: 2.00, Pt: 2.05, Au: 1.66, Hg: 1.55, Tl: 1.96, Pb: 2.02, Bi: 2.07,
  default: 1.80,
};
const radius = (symbol: string) => (RADII[symbol] ?? RADII.default) * .55;
const vdwRadius = (symbol: string) => VDW_RADII[symbol] ?? VDW_RADII.default;
const BOND_AUTO_DISABLE_ATOMS = 900;
const MAX_BOND_MESHES = 30000;

function centroid(atoms: Atom[]) {
  if (!atoms.length) return new THREE.Vector3();
  return atoms.reduce((sum, atom) => sum.add(new THREE.Vector3(atom.x, atom.y, atom.z)), new THREE.Vector3()).multiplyScalar(1 / atoms.length);
}

/** A self-contained Three.js editor. Persistent state always returns through OAW document actions. */
export function StructureViewport({ structure, mode, cameraView, projection, cameraNonce, selectionVisible, onSelect, onMeasure, onHover, onAdd, onTransform, onCaptureReady }: Props) {
  const mount = useRef<HTMLDivElement>(null);
  const latest = useRef({ structure, mode, cameraView, projection, selectionVisible, onSelect, onMeasure, onHover, onAdd, onTransform });
  const cameraPose = useRef<{ view: CameraView; projection: CameraProjection; nonce: number; position: number[]; quaternion: number[]; target: number[]; zoom: number } | null>(null);
  latest.current = { structure, mode, cameraView, projection, selectionVisible, onSelect, onMeasure, onHover, onAdd, onTransform };

  useEffect(() => {
    const host = mount.current; if (!host) return;
    const scene = new THREE.Scene(); scene.background = new THREE.Color(0x181818);
    const camera = projection === "perspective" ? new THREE.PerspectiveCamera(45, 1, .01, 1000) : new THREE.OrthographicCamera(-1, 1, 1, -1, .01, 1000); camera.up.set(0, 0, 1);
    // A retained drawing buffer is required for the explicitly authorized
    // transient PNG capture path; the viewport still bounds resolution.
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true }); renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5)); host.appendChild(renderer.domElement);
    const orbit = new OrbitControls(camera, renderer.domElement); orbit.enableDamping = true; orbit.dampingFactor = .1; orbit.screenSpacePanning = true;
    scene.add(new THREE.AmbientLight(0xffffff, .55)); const key = new THREE.DirectionalLight(0xffffff, .85); key.position.set(5, 10, 8); scene.add(key); const fill = new THREE.DirectionalLight(0xffffff, .25); fill.position.set(-5, -3, -6); scene.add(fill);
    const atomsGroup = new THREE.Group(); const bondsGroup = new THREE.Group(); const cellGroup = new THREE.Group(); scene.add(bondsGroup, atomsGroup, cellGroup);
    const pivot = new THREE.Object3D(); scene.add(pivot); const gizmo = new TransformControls(camera, renderer.domElement); const gizmoHelper = gizmo.getHelper(); scene.add(gizmoHelper);
    const raycaster = new THREE.Raycaster(); const pointer = new THREE.Vector2(); const overlay = document.createElement("div"); overlay.className = "atomsculptor-box-overlay"; host.appendChild(overlay);
    let meshes: THREE.Mesh[] = []; let displayedAtoms: Atom[] = []; let midpoint = new THREE.Vector3(); let press: { x: number; y: number; additive: boolean; boxSelect: boolean } | null = null; let boxStart: { x: number; y: number; additive: boolean } | null = null; let measured: number[] = []; let transformStart: Map<number, Atom> | null = null; let pivotStart = new THREE.Vector3(); let transformDirty = false; let gizmoDragging = false; let viewRadius = 8; let hovered: number | null = null;

    const disposeGroup = (group: THREE.Group) => { while (group.children.length) { const object = group.children.pop(); object?.traverse(node => { if (node instanceof THREE.Mesh || node instanceof THREE.LineSegments) { node.geometry.dispose(); const materials = Array.isArray(node.material) ? node.material : [node.material]; materials.forEach(material => material.dispose()); } }); } };
    const renderedPosition = (atom: Atom) => new THREE.Vector3(atom.x, atom.y, atom.z).sub(midpoint);
    const configureGizmo = () => {
      const selected = latest.current.structure.atoms.filter(atom => latest.current.structure.selected_atom_ids.includes(atom.id) && displayedAtoms.some(visible => visible.id === atom.id));
      if (!TRANSFORM_MODES.has(latest.current.mode) || !selected.length) { gizmo.detach(); gizmoHelper.visible = false; return; }
      pivot.position.copy(centroid(selected).sub(midpoint)); pivot.quaternion.identity(); pivot.scale.set(1, 1, 1); gizmo.setMode(latest.current.mode as "translate" | "rotate" | "scale"); gizmo.attach(pivot); gizmoHelper.visible = true;
    };
    const draw = () => {
      disposeGroup(atomsGroup); disposeGroup(bondsGroup); disposeGroup(cellGroup);
      const current = latest.current.structure; const visibleLayers = new Set(current.layers.filter(layer => layer.visible).map(layer => layer.id)); const selected = new Set(current.selected_atom_ids); displayedAtoms = current.atoms.filter(atom => visibleLayers.has(atom.layer_id) && (latest.current.selectionVisible || !selected.has(atom.id))); midpoint = centroid(displayedAtoms);
      meshes = displayedAtoms.map(atom => { const material = new THREE.MeshPhongMaterial({ color: ELEMENT_COLORS[atom.symbol] ?? ELEMENT_COLORS.default, emissive: selected.has(atom.id) ? 0x2080ff : 0, emissiveIntensity: selected.has(atom.id) ? .75 : 0, shininess: 70 }); const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius(atom.symbol), 28, 20), material); mesh.position.copy(renderedPosition(atom)); mesh.userData.atomId = atom.id; atomsGroup.add(mesh); return mesh; });
      const addBond = (first: Atom, second: Atom, explicit = false, periodicDisplacement?: THREE.Vector3) => {
        if (bondsGroup.children.length + 2 > MAX_BOND_MESHES) return;
        const start = renderedPosition(first); const delta = minimumImageDisplacement(first, second, current);
        const end = start.clone().add(periodicDisplacement ?? new THREE.Vector3(delta.x, delta.y, delta.z)); const distance = start.distanceTo(end);
        if (distance <= 0 || (!explicit && distance >= (vdwRadius(first.symbol) + vdwRadius(second.symbol)) * .6)) return;
        const addHalf = (atom: Atom, direction: THREE.Vector3, color: string) => {
          const length = distance / 2; const bond = new THREE.Mesh(new THREE.CylinderGeometry(.08, .08, length, 8), new THREE.MeshPhongMaterial({ color }));
          bond.position.copy(renderedPosition(atom)).addScaledVector(direction, length / 2);
          bond.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction); bondsGroup.add(bond);
        };
        const direction = end.clone().sub(start).normalize(); addHalf(first, direction, ELEMENT_COLORS[first.symbol] ?? ELEMENT_COLORS.default);
        addHalf(second, direction.clone().negate(), ELEMENT_COLORS[second.symbol] ?? ELEMENT_COLORS.default);
      };
      if (current.bonds?.length) { const byId = new Map(displayedAtoms.map(atom => [atom.id, atom])); for (const bond of current.bonds) { const first = byId.get(bond.first_atom_id); const second = byId.get(bond.second_atom_id); if (first && second) addBond(first, second, true); } }
      else if (displayedAtoms.length <= BOND_AUTO_DISABLE_ATOMS) {
        // Deliberately inspect neighbouring PBC images rather than only the
        // minimum image. This retains self-bonds and split bonds at cell
        // boundaries, matching the original AtomSculptor renderer.
        const [a = [0, 0, 0], b = [0, 0, 0], c = [0, 0, 0]] = current.cell ?? [];
        const xs = current.pbc[0] ? [-1, 0, 1] : [0], ys = current.pbc[1] ? [-1, 0, 1] : [0], zs = current.pbc[2] ? [-1, 0, 1] : [0];
        for (let i = 0; i < displayedAtoms.length; i += 1) for (let j = i; j < displayedAtoms.length; j += 1) for (const nx of xs) for (const ny of ys) for (const nz of zs) {
          if (i === j && (nx < 0 || (nx === 0 && ny < 0) || (nx === 0 && ny === 0 && nz <= 0))) continue;
          const first = displayedAtoms[i], second = displayedAtoms[j];
          const displacement = new THREE.Vector3(second.x - first.x + nx * a[0] + ny * b[0] + nz * c[0], second.y - first.y + nx * a[1] + ny * b[1] + nz * c[1], second.z - first.z + nx * a[2] + ny * b[2] + nz * c[2]);
          addBond(first, second, false, displacement);
        }
      }
      if (current.cell) { const [a, b, c] = current.cell.map(row => new THREE.Vector3(row[0], row[1], row[2])); const corners = [new THREE.Vector3(), a, b, c, a.clone().add(b), a.clone().add(c), b.clone().add(c), a.clone().add(b).add(c)].map(point => point.sub(midpoint)); const edges = [[0, 1], [0, 2], [0, 3], [1, 4], [1, 5], [2, 4], [2, 6], [3, 5], [3, 6], [4, 7], [5, 7], [6, 7]]; const points = edges.flatMap(([from, to]) => [corners[from], corners[to]]); const geometry = new THREE.BufferGeometry().setFromPoints(points); cellGroup.add(new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: 0x6c779d, transparent: true, opacity: .8 }))); }
      viewRadius = Math.max(3, ...displayedAtoms.map(atom => new THREE.Vector3(atom.x, atom.y, atom.z).distanceTo(midpoint))) * 1.9;
      const direction = directionForView(latest.current.cameraView);
      if (camera instanceof THREE.OrthographicCamera) { camera.left = -viewRadius; camera.right = viewRadius; camera.top = viewRadius; camera.bottom = -viewRadius; camera.updateProjectionMatrix(); }
      camera.position.copy(direction.multiplyScalar(viewRadius)); orbit.target.set(0, 0, 0); orbit.update(); configureGizmo();
    };
    const resize = () => { const width = Math.max(1, host.clientWidth); const height = Math.max(1, host.clientHeight); if (camera instanceof THREE.PerspectiveCamera) camera.aspect = width / height; else { const aspect = width / height; camera.left = -viewRadius * aspect; camera.right = viewRadius * aspect; camera.top = viewRadius; camera.bottom = -viewRadius; } camera.updateProjectionMatrix(); renderer.setSize(width, height, false); };
    const restoreCameraPose = () => { const saved = cameraPose.current; if (!saved || saved.view !== cameraView || saved.projection !== projection || saved.nonce !== cameraNonce) return; camera.position.fromArray(saved.position); camera.quaternion.fromArray(saved.quaternion); camera.zoom = saved.zoom; orbit.target.fromArray(saved.target); camera.updateProjectionMatrix(); orbit.update(); };
    const observer = new ResizeObserver(resize); observer.observe(host); resize(); draw(); restoreCameraPose();
    const hitAtom = (event: PointerEvent) => { const bounds = renderer.domElement.getBoundingClientRect(); pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1; pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1; raycaster.setFromCamera(pointer, camera); return raycaster.intersectObjects(meshes, false)[0]?.object.userData.atomId as number | undefined; };
    const updateOverlay = (event: PointerEvent) => { if (!boxStart) return; const bounds = renderer.domElement.getBoundingClientRect(); const x = Math.min(boxStart.x, event.clientX - bounds.left); const y = Math.min(boxStart.y, event.clientY - bounds.top); overlay.style.cssText = `display:block;left:${x}px;top:${y}px;width:${Math.abs(event.clientX - bounds.left - boxStart.x)}px;height:${Math.abs(event.clientY - bounds.top - boxStart.y)}px`; };
    const finishBox = (event: PointerEvent) => { if (!boxStart) return false; const bounds = renderer.domElement.getBoundingClientRect(); const endX = event.clientX - bounds.left; const endY = event.clientY - bounds.top; const width = Math.abs(endX - boxStart.x); const height = Math.abs(endY - boxStart.y); overlay.style.display = "none"; if (width < 4 && height < 4) { boxStart = null; orbit.enabled = true; return false; } const left = Math.min(boxStart.x, endX); const right = Math.max(boxStart.x, endX); const top = Math.min(boxStart.y, endY); const bottom = Math.max(boxStart.y, endY); const ids = boxStart.additive ? [...latest.current.structure.selected_atom_ids] : []; for (const mesh of meshes) { const p = mesh.position.clone().project(camera); const x = (p.x + 1) * .5 * bounds.width; const y = (1 - p.y) * .5 * bounds.height; if (x >= left && x <= right && y >= top && y <= bottom) { const id = mesh.userData.atomId as number; if (!ids.includes(id)) ids.push(id); } } boxStart = null; orbit.enabled = true; latest.current.onSelect(ids); return true; };
    const pointerDown = (event: PointerEvent) => { if (event.button !== 0 || gizmoDragging) return; const bounds = renderer.domElement.getBoundingClientRect(); const additive = event.shiftKey || event.metaKey || event.ctrlKey; const ownsPointer = latest.current.mode === "box" || (additive && latest.current.mode !== "measure"); if (ownsPointer) { event.preventDefault(); event.stopImmediatePropagation(); renderer.domElement.setPointerCapture(event.pointerId); } if (latest.current.mode === "box") { boxStart = { x: event.clientX - bounds.left, y: event.clientY - bounds.top, additive }; orbit.enabled = false; updateOverlay(event); return; } press = { x: event.clientX, y: event.clientY, additive, boxSelect: event.shiftKey }; };
    const pointerMove = (event: PointerEvent) => { if (boxStart) { event.preventDefault(); event.stopImmediatePropagation(); updateOverlay(event); return; } if (press && press.boxSelect && latest.current.mode !== "measure" && Math.hypot(event.clientX - press.x, event.clientY - press.y) > 4) { const bounds = renderer.domElement.getBoundingClientRect(); boxStart = { x: press.x - bounds.left, y: press.y - bounds.top, additive: press.additive }; press = null; orbit.enabled = false; event.preventDefault(); event.stopImmediatePropagation(); updateOverlay(event); return; } if (press || gizmoDragging) return; const id = hitAtom(event) ?? null; if (id !== hovered) { hovered = id; renderer.domElement.style.cursor = id === null ? "default" : "pointer"; latest.current.onHover(id); } };
    const pointerUp = (event: PointerEvent) => { if (renderer.domElement.hasPointerCapture(event.pointerId)) renderer.domElement.releasePointerCapture(event.pointerId); if (boxStart) { event.preventDefault(); event.stopImmediatePropagation(); finishBox(event); return; } const pointerPress = press; press = null; if (!pointerPress || Math.hypot(event.clientX - pointerPress.x, event.clientY - pointerPress.y) > 4 || gizmoDragging) return; const current = latest.current; if (current.mode === "add") { const bounds = renderer.domElement.getBoundingClientRect(); pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1; pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1; raycaster.setFromCamera(pointer, camera); const point = new THREE.Vector3(); if (raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), 0), point)) current.onAdd({ x: point.x + midpoint.x, y: point.y + midpoint.y, z: point.z + midpoint.z }); return; } const atomId = hitAtom(event); if (current.mode === "measure") { if (atomId === undefined) return; measured = measured.length >= 2 ? [atomId] : [...measured, atomId]; current.onMeasure(measured); return; } if (atomId === undefined) { if (!pointerPress.additive) current.onSelect([]); return; } const prior = current.structure.selected_atom_ids; current.onSelect(pointerPress.additive ? (prior.includes(atomId) ? prior.filter(id => id !== atomId) : [...prior, atomId]) : [atomId]); };
    const projectedTransform = () => { if (!transformStart) return []; const rotation = pivot.quaternion.clone(); const scale = pivot.scale.clone(); return latest.current.structure.atoms.map(atom => { const initial = transformStart?.get(atom.id); if (!initial) return atom; const position = new THREE.Vector3(initial.x, initial.y, initial.z).sub(midpoint).sub(pivotStart).multiply(scale).applyQuaternion(rotation).add(pivot.position).add(midpoint); return { ...atom, x: position.x, y: position.y, z: position.z }; }); };
    gizmo.addEventListener("dragging-changed", event => { const dragging = (event as { value: boolean }).value; gizmoDragging = dragging; orbit.enabled = !dragging; if (dragging) { transformStart = new Map(latest.current.structure.atoms.filter(atom => latest.current.structure.selected_atom_ids.includes(atom.id)).map(atom => [atom.id, { ...atom }])); pivotStart = pivot.position.clone(); transformDirty = false; } else if (transformStart && transformDirty) { latest.current.onTransform(projectedTransform()); transformStart = null; } });
    gizmo.addEventListener("objectChange", () => { if (!transformStart) return; transformDirty = true; const changed = new Map(projectedTransform().map(atom => [atom.id, atom])); for (const mesh of meshes) { const atom = changed.get(mesh.userData.atomId as number); if (atom) mesh.position.copy(renderedPosition(atom)); } });
    const nudge = (event: KeyboardEvent) => { if (!TRANSFORM_MODES.has(latest.current.mode) || event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return; const key = event.key.toLowerCase(); const direction = key === "arrowup" || key === "w" ? "up" : key === "arrowdown" || key === "s" ? "down" : key === "arrowleft" || key === "a" ? "left" : key === "arrowright" || key === "d" ? "right" : null; if (!direction) return; const selected = latest.current.structure.atoms.filter(atom => latest.current.structure.selected_atom_ids.includes(atom.id)); if (!selected.length) return; event.preventDefault(); const cameraDirection = new THREE.Vector3(); camera.getWorldDirection(cameraDirection); const up = camera.up.clone().normalize(); const right = new THREE.Vector3().crossVectors(cameraDirection, up).normalize(); const screenUp = new THREE.Vector3().crossVectors(right, cameraDirection).normalize(); const centre = centroid(selected); const next = latest.current.structure.atoms.map(atom => { if (!latest.current.structure.selected_atom_ids.includes(atom.id)) return atom; if (latest.current.mode === "translate") { const directionVector = direction === "up" ? screenUp.clone() : direction === "down" ? screenUp.clone().multiplyScalar(-1) : direction === "left" ? right.clone().multiplyScalar(-1) : right.clone(); const delta = directionVector.multiplyScalar(.1); return { ...atom, x: atom.x + delta.x, y: atom.y + delta.y, z: atom.z + delta.z }; } if (latest.current.mode === "scale") { const factor = direction === "up" || direction === "right" ? 1.02 : .98; return { ...atom, x: centre.x + (atom.x - centre.x) * factor, y: centre.y + (atom.y - centre.y) * factor, z: centre.z + (atom.z - centre.z) * factor }; } const axis = direction === "up" || direction === "down" ? right : screenUp; const angle = (direction === "up" || direction === "right" ? 1 : -1) * Math.PI / 180; const point = new THREE.Vector3(atom.x, atom.y, atom.z).sub(centre).applyAxisAngle(axis, angle).add(centre); return { ...atom, x: point.x, y: point.y, z: point.z }; }); latest.current.onTransform(next); };
    renderer.domElement.addEventListener("pointerdown", pointerDown, true); renderer.domElement.addEventListener("pointermove", pointerMove, true); renderer.domElement.addEventListener("pointerup", pointerUp, true); renderer.domElement.addEventListener("pointerleave", () => { if (hovered !== null) { hovered = null; latest.current.onHover(null); } }); window.addEventListener("keydown", nudge); renderer.setAnimationLoop(() => { orbit.update(); renderer.render(scene, camera); });
    const capture: StructureViewportCapture = async (maximum, view = "current") => {
      // Copy the WebGL frame immediately into a bounded 2D canvas.  It avoids
      // persisting a browser screenshot and remains reliable with ordinary
      // WebGL frame-buffer settings.
      // A requested canonical view is used only for this render. Preserve and
      // restore the complete user camera state so an agent observation cannot
      // visibly interrupt a researcher orbiting or editing the structure.
      const savedPosition = camera.position.clone(); const savedQuaternion = camera.quaternion.clone(); const savedZoom = camera.zoom; const savedTarget = orbit.target.clone();
      try {
        if (view !== "current") { camera.position.copy(directionForView(view).multiplyScalar(viewRadius)); orbit.target.set(0, 0, 0); camera.lookAt(orbit.target); camera.updateProjectionMatrix(); orbit.update(); }
        renderer.render(scene, camera);
        const source = renderer.domElement;
        const sourceWidth = source.width, sourceHeight = source.height;
        if (!sourceWidth || !sourceHeight) throw new Error("The structure viewport is not ready to capture.");
        const requested = Number.isFinite(maximum) ? maximum : 1280;
        const limit = Math.max(256, Math.min(1600, Math.floor(requested)));
        const scale = Math.min(1, limit / Math.max(sourceWidth, sourceHeight));
        const target = document.createElement("canvas");
        target.width = Math.max(1, Math.round(sourceWidth * scale)); target.height = Math.max(1, Math.round(sourceHeight * scale));
        const context = target.getContext("2d");
        if (!context) throw new Error("PNG capture is unavailable in this browser.");
        context.fillStyle = "#181818"; context.fillRect(0, 0, target.width, target.height);
        context.drawImage(source, 0, 0, target.width, target.height);
        return { dataBase64: target.toDataURL("image/png").split(",")[1], width: target.width, height: target.height };
      } finally {
        if (view !== "current") { camera.position.copy(savedPosition); camera.quaternion.copy(savedQuaternion); camera.zoom = savedZoom; orbit.target.copy(savedTarget); camera.updateProjectionMatrix(); orbit.update(); renderer.render(scene, camera); }
      }
    };
    onCaptureReady?.(capture);
    return () => { onCaptureReady?.(null); cameraPose.current = { view: cameraView, projection, nonce: cameraNonce, position: camera.position.toArray(), quaternion: camera.quaternion.toArray(), target: orbit.target.toArray(), zoom: camera.zoom }; observer.disconnect(); renderer.setAnimationLoop(null); renderer.domElement.removeEventListener("pointerdown", pointerDown, true); renderer.domElement.removeEventListener("pointermove", pointerMove, true); renderer.domElement.removeEventListener("pointerup", pointerUp, true); window.removeEventListener("keydown", nudge); gizmo.dispose(); orbit.dispose(); disposeGroup(atomsGroup); disposeGroup(bondsGroup); disposeGroup(cellGroup); overlay.remove(); renderer.dispose(); renderer.domElement.remove(); };
  }, [structure, mode, cameraView, projection, cameraNonce, selectionVisible, onCaptureReady]);
  return <div className="atomsculptor-legacy-viewport nodrag nopan nowheel" ref={mount} />;
}
