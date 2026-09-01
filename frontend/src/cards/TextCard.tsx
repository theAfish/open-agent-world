import { Check, FileClock, Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useWorldStore } from "../state/worldStore";
import type { ModificationRecord, WorldCard } from "../types/world";
import type { NodeSurfaceLevel } from "../state/nodeSurfaces";
import { RelationshipList } from "./CardUtilities";

export function TextCardBody({ card, level }: { card: WorldCard; level: NodeSurfaceLevel }) {
  const loadText = useWorldStore((state) => state.loadText);
  const saveText = useWorldStore((state) => state.saveText);
  const [content, setContent] = useState(String(card.config.content ?? ""));
  const [saveState, setSaveState] = useState<"saved" | "dirty" | "saving">("saved");
  const loaded = useRef(false);

  useEffect(() => {
    setContent(String(card.config.content ?? ""));
    setSaveState("saved");
  }, [card.config.content]);

  useEffect(() => {
    if ((level === "inspector" || level === "workspace") && !card.ephemeral && !loaded.current) {
      loaded.current = true;
      void loadText(card.id);
    }
  }, [card.ephemeral, card.id, level, loadText]);

  const history = Array.isArray(card.config.history)
    ? (card.config.history as ModificationRecord[])
    : [];
  const filename = String(card.config.filename ?? `${card.name}.txt`);

  const performSave = async () => {
    setSaveState("saving");
    const saved = await saveText(card.id, content);
    setSaveState(saved ? "saved" : "dirty");
  };

  return (
    <div className="expanded-stack nodrag nopan">
      <div className="resource-banner">
        <div>
          <span>Managed resource</span>
          <strong>{filename}</strong>
        </div>
        <div className={`save-state save-state--${saveState}`}>
          {saveState === "saved" ? <Check size={12} /> : null}
          {saveState}
        </div>
      </div>

      <label className="field-label text-editor-label">
        <span>Contents</span>
        <textarea
          className="text-editor"
          value={content}
          spellCheck
          onChange={(event) => {
            setContent(event.target.value);
            setSaveState("dirty");
          }}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "s") {
              event.preventDefault();
              void performSave();
            }
          }}
          aria-describedby={`text-save-state-${card.id}`}
        />
      </label>
      <div className="editor-actions">
        <span id={`text-save-state-${card.id}`}>{content.length.toLocaleString()} characters · r{Number(card.config.revision ?? 0)}</span>
        <button
          type="button"
          className="primary-button"
          onClick={() => void performSave()}
          disabled={saveState !== "dirty"}
        >
          <Save size={14} /> {saveState === "saving" ? "Saving…" : "Save text"}
        </button>
      </div>

      <section className="card-section">
        <div className="section-heading"><span>Relationships</span><small>live permissions</small></div>
        <RelationshipList card={card} />
      </section>

      <section className="card-section history-section">
        <div className="section-heading"><span>Modification history</span><small>{history.length} entries</small></div>
        {history.length > 0 ? (
          <ul className="history-list">
            {history.slice(-3).reverse().map((entry, index) => (
              <li key={`${entry.at}-${index}`}>
                <FileClock size={13} aria-hidden="true" />
                <span>{entry.summary}</span>
                <time dateTime={entry.at}>{new Date(entry.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
              </li>
            ))}
          </ul>
        ) : <div className="mini-empty"><FileClock size={14} /><span>History begins after the first save.</span></div>}
      </section>
    </div>
  );
}
