import { Bot, Boxes, Plus } from "lucide-react";
import { useState } from "react";
import { viewportCenterToWorld } from "../state/chunks";
import { useWorldStore } from "../state/worldStore";

export function EmptyWorld() {
  const cards = useWorldStore((state) => state.cards);
  const stressCards = useWorldStore((state) => state.stressCards);
  const syncState = useWorldStore((state) => state.syncState);
  const viewport = useWorldStore((state) => state.viewport);
  const createCard = useWorldStore((state) => state.createCard);
  const [seeding, setSeeding] = useState(false);
  if (cards.length > 0 || stressCards.length > 0 || syncState === "loading") return null;

  const createStarterWorld = async () => {
    setSeeding(true);
    const center = viewportCenterToWorld(viewport);
    await Promise.all([
      createCard("agent", { x: center.x - 440, y: center.y - 180 }),
      createCard("text", { x: center.x - 70, y: center.y - 260 }),
      createCard("image", { x: center.x - 30, y: center.y + 80 }),
      createCard("sandbox", { x: center.x + 360, y: center.y - 80 }),
    ]);
    setSeeding(false);
  };

  return (
    <section className="empty-world" aria-label="Empty world">
      <div className="empty-world-symbol" aria-hidden="true"><Boxes size={24} /><i /><i /><i /></div>
      <span className="empty-eyebrow">Uncharted terrain</span>
      <h1>Your world is open.</h1>
      <p>Place an agent, a resource, or a secure workplace. Their connections become real capabilities.</p>
      <div>
        <button type="button" className="primary-button" onClick={() => void createStarterWorld()} disabled={seeding || syncState === "offline"}>
          <Plus size={15} /> {seeding ? "Placing objects…" : "Place starter constellation"}
        </button>
        <button type="button" className="secondary-button" onClick={() => void createCard("agent")} disabled={syncState === "offline"}>
          <Bot size={15} /> Start with an agent
        </button>
      </div>
      <small>Tip: drag objects from the library to choose their exact position.</small>
    </section>
  );
}
