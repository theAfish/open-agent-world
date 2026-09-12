import { expect, it, vi } from 'vitest';
import { observeInteractions, reportInteraction } from './interactions';

it('isolates a failed observer from successful application actions and other observers', () => {
  const error = vi.spyOn(console, 'error').mockImplementation(() => {});
  const broken = observeInteractions(() => { throw new Error('broken guide'); });
  const listener = vi.fn(), detach = observeInteractions(listener);
  const sent = { type: 'message-sent' as const, cardId: 'room', conversationId: 'room' };
  try {
    expect(() => reportInteraction(sent)).not.toThrow();
    expect(listener).toHaveBeenCalledWith(sent);
    expect(error).toHaveBeenCalledOnce();
    detach(); reportInteraction(sent);
    expect(listener).toHaveBeenCalledOnce();
  } finally { broken(); detach(); error.mockRestore(); }
});
