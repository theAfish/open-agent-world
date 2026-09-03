import { Bot, MessageSquare, Plus, Send, UserRound, Users, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { activeConversationAgentIds } from "../state/conversationActivity";
import { appendMention, resolveConversationTargets } from "../state/conversationMentions";
import { useWorldStore } from "../state/worldStore";
import type { ConversationAgent, ConversationMessage, ConversationSession, WorldCard } from "../types/world";

export function ConversationWorkspace({ card }: { card: WorldCard }) {
  const runtimeEvents = useWorldStore((state) => state.events);
  const accessEvent = useWorldStore((state) => state.events.find((event) => {
    if (event.type !== "permission_changed") return false;
    const edge = event.payload.edge as Record<string, unknown> | undefined;
    return edge?.source === card.id || edge?.target === card.id;
  })?.id);
  const refreshEvent = useWorldStore((state) => state.events.find(
    (event) => event.conversation_id === card.id
      && (
        event.type === "conversation_message"
        || event.type === "conversation_session_created"
        || event.type === "agent_status_changed"
      ),
  )?.id);
  const pushToast = useWorldStore((state) => state.pushToast);
  const [sessions, setSessions] = useState<ConversationSession[]>([]);
  const [agents, setAgents] = useState<ConversationAgent[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>();
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [selectedAgentId, setSelectedAgentId] = useState<string>();
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [groupTitle, setGroupTitle] = useState("");
  const [groupAgentIds, setGroupAgentIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const transcriptEnd = useRef<HTMLDivElement>(null);

  const activeSession = sessions.find((session) => session.id === activeSessionId);
  const connectedAgents = agents.filter((agent) => agent.connected);
  const participants = (activeSession?.participant_ids ?? [])
    .map((id) => agents.find((item) => item.id === id))
    .filter((item): item is ConversationAgent => Boolean(item));
  const respondingAgentIds = useMemo(() => activeConversationAgentIds(
    runtimeEvents, card.id, activeSessionId,
  ), [activeSessionId, card.id, runtimeEvents]);
  const respondingAgents = participants.filter((agent) => respondingAgentIds.includes(agent.id));

  useEffect(() => {
    let current = true;
    void worldApi.getConversation(card.id).then((summary) => {
      if (!current) return;
      setSessions(summary.sessions);
      setAgents(summary.agents);
      setActiveSessionId((selected) => (
        selected && summary.sessions.some((session) => session.id === selected)
          ? selected
          : summary.sessions[0]?.id
      ));
      setError(undefined);
    }).catch((reason) => current && setError(apiErrorMessage(reason)));
    return () => { current = false; };
  }, [accessEvent, card.id, refreshEvent]);

  useEffect(() => {
    if (!activeSessionId) {
      setMessages([]);
      return;
    }
    let current = true;
    void worldApi.getConversationMessages(card.id, activeSessionId).then((items) => {
      if (current) {
        setMessages(items);
        setError(undefined);
      }
    }).catch((reason) => current && setError(apiErrorMessage(reason)));
    return () => { current = false; };
  }, [activeSessionId, card.id, refreshEvent]);

  useEffect(() => {
    if (!activeSession?.participant_ids.includes(selectedAgentId ?? "")) {
      setSelectedAgentId(activeSession?.participant_ids[0]);
    }
  }, [activeSession, selectedAgentId]);

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "end" });
  }, [messages.length, respondingAgentIds.join("|")]);

  const createSession = async (title: string, participantIds: string[]) => {
    setBusy(true);
    try {
      const session = await worldApi.createConversationSession(card.id, {
        title: title.trim() || "New session",
        participant_ids: participantIds,
      });
      setSessions((current) => [session, ...current]);
      setActiveSessionId(session.id);
      setCreatingGroup(false);
      setGroupTitle("");
      setGroupAgentIds([]);
      return session;
    } catch (reason) {
      pushToast({ tone: "error", title: "Session was not created", detail: apiErrorMessage(reason) });
      return undefined;
    } finally {
      setBusy(false);
    }
  };

  const openDirectSession = async (agent: ConversationAgent) => {
    const existing = sessions.find((session) => (
      session.participant_ids.length === 1 && session.participant_ids[0] === agent.id
    ));
    if (existing) {
      setActiveSessionId(existing.id);
      setSelectedAgentId(agent.id);
      return;
    }
    const created = await createSession(`Chat with ${agent.name}`, [agent.id]);
    if (created) setSelectedAgentId(agent.id);
  };

  const submit = async () => {
    const content = draft.trim();
    if (!content || !activeSession || busy) return;
    const targets = resolveConversationTargets(content, participants, selectedAgentId);
    setBusy(true);
    try {
      const result = await worldApi.postConversationMessage(card.id, activeSession.id, {
        content,
        mention_agent_ids: targets,
      });
      setMessages((current) => current.some((item) => item.id === result.message.id)
        ? current : [...current, result.message]);
      setDraft("");
      if (targets.length === 0 && participants.length > 1) {
        pushToast({
          tone: "neutral",
          title: "Message saved without calling an Agent",
          detail: "Select a participant or include an explicit @name to request a response.",
        });
      }
    } catch (reason) {
      pushToast({ tone: "error", title: "Message was not sent", detail: apiErrorMessage(reason) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="conversation-workspace-grid">
      <nav className="workspace-session-sidebar" aria-label="Conversation sessions and agents">
        <button type="button" className="workspace-new-session" onClick={() => setCreatingGroup(true)}>
          <Plus size={13} /> New group
        </button>
        {creatingGroup ? (
          <div className="conversation-group-builder">
            <header><strong>Create session</strong><button type="button" onClick={() => setCreatingGroup(false)} aria-label="Cancel group"><X size={12} /></button></header>
            <input value={groupTitle} onChange={(event) => setGroupTitle(event.target.value)} placeholder="Session name" aria-label="Session name" />
            {connectedAgents.map((agent) => (
              <label key={agent.id}>
                <input type="checkbox" checked={groupAgentIds.includes(agent.id)} onChange={() => setGroupAgentIds((current) => current.includes(agent.id) ? current.filter((id) => id !== agent.id) : [...current, agent.id])} />
                <span>{agent.name}</span>
              </label>
            ))}
            <button type="button" disabled={busy || groupAgentIds.length === 0} onClick={() => void createSession(groupTitle || "Group conversation", groupAgentIds)}>Create group</button>
          </div>
        ) : null}
        <div className="workspace-nav-label"><MessageSquare size={11} /> Sessions</div>
        <div className="conversation-sidebar-scroll">
          {sessions.map((session) => (
            <button type="button" className={`workspace-session ${session.id === activeSessionId ? "is-active" : ""}`} key={session.id} onClick={() => setActiveSessionId(session.id)}>
              {session.participant_ids.length > 1 ? <Users size={13} /> : <MessageSquare size={13} />}
              <span><strong>{session.title}</strong><small>{session.participant_ids.length} participants</small></span>
            </button>
          ))}
        </div>
        <div className="workspace-nav-label"><Bot size={11} /> Connected agents</div>
        <div className="conversation-sidebar-scroll conversation-contact-list">
          {connectedAgents.map((agent) => (
            <button type="button" className="workspace-session" key={agent.id} onClick={() => void openDirectSession(agent)}>
              <Bot size={13} /><span><strong>{agent.name}</strong><small>{agent.status}</small></span>
            </button>
          ))}
          {connectedAgents.length === 0 ? <p>Connect an Agent using Participate.</p> : null}
        </div>
      </nav>

      <main className="workspace-conversation">
        <header>
          <div><strong>{activeSession?.title ?? "Conversation"}</strong><span>{participants.length} active participants</span></div>
          <div className="conversation-targets" aria-label="Message target">
            {participants.map((agent) => (
              <button type="button" key={agent.id} className={selectedAgentId === agent.id ? "is-selected" : ""} onClick={() => setSelectedAgentId(agent.id)} title={`Address ${agent.name} by default`}>@{agent.name}</button>
            ))}
          </div>
        </header>
        <div className="workspace-transcript" aria-live="polite">
          {error ? <div className="workspace-welcome"><strong>Conversation unavailable</strong><p>{error}</p></div> : null}
          {!error && messages.length === 0 && respondingAgents.length === 0 ? (
            <div className="workspace-welcome"><span><MessageSquare size={22} /></span><strong>This session is ready</strong><p>Select a participant, type an explicit @name, or keep an unaddressed note.</p></div>
          ) : messages.map((message) => (
            <article className={`workspace-message is-${message.sender_kind}`} key={message.id} data-message-id={message.id}>
              <span>{message.sender_kind === "agent" ? <Bot size={13} /> : <UserRound size={13} />}</span>
              <div><strong>{message.sender_name}</strong><p>{message.content}</p></div>
            </article>
          ))}
          {!error ? respondingAgents.map((agent) => (
            <article className="workspace-message is-agent is-responding" key={`responding-${agent.id}`} data-responding-agent-id={agent.id} aria-label={`${agent.name} is responding`}>
              <span><Bot size={13} /></span>
              <div>
                <strong>{agent.name}</strong>
                <div className="conversation-typing-bubble" aria-hidden="true"><i /><i /><i /></div>
                <span className="sr-only">{agent.name} is responding</span>
              </div>
            </article>
          )) : null}
          <div ref={transcriptEnd} />
        </div>
        <div className="workspace-composer">
          <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void submit();
            }
          }} placeholder={activeSession ? "Write a message; use @name to address a participant…" : "Create or select a session first…"} aria-label="Conversation message" disabled={!activeSession} />
          <footer>
            <span>{selectedAgentId ? `Default: @${agents.find((item) => item.id === selectedAgentId)?.name}` : "No default recipient"} · Enter to send · Shift+Enter for new line</span>
            <button type="button" onClick={() => void submit()} disabled={!draft.trim() || !activeSession || busy} aria-label="Send message"><Send size={14} /></button>
          </footer>
        </div>
      </main>

      <aside className="workspace-context-panel conversation-participant-panel">
        <header><Users size={13} /><strong>Participants</strong></header>
        <section>
          <span className="workspace-panel-label">In this session</span>
          {participants.map((agent) => (
            <button type="button" className="workspace-context-item" key={agent.id} onClick={() => {
              setSelectedAgentId(agent.id);
              setDraft((value) => appendMention(value, agent.name));
            }}>
              <span><Bot size={12} /></span><div><strong>{agent.name}</strong><small>{agent.status} · insert mention</small></div>
            </button>
          ))}
          {participants.length === 0 ? <p>This session has no Agents. Create a direct or group session from the left.</p> : null}
        </section>
        <section>
          <span className="workspace-panel-label">Field policy</span>
          <p>Canvas connections authorize access. Session membership selects the group. Removing an edge keeps history but blocks future turns.</p>
        </section>
      </aside>
    </div>
  );
}
