import { t, useLocale } from "../i18n";
import { useReactFlow } from '@xyflow/react';
import { LocateFixed, Scan, Search, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { apiErrorMessage, worldApi } from '../api/client';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useWorldStore } from '../state/worldStore';
import type { MinisterChat, MinisterWorldView, MinisterProposal } from '../types/minister';
import type { WorldCard } from '../types/world';
import { MinisterConversation } from './MinisterConversation';
import { ModelSelect } from './ModelSelect';

export function MinisterReviews({ card }: { card: WorldCard }) {
  useLocale();
  const [proposals, setProposals] = useState<MinisterProposal[]>([]);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState<string>();
  const event = useWorldStore(s => s.events.find(item => item.node_id === card.id && item.type === 'minister_review')?.id);
  const live = useWorldStore(s => s.socketState);
  useEffect(() => {
    let active = true;
    const refresh = () => void worldApi.getMinisterProposals(card.id).then(result => { if (active) setProposals(result); })
      .catch(reason => { if (active) setError(apiErrorMessage(reason)); });
    refresh();
    const timer = window.setInterval(refresh, 15000); // Also expire reviews while idle/offline.
    return () => { active = false; window.clearInterval(timer); };
  }, [card.id, event, live]);
  const decide = async (proposal: MinisterProposal, approve: boolean) => {
    setBusy(proposal.id); setError(undefined);
    try {
      const result = await worldApi.decideMinisterProposal(card.id, proposal.id, approve);
      setProposals(current => current.map(item => item.id === proposal.id ? { ...item, status: result.status as MinisterProposal['status'] } : item));
      if (approve && result.status === 'applied') {
        // The host button approves only this proposal. The resumed conversation
        // must inspect again; later sensitive actions still need their own review.
        try {
          const chat = await worldApi.openMinisterChat(card.id);
          await worldApi.postConversationMessage(chat.conversation_id, chat.session_id, {
            content: 'I confirmed the proposed changes. Inspect the current canvas and continue the remaining task.',
            mention_agent_ids: [card.id], message_id: crypto.randomUUID(),
          });
        } catch {
          setError(t("The confirmed changes were applied. Send “continue” when Minister is ready to resume."));
        }
      }
    } catch (reason) { setError(apiErrorMessage(reason)); }
    finally { setBusy(undefined); }
  };
  const pending = proposals.filter(item => item.status === 'pending');
  const latest = proposals.at(-1);
  if (!pending.length && !error && !latest) return null;
  return <div className="minister-reviews" aria-label={t("Minister confirmations")}>
    {pending.map(proposal => <section key={proposal.id} className="minister-review" aria-label={t("Review canvas changes")}>
      <strong>{t("Confirm these changes")}</strong>
      {proposal.reasons.map(reason => <p key={reason}>{reason}</p>)}
      <ul>{proposal.changes.map((change, i) => <li key={i}>{change.action === 'delete' ? t("Delete") : change.action === 'create' ? t("Create") : t("Update")} <strong>{change.name}</strong>
        {change.configuration && <dl>{Object.entries(change.configuration).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>)}</dl>}
      </li>)}</ul>
      {proposal.affected_cards.length > 0 && <p>{t("Affected:")} {proposal.affected_cards.map(item => item.name).join(', ')}</p>}
      {proposal.connections.map((edge, i) => <p key={i}>{edge.action === 'remove' ? t("Remove access") : t("Grant access")}: {proposal.affected_cards.find(card => card.id === edge.source)?.name ?? t("New card")} → {proposal.affected_cards.find(card => card.id === edge.target)?.name ?? t("New card")}. {edge.description}</p>)}
      {!!proposal.running_resources.length && <p>{proposal.running_resources.length} {t("active run(s) may be affected. Lifecycle checks still apply.")}</p>}
      {proposal.resources.map((resource, i) => <p key={i}>{resource.name ?? resource.kind}{resource.status ? ` (${resource.status})` : ''}{resource.size_bytes !== undefined ? t(" · {v0} bytes of stored content", { v0: String(resource.size_bytes) }) : ''}{resource.sessions ? t(" · Sessions: {v0}", { v0: String(resource.sessions.map(session => session.title).join(', ')) }) : ''}{resource.workspace_root ? t(" · Host folder: {v0}", { v0: String(resource.workspace_root) }) : ''}{resource.runtime ? t(" · Runtime: {v0}", { v0: String(resource.runtime) }) : ''}</p>)}
      <div><button type="button" disabled={!!busy} onClick={() => void decide(proposal, false)}>{t("Reject")}</button>
        <button type="button" disabled={!!busy} onClick={() => void decide(proposal, true)}>{t("Confirm changes")}</button></div>
    </section>)}
    {!pending.length && latest && <p role="status">{latest.status === 'applied' ? t("Confirmed changes applied.") : latest.status === 'rejected' ? t("Proposal rejected; no changes applied.") : latest.status === 'failed' ? t("Proposal could not be applied. Inspect current state before proposing again.") : t("Applying confirmed changes…")}</p>}
    {error && <p role="alert" className="minister-error">{error}</p>}
  </div>;
}

export function MinisterNearby({ card }: { card: WorldCard }) {
  useLocale();
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [view, setView] = useState<MinisterWorldView>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  const graphEvent = useWorldStore(s => s.events.find(event => /^(card_|edge_|nodes_generated)/.test(event.type))?.id);
  const socket = useWorldStore(s => s.socketState);
  const { setCenter, getZoom } = useReactFlow();
  useEffect(() => {
    let current = true;
    setLoading(true);
    const timer = window.setTimeout(() => {
      void worldApi.getMinisterWorld(card.id, query, offset).then(result => {
        if (current) { setView(result); setError(undefined); }
      }).catch(reason => { if (current) setError(apiErrorMessage(reason)); })
        .finally(() => { if (current) setLoading(false); });
    }, 150);
    return () => { current = false; clearTimeout(timer); };
  }, [card.id, card.position.x, card.position.y, card.config.control_radius, query, offset, graphEvent, socket]);
  return <div className="minister-nearby">
    <label className="minister-search"><Search size={15} /><input aria-label={t("Search nearby cards")} placeholder={t("Name, type or card ID")} value={query}
      onChange={event => { setQuery(event.target.value); setOffset(0); }} /></label>
    <div className="minister-search-summary" role="status">{loading ? t("Looking around…") : t("{v0} cards in this circle", { v0: String(view?.total ?? 0) })}</div>
    {error && <p className="minister-error" role="alert">{error}</p>}
    <div className="minister-results nowheel">
      {!loading && !view?.nodes.length && <p>{t("No matching cards inside the circle.")}</p>}
      {view?.nodes.map(node => <button type="button" key={node.id} title={`${node.name} · ${node.id}`} onClick={() => {
        useWorldStore.getState().selectCards([node.id]);
        setCenter(node.position.x + node.size.width / 2, node.position.y + node.size.height / 2, { zoom: Math.max(getZoom(), 0.65), duration: 300 });
      }}><span><strong>{node.name}</strong><small>{node.type} · {Math.round(node.position.x)}, {Math.round(node.position.y)}</small></span><LocateFixed size={16} /></button>)}
    </div>
    <div className="minister-pager">
      <button type="button" disabled={!offset || loading} onClick={() => setOffset(Math.max(0, offset - 40))}>{t("Previous")}</button>
      <button type="button" disabled={view?.next_offset == null || loading} onClick={() => setOffset(view!.next_offset!)}>{t("Next")}</button>
    </div>
  </div>;
}

export function MinisterPanel({ card, radius, setRadius, saveRadius }: { card: WorldCard; radius: number; setRadius: (value: number) => void; saveRadius: () => void }) {
  useLocale();
  const [tab, setTab] = useState<"talk" | "nearby">("talk");
  const [chat, setChat] = useState<MinisterChat>();
  const [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  const update = useWorldStore(s => s.updateCard);
  useEffect(() => {
    let current = true;
    void worldApi.openMinisterChat(card.id).then(result => { if (current) { setChat(result); setError(undefined); } })
      .catch(reason => { if (current) setError(apiErrorMessage(reason)); });
    return () => { current = false; };
  }, [card.id, attempt]);
  return <section className="minister-panel nodrag nopan nowheel" aria-label={t("{v0} controls", { v0: String(card.name) })} onPointerDown={event => event.stopPropagation()} onMouseDown={event => event.stopPropagation()}>
    <header className="minister-panel-header"><Scan size={18} /><input aria-label={t("Minister name")} key={card.name} defaultValue={card.name} maxLength={200}
      onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); }}
      onBlur={event => { const name = event.target.value.trim(); if (name && name !== card.name) void update(card.id, { name }); }} />
      <button type="button" aria-label={t("Close Minister")} onClick={() => useNodeSurfaceStore.getState().dismiss(card.id)}><X size={17} /></button></header>
    <div className="minister-settings">
      <label>{t("Radius")} <input aria-label={t("Control radius")} type="number" min={200} max={3000} step={50} value={radius}
        onChange={event => setRadius(Number(event.target.value))} onBlur={saveRadius}
        onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); }} /></label>
      <label className="minister-edit-switch"><input type="checkbox" checked={card.config.allow_canvas_edits === true}
        onChange={event => void update(card.id, { config: { allow_canvas_edits: event.target.checked } })} /> {t("Allow canvas edits")}</label>
    </div>
    <details className="minister-model"><summary>{t("Model & actions")}</summary><ModelSelect value={String(card.config.model ?? "")} onChange={model => void update(card.id, { config: { model } })} />
      <p>{t("Create Agents and other cards, configure and arrange this area, and manage connections, groups and attachments. Destructive or sensitive changes need your confirmation.")}</p></details>
    <MinisterReviews card={card} />
    <nav className="minister-tabs" aria-label={t("Minister views")}><button type="button" aria-pressed={tab === "talk"} onClick={() => setTab("talk")}>{t("Conversation")}</button>
      <button type="button" aria-pressed={tab === "nearby"} onClick={() => setTab("nearby")}>{t("Nearby cards")}</button>
      <small>{card.config.allow_canvas_edits ? t("Edits enabled") : t("Inspect only")}</small></nav>
    {tab === "nearby" ? <MinisterNearby card={card} /> : chat ? <MinisterConversation card={card} chat={chat} />
      : <div className="minister-loading" role={error ? "alert" : "status"}>{error ?? t("Opening conversation…")}
        {error && <button type="button" className="minister-text-button" onClick={() => { setError(undefined); setAttempt(value => value + 1); }}>{t("Retry")}</button>}</div>}
  </section>;
}

