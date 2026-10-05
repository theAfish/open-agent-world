import { Check, ChevronDown, Circle, Image, Layers, MousePointerClick, Plus, Shapes, Square, Triangle, Type, Upload } from 'lucide-react';
import { printContentIds, printingLayers, type CardProduction, type ProductionLayer } from '../cards/cardProduction';
import { CardPrintArt } from '../components/CardPrintArt';
import { ELEMENT_LABELS } from './faceDesign';
import { SEMANTIC_SLOTS } from './designRecipes';
import { ProductionSlider } from './ProductionControls';
import { replaceProcessLayers } from './ProcessLayerControls';
import type { FaceButtonAction, FaceElement, FaceShape, SurfaceDesign } from './types';
import { BUTTON_ACTIONS } from './faceButtons';

export function LayerElementsPanel({ layer, production, surface, selection, onChange, addSlot, addShape, upload, select }: {
  layer: ProductionLayer; production: CardProduction; surface: SurfaceDesign; selection: string;
  onChange: (value: CardProduction) => void;
  addSlot: (kind: FaceElement['kind'], layerId?: string, action?: FaceButtonAction) => void;
  addShape: (kind: FaceShape['kind'], layerId?: string, full?: boolean) => void;
  upload: (file?: File, elementId?: string, layerId?: string) => Promise<void>;
  select: (id: string, kind: 'elements' | 'shapes') => void;
}) {
  const layers = printingLayers(production);
  const targets = [...surface.elements.map(item => ({ id: item.id, kind: 'elements' as const, name: ELEMENT_LABELS[item.kind] })),
    ...surface.shapes.slice(1).map(item => ({ id: item.id, kind: 'shapes' as const, name: ({ rect: '矩形', ellipse: '圆形', polygon: '多边形' })[item.kind] }))];
  const ids = printContentIds(layer, layers, targets.map(item => item.id));
  const pattern = layer.pattern ?? (layer.content?.source === 'all' ? production.print : { motif: 'none' as const, density: .38 });
  const updatePattern = (patch: Partial<NonNullable<ProductionLayer['pattern']>>) => onChange(replaceProcessLayers(production,
    layers.map(item => item.id === layer.id ? { ...item, pattern: { ...pattern, ...patch } } : item)));
  const slot = (kind: FaceElement['kind'], label: string, hint?: string) => {
    const exists = kind !== 'text' && surface.elements.some(item => item.kind === kind && ids.includes(item.id));
    return <button type="button" key={kind} className="process-element-option" data-added={exists} disabled={exists || surface.elements.length >= 32}
      aria-label={label} onClick={() => addSlot(kind, layer.id)}><span><b>{label}</b>{hint && <small>{hint}</small>}</span>{exists ? <Check size={14} /> : <Plus size={14} />}</button>;
  };
  return <section className="process-elements-panel" aria-label="添加元素">
    <h4>添加元素</h4>
    <details className="process-element-panel"><summary><Type size={16} /><span>文本<small>标题、说明与自由文本</small></span><ChevronDown size={14} /></summary>
      <div className="process-element-options">{SEMANTIC_SLOTS.filter(item => !['icon', 'illustration', 'badge', 'action'].includes(item.kind)).map(item => slot(item.kind, item.label, item.hint))}
        {slot('text', '自由文本', '可重复添加')}{slot('help', '帮助文字')}{slot('fields', '输入字段')}{slot('result', '运行结果')}
      </div>
    </details>
    <details className="process-element-panel"><summary><MousePointerClick size={16} /><span>按钮<small>核心操作与自定义逻辑入口</small></span><ChevronDown size={14} /></summary>
      {layer.kind !== 'ink' && <p className="face-note">请选择油墨层添加可操作的按钮。</p>}
      <div className="process-element-options">{BUTTON_ACTIONS.map(item => <button type="button" key={item.action} className="process-element-option"
        aria-label={`添加${item.label}按钮`} disabled={surface.elements.length >= 32 || layer.kind !== 'ink'} onClick={() => addSlot('button', layer.id, item.action)}>
        <span><b>{item.label}</b><small>{item.hint}</small></span><Plus size={14} /></button>)}</div>
    </details>
    <details className="process-element-panel"><summary><Shapes size={16} /><span>图形<small>基础形状、图标与徽标</small></span><ChevronDown size={14} /></summary>
      <div className="process-shape-options">{([{ kind: 'rect', label: '矩形', Icon: Square }, { kind: 'ellipse', label: '圆形', Icon: Circle }, { kind: 'polygon', label: '多边形', Icon: Triangle }] as const).map(({ kind, label, Icon }) =>
        <button type="button" key={kind} disabled={surface.shapes.length >= 24} onClick={() => addShape(kind, layer.id)}><Icon size={22} /><span>{label}</span></button>)}</div>
      <div className="process-element-options">{slot('icon', '图标', '灵感、文档、助手等')}{slot('badge', '徽标', '强调一段简短文字')}</div>
      {(layer.kind === 'ink' || layer.kind === 'foil') && <button type="button" className="process-fill-card" disabled={surface.shapes.length >= 24} onClick={() => addShape('rect', layer.id, true)}><Square size={14} />铺满卡面</button>}
    </details>
    <details className="process-element-panel"><summary><Image size={16} /><span>图像<small>{layer.kind === 'ink' ? '上传图片与印刷底纹' : '上传图片作为工艺图案'}</small></span><ChevronDown size={14} /></summary>
      <label className="process-image-upload"><Upload size={18} /><span>上传图片</span><small>PNG · 最大 1 MiB / 2048 px</small>
        <input aria-label="上传图像元素" type="file" accept="image/png" disabled={surface.elements.length >= 32} onChange={event => { void upload(event.target.files?.[0], undefined, layer.id); event.target.value = ''; }} />
      </label>
      {layer.kind === 'ink' && <div className="process-patterns"><h4>印刷底纹</h4><div role="group" aria-label="印刷底纹">{([['none', '无底纹'], ['contour', '等高线'], ['rays', '放射线'], ['grid', '几何网格']] as const).map(([motif, label]) =>
        <button type="button" key={motif} aria-pressed={pattern.motif === motif} onClick={() => updatePattern({ motif })}><span><CardPrintArt motif={motif} /></span><small>{label}</small></button>)}</div>
        {pattern.motif !== 'none' && <ProductionSlider label="底纹浓度" value={pattern.density} onChange={density => updatePattern({ density })} />}
      </div>}
    </details>
    <div className="process-owned-elements"><h4>本层元素 <small>{ids.length}</small></h4>
      {targets.filter(item => ids.includes(item.id)).map(item => <button type="button" key={item.id} aria-label={`编辑${item.name}`} aria-pressed={selection === item.id} onClick={() => select(item.id, item.kind)}><Layers size={13} />{item.name}</button>)}
      {!ids.length && <p className="face-note">{!layer.content ? '此层保留预设图案，添加元素后可自由设计。' : layer.kind === 'ink' || layer.kind === 'foil' ? '添加元素开始设计，也可在图形中铺满卡面。' : '还没有元素，当前工艺作用于整张卡面。添加元素后随元素轮廓加工。'}</p>}
    </div>
  </section>;
}
