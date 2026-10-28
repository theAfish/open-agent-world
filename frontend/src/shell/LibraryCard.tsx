import { useState, type CSSProperties, type ReactNode } from "react";
import { CardFace, CardStock } from "../components/CardFace";
import { CatalogIcon } from "../components/CatalogIcon";
import type { LibraryCard as CollectedCard } from "./libraryCatalog";

export function LibraryCard({ selected, included, color, children }: { selected: boolean; included: boolean; color: string; children: ReactNode }) {
  return <article className={`library-card ${selected ? "is-selected" : ""} ${included ? "is-in-deck" : ""}`} style={{ "--collection-color": color } as CSSProperties}>
    {children}
  </article>;
}

/** A single large material surface; the collection grid stays lightweight. */
export function LibraryCardPreview({ card }: { card: CollectedCard }) {
  const [failedArtwork, setFailedArtwork] = useState<string | null>(null);
  return <CardStock className="library-card-preview" size="standard" quality="showcase" finish={card.finish}
    style={{ "--collection-color": card.definition?.color ?? "#78967b" } as CSSProperties}>
    {card.artworkUrl && failedArtwork !== card.artworkUrl
      ? <img className="library-formation-cover" src={card.artworkUrl} alt={card.label} draggable={false} onError={() => setFailedArtwork(card.artworkUrl!)} />
      : <CardFace definition={card.definition} icon={<CatalogIcon definition={card.definition} />} label={card.label} description={card.description} />}
  </CardStock>;
}
