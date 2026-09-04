import {
  Bot,
  Clock3,
  MessageSquare,
  MessagesSquare,
  PanelRight,
  Radio,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { nodeSurfaceSupport, useNodeSurfaceStore } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import type { ConversationSession, WorldCard } from "../types/world";
import { ConversationWorkspace } from "./ConversationWorkspace";

interface WorkspaceSurfaceProps {
  card: WorldCard;
}

function WorkspaceTitlebar({ card }: WorkspaceSurfaceProps) {
  const catalog = useWorldStore((state) => state.catalog);
  const closeWorkspace = useNodeSurfaceStore((state) => state.closeWorkspace);

  return (
    <header className="workspace-titlebar node-drag-region">
      <div className="workspace-app-mark">{card.type === "conversation" ? <MessagesSquare size={16} /> : <Bot size={16} />}</div>
      <div>
        <span>{catalog.node_types.find((item) => item.id === card.type)?.label ?? card.type} workspace</span>
        <strong>{card.name}</strong>
      </div>
      <div className="workspace-window-actions">
        <button type="button" className="icon-button" onClick={() => closeWorkspace(card.id)} aria-label="Close workspace"><X size={15} /></button>
      </div>
    </header>
  );
}

function AgentWorkspace({ card }: { card: WorldCard }) {
  const catalog = useWorldStore((state) => state.catalog);
  const edges = useWorldStore((state) => state.edges);
  const cards = useWorldStore((state) => state.cards);
  const allEvents = useWorldStore((state) => state.events);
  const [sessions, setSessions] = useState<ConversationSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>();
  const [historyError, setHistoryError] = useState<string>();
  const events = useMemo(() => allEvents.filter((event) => (
    event.agent_id === card.id
      && (!activeSessionId || event.session_id === activeSessionId)
  )), [activeSessionId, allEvents, card.id]);
  const context = useMemo(() => edges
    .filter((edge) => edge.source === card.id || edge.target === card.id)
    .map((edge) => cards.find((item) => item.id === (edge.source === card.id ? edge.target : edge.source)))
    .filter((item): item is WorldCard => Boolean(item)), [card.id, cards, edges]);

  useEffect(() => {
    let current = true;
    void worldApi.getAgentConversationSessions(card.id).then((items) => {
      if (current) {
        setSessions(items);
        setActiveSessionId((selected) => (
          selected && items.some((session) => session.id === selected)
            ? selected
            : items[0]?.id
        ));
        setHistoryError(undefined);
      }
    }).catch((reason) => current && setHistoryError(apiErrorMessage(reason)));
    return () => { current = false; };
  }, [card.id, events[0]?.id]);

  return (
    <div className="agent-workspace-grid agent-activity-workspace">
      <nav className="workspace-session-sidebar" aria-label="Agent conversation history">
        <div className="workspace-nav-label"><MessageSquare size={11} /> Conversation history</div>
        <div className="conversation-sidebar-scroll">
          {sessions.map((session) => (
            <button
              type="button"
              className={`workspace-session agent-history-session nodrag nopan ${session.id === activeSessionId ? "is-active" : ""}`}
              key={session.id}
              onClick={() => setActiveSessionId(session.id)}
              aria-label={`Show runtime history for ${session.title}`}
            >
              <MessageSquare size={13} />
              <span><strong>{session.title}</strong><small>{session.conversation_name ?? cards.find((item) => item.id === session.conversation_id)?.name ?? "Conversation"}</small></span>
            </button>
          ))}
          {sessions.length === 0 ? <p>{historyError ?? "No Conversation sessions yet."}</p> : null}
        </div>
        <div className="workspace-agent-state">
          <span data-status={card.status} />
          <div><strong>{card.status}</strong><small>{String(card.config.model ?? "Default model")}</small></div>
        </div>
      </nav>

      <main className="agent-run-history">
        <header>
          <div><strong>Runtime history</strong><span>{activeSessionId ? "Selected session: runs, tools, output, and errors" : "Select a conversation session to inspect activity"}</span></div>
          <span className="agent-runtime-badge"><Radio size={12} /> {card.status}</span>
        </header>
        <div className="agent-run-timeline">
          {events.length > 0 ? events.slice(0, 40).map((event) => (
            <article key={event.id} className={`agent-run-event is-${event.type}`}>
              <span><Clock3 size={12} /></span>
              <div>
                <header><strong>{event.type.replaceAll("_", " ")}</strong><time>{new Date(event.timestamp).toLocaleTimeString()}</time></header>
                <p>{String(event.payload.error ?? event.payload.text ?? event.payload.name ?? event.message ?? "No summary provided")}</p>
                <small>Run {event.run_id?.slice(0, 8) ?? "unscoped"}</small>
                <details>
                  <summary>Event data</summary>
                  <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                </details>
              </div>
            </article>
          )) : (
            <div className="workspace-welcome">
              <span><Bot size={22} /></span>
              <strong>No runtime activity for this session</strong>
              <p>{activeSessionId ? "This agent has not emitted a run, tool, or response event for the selected session." : "Select a session to inspect agent activity."}</p>
            </div>
          )}
        </div>
      </main>

      <aside className="workspace-context-panel">
        <header><PanelRight size={13} /><strong>Agent context</strong></header>
        <section>
          <span className="workspace-panel-label">Connected objects</span>
          {context.length > 0 ? context.map((item) => (
            <div className="workspace-context-item" key={item.id}>
              <span>{item.type.slice(0, 1).toUpperCase()}</span>
              <div><strong>{item.name}</strong><small>{catalog.node_types.find((definition) => definition.id === item.type)?.label ?? item.type}</small></div>
            </div>
          )) : <p>No connected context yet.</p>}
        </section>
        <section>
          <span className="workspace-panel-label">Agent state</span>
          <dl>
            <div><dt>Model</dt><dd>{String(card.config.model ?? "Default")}</dd></div>
            <div><dt>Status</dt><dd>{card.status}</dd></div>
            <div><dt>Connections</dt><dd>{context.length}</dd></div>
            <div><dt>Sessions</dt><dd>{sessions.length}</dd></div>
          </dl>
        </section>
      </aside>
    </div>
  );
}

export function WorkspaceSurface({ card }: WorkspaceSurfaceProps) {
  const catalog = useWorldStore((state) => state.catalog);
  return (
    <section className="node-workspace-window" role="dialog" aria-modal="false" aria-label={`${card.name} workspace`} data-workspace-node-id={card.id}>
      <WorkspaceTitlebar card={card} />
      <div className="workspace-content">
      {card.type === "agent" ? <AgentWorkspace card={card} />
        : card.type === "conversation" ? <ConversationWorkspace card={card} /> : (
          <div className="workspace-welcome">
            <strong>{card.name}</strong>
            <p>This plugin node exposes a workspace surface. Its frontend module can replace this generic view.</p>
          </div>
        )}
      </div>
    </section>
  );
}
