import { CardPrintArt } from '../components/CardPrintArt';
import './worldCardPrint.css';

/** The printed face is present even on normal stock. Both full and LOD cards
 * coat this entire plane; titles and information are transparent top-print ink. */
export function WorldCardPrint({ motif = 'contour', fullBleed = false }: {
  motif?: 'contour' | 'rays' | 'grid' | 'none'; fullBleed?: boolean;
}) {
  if (motif === 'none') return null;
  return <div className="world-card-print" data-material-layer="artwork" data-full-bleed={fullBleed || undefined} aria-hidden="true">
    <div className="world-card-print-art"><CardPrintArt motif={motif} /></div>
    {!fullBleed && <span className="world-card-print-rule" data-material-region="accent" />}
  </div>;
}
