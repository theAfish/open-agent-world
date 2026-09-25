import { useWorkspaceAccess } from '../workspace/WorkspaceAccess';
import { t, useLocale } from "../i18n";
import { Check, FileClock, Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useWorldStore } from "../state/worldStore";
import type { ModificationRecord, WorldCard } from "../types/world";
import type { NodeSurfaceLevel } from "../state/nodeSurfaces";
import { surfaceDraftKey, useNodeSurfaceStore, useSurfaceDraft } from '../state/nodeSurfaces';
import { RelationshipList } from "./CardUtilities";
import { worldApi, apiErrorMessage } from "../api/client";

export function TextCardBody({ card, level }: { card: WorldCard; level: NodeSurfaceLevel }) {
  useLocale();
  const { deployed } = useWorkspaceAccess();
  const saveText = useWorldStore((state) => state.saveText);
  const draftKey = surfaceDraftKey(card.id, 'text');
  const [storedDraft, setStoredDraft] = useSurfaceDraft<{ content: string; revision?: number; saving?: boolean } | undefined>(draftKey, undefined);
  const [content, setContent] = useState(storedDraft?.content ?? '');
  const [saveState, setSaveState] = useState<"saved" | "dirty" | "saving">(storedDraft ? 'dirty' : 'saved');
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState<number | undefined>(storedDraft?.revision);
  const [history, setHistory] = useState<ModificationRecord[]>([]);
  const [reload, setReload] = useState(0);
  const draft = useRef({ content: storedDraft?.content ?? '', dirty: !!storedDraft });
  const saving = useRef(false);
  const previousStoredDraft = useRef(storedDraft);

  useEffect(() => {
    const previous = previousStoredDraft.current;
    previousStoredDraft.current = storedDraft;
    saving.current = !!storedDraft?.saving;
    if (!storedDraft) {
      if (previous) {
        draft.current = { content: previous.content, dirty: false };
        setContent(previous.content);
        setSaveState('saved');
        setReload(value => value + 1);
      }
      return;
    }
    draft.current = { content: storedDraft.content, dirty: true };
    setContent(storedDraft.content);
    setRevision(storedDraft.revision);
    setSaveState(storedDraft.saving ? 'saving' : 'dirty');
  }, [storedDraft]);

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
    if (deployed || !ready || !draft.current.dirty || saving.current) return;
    saving.current = true;
    const submitted = draft.current.content;
    setStoredDraft(current => current ? { ...current, saving: true } : current);
    setSaveState("saving");
    const saved = await saveText(card.id, submitted, revision);
    saving.current = false;
    if (saved) {
      const nextRevision = Number(useWorldStore.getState().cards.find(item => item.id === card.id)?.config.revision);
      setRevision(nextRevision);
      const latest = useNodeSurfaceStore.getState().drafts[draftKey];
      const current = latest ? JSON.parse(latest) as { content: string } : undefined;
      draft.current.dirty = !!current && current.content !== submitted;
      setStoredDraft(current && current.content !== submitted ? { content: current.content, revision: nextRevision } : undefined);
      setError("");
      setReload(value => value + 1);
    } else {
      setStoredDraft(current => current ? { ...current, saving: false } : current);
      setError(t("保存失败；草稿已保留。若正文被其他参与者更新，请先核对新版本，避免覆盖。"));
    }
    setSaveState(draft.current.dirty ? "dirty" : "saved");
  };

  return (
    <div className="expanded-stack">
      <div className="resource-banner">
        <div>
          <span>{t("Managed resource")}</span>
          <strong>{filename}</strong>
        </div>
        <div className={`save-state save-state--${saveState}`}>
          {saveState === "saved" ? <Check size={12} /> : null}
          {ready ? saveState : t("Loading…")}
        </div>
      </div>

      {error && <p role="alert">{error}</p>}
      <label className="field-label text-editor-label">
        <span>{t("Contents")}</span>
        <textarea
          className="text-editor"
          value={content}
          disabled={!ready}
          readOnly={deployed}
          spellCheck
          onChange={(event) => {
            draft.current = { content: event.target.value, dirty: true };
            setStoredDraft({ content: event.target.value, revision, saving: saving.current });
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
      {!deployed && <><div className="editor-actions">
        <span id={`text-save-state-${card.id}`}>{content.length.toLocaleString(useLocale.getState().locale)} {t("characters · r")}{revision ?? "—"}</span>
        <button
          type="button"
          className="primary-button"
          onClick={() => void performSave()}
          disabled={!ready || saveState !== "dirty"}
        >
          <Save size={14} /> {saveState === "saving" ? t("Saving…") : t("Save text")}
        </button>
      </div>

      <section className="card-section">
        <div className="section-heading"><span>{t("Relationships")}</span><small>{t("live permissions")}</small></div>
        <RelationshipList card={card} />
      </section>

      <section className="card-section history-section">
        <div className="section-heading"><span>{t("Modification history")}</span><small>{history.length} {t("entries")}</small></div>
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
        ) : <div className="mini-empty"><FileClock size={14} /><span>{t("History begins after the first save.")}</span></div>}
      </section></>}
    </div>
  );
}
