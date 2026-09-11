import { Handle, Position, useInternalNode, useReactFlow, type NodeProps } from "@xyflow/react";
import { ArrowUp, CircleStop, LocateFixed, Scan, Search, X } from "lucide-react";
import { memo, useEffect, useRef, useState, type CSSProperties } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import { useConversationTimeline } from "../state/useConversationTimeline";
import { useWorldStore } from "../state/worldStore";
import type { MinisterChat, MinisterWorldView, MinisterProposal } from "../types/minister";
import type { WorldCard } from "../types/world";
import { MarkdownMessage } from "./MarkdownMessage";
import { ModelSelect } from "./ModelSelect";
import type { CanvasNode } from "./types";
import "./minister.css";

export const MINISTER_TYPE = "core.minister";
const clampRadius = (value: number) => Math.max(200, Math.min(3000, Math.round(value / 50) * 50));

export function ministerToolSummary(content: string, kind: string): string {
  const name = content.split("\n")[0].replace(/^(Using|Finished) /, "");
  const labels: Record<string, [string, string]> = {
    canvas_inspect: ["Checking this area", "Area checked"], canvas_create: ["Creating a card", "Card created"],
    canvas_move: ["Moving a card", "Card moved"], canvas_rename: ["Renaming a card", "Card renamed"],
    canvas_connect: ["Connecting cards", "Cards connected"], canvas_disconnect: ["Disconnecting cards", "Cards disconnected"],
    canvas_update: ["Updating cards", "Cards updated"], canvas_delete: ["Reviewing deletion", "Deletion reviewed"],
    canvas_organize: ["Organizing cards", "Cards organized"],
  };
  const label = labels[name];
  if (!label) return "Canvas activity";
  try {
    const result = JSON.parse(content.slice(content.indexOf("\n\n") + 2));
    if (result?.ok === false) return `${label[0]}: needs attention`;
    if (result?.status === 'confirmation_required' || result?.result?.status === 'confirmation_required') return 'Waiting for your confirmation';
  } catch { /* Activity may have no structured detail. */ }
  return label[kind === "tool_started" ? 0 : 1];
}

export function MinisterReviews({ card }: { card: WorldCard }) {
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
          setError('The confirmed changes were applied. Send “continue” when Minister is ready to resume.');
        }
      }
    } catch (reason) { setError(apiErrorMessage(reason)); }
    finally { setBusy(undefined); }
  };
  const pending = proposals.filter(item => item.status === 'pending');
  const latest = proposals.at(-1);
  if (!pending.length && !error && !latest) return null;
  return <div className="minister-reviews" aria-label="Minister confirmations">
    {pending.map(proposal => <section key={proposal.id} className="minister-review" aria-label="Review canvas changes">
      <strong>Confirm these changes</strong>
      {proposal.reasons.map(reason => <p key={reason}>{reason}</p>)}
      <ul>{proposal.changes.map((change, i) => <li key={i}>{change.action === 'delete' ? 'Delete' : change.action === 'create' ? 'Create' : 'Update'} <strong>{change.name}</strong>
        {change.configuration && <dl>{Object.entries(change.configuration).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>)}</dl>}
      </li>)}</ul>
      {proposal.affected_cards.length > 0 && <p>Affected: {proposal.affected_cards.map(item => item.name).join(', ')}</p>}
      {proposal.connections.map((edge, i) => <p key={i}>{edge.action === 'remove' ? 'Remove access' : 'Grant access'}: {proposal.affected_cards.find(card => card.id === edge.source)?.name ?? 'New card'} → {proposal.affected_cards.find(card => card.id === edge.target)?.name ?? 'New card'}. {edge.description}</p>)}
      {!!proposal.running_resources.length && <p>{proposal.running_resources.length} active run(s) may be affected. Lifecycle checks still apply.</p>}
      {proposal.resources.map((resource, i) => <p key={i}>{resource.name ?? resource.kind}{resource.status ? ` (${resource.status})` : ''}{resource.size_bytes !== undefined ? ` · ${resource.size_bytes} bytes of stored content` : ''}{resource.sessions ? ` · Sessions: ${resource.sessions.map(session => session.title).join(', ')}` : ''}{resource.workspace_root ? ` · Host folder: ${resource.workspace_root}` : ''}{resource.runtime ? ` · Runtime: ${resource.runtime}` : ''}</p>)}
      <div><button type="button" disabled={!!busy} onClick={() => void decide(proposal, false)}>Reject</button>
        <button type="button" disabled={!!busy} onClick={() => void decide(proposal, true)}>Confirm changes</button></div>
    </section>)}
    {!pending.length && latest && <p role="status">{latest.status === 'applied' ? 'Confirmed changes applied.' : latest.status === 'rejected' ? 'Proposal rejected; no changes applied.' : latest.status === 'failed' ? 'Proposal could not be applied. Inspect current state before proposing again.' : 'Applying confirmed changes…'}</p>}
    {error && <p role="alert" className="minister-error">{error}</p>}
  </div>;
}

function MinisterConversation({ card, chat }: { card: WorldCard; chat: MinisterChat }) {
  const log = useRef<HTMLDivElement>(null);
  const refresh = useWorldStore(s => s.events.find(event => event.conversation_id === chat.conversation_id)?.id);
  const live = useWorldStore(s => s.socketState === "live");
  const timeline = useConversationTimeline(chat.conversation_id, chat.session_id, refresh, live, log);
  const draft = useNodeSurfaceStore(s => s.drafts[card.id] ?? "");
  const setDraft = useNodeSurfaceStore(s => s.setDraft);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();
  const active = timeline.activeAgentIds?.includes(card.id) || card.status === "running" || card.status === "waiting";

  const send = async () => {
    const content = draft.trim();
    if (!content || sending || active) return;
    setSending(true); setError(undefined);
    try {
      await worldApi.postConversationMessage(chat.conversation_id, chat.session_id, {
        content, mention_agent_ids: [card.id], message_id: crypto.randomUUID(),
      });
      setDraft(card.id, "");
      await timeline.loadLatest();
    } catch (reason) { setError(apiErrorMessage(reason)); }
    finally { setSending(false); }
  };
  return <div className="minister-conversation">
    <div className="minister-messages nowheel" ref={log} onScroll={timeline.onScroll} role="log" aria-label={`${card.name} conversation`} aria-live="polite">
      {timeline.hasBefore && <button type="button" className="minister-text-button" onClick={() => void timeline.loadOlder()}>Earlier messages</button>}
      {!timeline.loading && !timeline.messages.length && <div className="minister-welcome">
        <Scan size={24} /><strong>A little help, close at hand.</strong>
        <p>Ask me to find cards or tidy this part of your canvas.</p>
        <button type="button" onClick={() => setDraft(card.id, "What cards are inside your circle?")}>What’s nearby?</button>
      </div>}
      {timeline.messages.map(message => <article key={message.id} data-message-id={message.id}
        className={`minister-message is-${message.sender_kind}`}>
        {message.kind?.startsWith("tool_") ? <details className="minister-tool"><summary>{ministerToolSummary(message.content, message.kind)}</summary><pre aria-label="Tool debug details">{message.content}</pre></details>
          : <><small>{message.sender_kind === "user" ? "You" : message.sender_name}</small><MarkdownMessage content={message.content} /></>}
      </article>)}
      {active && <p className="minister-thinking" role="status">Minister is working…</p>}
    </div>
    {timeline.showLatest && <button type="button" className="minister-text-button" onClick={() => void timeline.loadLatest()}>Latest messages</button>}
    {(error || timeline.error) && <p className="minister-error" role="alert">{error || timeline.error}</p>}
    <form className="minister-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
      <textarea aria-label={`Message ${card.name}`} placeholder="Ask about this part of your canvas…" rows={2} value={draft}
        onChange={event => setDraft(card.id, event.target.value)} onKeyDown={event => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); }
        }} />
      {active ? <button type="button" aria-label={`Stop ${card.name}`} onClick={() => {
        void worldApi.stopAgent(card.id).then(() => timeline.loadLatest()).catch(reason => setError(apiErrorMessage(reason)));
      }}><CircleStop size={19} /></button>
        : <button type="submit" aria-label={`Send to ${card.name}`} disabled={!draft.trim() || sending}><ArrowUp size={19} /></button>}
    </form>
  </div>;
}

export function MinisterNearby({ card }: { card: WorldCard }) {
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
    <label className="minister-search"><Search size={15} /><input aria-label="Search nearby cards" placeholder="Name, type or card ID" value={query}
      onChange={event => { setQuery(event.target.value); setOffset(0); }} /></label>
    <div className="minister-search-summary" role="status">{loading ? "Looking around…" : `${view?.total ?? 0} cards in this circle`}</div>
    {error && <p className="minister-error" role="alert">{error}</p>}
    <div className="minister-results nowheel">
      {!loading && !view?.nodes.length && <p>No matching cards inside the circle.</p>}
      {view?.nodes.map(node => <button type="button" key={node.id} title={`${node.name} · ${node.id}`} onClick={() => {
        useWorldStore.getState().selectCards([node.id]);
        setCenter(node.position.x + node.size.width / 2, node.position.y + node.size.height / 2, { zoom: Math.max(getZoom(), 0.65), duration: 300 });
      }}><span><strong>{node.name}</strong><small>{node.type} · {Math.round(node.position.x)}, {Math.round(node.position.y)}</small></span><LocateFixed size={16} /></button>)}
    </div>
    <div className="minister-pager">
      <button type="button" disabled={!offset || loading} onClick={() => setOffset(Math.max(0, offset - 40))}>Previous</button>
      <button type="button" disabled={view?.next_offset == null || loading} onClick={() => setOffset(view!.next_offset!)}>Next</button>
    </div>
  </div>;
}

function MinisterPanel({ card, radius, setRadius, saveRadius }: { card: WorldCard; radius: number; setRadius: (value: number) => void; saveRadius: () => void }) {
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
  return <section className="minister-panel nodrag nopan nowheel" aria-label={`${card.name} controls`} onPointerDown={event => event.stopPropagation()} onMouseDown={event => event.stopPropagation()}>
    <header className="minister-panel-header"><Scan size={18} /><input aria-label="Minister name" key={card.name} defaultValue={card.name} maxLength={200}
      onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); }}
      onBlur={event => { const name = event.target.value.trim(); if (name && name !== card.name) void update(card.id, { name }); }} />
      <button type="button" aria-label="Close Minister" onClick={() => useNodeSurfaceStore.getState().dismiss(card.id)}><X size={17} /></button></header>
    <div className="minister-settings">
      <label>Radius <input aria-label="Control radius" type="number" min={200} max={3000} step={50} value={radius}
        onChange={event => setRadius(Number(event.target.value))} onBlur={saveRadius}
        onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); }} /></label>
      <label className="minister-edit-switch"><input type="checkbox" checked={card.config.allow_canvas_edits === true}
        onChange={event => void update(card.id, { config: { allow_canvas_edits: event.target.checked } })} /> Allow canvas edits</label>
    </div>
    <details className="minister-model"><summary>Model & actions</summary><ModelSelect value={String(card.config.model ?? "")} onChange={model => void update(card.id, { config: { model } })} />
      <p>Create Agents and other cards, configure and arrange this area, and manage connections, groups and attachments. Destructive or sensitive changes need your confirmation.</p></details>
    <MinisterReviews card={card} />
    <nav className="minister-tabs" aria-label="Minister views"><button type="button" aria-pressed={tab === "talk"} onClick={() => setTab("talk")}>Conversation</button>
      <button type="button" aria-pressed={tab === "nearby"} onClick={() => setTab("nearby")}>Nearby cards</button>
      <small>{card.config.allow_canvas_edits ? "Edits enabled" : "Inspect only"}</small></nav>
    {tab === "nearby" ? <MinisterNearby card={card} /> : chat ? <MinisterConversation card={card} chat={chat} />
      : <div className="minister-loading" role={error ? "alert" : "status"}>{error ?? "Opening conversation…"}
        {error && <button type="button" className="minister-text-button" onClick={() => { setError(undefined); setAttempt(value => value + 1); }}>Retry</button>}</div>}
  </section>;
}

export const MinisterNode = memo(function MinisterNode({ data, selected }: NodeProps<CanvasNode>) {
  const { card } = data;
  const level = useNodeSurfaceStore(s => s.surfaceLevels[card.id]);
  const open = level === "inspector" || level === "workspace";
  const update = useWorldStore(s => s.updateCard);
  const savedRadius = Number(card.config.control_radius ?? 600);
  const [radius, setRadius] = useState(savedRadius);
  const internal = useInternalNode(card.id);
  const { screenToFlowPosition } = useReactFlow();
  const pointer = useRef<{ x: number; y: number }>();
  const resizing = useRef(false);
  useEffect(() => { if (!resizing.current) setRadius(savedRadius); }, [savedRadius]);
  const saveRadius = () => {
    const value = Number.isFinite(radius) ? clampRadius(radius) : savedRadius;
    setRadius(value);
    if (value !== savedRadius) void update(card.id, { config: { control_radius: value } });
  };
  const toggle = () => open ? useNodeSurfaceStore.getState().dismiss(card.id) : useNodeSurfaceStore.getState().openInspector(card.id);
  return <div className={`minister-node ${selected ? "is-selected" : ""} ${open ? "is-open" : ""} ${card.config.allow_canvas_edits ? "can-edit" : ""}`}
    data-card-id={card.id} data-card-type={card.type} data-control-radius={radius} data-activity={card.status}
    style={{ "--minister-radius": `${radius}px`, "--minister-cx": `${card.size.width / 2}px`, "--minister-cy": `${card.size.height / 2}px` } as CSSProperties}>
    <div className="minister-radius" aria-hidden="true" />
    {[Position.Top, Position.Right, Position.Bottom, Position.Left].map(position => <Handle key={position}
      id={`boundary-${position}`} type="source" position={position} isConnectable={false}
      style={{ visibility: "hidden", pointerEvents: "none" }} />)}
    <div className="minister-orb minister-drag-region" role="button" tabIndex={0} aria-label={`Open Minister ${card.name}`} aria-expanded={open}
      onPointerDown={event => { pointer.current = { x: event.clientX, y: event.clientY }; }}
      onClick={event => {
        if (pointer.current && Math.hypot(event.clientX - pointer.current.x, event.clientY - pointer.current.y) > 5) return;
        if (!event.shiftKey && !event.ctrlKey && !event.metaKey) toggle();
      }} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); } }}>
      <Scan size={30} strokeWidth={1.35} /><span>Minister</span><i aria-label={card.status} />
    </div>
    <div className="minister-label">{card.name}</div>
    <button type="button" className="minister-radius-handle nodrag nopan" aria-label={`Resize ${card.name} control radius`} title="Drag to change control radius"
      onPointerDown={event => { event.stopPropagation(); resizing.current = true; event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={event => {
        if (!resizing.current) return;
        const point = screenToFlowPosition({ x: event.clientX, y: event.clientY });
        const origin = internal?.internals.positionAbsolute ?? card.position;
        setRadius(clampRadius(Math.hypot(point.x - origin.x - card.size.width / 2, point.y - origin.y - card.size.height / 2)));
      }} onPointerUp={event => { if (!resizing.current) return; resizing.current = false; event.currentTarget.releasePointerCapture(event.pointerId); saveRadius(); }}
      onPointerCancel={() => { resizing.current = false; setRadius(savedRadius); }} onKeyDown={event => {
        if (["ArrowLeft", "ArrowRight", "ArrowDown", "ArrowUp"].includes(event.key)) {
          event.preventDefault(); const value = clampRadius(radius + (["ArrowLeft", "ArrowDown"].includes(event.key) ? -50 : 50));
          setRadius(value); void update(card.id, { config: { control_radius: value } });
        }
      }}><span>{radius}</span></button>
    {open && <MinisterPanel card={card} radius={radius} setRadius={setRadius} saveRadius={saveRadius} />}
  </div>;
});
