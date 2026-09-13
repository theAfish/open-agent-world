import { t, useLocale } from "../i18n";
import { useEffect, useState } from 'react';
import type { ConversationMessage } from '../types/world';
import { MarkdownMessage } from './MarkdownMessage';

export function MinisterBubbleStack({ messages, since }: { messages: ConversationMessage[]; since: number }) {
  useLocale();
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, []);
  const recent = messages.filter(message => !message.kind?.startsWith('tool_') && Date.parse(message.created_at) >= since);
  return <>{recent.slice(-5).map((message, index, items) => {
    const age = now - Date.parse(message.created_at);
    if (age > 60500) return null;
    return <div key={message.id} className={`minister-bubble-slot ${age > 60000 || (items.length > 4 && index === 0) ? 'is-leaving' : ''}`}>
      <article className={`minister-message is-${message.sender_kind}`} data-message-id={message.id}>
        <small>{message.sender_kind === 'user' ? t("You") : message.sender_name}</small>
        <MarkdownMessage content={message.content} />
      </article>
    </div>;
  })}</>;
}
