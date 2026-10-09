import { useEffect, useRef, useState, type PointerEvent, type ReactNode } from 'react';
import { ArrowLeft, ArrowRight, Check, Eye, Layers, Redo2, SlidersHorizontal, Undo2, WandSparkles, X } from 'lucide-react';
import type { NodeSurfaceLevel } from '../types/world';
import { FaceArtwork, ShapePath } from './FaceArtwork';
import { ELEMENT_LABELS, FACE_MODES, MODE_LABELS, faceStudio, presetSurface, resizeDesign, snapBox } from './faceDesign';
import { chooseRecipe, contrast, designTokens, newSlot, freeSurface, restyleSurface, polishSurface, recipeSettings, recipeProduction, reflowSurface } from './designRecipes';
import { printingLayers, printContentIds, newProductionLayer, MAX_PRODUCTION_LAYERS, type CardProduction, type PrintProof } from '../cards/cardProduction';
import { DesignerPanel, SlotEditor, Choices, NumberControl, type DesignerSection } from './DesignerControls';
import type { FaceBox, FaceButtonAction, FaceDesign, FaceElement, FaceShape, FaceStudio, FunctionDesign, SurfaceDesign, SurfaceRecipe } from './types';
import { BUTTON_ACTIONS, buttonAction } from './faceButtons';
import { ProcessLayerRail } from './ProcessLayerControls';
import './faceDesigner.css';

type Drag = { pointer: number; x: number; y: number; id: string; kind: 'move' | 'resize' | 'vertex'; vertex?: number; surface: SurfaceDesign; before: FaceDesign; scale: number; changed: boolean };
const STEPS = [{ id: 'stock', label: '卡纸' }, { id: 'process', label: '工艺层' }, { id: 'preview', label: '成品' }] as const;
const shapeLabels = { rect: '圆角矩形', ellipse: '椭圆', polygon: '多边形' };
const clamp = (n: number, min: number, max: number) => Math.max(min, Math.min(max, n));

export function FaceDesignerCanvas({ face, onChange, saveControl, functionDesign }: { face: FaceDesign; functionDesign?: FunctionDesign; onChange: (patch: Partial<FaceDesign>) => void; saveControl?: ReactNode }) {
  const [mode, setMode] = useState<NodeSurfaceLevel>('preview'), [section, setSection] = useState<DesignerSection>('stock');
  const [processSelection, setProcessSelection] = useState('');
  const [proof,setProof]=useState<PrintProof>('composite'),[compare,setCompare]=useState(false);
  const [layerMode, setLayerMode] = useState<'elements' | 'shapes'>('elements'), [selection, setSelection] = useState('');
  const [snapping, setSnapping] = useState(true), [checker, setChecker] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [historyVersion, setHistoryVersion] = useState(0);
  const [guides, setGuides] = useState<{ axis: 'x' | 'y'; position: number }[]>([]), [wellSize, setWellSize] = useState({ width: 640, height: 570 });
  const history = useRef<{ past: FaceDesign[]; future: FaceDesign[] }>({ past: [], future: [] });
  const stage = useRef<HTMLDivElement>(null), well = useRef<HTMLDivElement>(null), drag = useRef<Drag>();
  const latestFace = useRef(face); latestFace.current = face;
  const studio = faceStudio(face), surface = studio.modes[mode] ?? presetSurface(mode, face), settings = recipeSettings(face, surface);
  const tokens = designTokens(surface, settings), advanced = section === 'advanced';
  const preview = section === 'preview' || section === 'preset', editing = section === 'process' || advanced;
  const stepIndex = Math.max(0, STEPS.findIndex(step => step.id === (section === 'advanced' ? 'process' : section === 'preset' ? 'preview' : section)));
  const production = recipeProduction(settings), processes = printingLayers(production);
  const processId = processes.find(layer => layer.id === processSelection)?.id ?? processes.at(-1)?.id;
  const stepRail = useRef<HTMLElement>(null);
  useEffect(() => { stepRail.current?.querySelector('[aria-current=step]')?.scrollIntoView?.({ block: 'nearest', inline: 'nearest', behavior: 'smooth' }); }, [section, processId]);
  const activeProcess = processes.find(layer => layer.id === processId);
  const editableIds = activeProcess ? printContentIds(activeProcess, processes, [...surface.elements, ...surface.shapes.slice(1)].map(item => item.id)) : [];
  const selected = surface[layerMode].find(item => item.id === selection);
  const shape = layerMode === 'shapes' ? selected as FaceShape | undefined : undefined;
  const element = layerMode === 'elements' ? selected as FaceElement | undefined : undefined;
  const inspect = editing && (advanced || Boolean(selected));
  const scale = Math.min(mode === 'node' ? 2 : 4, Math.max(100, wellSize.width - 48) / surface.width / (preview&&compare?2:1), Math.max(100, wellSize.height - 40) / surface.height);
  useEffect(() => {
    if (!well.current || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(entries => { const rect = entries[0].contentRect; setWellSize({ width: rect.width, height: rect.height }); });
    observer.observe(well.current); return () => observer.disconnect();
  }, []);
  const record = (before: FaceDesign) => { history.current.past = [...history.current.past.slice(-49), before]; history.current.future = []; setHistoryVersion(n => n + 1); };
  const commit = (patch: Partial<FaceDesign>) => {
    if (JSON.stringify({ ...face, ...patch }) === JSON.stringify(face)) return;
    record(face); onChange({ studio, ...patch }); setNotice('');
  };
  const updateStudio = (patch: Partial<FaceStudio>) => commit({ studio: { ...studio, ...patch } });
  const updateSurface = (next: SurfaceDesign, remember = true) => {
    const patch = { studio: { ...studio, modes: { ...studio.modes, [mode]: next } } };
    if (remember) commit(patch); else onChange(patch);
  };
  const updateSurfaces = (transform: (value: SurfaceDesign) => SurfaceDesign, patch: Partial<FaceDesign> = {}) =>
    commit({ ...patch, studio: { ...studio, modes: Object.fromEntries(Object.entries(studio.modes).map(([key, value]) => [key, transform(value)])) } });
  const updateSettings = (patch: Partial<SurfaceRecipe>) => {
    const transform = (value: SurfaceDesign) => {
      const current = recipeSettings(face, value), next = { ...current, production:recipeProduction(current), ...patch, tokens: { ...(patch.tokens ?? current.tokens) } };
      // Intent controls supersede exact overrides for that same intent.
      if (patch.appearance) for (const key of ['background', 'surface', 'text', 'muted', 'border'] as const) delete next.tokens[key];
      if (patch.softness !== undefined) delete next.tokens.radius;
      if (patch.density) { delete next.tokens.margin; delete next.tokens.gap; }
      if (patch.emphasis) delete next.tokens.title_size;
      return patch.density || patch.emphasis || patch.alignment || patch.field_layout || patch.tokens?.margin !== undefined || patch.tokens?.gap !== undefined ? reflowSurface(value, face, next) : restyleSurface(value, face, next);
    };
    if (advanced) updateSurface(transform(surface)); else updateSurfaces(transform);
  };
  const updateFace = (patch: Partial<FaceDesign>) => {
    const next = { ...face, ...patch };
    commit({ ...patch, studio: { ...studio, modes: Object.fromEntries(Object.entries(studio.modes).map(([key, value]) => [key, value.design ? restyleSurface(value, next, recipeSettings(next, value)) : value])) } });
  };
  const updateItem = (patch: Partial<Omit<FaceElement, 'kind'> & Omit<FaceShape, 'kind'>>, reflow = false) => {
    const next = { ...surface, [layerMode]: surface[layerMode].map(item => item.id === selection ? { ...item, ...patch } : item) };
    if (layerMode === 'shapes' && selection === surface.shapes[0].id && next.design) next.design = { ...next.design, tokens: { ...next.design.tokens,
      ...(patch.fill ? { background: patch.fill } : {}), ...(patch.radius !== undefined ? { radius: patch.radius } : {}) } };
    updateSurface(reflow && surface.design && (patch.placement === 'slot' || patch.sizing || patch.pin) ? reflowSurface(next, face) : next);
  };
  const remove = (id = selection) => {
    if (layerMode === 'shapes' && surface.shapes.length === 1) return;
    const next = { ...surface, [layerMode]: surface[layerMode].filter(item => item.id !== id) };
    updateSurface(next); setSelection('');
  };
  const undo = (redo = false) => {
    const from = redo ? history.current.future : history.current.past, to = redo ? history.current.past : history.current.future;
    const next = from.pop(); if (!next) return;
    to.push(face); onChange({ ...next, studio: next.studio }); setHistoryVersion(n => n + 1); setNotice(redo ? '已重做' : '已撤销');
  };
  const enableMode = (target: NodeSurfaceLevel, enabled: boolean) => {
    const modes = enabled ? FACE_MODES.filter(item => item === target || studio.enabled.includes(item)) : studio.enabled.filter(item => item !== target);
    if (!modes.length) return;
    updateStudio({ enabled: modes, initial: modes.includes(studio.initial) ? studio.initial : modes[0], open: modes.includes(studio.open) ? studio.open : modes[modes.length - 1],
      modes: { ...studio.modes, [target]: studio.modes[target] ?? reflowSurface(presetSurface(target, face), face) } });
  };
  const changeSection = (value: DesignerSection) => { if (drag.current) finishDrag(); setSection(value); setLayerMode('elements'); setSelection(''); };
  const selectProcess = (id: string) => { setProcessSelection(id); changeSection('process'); };
  const navigateStep = (offset: number) => {
    const index = section === 'stock' ? -1 : preview ? processes.length : processes.findIndex(layer => layer.id === processId);
    const target = clamp(index + offset, -1, processes.length);
    if (target === -1) changeSection('stock'); else if (target === processes.length) changeSection('preview'); else selectProcess(processes[target].id);
  };
  const updateProduction = (nextProduction: CardProduction) => {
    updateSurfaces(value => {
      const old = printingLayers(recipeProduction(recipeSettings(face, value)));
      const next = nextProduction.layers ?? [];
      const ids = [...value.elements, ...value.shapes.slice(1)].map(item => item.id);
      const removed = new Set(old.filter(layer => !next.some(item => item.id === layer.id)).flatMap(layer => printContentIds(layer, old, ids)));
      const retained = new Set(next.flatMap(layer => layer.content?.source === 'elements' ? layer.content.elementIds : []));
      const discard = (id: string) => removed.has(id) && !retained.has(id);
      const styled = restyleSurface(value, face, { ...recipeSettings(face, value), production: nextProduction });
      const recolor = next.filter(layer => layer.kind === 'ink' && old.some(item => item.id === layer.id && item.color !== layer.color));
      const ink = (id: string) => recolor.find(layer => printContentIds(layer, next, ids).includes(id))?.color;
      return { ...styled, elements: styled.elements.filter(item => !discard(item.id)).map(item => ink(item.id) ? { ...item, color: ink(item.id)!, overrides: { ...item.overrides, color: ink(item.id)! } } : item),
        shapes: styled.shapes.filter((item, index) => index === 0 || !discard(item.id)).map((item,index) => index > 0 && ink(item.id) ? { ...item, fill: ink(item.id)! } : item) };
    });
  };
  const duplicateProcess = (id: string) => {
    const source = processes.find(layer => layer.id === id);
    if (!source || processes.length >= MAX_PRODUCTION_LAYERS) return;
    const copies = new Map<string, string>(), copyId = newProductionLayer(source.kind).id;
    const clonedId = (value: string) => { if (!copies.has(value)) copies.set(value, `copy-${crypto.randomUUID()}`); return copies.get(value)!; };
    const views = Object.values(studio.modes);
    if (views.some(value => {
      const passes = printingLayers(recipeProduction(recipeSettings(face, value))), pass = passes.find(layer => layer.id === id);
      const ids = pass ? printContentIds(pass, passes, [...value.elements, ...value.shapes.slice(1)].map(item => item.id)) : [];
      return value.elements.length + value.elements.filter(item => ids.includes(item.id)).length > 32 || value.shapes.length + value.shapes.slice(1).filter(item => ids.includes(item.id)).length > 24;
    })) { setNotice('元素数量已达上限，无法复制此层。'); return; }
    updateSurfaces(value => {
      const recipe = recipeSettings(face, value), current = recipeProduction(recipe), passes = printingLayers(current);
      const at = passes.findIndex(layer => layer.id === id); if (at < 0) return value;
      const pass = passes[at], ids = printContentIds(pass, passes, [...value.elements, ...value.shapes.slice(1)].map(item => item.id));
      const copy = { ...structuredClone(pass), id: copyId, ...(pass.content ? { content: { source: 'elements' as const, elementIds: ids.map(clonedId) } } : {}),
        ...(pass.kind === 'ink' && pass.content?.source === 'all' && !pass.pattern ? { pattern: { motif: current.print.motif, density: current.print.density } } : {}) };
      passes.splice(at + 1, 0, copy);
      return { ...value, elements: [...value.elements, ...value.elements.filter(item => ids.includes(item.id)).map(item => ({ ...structuredClone(item), id: clonedId(item.id) }))],
        shapes: [...value.shapes, ...value.shapes.slice(1).filter(item => ids.includes(item.id)).map(item => ({ ...structuredClone(item), id: clonedId(item.id) }))],
        design: { ...recipe, production: { ...current, print: { ...current.print, layered: true }, layers: passes } } };
    });
    selectProcess(copyId);
  };
  const startDrag = (event: PointerEvent, id: string, kind: Drag['kind'] = 'move', vertex?: number) => {
    if (event.button !== 0 || !stage.current) return;
    event.stopPropagation(); event.preventDefault(); setSelection(id);
    stage.current.focus({ preventScroll: true });
    stage.current.setPointerCapture(event.pointerId);
    drag.current = { pointer: event.pointerId, x: event.clientX, y: event.clientY, id, kind, vertex, surface, before: face, changed: false,
      scale: stage.current.getBoundingClientRect().width / surface.width };
  };
  const move = (event: PointerEvent) => {
    const active = drag.current; if (!active || active.pointer !== event.pointerId) return;
    const dx = (event.clientX - active.x) / active.scale, dy = (event.clientY - active.y) / active.scale;
    if (!active.changed && Math.abs(dx) + Math.abs(dy) < 1) return;
    active.changed = true;
    const item = active.surface[layerMode].find(value => value.id === active.id)!;
    let changed: FaceBox = item;
    if (active.kind === 'vertex') {
      const polygon = item as FaceShape;
      changed = { ...polygon, points: polygon.points.map((point, index) => {
        if (index !== active.vertex) return point;
        const x = point.x * polygon.width + dx, y = point.y * polygon.height + dy;
        return { x: clamp((snapping && !event.altKey ? Math.round(x / 8) * 8 : x) / polygon.width, 0, 1), y: clamp((snapping && !event.altKey ? Math.round(y / 8) * 8 : y) / polygon.height, 0, 1) };
      }) } as FaceShape;
    } else {
      const candidate = active.kind === 'resize' ? { ...item, width: item.width + dx, height: item.height + dy } : { ...item, x: item.x + dx, y: item.y + dy };
      const snapped = snapBox(candidate, active.surface, active.surface[layerMode].filter(value => value.id !== item.id), snapping && !event.altKey, active.kind === 'resize');
      if (event.altKey) snapped.box = { ...candidate, x: clamp(candidate.x, -2048, 2048), y: clamp(candidate.y, -2048, 2048), width: clamp(candidate.width, 8, 2048), height: clamp(candidate.height, 8, 2048) };
      changed = layerMode === 'elements' ? { ...snapped.box, placement: 'free' } as FaceElement : snapped.box; setGuides(snapped.guides);
    }
    updateSurface({ ...active.surface, [layerMode]: active.surface[layerMode].map(value => value.id === item.id ? changed : value) }, false);
  };
  const finishDrag = (cancel = false) => {
    const active = drag.current; if (!active) return;
    drag.current = undefined; setGuides([]);
    if (cancel) onChange({ ...active.before, studio: active.before.studio }); else if (active.changed) record(active.before);
  };
  const assignInk = (next: SurfaceDesign, inkId: string | undefined, itemId: string): SurfaceDesign => {
    if (!inkId) return next;
    const production = recipeProduction(recipeSettings(face, next));
    const layers = printingLayers(production).map(layer => layer.id === inkId
      ? { ...layer, content: { source: layer.content?.source ?? 'elements', elementIds: [...(layer.content?.elementIds ?? []), itemId] } } : layer);
    return { ...next, design: { ...recipeSettings(face, next), production: { ...production, print: { ...production.print, layered: true }, layers } } };
  };
  const addShape = (kind: FaceShape['kind'], inkId?: string, full = false) => {
    if (surface.shapes.length >= 24) return;
    const id = `shape-${crypto.randomUUID()}`, width = Math.min(160, surface.width - 32), height = Math.min(160, surface.height - 32);
    updateSurface(assignInk({ ...surface, shapes: [...surface.shapes, { id, kind, x: full ? 0 : 16, y: full ? 0 : 16, width: full ? surface.width : width, height: full ? surface.height : height, radius: full ? 0 : 20, fill: processes.find(layer => layer.id === inkId)?.color ?? face.color,
      points: kind === 'polygon' ? [{ x: .5, y: 0 }, { x: 1, y: .35 }, { x: .8, y: 1 }, { x: .2, y: 1 }, { x: 0, y: .35 }] : [] }] }, inkId, id));
    setSelection(id); setLayerMode('shapes');
  };
  const addSlot = (kind: FaceElement['kind'], inkId?: string, action: FaceButtonAction = 'custom') => {
    const added = newSlot(kind);
    const pass = processes.find(layer => layer.id === inkId);
    const ownedIds = pass ? printContentIds(pass, processes, surface.elements.map(item => item.id)) : surface.elements.map(item => item.id);
    if (surface.elements.length >= 32 || (!['text', 'illustration', 'button'].includes(kind) && surface.elements.some(item => item.kind === kind && ownedIds.includes(item.id)))) return;
    if (surface.elements.some(item => item.id === added.id)) added.id = `${kind}-${crypto.randomUUID()}`;
    if (kind === 'illustration') added.id = `image-${crypto.randomUUID()}`;
    if (kind === 'text') { added.id = `text-${crypto.randomUUID()}`; added.text = '自由文本'; }
    if (kind === 'button') { added.id = `button-${crypto.randomUUID()}`; added.button = { action }; added.text = BUTTON_ACTIONS.find(item => item.action === action)!.label; }
    const sizes: Partial<Record<FaceElement['kind'], [number, number]>> = {
      icon: [56, 56], illustration: [180, 135], title: [240, 64], description: [240, 72],
      fields: [280, 100], action: [180, 36], button: [140, 36], result: [280, 96], text: [200, 48],
    };
    const [width, height] = sizes[kind] ?? [180, 32];
    added.placement = 'free'; added.width = Math.min(width, surface.width - 48); added.height = Math.min(height, surface.height - 48);
    added.font_size = kind === 'title' ? tokens.title_size : kind === 'icon' ? 32 : tokens.body_size;
    added.x = Math.round((surface.width - added.width) / 2); added.y = Math.round((surface.height - added.height) / 2); added.color = processes.find(layer => layer.id === inkId)?.color ?? tokens.text;
    updateSurface(assignInk({ ...surface, elements: [...surface.elements, added] }, inkId, added.id)); setLayerMode('elements'); setSelection(added.id);
  };
  const upload = async (file?: File, elementId?: string, layerId?: string) => {
    if (!file) return; setError(''); const targetMode = mode;
    try {
      if (file.type !== 'image/png' || file.size > 1024 * 1024) throw new Error('请选择不超过 1 MiB 的 PNG 图片。');
      const png = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsDataURL(file); });
      const img = new Image(); img.src = png; await img.decode();
      if (img.width > 2048 || img.height > 2048) throw new Error('图片尺寸不能超过 2048 × 2048。');
      const current = latestFace.current, currentStudio = faceStudio(current), target = currentStudio.modes[targetMode] ?? presetSurface(targetMode, current);
      let next = elementId ? { ...target, elements: target.elements.map(e => e.id === elementId ? { ...e, image_png: png } : e) } : { ...target, background_png: png, image_shape: true };
      if (layerId) {
        const targetProduction = recipeProduction(recipeSettings(current, target));
        if (!printingLayers(targetProduction).some(layer => layer.id === layerId)) return;
        if (target.elements.length >= 32) throw new Error('每张卡面最多添加 32 个内容元素。');
        const added = { ...newSlot('illustration'), id: `image-${crypto.randomUUID()}`, image_png: png, placement: 'free' as const,
          width: Math.min(180, target.width - 32), height: Math.min(135, target.height - 32), x: 16, y: 16 };
        next = assignInk({ ...target, elements: [...target.elements, added] }, layerId, added.id);
        if (mode === targetMode) { setLayerMode('elements'); setSelection(added.id); }
      }
      record(current); onChange({ studio: { ...currentStudio, modes: { ...currentStudio.modes, [targetMode]: next } } });
    } catch (cause) { setError(cause instanceof Error ? cause.message : '无法读取 PNG 图片。'); }
  };
  const editText = (item: FaceElement, value: string) => {
    const key = ({ title: 'title', description: 'description', help: 'help_text', action: 'button_label' } as Partial<Record<FaceElement['kind'], keyof FaceDesign>>)[item.kind];
    if (key) updateFace({ [key]: value });
    else { const next = { ...surface, elements: surface.elements.map(e => e.id === item.id ? { ...e, text: value } : e) }; updateSurface(next); }
  };
  const applyPolish = () => {
    const details = new Set<string>();
    const transform = (value: SurfaceDesign) => { const result = polishSurface(value, face); result.details.forEach(detail => details.add(detail)); return result.surface; };
    if (advanced) updateSurface(transform(surface)); else updateSurfaces(transform);
    setNotice(details.size ? `已调整${advanced ? '当前视图' : '四种视图'}的${details.size}项细节：${[...details].join('、')}。可撤销。` : '间距、层级与对比度已协调，无需调整。');
  };
  const layerName = (item: FaceShape | FaceElement) => 'fill' in item ? shapeLabels[item.kind] : ELEMENT_LABELS[item.kind];
  const slotEditor = (item: FaceElement) => <SlotEditor item={item} face={face} editText={editText} updateFace={updateFace} upload={upload} />;
  return <div className="face-studio" data-step={section} data-history={historyVersion} data-advanced={advanced} onKeyDown={event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !(event.target instanceof HTMLElement && event.target.closest('input, textarea, select, [contenteditable=true]'))) { event.preventDefault(); event.stopPropagation(); undo(event.shiftKey); }
  }}>
    <header className="face-studio-header"><span className="face-toolbar-context"><Layers size={15} />卡面工坊</span>
      <div className="face-toolbar"><div className="face-history"><button type="button" aria-label="撤销" title="撤销 · Ctrl Z" disabled={!history.current.past.length} onClick={() => undo()}><Undo2 size={16} /></button>
        <button type="button" aria-label="重做" title="重做 · Ctrl Shift Z" disabled={!history.current.future.length} onClick={() => undo(true)}><Redo2 size={16} /></button></div>
        <button type="button" className="face-polish" onClick={applyPolish}><WandSparkles size={15} />润色</button>
        <button type="button" aria-label="预设" aria-pressed={section==='preset'} onClick={() => changeSection('preset')}><Layers size={15} />预设</button><button type="button" aria-label="高级设置" aria-pressed={advanced} onClick={() => changeSection(advanced?'process':'advanced')}><SlidersHorizontal size={15} /></button>
        <button type="button" aria-pressed={preview} onClick={() => changeSection(preview ? 'process' : 'preview')}><Eye size={15} />{preview ? '继续编辑' : '预览'}</button>{saveControl}</div>
    </header>
    <nav ref={stepRail} className="face-step-rail" aria-label="设计步骤" onKeyDown={event => {
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { event.preventDefault(); navigateStep(event.key === 'ArrowRight' ? 1 : -1); }
    }}>
      <button type="button" className="face-end-step" aria-label="卡纸" aria-pressed={section === 'stock'} aria-current={section === 'stock' ? 'step' : undefined} onClick={() => changeSection('stock')}><span>01</span><b>卡纸</b></button>
      <ProcessLayerRail production={production} selectedId={editing ? processId : undefined} onSelect={selectProcess} onChange={updateProduction} />
      <button type="button" className="face-end-step" aria-label="成品" aria-pressed={section === 'preview'} aria-current={section === 'preview' ? 'step' : undefined} onClick={() => changeSection('preview')}><Check size={16} /><b>成品</b></button>
    </nav>
    <div className="face-studio-body" data-inspect={inspect} data-preview={preview}>
      <aside className="face-sidebar"><DesignerPanel {...{ proof,compare,setProof,setCompare,face, surface, settings, studio, mode, section, processId, selectProcess, updateProduction, duplicateProcess, layerMode, selection, snapping, checker, updateSurface, updateSettings, updateFace, updateStudio, enableMode, addSlot, addShape, remove, upload, editText }}
        releaseLayout={()=>{updateSurface(freeSurface(surface));setNotice('已保留所有位置，全部图层可自由编辑');}}
        blankLayout={()=>{updateSurface({...surface,elements:[],shapes:surface.shapes.slice(0,1),background_png:'',design:{...settings,production:{...recipeProduction(settings),print:{...recipeProduction(settings).print,motif:'none',density:0}}}});setSelection('');}}
        selectLayer={(id,kind)=>{setLayerMode(kind);setSelection(id);}}
        setMode={value => { setMode(value); setSelection(''); }} setSection={changeSection} setLayerMode={value => { setLayerMode(value); setSelection(''); }} setSnapping={setSnapping} setChecker={setChecker}
        select={id => { setLayerMode('elements'); setSelection(id); }} showPreview={() => changeSection('preview')}
        resize={(width, height) => updateSurface(resizeDesign(surface, width, height))}
        applyPreset={(preset,color)=>updateSurfaces(value=>reflowSurface(chooseRecipe(value,{...face,color},preset.recipe),{...face,color},structuredClone(preset)),{color})}
        applyRecipe={recipe => updateSurfaces(value => chooseRecipe(value, face, recipe))}
        applyKit={(kit, appearance, accent) => updateSurfaces(value => restyleSurface(value, { ...face, color: accent }, { ...recipeSettings(face, value), kit, appearance, tokens: {} }), { color: accent })} />
      </aside>
      <main className="face-studio-center"><div className="face-canvas-heading"><div><span className="face-live-dot" />{section==='stock'?'纸张预览':preview ? '成品预览' : '实时预览'}</div>
        <select aria-label="预览显示模式" value={mode} onChange={e => { setMode(e.target.value as NodeSurfaceLevel); setSelection(''); }}>{FACE_MODES.map(value => <option key={value} value={value}>{MODE_LABELS[value]}</option>)}</select></div>
        <div ref={well} className="face-canvas-well" data-checker={checker && advanced} onPointerDown={e => { if (e.target === e.currentTarget) setSelection(''); }}>
          {preview&&compare&&<div className="face-proof-base" style={{width:surface.width*scale,height:surface.height*scale}}><span>未加工卡面</span><div style={{width:surface.width,height:surface.height,transform:'scale('+scale+')',transformOrigin:'top left'}}><FaceArtwork face={face} surface={surface} functionDesign={functionDesign} sample interactive={false} proof="artwork" /></div></div>}
          <div className="face-stage-size" style={{ width: surface.width * scale, height: surface.height * scale }}><div ref={stage} className="face-edit-stage" tabIndex={0} role="group" aria-label={`${MODE_LABELS[mode]}模式设计画布`}
            style={{ width: surface.width, height: surface.height, transform: `scale(${scale})` }} data-mode={mode}
            onPointerMove={move} onPointerUp={() => finishDrag()} onPointerCancel={() => finishDrag(true)} onLostPointerCapture={() => finishDrag()}
            onKeyDown={event => {
              if (event.target instanceof HTMLElement && event.target.closest('input, textarea, select, [contenteditable=true]')) return;
              if (event.key === 'Escape') { event.stopPropagation(); if (drag.current) finishDrag(true); else setSelection(''); return; }
              if (!editing) return;
              if (event.key === 'Delete' || event.key === 'Backspace') { if (selected) { event.preventDefault(); event.stopPropagation(); remove(); } return; }
              if (selected && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
                event.preventDefault(); event.stopPropagation(); const step = event.shiftKey ? 8 : 1;
                updateItem({ ...(element ? { placement: 'free' as const } : {}), x: clamp(selected.x + (event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0), 0, Math.max(0, surface.width - selected.width)),
                  y: clamp(selected.y + (event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0), 0, Math.max(0, surface.height - selected.height)) });
              }
            }}>
            <FaceArtwork face={face} level={mode} surface={section==='stock'?{...surface,elements:[],shapes:surface.shapes.slice(0,1),background_png:'',design:{...settings,production:{...recipeProduction(settings),layers:[],print:{motif:'none',density:0,layered:true}}}}:surface} functionDesign={functionDesign} sample interactive proof={preview?proof:section==='stock'?'artwork':'composite'}>
            {editing && <div className="face-edit-overlay" onPointerDown={() => setSelection('')}>
              {layerMode === 'shapes' ? <svg className="face-shape-hits" viewBox={`0 0 ${surface.width} ${surface.height}`}>{surface.shapes.filter(item => advanced || editableIds.includes(item.id)).map(item => <g key={item.id} className={selection === item.id ? 'is-selected' : ''} aria-label={`选择${shapeLabels[item.kind]}`} role="button" tabIndex={0}
                onFocus={() => setSelection(item.id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelection(item.id); } }} onPointerDown={event => startDrag(event, item.id)}><ShapePath shape={item} /></g>)}</svg>
                : surface.elements.filter(item => advanced || editableIds.includes(item.id)).map(item => <div key={item.id} role="button" tabIndex={0} aria-label={`设计${ELEMENT_LABELS[item.kind]}`} data-edit-element={item.id} data-free={true}
                  className={`face-element-hit ${selection === item.id ? 'is-selected' : ''}`} style={{ left: item.x, top: item.y, width: item.width, height: item.height }}
                  onFocus={() => { setLayerMode('elements'); setSelection(item.id); }} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelection(item.id); } }} onPointerDown={event => startDrag(event, item.id)}><span>{ELEMENT_LABELS[item.kind]}</span></div>)}
              {selected && <div className="face-selection" style={{ left: selected.x, top: selected.y, width: selected.width, height: selected.height }}>
                {editing && <button type="button" className="face-resize-handle" aria-label="拖动缩放选中元素" onPointerDown={event => startDrag(event, selected.id, 'resize')} />}
                {editing && shape?.kind === 'polygon' && shape.points.map((point, index) => <button type="button" key={index} className="face-vertex" aria-label={`顶点 ${index + 1}`} style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }} onPointerDown={event => startDrag(event, shape.id, 'vertex', index)} />)}
              </div>}
              {guides.map((guide, index) => <div key={index} className={`face-snap-guide is-${guide.axis}`} style={guide.axis === 'x' ? { left: guide.position } : { top: guide.position }} />)}
            </div>}</FaceArtwork>
          </div></div>
          <span className="face-workspace-caption">{editing ? '拖动排版 · 方向键微调 · Alt 暂停吸附' : section==='stock'?'': '移动指针查看材质'}</span>
        </div>
        <div className="face-canvas-status"><span><Check size={13} />{surface.width} × {surface.height}<i>·</i>{surface.elements.length} 个内容图层</span><small>{advanced ? `${surface.width} × ${surface.height} · ` : ''}{Math.round(scale * 100)}%</small></div>
      </main>
      {inspect && <aside className="face-properties"><div className="face-inspector-heading"><span>{selected ? layerName(selected) : '图层与检查器'}</span><button type="button" aria-label="关闭内容检查器" onClick={() => setSelection('')}><X size={14} /></button></div>
        <div className="face-panel-scroll">
          {element && <>{slotEditor(element)}
            {element.kind === 'button' && <><label>按钮功能<select aria-label="按钮功能" value={buttonAction(element)}
              onChange={event => updateItem({ button: { ...element.button, action: event.target.value as FaceButtonAction } })}>
              {BUTTON_ACTIONS.map(item => <option key={item.action} value={item.action}>{item.label}</option>)}</select></label>
              <label>按钮底色<input type="color" value={element.button?.background ?? tokens.surface} onChange={event => updateItem({ button: { action: buttonAction(element)!, ...element.button, background: event.target.value } })} /></label>
              <NumberControl label="按钮圆角" value={element.button?.radius ?? 8} max={1024} onChange={radius => updateItem({ button: { action: buttonAction(element)!, ...element.button, radius } })} />
              {buttonAction(element) === 'custom' && <p className="face-note">连接卡面设计器到功能设计器后可查看此按钮。逻辑挂载将在后续开放。</p>}</>}
            <Choices label="文字对齐" value={element.align} options={[["left", "左"], ["center", "中"], ["right", "右"]]} onChange={align => updateItem({ align, overrides: { ...element.overrides, align } }, true)} />
            {contrast(element.color, tokens.background) < 4.5 && element.kind !== 'icon' && <p className="face-contrast" role="status">文字对比度偏低，可点击「润色」调整。</p>}
            <button type="button" className="face-convert" onClick={() => { updateItem({ placement: element.placement === 'free' ? 'slot' : 'free' }, element.placement === 'free'); }}>
              <Layers size={14} />{element.placement === 'free' ? '加入预设排版' : '转为自由图层'}</button>
          </>}
          {selected && <><div className="face-divider" /><h4>{element?.placement === 'free' || shape ? '自由定位' : '槽位位置'}</h4>
            <div className="factory-row"><NumberControl label="位置 X" min={-2048} value={selected.x} onChange={x => updateItem({ x, ...(element ? { placement: 'free' as const } : {}) })} /><NumberControl label="位置 Y" min={-2048} value={selected.y} onChange={y => updateItem({ y, ...(element ? { placement: 'free' as const } : {}) })} /></div>
            <div className="factory-row"><NumberControl label="元素宽度" value={selected.width} min={8} onChange={width => updateItem({ width, ...(element ? { placement: 'free' as const } : {}) })} /><NumberControl label="元素高度" value={selected.height} min={8} onChange={height => updateItem({ height, ...(element ? { placement: 'free' as const } : {}) })} /></div>
            {element && <><label>宽度约束<select value={element.sizing ?? 'fill'} onChange={e => updateItem({ sizing: e.target.value as FaceElement['sizing'] }, true)}><option value="fill">Fill · 填充</option><option value="hug">Hug · 适应内容</option><option value="fixed">Fixed · 固定</option></select></label>
              <label>停靠方向<select value={element.pin ?? 'start'} onChange={e => updateItem({ pin: e.target.value as FaceElement['pin'] }, true)}><option value="start">起点</option><option value="center">居中</option><option value="end">末端</option></select></label>
              <label>文字颜色<input type="color" value={element.color} onChange={e => updateItem({ color: e.target.value, overrides: { ...element.overrides, color: e.target.value } })} /></label>
              <NumberControl label="字号" value={element.font_size} min={8} max={128} onChange={font_size => updateItem({ font_size, overrides: { ...element.overrides, font_size } }, true)} /></>}
            {shape && <><label>填充颜色<input type="color" value={shape.fill} onChange={e => updateItem({ fill: e.target.value })} /></label>{shape.kind === 'rect' && <NumberControl label="圆角" value={shape.radius} max={1024} onChange={radius => updateItem({ radius })} />}
              {shape.kind === 'polygon' && <div className="face-add-shapes"><button type="button" disabled={shape.points.length >= 24} onClick={() => { const [a, b] = shape.points; updateItem({ points: [a, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, ...shape.points.slice(1)] }); }}>添加顶点</button>
                <button type="button" disabled={shape.points.length <= 3} onClick={() => updateItem({ points: shape.points.slice(0, -1) })}>移除末尾顶点</button></div>}</>}
            <button type="button" onClick={() => updateSurface({ ...surface, [layerMode]: [...surface[layerMode].filter(item => item.id !== selection), selected] })}>置顶</button>
          </>}
          {selected && <><label className="face-slider">油墨浓度<input aria-label="油墨浓度" type="range" min="0" max="100" value={Math.round((selected.print?.opacity??1)*100)} onChange={event=>updateItem({print:{blend:selected.print?.blend??'normal',opacity:Number(event.target.value)/100}})} /></label>
          </>}
          {selected && <button type="button" className="face-remove" disabled={layerMode === 'shapes' && surface.shapes.length === 1} onClick={() => remove()}>删除{advanced ? '' : '此内容'}</button>}
          {advanced && <><div className="face-divider" /><h4>{layerMode === 'shapes' ? '形状图层' : '内容图层'}</h4><div className="face-layer-list">{[...surface[layerMode]].reverse().map(item => <button type="button" key={item.id} aria-pressed={selection === item.id} onClick={() => setSelection(item.id)}><span>{layerName(item)}</span><small>{'placement' in item && item.placement === 'free' ? '自由' : 'fill' in item ? '' : '槽位'}</small></button>)}</div>
            {layerMode === 'shapes' ? <div className="face-add-shapes">{(['rect', 'ellipse', 'polygon'] as const).map(kind => <button type="button" key={kind} disabled={surface.shapes.length >= 24} onClick={() => addShape(kind)}>＋{shapeLabels[kind]}</button>)}</div>
              : <label>添加图层<select value="" disabled={surface.elements.length >= 32} onChange={e => addSlot(e.target.value as FaceElement['kind'])}><option value="" disabled>选择内容类型…</option>{Object.entries(ELEMENT_LABELS).filter(([kind]) => ['text', 'button'].includes(kind) || !surface.elements.some(item => item.kind === kind)).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select></label>}
          </>}
        </div>
      </aside>}
    </div>
    <footer className="face-studio-footer"><button type="button" aria-label="上一步" disabled={stepIndex===0} onClick={()=>navigateStep(-1)}><ArrowLeft size={14}/>上一步</button>
      <span role="status">{notice || `${String(stepIndex+1).padStart(2,'0')} / ${String(STEPS.length).padStart(2,'0')}`}</span>
      {stepIndex<STEPS.length-1?<button type="button" className="face-step-next" aria-label="下一步" onClick={()=>navigateStep(1)}>{section === 'stock' ? '开始加工' : '下一步'}<ArrowRight size={14}/></button>:<button type="button" className="face-step-next" onClick={()=>changeSection('process')}>继续设计<ArrowLeft size={14}/></button>}
    </footer>
    {error && <p role="alert" className="factory-error">{error}</p>}
  </div>;
}
