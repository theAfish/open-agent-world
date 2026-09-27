import { useState, type CSSProperties } from 'react';
import { Bot, RotateCcw } from 'lucide-react';
import { CardFinishLayer } from '../cards/CardFinishLayer';
import { useCardFinish } from '../cards/useCardFinish';
import { CARD_FINISHES, finishLabel, type CardFinish } from '../cards/cardFinish';
import { CardFace } from '../components/CardFace';
import './finishPreview.css';

const descriptions: Record<CardFinish, string> = {
  normal: 'Uncoated stock. Original ink and artwork.',
  foil: 'Silver facets, fine grain and a broad metallic reflection.',
  rainbow: 'A continuous spectrum beneath a clear iridescent coat.',
  starlight: 'Embedded flakes catch the light on a smoked base.',
  laser: 'Holographic facets and engraved security rosettes.',
};

function PreviewCard({ finish, compact = false, reveal = false }: { finish: CardFinish; compact?: boolean; reveal?: boolean }) {
  const quality = compact ? 'thumbnail' : 'showcase';
  const pointer = useCardFinish(finish, quality, true);
  return <article className={`finish-preview-card card-stock card-finish-surface ${compact ? 'is-compact' : ''}`}
    data-preview-finish={finish} style={{ '--collection-color': '#628e80' } as CSSProperties} {...pointer}>
    <CardFace icon={<Bot />} label="Research Agent" description="Explore ideas. Connect knowledge. Make something new." />
    <CardFinishLayer finish={finish} quality={quality} reveal={reveal} />
  </article>;
}

/** Loaded only in development; never writes a finish or changes probability. */
export function FinishPreview({ embedded = false }: { embedded?: boolean }) {
  const [finish, setFinish] = useState<CardFinish>('foil');
  const [dark, setDark] = useState(false);
  const [dense, setDense] = useState(false);
  const [reveal, setReveal] = useState(0);
  return <section className={`finish-preview ${embedded ? 'is-embedded' : ''}`} data-theme={dark ? 'dark' : 'light'}>
    <div className="finish-preview-heading"><small>OAW / MATERIAL STUDY</small><h2>Card finishes</h2>
      <p>Move over a card to tilt it beneath a fixed studio light. Each finish is printed into the surface.</p></div>
    <div className="finish-preview-controls">
      {embedded && <label>Finish <select value={finish} onChange={event => setFinish(event.target.value as CardFinish)}>
        {CARD_FINISHES.map(value => <option key={value} value={value}>{finishLabel(value)}</option>)}
      </select></label>}
      <label><input type="checkbox" checked={dark} onChange={event => setDark(event.target.checked)} /> Dark card stock</label>
      {!embedded && <label><input type="checkbox" checked={dense} onChange={event => setDense(event.target.checked)} /> 200 thumbnails</label>}
      <button type="button" onClick={() => setReveal(value => value + 1)}><RotateCcw size={13} /> Replay light</button>
      {embedded && <a href="/?card-finishes" target="_blank" rel="noreferrer">Open material gallery</a>}
    </div>
    <div className={`finish-preview-grid ${dense ? 'is-dense' : ''}`} key={reveal}>
      {(dense ? Array.from({ length: 200 }, (_, index) => CARD_FINISHES[index % CARD_FINISHES.length]) : embedded ? [finish] : CARD_FINISHES).map((value, index) =>
        <figure key={`${value}:${index}`}><PreviewCard finish={value} compact={dense} reveal={reveal > 0 && !dense} />
          <figcaption><strong>{finishLabel(value)}</strong>{!dense && <span>{descriptions[value]}</span>}</figcaption></figure>)}
    </div>
    {!embedded && <p className="finish-preview-note">Development preview · No collection changes · Use your browser's reduced-motion setting to inspect the static material.</p>}
  </section>;
}
