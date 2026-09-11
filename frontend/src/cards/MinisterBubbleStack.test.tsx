// @vitest-environment jsdom
import { act, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ConversationMessage } from '../types/world';
import { MinisterBubbleStack } from './MinisterBubbleStack';

afterEach(() => vi.useRealTimers());
it('bounds canvas bubbles, expires them, and leaves durable input untouched', () => {
  vi.useFakeTimers();
  const since = Date.now();
  const messages = Array.from({ length: 7 }, (_, index) => ({
    id: String(index), content: `Message ${index}`, sender_kind: 'user',
    created_at: new Date(since).toISOString(),
  } as ConversationMessage));
  const view = render(<MinisterBubbleStack messages={messages} since={since} />);
  expect(view.container.querySelectorAll('.minister-bubble-slot:not(.is-leaving)')).toHaveLength(4);
  act(() => vi.advanceTimersByTime(61000));
  expect(view.container.querySelectorAll('article')).toHaveLength(0);
  expect(messages).toHaveLength(7);
  view.unmount();
});
