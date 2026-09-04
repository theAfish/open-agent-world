import { AlertTriangle, Layers3, Link2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { summarizeLegionSelection } from "../state/legions";
import { useWorldStore } from "../state/worldStore";

export function LegionSelection() {
  const cards = useWorldStore((state) => state.cards);
  const stressCards = useWorldStore((state) => state.stressCards);
  const edges = useWorldStore((state) => state.edges);
  const catalog = useWorldStore((state) => state.catalog);
  const selectedCardIds = useWorldStore((state) => state.selectedCardIds);
  const positionCommitBusy = useWorldStore((state) => state.positionCommitBusy);
  const syncState = useWorldStore((state) => state.syncState);
  const createLegion = useWorldStore((state) => state.createLegion);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [captureIds, setCaptureIds] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const nameInput = useRef<HTMLInputElement>(null);
  const allCards = useMemo(() => [...cards, ...stressCards], [cards, stressCards]);
  const activeIds = dialogOpen ? captureIds : selectedCardIds;
  const selection = useMemo(
    () => summarizeLegionSelection(allCards, edges, activeIds, catalog),
    [activeIds, allCards, catalog, edges],
  );

  const closeDialog = () => {
    if (busy) return;
    setDialogOpen(false);
    setCaptureIds([]);
    setName("");
    setDescription("");
  };

  const openDialog = () => {
    setCaptureIds([...selectedCardIds]);
    setName(selection.cards.length > 0 ? `${selection.cards[0].name} Legion` : "New Legion");
    setDescription("");
    setDialogOpen(true);
  };

  useEffect(() => {
    if (!dialogOpen) return undefined;
    window.setTimeout(() => nameInput.current?.focus(), 0);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) closeDialog();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, dialogOpen]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      !name.trim()
      || selection.unsupportedCards.length > 0
      || selection.unsupportedEdges.length > 0
      || busy
    ) return;
    setBusy(true);
    const created = await createLegion({
      name,
      description,
      nodeIds: captureIds,
    });
    setBusy(false);
    if (created) closeDialog();
  };

  if (selection.cards.length < 2 && !dialogOpen) return null;

  const unavailableCards = selection.unsupportedCards.map((card) => {
    const definition = catalog.node_types.find((item) => item.id === card.type);
    const owner = card.ephemeral ? "synthetic card" : definition?.plugin_id ?? "missing node definition";
    return `${card.name} (${owner})`;
  });
  const unavailableRelationships = [...new Set(selection.unsupportedEdges.map((edge) => {
    const definition = catalog.relationships.find((item) => item.id === edge.relationship);
    return `${definition?.label ?? edge.relationship} (${definition?.plugin_id ?? "missing relationship definition"})`;
  }))];
  const pluginBlockedDetail = [
    ...(unavailableCards.length > 0 ? [`Cards: ${unavailableCards.join(", ")}.`] : []),
    ...(unavailableRelationships.length > 0
      ? [`Relationships: ${unavailableRelationships.join(", ")}.`]
      : []),
  ].join(" ");
  const firstBlockedLabel = unavailableCards[0] ?? unavailableRelationships[0];
  const canCollect = selection.cards.length >= 2
    && selection.unsupportedCards.length === 0
    && selection.unsupportedEdges.length === 0
    && syncState !== "offline";
  const canInspect = selection.cards.length >= 2 && syncState !== "offline";

  return (
    <>
      {!dialogOpen ? (
        <aside className="legion-selection-bar" aria-label="Selected formation actions" data-testid="legion-selection-bar">
          <span className="legion-selection-mark" aria-hidden="true"><Layers3 size={15} /></span>
          <div className="legion-selection-summary">
            <strong>{selection.cards.length} selected</strong>
            <span>
              <Link2 size={11} /> {selection.internalEdges.length} internal {selection.internalEdges.length === 1 ? "link" : "links"}
              {selection.externalEdges.length > 0
                ? ` · ${selection.externalEdges.length} external ${selection.externalEdges.length === 1 ? "link" : "links"} excluded`
                : ""}
            </span>
          </div>
          {firstBlockedLabel ? (
            <span className="legion-selection-warning" title={pluginBlockedDetail}>
              <AlertTriangle size={13} /> Plugin blocked: {firstBlockedLabel}
            </span>
          ) : null}
          <button
            type="button"
            className="primary-button"
            onClick={openDialog}
            disabled={!canInspect}
            title={pluginBlockedDetail
              ? pluginBlockedDetail
              : "Save this induced subgraph as a reusable Legion card"}
          >
            <Layers3 size={14} /> {positionCommitBusy ? "Saving layout…" : "Save as Legion"}
          </button>
        </aside>
      ) : null}

      {dialogOpen ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeDialog();
          }}
        >
          <form className="legion-dialog" role="dialog" aria-modal="true" aria-labelledby="legion-dialog-title" onSubmit={submit}>
            <header>
              <div className="dialog-icon"><Layers3 size={18} /></div>
              <div>
                <span>Reusable formation</span>
                <h2 id="legion-dialog-title">Create a Legion card</h2>
              </div>
              <button type="button" className="icon-button" onClick={closeDialog} disabled={busy} aria-label="Close Legion creator">
                <X size={16} />
              </button>
            </header>

            <div className="legion-dialog-topology">
              <div><strong>{selection.cards.length}</strong><span>cards</span></div>
              <i aria-hidden="true" />
              <div><strong>{selection.internalEdges.length}</strong><span>internal links</span></div>
              <i aria-hidden="true" />
              <div><strong>{new Set(selection.cards.map((card) => card.type)).size}</strong><span>node types</span></div>
            </div>

            {selection.externalEdges.length > 0 ? (
              <p className="legion-dialog-note">
                <AlertTriangle size={13} /> {selection.externalEdges.length} connection{selection.externalEdges.length === 1 ? "" : "s"} leaving the selection will not be included.
              </p>
            ) : null}
            {selection.unsupportedCards.length > 0 ? (
              <p className="legion-dialog-note is-error">
                <AlertTriangle size={13} /> Plugin-blocked cards: {unavailableCards.join(", ")}.
              </p>
            ) : null}
            {selection.unsupportedEdges.length > 0 ? (
              <p className="legion-dialog-note is-error">
                <AlertTriangle size={13} /> Plugin-blocked relationships: {unavailableRelationships.join(", ")}.
              </p>
            ) : null}

            <label className="legion-dialog-field">
              <span>Legion name</span>
              <input ref={nameInput} value={name} onChange={(event) => setName(event.target.value)} maxLength={120} required />
            </label>
            <label className="legion-dialog-field">
              <span>Description <small>optional</small></span>
              <textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={500} rows={3} placeholder="What this formation is designed to do" />
            </label>

            <footer>
              <span>{positionCommitBusy ? "Waiting for the latest card positions to finish saving…" : "The original cards remain on the canvas."}</span>
              <button type="button" className="secondary-button" onClick={closeDialog} disabled={busy}>Cancel</button>
              <button type="submit" className="primary-button" disabled={!canCollect || !name.trim() || busy}>
                <Layers3 size={14} /> {busy ? "Collecting…" : "Collect Legion"}
              </button>
            </footer>
          </form>
        </div>
      ) : null}
    </>
  );
}
