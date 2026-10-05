import { useEffect, useRef, useState } from 'react';
import { Check, Plus } from 'lucide-react';
import { CatalogIcon } from '../components/CatalogIcon';
import type { NodeSurfaceLevel } from '../types/world';
import { FaceArtwork } from './FaceArtwork';
import { ELEMENT_LABELS, FACE_MODES, MODE_LABELS } from './faceDesign';
import { RECIPES, STYLE_KITS, chooseRecipe, designTokens, slotText } from './designRecipes';
import type { DesignTokens, FaceButtonAction, FaceDesign, FaceElement, FaceShape, FaceStudio, LayoutRecipe, StyleKit, SurfaceDesign, SurfaceRecipe } from './types';
import { CoreButtonWarnings } from './CoreButtonWarnings';
import { recipeProduction } from './designRecipes';
import { MAX_PRODUCTION_LAYERS, printingLayers, type CardProduction, type PrintProof } from '../cards/cardProduction';
import { LayerElementsPanel } from './LayerElementsPanel';
import { PROCESS_RECIPES, ProcessLayerControls, replaceProcessLayers } from './ProcessLayerControls';
import { ProductionControls, PROOF_LABELS } from './ProductionControls';
import { readProductionPresets, saveProductionPreset } from './productionPresets';

export type DesignerSection = 'stock' | 'process' | 'preview' | 'preset' | 'advanced';
export function NumberControl({ label, value, min = 0, max = 2048, step = 1, onChange }: { label: string; value: number; min?: number; max?: number; step?: number; onChange: (value: number) => void }) {
  const [text, setText] = useState(String(Math.round(value * 100) / 100));
  useEffect(() => setText(String(Math.round(value * 100) / 100)), [value]);
  return <label>{label}<input type="number" value={text} min={min} max={max} step={step} onChange={event => setText(event.target.value)}
    onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); event.currentTarget.blur(); } }} onBlur={() => {
      const next = text.trim() && Number.isFinite(Number(text)) ? Math.max(min, Math.min(max, Number(text))) : value;
      setText(String(next)); if (next !== value) onChange(next);
    }} /></label>;
}
export function Choices<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: readonly (readonly [T, string])[]; onChange: (value: T) => void }) {
  return <div className="face-control"><span>{label}</span><div className="face-segments" role="group" aria-label={label}>
    {options.map(([id, name]) => <button type="button" key={id} aria-pressed={value === id} onClick={() => onChange(id)}>{name}</button>)}
  </div></div>;
}
function RecipeThumbnail({ face, surface }: { face: FaceDesign; surface: SurfaceDesign }) {
  const scale = Math.min(70 / surface.width, 80 / surface.height);
  return <span className="face-recipe-art" aria-hidden="true"><span style={{ width: surface.width * scale, height: surface.height * scale }}>
    <span style={{ width: surface.width, height: surface.height, transform: `scale(${scale})` }}><FaceArtwork face={face} surface={surface} thumbnail /></span>
  </span></span>;
}
type SlotProps = { face: FaceDesign; item: FaceElement; editText: (item: FaceElement, value: string) => void; updateFace: (patch: Partial<FaceDesign>) => void; upload: (file?: File, elementId?: string, layerId?: string) => Promise<void>; titleLabel?: string };
export function SlotEditor({ face, item, editText, updateFace, upload, titleLabel }: SlotProps) {
  const icons = ['sparkles', 'file-text', 'calculator', 'bot', 'layers', 'book-open'], names = ['灵感', '文档', '计算', '助手', '集合', '阅读'];
  if (item.kind === 'icon') return <div className="face-icon-picker" role="group" aria-label="卡牌图标">{icons.map((icon, i) =>
    <button type="button" key={icon} aria-label={names[i]} aria-pressed={face.icon === icon} onClick={() => updateFace({ icon })}><CatalogIcon definition={{ icon }} size={19} /></button>)}</div>;
  if (item.kind === 'illustration') return <label className="face-upload">{item.image_png ? '替换插图' : '上传插图'}<input type="file" accept="image/png" onChange={e => { void upload(e.target.files?.[0], item.id); e.target.value = ''; }} /><small>PNG · 最大 1 MiB / 2048 px</small></label>;
  if (['fields', 'result'].includes(item.kind)) return <p className="face-note">{item.kind === 'fields' ? '印刷后自动填入功能设计器中的输入字段。' : '运行结果将在这里显示。'}</p>;
  return <label>{titleLabel ?? ELEMENT_LABELS[item.kind]}{item.kind === 'title' || item.kind === 'action' || item.kind === 'button'
    ? <input aria-label={titleLabel ?? ELEMENT_LABELS[item.kind]} maxLength={item.kind === 'title' ? 120 : 40} value={slotText(item, face)} onChange={e => editText(item, e.target.value)} />
    : <textarea aria-label={titleLabel ?? ELEMENT_LABELS[item.kind]} rows={item.kind === 'description' ? 3 : 2} maxLength={item.kind === 'description' ? 500 : 1000} value={slotText(item, face)} onChange={e => editText(item, e.target.value)} />}</label>;
}
interface PanelProps {
  processId?: string; selectProcess: (id: string) => void; updateProduction: (value: CardProduction) => void; duplicateProcess: (id: string) => void;
  proof: PrintProof; compare: boolean; setProof: (value:PrintProof)=>void; setCompare:(value:boolean)=>void;
  applyPreset: (settings:SurfaceRecipe,color:string)=>void;
  face: FaceDesign; surface: SurfaceDesign; settings: SurfaceRecipe; studio: FaceStudio; mode: NodeSurfaceLevel; section: DesignerSection;
  layerMode: 'elements' | 'shapes'; snapping: boolean; checker: boolean;
  updateSurface: (surface: SurfaceDesign) => void; updateSettings: (patch: Partial<SurfaceRecipe>) => void; updateFace: (patch: Partial<FaceDesign>) => void;
  updateStudio: (patch: Partial<FaceStudio>) => void; enableMode: (mode: NodeSurfaceLevel, enabled: boolean) => void;
  setMode: (mode: NodeSurfaceLevel) => void; setSection: (section: DesignerSection) => void; setLayerMode: (mode: 'elements' | 'shapes') => void;
  setSnapping: (value: boolean) => void; setChecker: (value: boolean) => void;
  addShape: (kind: FaceShape['kind'], inkId?: string, full?: boolean) => void; releaseLayout: () => void; blankLayout: () => void;
  selection: string; selectLayer: (id: string, kind: 'elements' | 'shapes') => void;
  addSlot: (kind: FaceElement['kind'], inkId?: string, action?: FaceButtonAction) => void; remove: (id: string) => void; select: (id: string) => void; showPreview: () => void;
  resize: (width: number, height: number) => void; applyKit: (kit: StyleKit, appearance: SurfaceRecipe['appearance'], accent: string) => void;
  applyRecipe: (recipe: LayoutRecipe) => void;
  upload: SlotProps['upload']; editText: SlotProps['editText'];
}

export function DesignerPanel(p: PanelProps) {
  const { face, surface, settings, studio, mode, section, updateSettings, updateFace, updateSurface, setSection } = p;
  const [advancedTab, setAdvancedTab] = useState<'layers' | 'constraints' | 'tokens'>('layers');
  const production=recipeProduction(settings);
  const [presets,setPresets]=useState(readProductionPresets),[presetName,setPresetName]=useState(''),[presetMessage,setPresetMessage]=useState(''),[savingPreset,setSavingPreset]=useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  useEffect(() => { if (scroll.current) scroll.current.scrollTop = 0; }, [section, p.processId, advancedTab]);
  const tokens = designTokens(surface, settings);
  const inkLayers = printingLayers(production);
  const tokenChange = (key: keyof DesignTokens, value: string | number) => updateSettings({ tokens: { ...settings.tokens, [key]: value } });
  return <div ref={scroll} className="face-panel-scroll">
    {section==='stock'&&<><div className="face-panel-title"><h3>从一张纸开始</h3><p>选择触感，再挑一个底色。</p></div>
      <ProductionControls stage="stock" production={production} onChange={value=>updateSettings({production:value,appearance:value.stock.type==='ink'?'dark':'light'})} />
    </>}
    {section==='process'&&<ProcessLayerControls production={production} selectedId={p.processId} onSelect={p.selectProcess} onChange={p.updateProduction} onDuplicate={p.duplicateProcess}
      renderContent={layer => <LayerElementsPanel key={layer.id} {...{ layer, production, surface }} selection={p.selection} onChange={p.updateProduction}
        addSlot={p.addSlot} addShape={p.addShape} upload={p.upload} select={p.selectLayer} />} />}
    {section==='preview'&&<><div className="face-panel-title"><h3>检查成品</h3><p>和实际画布共用卡面渲染。</p></div>
      <CoreButtonWarnings face={face} studio={studio} />
      <label>查看印刷层<select aria-label="查看印刷层" value={p.proof} onChange={e=>p.setProof(e.target.value as PrintProof)}>{Object.entries(PROOF_LABELS).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
      <label className="factory-check"><input type="checkbox" checked={p.compare} onChange={e=>p.setCompare(e.target.checked)} />对比未加工卡面</label>
      <button type="button" className="face-next" onClick={()=>setSection('preset')}>保存为工艺配方<Plus size={14} /></button>
    </>}
    {section==='preset'&&<><div className="face-panel-title"><h3>预设</h3><p>选择版式或工艺组合，开始一张新设计。</p></div>
      <details className="face-preset-section" open><summary>版式</summary>
        <div className="face-recipe-grid">{RECIPES.map(recipe=><button type="button" key={recipe.id} aria-label={`应用 ${recipe.name} 版式`} aria-pressed={settings.recipe===recipe.id} onClick={()=>{p.applyRecipe(recipe.id);p.select('');}}>
          <RecipeThumbnail face={face} surface={chooseRecipe(surface,face,recipe.id)} /><span>{({hero:'主视觉',compact:'紧凑',split:'分栏',badge:'徽章',editorial:'刊物',utility:'工具',minimal:'留白',poster:'海报'})[recipe.id]}</span></button>)}</div>
        <div className="face-layout-actions"><button type="button" onClick={p.blankLayout}>空白版面</button><button type="button" onClick={p.releaseLayout}>全部自由编辑</button></div>
      </details>
      <details className="face-preset-section" open><summary>经典组合</summary><div className="process-recipes">{PROCESS_RECIPES.map(recipe=><button type="button" key={recipe.name} disabled={inkLayers.length+3>MAX_PRODUCTION_LAYERS} aria-label={`添加${recipe.name}`} onClick={()=>{const next=recipe.create();p.updateProduction(replaceProcessLayers(production,[...inkLayers,...next]));p.selectProcess(next.at(-1)!.id);}}><strong>{recipe.name}</strong><small>{recipe.hint}</small></button>)}</div></details>
      <details className="face-preset-section"><summary>字体与配色</summary><label className="face-accent">主题油墨<input type="color" value={face.color} onChange={e=>updateFace({color:e.target.value})} /></label><div className="face-kit-grid">{Object.entries(STYLE_KITS).map(([key,kit])=><button type="button" key={key} aria-label={`应用 ${kit.name} 风格`} aria-pressed={settings.kit===key} onClick={()=>p.applyKit(key as StyleKit,settings.appearance,kit.accent)}>
        <span className="face-kit-swatch" style={{background:kit.background,color:kit.text}}><b>Aa</b><i style={{background:kit.accent}} /></span><span>{kit.name}</span></button>)}</div></details>
      <h4>我的配方</h4>
      <label>配方名称<input aria-label="配方名称" maxLength={60} value={presetName} onChange={e=>setPresetName(e.target.value)} /></label>
      <button type="button" className="face-next" disabled={savingPreset||!presetName.trim()} onClick={()=>{setSavingPreset(true);setPresetMessage('');void saveProductionPreset(presetName,{...settings,production},face.color).then(value=>{setPresets(value);setPresetMessage('配方已保存');}).catch(error=>setPresetMessage(String(error.message??error))).finally(()=>setSavingPreset(false));}}>保存配方<Check size={14} /></button>
      {presetMessage&&<p role="status">{presetMessage}</p>}
      <div className="face-preset-list">{presets.map(preset=><button type="button" key={preset.id} onClick={()=>{p.applyPreset(preset.settings,preset.color);setPresetMessage('已应用配方');}}><strong>{preset.name}</strong></button>)}</div>
      {!presets.length&&<p className="face-note">保存的工艺配方会出现在这里。</p>}</>}
    {section === 'advanced' && <><div className="face-panel-title"><h3>精细控制</h3><p>仅调整当前视图，保留其他视图的设计。</p></div>
      <div className="face-advanced-tabs" role="group" aria-label="高级控制">{([['layers', '图层'], ['constraints', '约束'], ['tokens', '样式令牌']] as const).map(([value, label]) =>
        <button type="button" key={value} aria-pressed={advancedTab === value} onClick={() => setAdvancedTab(value)}>{label}</button>)}</div>
      {advancedTab === 'layers' && <><Choices label="编辑对象" value={p.layerMode} options={[["elements", "编辑内容"], ["shapes", "编辑形状"]]} onChange={p.setLayerMode} />
        <p className="face-note">拖动只改变选中的元素，其他图层保持原位。</p>
        <details open><summary>显示模式与画布</summary><nav className="face-mode-tabs" aria-label="编辑显示模式">{FACE_MODES.map(value => <button type="button" key={value} aria-pressed={mode === value} onClick={() => p.setMode(value)}>
          {MODE_LABELS[value]}<small>{studio.enabled.includes(value) ? '已启用' : '未启用'}</small></button>)}</nav>
          <label className="factory-check"><input type="checkbox" checked={studio.enabled.includes(mode)} disabled={studio.enabled.length === 1 && studio.enabled.includes(mode)} onChange={e => p.enableMode(mode, e.target.checked)} />允许此显示模式</label>
          <div className="factory-row"><label>初始显示<select value={studio.initial} onChange={e => p.updateStudio({ initial: e.target.value as NodeSurfaceLevel })}>{studio.enabled.map(value => <option value={value} key={value}>{MODE_LABELS[value]}</option>)}</select></label>
            <label>点击打开<select value={studio.open} onChange={e => p.updateStudio({ open: e.target.value as NodeSurfaceLevel })}>{studio.enabled.map(value => <option value={value} key={value}>{MODE_LABELS[value]}</option>)}</select></label></div>
          <div className="factory-row"><NumberControl label="画布宽度" value={surface.width} min={96} onChange={width => p.resize(width, surface.height)} /><NumberControl label="画布高度" value={surface.height} min={96} onChange={height => p.resize(surface.width, height)} /></div>
        </details>
        <details><summary>背景与 PNG 轮廓</summary><label>上传 PNG 背景<input type="file" accept="image/png" onChange={e => { void p.upload(e.target.files?.[0]); e.target.value = ''; }} /></label><small>透明通道可定义卡面轮廓 · 最大 1 MiB / 2048 px</small>
          {surface.background_png && <><label className="factory-check"><input type="checkbox" checked={surface.image_shape} onChange={e => updateSurface({ ...surface, image_shape: e.target.checked })} />以 PNG 透明通道定义形状</label>
            <label>背景适配<select value={surface.image_fit} onChange={e => updateSurface({ ...surface, image_fit: e.target.value as SurfaceDesign['image_fit'] })}><option value="contain">完整显示</option><option value="cover">填满裁切</option><option value="stretch">拉伸</option></select></label>
            <button type="button" onClick={() => updateSurface({ ...surface, background_png: '' })}>移除背景图</button></>}
          <label className="factory-check"><input type="checkbox" checked={p.checker} onChange={e => p.setChecker(e.target.checked)} />显示透明网格</label>
        </details></>}
      {advancedTab === 'constraints' && <><h4>语义内容 · 自动堆叠</h4><p className="face-note">主动调整排版约束时，重新编排预设内容。自由图层保持原位。</p>
        <NumberControl label="堆叠间距" value={tokens.gap} max={128} onChange={gap => tokenChange('gap', gap)} /><NumberControl label="安全边距" value={tokens.margin} max={512} onChange={margin => tokenChange('margin', margin)} />
        <Choices label="堆叠对齐" value={settings.alignment} options={[["left", "居左"], ["center", "居中"], ["right", "居右"]]} onChange={alignment => updateSettings({ alignment })} />
        <label>功能字段布局<select value={surface.field_layout} onChange={e => updateSettings({ field_layout: e.target.value as SurfaceDesign['field_layout'] })}><option value="stack">单列</option><option value="columns">双列</option></select></label>
        <label className="factory-check"><input type="checkbox" checked={p.snapping} onChange={e => p.setSnapping(e.target.checked)} />对齐吸附</label>
        <p className="face-note">选中图层后可设置适应内容 / 填充 / 固定宽度与停靠方向。Alt 拖动暂停吸附。</p></>}
      {advancedTab === 'tokens' && <><p className="face-note">精确覆盖当前风格。采用统一字体，保持层级清晰。</p>
        {([['background', '卡面底色'], ['surface', '图标表面'], ['text', '主要文字'], ['muted', '次要文字'], ['border', '边框颜色']] as const).map(([key, label]) => <label className="face-token" key={key}>{label}<input type="color" value={tokens[key]} onChange={e => tokenChange(key, e.target.value)} /></label>)}
        {([['title_size', '标题字号'], ['body_size', '正文字号'], ['radius', '圆角']] as const).map(([key, label]) => <NumberControl key={key} label={label} value={tokens[key]} min={key === 'radius' ? 0 : 8} max={key === 'radius' ? 1024 : 128} onChange={value => tokenChange(key, value)} />)}
        <button type="button" onClick={() => updateSettings({ tokens: {} })}>恢复风格默认值</button></>}
    </>}
  </div>;
}
