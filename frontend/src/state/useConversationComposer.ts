import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { apiErrorMessage, worldApi } from '../api/client';
import { useTransferQueue } from '../files/TransferList';
import { useFileIntake } from '../files/useFileIntake';
import { t } from '../i18n';
import { completeMention, mentionCompletion, resolveConversationTargets } from './conversationMentions';
import { reportInteraction } from './interactions';
import { surfaceDraftKey, useSurfaceDraft, useNodeSurfaceStore } from './nodeSurfaces';
import type { useConversationTimeline } from './useConversationTimeline';
import { useWorldStore } from './worldStore';
import type { ConversationAgent, ConversationAttachment, ConversationMessage, ConversationSession } from '../types/world';

type OutgoingMessage = { message: ConversationMessage; status: "sending" | "confirmed" | "unconfirmed"; error?: string };
const NO_ATTACHMENTS: ConversationAttachment[] = [];
const NO_OUTGOING: OutgoingMessage[] = [];

type ComposerOptions = {
  conversationId: string;
  activeSessionId: string | undefined;
  activeSession: ConversationSession | undefined;
  participants: ConversationAgent[];
  busy: boolean;
  history: ReturnType<typeof useConversationTimeline>;
  transcript: RefObject<HTMLDivElement>;
};

export function useConversationComposer({
  conversationId, activeSessionId, activeSession, participants, busy, history, transcript,
}: ComposerOptions) {
  const pushToast = useWorldStore(state => state.pushToast);
  const [draft, setDraft] = useSurfaceDraft(surfaceDraftKey(conversationId, 'composer', activeSessionId), '');
  const [attachments, setAttachments] = useSurfaceDraft(surfaceDraftKey(conversationId, 'attachments', activeSessionId), NO_ATTACHMENTS);
  const fileInput = useRef<HTMLInputElement>(null);
  const [attachmentError, setAttachmentError] = useState<string>();
  const [outgoing, setOutgoing] = useSurfaceDraft(surfaceDraftKey(conversationId, 'outgoing'), NO_OUTGOING);
  const revealOutgoing = useRef(false);
  const [preferredAgentId, setPreferredAgentId] = useSurfaceDraft<string | undefined>(surfaceDraftKey(conversationId, 'conversation-agent', activeSessionId), undefined);
  const [continuationRunId, setContinuationRunId] = useSurfaceDraft<string | undefined>(surfaceDraftKey(conversationId, 'conversation-continuation-run', activeSessionId), undefined);
  const [mentionCaret, setMentionCaret] = useState<number>();
  const [mentionIndex, setMentionIndex] = useState(0);
  const messageInput = useRef<HTMLTextAreaElement>(null);
  const currentSessionScope = useRef("");
  currentSessionScope.current = `${conversationId}/${activeSessionId ?? ""}`;

  const transfers = useTransferQueue(conversationId);
  const intake = useFileIntake({
    disabled: !activeSession || busy || transfers.active,
    limits: { maxEntries: Math.max(0, 20 - attachments.length), directories: false },
    resolveFileLimit: async () => (await worldApi.fileTransferPolicy()).max_file_bytes,
    destination: () => activeSession?.id,
    onError: reason => setAttachmentError(apiErrorMessage(reason)),
    onEntries: async (entries, sessionId) => {
      if (!sessionId) return;
      setAttachmentError(undefined);
      // This setter belongs to the session where intake began, including after a switch.
      const appendAttachments = setAttachments;
      const draftKey = surfaceDraftKey(conversationId, 'attachments', sessionId);
      await transfers.queue.add(entries.map(entry => ({ name: entry.path, size: entry.file!.size, direction: 'upload', scope: sessionId,
        run: async options => {
          const current = JSON.parse(useNodeSurfaceStore.getState().drafts[draftKey] || '[]') as ConversationAttachment[];
          if (current.length >= 20) throw new Error(t('Choose at most {count} entries.', { count: 20 }));
          const attachment = await worldApi.uploadConversationAttachment(conversationId, sessionId, entry.file!, options);
          if (!options.signal?.aborted) appendAttachments(current => current.some(file => file.version_id === attachment.version_id) ? current : [...current, attachment]);
        },
      })));
    },
  });
  const uploading = intake.processing || transfers.active;
  // Canvas and workspace can own separate REST snapshots of this conversation.
  // Derive a fallback locally: a stale copy must not clear the shared choice
  // while the other copy is creating a session or updating its participants.
  const selectedAgentId = participants.some(agent => agent.id === preferredAgentId)
    ? preferredAgentId : participants[0]?.id;
  const completion = mentionCaret === undefined
    ? undefined
    : mentionCompletion(draft, mentionCaret, participants);

  useEffect(() => {
    setMentionIndex(0);
  }, [completion?.query]);

  useEffect(() => {
    setMentionCaret(undefined);
    setAttachmentError(undefined);
  }, [activeSessionId]);

  const chooseMention = (agent: Pick<ConversationAgent, "id" | "name">) => {
    if (!completion) return;
    const next = completeMention(draft, completion, agent.name);
    setDraft(next.content);
    setPreferredAgentId(agent.id);
    setMentionCaret(undefined);
    window.requestAnimationFrame(() => {
      messageInput.current?.focus();
      messageInput.current?.setSelectionRange(next.caret, next.caret);
    });
  };

  const submit = async () => {
    const content = draft.trim();
    if ((!content && attachments.length === 0) || !activeSession || uploading) return;
    const targets = resolveConversationTargets(content, participants, selectedAgentId);
    const messageId = crypto.randomUUID();
    const message: ConversationMessage = {
      id: messageId, conversation_id: conversationId, session_id: activeSession.id,
      sender_kind: "user", sender_name: t("You"), content, mention_agent_ids: targets,
      attachments,
      continuation_run_id: continuationRunId,
      created_at: new Date().toISOString(),
    };
    revealOutgoing.current = true;
    setOutgoing((current) => [...current, { message, status: "sending" }]);
    setDraft("");
    setContinuationRunId(undefined);
    setAttachments([]);
    setMentionCaret(undefined);
    try {
      const result = await worldApi.postConversationMessage(conversationId, activeSession.id, {
        content,
        message_id: messageId,
        continuation_run_id: continuationRunId,
        mention_agent_ids: targets,
        ...(attachments.length ? { attachments: attachments.map(({ version_id, path }) => ({ version_id, path })) } : {}),
      });
      setOutgoing((current) => current.map((item) => item.message.id === messageId
        ? { message: result.message, status: "confirmed" } : item));
      reportInteraction({ type: 'message-sent', cardId: conversationId, conversationId });
      if (currentSessionScope.current === `${conversationId}/${result.message.session_id}`) {
        void history.loadLatest();
      }
      if (targets.length === 0 && participants.length > 1) {
        pushToast({
          tone: "neutral",
          title: t("Message saved without calling an Agent"),
          detail: t("Select a participant or include an explicit @name to request a response."),
        });
      }
    } catch (reason) {
      setOutgoing((current) => current.map((item) => item.message.id === messageId
        ? { ...item, status: "unconfirmed", error: apiErrorMessage(reason) } : item));
      if (currentSessionScope.current === `${conversationId}/${message.session_id}`) void history.loadLatest();
      pushToast({ tone: "error", title: t("Send could not be confirmed"), detail: apiErrorMessage(reason) });
    }
  };

  const visibleOutgoing = outgoing.filter((item) => item.message.conversation_id === conversationId
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
  const restoreMessageDraft = (message: ConversationMessage) => {
    setContinuationRunId(message.continuation_run_id);
    setDraft(message.content);
    setAttachments(message.attachments ?? []);
    setOutgoing(current => current.filter(item => item.message.id !== message.id));
    messageInput.current?.focus();
  };

  return {
    draft, setDraft, attachments, setAttachments, attachmentError, fileInput, messageInput, continuationRunId, setContinuationRunId,
    selectedAgentId, setPreferredAgentId, completion, mentionIndex, setMentionIndex,
    setMentionCaret, chooseMention, submit, restoreMessageDraft, transfers, intake, uploading,
    visibleOutgoing, messages,
  };
}

export type ConversationComposerState = ReturnType<typeof useConversationComposer>;
