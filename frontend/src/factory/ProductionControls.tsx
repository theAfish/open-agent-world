import type { CSSProperties } from 'react';
import { CARD_STOCKS, LAMINATES, resolveStock, type CardProduction, type PrintProof } from '../cards/cardProduction';
import { CardPrintArt } from '../components/CardPrintArt';
import './productionControls.css';

export const PROOF_LABELS: Record<PrintProof, string> = {
  composite: '成品', artwork: '原始印刷', finishing: '局部工艺', laminate: '覆膜', protection: '信息层',
};
const PAPER_COLORS = ['#f5f3e9', '#e9deca', '#e0e8df', '#dbe3ed', '#ead9dc', '#233332'];
const FILM_LABELS = { none: '无覆膜', gloss: '透明亮膜', holo: '全息', aurora: '极光', laser: '镭射', starlight: '星光' };
const FILM_HINTS = { none: '保留纸面质感', gloss: '清透反光', holo: '细碎光谱', aurora: '流动虹彩', laser: '定向刻纹', starlight: '闪光微粒' };
const PROCESSES = [
  { key: 'foil', label: '烫金', initial: .7 },
  { key: 'emboss', label: '压纹', initial: .5 },
  { key: 'spotUV', label: '局部光油', initial: .6 },
  { key: 'edgeFoil', label: '烫边', initial: .55 },
] as const;

export function ProductionSlider({ label, value, onChange, ends = ['轻', '强'], min = 0 }: {
  label: string; value: number; onChange: (value: number) => void; ends?: readonly [string, string]; min?: number;
}) {
  return <label className="face-slider production-slider"><span>{label}<output>{Math.round(value * 100)}%</output></span>
    <input aria-label={label} type="range" min={Math.round(min * 100)} max="100" value={Math.round(value * 100)} onChange={event => onChange(Number(event.target.value) / 100)} />
    <span><small>{ends[0]}</small><small>{ends[1]}</small></span></label>;
}

export function ProductionControls({ stage, production, onChange }: {
  stage: 'stock' | 'print' | 'finishing' | 'laminate'; production: CardProduction; onChange: (value: CardProduction) => void;
}) {
  const update = <K extends keyof CardProduction>(key: K, value: CardProduction[K]) => onChange({ ...production, [key]: value });
  const stock = resolveStock(production);
  const finishing = production.finishing;
  if (stage === 'stock') return <div className="production-controls">
    <div className="production-options production-paper-options" role="group" aria-label="卡纸材质">
      {Object.entries(CARD_STOCKS).map(([id, material]) => <button type="button" key={id} aria-label={material.label} aria-pressed={production.stock.type === id}
        onClick={() => update('stock', { ...production.stock, type: id as CardProduction['stock']['type'], grain: material.grain })}>
        <span className="production-paper-sample" data-stock={id} aria-hidden="true" style={{ backgroundColor: production.stock.color ? stock.paper : material.paper, color: production.stock.color ? stock.ink : material.ink }}>Aa</span>
        <strong>{material.label}</strong></button>)}
    </div>
    <label className="production-color">纸面颜色<span><input aria-label="纸面颜色" type="color" value={stock.paper} onChange={event => update('stock', { ...production.stock, color: event.target.value })} />
      <output>{stock.paper.toUpperCase()}</output><button type="button" aria-label="恢复纸张原色" title="恢复纸张原色" onClick={() => { const { color: _color, ...original } = production.stock; update('stock', original); }}>原色</button></span></label>
    <div className="production-paper-colors" role="group" aria-label="常用纸色">{PAPER_COLORS.map(color => <button type="button" key={color} aria-label={`纸色 ${color}`} aria-pressed={stock.paper.toLowerCase() === color}
      style={{ '--paper-swatch': color } as CSSProperties} onClick={() => update('stock', { ...production.stock, color })} />)}</div>
    <ProductionSlider label="纸面纹理" value={production.stock.grain} ends={['平滑', '粗糙']} onChange={grain => update('stock', { ...production.stock, grain })} />
  </div>;
  if (stage === 'print') return <div className="production-controls">
    <div className="production-options production-pattern-options" role="group" aria-label="印刷底纹">
      {([['none', '无底纹'], ['contour', '等高线'], ['rays', '放射线'], ['grid', '几何网格']] as const).map(([motif, label]) =>
        <button key={motif} type="button" aria-label={label} aria-pressed={production.print.motif === motif} onClick={() => update('print', { ...production.print, motif })}>
          <span className="production-pattern-sample" aria-hidden="true"><CardPrintArt motif={motif} /></span><strong>{label}</strong></button>)}
    </div>
    {production.print.motif !== 'none' && <ProductionSlider label="底纹墨量" value={production.print.density} ends={['淡', '浓']} onChange={density => update('print', { ...production.print, density })} />}
  </div>;
  if (stage === 'finishing') return <div className="production-controls">
    <div className="production-options production-process-options" role="group" aria-label="局部工艺">
      {PROCESSES.map(({ key, label, initial }) => <button key={key} type="button" aria-label={label} aria-pressed={finishing[key] > 0}
        onClick={() => update('finishing', { ...finishing, [key]: finishing[key] > 0 ? 0 : initial })}>
        <span className="production-process-sample" data-process={key} aria-hidden="true">Aa</span><strong>{label}</strong></button>)}
    </div>
    {PROCESSES.filter(({ key }) => finishing[key] > 0).map(({ key, label }) => <ProductionSlider key={key} label={`${label}强度`} value={finishing[key]} onChange={value => update('finishing', { ...finishing, [key]: value })} />)}
    {(finishing.foil > 0 || finishing.emboss > 0) && <label>工艺区域<select aria-label="工艺区域" value={finishing.target} onChange={event => update('finishing', { ...finishing, target: event.target.value as 'accents' | 'artwork' })}>
      <option value="accents">装饰线条</option><option value="artwork">插画区域</option></select></label>}
    {(finishing.foil > 0 || finishing.edgeFoil > 0) && <div className="production-tones" role="group" aria-label="金属颜色">
      {([['gold', '香槟金'], ['silver', '银色']] as const).map(([foilTone, label]) => <button type="button" key={foilTone} aria-pressed={finishing.foilTone === foilTone}
        onClick={() => update('finishing', { ...finishing, foilTone })}><i data-metal={foilTone} />{label}</button>)}
    </div>}
    <p className="production-hint">{finishing.spotUV > 0 ? '光油覆盖插画，烫边沿卡牌边缘。' : '可叠加工艺，移动卡面查看反光与凹凸。'}</p>
  </div>;
  return <div className="production-controls">
    <div className="production-options production-film-options" role="group" aria-label="覆膜效果">{LAMINATES.map(type => <button key={type} type="button" aria-label={FILM_LABELS[type]} title={FILM_HINTS[type]} aria-pressed={production.laminate.type === type}
      onClick={() => update('laminate', { ...production.laminate, type })}>
      <span className="production-film-sample" data-film={type} aria-hidden="true" /><strong>{FILM_LABELS[type]}</strong>
    </button>)}</div>
    {production.laminate.type !== 'none' && <>
      <ProductionSlider label="反光强度" value={production.laminate.strength} onChange={strength => update('laminate', { ...production.laminate, strength })} />
      <ProductionSlider label="光泽柔度" value={production.laminate.roughness} min={.06} ends={['锐利', '柔和']} onChange={roughness => update('laminate', { ...production.laminate, roughness })} />
    </>}
    <p className="production-hint">{production.laminate.type === 'none' ? '保留纸张与局部工艺的质感。' : '移动卡面，查看不同角度的光泽。'}</p>
  </div>;
}
