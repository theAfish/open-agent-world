import { ExecutionGraphCard } from './ExecutionGraph';
import { JobsCard } from './Jobs';
import { isMissingCard, MissingPlugin } from "./MissingPlugin";
import { useWorkspaceAccess } from '../workspace/WorkspaceAccess';
import { t, useLocale } from "../i18n";
import { SandboxWorkspace } from "./SandboxWorkspace";
import { ArtifactCollection } from "./Artifacts";
import { LifecycleDisclosure } from "./LifecycleStatus";
import { BarracksBody } from "./Barracks";
import {
  Bot,
  Clock3,
  MessageSquare,
  Radio,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { CardName } from "./CardName";
import { IconButton } from "../components/IconButton";
import { collapsedSurface, nodePresentation, useNodeSurfaceStore, useSurfaceDraft, surfaceDraftKey } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import { useConversationView } from "../state/conversationView";
import type { ConversationSession, WorldCard } from "../types/world";
import { TaskBoardBody } from "./TaskBoard";
import { SkillToolboxBody, SkillNodeBody } from "./SkillToolbox";
import { WorkSourceWorkspace } from "./NodeExecution";
import { ConversationWorkspace } from "./ConversationWorkspace";
import { MinisterRoleSettings } from './MinisterRoleCard';
import { openMinisterSettings, useMinisterRole } from '../state/ministerRole';
import { AgentCardBody } from "./AgentCard";
import { modelLabel } from "./modelLabel";
import { PluginSurface } from "../plugins/PluginSurface";
import { CatalogIcon } from "../components/CatalogIcon";
import { CardFinishLayer } from "./CardFinishLayer";
import { normalizeCardFinish } from "./cardFinish";
import { WorkspaceEmptyState, WorkspaceToolbar } from '../workspace/WorkspacePrimitives';
import './agentWorkspace.css';
import { ContentSurfaceControls, hasContentSurface } from './ContentSurfaceControls';

interface WorkspaceSurfaceProps {
  card: WorldCard;
}

function WorkspaceTitlebar({ card }: WorkspaceSurfaceProps) {
  useLocale();
  const catalog = useWorldStore((state) => state.catalog);
  const closeWorkspace = useNodeSurfaceStore((state) => state.closeWorkspace);
  const base = useNodeSurfaceStore((state) => state.baseLevels[card.id]);
  const deleteCard = useWorldStore((state) => state.deleteCard);
  const canCollapse = collapsedSurface(nodePresentation(card.type, catalog), "workspace", base) !== "workspace";

  return (
    <header className="workspace-titlebar node-drag-region card-finish-surface" data-finish={normalizeCardFinish(card.finish)}>
      <div className="workspace-app-mark"><CatalogIcon definition={catalog.node_types.find((d) => d.id === card.type)} size={16} /></div>
      <div>
        <span>{card.type === "xrd.match" ? "XRD 谱解析" : (catalog.node_types.find((item) => item.id === card.type)?.label ?? card.type)} {t("workspace")}</span>
        <CardName card={card} workspace label={catalog.node_types.find(item => item.id === card.type)?.label ?? card.type} />
      </div>
      <div className="workspace-window-actions">
        {!card.ephemeral && <IconButton icon={Trash2} size="sm" quiet danger onClick={() => { void deleteCard(card.id); }}
          label={t("Remove {v0}", { v0: String(card.name) })} title={card.missing_plugin ? t("Remove") : t("Remove object (Ctrl+Z to undo)")} />}
        {canCollapse && <IconButton icon={X} size="sm" quiet onClick={() => closeWorkspace(card.id)} label={t("Close workspace")} />}
      </div>
      <CardFinishLayer finish={card.finish} quality="thumbnail" surface="chrome" />
    </header>
  );
}

function AgentWorkspace({ card }: { card: WorldCard }) {
  useLocale();
  const modelCatalog = useWorldStore(state => state.modelCatalog);
  const catalog = useWorldStore((state) => state.catalog);
  const edges = useWorldStore((state) => state.edges);
  const cards = useWorldStore((state) => state.cards);
  const allEvents = useWorldStore((state) => state.events);
  const [sessions, setSessions] = useState<ConversationSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useSurfaceDraft<string | undefined>(surfaceDraftKey(card.id, 'agent-history-session'), undefined);
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
    <div className="agent-activity nodrag nopan nowheel">
      <WorkspaceToolbar label={t("Agent conversation history")} className="agent-activity-toolbar">
        <MessageSquare size={15} aria-hidden="true" />
        <select aria-label={t("Conversation history")} value={activeSessionId ?? ''} disabled={!sessions.length}
          onChange={event => {
            const session = sessions.find(item => item.id === event.target.value);
            if (!session) return;
            setActiveSessionId(session.id);
            useConversationView.getState().showSession(session.conversation_id, session.id);
          }}>
          {!sessions.length && <option value="">{t("No Conversation sessions yet.")}</option>}
          {sessions.map(session => <option key={session.id} value={session.id}>{session.title} · {session.group_title ?? session.conversation_name ?? cards.find(item => item.id === session.conversation_id)?.name ?? t("Conversation")}</option>)}
        </select>
        <span className="agent-activity-status"><Radio size={12} /> {t(card.status)}</span>
      </WorkspaceToolbar>
      {historyError && <p role="alert">{historyError}</p>}
      <details className="agent-context-disclosure">
        <summary>{t("Agent context")} · {modelLabel(modelCatalog, card.config.model)} · {context.length} {t("Connections")}</summary>
        <div className="agent-context-list">
          {context.length ? context.map(item => <div key={item.id}><strong>{item.name}</strong><small>{catalog.node_types.find(definition => definition.id === item.type)?.label ?? item.type}</small></div>) : <p>{t("No connected context yet.")}</p>}
        </div>
      </details>
      <main className="agent-activity-feed" aria-label={t("Runtime history")}>
          <LifecycleDisclosure agentId={card.id} />
          {events.length > 0 ? events.slice(0, 40).map((event) => (
            <article key={event.id} className={`agent-activity-event is-${event.type}`}>
              <span><Clock3 size={12} /></span>
              <div>
                <header><strong>{event.type.replaceAll("_", " ")}</strong><time>{new Date(event.timestamp).toLocaleTimeString(useLocale.getState().locale)}</time></header>
                <p>{String(event.payload.error ?? event.payload.text ?? event.payload.name ?? event.message ?? t("No summary provided"))}</p>
                <small>{t("Run")} {event.run_id?.slice(0, 8) ?? "unscoped"}</small>
                <details>
                  <summary>{t("Event data")}</summary>
                  <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                </details>
              </div>
            </article>
          )) : (
            <WorkspaceEmptyState icon={<Bot size={24} />} title={t("No runtime activity for this session")}
              description={!sessions.length ? t("Connect this Agent to a Conversation, then send a message to start a session.") : activeSessionId ? t("This agent has not emitted a run, tool, or response event for the selected session.") : t("Select a session to inspect agent activity.")} />
          )}
      </main>
    </div>
  );
}

export function WorkspaceSurface({ card }: WorkspaceSurfaceProps) {
  useLocale();
  const catalog = useWorldStore(state => state.catalog);
  const definition = catalog.node_types.find(item => item.id === card.type);
  const contentSurface = !isMissingCard(card, catalog) && hasContentSurface(definition);
  return <section data-missing={Boolean(card.missing_plugin) || undefined} data-content-surface={contentSurface || undefined}
    data-content-controls={contentSurface ? definition?.presentation?.content_controls ?? 'side' : undefined}
    className="node-workspace-window" role="dialog" aria-modal="false" aria-label={t("{v0} workspace", { v0: String(card.name) })} data-workspace-node-id={card.id}>
    {contentSurface ? <ContentSurfaceControls card={card} level="workspace" /> : <WorkspaceTitlebar card={card} />}
    <WorkspaceContent card={card} />
  </section>;
}

/** Shared by a canvas window and a docked Legion pane. */
export function WorkspaceContent({ card }: WorkspaceSurfaceProps) {
  useLocale();
  const catalog = useWorldStore((state) => state.catalog);
  const { deployed } = useWorkspaceAccess();
  const [agentTab, setAgentTab] = useSurfaceDraft(surfaceDraftKey(card.id, 'agent-tab'), 'activity');
  const ministerTab = useMinisterRole(s => s.settingsCardId === card.id) && Boolean(card.minister);
  if (isMissingCard(card, catalog)) return <div className="workspace-content"><MissingPlugin card={card} /></div>;
  return (
      <div className="workspace-content">
      <PluginSurface card={card} slot="workspace" level="workspace">
      {card.type === "xrd.match" ? <div className="agent-window-body"><div className="agent-settings-window nodrag nopan nowheel"><AgentCardBody card={card} level="workspace" /></div></div> : catalog.node_types.find((definition) => definition.id === card.type)?.traits.includes("core.agent") ? deployed ? <div className="workspace-welcome"><Bot size={22} /><strong>{card.name}</strong><p>{t(card.status)}</p></div> : <>
        <nav className="agent-window-tabs nodrag nopan" role="tablist" aria-label={t("Agent window")}>
          <button role="tab" aria-selected={!ministerTab && agentTab === "activity"} onClick={() => { useMinisterRole.setState({ settingsCardId: undefined }); setAgentTab("activity"); }}>{t("Activity")}</button>
          <button role="tab" aria-selected={!ministerTab && agentTab === "settings"} onClick={() => { useMinisterRole.setState({ settingsCardId: undefined }); setAgentTab("settings"); }}>{t("Settings")}</button>
          {card.minister && <button role="tab" aria-selected={ministerTab} onClick={() => openMinisterSettings(card.id)}>{t('Minister')}</button>}
        </nav>
        <div className="agent-window-body">{ministerTab ? <div className="agent-settings-window nodrag nopan nowheel"><MinisterRoleSettings card={card} onOpenHistory={() => setAgentTab('activity')} /></div> : agentTab === "settings"
          ? <div className="agent-settings-window nodrag nopan nowheel"><AgentCardBody card={card} level="workspace" /></div>
          : <AgentWorkspace card={card} />}</div>
        </>
        : catalog.node_types.find((definition) => definition.id === card.type)?.traits.includes("ui.agent-barracks.v1") ? <BarracksBody card={card} workspace />
        : card.type === "core.execution-graph" ? <ExecutionGraphCard card={card} />
        : card.type === 'core.jobs' ? <JobsCard card={card} />
        : card.type === "sandbox" ? <SandboxWorkspace card={card} />
        : card.type === "core.artifact-collection" ? <ArtifactCollection card={card} />
        : card.type === "conversation" ? <ConversationWorkspace card={card} />
        : catalog.node_types.find((definition) => definition.id === card.type)?.traits.includes("ui.skill-package.v1") ? <SkillToolboxBody card={card} workspace />
        : catalog.node_types.find((definition) => definition.id === card.type)?.traits.includes("ui.skill.v1") ? <SkillNodeBody card={card} workspace />
        : catalog.node_types.find((definition) => definition.id === card.type)?.traits.includes("ui.task-board.v1") ? <TaskBoardBody card={card} workspace />
        : catalog.node_types.find((definition) => definition.id === card.type)?.has_execution ? <WorkSourceWorkspace nodeId={card.id} /> : (
          <WorkspaceEmptyState title={card.name}
            description={t("This plugin node exposes a workspace surface. Its frontend module can replace this generic view.")} />
        )}
      </PluginSurface>
      </div>
  );
}
