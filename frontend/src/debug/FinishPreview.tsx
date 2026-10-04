import { useRef, useState, type CSSProperties } from 'react';
import { Bot, RotateCcw } from 'lucide-react';
import { CARD_FINISHES, finishLabel, type CardFinish } from '../cards/cardFinish';
import { CardFace, CardStock } from '../components/CardFace';
import { useMaterialStudy, type StudyMode } from './useMaterialStudy';
import './finishPreview.css';

const descriptions: Record<CardFinish, string> = {
  normal: 'Uncoated stock. Original ink and artwork.',
  foil: 'A coherent silver reflection with fine brushing and polished edges.',
  rainbow: 'Aurora laminate. Broad spectral fields bend and travel over the printed artwork.',
  starlight: 'Isolated angle-lit flakes and round spot gloss. No continuous band.',
  laser: 'Fine engraved grooves that appear within a narrow viewing angle.',
};

function StudyPattern() {
  return <span className="finish-study-pattern" data-material-region="artwork" aria-hidden="true">
    <svg viewBox="0 0 140 160" preserveAspectRatio="none">
      <rect x="0" y="0" width="68" height="76" rx="3" fill="var(--study-slate)" />
      <rect x="72" y="0" width="68" height="76" rx="3" fill="var(--study-sand)" />
      <rect x="0" y="80" width="68" height="80" rx="3" fill="var(--study-sage)" />
      <rect x="72" y="80" width="68" height="80" rx="3" fill="var(--study-rose)" />
      <g fill="none" stroke="var(--study-line)" strokeWidth=".6">
        <circle cx="70" cy="80" r="28" /><circle cx="70" cy="80" r="18" />
        <path d="M12 24h42m-42 5h42m-42 5h42M88 114h34m-34 5h34m-34 5h34M17 134l25-30 13 16M88 19l32 37M88 56l32-37" />
      </g>
    </svg>
  </span>;
}

function PreviewCard({ finish, compact = false, pattern, layer }: {
  finish: CardFinish; compact?: boolean; pattern: boolean; layer?: 'print' | 'laminate' | 'composite';
}) {
  const quality = compact ? 'thumbnail' : 'showcase';
  return <CardStock className={`finish-preview-card ${compact ? 'is-compact' : ''}`} size={compact ? 'compact' : 'standard'}
    data-preview-finish={finish} data-study-layer={layer} style={{ '--collection-color': '#628e80' } as CSSProperties} finish={finish} quality={quality}>
    <CardFace icon={<Bot />} tone="sage" label="Research Agent" description="Explore ideas. Connect knowledge. Make something new."
      badge="OAW / 001" artwork={pattern ? <StudyPattern /> : undefined} />
  </CardStock>;
}

/** Loaded only in development; never writes a finish or changes probability. */
export function FinishPreview({ embedded = false }: { embedded?: boolean }) {
  const [finish, setFinish] = useState<CardFinish>('rainbow');
  const [dark, setDark] = useState(true);
  const [dense, setDense] = useState(false);
  const [revision, setRevision] = useState(0);
  const [mode, setMode] = useState<StudyMode>('pointer');
  const [pattern, setPattern] = useState(true);
  const [comparison, setComparison] = useState(false);
  const grid = useRef<HTMLDivElement>(null);
  const reduced = useMaterialStudy(grid, mode, `${revision}:${dense}:${finish}:${comparison}:${pattern}`, comparison);
  return <section className={`finish-preview ${embedded ? 'is-embedded' : ''}`} data-theme={dark ? 'dark' : 'light'}>
    <div className="finish-preview-heading"><small>OAW / MATERIAL STUDY</small><h2>Card finishes</h2>
      <p>A printed card beneath an optical film. Tilt to follow the colour travel; titles, symbols and fine print stay on a protected top layer.</p></div>
    <div className="finish-preview-controls">
      {embedded && <label>Finish <select value={finish} onChange={event => setFinish(event.target.value as CardFinish)}>
        {CARD_FINISHES.map(value => <option key={value} value={value}>{finishLabel(value)}</option>)}
      </select></label>}
      <label>Lighting <select aria-label="Study lighting" value={mode} onChange={event => setMode(event.target.value as StudyMode)}>
        <option value="pointer">Pointer / tilt</option>
        <option value="representative">Representative angles</option>
        <option value="sweep" disabled={dense}>Slow light sweep</option>
      </select></label>
      <label><input type="checkbox" checked={comparison} onChange={event => { setComparison(event.target.checked); setDense(false); }} /> Layer comparison</label>
      <label><input type="checkbox" checked={pattern} onChange={event => setPattern(event.target.checked)} /> Printed test pattern</label>
      <label><input type="checkbox" checked={dark} onChange={event => setDark(event.target.checked)} /> Dark card stock</label>
      {!embedded && <label><input type="checkbox" checked={dense} onChange={event => { setDense(event.target.checked); setComparison(false); if (event.target.checked && mode === 'sweep') setMode('representative'); }} /> 200 thumbnails</label>}
      <button type="button" onClick={() => setRevision(value => value + 1)}><RotateCcw size={13} /> Reset view</button>
      {embedded && <a href="/?card-finishes" target="_blank" rel="noreferrer">Open material gallery</a>}
      <a href="/?card-design" target="_blank" rel="noreferrer">Card design system</a>
    </div>
    <p className="finish-study-status" role="status">{mode === 'pointer' ? 'Move the pointer to explore. The laminate remains visible at its neutral resting angle.'
      : mode === 'representative' || reduced ? 'Fixed characteristic angles for comparison.' : 'Slow studio-light sweep. Change to Pointer / tilt to stop.'}
      {mode === 'sweep' && reduced && ' Sweep is paused for reduced motion.'}</p>
    <div ref={grid} className={`finish-preview-grid ${dense ? 'is-dense' : ''} ${comparison ? 'is-comparison' : ''}`} key={revision} data-study-mode={mode}
      onPointerOverCapture={event => { if (mode !== 'pointer') event.stopPropagation(); }}
      onPointerOutCapture={event => { if (mode !== 'pointer') event.stopPropagation(); }}
      onPointerMoveCapture={event => { if (mode !== 'pointer') event.stopPropagation(); }}>
      {comparison ? (['print', 'laminate', 'composite'] as const).map((layer, index) =>
        <figure key={layer}><PreviewCard finish={layer === 'print' ? 'normal' : 'rainbow'} pattern={pattern} layer={layer} />
          <figcaption><strong>{['01 / Printed stock', '02 / Masked laminate', '03 / Finished card'][index]}</strong>
            <span>{['Original artwork and neutral top print.', 'The film in isolation. Dark cutouts show protected regions.', 'Artwork + aurora film + crisp top print. Move over any card to compare the same angle.'][index]}</span></figcaption></figure>)
        : (dense ? Array.from({ length: 200 }, (_, index) => CARD_FINISHES[index % CARD_FINISHES.length]) : embedded ? [finish] : CARD_FINISHES).map((value, index) =>
        <figure key={`${value}:${index}`}><PreviewCard finish={value} compact={dense} pattern={pattern} />
          <figcaption><strong>{value === 'rainbow' ? 'Holo' : finishLabel(value)}</strong>{!dense && <span>{descriptions[value]}</span>}</figcaption></figure>)}
    </div>
    {!embedded && <p className="finish-preview-note">Development preview · No collection changes · Use your browser's reduced-motion setting to inspect the static material.</p>}
  </section>;
}
