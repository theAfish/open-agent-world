import type { KeyboardEvent } from 'react';
import { Bot, LoaderCircle, Paperclip, Send, X } from 'lucide-react';
import { IconButton } from '../components/IconButton';
import { TransferList } from '../files/TransferList';
import { t } from '../i18n';
import type { ConversationComposerState } from '../state/useConversationComposer';
import type { ConversationSession } from '../types/world';

type Props = {
  composer: ConversationComposerState;
  activeSession: ConversationSession | undefined;
  selectedAgentName: string | undefined;
  busy: boolean;
  onInteract: () => void;
};

export function ConversationComposer({ composer, activeSession, selectedAgentName, busy, onInteract }: Props) {
  const {
    draft, setDraft, attachments, setAttachments, attachmentError, fileInput, messageInput, continuationRunId, setContinuationRunId,
    selectedAgentId, completion, mentionIndex, setMentionIndex, setMentionCaret,
    chooseMention, submit, transfers, intake, uploading,
  } = composer;
  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
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
  };

  return (
    <div className="workspace-composer" {...intake.pasteProps} onPointerDown={onInteract}>
      <TransferList queue={transfers.queue} items={transfers.items} />
      {attachmentError && <p className="conversation-execution-error" role="alert">{attachmentError}</p>}
      <input ref={fileInput} type="file" multiple hidden aria-label={t("Attach files")} onChange={(event) => {
        const files = Array.from(event.target.files ?? []);
        event.target.value = "";
        void intake.pick(files, activeSession?.id);
      }} />
      {attachments.length > 0 && <div className="conversation-pending-files">{attachments.map((file) => <span key={file.version_id}><span>{file.name}</span><IconButton icon={X} size="xs" quiet label={t("Remove attachment {v0}", { v0: String(file.name) })} onClick={() => setAttachments((current) => current.filter((item) => item.version_id !== file.version_id))} /></span>)}</div>}
      {continuationRunId && <div className="conversation-pending-files"><span>
        <span>{t('Continuing the original task')}</span>
        <IconButton icon={X} size="xs" quiet label={t('Clear continuation')} onClick={() => setContinuationRunId(undefined)} />
      </span></div>}
      <textarea ref={messageInput} value={draft} onChange={(event) => {
        setDraft(event.target.value);
        if (!event.target.value.trim()) setContinuationRunId(undefined);
        setMentionCaret(event.target.selectionStart ?? event.target.value.length);
      }} onClick={(event) => setMentionCaret(event.currentTarget.selectionStart ?? undefined)} onSelect={(event) => setMentionCaret(event.currentTarget.selectionStart ?? undefined)} onKeyDown={handleKeyDown} placeholder={activeSession ? t("Write a message; use @name to address a participant…") : t("Create or select a session first…")} aria-label={t("Conversation message")} aria-autocomplete="list" aria-expanded={Boolean(completion)} aria-controls={completion ? "conversation-mention-menu" : undefined} disabled={!activeSession} />
      {completion ? (
        <div className="conversation-mention-menu" id="conversation-mention-menu" role="listbox" aria-label={t("Mention an Agent")}>
          {completion.candidates.map((agent, index) => (
            <button type="button" role="option" aria-selected={index === mentionIndex} className={index === mentionIndex ? "is-selected" : ""} key={agent.id} onMouseDown={(event) => event.preventDefault()} onClick={() => chooseMention(agent)}>
              <span><Bot size={12} /></span><strong>{agent.name}</strong><small>@{agent.name}</small>
            </button>
          ))}
        </div>
      ) : null}
      <footer>
        <button type="button" aria-label={uploading ? t("Uploading files") : t("Attach files")} title={t("Attach files")} disabled={!activeSession || uploading || busy} onClick={() => fileInput.current?.click()}>{uploading ? <LoaderCircle size={14} /> : <Paperclip size={14} />}</button>
        <span className="conversation-composer-guidance">{selectedAgentId ? <span>@{selectedAgentName}</span> : null}<span>{t("Enter to send · Shift+Enter for new line")}</span></span>
        <button type="button" onClick={() => void submit()} disabled={(!draft.trim() && attachments.length === 0) || !activeSession || uploading} aria-label={t("Send message")}><Send size={14} /></button>
      </footer>
    </div>
  );
}
