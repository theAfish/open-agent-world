import { useState, type ReactNode, type CSSProperties } from 'react';
import { ArrowLeft, ArrowRight, Copy, Eye, EyeOff, Plus, Trash2 } from 'lucide-react';
import { MAX_PRODUCTION_LAYERS, newDesignLayer, newProductionLayer, printingLayers,
  type CardProduction, type ProductionLayer, type ProductionLayerKind, type ProductionMask } from '../cards/cardProduction';
import { ProductionSlider } from './ProductionControls';
import './processLayerControls.css';

export const PROCESS_KINDS: readonly [ProductionLayerKind, string][] = [['ink', '油墨'], ['laminate', '覆膜'], ['foil', '烫金'], ['emboss', '压印'], ['uv', 'UV']];
export const PROCESS_NAMES = Object.fromEntries(PROCESS_KINDS) as Record<ProductionLayerKind, string>;
const FILMS: readonly [ProductionLayer['film'], string][] = [['gloss', '透明亮膜'], ['holo', '全息'], ['aurora', '极光'], ['laser', '镭射'], ['starlight', '星光']];
const recipeLayer = (kind: ProductionLayerKind, patch: Partial<ProductionLayer>, mask: Partial<ProductionMask> = {}) => {
  const layer = newProductionLayer(kind);
  return { ...layer, ...patch, mask: { ...layer.mask, ...mask } };
};
export const PROCESS_RECIPES = [
  { name: '哑膜金字', hint: '柔雾覆膜 → 文字烫金 → 文字 UV', create: () => [recipeLayer('laminate', { film: 'gloss', roughness: .94, strength: .4 }, { source: 'all' }), recipeLayer('foil', {}, { source: 'text' }), recipeLayer('uv', {}, { source: 'text' })] },
  { name: '镭射压纹', hint: '镭射覆膜 → 斜纹压印 → 边框 UV', create: () => [recipeLayer('laminate', { film: 'laser' }, { source: 'all' }), recipeLayer('emboss', {}, { source: 'preset', preset: 'diagonal' }), recipeLayer('uv', {}, { source: 'preset', preset: 'border' })] },
  { name: '金边浮雕', hint: '四角压印 → 边框烫金 → 文字 UV', create: () => [recipeLayer('emboss', {}, { source: 'preset', preset: 'corners' }), recipeLayer('foil', {}, { source: 'preset', preset: 'border' }), recipeLayer('uv', {}, { source: 'text' })] },
];


export function LayerSwatch({ layer }: { layer: ProductionLayer }) {
  return <span className="process-layer-swatch" data-kind={layer.kind} data-film={layer.film}
    style={{ '--process-color': layer.color } as CSSProperties} aria-hidden="true" />;
}
type LayerProps = { production: CardProduction; selectedId?: string; onSelect: (id: string) => void; onChange: (value: CardProduction) => void };
export function replaceProcessLayers(production: CardProduction, layers: ProductionLayer[]): CardProduction {
  return { ...production, print: { ...production.print, layered: true }, layers };
}
export function ProcessLayerRail({ production, selectedId, onSelect, onChange }: LayerProps) {
  const layers = printingLayers(production);
  const [dragging, setDragging] = useState<string | null>(null);
  return <>
    <ol className="process-layer-list" aria-label="工艺层顺序">{layers.map((layer, index) => <li key={layer.id} data-layer-id={layer.id} data-enabled={layer.enabled}
      onDragOver={event => { if (dragging) event.preventDefault(); }} onDrop={event => {
        event.preventDefault(); const from = layers.findIndex(item => item.id === dragging);
        if (from >= 0 && from !== index) { const next = [...layers]; next.splice(index, 0, ...next.splice(from, 1)); onChange(replaceProcessLayers(production, next)); }
        setDragging(null);
      }}>
      <button type="button" draggable className="process-layer-select" aria-label={`选择${PROCESS_NAMES[layer.kind]}第 ${index + 1} 层`}
        aria-pressed={selectedId === layer.id} aria-current={selectedId === layer.id ? 'step' : undefined}
        onDragStart={event => { setDragging(layer.id); event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', layer.id); }} onDragEnd={() => setDragging(null)}
        onClick={() => onSelect(layer.id)}><LayerSwatch layer={layer} /><span className="process-chip-label"><small>{String(index + 1).padStart(2, '0')}</small><b>{PROCESS_NAMES[layer.kind]}</b></span>{!layer.enabled && <EyeOff size={12} />}</button>
    </li>)}</ol>
    <details className="process-add-menu" onKeyDown={event => { event.stopPropagation(); if (event.key === 'Escape') event.currentTarget.open = false; }}>
      <summary aria-label="添加工艺层"><Plus size={15} /><span>添加层</span></summary>
      <div role="group" aria-label="添加工艺层">{PROCESS_KINDS.map(([kind, label]) => <button type="button" key={kind} disabled={layers.length >= MAX_PRODUCTION_LAYERS}
        aria-label={`添加${kind === 'uv' ? ' ' : ''}${label}`} onClick={event => {
          const layer = newDesignLayer(kind); onChange(replaceProcessLayers(production, [...layers, layer])); onSelect(layer.id);
          const menu = event.currentTarget.closest('details'); if (menu) menu.open = false;
        }}><LayerSwatch layer={newDesignLayer(kind)} />{label}</button>)}<small>{layers.length} / {MAX_PRODUCTION_LAYERS} 层 · 从左到右加工</small></div>
    </details>
  </>;
}

export function ProcessLayerControls({ production, selectedId, onSelect, onChange, renderContent, onDuplicate }: LayerProps & { renderContent?: (layer: ProductionLayer) => ReactNode; onDuplicate?: (id: string) => void }) {
  const layers = printingLayers(production), selected = layers.find(layer => layer.id === selectedId) ?? layers.at(-1);
  if (!selected) return <p className="face-note">从顶部添加一道工艺，然后在这里设置并添加元素。</p>;
  const index = layers.indexOf(selected), name = PROCESS_NAMES[selected.kind];
  const commit = (next: ProductionLayer[]) => onChange(replaceProcessLayers(production, next));
  const update = (patch: Partial<ProductionLayer>) => commit(layers.map(layer => layer.id === selected.id ? { ...layer, ...patch } : layer));
  const move = (offset: number) => { const next = [...layers]; next.splice(index + offset, 0, ...next.splice(index, 1)); commit(next); };
  return <div className="process-controls"><section className="process-inspector" aria-label="当前工艺层">
    <div className="process-inspector-heading"><div><small>第 {index + 1} 层</small><h3>{name}{selected.kind === 'ink' ? '印刷' : ''}</h3></div><LayerSwatch layer={selected} /></div>
    <div className="process-layer-actions" role="group" aria-label="当前层操作">
      <button type="button" title="提前加工" aria-label={`提前${name}第 ${index + 1} 层`} disabled={index === 0} onClick={() => move(-1)}><ArrowLeft size={14} /></button>
      <button type="button" title="延后加工" aria-label={`延后${name}第 ${index + 1} 层`} disabled={index === layers.length - 1} onClick={() => move(1)}><ArrowRight size={14} /></button>
      <button type="button" title={selected.enabled ? '隐藏此层' : '显示此层'} aria-label={`${selected.enabled ? '隐藏' : '显示'}${name}第 ${index + 1} 层`} onClick={() => update({ enabled: !selected.enabled })}>{selected.enabled ? <Eye size={14} /> : <EyeOff size={14} />}</button>
      <button type="button" title="复制此层" aria-label="复制当前工艺层" disabled={layers.length >= MAX_PRODUCTION_LAYERS} onClick={() => {
        if (onDuplicate) { onDuplicate(selected.id); return; }
        const copy = { ...structuredClone(selected), id: newProductionLayer(selected.kind).id };
        const next = [...layers]; next.splice(index + 1, 0, copy); commit(next); onSelect(copy.id);
      }}><Copy size={14} /></button>
      <button type="button" title="删除此层" aria-label="删除当前工艺层" onClick={() => { const next = layers.filter(layer => layer.id !== selected.id); commit(next); onSelect(next[Math.min(index, next.length - 1)]?.id ?? ''); }}><Trash2 size={14} /></button>
    </div>
    {selected.kind === 'laminate' && <label>覆膜类型<select aria-label="覆膜类型" value={selected.film} onChange={event => update({ film: event.target.value as ProductionLayer['film'] })}>{FILMS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>}
    {selected.kind === 'emboss' && <div className="process-relief" role="group" aria-label="压印方向">{([['raised', '凸起'], ['recessed', '凹入']] as const).map(([value, label]) => <button type="button" key={value} aria-pressed={selected.relief === value} onClick={() => update({ relief: value })}>{label}</button>)}</div>}
    {(selected.kind === 'foil' || selected.kind === 'ink') && <label className="process-color">{selected.kind === 'foil' ? '金属颜色' : '油墨颜色'}<span><input aria-label={selected.kind === 'foil' ? '金属颜色' : '油墨颜色'} type="color" value={selected.color} onChange={event => update({ color: event.target.value })} /><output>{selected.color.toUpperCase()}</output></span></label>}
    <ProductionSlider label="工艺强度" value={selected.strength} onChange={strength => update({ strength })} />
    {selected.kind !== 'ink' && <ProductionSlider label="表面粗糙度" value={selected.roughness} min={.06} ends={['镜面', '柔雾']} onChange={roughness => update({ roughness })} />}
  </section>{renderContent?.(selected)}</div>;
}
