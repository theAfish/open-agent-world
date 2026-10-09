import { useEffect, useState } from 'react';
import { ArrowUpRight, Boxes, Check, Copy, Database, Gem, Leaf, Moon, Package, Puzzle, RotateCcw, Sparkles, Sun } from 'lucide-react';
import { PackSurface, type PackPhase, type PackPreviewCard } from '../shell/PackSurface';
import { PACK_DESIGNS, PACK_PACKAGING, type PackPackaging } from '../shell/packDesign';
import type { PackDefinition } from '../types/world';
import { PackViewContext, type PackView } from '../shell/pack3d/types';
import './packDesignPreview.css';

const studies = {
  standard: { zh: '标准卡包', name: 'SQL database', description: '让每一个想法，都有迹可循。', note: '轻盈便携，日常探索的最佳选择。', icon: <Database />, count: 1, traits: ['柔感哑光', '轻巧软袋', '易撕开口'] },
  premium: { zh: '高级烫印卡包', name: 'Codex 智能体', description: '连接 OAW 工具的 Codex 工作者。', note: '精致细节，收藏级的探索体验。', icon: <Sparkles />, count: 1, traits: ['珠光纸面', '全息封条', '浮雕细节'] },
  paper: { zh: '环保纸质卡包', name: '研究图书馆', description: '留一处空间，给论文与灵感。', note: '简约环保，适合长期收纳与重复使用。', icon: <Puzzle />, count: 2, traits: ['再生纸感', '弧形翻盖', '叶片封签'] },
  collector: { zh: '收藏版卡盒', name: '核心基础', description: '为你的世界提供智能体、资源和工作区。', note: '精致收纳，完整的主题体验。', icon: <Boxes />, count: 12, traits: ['立体硬盒', '山景封面', '完整收藏'] },
};
const materials = [
  ['matte', '柔感哑光膜', 'Soft-touch Matte'], ['satin', '细腻缎面', 'Satin Finish'],
  ['foil', '全息烫印', 'Holographic Foil'], ['translucent', '透明涂层', 'Clearcoat Layer'],
  ['paper', '再生纸纤维', 'Recycled Paper'], ['metal', '金属内衬', 'Metallic Lining'],
];

function PreviewPack({ packaging, opened = false, database = false, inspect }: { packaging: PackPackaging; opened?: boolean; database?: boolean; inspect?: () => void }) {
  const study = studies[packaging];
  const [phase, setPhase] = useState<PackPhase>('idle');
  const [isOpen, setOpen] = useState(opened);
  const [finish, setFinish] = useState(false);
  useEffect(() => {
    if (phase !== 'revealing') return;
    const light = window.setTimeout(() => setFinish(true), 720);
    const settle = window.setTimeout(() => setPhase('idle'), 2100);
    return () => { window.clearTimeout(light); window.clearTimeout(settle); };
  }, [phase]);
  const definition: PackDefinition = { id: `preview.${packaging}`, plugin_id: 'preview', name: database ? 'SQL database' : study.name,
    description: database ? '让每一个想法，都有迹可循。' : study.description, cards: [], packaging };
  const cards: PackPreviewCard[] = [
    { id: 'explore', label: 'Explore More', icon: database ? <Database /> : study.icon, color: '#7f9c9e', finish: 'foil' },
    { id: 'create', label: 'Create Together', icon: <Boxes />, color: '#a3ad90', finish: 'normal' },
    { id: 'possibilities', label: 'New Possibilities', icon: <Sparkles />, color: '#b5a084', finish: 'laser' },
  ];
  return <PackSurface definition={definition} edition={phase === 'revealing' ? '发现更多可能。' : isOpen ? '拖动查看内部 · 点击重新体验' : '拖动旋转 · 点击拆开'}
    cards={cards.slice(0, database ? 1 : study.count)} count={database ? 1 : study.count} opened={isOpen} phase={phase} finishVisible={finish}
    label={`${isOpen ? '重新体验' : '打开'}${study.zh}`} sealLabel="TEAR TO OPEN" disabled={phase !== 'idle'}
    onClick={inspect ?? (() => { if (isOpen) { setOpen(false); setFinish(false); } else { setOpen(true); setPhase('revealing'); } })} />;
}

/** No backend or library store: opening these samples never changes a collection. */
export function PackDesignPreview() {
  const [selected, setSelected] = useState<PackPackaging>('standard');
  const [dark, setDark] = useState(false);
  const [compact, setCompact] = useState(false);
  const [reset, setReset] = useState(0);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const [opening, setOpening] = useState(.48);
  const [view, setView] = useState<PackView>({ yaw: -18, pitch: -3, light: 0, surface: 'material' });
  const snippet = `PackDefinition(\n    id="acme.tools.default",\n    name="Acme Tools",\n    cards=("acme.tool",),\n    packaging="${selected}",\n)`;
  useEffect(() => { setCopied(false); setCopyError(false); }, [selected]);
  return <PackViewContext.Provider value={view}><main className="pack-design-preview" data-theme={dark ? 'dark' : 'light'}>
    <div className="pack-design-sheet">
      <header className="pack-design-heading">
        <div><span className="pack-design-overline">O A W <i /> DESIGN SYSTEM</span><h1>小小卡包，更大的可能。</h1>
          <p>以更轻盈的方式，装载每一次探索。</p><span className="pack-design-english">Same ideas. A kinder form. More to explore.</span></div>
        <div className="pack-design-edition">PACK DESIGN LANGUAGE<br /><b>CONCEPT C / VOLUME STUDY</b><span>PLAY<br />EXPLORE<br />CREATE<br />TOGETHER</span></div>
      </header>
      <div className="pack-design-toolbar"><span>四种形式，一套温和的设计语言。</span><div>
        <label><input type="checkbox" checked={compact} onChange={event => setCompact(event.target.checked)} /> 卡库尺寸</label>
        <button type="button" onClick={() => setReset(value => value + 1)}><RotateCcw size={14} />重置拆包</button>
        <button type="button" aria-label="深色预览" aria-pressed={dark} onClick={() => setDark(value => !value)}>{dark ? <Sun size={16} /> : <Moon size={16} />}</button>
      </div></div>
      <section className="pack-design-inspector" aria-label="3D 包装检查">
        <div className="pack-design-views" role="group" aria-label="观察角度">
          {([['正面', 0, -3], ['侧面', 75, -3], ['背面', 180, -3], ['俯视', -25, 35]] as const).map(([label, yaw, pitch]) =>
            <button key={label} type="button" aria-pressed={view.yaw === yaw && view.pitch === pitch} onClick={() => setView(value => ({ ...value, yaw, pitch }))}>{label}</button>)}
        </div>
        <label>旋转 <input aria-label="旋转角度" type="range" min="-180" max="180" value={view.yaw} onChange={event => setView(value => ({ ...value, yaw: Number(event.target.value) }))} /><output>{view.yaw}°</output></label>
        <label>光照 <input aria-label="光照角度" type="range" min="-180" max="180" value={view.light} onChange={event => setView(value => ({ ...value, light: Number(event.target.value) }))} /></label>
        <div className="pack-design-views" role="group" aria-label="表面显示">
          {([['material', '成品'], ['clay', '素模'], ['wireframe', '网格']] as const).map(([surface, label]) => <button type="button" key={surface} aria-pressed={view.surface === surface} onClick={() => setView(value => ({ ...value, surface }))}>{label}</button>)}
        </div>
      </section>
      <section className={`pack-design-grid${compact ? ' is-compact' : ''}`} aria-label="四种包装预设">
        {PACK_PACKAGING.map((packaging, index) => <section key={packaging} className={`pack-design-study${selected === packaging ? ' is-selected' : ''}`}>
          <header><h2><span>0{index + 1}</span>{studies[packaging].zh}</h2><small>{PACK_DESIGNS[packaging].label}</small><p>{studies[packaging].note}</p></header>
          <PreviewPack key={`${packaging}-${reset}`} packaging={packaging} />
          <ul>{studies[packaging].traits.map((trait, i) => <li key={trait}>{i === 0 ? <Leaf /> : i === 1 ? <Gem /> : <Package />}{trait}</li>)}</ul>
          <button className="pack-design-choose" type="button" aria-pressed={selected === packaging} onClick={() => setSelected(packaging)}>
            {selected === packaging ? <Check size={13} /> : <span className="pack-design-choice-dot" />}{selected === packaging ? '已选择此预设' : '选择此预设'}<code>{packaging}</code>
          </button>
        </section>)}
      </section>
      <section className="pack-design-details" aria-label="材质与开发配置">
        <article className="pack-design-opened"><h2>打开，发现更多。<small>Opening Study</small></h2><p>拖动进度，查看所选包装的每一个拆开瞬间。</p>
          <PackViewContext.Provider value={{ ...view, opening }}><div className="pack-design-opened-scene"><PreviewPack key={`opened-${selected}-${reset}`} packaging={selected} opened inspect={() => setOpening(value => value >= .95 ? 0 : .95)} /></div></PackViewContext.Provider>
          <label className="pack-design-scrub">拆包进度 <input aria-label="拆包进度" type="range" min="0" max="1" step="0.01" value={opening} onChange={event => setOpening(Number(event.target.value))} /><output>{Math.round(opening * 100)}%</output></label>
        </article>
        <article className="pack-design-materials"><h2>材质语言<small>Material Language</small></h2><div>{materials.map(([id, zh, en]) => <figure key={id}><span className={`pack-material-swatch pack-material-${id}`} /><figcaption>{zh}<small>{en}</small></figcaption></figure>)}</div></article>
        <article className="pack-design-config"><h2>从这里开始<small>Developer Preset</small></h2><p>当前选择 <strong>{studies[selected].zh}</strong></p><pre><code>{snippet}</code></pre>
          <button type="button" onClick={async () => { try { await navigator.clipboard.writeText(snippet); setCopied(true); setCopyError(false); } catch { setCopyError(true); } }}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? '已复制配置' : '复制配置'}</button>
          <span role="status">{copyError ? '请选中上方代码手动复制。' : 'packaging 可独立配置，封面与主题色继续复用。'}</span><a href="/?card-design">查看卡面设计语言 <ArrowUpRight size={13} /></a>
        </article>
      </section>
      <footer className="pack-design-footer"><strong>OAW</strong><span>OPEN AGENT WORLD</span><i /><small>PLAY MORE THAN CARDS.</small></footer>
    </div>
  </main></PackViewContext.Provider>;
}
