import { Check, FileClock, Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useWorldStore } from "../state/worldStore";
import type { ModificationRecord, WorldCard } from "../types/world";
import type { NodeSurfaceLevel } from "../state/nodeSurfaces";
import { RelationshipList } from "./CardUtilities";
import { worldApi, apiErrorMessage } from "../api/client";

export function TextCardBody({ card, level }: { card: WorldCard; level: NodeSurfaceLevel }) {
  const saveText = useWorldStore((state) => state.saveText);
  const [content, setContent] = useState("");
  const [saveState, setSaveState] = useState<"saved" | "dirty" | "saving">("saved");
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState<number>();
  const [history, setHistory] = useState<ModificationRecord[]>([]);
  const [reload, setReload] = useState(0);
  const draft = useRef({ content: "", dirty: false });
  const saving = useRef(false);

  useEffect(() => {
    if ((level !== "inspector" && level !== "workspace") || card.ephemeral) return;
    let active = true;
    void worldApi.getText(card.id).then(document => {
      if (!active) return;
      setHistory((document.history ?? []) as ModificationRecord[]);
      setReady(true);
      if (draft.current.dirty || saving.current) return;
      draft.current.content = document.content;
      setContent(document.content);
      setRevision(document.revision);
      setSaveState("saved");
      setError("");
    }).catch(reason => { if (active) setError(apiErrorMessage(reason)); });
    return () => { active = false; };
  }, [card.ephemeral, card.id, card.config.revision, level, reload]);
  const filename = String(card.config.filename ?? `${card.name}.txt`);

  const performSave = async () => {
    if (!ready || !draft.current.dirty || saving.current) return;
    saving.current = true;
    const submitted = draft.current.content;
    setSaveState("saving");
    const saved = await saveText(card.id, submitted, revision);
    saving.current = false;
    if (saved) {
      setRevision(Number(useWorldStore.getState().cards.find(item => item.id === card.id)?.config.revision));
      draft.current.dirty = draft.current.content !== submitted;
      setError("");
      setReload(value => value + 1);
    } else {
      setError("保存失败；草稿已保留。若正文被其他参与者更新，请先核对新版本，避免覆盖。");
    }
    setSaveState(draft.current.dirty ? "dirty" : "saved");
  };

  return (
    <div className="expanded-stack">
      <div className="resource-banner">
        <div>
          <span>Managed resource</span>
          <strong>{filename}</strong>
        </div>
        <div className={`save-state save-state--${saveState}`}>
          {saveState === "saved" ? <Check size={12} /> : null}
          {ready ? saveState : "Loading…"}
        </div>
      </div>

      {error && <p role="alert">{error}</p>}
      <label className="field-label text-editor-label">
        <span>Contents</span>
        <textarea
          className="text-editor"
          value={content}
          disabled={!ready}
          spellCheck
          onChange={(event) => {
            draft.current = { content: event.target.value, dirty: true };
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
        <span id={`text-save-state-${card.id}`}>{content.length.toLocaleString()} characters · r{revision ?? "—"}</span>
        <button
          type="button"
          className="primary-button"
          onClick={() => void performSave()}
          disabled={!ready || saveState !== "dirty"}
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
