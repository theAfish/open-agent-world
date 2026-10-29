import { createContext, useContext, useMemo, useState, type CSSProperties, type HTMLAttributes, type ReactNode } from "react";
import { cardFaceDesign, type CardFaceTone, type CardFaceVariant } from './cardFaceDesign';
import type { NodeTypeCatalogItem } from '../types/world';
import { CardFinishLayer } from "../cards/CardFinishLayer";
import type { CardMaterialOptions } from '../cards/CardMaterialCanvas';
import { productionForFinish, stockStyle } from '../cards/cardProduction';
import { CardPrintArt } from './CardPrintArt';
import { CardFaceIllustration } from './CardFaceIllustration';
import { t } from '../i18n';
import { useCardFinish } from "../cards/useCardFinish";
import { normalizeCardFinish, type CardFinish } from "../cards/cardFinish";
import "./cardFace.css";
import "./cardTilt.css";

const ProductionContext = createContext(productionForFinish());

/** One compact card surface shared by the Library and the active hand. */
export function CardStock({ className = "", finish, quality = "thumbnail", size = 'compact', reveal = false, finishVisible = true, children, materialOptions,
  onPointerEnter, onPointerMove, onPointerLeave, onPointerCancel, style, ...props }: HTMLAttributes<HTMLSpanElement> & {
  finish?: CardFinish; quality?: "thumbnail" | "standard" | "showcase"; size?: 'compact' | 'standard'; reveal?: boolean; finishVisible?: boolean;
  materialOptions?: CardMaterialOptions;
}) {
  const controlledPose = materialOptions?.pose !== undefined;
  const material = useCardFinish(controlledPose ? 'normal' : finish, quality, !controlledPose);
  const production = useMemo(() => materialOptions?.production ?? productionForFinish(normalizeCardFinish(finish)), [materialOptions?.production, finish]);
  return <ProductionContext.Provider value={production}><span {...props} className={`card-stock card-stock--${size} card-finish-surface ${className}`}
    data-card-stock={production.stock.type} style={{ ...stockStyle(production), ...style } as CSSProperties}
    data-material-layer="substrate"
    data-finish={finish === undefined ? undefined : normalizeCardFinish(finish)}
    onPointerEnter={event => { material.onPointerEnter?.(event); onPointerEnter?.(event); }}
    onPointerMove={event => { material.onPointerMove?.(event); onPointerMove?.(event); }}
    onPointerLeave={event => { material.onPointerLeave?.(); onPointerLeave?.(event); }}
    onPointerCancel={event => { material.onPointerCancel?.(); onPointerCancel?.(event); }}>
    {children}{finishVisible && <CardFinishLayer finish={finish} quality={quality} reveal={reveal} {...materialOptions} />}
  </span></ProductionContext.Provider>;
}

export interface CardFaceProps {
  definition?: Pick<NodeTypeCatalogItem, 'id' | 'traits' | 'card_face'> & Partial<Pick<NodeTypeCatalogItem, 'deck_label' | 'icon' | 'icon_url'>>;
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
export function CardFace({ definition, icon, label, description, variant: variantOverride,
  tone: toneOverride, imageUrl = definition?.card_face?.image_url,
  imageAlt = '', badge, artwork }: CardFaceProps) {
  const [failedImage, setFailedImage] = useState<string>();
  const production = useContext(ProductionContext);
  const design = cardFaceDesign(definition);
  const variant = variantOverride ?? design.variant, tone = toneOverride ?? design.tone;
  // Authored faces retain their chosen layout, artwork and print treatment.
  const archival = !definition?.card_face && variantOverride === undefined && toneOverride === undefined && !imageUrl && !artwork;
  const showImage = variant === 'image' && imageUrl && failedImage !== imageUrl;
  return <span className={`card-face card-face--${variant}`} data-variant={variant} data-tone={tone} data-design={archival ? 'archival' : undefined}>
    <span className="card-face-edition" data-material-layer="protected"><b>OAW</b><span>{archival ? definition?.deck_label && t(definition.deck_label) : definition?.id.split('.').pop()?.replaceAll('_', ' ') ?? 'COLLECTION'}</span></span>
    {!archival && badge && <span className="card-face-badge" data-material-layer="protected" data-material-region="text" title={badge}>{badge}</span>}
    <span className={`card-face-art ${showImage ? 'has-image' : ''}`} data-material-layer="artwork" data-material-region="artwork">
      {!archival && <CardPrintArt motif={production.print.motif} />}
      {artwork}
      {showImage ? <img className="card-face-image" src={imageUrl} alt={imageAlt} draggable={false}
        decoding="async" loading="lazy" onError={() => setFailedImage(imageUrl)} />
        : archival ? <CardFaceIllustration definition={definition} fallback={icon} />
        : <span className="card-face-symbol" data-material-layer="protected" data-material-region="icon" aria-hidden="true">{icon}</span>}
    </span>
    <span className="card-face-copy" data-material-layer="protected" data-material-region="text"><strong title={label}>{label}</strong>
      {!archival && description && <small>{description}</small>}
      {!archival && <span className="card-face-detail" aria-hidden="true"><i />OPEN AGENT WORLD<span>O / W</span></span>}
    </span>
  </span>;
}
