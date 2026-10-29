import { useState, type ComponentProps, type CSSProperties, type ReactNode } from "react";
import { CardFace, CardStock } from "../components/CardFace";
import { CatalogIcon } from "../components/CatalogIcon";
import { Layers3 } from "lucide-react";
import type { LibraryCard as CollectedCard } from "./libraryCatalog";

export function LibraryCard({ selected, included, color, children }: { selected: boolean; included: boolean; color: string; children: ReactNode }) {
  return <article className={`library-card ${selected ? "is-selected" : ""} ${included ? "is-in-deck" : ""}`} style={{ "--collection-color": color } as CSSProperties}>
    {children}
  </article>;
}

/** The same printed card in the collection, inspector and active deck. */
export function LibraryCardSurface({ card, style, children, ...props }: Omit<ComponentProps<typeof CardStock>, 'finish'> & {
  card: Pick<CollectedCard, 'kind' | 'definition' | 'label' | 'description' | 'artworkUrl' | 'finish'>;
}) {
  const [failedArtwork, setFailedArtwork] = useState<string | null>(null);
  return <CardStock {...props} finish={card.finish}
    style={{ "--collection-color": card.definition?.color ?? "#78967b", ...style } as CSSProperties}>
    {card.artworkUrl && failedArtwork !== card.artworkUrl
      ? <img className="card-face-cover" data-material-layer="artwork" data-material-region="artwork" src={card.artworkUrl} alt={card.label} draggable={false} onError={() => setFailedArtwork(card.artworkUrl!)} />
      : <CardFace definition={card.definition} icon={card.kind === 'legion' ? <Layers3 /> : <CatalogIcon definition={card.definition} />}
        label={card.label} description={card.description} imageAlt={card.label} />}
    {children}
  </CardStock>;
}
