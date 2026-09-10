import { useConversationTimeline } from "../state/useConversationTimeline";
import { MarkdownMessage } from "./MarkdownMessage";
import { ConversationAttachments } from "./ConversationAttachments";
import { ArrowDown, Bot, Info, LoaderCircle, MessageSquare, MoreHorizontal, Paperclip, Pencil, Plus, Send, Trash2, UserMinus, UserRound, Users, X } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { activeConversationAgentIds } from "../state/conversationActivity";
import {
  appendMention,
  completeMention,
  mentionCompletion,
  resolveConversationTargets,
} from "../state/conversationMentions";
import { useWorldStore } from "../state/worldStore";
import { useOpenFiles } from "../state/openFiles";
import type { ConversationAgent, ConversationAttachment, ConversationMessage, ConversationSession, WorldCard } from "../types/world";

type OutgoingMessage = { message: ConversationMessage; status: "sending" | "confirmed" | "unconfirmed" };

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
        || event.type === "conversation_session_updated"
        || event.type === "conversation_session_deleted"
        || event.type === "agent_status_changed"
      ),
  )?.id);
  const pushToast = useWorldStore((state) => state.pushToast);
  const socketLive = useWorldStore((state) => state.socketState === "live");
  const [sessions, setSessions] = useState<ConversationSession[]>([]);
  const [agents, setAgents] = useState<ConversationAgent[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>();
  useEffect(() => () => useOpenFiles.getState().clear(card.id), [card.id, activeSessionId]);

  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<ConversationAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const [outgoing, setOutgoing] = useState<OutgoingMessage[]>([]);
  const revealOutgoing = useRef(false);
  const [selectedAgentId, setSelectedAgentId] = useState<string>();
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [groupTitle, setGroupTitle] = useState("");
  const [groupAgentIds, setGroupAgentIds] = useState<string[]>([]);
  const [addingParticipants, setAddingParticipants] = useState(false);
  const [participantAgentIds, setParticipantAgentIds] = useState<string[]>([]);
  const [mentionCaret, setMentionCaret] = useState<number>();
  const [mentionIndex, setMentionIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const transcript = useRef<HTMLDivElement>(null);

  const messageInput = useRef<HTMLTextAreaElement>(null);
  const selectedScope = useRef("");
  selectedScope.current = `${card.id}/${activeSessionId ?? ""}`;

  const activeSession = sessions.find((session) => session.id === activeSessionId);
  const connectedAgents = agents.filter((agent) => agent.connected);
  const participants = (activeSession?.participant_ids ?? [])
    .map((id) => agents.find((item) => item.id === id))
    .filter((item): item is ConversationAgent => Boolean(item?.connected));
  const respondingAgentIds = useMemo(() => activeConversationAgentIds(
    runtimeEvents, card.id, activeSessionId,
  ), [activeSessionId, card.id, runtimeEvents]);
  const history = useConversationTimeline(card.id, activeSessionId, refreshEvent, socketLive, transcript);
  const visibleOutgoing = outgoing.filter((item) => item.message.conversation_id === card.id
    && item.message.session_id === activeSessionId && !history.messages.some((message) => message.id === item.message.id));
  const messages = [...history.messages, ...visibleOutgoing.map((item) => item.message)];
  useEffect(() => {
    const ids = new Set(history.messages.map((message) => message.id));
    setOutgoing((current) => current.some((item) => ids.has(item.message.id))
      ? current.filter((item) => !ids.has(item.message.id)) : current);
  }, [history.messages]);
  useLayoutEffect(() => {
    if (revealOutgoing.current && transcript.current) {
      transcript.current.scrollTop = transcript.current.scrollHeight;
      revealOutgoing.current = false;
    }
  }, [outgoing]);
  const respondingAgents = participants.filter((agent) => (history.activeAgentIds ?? respondingAgentIds).includes(agent.id));
  const activeGroupId = activeSession?.group_id ?? activeSession?.id;
  const groups = [...new Map(sessions.map((session) => [session.group_id ?? session.id, session])).values()];
  const groupSessions = sessions.filter((session) => (session.group_id ?? session.id) === activeGroupId);
  const [renaming, setRenaming] = useState<string>();
  const [sessionTitle, setSessionTitle] = useState("");
  const availableAgents = connectedAgents.filter((agent) => (
    !activeSession?.participant_ids.includes(agent.id)
  ));
  const completion = mentionCaret === undefined
    ? undefined
    : mentionCompletion(draft, mentionCaret, participants);

  useEffect(() => {
    // REST owns the durable snapshot. Socket liveness only invalidates that
    // snapshot on reconnect; it never gates historical reads.
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
  }, [accessEvent, card.id, refreshEvent, socketLive]);

  useEffect(() => {
    const eligible = activeSession?.participant_ids.filter((id) => agents.some((agent) => agent.id === id && agent.connected)) ?? [];
    if (!eligible.includes(selectedAgentId ?? "")) {
      setSelectedAgentId(eligible[0]);
    }
  }, [activeSession, agents, selectedAgentId]);

  useEffect(() => {
    setMentionIndex(0);
  }, [completion?.query]);

  useEffect(() => {
    setAddingParticipants(false);
    setParticipantAgentIds([]);
    setMentionCaret(undefined);
    setRenaming(undefined);
    setDraft("");
    setAttachments([]);
  }, [activeSessionId]);

  const createSession = async (title: string, participantIds: string[], groupId?: string) => {
    setBusy(true);
    try {
      const session = await worldApi.createConversationSession(card.id, {
        title: "New session",
        group_title: groupId ? undefined : title.trim() || "Group conversation",
        group_id: groupId,
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
      !session.is_default && session.participant_ids.length === 1 && session.participant_ids[0] === agent.id
    ));
    if (existing) {
      setActiveSessionId(existing.id);
      setSelectedAgentId(agent.id);
      return;
    }
    const created = await createSession(`Chat with ${agent.name}`, [agent.id]);
    if (created) setSelectedAgentId(agent.id);
  };

  const addParticipants = async () => {
    if (!activeSession || participantAgentIds.length === 0 || busy) return;
    setBusy(true);
    try {
      const updated = await worldApi.addConversationSessionParticipants(
        card.id, activeSession.id, participantAgentIds,
      );
      setSessions((current) => current.map((session) => (
        session.id === updated.id ? updated : session
      )));
      setAddingParticipants(false);
      setParticipantAgentIds([]);
    } catch (reason) {
      pushToast({ tone: "error", title: "Agents were not added", detail: apiErrorMessage(reason) });
    } finally {
      setBusy(false);
    }
  };

  const chooseMention = (agent: Pick<ConversationAgent, "id" | "name">) => {
    if (!completion) return;
    const next = completeMention(draft, completion, agent.name);
    setDraft(next.content);
    setSelectedAgentId(agent.id);
    setMentionCaret(undefined);
    window.requestAnimationFrame(() => {
      messageInput.current?.focus();
      messageInput.current?.setSelectionRange(next.caret, next.caret);
    });
  };

  const removeParticipant = async (agent: ConversationAgent) => {
    if (!activeSession || busy) return;
    if (!window.confirm(`Remove ${agent.name} from ${activeSession.title}?`)) return;
    setBusy(true);
    try {
      const updated = await worldApi.removeConversationSessionParticipant(
        card.id, activeSession.id, agent.id,
      );
      setSessions((current) => current.map((session) => (
        session.id === updated.id ? updated : session
      )));
      if (selectedAgentId === agent.id) setSelectedAgentId(updated.participant_ids[0]);
    } catch (reason) {
      pushToast({ tone: "error", title: "Agent was not removed", detail: apiErrorMessage(reason) });
    } finally {
      setBusy(false);
    }
  };

  const deleteSession = async (target: ConversationSession) => {
    if (target.is_default || busy) return;
    if (!window.confirm(`Delete session ${target.title}? Its conversation history will be deleted.`)) return;
    setBusy(true);
    try {
      await worldApi.deleteConversationSession(card.id, target.id);
      setSessions((current) => current.filter((session) => session.id !== target.id));
      setActiveSessionId((current) => current === target.id ? sessions.find((session) => session.id !== target.id)?.id : current);
    } catch (reason) {
      pushToast({ tone: "error", title: "Session was not deleted", detail: apiErrorMessage(reason) });
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    const content = draft.trim();
    if ((!content && attachments.length === 0) || !activeSession || busy || uploading) return;
    const targets = resolveConversationTargets(content, participants, selectedAgentId);
    const messageId = crypto.randomUUID();
    const message: ConversationMessage = {
      id: messageId, conversation_id: card.id, session_id: activeSession.id,
      sender_kind: "user", sender_name: "You", content, mention_agent_ids: targets,
      attachments,
      created_at: new Date().toISOString(),
    };
    revealOutgoing.current = true;
    setOutgoing((current) => [...current, { message, status: "sending" }]);
    setDraft("");
    setAttachments([]);
    setMentionCaret(undefined);
    setBusy(true);
    try {
      const result = await worldApi.postConversationMessage(card.id, activeSession.id, {
        content,
        message_id: messageId,
        mention_agent_ids: targets,
        ...(attachments.length ? { attachments: attachments.map(({ version_id, path }) => ({ version_id, path })) } : {}),
      });
      setOutgoing((current) => current.map((item) => item.message.id === messageId
        ? { message: result.message, status: "confirmed" } : item));
      if (selectedScope.current === `${card.id}/${result.message.session_id}`) {
        void history.loadLatest();
      }
      if (targets.length === 0 && participants.length > 1) {
        pushToast({
          tone: "neutral",
          title: "Message saved without calling an Agent",
          detail: "Select a participant or include an explicit @name to request a response.",
        });
      }
    } catch (reason) {
      setOutgoing((current) => current.map((item) => item.message.id === messageId
        ? { ...item, status: "unconfirmed" } : item));
      if (selectedScope.current === `${card.id}/${message.session_id}`) void history.loadLatest();
      pushToast({ tone: "error", title: "Send could not be confirmed", detail: apiErrorMessage(reason) });
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
            <input value={groupTitle} onChange={(event) => setGroupTitle(event.target.value)} placeholder="Group name" aria-label="Group name" />
            {connectedAgents.map((agent) => (
              <label key={agent.id}>
                <input type="checkbox" checked={groupAgentIds.includes(agent.id)} onChange={() => setGroupAgentIds((current) => current.includes(agent.id) ? current.filter((id) => id !== agent.id) : [...current, agent.id])} />
                <span>{agent.name}</span>
              </label>
            ))}
            <button type="button" disabled={busy || groupAgentIds.length === 0} onClick={() => void createSession(groupTitle || "Group conversation", groupAgentIds)}>Create group</button>
          </div>
        ) : null}
        <div className="workspace-nav-label"><Users size={11} /> Groups</div>
        <div className="conversation-sidebar-scroll">
          {groups.map((group) => (
            <button type="button" className={`workspace-session ${(group.group_id ?? group.id) === activeGroupId ? "is-active" : ""}`} key={group.group_id ?? group.id} onClick={() => setActiveSessionId(sessions.find((item) => (item.group_id ?? item.id) === (group.group_id ?? group.id))?.id)}>
              <Users size={13} /><span><strong>{group.group_title ?? group.title}</strong></span>
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
          <div className="conversation-heading"><strong title={activeSession?.title}>{activeSession?.title ?? "Conversation"}</strong><span>{participants.length} active participants</span></div>
          <div className="conversation-header-tools">
            <button type="button" className="conversation-add-agent" aria-label="Add agents to session" disabled={!activeSession || availableAgents.length === 0} onClick={() => setAddingParticipants((value) => !value)}><Plus size={12} /> Add</button>
            {addingParticipants ? (
              <div className="conversation-participant-picker" role="dialog" aria-label="Add participants">
                <header><strong>Add to session</strong><button type="button" onClick={() => setAddingParticipants(false)} aria-label="Close participant picker"><X size={12} /></button></header>
                {availableAgents.map((agent) => (
                  <label key={agent.id}>
                    <input type="checkbox" aria-label={`Add ${agent.name} to session`} checked={participantAgentIds.includes(agent.id)} onChange={() => setParticipantAgentIds((current) => current.includes(agent.id) ? current.filter((id) => id !== agent.id) : [...current, agent.id])} />
                    <span><Bot size={11} /> {agent.name}</span>
                  </label>
                ))}
                <button type="button" disabled={busy || participantAgentIds.length === 0} onClick={() => void addParticipants()}>Add selected</button>
              </div>
            ) : null}
          </div>
        </header>
        <div className="conversation-transcript-region">
        <div
          className="workspace-transcript"
          aria-live="polite"
          ref={transcript}
          onScroll={history.onScroll}
        >
          <div className="conversation-transcript-content">
          {error || history.error ? <div role="alert"><p>{error ?? history.error}</p><button type="button" onClick={() => void history.loadLatest()}>Retry history</button></div> : null}
          {history.hasBefore ? <button type="button" disabled={history.loading} onClick={() => void history.loadOlder()}>Load older messages</button> : null}
          {history.loading ? <div className="sr-only" role="status">Loading messages...</div> : null}
          {!error && messages.length === 0 && respondingAgents.length === 0 && !history.loading ? (
            <div className="workspace-welcome"><span><MessageSquare size={22} /></span><strong>This session is ready</strong><p>Select a participant, type an explicit @name, or keep an unaddressed note.</p></div>
          ) : null}
          {messages.map((message) => (
            <article className={`workspace-message is-${message.sender_kind}`} key={message.id} data-message-id={message.id}>
              <span>{message.sender_kind === "agent" ? <Bot size={13} /> : message.sender_kind === "system" ? <Info size={13} /> : <UserRound size={13} />}</span>
              <div><strong>{message.sender_name}</strong>{message.kind?.startsWith("tool_")
                ? <details className="conversation-tool-message"><summary>{message.content.split("\n")[0]}</summary><pre>{message.content.split("\n").slice(1).join("\n").trim() || "No additional details"}</pre></details>
                : message.content ? (message.sender_kind === "agent" ? <MarkdownMessage content={message.content} /> : <p>{message.content}</p>) : null}
                {message.attachments?.length ? <ConversationAttachments conversationId={card.id} sessionId={message.session_id} files={message.attachments} /> : null}
                {visibleOutgoing.find((item) => item.message.id === message.id)?.status === "sending" ? <small className="conversation-delivery-state" role="status">Sending...</small> : null}
                {visibleOutgoing.find((item) => item.message.id === message.id)?.status === "unconfirmed" ? <small className="conversation-delivery-state is-error" role="alert">Send not confirmed. Your text is kept here.</small> : null}
              </div>
            </article>
          ))}
          {history.hasAfter ? <button type="button" disabled={history.loading} onClick={() => void history.loadNewer()}>Load newer messages</button> : null}
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
          </div>
        </div>
        {history.showLatest ? <button type="button" className="conversation-latest" aria-label="Jump to latest" title="Jump to latest" disabled={history.loading} onClick={() => void history.loadLatest()}><ArrowDown size={18} aria-hidden="true" /></button> : null}
        </div>
        <div className="workspace-composer">
          <input ref={fileInput} type="file" multiple hidden aria-label="Attach files" onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            event.target.value = "";
            if (!activeSession || uploading || busy || files.length === 0) return;
            if (files.length + attachments.length > 20 || files.some((file) => file.size > 64 * 1024 * 1024)) {
              pushToast({ tone: "error", title: "Choose up to 20 files, each at most 64 MiB" }); return;
            }
            const sessionId = activeSession.id;
            const scope = `${card.id}/${sessionId}`;
            setUploading(true);
            void (async () => {
              try {
                for (const file of files) {
                  const attachment = await worldApi.uploadConversationAttachment(card.id, sessionId, file);
                  if (selectedScope.current === scope) setAttachments((current) => [...current, attachment]);
                }
              } catch (reason) {
                pushToast({ tone: "error", title: "File upload failed", detail: apiErrorMessage(reason) });
              } finally { setUploading(false); }
            })();
          }} />
          <div className="conversation-pending-files">{attachments.map((file) => <span key={file.version_id}>{file.name}<button type="button" aria-label={`Remove attachment ${file.name}`} onClick={() => setAttachments((current) => current.filter((item) => item.version_id !== file.version_id))}><X size={12} /></button></span>)}</div>
          <textarea ref={messageInput} value={draft} onChange={(event) => {
            setDraft(event.target.value);
            setMentionCaret(event.target.selectionStart ?? event.target.value.length);
          }} onClick={(event) => setMentionCaret(event.currentTarget.selectionStart ?? undefined)} onSelect={(event) => setMentionCaret(event.currentTarget.selectionStart ?? undefined)} onKeyDown={(event) => {
            if (completion && !event.shiftKey) {
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const direction = event.key === "ArrowDown" ? 1 : -1;
                setMentionIndex((current) => (
                  (current + direction + completion.candidates.length) % completion.candidates.length
                ));
                return;
              }
              if (event.key === "Enter" || event.key === "Tab") {
                event.preventDefault();
                chooseMention(completion.candidates[mentionIndex] ?? completion.candidates[0]);
                return;
              }
              if (event.key === "Escape") {
                event.preventDefault();
                setMentionCaret(undefined);
                return;
              }
            }
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void submit();
            }
          }} placeholder={activeSession ? "Write a message; use @name to address a participant…" : "Create or select a session first…"} aria-label="Conversation message" aria-autocomplete="list" aria-expanded={Boolean(completion)} aria-controls={completion ? "conversation-mention-menu" : undefined} disabled={!activeSession} />
          {completion ? (
            <div className="conversation-mention-menu" id="conversation-mention-menu" role="listbox" aria-label="Mention an Agent">
              {completion.candidates.map((agent, index) => (
                <button type="button" role="option" aria-selected={index === mentionIndex} className={index === mentionIndex ? "is-selected" : ""} key={agent.id} onMouseDown={(event) => event.preventDefault()} onClick={() => chooseMention(agent)}>
                  <span><Bot size={12} /></span><strong>{agent.name}</strong><small>@{agent.name}</small>
                </button>
              ))}
            </div>
          ) : null}
          <footer>
            <button type="button" aria-label={uploading ? "Uploading files" : "Attach files"} title="Attach files" disabled={!activeSession || busy || uploading} onClick={() => fileInput.current?.click()}>{uploading ? <LoaderCircle size={14} /> : <Paperclip size={14} />}</button>
            <span>{selectedAgentId ? `Default: @${agents.find((item) => item.id === selectedAgentId)?.name}` : "No default recipient"} · Enter to send · Shift+Enter for new line</span>
            <button type="button" onClick={() => void submit()} disabled={(!draft.trim() && attachments.length === 0) || !activeSession || busy || uploading} aria-label="Send message"><Send size={14} /></button>
          </footer>
        </div>
      </main>

      <aside className="workspace-context-panel conversation-participant-panel">
        <div className="conversation-participant-details">
        <header><Users size={13} /><strong>Participants</strong></header>
        <section>
          <span className="workspace-panel-label">In this session</span>
          {participants.map((agent) => (
            <div className="conversation-participant-row" key={agent.id}>
              <button type="button" className="workspace-context-item" onClick={() => {
                setSelectedAgentId(agent.id);
                setDraft((value) => appendMention(value, agent.name));
              }}>
                <span><Bot size={12} /></span><div><strong>{agent.name}</strong><small>{agent.status} · insert mention</small></div>
              </button>
              <button type="button" className="conversation-kick-agent" aria-label={`Remove ${agent.name} from session`} disabled={busy} onClick={() => void removeParticipant(agent)} title={`Remove ${agent.name}`}><UserMinus size={12} /></button>
            </div>
          ))}
          {participants.length === 0 ? <p>This session has no Agents. Create a direct or group session from the left.</p> : null}
        </section>
        <section>
          <span className="workspace-panel-label">Field policy</span>
          <p>Canvas connections authorize access. Session membership selects the group. Removing an edge keeps history but blocks future turns.</p>
        </section>
        </div>
        <div className="conversation-session-list">
          <div className="workspace-nav-label"><MessageSquare size={11} /> Sessions</div>
          <button type="button" className="workspace-new-session" disabled={!activeSession || busy} onClick={() => void createSession("New session", activeSession?.participant_ids ?? [], activeGroupId)}><Plus size={13} /> New session</button>
          <div className="conversation-sidebar-scroll">
            {groupSessions.map((session) => (
              <div className="conversation-session-row" key={session.id}>
                <button type="button" className={`workspace-session ${session.id === activeSessionId ? "is-active" : ""}`} title={session.title} aria-current={session.id === activeSessionId ? "true" : undefined} onClick={() => setActiveSessionId(session.id)}>
                  <MessageSquare size={13} /><span><strong>{session.title}</strong><small>{new Date(session.created_at).toLocaleString()}</small></span>
                </button>
                <details className="conversation-session-actions" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false; }} onKeyDown={(event) => { if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); } }}>
                  <summary aria-label={`Session actions for ${session.title}`} title="Session actions"><MoreHorizontal size={15} /></summary>
                  <div className="conversation-session-menu">
                    <button type="button" disabled={busy} onClick={(event) => { event.currentTarget.closest("details")?.removeAttribute("open"); setSessionTitle(session.title); setRenaming(session.id); }}><Pencil size={12} /> Rename session</button>
                    <button type="button" className="is-danger" disabled={busy || session.is_default} onClick={(event) => { event.currentTarget.closest("details")?.removeAttribute("open"); void deleteSession(session); }}><Trash2 size={12} /> Delete session</button>
                  </div>
                </details>
                {renaming === session.id ? <form className="conversation-session-rename" onSubmit={(event) => {
                  event.preventDefault();
                  if (!sessionTitle.trim() || busy) return;
                  setBusy(true);
                  void worldApi.renameConversationSession(card.id, session.id, sessionTitle.trim()).then((updated) => {
                    setSessions((current) => current.map((item) => item.id === updated.id ? updated : item));
                    setRenaming(undefined);
                  }).catch((reason) => pushToast({ tone: "error", title: "Session was not renamed", detail: apiErrorMessage(reason) })).finally(() => setBusy(false));
                }}>
                  <input autoFocus aria-label="Session title" maxLength={200} value={sessionTitle} onChange={(event) => setSessionTitle(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") setRenaming(undefined); }} />
                  <button type="submit" disabled={busy || !sessionTitle.trim()}>Save name</button><button type="button" onClick={() => setRenaming(undefined)}>Cancel</button>
                </form> : null}
              </div>
            ))}
          </div>
        </div>
      </aside>
    </div>
  );
}
