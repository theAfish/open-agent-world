import { useState } from 'react';
import { ArrowUpRight, Bot, Box, FileText, Image, Leaf, Moon, Sun, Terminal } from 'lucide-react';
import { CardFace, CardStock, type CardFaceProps } from '../components/CardFace';
import { CARD_FINISHES, type CardFinish } from '../cards/cardFinish';
import landscape from './cardLandscape.svg';
import './cardDesignPreview.css';

const examples: (CardFaceProps & { kind: string })[] = [
  { kind: 'AGENT / 图标', label: 'Sage', description: '让好奇心，有迹可循。', icon: <Bot />, variant: 'icon', tone: 'sage' },
  { kind: 'TOOL / 图标', label: 'Python Console', description: '从一个想法，到一次运行。', icon: <Terminal />, variant: 'icon', tone: 'sand' },
  { kind: 'DATASET / 图片', label: 'World Landscapes', description: '山川之间，发现新的视角。', icon: <Image />, variant: 'image', tone: 'sky', imageUrl: landscape, badge: 'Collection' },
  { kind: 'NOTE / 文字', label: 'A quieter internet', description: '记录慢一点的思考，留一些空间给新的可能。', icon: <FileText />, variant: 'text', tone: 'rose' },
  { kind: 'UTILITY / 紧凑', label: 'Everyday tools', description: '小而有用，随手可取。', icon: <Box />, variant: 'compact', tone: 'stone' },
  { kind: 'SKILL / 深色', label: 'Synthesis', description: '连接线索，让思路渐渐清晰。', icon: <Leaf />, variant: 'dark', tone: 'midnight' },
];
const finishes: Record<CardFinish, string> = { normal: '原纸', foil: '烫箔', rainbow: '虹彩', starlight: '星光', laser: '镭射' };
const palette = [ ['Midnight', '#303a46'], ['Sage', '#a1b29b'], ['Sand', '#e9d5b7'], ['Sky', '#a9c5d4'], ['Rose', '#d5a3ab'], ['Stone', '#d7d9d2'] ];

/** A live design reference, using the same faces as the Library and hand. */
export function CardDesignPreview() {
  const [finish, setFinish] = useState<CardFinish>('normal');
  const [dark, setDark] = useState(false);
  const [compact, setCompact] = useState(false);
  return <main className="card-design-preview" data-theme={dark ? 'dark' : 'light'}>
    <header className="design-heading">
      <div><span className="design-overline">O A W <i /> CARD DESIGN SYSTEM</span>
        <h1>更少的形式，<br className="design-mobile-break" />更多的可能。</h1>
        <p>先让纸张与印刷成立，再用局部工艺和薄膜赋予触感。</p></div>
      <span className="design-edition">OPEN AGENT WORLD<br />CARD STUDY — 01</span>
    </header>
    <section className="design-controls" aria-label="卡面预览设置">
      <div className="design-finish-options" role="group" aria-label="材质">
        {CARD_FINISHES.map(value => <button type="button" key={value} aria-pressed={finish === value} onClick={() => setFinish(value)}>{finishes[value]}</button>)}
      </div>
      <div className="design-view-options">
        <label><input type="checkbox" checked={compact} onChange={event => setCompact(event.target.checked)} /> 缩略卡</label>
        <button type="button" aria-label="深色主题" aria-pressed={dark} onClick={() => setDark(value => !value)}>{dark ? <Sun size={16} /> : <Moon size={16} />}</button>
      </div>
    </section>
    <section className={`design-card-grid ${compact ? 'is-compact' : ''}`} aria-label="卡面预设">
      {examples.map(({ kind, ...face }) => <figure key={kind}>
        <figcaption>{kind}</figcaption>
        <CardStock size={compact ? 'compact' : 'standard'} className="design-example" finish={finish} quality="showcase"><CardFace {...face} /></CardStock>
      </figure>)}
    </section>
    <section className="design-foundations" aria-label="设计规范">
      <article className="design-palette"><h2>01 <span>自然的色彩</span></h2><div>{palette.map(([name, color]) => <figure key={name}><span style={{ background: color }} /><figcaption>{name}<small>{color.toUpperCase()}</small></figcaption></figure>)}</div></article>
      <article className="design-type"><h2>02 <span>清晰的层级</span></h2><div><b>Aa</b><p><strong>简明，无衬线。</strong><span>一个标题，一段简述。<br />细节适量，留白有度。</span></p></div></article>
      <article className="design-material"><h2>03 <span>真实的表面工艺</span></h2><p>纸张 → 印刷 → 工艺 → 覆膜</p><span>文字与关键信息始终位于受保护的顶层。<br />在生产编辑器中分别设计底图、烫印与薄膜。</span><a href="/?card-studio">卡牌生产编辑器 <ArrowUpRight size={14} /></a></article>
    </section>
    <footer className="design-footer"><strong>OAW</strong><span>同一种语言，不同的世界。</span><small>PRINT FIRST. FINISH WITH INTENT.</small></footer>
  </main>;
}
