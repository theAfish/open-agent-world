/** Printed geometry, not an optical effect: remains visible on uncoated stock. */
export function CardPrintArt({ motif = 'contour' }: { motif?: 'contour' | 'rays' | 'grid' | 'none' }) {
  if (motif === 'none') return null;
  return <span className="card-print-art" aria-hidden="true" data-print-motif={motif}>
    <svg viewBox="0 0 240 250" preserveAspectRatio="xMidYMid slice">
      <path className="card-print-field" d="M0 178Q56 78 120 162T240 122V250H0Z" />
      <g fill="none" stroke="currentColor" strokeWidth=".65">
        {motif === 'contour' && Array.from({length:15},(_,i)=><path key={i} d={`M-35 ${55+i*13} C35 ${-20+i*13} 76 ${145+i*8} 142 ${90+i*9} S230 ${15+i*15} 280 ${95+i*14}`} />)}
        {motif === 'rays' && Array.from({length:32},(_,i)=><path key={i} d="M120 33V7" transform={`rotate(${i*11.25} 120 125)`} />)}
        {motif === 'grid' && Array.from({length:12},(_,i)=><path key={i} d={`M${i*24} 0V250M0 ${i*24}H240`} />)}
        <circle cx="120" cy="125" r="60" /><circle cx="120" cy="125" r="66" />
      </g>
    </svg>
    <span className="card-print-rail" data-material-region="accent" />
    <span className="card-print-rail is-bottom" data-material-region="accent" />
  </span>;
}
