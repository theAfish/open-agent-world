import { useEffect, useRef, useState, type PointerEvent, type ReactNode } from 'react';
import { ArrowLeft, Check, Eye, Layers, LayoutTemplate, Redo2, SlidersHorizontal, Sparkles, Type, Undo2, WandSparkles, X } from 'lucide-react';
import type { NodeSurfaceLevel } from '../types/world';
import { FaceArtwork, ShapePath } from './FaceArtwork';
import { ELEMENT_LABELS, FACE_MODES, MODE_LABELS, faceStudio, presetSurface, resizeDesign, snapBox } from './faceDesign';
import { RECIPES, STYLE_KITS, chooseRecipe, contrast, designTokens, newSlot, polishSurface, recipeSettings, reflowSurface } from './designRecipes';
import { DesignerPanel, SlotEditor, Choices, NumberControl, type DesignerSection } from './DesignerControls';
import type { FaceBox, FaceDesign, FaceElement, FaceShape, FaceStudio, SurfaceDesign, SurfaceRecipe } from './types';
import './faceDesigner.css';

type Drag = { pointer: number; x: number; y: number; id: string; kind: 'move' | 'resize' | 'vertex'; vertex?: number; surface: SurfaceDesign; before: FaceDesign; scale: number; changed: boolean };
const shapeLabels = { rect: '圆角矩形', ellipse: '椭圆', polygon: '多边形' };
const clamp = (n: number, min: number, max: number) => Math.max(min, Math.min(max, n));

export function FaceDesignerCanvas({ face, onChange, saveControl }: { face: FaceDesign; onChange: (patch: Partial<FaceDesign>) => void; saveControl?: ReactNode }) {
  const [mode, setMode] = useState<NodeSurfaceLevel>('preview'), [section, setSection] = useState<DesignerSection>('card');
  const [layerMode, setLayerMode] = useState<'elements' | 'shapes'>('elements'), [selection, setSelection] = useState('');
  const [snapping, setSnapping] = useState(true), [preview, setPreview] = useState(false), [checker, setChecker] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [historyVersion, setHistoryVersion] = useState(0);
  const [guides, setGuides] = useState<{ axis: 'x' | 'y'; position: number }[]>([]), [wellSize, setWellSize] = useState({ width: 640, height: 570 });
  const history = useRef<{ past: FaceDesign[]; future: FaceDesign[] }>({ past: [], future: [] });
  const stage = useRef<HTMLDivElement>(null), well = useRef<HTMLDivElement>(null), drag = useRef<Drag>();
  const latestFace = useRef(face); latestFace.current = face;
  const studio = faceStudio(face), surface = studio.modes[mode] ?? presetSurface(mode, face), settings = recipeSettings(face, surface);
  const tokens = designTokens(surface, settings), advanced = section === 'advanced';
  const selected = surface[layerMode].find(item => item.id === selection);
  const shape = layerMode === 'shapes' ? selected as FaceShape | undefined : undefined;
  const element = layerMode === 'elements' ? selected as FaceElement | undefined : undefined;
  const inspect = !preview && (advanced || Boolean(selected));
  const scale = Math.min(mode === 'node' ? 2 : 4, Math.max(100, wellSize.width - 72) / surface.width, Math.max(100, wellSize.height - 82) / surface.height);
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
      const current = recipeSettings(face, value), next = { ...current, ...patch, tokens: { ...(patch.tokens ?? current.tokens) } };
      // Intent controls supersede exact overrides for that same intent.
      if (patch.appearance) for (const key of ['background', 'surface', 'text', 'muted', 'border'] as const) delete next.tokens[key];
      if (patch.softness !== undefined) delete next.tokens.radius;
      if (patch.density) { delete next.tokens.margin; delete next.tokens.gap; }
      if (patch.emphasis) delete next.tokens.title_size;
      return reflowSurface(value, face, next);
    };
    if (advanced) updateSurface(transform(surface)); else updateSurfaces(transform);
  };
  const updateFace = (patch: Partial<FaceDesign>) => {
    const next = { ...face, ...patch };
    commit({ ...patch, studio: { ...studio, modes: Object.fromEntries(Object.entries(studio.modes).map(([key, value]) => [key, value.design ? reflowSurface(value, next) : value])) } });
  };
  const updateItem = (patch: Partial<Omit<FaceElement, 'kind'> & Omit<FaceShape, 'kind'>>, reflow = false) => {
    const next = { ...surface, [layerMode]: surface[layerMode].map(item => item.id === selection ? { ...item, ...patch } : item) };
    if (layerMode === 'shapes' && selection === surface.shapes[0].id && next.design) next.design = { ...next.design, tokens: { ...next.design.tokens,
      ...(patch.fill ? { background: patch.fill } : {}), ...(patch.radius !== undefined ? { radius: patch.radius } : {}) } };
    updateSurface(reflow && surface.design ? reflowSurface(next, face) : next);
  };
  const remove = (id = selection) => {
    if (layerMode === 'shapes' && surface.shapes.length === 1) return;
    const next = { ...surface, [layerMode]: surface[layerMode].filter(item => item.id !== id) };
    updateSurface(surface.design && layerMode === 'elements' ? reflowSurface(next, face) : next); setSelection('');
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
  const changeSection = (value: DesignerSection) => { setSection(value); setPreview(false); if (value !== 'advanced') { setLayerMode('elements'); setSelection(''); } };
  const startDrag = (event: PointerEvent, id: string, kind: Drag['kind'] = 'move', vertex?: number) => {
    if (event.button !== 0 || !stage.current) return;
    event.stopPropagation(); event.preventDefault(); setSelection(id);
    stage.current.focus({ preventScroll: true });
    const item = surface[layerMode].find(value => value.id === id) as FaceElement | undefined;
    if (!advanced && !event.altKey && item?.placement !== 'free') return;
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
  const addShape = (kind: FaceShape['kind']) => {
    const id = `shape-${crypto.randomUUID()}`, width = Math.min(160, surface.width - 32), height = Math.min(160, surface.height - 32);
    updateSurface({ ...surface, shapes: [...surface.shapes, { id, kind, x: 16, y: 16, width, height, radius: 20, fill: face.color,
      points: kind === 'polygon' ? [{ x: .5, y: 0 }, { x: 1, y: .35 }, { x: .8, y: 1 }, { x: .2, y: 1 }, { x: 0, y: .35 }] : [] }] });
    setSelection(id); setLayerMode('shapes');
  };
  const addSlot = (kind: FaceElement['kind']) => {
    const added = newSlot(kind); if (kind === 'text') { added.id = `text-${crypto.randomUUID()}`; added.text = '自由文本'; added.placement = 'free'; added.x = 24; added.y = 24; }
    updateSurface(reflowSurface({ ...surface, elements: [...surface.elements, added] }, face)); setLayerMode('elements'); setSelection(added.id);
  };
  const upload = async (file?: File, elementId?: string) => {
    if (!file) return; setError(''); const targetMode = mode;
    try {
      if (file.type !== 'image/png' || file.size > 1024 * 1024) throw new Error('请选择不超过 1 MiB 的 PNG 图片。');
      const png = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsDataURL(file); });
      const img = new Image(); img.src = png; await img.decode();
      if (img.width > 2048 || img.height > 2048) throw new Error('图片尺寸不能超过 2048 × 2048。');
      const current = latestFace.current, currentStudio = faceStudio(current), target = currentStudio.modes[targetMode] ?? presetSurface(targetMode, current);
      const next = elementId ? { ...target, elements: target.elements.map(e => e.id === elementId ? { ...e, image_png: png } : e) } : { ...target, background_png: png, image_shape: true };
      record(current); onChange({ studio: { ...currentStudio, modes: { ...currentStudio.modes, [targetMode]: next } } });
    } catch (cause) { setError(cause instanceof Error ? cause.message : '无法读取 PNG 图片。'); }
  };
  const editText = (item: FaceElement, value: string) => {
    const key = ({ title: 'title', description: 'description', help: 'help_text', action: 'button_label' } as Partial<Record<FaceElement['kind'], keyof FaceDesign>>)[item.kind];
    if (key) updateFace({ [key]: value });
    else { const next = { ...surface, elements: surface.elements.map(e => e.id === item.id ? { ...e, text: value } : e) }; updateSurface(surface.design ? reflowSurface(next, face) : next); }
  };
  const applyPolish = () => {
    const details = new Set<string>();
    const transform = (value: SurfaceDesign) => { const result = polishSurface(value, face); result.details.forEach(detail => details.add(detail)); return result.surface; };
    if (advanced) updateSurface(transform(surface)); else updateSurfaces(transform);
    setNotice(details.size ? `已调整${advanced ? '当前视图' : '四种视图'}的${details.size}项细节：${[...details].join('、')}。可撤销。` : '间距、层级与对比度已协调，无需调整。');
  };
  const layerName = (item: FaceShape | FaceElement) => 'fill' in item ? shapeLabels[item.kind] : ELEMENT_LABELS[item.kind];
  const slotEditor = (item: FaceElement) => <SlotEditor item={item} face={face} editText={editText} updateFace={updateFace} upload={upload} />;
  return <div className="face-studio" data-history={historyVersion} data-advanced={advanced} onKeyDown={event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !(event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement)) { event.preventDefault(); event.stopPropagation(); undo(event.shiftKey); }
  }}>
    <header className="face-studio-header"><span className="face-toolbar-context">{advanced ? '当前视图 · 精细编辑' : '版式 / 风格 / 内容 / 材质'}</span>
      <div className="face-toolbar"><div className="face-history"><button type="button" aria-label="撤销" title="撤销 · Ctrl Z" disabled={!history.current.past.length} onClick={() => undo()}><Undo2 size={16} /></button>
        <button type="button" aria-label="重做" title="重做 · Ctrl Shift Z" disabled={!history.current.future.length} onClick={() => undo(true)}><Redo2 size={16} /></button></div>
        <button type="button" className="face-polish" onClick={applyPolish}><WandSparkles size={15} />润色</button>
        <button type="button" aria-pressed={preview} onClick={() => setPreview(value => !value)}><Eye size={15} />{preview ? '继续编辑' : '预览'}</button>{saveControl}</div>
    </header>
    <div className="face-studio-body" data-inspect={inspect} data-preview={preview}>
      {!preview && <aside className="face-sidebar"><nav className="face-section-tabs" aria-label="设计步骤">
        {([{ id: 'card', label: '卡片', icon: LayoutTemplate }, { id: 'content', label: '内容', icon: Type }, { id: 'material', label: '材质', icon: Sparkles }, { id: 'advanced', label: '高级', icon: SlidersHorizontal }] as const).map(tab =>
          <button type="button" key={tab.id} aria-pressed={section === tab.id} onClick={() => changeSection(tab.id)}><tab.icon size={17} />{tab.label}</button>)}
      </nav><DesignerPanel {...{ face, surface, settings, studio, mode, section, layerMode, snapping, checker, updateSurface, updateSettings, updateFace, updateStudio, enableMode, addSlot, remove, upload, editText }}
        setMode={value => { setMode(value); setSelection(''); }} setSection={changeSection} setLayerMode={value => { setLayerMode(value); setSelection(''); }} setSnapping={setSnapping} setChecker={setChecker}
        select={id => { setLayerMode('elements'); setSelection(id); }} showPreview={() => { setPreview(true); setSelection(''); }}
        resize={(width, height) => { const next = resizeDesign(surface, width, height); updateSurface(next.design ? reflowSurface(next, face) : next); }}
        applyRecipe={recipe => updateSurfaces(value => chooseRecipe(value, face, recipe))}
        applyKit={(kit, appearance, accent) => updateSurfaces(value => reflowSurface(value, { ...face, color: accent }, { ...recipeSettings(face, value), kit, appearance, tokens: {} }), { color: accent })} />
      </aside>}
      <main className="face-studio-center"><div className="face-canvas-heading"><div><span className="face-live-dot" />{preview ? '成品预览' : '实时预览'}<small>{RECIPES.find(r => r.id === settings.recipe)?.name} / {STYLE_KITS[settings.kit].name}</small></div>
        <select aria-label="预览显示模式" value={mode} onChange={e => { setMode(e.target.value as NodeSurfaceLevel); setSelection(''); }}>{FACE_MODES.map(value => <option key={value} value={value}>{MODE_LABELS[value]}</option>)}</select></div>
        <div ref={well} className="face-canvas-well" data-checker={checker && advanced} onPointerDown={e => { if (e.target === e.currentTarget) setSelection(''); }}>
          <div className="face-stage-size" style={{ width: surface.width * scale, height: surface.height * scale }}><div ref={stage} className="face-edit-stage" tabIndex={0} role="group" aria-label={`${MODE_LABELS[mode]}模式设计画布`}
            style={{ width: surface.width, height: surface.height, transform: `scale(${scale})` }} data-mode={mode}
            onPointerMove={move} onPointerUp={() => finishDrag()} onPointerCancel={() => finishDrag(true)} onLostPointerCapture={() => finishDrag()}
            onKeyDown={event => {
              if (event.key === 'Escape') { event.stopPropagation(); if (drag.current) finishDrag(true); else setSelection(''); return; }
              if (preview) return;
              if (event.key === 'Delete' || event.key === 'Backspace') { if (selected) { event.preventDefault(); event.stopPropagation(); remove(); } return; }
              if (selected && (advanced || event.altKey || element?.placement === 'free') && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
                event.preventDefault(); event.stopPropagation(); const step = event.shiftKey ? 8 : 1;
                updateItem({ ...(element ? { placement: 'free' as const } : {}), x: clamp(selected.x + (event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0), 0, Math.max(0, surface.width - selected.width)),
                  y: clamp(selected.y + (event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0), 0, Math.max(0, surface.height - selected.height)) });
              }
            }}>
            <FaceArtwork face={face} surface={surface} sample interactive>
            {!preview && <div className="face-edit-overlay" onPointerDown={() => setSelection('')}>
              {advanced && layerMode === 'shapes' ? <svg className="face-shape-hits" viewBox={`0 0 ${surface.width} ${surface.height}`}>{surface.shapes.map(item => <g key={item.id} className={selection === item.id ? 'is-selected' : ''} aria-label={`选择${shapeLabels[item.kind]}`} role="button" tabIndex={0}
                onFocus={() => setSelection(item.id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelection(item.id); } }} onPointerDown={event => startDrag(event, item.id)}><ShapePath shape={item} /></g>)}</svg>
                : surface.elements.map(item => <div key={item.id} role="button" tabIndex={0} aria-label={`设计${ELEMENT_LABELS[item.kind]}`} data-edit-element={item.id} data-free={advanced || item.placement === 'free'}
                  className={`face-element-hit ${selection === item.id ? 'is-selected' : ''}`} style={{ left: item.x, top: item.y, width: item.width, height: item.height }}
                  onFocus={() => { setLayerMode('elements'); setSelection(item.id); }} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelection(item.id); } }} onPointerDown={event => startDrag(event, item.id)}><span>{ELEMENT_LABELS[item.kind]}</span></div>)}
              {selected && <div className="face-selection" style={{ left: selected.x, top: selected.y, width: selected.width, height: selected.height }}>
                {(advanced || element?.placement === 'free') && <button type="button" className="face-resize-handle" aria-label="拖动缩放选中元素" onPointerDown={event => startDrag(event, selected.id, 'resize')} />}
                {advanced && shape?.kind === 'polygon' && shape.points.map((point, index) => <button type="button" key={index} className="face-vertex" aria-label={`顶点 ${index + 1}`} style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }} onPointerDown={event => startDrag(event, shape.id, 'vertex', index)} />)}
              </div>}
              {guides.map((guide, index) => <div key={index} className={`face-snap-guide is-${guide.axis}`} style={guide.axis === 'x' ? { left: guide.position } : { top: guide.position }} />)}
            </div>}</FaceArtwork>
          </div></div>
          <span className="face-workspace-caption">{preview ? '' : advanced ? '拖动自由编辑 · Alt 暂停吸附' : '点选内容即可编辑 · 布局自动协调'}</span>
        </div>
        <div className="face-canvas-status"><span><Check size={13} />{surface.design ? '自动布局' : '保留原有布局'}<i>·</i>{surface.elements.filter(e => e.placement === 'free').length > 0 ? '含自由图层' : '安全边距'}</span><small>{advanced ? `${surface.width} × ${surface.height} · ` : ''}{Math.round(scale * 100)}%</small></div>
        {preview && <button type="button" className="face-back" onClick={() => setPreview(false)}><ArrowLeft size={14} />返回设计</button>}
      </main>
      {inspect && <aside className="face-properties"><div className="face-inspector-heading"><span>{selected ? layerName(selected) : '图层与检查器'}</span>{!advanced && <button type="button" aria-label="关闭内容检查器" onClick={() => setSelection('')}><X size={14} /></button>}</div>
        <div className="face-panel-scroll">
          {element && <>{slotEditor(element)}
            <Choices label="文字对齐" value={element.align} options={[["left", "左"], ["center", "中"], ["right", "右"]]} onChange={align => updateItem({ align, overrides: { ...element.overrides, align } }, true)} />
            {contrast(element.color, tokens.background) < 4.5 && element.kind !== 'icon' && <p className="face-contrast" role="status">文字对比度偏低，可点击「润色」调整。</p>}
            <button type="button" className="face-convert" onClick={() => { updateItem({ placement: element.placement === 'free' ? 'slot' : 'free' }, element.placement === 'free'); setSection('advanced'); }}>
              <Layers size={14} />{element.placement === 'free' ? '恢复自动布局' : '转为自由图层'}</button>
          </>}
          {advanced && selected && <><div className="face-divider" /><h4>{element?.placement === 'free' || shape ? '自由定位' : '槽位位置'}</h4>
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
          {selected && <button type="button" className="face-remove" disabled={layerMode === 'shapes' && surface.shapes.length === 1} onClick={() => remove()}>删除{advanced ? '' : '此内容'}</button>}
          {advanced && <><div className="face-divider" /><h4>{layerMode === 'shapes' ? '形状图层' : '内容图层'}</h4><div className="face-layer-list">{[...surface[layerMode]].reverse().map(item => <button type="button" key={item.id} aria-pressed={selection === item.id} onClick={() => setSelection(item.id)}><span>{layerName(item)}</span><small>{'placement' in item && item.placement === 'free' ? '自由' : 'fill' in item ? '' : '槽位'}</small></button>)}</div>
            {layerMode === 'shapes' ? <div className="face-add-shapes">{(['rect', 'ellipse', 'polygon'] as const).map(kind => <button type="button" key={kind} disabled={surface.shapes.length >= 24} onClick={() => addShape(kind)}>＋{shapeLabels[kind]}</button>)}</div>
              : <label>添加图层<select value="" disabled={surface.elements.length >= 32} onChange={e => addSlot(e.target.value as FaceElement['kind'])}><option value="" disabled>选择内容类型…</option>{Object.entries(ELEMENT_LABELS).filter(([kind]) => kind === 'text' || !surface.elements.some(item => item.kind === kind)).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select></label>}
          </>}
        </div>
      </aside>}
    </div>
    <footer className="face-studio-footer"><span role="status">{notice || (advanced ? '高级设置仅作用于当前视图。' : '版式、风格与材质同步所有视图；内容按视图编排。')}</span></footer>
    {error && <p role="alert" className="factory-error">{error}</p>}
  </div>;
}
