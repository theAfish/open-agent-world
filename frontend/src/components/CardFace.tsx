import { useState, type HTMLAttributes, type ReactNode } from "react";
import { cardFaceDesign, type CardFaceTone, type CardFaceVariant } from './cardFaceDesign';
import type { NodeTypeCatalogItem } from '../types/world';
import { CardFinishLayer } from "../cards/CardFinishLayer";
import { useCardFinish } from "../cards/useCardFinish";
import { normalizeCardFinish, type CardFinish } from "../cards/cardFinish";
import "./cardFace.css";
import "./cardTilt.css";

/** One compact card surface shared by the Library and the active hand. */
export function CardStock({ className = "", finish, quality = "thumbnail", size = 'compact', reveal = false, children,
  onPointerEnter, onPointerMove, onPointerLeave, onPointerCancel, ...props }: HTMLAttributes<HTMLSpanElement> & {
  finish?: CardFinish; quality?: "thumbnail" | "standard" | "showcase"; size?: 'compact' | 'standard'; reveal?: boolean;
}) {
  const material = useCardFinish(finish, quality, true);
  return <span {...props} className={`card-stock card-stock--${size} card-finish-surface ${className}`}
    data-material-layer="substrate"
    data-finish={finish === undefined ? undefined : normalizeCardFinish(finish)}
    onPointerEnter={event => { material.onPointerEnter?.(event); onPointerEnter?.(event); }}
    onPointerMove={event => { material.onPointerMove?.(event); onPointerMove?.(event); }}
    onPointerLeave={event => { material.onPointerLeave?.(); onPointerLeave?.(event); }}
    onPointerCancel={event => { material.onPointerCancel?.(); onPointerCancel?.(event); }}>
    {children}<CardFinishLayer finish={finish} quality={quality} reveal={reveal} />
  </span>;
}

export interface CardFaceProps {
  definition?: Pick<NodeTypeCatalogItem, 'id' | 'traits' | 'card_face'>;
  icon: ReactNode;
  label: string;
  description?: string;
  variant?: CardFaceVariant;
  tone?: CardFaceTone;
  imageUrl?: string | null;
  imageAlt?: string;
  badge?: string;
  /** Printed illustration, beneath the laminate and protected symbol. */
  artwork?: ReactNode;
}

/** A small family of layouts. Material, layout and colour are independent. */
export function CardFace({ definition, icon, label, description, variant = cardFaceDesign(definition).variant,
  tone = cardFaceDesign(definition).tone, imageUrl = definition?.card_face?.image_url,
  imageAlt = '', badge, artwork }: CardFaceProps) {
  const [failedImage, setFailedImage] = useState<string>();
  const showImage = variant === 'image' && imageUrl && failedImage !== imageUrl;
  return <span className={`card-face card-face--${variant}`} data-variant={variant} data-tone={tone}>
    {badge && <span className="card-face-badge" data-material-layer="protected" data-material-region="text" title={badge}>{badge}</span>}
    <span className={`card-face-art ${showImage ? 'has-image' : ''}`} data-material-layer="artwork" data-material-region="artwork">
      {artwork}
      {showImage ? <img className="card-face-image" src={imageUrl} alt={imageAlt} draggable={false}
        decoding="async" loading="lazy" onError={() => setFailedImage(imageUrl)} />
        : <span className="card-face-symbol" data-material-layer="protected" data-material-region="icon" aria-hidden="true">{icon}</span>}
    </span>
    <span className="card-face-copy" data-material-layer="protected" data-material-region="text"><strong title={label}>{label}</strong>
      {description && <small>{description}</small>}
      <span className="card-face-detail" aria-hidden="true" />
    </span>
  </span>;
}
