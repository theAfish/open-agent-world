import { useEffect, useRef, useState } from 'react';
import { useInternalNode } from '@xyflow/react';
import { ViewportPortal } from '../canvas/FlowPortal';
import { apiErrorMessage, worldApi } from '../api/client';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import type { WorldCard } from '../types/world';
import type { MinisterChat } from '../types/minister';
import { MinisterConversation } from './MinisterConversation';

// Presence is transient; the conversation API remains the history authority.
export function MinisterPresence({ card, active, setActive, panelOpen }: {
  card: WorldCard; active: boolean; setActive: (value: boolean) => void; panelOpen: boolean;
}) {
  const node = useInternalNode(card.id);
  const origin = node?.internals.positionAbsolute ?? card.position;
  const [chat, setChat] = useState<MinisterChat>();
  const [error, setError] = useState<string>();
  const [greeting, setGreeting] = useState(false);
  const greeted = useRef(false);
  const region = useRef<HTMLDivElement>(null);
  const draft = useNodeSurfaceStore(s => s.drafts[card.id] ?? '');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!active || greeted.current) return;
    greeted.current = true;
    setGreeting(true);
    const timer = window.setTimeout(() => setGreeting(false), 9000);
    return () => window.clearTimeout(timer);
  }, [active]);
  useEffect(() => {
    if (!active || chat) return;
    let current = true;
    // Opening the durable session does not send a message or invoke a model.
    void worldApi.openMinisterChat(card.id).then(value => { if (current) { setChat(value); setError(undefined); } })
      .catch(reason => { if (current) setError(apiErrorMessage(reason)); });
    return () => { current = false; };
  }, [active, card.id, chat, attempt]);
  useEffect(() => {
    if (!active) return;
    let timer: number | undefined;
    const move = (event: PointerEvent) => {
      const avatar = document.querySelector(`[data-card-id="${CSS.escape(card.id)}"] .minister-avatar-zone`);
      const near = [avatar, region.current].some(element => {
        const box = element?.getBoundingClientRect();
        return box && event.clientX >= box.left - 28 && event.clientX <= box.right + 28
          && event.clientY >= box.top - 28 && event.clientY <= box.bottom + 28;
      });
      window.clearTimeout(timer);
      if (!near) timer = window.setTimeout(() => {
        if (!draft && !region.current?.contains(document.activeElement) && card.status !== 'running' && card.status !== 'waiting') {
          setActive(false); setGreeting(false);
        }
      }, 1800);
    };
    window.addEventListener('pointermove', move);
    return () => { window.clearTimeout(timer); window.removeEventListener('pointermove', move); };
  }, [active, card.id, card.status, draft, setActive]);
  return <ViewportPortal><div ref={region} className="minister-presence nodrag nopan nowheel"
    hidden={!active || panelOpen} style={{ left: origin.x + card.size.width + 16, top: origin.y + card.size.height / 2 }}
    onPointerDown={event => event.stopPropagation()} onDoubleClick={event => event.stopPropagation()}
    onKeyDown={event => { event.stopPropagation(); if (event.key === 'Escape') { (document.activeElement as HTMLElement)?.blur(); setActive(false); setGreeting(false); } }}>
    {greeting && <div className="minister-greeting">Hi! Need a hand with this part of your canvas?</div>}
    {chat ? <MinisterConversation card={card} chat={chat} presence /> : <div className="minister-presence-loading" role="status">
      {error ?? 'Getting ready…'}{error && <button onClick={() => setAttempt(value => value + 1)}>Retry</button>}
    </div>}
    <button className="minister-presence-history" onClick={() => useNodeSurfaceStore.getState().openInspector(card.id)}>History & confirmations</button>
  </div></ViewportPortal>;
}
