import { ArrowUp, CircleStop, Scan } from 'lucide-react';
import { useRef, useState } from 'react';
import { apiErrorMessage, worldApi } from '../api/client';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useConversationTimeline } from '../state/useConversationTimeline';
import { useWorldStore } from '../state/worldStore';
import type { MinisterChat } from '../types/minister';
import type { WorldCard } from '../types/world';
import { MarkdownMessage } from './MarkdownMessage';
import { MinisterBubbleStack } from './MinisterBubbleStack';
import { ministerToolSummary } from './ministerActivity';

export function MinisterConversation({ card, chat, presence = false }: { card: WorldCard; chat: MinisterChat; presence?: boolean }) {
  const log = useRef<HTMLDivElement>(null);
  const mountedAt = useRef(Date.now());
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
      {!presence && timeline.hasBefore && <button type="button" className="minister-text-button" onClick={() => void timeline.loadOlder()}>Earlier messages</button>}
      {!presence && !timeline.loading && !timeline.messages.length && <div className="minister-welcome">
        <Scan size={24} /><strong>A little help, close at hand.</strong>
        <p>Ask me to find cards or tidy this part of your canvas.</p>
        <button type="button" onClick={() => setDraft(card.id, "What cards are inside your circle?")}>What’s nearby?</button>
      </div>}
      {presence ? <MinisterBubbleStack messages={timeline.messages} since={mountedAt.current} /> : timeline.messages.map(message => <article key={message.id} data-message-id={message.id}
        className={`minister-message is-${message.sender_kind}`}>
        {message.kind?.startsWith("tool_") ? <details className="minister-tool"><summary>{ministerToolSummary(message.content, message.kind)}</summary><pre aria-label="Tool debug details">{message.content}</pre></details>
          : <><small>{message.sender_kind === "user" ? "You" : message.sender_name}</small><MarkdownMessage content={message.content} /></>}
      </article>)}
      {active && <p className="minister-thinking" role="status">Minister is working…</p>}
    </div>
    {!presence && timeline.showLatest && <button type="button" className="minister-text-button" onClick={() => void timeline.loadLatest()}>Latest messages</button>}
    {(error || timeline.error) && <p className="minister-error" role="alert">{error || timeline.error}</p>}
    <form className="minister-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
      <textarea aria-label={`Message ${card.name}`} placeholder="Ask about this part of your canvas…" rows={presence ? 1 : 2} value={draft}
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

