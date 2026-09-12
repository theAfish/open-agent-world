/** Successful UI interactions that cannot be inferred from a world snapshot.
 * No message content, credentials, or persistent world data belongs here. */
export type WorldInteraction =
  | { type: 'viewport'; x: number; y: number; zoom: number }
  | { type: 'focus'; ids: string[] }
  | { type: 'message-sent'; cardId: string; conversationId: string }
  | { type: 'minister-opened'; cardId: string }
  | { type: 'glue-saved'; bonds: { a: string; b: string }[] };

const listeners = new Set<(event: WorldInteraction) => void>();
export function observeInteractions(listener: (event: WorldInteraction) => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function reportInteraction(event: WorldInteraction) {
  listeners.forEach(listener => {
    // A guide/observer failure must not turn a confirmed application action into
    // a failed send or failed save in its caller.
    try { listener(event); }
    catch (error) { console.error('World interaction observer failed', error); }
  });
}
