import { Boxes, LibraryBig } from "lucide-react";
import { useWorldStore } from "../state/worldStore";
import { useCardLibrary } from "../state/cardLibrary";

export function EmptyWorld() {
  const cards = useWorldStore(state => state.cards);
  const stressCards = useWorldStore(state => state.stressCards);
  const syncState = useWorldStore(state => state.syncState);
  if (cards.length || stressCards.length || syncState === "loading") return null;
  return <section className="empty-world" aria-label="Empty world">
    <div className="empty-world-symbol" aria-hidden="true"><Boxes size={24} /><i /><i /><i /></div>
    <span className="empty-eyebrow">Uncharted terrain</span>
    <h1>Your world is open.</h1>
    <p>Open a pack, collect its cards, and build a deck. Their connections become real capabilities.</p>
    <div><button type="button" className="primary-button" onClick={useCardLibrary.getState().show}><LibraryBig size={15} /> Explore your packs</button></div>
    <small>Drag cards from your active deck to choose their exact position.</small>
  </section>;
}
