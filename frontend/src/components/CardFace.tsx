import type { HTMLAttributes, ReactNode } from "react";
import { CardFinishLayer } from "../cards/CardFinishLayer";
import { useCardFinish } from "../cards/useCardFinish";
import { normalizeCardFinish, type CardFinish } from "../cards/cardFinish";
import "./cardFace.css";
import "./cardTilt.css";

/** One compact card surface shared by the Library and the active hand. */
export function CardStock({ className = "", finish, quality = "thumbnail", reveal = false, children,
  onPointerEnter, onPointerMove, onPointerLeave, onPointerCancel, ...props }: HTMLAttributes<HTMLSpanElement> & {
  finish?: CardFinish; quality?: "thumbnail" | "standard" | "showcase"; reveal?: boolean;
}) {
  const material = useCardFinish(finish, quality, true);
  return <span {...props} className={`card-stock card-stock--compact card-finish-surface ${className}`}
    data-finish={finish === undefined ? undefined : normalizeCardFinish(finish)}
    onPointerEnter={event => { material.onPointerEnter?.(event); onPointerEnter?.(event); }}
    onPointerMove={event => { material.onPointerMove?.(event); onPointerMove?.(event); }}
    onPointerLeave={event => { material.onPointerLeave?.(); onPointerLeave?.(event); }}
    onPointerCancel={event => { material.onPointerCancel?.(); onPointerCancel?.(event); }}>
    {children}<CardFinishLayer finish={finish} quality={quality} reveal={reveal} />
  </span>;
}

/** Shared printed face for collected cards and the active hand. */
export function CardFace({ icon, label, description }: { icon: ReactNode; label: string; description: string }) {
  return <span className="card-face">
    <span className="card-face-corner" aria-hidden="true">{icon}</span>
    <span className="card-face-art" aria-hidden="true">
      <span className="card-face-engraving">
        <svg viewBox="0 0 180 150" fill="none" focusable="false">
          <path className="card-face-arch" d="M20 132V53C20 26 51 12 90 12s70 14 70 41v79M26 129V55c0-25 28-37 64-37s64 12 64 37v74" />
          <ellipse cx="90" cy="74" rx="47" ry="50" />
          <ellipse cx="90" cy="74" rx="41" ry="44" strokeDasharray="1 4" />
          <path d="M54 112C33 101 26 80 34 60m92 52c21-11 28-32 20-52M40 98l-10-3 4-10m106 13 10-3-4-10M34 77l-8-7 8-6m112 13 8-7-8-6M63 120l-7 5m61-5 7 5M77 132h26" />
          <path className="card-face-gem" d="m90 4 4 5-4 5-4-5Zm0 124 4 5-4 5-4-5ZM15 47l3 4-3 4-3-4Zm150 0 3 4-3 4-3-4Z" />
          <path d="M13 118v17h19m135-17v17h-19M45 28h-9v9m99-9h9v9" />
        </svg>
      </span>
      {icon}
    </span>
    <span className="card-face-copy"><strong>{label}</strong><small>{description}</small></span>
  </span>;
}
