import {
  Bot,
  History,
  Maximize2,
  MessageSquare,
  Minimize2,
  PanelRight,
  Plus,
  Send,
  Settings2,
  X,
} from "lucide-react";
import { useMemo } from "react";
import { nodeSurfaceSupport, useNodeSurfaceStore } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";

interface WorkspaceWindowProps {
  card: WorldCard;
  children: React.ReactNode;
}

function WorkspaceWindow({ card, children }: WorkspaceWindowProps) {
  const catalog = useWorldStore((state) => state.catalog);
  const closeWorkspace = useNodeSurfaceStore((state) => state.closeWorkspace);
  const maximized = useNodeSurfaceStore((state) => Boolean(state.maximizedWorkspaces[card.id]));
  const toggleMaximized = useNodeSurfaceStore((state) => state.toggleWorkspaceMaximized);

  return (
    <div className="node-workspace-layer" data-testid="node-workspace-layer">
      <section
        className={`node-workspace-window ${maximized ? "is-maximized" : ""}`}
        role="dialog"
        aria-modal="false"
        aria-label={`${card.name} workspace`}
        data-workspace-node-id={card.id}
      >
        <header className="workspace-titlebar">
          <div className="workspace-app-mark"><Bot size={16} /></div>
          <div>
            <span>{catalog.node_types.find((item) => item.id === card.type)?.label ?? card.type} workspace</span>
            <strong>{card.name}</strong>
          </div>
          <div className="workspace-window-actions">
            <button
              type="button"
              className="icon-button"
              onClick={() => toggleMaximized(card.id)}
              aria-label={maximized ? "Restore workspace" : "Maximize workspace"}
            >
              {maximized ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
            </button>
            <button type="button" className="icon-button" onClick={closeWorkspace} aria-label="Close workspace">
              <X size={15} />
            </button>
          </div>
        </header>
        <div className="workspace-content">{children}</div>
      </section>
    </div>
  );
}

function AgentWorkspace({ card }: { card: WorldCard }) {
  const catalog = useWorldStore((state) => state.catalog);
  const edges = useWorldStore((state) => state.edges);
  const cards = useWorldStore((state) => state.cards);
  const runAgent = useWorldStore((state) => state.runAgent);
  const draft = useNodeSurfaceStore((state) => state.drafts[card.id] ?? String(card.config.prompt ?? ""));
  const setDraft = useNodeSurfaceStore((state) => state.setDraft);
  const output = Array.isArray(card.config.output) ? card.config.output.map(String) : [];
  const context = useMemo(() => edges
    .filter((edge) => edge.source === card.id || edge.target === card.id)
    .map((edge) => cards.find((item) => item.id === (edge.source === card.id ? edge.target : edge.source)))
    .filter((item): item is WorldCard => Boolean(item)), [card.id, cards, edges]);

  const submit = () => {
    const value = draft.trim();
    if (value && card.status !== "running") void runAgent(card.id, value);
  };

  return (
    <div className="agent-workspace-grid">
      <nav className="workspace-session-sidebar" aria-label="Agent sessions">
        <button type="button" className="workspace-new-session"><Plus size={13} /> New session</button>
        <div className="workspace-nav-label"><History size={11} /> Sessions</div>
        <button type="button" className="workspace-session is-active">
          <MessageSquare size={13} />
          <span><strong>Current session</strong><small>Quick interaction</small></span>
        </button>
        <div className="workspace-agent-state">
          <span data-status={card.status} />
          <div><strong>{card.status}</strong><small>{String(card.config.model ?? "Default model")}</small></div>
        </div>
      </nav>

      <main className="workspace-conversation">
        <header>
          <div><strong>Conversation</strong><span>Session-ready workspace foundation</span></div>
          <button type="button" className="secondary-button"><Settings2 size={13} /> Configure</button>
        </header>
        <div className="workspace-transcript">
          {output.length > 0 ? output.slice(-12).map((line, index) => (
            <div className="workspace-message" key={`${index}-${line.slice(0, 20)}`}>
              <span><Bot size={13} /></span><p>{line}</p>
            </div>
          )) : (
            <div className="workspace-welcome">
              <span><Bot size={22} /></span>
              <strong>Start a focused session with {card.name}</strong>
              <p>The workspace keeps room for rich messages, tool results, files, and durable session history.</p>
            </div>
          )}
        </div>
        <div className="workspace-composer">
          <textarea
            value={draft}
            onChange={(event) => setDraft(card.id, event.target.value)}
            onKeyDown={(event) => {
              if ((event.ctrlKey || event.metaKey) && event.key === "Enter") submit();
            }}
            placeholder={`Message ${card.name}…`}
            aria-label={`Message ${card.name}`}
          />
          <footer><span>Ctrl/⌘ + Enter to send</span><button type="button" onClick={submit} disabled={!draft.trim() || card.status === "running"}><Send size={14} /></button></footer>
        </div>
      </main>

      <aside className="workspace-context-panel">
        <header><PanelRight size={13} /><strong>Context</strong></header>
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
          <span className="workspace-panel-label">Agent configuration</span>
          <dl>
            <div><dt>Model</dt><dd>{String(card.config.model ?? "Default")}</dd></div>
            <div><dt>Status</dt><dd>{card.status}</dd></div>
            <div><dt>Tools</dt><dd>{context.length}</dd></div>
          </dl>
        </section>
      </aside>
    </div>
  );
}

export function NodeWorkspace() {
  const activeNodeId = useNodeSurfaceStore((state) => state.activeNodeId);
  const level = useNodeSurfaceStore((state) => state.level);
  const card = useWorldStore((state) => (
    state.cards.find((item) => item.id === activeNodeId)
    ?? state.stressCards.find((item) => item.id === activeNodeId)
  ));
  const catalog = useWorldStore((state) => state.catalog);

  if (level !== "workspace" || !card || !nodeSurfaceSupport(card.type, catalog).workspace) return null;

  return (
    <WorkspaceWindow card={card}>
      {card.type === "agent" ? <AgentWorkspace card={card} /> : (
        <div className="workspace-welcome">
          <strong>{card.name}</strong>
          <p>This plugin node exposes a workspace surface. Its frontend module can replace this generic view.</p>
        </div>
      )}
    </WorkspaceWindow>
  );
}
